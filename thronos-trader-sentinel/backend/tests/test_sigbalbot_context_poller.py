"""
Tests for the SigBalBot Context Poller.

Covers:
  1. Authentication (Bearer header sent correctly)
  2. Cursor persistence (saved after poll, loaded on next)
  3. Duplicate detection (already-processed items skipped)
  4. Empty feeds (no crash, no signals posted)
  5. Watchlist evaluation (symbol runs through Sentinel TA)
  6. Native signal evaluation (agreement / disagreement with Sentinel)
  7. No feedback loop (Sentinel's own signals are skipped)
  8. Signal ID stability
  9. Poller status reporting
"""
import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

# Set env before import
os.environ["SIGBALBOT_WEBHOOK_URL"] = "https://sigbalbot.up.railway.app/api/v1/signals/trader-sentinel"
os.environ["SIGBALBOT_API_KEY"] = "test-poller-key-456"

import app.brain.sigbalbot_context_poller as poller

# Reset cached config
poller._CONTEXT_BASE_URL = None
poller._API_KEY = None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _mock_response(status_code: int, body: dict) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        json=body,
        request=httpx.Request("GET", "https://example.com"),
    )


SAMPLE_CONTEXT_RESPONSE = {
    "ok": True,
    "contract_version": "1.0",
    "generated_at": "2025-01-15T12:00:00Z",
    "watchlist": [
        {
            "symbol": "BSB/USDT",
            "label": "BSB",
            "mode": "sniper",
            "market_cap_usd": 9000000,
            "volume_24h": 1250000,
            "liquidity_usd": 340000,
            "last_scan_at": "2025-01-15T11:55:00Z",
            "last_scan_signal": "HOLD",
            "last_scan_reason": "No clear signal",
        }
    ],
    "native_signals": [
        {
            "id": 123,
            "symbol": "BANK/USDT",
            "signal": "AGG_LONG",
            "timeframe": "15m",
            "price": 0.04,
            "confidence": 82,
            "risk": "STANDARD",
            "reason": "Bullish divergence detected",
            "payload_json": "{}",
            "created_at": "2025-01-15T11:58:00Z",
        }
    ],
}


class MockTAResult:
    """Mock for tech_module.calculate() return value."""
    def __init__(self, price=65000, rsi=32, macd_trend="bullish", error=None):
        self.rsi_14 = rsi
        self.rsi_signal = "oversold" if rsi < 30 else "neutral"
        self.macd_trend = macd_trend
        self.macd_histogram = 50 if macd_trend == "bullish" else -50
        self.bb_signal = "oversold"
        self.bb_pct = 0.1
        self.ema_cross = "golden_cross"
        self.williams_r = -85
        self.williams_r_signal = "oversold"
        self.score = 3.5
        self.current_price = price
        self.error = error
        self.candles_raw = None


# ── 1. Authentication ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_context_sends_bearer_auth():
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_response(200, SAMPLE_CONTEXT_RESPONSE))

    with patch("httpx.AsyncClient") as mock_cls:
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = ctx

        result = await poller.fetch_context(since=None)

    assert result is not None
    assert result["ok"] is True
    call_kwargs = mock_client.get.call_args
    headers = call_kwargs.kwargs.get("headers", {})
    assert "Authorization" in headers
    assert headers["Authorization"] == "Bearer test-poller-key-456"


@pytest.mark.asyncio
async def test_fetch_context_401_returns_none():
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_response(401, {"error": "unauthorized"}))

    with patch("httpx.AsyncClient") as mock_cls:
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = ctx

        result = await poller.fetch_context()

    assert result is None
    mock_client.get.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_context_403_returns_none():
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_response(403, {"error": "forbidden"}))

    with patch("httpx.AsyncClient") as mock_cls:
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = ctx

        result = await poller.fetch_context()

    assert result is None
    mock_client.get.assert_called_once()


