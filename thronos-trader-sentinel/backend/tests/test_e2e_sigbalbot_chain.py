"""E2E integration test — Thronos → SigBalBot → Sentinel → Signal → SigBalBot POST.

Mocks all HTTP boundaries (httpx calls) and verifies the full chain from
wallet-snapshot ingestion through signal evaluation and posting.
"""
import asyncio
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_tmp_dir = tempfile.mkdtemp(prefix="e2e_chain_test_")
os.environ["WALLET_SNAPSHOT_DIR"] = _tmp_dir

import app.brain.wallet_snapshot_consumer as wsc
import app.brain.sigbalbot_publisher as publisher
from app.brain.sigbalbot_context_poller import (
    _is_sentinel_signal,
    _parse_exchanges,
    _verify_hunter_exchanges,
    _assess_sniper_risk,
    _evaluate_watchlist_symbol,
    _evaluate_native_signal,
    _VALID_ASSET_CLASSES,
    fetch_context,
    poll_and_evaluate,
)
from app.brain.market_review_publisher import current_review_id, build_review_payload
import app.brain.store as brain_store

wsc.SNAPSHOT_DIR = Path(_tmp_dir)


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset module-level state between tests."""
    snap_path = wsc._snapshot_path()
    if snap_path.exists():
        snap_path.unlink()

    publisher._WEBHOOK_URL = None
    publisher._API_KEY = None

    import app.brain.sigbalbot_context_poller as poller
    poller._CONTEXT_BASE_URL = None
    poller._API_KEY = None
    poller._context_capability_confirmed = True
    poller._contract_version = "1.0"
    yield
    if snap_path.exists():
        snap_path.unlink()


def _thronos_wallet_snapshot():
    """Simulates what Thronos returns at GET /api/sigbalbot/wallet-snapshots."""
    return {
        "ok": True,
        "subscribers": [
            {
                "address": "THR0A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6A7",
                "tier": "pro",
                "rewards_multiplier": 1.5,
                "active": True,
                "verified": True,
                "expires_at": int(time.time()) + 86400,
                "thr_balance": 250.0,
                "snapshot_at": int(time.time()),
            }
        ],
    }


def _sigbalbot_context_response(wallet_snapshots=None):
    """Simulates SigBalBot's context response that includes relayed wallet snapshots."""
    ctx = {
        "ok": True,
        "contract_version": "1.0",
        "generated_at": "2026-08-12T10:00:00Z",
        "watchlist": [
            {
                "symbol": "BTC/USDT",
                "mode": "watch",
                "label": "BTC",
                "market_cap_usd": 1200000000000,
                "volume_24h": 35000000000,
                "liquidity_usd": 500000000,
                "last_scan_signal": "AGG_LONG",
                "last_scan_at": "2026-08-12T09:55:00Z",
            }
        ],
        "native_signals": [],
        "wallet_snapshot_status": {"state": "fresh", "subscriber_count": 1},
    }
    if wallet_snapshots is not None:
        ctx["wallet_snapshots"] = wallet_snapshots
    return ctx


def _make_mock_response(status_code, body):
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = body
    mock.text = json.dumps(body)
    return mock


# ── 1. Wallet snapshot ingestion from context ──────────────────────────────

def test_wallet_snapshots_ingested_from_context():
    """Thronos returns wallet snapshot → SigBalBot relays → Sentinel ingests."""
    thronos = _thronos_wallet_snapshot()
    ctx = _sigbalbot_context_response(wallet_snapshots=thronos["subscribers"])

    count = wsc.ingest_wallet_snapshots(ctx)
    assert count == 1, "Expected 1 subscriber ingested"

    active = wsc.get_active_subscriber_count()
    assert active == 1, "Expected 1 active subscriber"


# ── 2. Subscriber count correct after ingestion ───────────────────────────

def test_subscriber_count_and_tier():
    thronos = _thronos_wallet_snapshot()
    ctx = _sigbalbot_context_response(wallet_snapshots=thronos["subscribers"])
    wsc.ingest_wallet_snapshots(ctx)

    assert wsc.get_active_subscriber_count() == 1
    tiers = wsc.get_subscriber_tiers()
    assert tiers.get("pro") == 1


# ── 3. Context polling integrates wallet snapshots ─────────────────────────

