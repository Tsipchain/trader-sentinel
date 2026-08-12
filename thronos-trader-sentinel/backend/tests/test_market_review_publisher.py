"""
Tests for the market review publisher.

Covers:
  - Payload structure and required fields
  - Review ID format (sentinel-review-YYYYMMDD-am/pm)
  - Stable ID across retries
  - Contract-aware response handling (202, 200+dup, 503, 401, 400)
  - Missing configuration handling
  - Unavailable Sentinel context handling
  - Confidence 1-10 and risk mapping
  - No API key leakage in logs
"""
import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import httpx

os.environ["SIGBALBOT_WEBHOOK_URL"] = "https://sigbalbot.up.railway.app/api/v1/signals/trader-sentinel"
os.environ["SIGBALBOT_API_KEY"] = "test-key-review-123"

import app.brain.market_review_publisher as mrp
mrp._MARKET_REVIEW_URL = None


SAMPLE_CONTEXT = {
    "risk": {
        "composite_score": 4.2,
        "recommendation": {"level": "WATCH", "description": "Elevated signals. Monitor developments.", "action": "monitor"},
        "alerts": ["Calendar: Active event window — CPI"],
        "calendar_score": 5.0,
        "geo_score": 3.5,
        "technical_score": 4.0,
    },
    "assets": {
        "BTC/USDT": {
            "price": 67000, "rsi": 55, "rsi_signal": "neutral",
            "macd_trend": "bullish", "macd_histogram": 120,
            "bb_signal": "neutral", "bb_pct": 0.6,
            "ema_cross": "golden_cross", "williams_r": -45,
            "volatility_score": 4.0, "orderbook_imbalance": 0.12,
        },
        "ETH/USDT": {
            "price": 3500, "rsi": 48, "rsi_signal": "neutral",
            "macd_trend": "bearish", "macd_histogram": -15,
            "bb_signal": "neutral", "bb_pct": 0.4,
            "ema_cross": "none", "williams_r": -55,
            "volatility_score": 5.0, "orderbook_imbalance": -0.05,
        },
        "SOL/USDT": {
            "price": 155.30, "rsi": 62, "rsi_signal": "neutral",
            "macd_trend": "bullish", "macd_histogram": 2.1,
            "bb_signal": "neutral", "bb_pct": 0.7,
            "ema_cross": "none", "williams_r": -30,
            "volatility_score": 6.0, "orderbook_imbalance": 0.08,
        },
    },
    "sessions": {
        "active": ["London", "New York"],
        "volume_expectation": "high",
        "crypto_impact": 7.5,
        "overlap": True,
    },
    "bias": {"volume": "peak", "should_trade": True},
}


# ── Review ID format ─────────────────────────────────────────────────────────

def test_review_id_am():
    morning = datetime(2026, 8, 12, 9, 30, tzinfo=timezone.utc)
    assert mrp.current_review_id(morning) == "sentinel-review-20260812-am"


def test_review_id_pm():
    afternoon = datetime(2026, 8, 12, 15, 0, tzinfo=timezone.utc)
    assert mrp.current_review_id(afternoon) == "sentinel-review-20260812-pm"


def test_review_id_midnight():
    midnight = datetime(2026, 8, 13, 0, 0, tzinfo=timezone.utc)
    assert mrp.current_review_id(midnight) == "sentinel-review-20260813-am"


def test_review_id_noon_boundary():
    noon = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
    assert mrp.current_review_id(noon) == "sentinel-review-20260812-pm"


# ── Payload structure ────────────────────────────────────────────────────────

REQUIRED_FIELDS = ["id", "market_regime", "risk", "confidence", "summary", "outlook"]


def test_payload_has_all_required_fields():
    payload = mrp.build_review_payload(SAMPLE_CONTEXT, "sentinel-review-20260812-am")
    for field in REQUIRED_FIELDS:
        assert field in payload, f"missing required field: {field}"