# ── 2. Cursor persistence ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cursor_persisted_after_poll():
    with (
        patch.object(poller, "fetch_context", new_callable=AsyncMock) as mock_fetch,
        patch("app.brain.store.load_sigbalbot_cursor") as mock_load,
        patch("app.brain.store.save_sigbalbot_cursor") as mock_save,
        patch.object(poller, "_evaluate_watchlist_symbol", new_callable=AsyncMock, return_value=None),
        patch.object(poller, "_evaluate_native_signal", new_callable=AsyncMock, return_value=None),
    ):
        mock_fetch.return_value = SAMPLE_CONTEXT_RESPONSE
        mock_load.return_value = {"last_cursor": None, "processed_signal_ids": []}

        await poller.poll_and_evaluate()

        mock_save.assert_called_once()
        saved = mock_save.call_args[0][0]
        assert saved["last_cursor"] == "2025-01-15T12:00:00Z"
        assert "last_poll_at" in saved


@pytest.mark.asyncio
async def test_cursor_sent_as_since_param():
    with (
        patch.object(poller, "fetch_context", new_callable=AsyncMock) as mock_fetch,
        patch("app.brain.store.load_sigbalbot_cursor") as mock_load,
        patch("app.brain.store.save_sigbalbot_cursor"),
        patch.object(poller, "_evaluate_watchlist_symbol", new_callable=AsyncMock, return_value=None),
        patch.object(poller, "_evaluate_native_signal", new_callable=AsyncMock, return_value=None),
    ):
        mock_fetch.return_value = {**SAMPLE_CONTEXT_RESPONSE, "watchlist": [], "native_signals": []}
        mock_load.return_value = {"last_cursor": "2025-01-15T10:00:00Z", "processed_signal_ids": []}

        await poller.poll_and_evaluate()

        mock_fetch.assert_called_once_with(since="2025-01-15T10:00:00Z")


# ── 3. Duplicate detection ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_duplicate_watchlist_item_skipped():
    wl_key = "wl:BSB/USDT:2025-01-15T11:55:00Z"
    with (
        patch.object(poller, "fetch_context", new_callable=AsyncMock) as mock_fetch,
        patch("app.brain.store.load_sigbalbot_cursor") as mock_load,
        patch("app.brain.store.save_sigbalbot_cursor"),
        patch.object(poller, "_evaluate_watchlist_symbol", new_callable=AsyncMock) as mock_eval_wl,
        patch.object(poller, "_evaluate_native_signal", new_callable=AsyncMock, return_value=None),
    ):
        mock_fetch.return_value = {**SAMPLE_CONTEXT_RESPONSE, "native_signals": []}
        mock_load.return_value = {"last_cursor": None, "processed_signal_ids": [wl_key]}

        summary = await poller.poll_and_evaluate()

        mock_eval_wl.assert_not_called()
        assert summary["skipped_duplicates"] == 1


@pytest.mark.asyncio
async def test_duplicate_native_signal_skipped():
    with (
        patch.object(poller, "fetch_context", new_callable=AsyncMock) as mock_fetch,
        patch("app.brain.store.load_sigbalbot_cursor") as mock_load,
        patch("app.brain.store.save_sigbalbot_cursor"),
        patch.object(poller, "_evaluate_watchlist_symbol", new_callable=AsyncMock, return_value=None),
        patch.object(poller, "_evaluate_native_signal", new_callable=AsyncMock) as mock_eval_ns,
    ):
        mock_fetch.return_value = {**SAMPLE_CONTEXT_RESPONSE, "watchlist": []}
        mock_load.return_value = {"last_cursor": None, "processed_signal_ids": ["123"]}

        summary = await poller.poll_and_evaluate()

        mock_eval_ns.assert_not_called()
        assert summary["skipped_duplicates"] == 1