@pytest.mark.asyncio
async def test_poll_and_evaluate_ingests_snapshots():
    """Full poll_and_evaluate cycle ingests wallet snapshots from context."""
    thronos = _thronos_wallet_snapshot()
    ctx = _sigbalbot_context_response(wallet_snapshots=thronos["subscribers"])
    ctx["watchlist"] = []
    mock_resp = _make_mock_response(200, ctx)

    with patch.dict(os.environ, {
        "SIGBALBOT_WEBHOOK_URL": "https://fake.sigbalbot.test/api/v1/signals/trader-sentinel",
        "SIGBALBOT_API_KEY": "test-key-e2e",
    }):
        import app.brain.sigbalbot_context_poller as poller
        poller._CONTEXT_BASE_URL = None
        poller._API_KEY = None

        with patch("app.brain.store.load_sigbalbot_cursor", return_value={"processed_signal_ids": []}):
            with patch("app.brain.store.save_sigbalbot_cursor") as save_mock:
                with patch("httpx.AsyncClient") as mock_client_cls:
                    mock_client = AsyncMock()
                    mock_client.get = AsyncMock(return_value=mock_resp)
                    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                    mock_client.__aexit__ = AsyncMock(return_value=False)
                    mock_client_cls.return_value = mock_client

                    summary = await poll_and_evaluate()

        assert summary["wallet_snapshots_ingested"] == 1
        assert wsc.get_active_subscriber_count() == 1


# ── 4. Signal posted exactly once ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_signal_posted_once():
    """When context contains a native signal, it posts exactly once after evaluation."""
    ctx = _sigbalbot_context_response()
    ctx["watchlist"] = []
    ctx["native_signals"] = [{
        "id": "sigbal-BTC-LONG-999",
        "symbol": "BTC/USDT",
        "signal": "AGG_LONG",
        "confidence": 85,
        "timeframe": "15m",
        "reason": "Multi-indicator",
        "created_at": "2026-08-12T09:55:00Z",
    }]
    mock_ctx_resp = _make_mock_response(200, ctx)

    mock_post_resp = _make_mock_response(202, {
        "event_id": "evt_abc",
        "telegram_sent": True,
        "duplicate": False,
    })

    ta_result = MagicMock()
    ta_result.rsi_14 = 42.0
    ta_result.rsi_signal = "neutral"
    ta_result.macd_trend = "bullish"
    ta_result.macd_histogram = 0.5
    ta_result.bb_signal = "neutral"
    ta_result.bb_pct = 0.5
    ta_result.ema_cross = "golden_cross"
    ta_result.williams_r = -45.0
    ta_result.williams_r_signal = "neutral"
    ta_result.score = 7.5
    ta_result.current_price = 65000.0
    ta_result.error = None

    post_calls = []

    async def capture_post(url, json=None, headers=None):
        post_calls.append({"url": url, "json": json, "headers": headers})
        return mock_post_resp

    with patch.dict(os.environ, {
        "SIGBALBOT_WEBHOOK_URL": "https://fake.sigbalbot.test/api/v1/signals/trader-sentinel",
        "SIGBALBOT_API_KEY": "test-key-e2e",
    }):
        import app.brain.sigbalbot_context_poller as poller
        poller._CONTEXT_BASE_URL = None
        poller._API_KEY = None
        publisher._WEBHOOK_URL = None
        publisher._API_KEY = None

        with patch("app.brain.store.load_sigbalbot_cursor", return_value={"processed_signal_ids": []}):
            with patch("app.brain.store.save_sigbalbot_cursor"):
                with patch("httpx.AsyncClient") as mock_client_cls:
                    mock_client = AsyncMock()
                    mock_client.get = AsyncMock(return_value=mock_ctx_resp)
                    mock_client.post = capture_post
                    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                    mock_client.__aexit__ = AsyncMock(return_value=False)
                    mock_client_cls.return_value = mock_client

                    with patch("app.sentinel.technicals.calculate", return_value=ta_result):
                        with patch("app.brain.sleep_trader._evaluate_entry", return_value=("buy", 0.78)):
                            with patch("app.sentinel.risk.generate_report", new_callable=AsyncMock) as risk_mock:
                                risk_mock.return_value = MagicMock(composite_score=3.0)
                                summary = await poll_and_evaluate()

    assert summary["signals_posted"] == 1
    assert len(post_calls) == 1


# ── 5. Duplicate signal not reposted ──────────────────────────────────────

