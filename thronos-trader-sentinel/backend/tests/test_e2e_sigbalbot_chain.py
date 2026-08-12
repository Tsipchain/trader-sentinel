"""
End-to-end integration tests for the full SigBalBot ↔ Sentinel chain.

All HTTP boundaries (SigBalBot status, context, signal POST) are mocked.
Tests exercise the complete flow: capability check → context poll →
TA evaluation → signal publish → wallet snapshot ingestion.

Scenarios:
  1. Happy path: status OK → context with watchlist + native → TA agrees → signal posted (202)
  2. Capability gate: status missing context_feed → poller does not poll
  3. Context auth failure: 401 → no evaluation, no signals posted
  4. Watchlist produces signal, native disagrees → only watchlist signal posted
  5. Feedback loop: Sentinel's own signal returned in native_signals → skipped
  6. Wallet snapshots ingested during context poll → confidence boost available
  7. Signal POST 503 retry then 202 → signal delivered after retry
"""
import asyncio
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

os.environ["SIGBALBOT_WEBHOOK_URL"] = "https://sigbalbot.up.railway.app/api/v1/signals/trader-sentinel"
os.environ["SIGBALBOT_API_KEY"] = "test-e2e-key-789"

_tmp_dir = tempfile.mkdtemp(prefix="e2e_wallet_snap_")
os.environ["WALLET_SNAPSHOT_DIR"] = _tmp_dir

import app.brain.sigbalbot_context_poller as poller
import app.brain.sigbalbot_publisher as publisher
import app.brain.wallet_snapshot_consumer as wsc

poller._CONTEXT_BASE_URL = None
poller._API_KEY = None
publisher._WEBHOOK_URL = None
publisher._API_KEY = None
wsc.SNAPSHOT_DIR = Path(_tmp_dir)


def _mock_response(status_code: int, body: dict, method: str = "GET") -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        json=body,
        request=httpx.Request(method, "https://sigbalbot.up.railway.app"),
    )


class MockTAResult:
    def __init__(self, price=65000, rsi=32, macd_trend="bullish", error=None, side="buy"):
        self.rsi_14 = rsi
        self.rsi_signal = "oversold" if rsi < 30 else "neutral"
        self.macd_trend = macd_trend
        self.macd_histogram = 50 if macd_trend == "bullish" else -50
        self.bb_signal = "oversold" if side == "buy" else "overbought"
        self.bb_pct = 0.1
        self.ema_cross = "golden_cross" if side == "buy" else "death_cross"
        self.williams_r = -85 if side == "buy" else -15
        self.williams_r_signal = "oversold" if side == "buy" else "overbought"
        self.score = 3.5
        self.current_price = price
        self.error = error
        self.candles_raw = None


STATUS_OK = {
    "status": "ok",
    "contract_version": "1.0",
    "capabilities": {"context_feed": "/api/v1/context/trader-sentinel"},
}

CONTEXT_FULL = {
    "ok": True,
    "contract_version": "1.0",
    "generated_at": "2026-01-20T14:00:00Z",
    "watchlist": [
        {
            "symbol": "BTC/USDT",
            "label": "BTC",
            "mode": "sniper",
            "market_cap_usd": 1_200_000_000_000,
            "volume_24h": 45_000_000_000,
            "liquidity_usd": 500_000_000,
            "last_scan_at": "2026-01-20T13:55:00Z",
            "last_scan_signal": "HOLD",
        },
    ],
    "native_signals": [
        {
            "id": "sigbal-ETH-USDT-AGG_LONG-1737370800",
            "symbol": "ETH/USDT",
            "signal": "AGG_LONG",
            "timeframe": "15m",
            "price": 3200,
            "confidence": 85,
            "risk": "STANDARD",
            "reason": "Multi-indicator convergence",
            "created_at": "2026-01-20T13:58:00Z",
        },
    ],
    "wallet_snapshots": [
        {
            "address": "THR" + "A" * 37 + "0",
            "tier": "pro",
            "rewards_multiplier": 1.5,
            "active": True,
            "verified": True,
            "expires_at": int(time.time()) + 86400,
            "thr_balance": 150.0,
            "snapshot_at": int(time.time()),
        },
        {
            "address": "THR" + "B" * 37 + "1",
            "tier": "starter",
            "rewards_multiplier": 1.0,
            "active": True,
            "verified": True,
            "expires_at": int(time.time()) + 86400,
            "thr_balance": 50.0,
            "snapshot_at": int(time.time()),
        },
    ],
}