# ── 4. Empty feeds ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_empty_watchlist_and_signals():
    with (
        patch.object(poller, "fetch_context", new_callable=AsyncMock) as mock_fetch,
        patch("app.brain.store.load_sigbalbot_cursor") as mock_load,
        patch("app.brain.store.save_sigbalbot_cursor"),
    ):
        mock_fetch.return_value = {
            "ok": True, "contract_version": "1.0",
            "generated_at": "2025-01-15T12:00:00Z",
            "watchlist": [], "native_signals": [],
        }
        mock_load.return_value = {"last_cursor": None, "processed_signal_ids": []}

        summary = await poller.poll_and_evaluate()

        assert summary["context_items"] == 0
        assert summary["watchlist_evaluated"] == 0
        assert summary["native_signals_evaluated"] == 0
        assert summary["signals_posted"] == 0


@pytest.mark.asyncio
async def test_fetch_failure_returns_error():
    with (
        patch.object(poller, "fetch_context", new_callable=AsyncMock) as mock_fetch,
        patch("app.brain.store.load_sigbalbot_cursor") as mock_load,
    ):
        mock_fetch.return_value = None
        mock_load.return_value = {"last_cursor": None, "processed_signal_ids": []}

        summary = await poller.poll_and_evaluate()

        assert summary["error"] == "fetch_failed"


# ── 5. Watchlist evaluation ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_watchlist_evaluation_produces_signal():
    with (
        patch("app.sentinel.technicals.calculate", new_callable=AsyncMock) as mock_ta,
        patch("app.sentinel.risk.generate_report", new_callable=AsyncMock) as mock_risk,
        patch("app.brain.sleep_trader._evaluate_entry") as mock_eval,
    ):
        mock_ta.return_value = MockTAResult(price=0.12, rsi=28)
        mock_eval.return_value = ("buy", 0.72)

        mock_risk_report = MagicMock()
        mock_risk_report.composite_score = 3.5
        mock_risk.return_value = mock_risk_report

        result = await poller._evaluate_watchlist_symbol(SAMPLE_CONTEXT_RESPONSE["watchlist"][0])

    assert result is not None
    assert result["symbol"] == "BSB/USDT"
    assert result["signal"] == "LONG"
    assert result["confidence"] == 72
    assert result["strategy"] == "sigbalbot-context-watchlist"
    assert result["metadata"]["source"] == "sigbalbot_context"
    assert result["metadata"]["watchlist_mode"] == "sniper"
    assert result["id"].startswith("sentinel-ctx-")


@pytest.mark.asyncio
async def test_watchlist_low_confidence_returns_none():
    with (
        patch("app.sentinel.technicals.calculate", new_callable=AsyncMock) as mock_ta,
        patch("app.brain.sleep_trader._evaluate_entry") as mock_eval,
    ):
        mock_ta.return_value = MockTAResult(price=0.12)
        mock_eval.return_value = ("buy", 0.40)

        result = await poller._evaluate_watchlist_symbol(SAMPLE_CONTEXT_RESPONSE["watchlist"][0])

    assert result is None


@pytest.mark.asyncio
async def test_watchlist_no_side_returns_none():
    with (
        patch("app.sentinel.technicals.calculate", new_callable=AsyncMock) as mock_ta,
        patch("app.brain.sleep_trader._evaluate_entry") as mock_eval,
    ):
        mock_ta.return_value = MockTAResult(price=0.12)
        mock_eval.return_value = (None, 0.30)

        result = await poller._evaluate_watchlist_symbol(SAMPLE_CONTEXT_RESPONSE["watchlist"][0])

    assert result is None


