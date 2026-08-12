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
    fetch_context,
    poll_and_evaluate,
)

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