POST_202 = {
    "ok": True,
    "duplicate": False,
    "telegram_sent": True,
    "event_id": "evt-e2e-001",
    "outcomes_scheduled": 4,
    "contract_version": "1.0",
}


@pytest.fixture(autouse=True)
def _reset_poller_state():
    poller._context_capability_confirmed = False
    poller._contract_version = None
    poller._CONTEXT_BASE_URL = None
    poller._API_KEY = None
    publisher._WEBHOOK_URL = None
    publisher._API_KEY = None
    snap_path = wsc._snapshot_path()
    if snap_path.exists():
        snap_path.unlink()
    yield
    if snap_path.exists():
        snap_path.unlink()


def _make_httpx_ctx(mock_client):
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


# ── Scenario 1: Happy path ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_e2e_happy_path_status_context_evaluate_post():
    """Full chain: status OK → context → TA agrees on watchlist → signal posted 202."""

    WATCHLIST_SIGNAL = {
        "id": "sentinel-ctx-BTC-USDT-LONG-1737370800",
        "symbol": "BTC/USDT",
        "signal": "LONG",
        "timeframe": "1h",
        "price": 65000,
        "confidence": 72,
        "risk": "STANDARD",
        "reason": "SigBalBot watchlist (sniper:BTC) confirmed by Sentinel TA",
        "strategy": "sigbalbot-context-watchlist",
        "market_regime": "bullish",
        "confluence_score": 0.72,
        "model_version": "pytheia-sentinel-v0.2",
        "confirmations": ["RSI 28", "MACD bullish"],
        "entry": 65000,
        "tp1": None,
        "tp2": None,
        "sl": None,
        "metadata": {"source": "sigbalbot_context"},
    }

    NATIVE_SIGNAL = {
        "id": "sentinel-ctx-native-ETH-USDT-LONG-1737370800",
        "symbol": "ETH/USDT",
        "signal": "LONG",
        "timeframe": "15m",
        "price": 3200,
        "confidence": 82,
        "risk": "STANDARD",
        "reason": "SigBalBot native signal confirmed by Sentinel TA",
        "strategy": "sigbalbot-context-native",
        "market_regime": "bullish",
        "confluence_score": 0.75,
        "model_version": "pytheia-sentinel-v0.2",
        "confirmations": ["RSI 28", "MACD bullish", "SigBalBot AGG_LONG (85%)"],
        "entry": 3200,
        "tp1": None,
        "tp2": None,
        "sl": None,
        "metadata": {"source": "sigbalbot_context_native", "sigbalbot_signal_id": "sigbal-ETH-USDT-AGG_LONG-1737370800"},
    }

    # Phase 1: capability check
    status_client = AsyncMock()
    status_client.get = AsyncMock(return_value=_mock_response(200, STATUS_OK))

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _make_httpx_ctx(status_client)
        cap = await poller.check_context_capability()

    assert cap is True
    assert poller._contract_version == "1.0"

    # Phase 2: poll cycle — mock at evaluation boundary (not deep TA internals)
    with (
        patch.object(poller, "fetch_context", new_callable=AsyncMock) as mock_fetch,
        patch("app.brain.store.load_sigbalbot_cursor") as mock_load,
        patch("app.brain.store.save_sigbalbot_cursor") as mock_save,
        patch.object(poller, "_evaluate_watchlist_symbol", new_callable=AsyncMock, return_value=WATCHLIST_SIGNAL),
        patch.object(poller, "_evaluate_native_signal", new_callable=AsyncMock, return_value=NATIVE_SIGNAL),
        patch.object(poller.sigbalbot_publisher, "is_configured", return_value=True),
        patch.object(poller.sigbalbot_publisher, "publish_signal", new_callable=AsyncMock) as mock_publish,
    ):
        mock_fetch.return_value = CONTEXT_FULL
        mock_load.return_value = {"last_cursor": None, "processed_signal_ids": []}
        mock_publish.return_value = POST_202

        summary = await poller.poll_and_evaluate()

    assert "error" not in summary
    assert summary["context_items"] == 2
    assert summary["watchlist_evaluated"] == 1
    assert summary["native_signals_evaluated"] == 1
    assert summary["signals_posted"] == 2
    assert summary["wallet_snapshots_ingested"] == 2
    assert summary["skipped_feedback_loop"] == 0

    mock_save.assert_called_once()
    saved = mock_save.call_args[0][0]
    assert saved["last_cursor"] is not None

    posted_calls = mock_publish.call_args_list
    assert len(posted_calls) == 2
    posted_ids = {c[0][0]["id"] for c in posted_calls}
    assert "sentinel-ctx-BTC-USDT-LONG-1737370800" in posted_ids
    assert "sentinel-ctx-native-ETH-USDT-LONG-1737370800" in posted_ids

    # Wallet snapshots were ingested
    assert wsc.get_active_subscriber_count() == 2