@pytest.mark.asyncio
async def test_duplicate_signal_not_reposted():
    """A signal that was already processed (in cursor) is not evaluated again."""
    ctx = _sigbalbot_context_response()
    ctx["watchlist"] = []
    ctx["native_signals"] = [{
        "id": "sigbal-BTC-LONG-999",
        "symbol": "BTC/USDT",
        "signal": "AGG_LONG",
        "confidence": 85,
        "timeframe": "15m",
        "reason": "Multi-indicator",
        "created_at": "2026-08-12T09:55:00Z",
    }]
    mock_resp = _make_mock_response(200, ctx)

    with patch.dict(os.environ, {
        "SIGBALBOT_WEBHOOK_URL": "https://fake.sigbalbot.test/api/v1/signals/trader-sentinel",
        "SIGBALBOT_API_KEY": "test-key-e2e",
    }):
        import app.brain.sigbalbot_context_poller as poller
        poller._CONTEXT_BASE_URL = None
        poller._API_KEY = None

        already_processed = {"processed_signal_ids": ["sigbal-BTC-LONG-999"]}
        with patch("app.brain.store.load_sigbalbot_cursor", return_value=already_processed):
            with patch("app.brain.store.save_sigbalbot_cursor"):
                with patch("httpx.AsyncClient") as mock_client_cls:
                    mock_client = AsyncMock()
                    mock_client.get = AsyncMock(return_value=mock_resp)
                    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                    mock_client.__aexit__ = AsyncMock(return_value=False)
                    mock_client_cls.return_value = mock_client

                    summary = await poll_and_evaluate()

    assert summary["skipped_duplicates"] == 1
    assert summary["native_signals_evaluated"] == 0
    assert summary["signals_posted"] == 0


# ── 6. No secrets in posted payload ───────────────────────────────────────

@pytest.mark.asyncio
async def test_no_secrets_in_posted_payload():
    """Signal payloads must never contain API keys, tokens, or private keys."""
    ctx = _sigbalbot_context_response()
    ctx["watchlist"] = []
    ctx["native_signals"] = [{
        "id": "sigbal-ETH-LONG-888",
        "symbol": "ETH/USDT",
        "signal": "AGG_LONG",
        "confidence": 90,
        "timeframe": "15m",
        "reason": "Confluence",
        "created_at": "2026-08-12T09:56:00Z",
    }]
    mock_ctx_resp = _make_mock_response(200, ctx)
    mock_post_resp = _make_mock_response(202, {"event_id": "evt_xyz", "telegram_sent": True})

    posted_payloads = []

    async def capture_post(url, json=None, headers=None):
        posted_payloads.append(json)
        return mock_post_resp

    ta_result = MagicMock()
    ta_result.rsi_14 = 38.0
    ta_result.rsi_signal = "oversold"
    ta_result.macd_trend = "bullish"
    ta_result.macd_histogram = 0.8
    ta_result.bb_signal = "lower"
    ta_result.bb_pct = 0.2
    ta_result.ema_cross = "golden_cross"
    ta_result.williams_r = -80.0
    ta_result.williams_r_signal = "oversold"
    ta_result.score = 8.0
    ta_result.current_price = 3200.0
    ta_result.error = None

    api_key = "super-secret-key-12345"

    with patch.dict(os.environ, {
        "SIGBALBOT_WEBHOOK_URL": "https://fake.sigbalbot.test/api/v1/signals/trader-sentinel",
        "SIGBALBOT_API_KEY": api_key,
    }):
        import app.brain.sigbalbot_context_poller as poller
        poller._CONTEXT_BASE_URL = None
        poller._API_KEY = None
        publisher._WEBHOOK_URL = None
        publisher._API_KEY = None

        with patch("app.brain.store.load_sigbalbot_cursor", return_value={"processed_signal_ids": []}):
            with patch("app.brain.store.save_sigbalbot_cursor"):
                with patch("httpx.AsyncClient") as mock_client_cls:
                    mock_client = AsyncMock()
                    mock_client.get = AsyncMock(return_value=mock_ctx_resp)
                    mock_client.post = capture_post
                    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                    mock_client.__aexit__ = AsyncMock(return_value=False)
                    mock_client_cls.return_value = mock_client

                    with patch("app.sentinel.technicals.calculate", return_value=ta_result):
                        with patch("app.brain.sleep_trader._evaluate_entry", return_value=("buy", 0.82)):
                            await poll_and_evaluate()

    assert len(posted_payloads) >= 1
    payload_str = json.dumps(posted_payloads[0])
    assert api_key not in payload_str, "API key leaked into signal payload"
    assert "private_key" not in payload_str.lower()
    assert "seed_phrase" not in payload_str.lower()
    assert "mnemonic" not in payload_str.lower()