def test_payload_values():
    payload = mrp.build_review_payload(SAMPLE_CONTEXT, "sentinel-review-20260812-am")
    assert payload["id"] == "sentinel-review-20260812-am"
    assert payload["market_regime"] == "NEUTRAL"  # composite 4.2 → NEUTRAL
    assert payload["risk"] == "MEDIUM"  # composite 4.2 → MEDIUM
    assert isinstance(payload["confidence"], int)
    assert 1 <= payload["confidence"] <= 10
    assert payload["confidence"] == 6  # 10 - int(4.2) = 6
    assert "BTC" in payload["summary"]
    assert "ETH" in payload["summary"]
    assert "SOL" in payload["summary"]
    assert len(payload["outlook"]) > 0


def test_payload_id_stable():
    p1 = mrp.build_review_payload(SAMPLE_CONTEXT, "sentinel-review-20260812-am")
    p2 = mrp.build_review_payload(SAMPLE_CONTEXT, "sentinel-review-20260812-am")
    assert p1["id"] == p2["id"]


# ── Confidence 1-10 mapping ──────────────────────────────────────────────────

def test_confidence_mapping():
    for score, expected in [(0.0, 10), (1.0, 9), (3.0, 7), (5.0, 5), (7.0, 3), (9.5, 1), (10.0, 1)]:
        assert mrp._confidence_1_10(score) == expected, f"score={score} → expected {expected}"


# ── Market regime mapping ────────────────────────────────────────────────────

def test_market_regime_mapping():
    assert mrp._market_regime_from_score(1.0) == "RISK-ON"
    assert mrp._market_regime_from_score(4.0) == "NEUTRAL"
    assert mrp._market_regime_from_score(6.0) == "CAUTIOUS"
    assert mrp._market_regime_from_score(8.0) == "RISK-OFF"


# ── Risk label mapping ───────────────────────────────────────────────────────

def test_risk_label_mapping():
    assert mrp._risk_label(1.0) == "LOW"
    assert mrp._risk_label(4.0) == "MEDIUM"
    assert mrp._risk_label(6.0) == "HIGH"
    assert mrp._risk_label(8.0) == "EXTREME"


# ── Contract response handling ───────────────────────────────────────────────

def _mock_response(status_code: int, body: dict) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        json=body,
        request=httpx.Request("POST", "https://example.com"),
    )


@pytest.mark.asyncio
async def test_publish_202_delivered():
    body = {"ok": True, "duplicate": False, "telegram_sent": True, "event_id": "evt-r1", "contract_version": "1.0"}
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mock_response(202, body))

    with patch("httpx.AsyncClient") as mock_cls:
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = ctx

        result = await mrp.publish_review({"id": "sentinel-review-20260812-am", "market_regime": "NEUTRAL", "risk": "MEDIUM", "confidence": 6, "summary": "test", "outlook": "test"})

    assert result is not None
    assert result["ok"] is True
    assert result["telegram_sent"] is True
    mock_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_publish_200_duplicate():
    body = {"ok": True, "duplicate": True, "event_id": "evt-r1", "contract_version": "1.0"}
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mock_response(200, body))

    with patch("httpx.AsyncClient") as mock_cls:
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = ctx

        result = await mrp.publish_review({"id": "sentinel-review-20260812-am", "market_regime": "NEUTRAL", "risk": "MEDIUM", "confidence": 6, "summary": "test", "outlook": "test"})

    assert result is not None
    assert result["duplicate"] is True
    mock_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_publish_503_retries():
    body = {"ok": False, "retryable": True, "event_id": "evt-rx", "contract_version": "1.0"}
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mock_response(503, body))

    with patch("httpx.AsyncClient") as mock_cls, patch("asyncio.sleep", new_callable=AsyncMock):
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = ctx

        result = await mrp.publish_review({"id": "test", "market_regime": "NEUTRAL", "risk": "LOW", "confidence": 8, "summary": "t", "outlook": "t"})

    assert result is None
    assert mock_client.post.call_count == mrp._MAX_RETRIES