# ── Scenario 2: Capability gate ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_e2e_capability_gate_blocks_polling():
    """Status endpoint missing context_feed → poller never calls fetch_context."""

    status_no_cap = {
        "status": "ok",
        "contract_version": "1.0",
        "capabilities": {},
    }

    status_client = AsyncMock()
    status_client.get = AsyncMock(return_value=_mock_response(200, status_no_cap))

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _make_httpx_ctx(status_client)
        cap = await poller.check_context_capability()

    assert cap is False
    assert poller._context_capability_confirmed is False

    # Simulate one iteration of _poll_loop where capability is not confirmed
    iteration = 0

    async def fake_sleep(seconds):
        nonlocal iteration
        iteration += 1
        if iteration >= 1:
            raise asyncio.CancelledError()

    with (
        patch.object(poller, "check_context_capability", new_callable=AsyncMock, return_value=False),
        patch.object(poller, "poll_and_evaluate", new_callable=AsyncMock) as mock_poll,
        patch("asyncio.sleep", side_effect=fake_sleep),
    ):
        with pytest.raises(asyncio.CancelledError):
            await poller._poll_loop()

    mock_poll.assert_not_called()


# ── Scenario 3: Context auth failure ───────────────────────────────────────

@pytest.mark.asyncio
async def test_e2e_context_401_no_evaluation():
    """Context endpoint returns 401 → no TA evaluation, no signals posted."""

    # Confirm capability first
    poller._context_capability_confirmed = True
    poller._contract_version = "1.0"

    # Mock fetch_context to return None (as it would on 401)
    with (
        patch.object(poller, "fetch_context", new_callable=AsyncMock, return_value=None) as mock_fetch,
        patch("app.brain.store.load_sigbalbot_cursor") as mock_load,
        patch.object(poller.sigbalbot_publisher, "publish_signal", new_callable=AsyncMock) as mock_publish,
    ):
        mock_load.return_value = {"last_cursor": None, "processed_signal_ids": []}

        summary = await poller.poll_and_evaluate()

    assert summary["error"] == "fetch_failed"
    assert summary["watchlist_evaluated"] == 0
    assert summary["native_signals_evaluated"] == 0
    assert summary["signals_posted"] == 0
    mock_publish.assert_not_called()

    # Also verify fetch_context itself returns None on 401
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_response(401, {"error": "unauthorized"}))

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _make_httpx_ctx(mock_client)
        result = await poller.fetch_context()

    assert result is None
    mock_client.get.assert_called_once()


# ── Scenario 4: Watchlist agrees, native disagrees ─────────────────────────

