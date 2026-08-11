"""
End-to-end smoke tests for the SigBalBot webhook publisher.

Covers:
  - Payload structure and required fields
  - Symbol normalization (BTC/USDT:USDT → BTC/USDT)
  - Signal value validation (LONG, SHORT only from sleep_trader)
  - Contract-aware response handling (202, 200+dup, 503, 401, 400)
  - Same id across retries
  - No API key leakage in logs
"""
import asyncio
import json
import logging
import os
import time
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import httpx

# Ensure env is set before import so _load_config picks it up
os.environ["SIGBALBOT_WEBHOOK_URL"] = "https://sigbalbot.up.railway.app/api/v1/signals/trader-sentinel"
os.environ["SIGBALBOT_API_KEY"] = "test-key-abc123"

# Reset cached config so tests pick up the env vars above
import app.brain.sigbalbot_publisher as pub
pub._WEBHOOK_URL = None
pub._API_KEY = None


# ── Fixtures ─────────────────────────────────────────────────────────────────

SAMPLE_TRADE = {
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
        "divergence": {"type": "regular_bullish", "double_confirmed": True, "description": "RSI+MACD diverge"},
        "confluence": {"level": "strong", "agreeing_tf": 3, "confidence_bonus": 0.15, "direction": "long"},
    },
    "opened_at": time.time(),
    "status": "open",
    "order_id": "ord-123",
    "market_type": "futures",
}

SAMPLE_TA = {
    "rsi": 32,
    "macd_trend": "bullish",
    "ema_cross": "golden_cross",
    "price": 65432.10,
}


# ── Point 8: Payload structure ───────────────────────────────────────────────

REQUIRED_FIELDS = [
    "id", "symbol", "signal", "timeframe", "price", "confidence", "risk",
    "reason", "strategy", "market_regime", "confluence_score", "confirmations",
    "entry", "tp1", "tp2", "sl", "model_version",
]


def test_payload_has_all_required_fields():
    payload = pub.build_signal_payload(SAMPLE_TRADE, SAMPLE_TA)
    for field in REQUIRED_FIELDS:
        assert field in payload, f"missing required field: {field}"


def test_payload_values():
    payload = pub.build_signal_payload(SAMPLE_TRADE, SAMPLE_TA)
    assert payload["symbol"] == "BTC/USDT"
    assert payload["signal"] == "LONG"
    assert payload["timeframe"] == "1h"
    assert payload["price"] == 65432.10
    assert payload["entry"] == 65432.10
    assert payload["tp1"] == 66088.42
    assert payload["sl"] == 64777.88
    assert payload["tp2"] is not None
    assert payload["confidence"] == 0.72
    assert payload["risk"] == "HIGH"  # leverage=10
    assert payload["market_regime"] == "bullish"
    assert payload["model_version"] == "pytheia-sentinel-v0.2"
    assert payload["confluence_score"] >= 0.15
    assert "RSI 32" in payload["confirmations"]
    assert "MACD bullish" in payload["confirmations"]
    assert "EMA golden_cross" in payload["confirmations"]


# ── Point 7: Signal values ───────────────────────────────────────────────────

def test_signal_long():
    trade = {**SAMPLE_TRADE, "side": "buy"}
    payload = pub.build_signal_payload(trade, SAMPLE_TA)
    assert payload["signal"] == "LONG"
    assert payload["signal"] in pub._VALID_SIGNALS


def test_signal_short():
    trade = {**SAMPLE_TRADE, "side": "sell"}
    payload = pub.build_signal_payload(trade, SAMPLE_TA)
    assert payload["signal"] == "SHORT"
    assert payload["signal"] in pub._VALID_SIGNALS


# ── Point 8: Symbol normalization ────────────────────────────────────────────

def test_symbol_normalized_slash():
    payload = pub.build_signal_payload({**SAMPLE_TRADE, "symbol": "BTC/USDT"}, SAMPLE_TA)
    assert payload["symbol"] == "BTC/USDT"


def test_symbol_normalized_futures_suffix():
    payload = pub.build_signal_payload({**SAMPLE_TRADE, "symbol": "BTC/USDT:USDT"}, SAMPLE_TA)
    assert payload["symbol"] == "BTC/USDT"


def test_symbol_normalized_no_slash():
    payload = pub.build_signal_payload({**SAMPLE_TRADE, "symbol": "ETHUSDT"}, SAMPLE_TA)
    assert payload["symbol"] == "ETH/USDT"


# ── Point 6: Same id across retries ─────────────────────────────────────────

def test_id_stable():
    trade = {**SAMPLE_TRADE, "opened_at": 1700000000}
    payload = pub.build_signal_payload(trade, SAMPLE_TA)
    assert payload["id"] == "sentinel-BTC-USDT-LONG-1700000000"
    payload2 = pub.build_signal_payload(trade, SAMPLE_TA)
    assert payload["id"] == payload2["id"]


# ── Point 4/5: Contract response handling ────────────────────────────────────

def _mock_response(status_code: int, body: dict) -> httpx.Response:
    resp = httpx.Response(
        status_code=status_code,
        json=body,
        request=httpx.Request("POST", "https://example.com"),
    )
    return resp