# ── 7. Feedback loop guard ─────────────────────────────────────────────────

def test_feedback_loop_guard():
    """Signals originating from Sentinel are skipped to prevent feedback loops."""
    assert _is_sentinel_signal("sentinel-BTC-USDT-LONG-1234") is True
    assert _is_sentinel_signal("sentinel-ctx-native-ETH-SHORT-5678") is True
    assert _is_sentinel_signal("sigbal-BTC-LONG-999") is False
    assert _is_sentinel_signal("external-signal-123") is False


# ── 8. Contract version validation ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_unsupported_contract_version_rejects():
    """Context with unsupported contract_version returns None."""
    ctx = _sigbalbot_context_response()
    ctx["contract_version"] = "2.0"
    mock_resp = _make_mock_response(200, ctx)

    with patch.dict(os.environ, {
        "SIGBALBOT_WEBHOOK_URL": "https://fake.sigbalbot.test/api/v1/signals/trader-sentinel",
        "SIGBALBOT_API_KEY": "test-key-e2e",
    }):
        import app.brain.sigbalbot_context_poller as poller
        poller._CONTEXT_BASE_URL = None
        poller._API_KEY = None

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await fetch_context()

    assert result is None


# ── 9. Empty wallet snapshots handled gracefully ───────────────────────────

def test_empty_wallet_snapshots_no_crash():
    """Context without wallet_snapshots does not crash the consumer."""
    ctx = _sigbalbot_context_response(wallet_snapshots=None)
    count = wsc.ingest_wallet_snapshots(ctx)
    assert count == 0
    assert wsc.get_active_subscriber_count() == 0


# ── 10. Snapshot freshness check ──────────────────────────────────────────

def test_snapshot_freshness_after_ingestion():
    """Snapshot is fresh immediately after ingestion, stale after max age."""
    thronos = _thronos_wallet_snapshot()
    ctx = _sigbalbot_context_response(wallet_snapshots=thronos["subscribers"])
    wsc.ingest_wallet_snapshots(ctx)

    assert wsc.is_snapshot_fresh() is True

    snap = wsc._load_snapshot()
    snap["fetched_at"] = time.time() - 10000
    wsc._save_snapshot(snap)
    assert wsc.is_snapshot_fresh() is False


# ════════════════════════════════════════════════════════════════════════════
# Sniper contract-alignment tests
# ════════════════════════════════════════════════════════════════════════════

def _sniper_watchlist_item(**overrides):
    """Base sniper watchlist item matching the SigBalBot v1 sniper schema."""
    item = {
        "symbol": "TOKEN/USDT",
        "label": "TOKEN",
        "mode": "sniper",
        "market_cap_usd": 9_000_000,
        "volume_24h": 1_250_000,
        "liquidity_usd": 340_000,
        "exchanges": "mexc,bybit,binance",
        "last_scan_at": "2026-08-13T10:00:00Z",
        "last_scan_signal": "HOLD",
        "last_scan_reason": "Awaiting confirmation",
    }
    item.update(overrides)
    return item


def _mock_ta_result(price=0.045):
    ta = MagicMock()
    ta.rsi_14 = 38.0
    ta.rsi_signal = "oversold"
    ta.macd_trend = "bullish"
    ta.macd_histogram = 0.5
    ta.bb_signal = "neutral"
    ta.bb_pct = 0.4
    ta.ema_cross = "golden_cross"
    ta.williams_r = -70.0
    ta.williams_r_signal = "neutral"
    ta.score = 7.5
    ta.current_price = price
    ta.error = None
    return ta


# ── 11. MEXC + Bybit candidate accepted ──────────────────────────────────

def test_sniper_mexc_bybit_accepted():
    """Candidate with both MEXC and Bybit passes hunter verification."""
    exchanges = _parse_exchanges("mexc,bybit")
    verified, has_binance, reasons = _verify_hunter_exchanges(exchanges)
    assert verified is True
    assert has_binance is False
    assert reasons == []


# ── 12. MEXC + Bybit + Binance gets extra confirmation ──────────────────

def test_sniper_binance_extra_confirmation():
    """Binance presence gives additional listing confirmation but is not mandatory."""
    exchanges = _parse_exchanges("mexc,bybit,binance")
    verified, has_binance, reasons = _verify_hunter_exchanges(exchanges)
    assert verified is True
    assert has_binance is True