# ── 6. Native signal evaluation ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_native_signal_agreement_produces_signal():
    with (
        patch("app.sentinel.technicals.calculate", new_callable=AsyncMock) as mock_ta,
        patch("app.brain.sleep_trader._evaluate_entry") as mock_eval,
    ):
        mock_ta.return_value = MockTAResult(price=0.04, rsi=28)
        mock_eval.return_value = ("buy", 0.65)

        result = await poller._evaluate_native_signal(SAMPLE_CONTEXT_RESPONSE["native_signals"][0])

    assert result is not None
    assert result["symbol"] == "BANK/USDT"
    assert result["signal"] == "LONG"
    assert result["strategy"] == "sigbalbot-context-native"
    assert result["metadata"]["sigbalbot_signal_id"] == 123
    assert result["metadata"]["sigbalbot_signal"] == "AGG_LONG"
    assert result["metadata"]["sigbalbot_confidence"] == 82
    assert "SigBalBot AGG_LONG (82%)" in result["confirmations"]


@pytest.mark.asyncio
async def test_native_signal_disagreement_returns_none():
    with (
        patch("app.sentinel.technicals.calculate", new_callable=AsyncMock) as mock_ta,
        patch("app.brain.sleep_trader._evaluate_entry") as mock_eval,
    ):
        mock_ta.return_value = MockTAResult(price=0.04, rsi=78, macd_trend="bearish")
        # Sentinel says SHORT, SigBalBot says LONG → disagreement
        mock_eval.return_value = ("sell", 0.70)

        result = await poller._evaluate_native_signal(SAMPLE_CONTEXT_RESPONSE["native_signals"][0])

    assert result is None


@pytest.mark.asyncio
async def test_native_signal_non_directional_skipped():
    signal = {
        "id": 999,
        "symbol": "XYZ/USDT",
        "signal": "HOLD",
        "timeframe": "1h",
        "price": 1.0,
        "confidence": 50,
        "risk": "STANDARD",
        "reason": "Flat market",
        "created_at": "2025-01-15T12:00:00Z",
    }
    result = await poller._evaluate_native_signal(signal)
    assert result is None


# ── 7. No feedback loop ─────────────────────────────────────────────────────

def test_sentinel_signal_detected():
    assert poller._is_sentinel_signal("sentinel-BTC-USDT-LONG-1700000000") is True
    assert poller._is_sentinel_signal("sentinel-ctx-BSB-USDT-LONG-1700000000") is True


def test_non_sentinel_signal_not_detected():
    assert poller._is_sentinel_signal("123") is False
    assert poller._is_sentinel_signal("sigbalbot-signal-456") is False


@pytest.mark.asyncio
async def test_feedback_loop_signal_skipped():
    feedback_signal = {
        "id": "sentinel-BTC-USDT-LONG-1700000000",
        "symbol": "BTC/USDT",
        "signal": "LONG",
        "timeframe": "1h",
        "price": 65000,
        "confidence": 80,
        "risk": "STANDARD",
        "reason": "test",
        "created_at": "2025-01-15T12:00:00Z",
    }
    context = {
        "ok": True, "contract_version": "1.0",
        "generated_at": "2025-01-15T12:00:00Z",
        "watchlist": [],
        "native_signals": [feedback_signal],
    }

    with (
        patch.object(poller, "fetch_context", new_callable=AsyncMock) as mock_fetch,
        patch("app.brain.store.load_sigbalbot_cursor") as mock_load,
        patch("app.brain.store.save_sigbalbot_cursor"),
        patch.object(poller, "_evaluate_native_signal", new_callable=AsyncMock) as mock_eval,
    ):
        mock_fetch.return_value = context
        mock_load.return_value = {"last_cursor": None, "processed_signal_ids": []}

        summary = await poller.poll_and_evaluate()

        mock_eval.assert_not_called()
        assert summary["skipped_feedback_loop"] == 1
        assert summary["native_signals_evaluated"] == 0


# ── 8. Signal ID stability ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_watchlist_signal_id_is_stable_format():
    with (
        patch("app.sentinel.technicals.calculate", new_callable=AsyncMock) as mock_ta,
        patch("app.sentinel.risk.generate_report", new_callable=AsyncMock) as mock_risk,
        patch("app.brain.sleep_trader._evaluate_entry") as mock_eval,
    ):
        mock_ta.return_value = MockTAResult(price=0.12, rsi=28)
        mock_eval.return_value = ("buy", 0.72)
        mock_risk_report = MagicMock()
        mock_risk_report.composite_score = 3.5
        mock_risk.return_value = mock_risk_report

        result = await poller._evaluate_watchlist_symbol(SAMPLE_CONTEXT_RESPONSE["watchlist"][0])

    assert result["id"].startswith("sentinel-ctx-BSB-USDT-LONG-")