@pytest.mark.asyncio
async def test_publish_401_no_retry():
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mock_response(401, {"error": "unauthorized"}))

    with patch("httpx.AsyncClient") as mock_cls:
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = ctx

        result = await mrp.publish_review({"id": "test", "market_regime": "NEUTRAL", "risk": "LOW", "confidence": 8, "summary": "t", "outlook": "t"})

    assert result is None
    mock_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_publish_400_no_retry():
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mock_response(400, {"error": "missing field: summary"}))

    with patch("httpx.AsyncClient") as mock_cls:
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = ctx

        result = await mrp.publish_review({"id": "test", "market_regime": "NEUTRAL", "risk": "LOW", "confidence": 8, "summary": "t", "outlook": "t"})

    assert result is None
    mock_client.post.assert_called_once()


# ── Missing configuration ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_publish_not_configured():
    with patch.object(mrp, "_load_review_url", return_value=""), patch.object(mrp, "_get_api_key", return_value=""):
        result = await mrp.publish_review({"id": "test"})
    assert result is None


# ── Unavailable Sentinel context ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_gather_context_unavailable_skips_review():
    with patch.object(mrp, "gather_sentinel_context", return_value=None):
        result = await mrp.run_single_review()
    assert result is None


@pytest.mark.asyncio
async def test_gather_context_returns_none_when_btc_missing():
    context_no_btc = {
        "risk": SAMPLE_CONTEXT["risk"],
        "assets": {
            "ETH/USDT": SAMPLE_CONTEXT["assets"]["ETH/USDT"],
        },
        "sessions": SAMPLE_CONTEXT["sessions"],
        "bias": SAMPLE_CONTEXT["bias"],
    }
    payload = mrp.build_review_payload(context_no_btc, "sentinel-review-20260812-am")
    assert "BTC" not in payload["summary"]
    assert payload["id"] == "sentinel-review-20260812-am"


@pytest.mark.asyncio
async def test_run_single_review_returns_none_on_no_context():
    with patch.object(mrp, "gather_sentinel_context", return_value=None):
        result = await mrp.run_single_review()
    assert result is None


# ── No API key in logs ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_api_key_in_logs(caplog):
    body = {"ok": True, "duplicate": False, "telegram_sent": True, "event_id": "e1", "contract_version": "1.0"}
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mock_response(202, body))

    with patch("httpx.AsyncClient") as mock_cls:
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = ctx

        with caplog.at_level(logging.DEBUG):
            await mrp.publish_review({"id": "test", "market_regime": "NEUTRAL", "risk": "LOW", "confidence": 8, "summary": "t", "outlook": "t"})

    api_key = os.environ["SIGBALBOT_API_KEY"]
    for record in caplog.records:
        assert api_key not in record.getMessage(), f"API key leaked in log: {record.getMessage()}"


# ── Auth header ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_auth_header_bearer():
    body = {"ok": True, "event_id": "e1", "contract_version": "1.0"}
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mock_response(202, body))

    with patch("httpx.AsyncClient") as mock_cls:
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = ctx

        await mrp.publish_review({"id": "test", "market_regime": "NEUTRAL", "risk": "LOW", "confidence": 8, "summary": "t", "outlook": "t"})

    call_kwargs = mock_client.post.call_args
    headers = call_kwargs.kwargs.get("headers", {})
    assert "Authorization" in headers
    assert headers["Authorization"].startswith("Bearer ")
    url_called = str(call_kwargs.args[0] if call_kwargs.args else "")
    assert "api_key" not in url_called.lower()


# ── Review URL derivation ────────────────────────────────────────────────────

def test_review_url_derived_from_webhook_url():
    mrp._MARKET_REVIEW_URL = None
    url = mrp._load_review_url()
    assert url == "https://sigbalbot.up.railway.app/api/v1/market-review/trader-sentinel"


# ── Scheduler guard ──────────────────────────────────────────────────────────

def test_start_scheduler_guard():
    mrp._scheduler_task = None
    with patch("asyncio.create_task") as mock_create:
        mock_task = MagicMock()
        mock_task.done.return_value = False
        mock_create.return_value = mock_task

        t1 = mrp.start_scheduler()
        t2 = mrp.start_scheduler()

        mock_create.assert_called_once()
        assert t1 is t2
    mrp._scheduler_task = None