# ── 13. Binance missing does not reject ─────────────────────────────────

def test_sniper_binance_missing_does_not_reject():
    """Candidate with MEXC+Bybit but no Binance is still valid."""
    item = _sniper_watchlist_item(exchanges="mexc,bybit")
    exchanges = _parse_exchanges(item["exchanges"])
    verified, has_binance, reasons = _verify_hunter_exchanges(exchanges)
    assert verified is True
    assert has_binance is False

    verdict, _, _, risk_reasons = _assess_sniper_risk(
        item, exchanges, verified, has_binance, "STANDARD",
    )
    assert verdict != "REJECT"


# ── 14. MEXC or Bybit missing lowers/rejects ────────────────────────────

def test_sniper_missing_hunter_exchange_rejected():
    """Candidate missing MEXC or Bybit gets REJECT verdict."""
    for ex_str in ("bybit", "mexc", "binance", ""):
        exchanges = _parse_exchanges(ex_str)
        verified, has_binance, reasons = _verify_hunter_exchanges(exchanges)
        assert verified is False, f"exchanges={ex_str} should not be hunter-verified"

    item = _sniper_watchlist_item(exchanges="bybit")
    exchanges = _parse_exchanges(item["exchanges"])
    verified, _, _ = _verify_hunter_exchanges(exchanges)
    verdict, _, _, risk_reasons = _assess_sniper_risk(
        item, exchanges, verified, False, "STANDARD",
    )
    assert verdict == "REJECT"
    assert any("hunter" in r or "MEXC" in r for r in risk_reasons)


# ── 15. null market_cap_usd treated as unknown, not zero ─────────────────

def test_sniper_null_market_cap_is_unknown():
    """null market_cap_usd means unknown; it should not block evaluation but increase risk."""
    item = _sniper_watchlist_item(market_cap_usd=None)
    exchanges = _parse_exchanges(item["exchanges"])
    verified, has_binance, _ = _verify_hunter_exchanges(exchanges)
    verdict, cap_known, _, risk_reasons = _assess_sniper_risk(
        item, exchanges, verified, has_binance, "STANDARD",
    )
    assert cap_known is False
    assert "market cap unknown" in risk_reasons
    assert verdict != "REJECT", "unknown market cap alone must not REJECT"


# ── 16. Missing market cap does not fabricate a value ────────────────────

@pytest.mark.asyncio
async def test_sniper_no_fabricated_market_cap():
    """Sentinel must not invent a market cap when the value is null."""
    item = _sniper_watchlist_item(market_cap_usd=None)
    ta = _mock_ta_result()

    with patch("app.sentinel.technicals.calculate", return_value=ta):
        with patch("app.brain.sleep_trader._evaluate_entry", return_value=("buy", 0.75)):
            with patch("app.sentinel.risk.generate_report", new_callable=AsyncMock) as risk_mock:
                risk_mock.return_value = MagicMock(composite_score=3.0)
                result = await _evaluate_watchlist_symbol(item)

    if result:
        meta = result.get("metadata", {})
        assert meta.get("market_cap_usd") is None
        assert meta.get("market_cap_known") is False


# ── 17. Low liquidity produces CAUTION or REJECT ────────────────────────

def test_sniper_low_liquidity_caution():
    """Low liquidity should produce CAUTION verdict."""
    item = _sniper_watchlist_item(liquidity_usd=25_000)
    exchanges = _parse_exchanges(item["exchanges"])
    verified, has_binance, _ = _verify_hunter_exchanges(exchanges)
    verdict, _, _, risk_reasons = _assess_sniper_risk(
        item, exchanges, verified, has_binance, "STANDARD",
    )
    assert verdict in ("CAUTION", "REJECT")
    assert "low liquidity" in risk_reasons


# ── 18. Admin acceptance alone does not create a signal ──────────────────

@pytest.mark.asyncio
async def test_admin_acceptance_alone_no_signal():
    """Watchlist acceptance without TA confirmation must not produce a signal."""
    item = _sniper_watchlist_item()
    ta = _mock_ta_result()

    with patch("app.sentinel.technicals.calculate", return_value=ta):
        with patch("app.brain.sleep_trader._evaluate_entry", return_value=(None, 0.30)):
            with patch("app.sentinel.risk.generate_report", new_callable=AsyncMock) as risk_mock:
                risk_mock.return_value = MagicMock(composite_score=3.0)
                result = await _evaluate_watchlist_symbol(item)

    assert result is None, "admin acceptance without TA confirmation must not produce a signal"