@pytest.mark.asyncio
async def test_e2e_watchlist_agrees_native_disagrees():
    """Watchlist symbol TA agrees → signal posted. Native signal TA disagrees → skipped."""

    WATCHLIST_RESULT = {
        "id": "sentinel-ctx-BTC-USDT-LONG-1737370800",
        "symbol": "BTC/USDT",
        "signal": "LONG",
        "timeframe": "1h",
        "price": 65000,
        "confidence": 72,
        "risk": "STANDARD",
        "reason": "Watchlist confirmed",
        "strategy": "sigbalbot-context-watchlist",
        "market_regime": "bullish",
        "confluence_score": 0.72,
        "model_version": "pytheia-sentinel-v0.2",
        "confirmations": ["RSI 28"],
        "entry": 65000,
        "tp1": None,
        "tp2": None,
        "sl": None,
        "metadata": {"source": "sigbalbot_context"},
    }

    context = {
        **CONTEXT_FULL,
        "native_signals": [
            {
                "id": "sigbal-SOL-USDT-AGG_SHORT-1737370800",
                "symbol": "SOL/USDT",
                "signal": "AGG_SHORT",
                "timeframe": "15m",
                "price": 220,
                "confidence": 80,
                "risk": "STANDARD",
                "reason": "Bearish setup",
                "created_at": "2026-01-20T13:58:00Z",
            },
        ],
        "wallet_snapshots": [],
    }

    with (
        patch.object(poller, "fetch_context", new_callable=AsyncMock, return_value=context),
        patch("app.brain.store.load_sigbalbot_cursor") as mock_load,
        patch("app.brain.store.save_sigbalbot_cursor"),
        patch.object(poller, "_evaluate_watchlist_symbol", new_callable=AsyncMock, return_value=WATCHLIST_RESULT),
        patch.object(poller, "_evaluate_native_signal", new_callable=AsyncMock, return_value=None),
        patch.object(poller.sigbalbot_publisher, "is_configured", return_value=True),
        patch.object(poller.sigbalbot_publisher, "publish_signal", new_callable=AsyncMock) as mock_publish,
    ):
        mock_load.return_value = {"last_cursor": None, "processed_signal_ids": []}
        mock_publish.return_value = POST_202

        summary = await poller.poll_and_evaluate()

    assert summary["watchlist_evaluated"] == 1
    assert summary["native_signals_evaluated"] == 1

    # Watchlist signal posted, native returned None (disagreement) → only 1 signal
    assert summary["signals_posted"] == 1
    posted_calls = mock_publish.call_args_list
    assert len(posted_calls) == 1

    posted = posted_calls[0][0][0]
    assert posted["symbol"] == "BTC/USDT"
    assert posted["signal"] == "LONG"
    assert posted["strategy"] == "sigbalbot-context-watchlist"


# ── Scenario 5: Feedback loop guard ────────────────────────────────────────

@pytest.mark.asyncio
async def test_e2e_feedback_loop_sentinel_signal_skipped():
    """Native signal with sentinel- prefix is never evaluated (feedback loop guard)."""

    context = {
        "ok": True,
        "contract_version": "1.0",
        "generated_at": "2026-01-20T14:00:00Z",
        "watchlist": [],
        "native_signals": [
            {
                "id": "sentinel-BTC-USDT-LONG-1737370800",
                "symbol": "BTC/USDT",
                "signal": "LONG",
                "timeframe": "1h",
                "price": 65000,
                "confidence": 80,
                "risk": "STANDARD",
                "reason": "Sentinel auto-signal",
                "created_at": "2026-01-20T13:58:00Z",
            },
            {
                "id": "sentinel-ctx-ETH-USDT-SHORT-1737370900",
                "symbol": "ETH/USDT",
                "signal": "SHORT",
                "timeframe": "1h",
                "price": 3200,
                "confidence": 75,
                "risk": "ELEVATED",
                "reason": "Sentinel watchlist signal",
                "created_at": "2026-01-20T13:59:00Z",
            },
        ],
    }

    with (
        patch.object(poller, "fetch_context", new_callable=AsyncMock, return_value=context),
        patch("app.brain.store.load_sigbalbot_cursor") as mock_load,
        patch("app.brain.store.save_sigbalbot_cursor"),
        patch.object(poller, "_evaluate_native_signal", new_callable=AsyncMock) as mock_eval_native,
        patch.object(poller.sigbalbot_publisher, "is_configured", return_value=True),
        patch.object(poller.sigbalbot_publisher, "publish_signal", new_callable=AsyncMock) as mock_publish,
    ):
        mock_load.return_value = {"last_cursor": None, "processed_signal_ids": []}

        summary = await poller.poll_and_evaluate()

    assert summary["skipped_feedback_loop"] == 2
    assert summary["native_signals_evaluated"] == 0
    mock_eval_native.assert_not_called()
    mock_publish.assert_not_called()


# ── Scenario 6: Wallet snapshots ingested ──────────────────────────────────