@pytest.mark.asyncio
async def test_native_signal_id_is_stable_format():
    with (
        patch("app.sentinel.technicals.calculate", new_callable=AsyncMock) as mock_ta,
        patch("app.brain.sleep_trader._evaluate_entry") as mock_eval,
    ):
        mock_ta.return_value = MockTAResult(price=0.04, rsi=28)
        mock_eval.return_value = ("buy", 0.65)

        result = await poller._evaluate_native_signal(SAMPLE_CONTEXT_RESPONSE["native_signals"][0])

    assert result["id"].startswith("sentinel-ctx-native-BANK-USDT-LONG-")


# ── 9. Poller status ────────────────────────────────────────────────────────

def test_poller_status_when_configured():
    with patch("app.brain.store.load_sigbalbot_cursor") as mock_load:
        mock_load.return_value = {
            "last_cursor": "2025-01-15T12:00:00Z",
            "processed_signal_ids": ["123", "456"],
            "last_poll_at": "2025-01-15T12:02:00Z",
            "last_summary": {"signals_posted": 1},
        }
        status = poller.get_poller_status()

    assert status["configured"] is True
    assert status["poll_interval_s"] == 120
    assert status["last_cursor"] == "2025-01-15T12:00:00Z"
    assert status["processed_ids_count"] == 2
    assert status["last_summary"]["signals_posted"] == 1


def test_is_configured():
    poller._CONTEXT_BASE_URL = None
    poller._API_KEY = None
    assert poller.is_configured() is True


def test_context_url_derived_from_webhook_url():
    poller._CONTEXT_BASE_URL = None
    poller._API_KEY = None
    url, key = poller._load_config()
    assert url == "https://sigbalbot.up.railway.app/api/v1/context/trader-sentinel"
    assert key == "test-poller-key-456"


# ── 10. Fetch sends since parameter ────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_context_sends_since_param():
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_response(200, SAMPLE_CONTEXT_RESPONSE))

    with patch("httpx.AsyncClient") as mock_cls:
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = ctx

        await poller.fetch_context(since="2025-01-15T10:00:00Z")

    call_kwargs = mock_client.get.call_args
    params = call_kwargs.kwargs.get("params", {})
    assert params["since"] == "2025-01-15T10:00:00Z"
    assert params["limit"] == 25


# ── 11. 503 retry behavior ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_context_503_retries():
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_response(503, {"error": "service unavailable"}))

    with patch("httpx.AsyncClient") as mock_cls, patch("asyncio.sleep", new_callable=AsyncMock):
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = ctx

        result = await poller.fetch_context()

    assert result is None
    assert mock_client.get.call_count == poller._MAX_RETRIES


# ── 12. Processed IDs bounded ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_processed_ids_bounded():
    large_list = [str(i) for i in range(600)]
    with (
        patch.object(poller, "fetch_context", new_callable=AsyncMock) as mock_fetch,
        patch("app.brain.store.load_sigbalbot_cursor") as mock_load,
        patch("app.brain.store.save_sigbalbot_cursor") as mock_save,
    ):
        mock_fetch.return_value = {
            "ok": True, "contract_version": "1.0",
            "generated_at": "2025-01-15T12:00:00Z",
            "watchlist": [], "native_signals": [],
        }
        mock_load.return_value = {"last_cursor": None, "processed_signal_ids": large_list}

        await poller.poll_and_evaluate()

        saved = mock_save.call_args[0][0]
        assert len(saved["processed_signal_ids"]) <= poller._MAX_PROCESSED_IDS