# ── 19. Lesson evidence alone does not create a signal ──────────────────

@pytest.mark.asyncio
async def test_lesson_evidence_alone_no_signal():
    """Lesson match without TA/risk confirmation must not produce a finalized signal."""
    import app.brain.platinum_signal_enricher as enricher

    payload = {
        "id": "sentinel-ctx-TOKEN-USDT-LONG-999",
        "symbol": "TOKEN/USDT",
        "signal": "LONG",
        "confidence": 30,
        "risk": "STANDARD",
    }
    ta = {"rsi": 50, "macd_trend": "neutral", "bb_position": "neutral"}
    enriched = enricher.enrich_signal(dict(payload), ta)
    assert enriched["confidence"] == 30, "lesson evidence must not boost confidence"


# ── 20. Actionable confirmation posts once with stable ID ────────────────

@pytest.mark.asyncio
async def test_sniper_actionable_posts_once_stable_id():
    """An actionable sniper signal posts exactly once; same item retried uses cursor dedup."""
    item = _sniper_watchlist_item()
    ctx = _sigbalbot_context_response()
    ctx["watchlist"] = [item]
    ctx["native_signals"] = []
    mock_ctx_resp = _make_mock_response(200, ctx)
    mock_post_resp = _make_mock_response(202, {"event_id": "evt_sniper", "telegram_sent": True})

    post_calls = []

    async def capture_post(url, json=None, headers=None):
        post_calls.append(json)
        return mock_post_resp

    ta = _mock_ta_result()

    with patch.dict(os.environ, {
        "SIGBALBOT_WEBHOOK_URL": "https://fake.sigbalbot.test/api/v1/signals/trader-sentinel",
        "SIGBALBOT_API_KEY": "test-key-e2e",
    }):
        import app.brain.sigbalbot_context_poller as poller
        poller._CONTEXT_BASE_URL = None
        poller._API_KEY = None
        publisher._WEBHOOK_URL = None
        publisher._API_KEY = None

        with patch("app.brain.store.load_sigbalbot_cursor", return_value={"processed_signal_ids": []}):
            with patch("app.brain.store.save_sigbalbot_cursor"):
                with patch("httpx.AsyncClient") as mock_client_cls:
                    mock_client = AsyncMock()
                    mock_client.get = AsyncMock(return_value=mock_ctx_resp)
                    mock_client.post = capture_post
                    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                    mock_client.__aexit__ = AsyncMock(return_value=False)
                    mock_client_cls.return_value = mock_client

                    with patch("app.sentinel.technicals.calculate", return_value=ta):
                        with patch("app.brain.sleep_trader._evaluate_entry", return_value=("buy", 0.72)):
                            with patch("app.sentinel.risk.generate_report", new_callable=AsyncMock) as risk_mock:
                                risk_mock.return_value = MagicMock(composite_score=3.0)
                                summary = await poll_and_evaluate()

    assert summary["signals_posted"] == 1
    assert len(post_calls) == 1
    posted = post_calls[0]
    assert posted["id"].startswith("sentinel-ctx-")
    meta = posted.get("metadata", {})
    assert meta.get("sentinel_verdict") == "CONFIRM"
    assert meta.get("source_exchanges") == ["mexc", "bybit", "binance"]
    assert meta.get("liquidity_definition") == "visible_order_book_depth_2pct"


# ── 21. Duplicate retry does not publish twice ───────────────────────────