@pytest.mark.asyncio
async def test_publish_202_delivered():
    body = {
        "ok": True, "duplicate": False, "telegram_sent": True,
        "event_id": "evt-1", "outcomes_scheduled": 4, "contract_version": "1.0",
    }
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mock_response(202, body))

    with patch("httpx.AsyncClient") as mock_cls:
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = ctx

        result = await pub.publish_signal(pub.build_signal_payload(SAMPLE_TRADE, SAMPLE_TA))

    assert result is not None
    assert result["ok"] is True
    assert result["telegram_sent"] is True
    assert result["event_id"] == "evt-1"
    mock_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_publish_200_duplicate():
    body = {"ok": True, "duplicate": True, "event_id": "evt-1", "contract_version": "1.0"}
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mock_response(200, body))

    with patch("httpx.AsyncClient") as mock_cls:
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = ctx

        result = await pub.publish_signal(pub.build_signal_payload(SAMPLE_TRADE, SAMPLE_TA))

    assert result is not None
    assert result["duplicate"] is True
    mock_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_publish_503_retries_then_fails():
    body = {"ok": False, "retryable": True, "event_id": "evt-x", "contract_version": "1.0"}
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mock_response(503, body))

    with patch("httpx.AsyncClient") as mock_cls, patch("asyncio.sleep", new_callable=AsyncMock):
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = ctx

        result = await pub.publish_signal(pub.build_signal_payload(SAMPLE_TRADE, SAMPLE_TA))

    assert result is None
    assert mock_client.post.call_count == pub._MAX_RETRIES


@pytest.mark.asyncio
async def test_publish_401_permanent_no_retry():
    body = {"error": "unauthorized"}
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mock_response(401, body))

    with patch("httpx.AsyncClient") as mock_cls:
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = ctx

        result = await pub.publish_signal(pub.build_signal_payload(SAMPLE_TRADE, SAMPLE_TA))

    assert result is None
    mock_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_publish_403_permanent_no_retry():
    body = {"error": "forbidden"}
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mock_response(403, body))

    with patch("httpx.AsyncClient") as mock_cls:
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = ctx

        result = await pub.publish_signal(pub.build_signal_payload(SAMPLE_TRADE, SAMPLE_TA))

    assert result is None
    mock_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_publish_400_contract_error_no_retry():
    body = {"error": "missing field: symbol", "contract_version": "1.0"}
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mock_response(400, body))

    with patch("httpx.AsyncClient") as mock_cls:
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = ctx

        result = await pub.publish_signal(pub.build_signal_payload(SAMPLE_TRADE, SAMPLE_TA))

    assert result is None
    mock_client.post.assert_called_once()


# ── Point 9: No API key in logs ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_api_key_in_logs(caplog):
    body = {"ok": True, "duplicate": False, "telegram_sent": True, "event_id": "e1", "outcomes_scheduled": 4, "contract_version": "1.0"}
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mock_response(202, body))

    with patch("httpx.AsyncClient") as mock_cls:
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = ctx

        with caplog.at_level(logging.DEBUG):
            await pub.publish_signal(pub.build_signal_payload(SAMPLE_TRADE, SAMPLE_TA))

    api_key = os.environ["SIGBALBOT_API_KEY"]
    for record in caplog.records:
        assert api_key not in record.getMessage(), f"API key leaked in log: {record.getMessage()}"


# ── Point 9: Auth header sent correctly ──────────────────────────────────────

@pytest.mark.asyncio
async def test_auth_header_bearer_not_query_param():
    body = {"ok": True, "duplicate": False, "telegram_sent": True, "event_id": "e1", "outcomes_scheduled": 4, "contract_version": "1.0"}
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mock_response(202, body))

    with patch("httpx.AsyncClient") as mock_cls:
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = ctx

        await pub.publish_signal(pub.build_signal_payload(SAMPLE_TRADE, SAMPLE_TA))

    call_kwargs = mock_client.post.call_args
    headers = call_kwargs.kwargs.get("headers", {})
    assert "Authorization" in headers
    assert headers["Authorization"].startswith("Bearer ")
    url_called = str(call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("url", ""))
    assert "api_key" not in url_called.lower()
    assert "SIGBALBOT_API_KEY" not in url_called


# ── Point 3: check_status makes real authenticated request ───────────────────

@pytest.mark.asyncio
async def test_check_status_url_and_auth():
    status_body = {"ok": True, "receiver": "sigbalbot", "configured": True, "last_event": None}
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_response(200, status_body))

    with patch("httpx.AsyncClient") as mock_cls:
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = ctx

        result = await pub.check_status()

    assert result["ok"] is True
    call_args = mock_client.get.call_args
    called_url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
    assert called_url == "https://sigbalbot.up.railway.app/api/v1/signals/trader-sentinel/status"
    headers = call_args.kwargs.get("headers", {})
    assert headers["Authorization"] == "Bearer test-key-abc123"


# ── Risk mapping ─────────────────────────────────────────────────────────────

def test_risk_levels():
    for lev, expected in [(1, "LOW"), (3, "LOW"), (5, "MEDIUM"), (10, "HIGH"), (20, "EXTREME"), (50, "EXTREME")]:
        trade = {**SAMPLE_TRADE, "leverage": lev}
        payload = pub.build_signal_payload(trade, SAMPLE_TA)
        assert payload["risk"] == expected, f"leverage={lev} → expected {expected}, got {payload['risk']}"