@pytest.mark.asyncio
async def test_e2e_wallet_snapshots_ingested_during_poll():
    """Context response with wallet_snapshots → ingested, confidence boost available."""

    many_subscribers = []
    for i in range(10):
        many_subscribers.append({
            "address": f"THR{'C' * 37}{i}",
            "tier": "pro" if i % 2 == 0 else "starter",
            "rewards_multiplier": 1.5 if i % 2 == 0 else 1.0,
            "active": True,
            "verified": True,
            "expires_at": int(time.time()) + 86400,
            "thr_balance": 100.0 + i * 25,
            "snapshot_at": int(time.time()),
        })

    context = {
        "ok": True,
        "contract_version": "1.0",
        "generated_at": "2026-01-20T14:00:00Z",
        "watchlist": [],
        "native_signals": [],
        "wallet_snapshots": many_subscribers,
    }

    with (
        patch.object(poller, "fetch_context", new_callable=AsyncMock, return_value=context),
        patch("app.brain.store.load_sigbalbot_cursor") as mock_load,
        patch("app.brain.store.save_sigbalbot_cursor"),
    ):
        mock_load.return_value = {"last_cursor": None, "processed_signal_ids": []}

        summary = await poller.poll_and_evaluate()

    assert summary["wallet_snapshots_ingested"] == 10
    assert wsc.get_active_subscriber_count() == 10
    assert wsc.get_confidence_boost() == 0.01
    assert wsc.is_snapshot_fresh() is True

    tiers = wsc.get_subscriber_tiers()
    assert tiers["pro"] == 5
    assert tiers["starter"] == 5

    status = wsc.get_snapshot_status()
    assert status["has_snapshot"] is True
    assert status["total_subscribers"] == 10
    assert status["fresh"] is True


# ── Scenario 7: Signal POST 503 retry then 202 ────────────────────────────

@pytest.mark.asyncio
async def test_e2e_signal_post_503_then_202():
    """Signal POST returns 503 on first attempt, 202 on second → delivered."""

    payload = publisher.build_signal_payload(
        {
            "symbol": "BTC/USDT",
            "side": "buy",
            "amount": 0.003,
            "entry_price": 65432.10,
            "leverage": 10,
            "sl_price": 64777.88,
            "tp_price": 66088.42,
            "confidence": 0.72,
            "ta_summary": {"rsi": 32, "macd": "bullish", "ema": "golden_cross"},
            "strategies": {
                "divergence": {"type": "regular_bullish", "double_confirmed": True},
                "confluence": {"level": "strong", "agreeing_tf": 3, "confidence_bonus": 0.15, "direction": "long"},
            },
            "opened_at": 1737370800,
            "status": "open",
            "order_id": "ord-e2e",
            "market_type": "futures",
        },
        {"rsi": 32, "macd_trend": "bullish", "ema_cross": "golden_cross", "price": 65432.10},
    )

    assert payload["id"] == "sentinel-BTC-USDT-LONG-1737370800"
    assert payload["signal"] == "LONG"

    resp_503 = _mock_response(503, {"ok": False, "retryable": True, "event_id": "evt-retry", "contract_version": "1.0"}, "POST")
    resp_202 = _mock_response(202, POST_202, "POST")

    call_count = 0

    async def post_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return resp_503
        return resp_202

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=post_side_effect)

    with (
        patch("httpx.AsyncClient") as mock_cls,
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        mock_cls.return_value = _make_httpx_ctx(mock_client)

        result = await publisher.publish_signal(payload)

    assert result is not None
    assert result["ok"] is True
    assert result["telegram_sent"] is True
    assert result["event_id"] == "evt-e2e-001"
    assert call_count == 2


# ── Bonus: contract version rejection in full chain ────────────────────────

@pytest.mark.asyncio
async def test_e2e_unsupported_contract_version_rejects_context():
    """Context response with unsupported contract_version → entire response rejected."""

    poller._context_capability_confirmed = True
    poller._contract_version = "1.0"

    context_bad_version = {
        "ok": True,
        "contract_version": "2.0",
        "generated_at": "2026-01-20T14:00:00Z",
        "watchlist": [{"symbol": "BTC/USDT", "mode": "watch"}],
        "native_signals": [],
    }

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_response(200, context_bad_version))

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _make_httpx_ctx(mock_client)
        result = await poller.fetch_context()

    assert result is None


# ── Bonus: auth header not leaked in URL ───────────────────────────────────

@pytest.mark.asyncio
async def test_e2e_api_key_in_header_not_url():
    """API key sent via Bearer header, never as query parameter."""

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_response(200, CONTEXT_FULL))

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _make_httpx_ctx(mock_client)
        await poller.fetch_context(since="2026-01-20T13:00:00Z")

    call_kwargs = mock_client.get.call_args
    headers = call_kwargs.kwargs.get("headers", {})
    assert "Authorization" in headers
    assert headers["Authorization"].startswith("Bearer ")

    params = call_kwargs.kwargs.get("params", {})
    assert "api_key" not in str(params).lower()
    assert "SIGBALBOT_API_KEY" not in str(params)

    url_called = str(call_kwargs.args[0] if call_kwargs.args else "")
    assert "api_key" not in url_called.lower()