@pytest.mark.asyncio
async def test_sniper_duplicate_retry_no_double_post():
    """Second poll with same watchlist item (already in cursor) does not re-evaluate."""
    item = _sniper_watchlist_item()
    ctx = _sigbalbot_context_response()
    ctx["watchlist"] = [item]
    ctx["native_signals"] = []
    mock_resp = _make_mock_response(200, ctx)

    wl_key = f"wl:{item['symbol']}:{item['last_scan_at']}"

    with patch.dict(os.environ, {
        "SIGBALBOT_WEBHOOK_URL": "https://fake.sigbalbot.test/api/v1/signals/trader-sentinel",
        "SIGBALBOT_API_KEY": "test-key-e2e",
    }):
        import app.brain.sigbalbot_context_poller as poller
        poller._CONTEXT_BASE_URL = None
        poller._API_KEY = None

        already = {"processed_signal_ids": [wl_key]}
        with patch("app.brain.store.load_sigbalbot_cursor", return_value=already):
            with patch("app.brain.store.save_sigbalbot_cursor"):
                with patch("httpx.AsyncClient") as mock_client_cls:
                    mock_client = AsyncMock()
                    mock_client.get = AsyncMock(return_value=mock_resp)
                    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                    mock_client.__aexit__ = AsyncMock(return_value=False)
                    mock_client_cls.return_value = mock_client

                    summary = await poll_and_evaluate()

    assert summary["skipped_duplicates"] >= 1
    assert summary["watchlist_evaluated"] == 0
    assert summary["signals_posted"] == 0


# ── 22. Feedback loop guard for sentinel-origin signals ──────────────────

def test_feedback_loop_guard_sentinel_ctx():
    """sentinel-ctx- prefixed signals are also caught by the feedback loop guard."""
    assert _is_sentinel_signal("sentinel-ctx-TOKEN-USDT-LONG-9999") is True
    assert _is_sentinel_signal("sigbal-sniper-TOKEN-LONG-123") is False


# ════════════════════════════════════════════════════════════════════════════
# Asset-class routing alignment tests
# ════════════════════════════════════════════════════════════════════════════

# ── 23. Crypto fallback when asset_class absent ────────────────────────────

@pytest.mark.asyncio
async def test_asset_class_crypto_fallback():
    """Watchlist item without asset_class defaults to crypto."""
    item = _sniper_watchlist_item()
    assert "asset_class" not in item
    ta = _mock_ta_result()

    with patch("app.sentinel.technicals.calculate", return_value=ta):
        with patch("app.brain.sleep_trader._evaluate_entry", return_value=("buy", 0.75)):
            with patch("app.sentinel.risk.generate_report", new_callable=AsyncMock) as risk_mock:
                risk_mock.return_value = MagicMock(composite_score=3.0)
                result = await _evaluate_watchlist_symbol(item)

    assert result is not None
    assert result["asset_class"] == "crypto"
    assert result["metadata"]["asset_class"] == "crypto"


# ── 24. Equities signal preserves asset_class ─────────────────────────────

@pytest.mark.asyncio
async def test_asset_class_equities_signal():
    """Watchlist item with asset_class=equities produces a signal with equities tag."""
    item = _sniper_watchlist_item(asset_class="equities", symbol="AAPL/USD")
    ta = _mock_ta_result(price=185.0)

    with patch("app.sentinel.technicals.calculate", return_value=ta):
        with patch("app.brain.sleep_trader._evaluate_entry", return_value=("buy", 0.70)):
            with patch("app.sentinel.risk.generate_report", new_callable=AsyncMock) as risk_mock:
                risk_mock.return_value = MagicMock(composite_score=2.5)
                result = await _evaluate_watchlist_symbol(item)

    assert result is not None
    assert result["asset_class"] == "equities"
    assert result["metadata"]["asset_class"] == "equities"


# ── 25. Metals signal preserves asset_class ───────────────────────────────

@pytest.mark.asyncio
async def test_asset_class_metals_signal():
    """Watchlist item with asset_class=metals produces a signal with metals tag."""
    item = _sniper_watchlist_item(asset_class="metals", symbol="XAU/USD")
    ta = _mock_ta_result(price=2450.0)

    with patch("app.sentinel.technicals.calculate", return_value=ta):
        with patch("app.brain.sleep_trader._evaluate_entry", return_value=("buy", 0.68)):
            with patch("app.sentinel.risk.generate_report", new_callable=AsyncMock) as risk_mock:
                risk_mock.return_value = MagicMock(composite_score=3.5)
                result = await _evaluate_watchlist_symbol(item)

    assert result is not None
    assert result["asset_class"] == "metals"
    assert result["metadata"]["asset_class"] == "metals"


# ── 26. Equities review stable ID and dedup ───────────────────────────────

def test_equities_review_stable_id_dedup():
    """Equities review ID is distinct from crypto and stable across calls."""
    from datetime import datetime, timezone

    now = datetime(2026, 8, 13, 14, 30, tzinfo=timezone.utc)
    crypto_id = current_review_id(now=now, asset_class="crypto")
    equities_id = current_review_id(now=now, asset_class="equities")

    assert crypto_id != equities_id
    assert "crypto" in crypto_id
    assert "equities" in equities_id
    assert equities_id == current_review_id(now=now, asset_class="equities")


# ── 27. Metals review stable ID and dedup ─────────────────────────────────

def test_metals_review_stable_id_dedup():
    """Metals review ID is distinct from crypto/equities and stable."""
    from datetime import datetime, timezone

    now = datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc)
    crypto_id = current_review_id(now=now, asset_class="crypto")
    metals_id = current_review_id(now=now, asset_class="metals")

    assert crypto_id != metals_id
    assert "metals" in metals_id
    assert metals_id == current_review_id(now=now, asset_class="metals")

    payload = build_review_payload(
        {"risk": {"composite_score": 4.0, "recommendation": {}, "alerts": []},
         "assets": {}, "sessions": {}, "bias": {}},
        metals_id,
        asset_class="metals",
    )
    assert payload["asset_class"] == "metals"
    assert payload["id"] == metals_id


# ── 28. Identical ticker across asset classes not conflated ───────────────

@pytest.mark.asyncio
async def test_identical_ticker_not_conflated():
    """Same ticker in different asset classes produces distinct signal IDs."""
    item_crypto = _sniper_watchlist_item(asset_class="crypto", symbol="GOLD/USDT")
    item_metals = _sniper_watchlist_item(asset_class="metals", symbol="GOLD/USDT")
    ta = _mock_ta_result(price=1.50)

    results = []
    for item in (item_crypto, item_metals):
        with patch("app.sentinel.technicals.calculate", return_value=ta):
            with patch("app.brain.sleep_trader._evaluate_entry", return_value=("buy", 0.72)):
                with patch("app.sentinel.risk.generate_report", new_callable=AsyncMock) as risk_mock:
                    risk_mock.return_value = MagicMock(composite_score=3.0)
                    r = await _evaluate_watchlist_symbol(item)
        if r:
            results.append(r)

    assert len(results) == 2
    assert results[0]["asset_class"] == "crypto"
    assert results[1]["asset_class"] == "metals"
    assert results[0]["asset_class"] != results[1]["asset_class"]
    assert results[0]["metadata"]["asset_class"] != results[1]["metadata"]["asset_class"]


# ── 29. Missing exact instrument produces no signal ───────────────────────

@pytest.mark.asyncio
async def test_missing_instrument_no_signal():
    """Watchlist item with empty symbol produces no signal regardless of asset_class."""
    item = _sniper_watchlist_item(asset_class="equities", symbol="")
    ta = _mock_ta_result()

    with patch("app.sentinel.technicals.calculate", return_value=ta):
        with patch("app.brain.sleep_trader._evaluate_entry", return_value=("buy", 0.80)):
            with patch("app.sentinel.risk.generate_report", new_callable=AsyncMock) as risk_mock:
                risk_mock.return_value = MagicMock(composite_score=2.0)
                result = await _evaluate_watchlist_symbol(item)

    assert result is None


# ── 30. Learning journal groups outcomes by asset_class ───────────────────

def test_learning_journal_groups_by_asset_class():
    """Learning journal entries are stored and retrieved per asset_class."""
    import tempfile
    from pathlib import Path

    tmp = tempfile.mkdtemp(prefix="journal_test_")
    original_dir = brain_store.LEARNING_JOURNAL_DIR
    brain_store.LEARNING_JOURNAL_DIR = Path(tmp)

    try:
        brain_store.append_learning_entry("crypto", {"signal_id": "s1", "outcome": "win", "ts": "2026-08-13T10:00:00Z"})
        brain_store.append_learning_entry("equities", {"signal_id": "s2", "outcome": "loss", "ts": "2026-08-13T10:01:00Z"})
        brain_store.append_learning_entry("crypto", {"signal_id": "s3", "outcome": "win", "ts": "2026-08-13T10:02:00Z"})

        crypto_entries = brain_store.load_learning_journal("crypto")
        equities_entries = brain_store.load_learning_journal("equities")
        metals_entries = brain_store.load_learning_journal("metals")

        assert len(crypto_entries) == 2
        assert len(equities_entries) == 1
        assert len(metals_entries) == 0
        assert crypto_entries[0]["signal_id"] == "s1"
        assert crypto_entries[1]["signal_id"] == "s3"
        assert equities_entries[0]["signal_id"] == "s2"
    finally:
        brain_store.LEARNING_JOURNAL_DIR = original_dir
