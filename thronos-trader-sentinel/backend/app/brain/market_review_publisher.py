"""
Market Review Publisher — Publishes 12-hour market reviews to SigBalBot.

Runs as a background asyncio task on the Railway server worker.
Default schedule: every 12 hours (AM/PM windows), producing two reviews/day.

Review IDs follow the pattern:
  sentinel-review-YYYYMMDD-am   (00:00–11:59 UTC)
  sentinel-review-YYYYMMDD-pm   (12:00–23:59 UTC)

Builds each review from real Sentinel context:
  - Risk composite (calendar + geo + technical)
  - Technical analysis (RSI, MACD, BB, EMA, Williams %R)
  - Market sessions (volume, liquidity, session bias)
  - Multi-asset context (BTC, ETH, SOL)

Contract: same HTTP semantics as signal publisher (v1.0).
"""
import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import httpx

log = logging.getLogger(__name__)

_MARKET_REVIEW_URL: str | None = None
_TIMEOUT = 15
_MAX_RETRIES = 3
_REVIEW_INTERVAL_S = 12 * 3600

_scheduler_task: asyncio.Task | None = None
_last_published_ids: dict[str, str] = {}

_VALID_ASSET_CLASSES: set[str] = {"crypto", "equities", "metals"}

REVIEW_SYMBOLS: dict[str, list[str]] = {
    "crypto": ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
    "equities": [],
    "metals": [],
}


def _load_review_url() -> str:
    global _MARKET_REVIEW_URL
    if _MARKET_REVIEW_URL is None:
        base = os.getenv("SIGBALBOT_WEBHOOK_URL", "").strip()
        if base:
            root = base.rsplit("/signals/", 1)[0] if "/signals/" in base else base.rstrip("/")
            _MARKET_REVIEW_URL = f"{root}/market-review/trader-sentinel"
    return _MARKET_REVIEW_URL or ""


def _get_api_key() -> str:
    return os.getenv("SIGBALBOT_API_KEY", "").strip()


def is_configured() -> bool:
    return bool(_load_review_url() and _get_api_key())


def current_review_id(now: datetime | None = None, asset_class: str = "crypto") -> str:
    if now is None:
        now = datetime.now(timezone.utc)
    window = "am" if now.hour < 12 else "pm"
    return f"sentinel-review-{asset_class}-{now.strftime('%Y%m%d')}-{window}"


def _market_regime_from_score(composite_score: float) -> str:
    if composite_score < 3.0:
        return "RISK-ON"
    elif composite_score < 5.0:
        return "NEUTRAL"
    elif composite_score < 7.0:
        return "CAUTIOUS"
    else:
        return "RISK-OFF"


def _risk_label(composite_score: float) -> str:
    if composite_score < 3.0:
        return "LOW"
    elif composite_score < 5.0:
        return "MEDIUM"
    elif composite_score < 7.0:
        return "HIGH"
    else:
        return "EXTREME"


def _confidence_1_10(composite_score: float) -> int:
    """Map composite risk score (0-10) to confidence (10-1).

    Low risk = high confidence in market stability.
    High risk = low confidence.
    """
    return max(1, min(10, 10 - int(composite_score)))


async def gather_sentinel_context(asset_class: str = "crypto") -> dict[str, Any] | None:
    """Collect real market data from Sentinel modules.

    Returns None if critical context is unavailable or no symbols are
    configured for the given asset_class.
    """
    from app.sentinel import risk as risk_module
    from app.sentinel import technicals as tech_module
    from app.sentinel import sessions as sessions_module
    from app.sentinel import calendar as cal_module
    from app.sentinel import geo as geo_module

    symbols = REVIEW_SYMBOLS.get(asset_class, [])
    if not symbols:
        log.debug("market-review: no symbols configured for asset_class=%s, skipping", asset_class)
        return None

    context: dict[str, Any] = {"asset_class": asset_class}

    try:
        risk_report = await risk_module.generate_report(symbol=symbols[0])
        context["risk"] = {
            "composite_score": risk_report.composite_score,
            "recommendation": risk_report.recommendation,
            "alerts": risk_report.alerts,
            "calendar_score": risk_report.calendar_score,
            "geo_score": risk_report.geo_score,
            "technical_score": risk_report.technical_score,
        }
    except Exception as exc:
        log.warning("market-review: risk module unavailable for %s: %s", asset_class, str(exc)[:100])
        return None

    asset_context: dict[str, Any] = {}
    for symbol in symbols:
        try:
            ta = await tech_module.calculate(symbol)
            asset_context[symbol] = {
                "price": ta.current_price,
                "rsi": ta.rsi_14,
                "rsi_signal": ta.rsi_signal,
                "macd_trend": ta.macd_trend,
                "macd_histogram": ta.macd_histogram,
                "bb_signal": ta.bb_signal,
                "bb_pct": ta.bb_pct,
                "ema_cross": ta.ema_cross,
                "williams_r": ta.williams_r,
                "volatility_score": ta.volatility_score,
                "orderbook_imbalance": ta.orderbook_imbalance,
            }
        except Exception as exc:
            log.debug("market-review: TA unavailable for %s: %s", symbol, str(exc)[:80])
    context["assets"] = asset_context

    primary = symbols[0]
    if not asset_context.get(primary):
        log.warning("market-review: %s TA unavailable for %s, skipping review cycle", primary, asset_class)
        return None

    try:
        sessions_report = sessions_module.calculate()
        context["sessions"] = {
            "active": [s["name"] for s in sessions_report.active_sessions if s.get("is_open")],
            "volume_expectation": sessions_report.volume_expectation,
            "crypto_impact": sessions_report.crypto_impact_score,
            "overlap": bool(sessions_report.session_overlap),
        }
    except Exception as exc:
        log.debug("market-review: sessions module unavailable: %s", str(exc)[:80])
        context["sessions"] = {}

    try:
        bias = sessions_module.get_session_bias()
        context["bias"] = {
            "volume": bias.get("volume", "unknown"),
            "should_trade": bias.get("should_trade", True),
        }
    except Exception:
        context["bias"] = {}

    return context


def build_review_payload(context: dict[str, Any], review_id: str, asset_class: str = "crypto") -> dict[str, Any]:
    """Build the market review payload from gathered Sentinel context."""
    risk_data = context.get("risk", {})
    composite = risk_data.get("composite_score", 5.0)
    recommendation = risk_data.get("recommendation", {})
    alerts = risk_data.get("alerts", [])
    assets = context.get("assets", {})
    sessions = context.get("sessions", {})
    bias = context.get("bias", {})

    summary_parts = []

    btc = assets.get("BTC/USDT", {})
    eth = assets.get("ETH/USDT", {})
    sol = assets.get("SOL/USDT", {})

    if btc.get("price"):
        summary_parts.append(
            f"BTC ${btc['price']:,.0f} (RSI {btc.get('rsi', 0):.0f}, "
            f"MACD {btc.get('macd_trend', '?')}, BB {btc.get('bb_signal', '?')})"
        )
    if eth.get("price"):
        summary_parts.append(
            f"ETH ${eth['price']:,.0f} (RSI {eth.get('rsi', 0):.0f}, {eth.get('macd_trend', '?')})"
        )
    if sol.get("price"):
        summary_parts.append(
            f"SOL ${sol['price']:,.2f} (RSI {sol.get('rsi', 0):.0f}, {sol.get('macd_trend', '?')})"
        )

    rec_level = recommendation.get("level", "NEUTRAL")
    rec_desc = recommendation.get("description", "")
    summary_parts.append(f"Risk: {rec_level} — {rec_desc}")

    vol = bias.get("volume", sessions.get("volume_expectation", "unknown"))
    active = sessions.get("active", [])
    if active:
        summary_parts.append(f"Sessions: {', '.join(active[:3])}. Volume: {vol}.")
    elif vol != "unknown":
        summary_parts.append(f"Volume expectation: {vol}.")

    if alerts:
        summary_parts.append(f"Alerts: {'; '.join(alerts[:3])}")

    summary = " | ".join(summary_parts) if summary_parts else "Sentinel context gathered but no notable observations."

    outlook_parts = []
    if btc:
        rsi = btc.get("rsi", 50)
        if rsi < 30:
            outlook_parts.append("BTC RSI oversold — watching for reversal")
        elif rsi > 70:
            outlook_parts.append("BTC RSI overbought — watching for pullback")

        ema = btc.get("ema_cross", "")
        if ema == "golden_cross":
            outlook_parts.append("BTC EMA golden cross active — bullish structure")
        elif ema == "death_cross":
            outlook_parts.append("BTC EMA death cross active — bearish structure")

        bb = btc.get("bb_signal", "")
        if "squeeze" in (bb or "").lower():
            outlook_parts.append("Bollinger squeeze detected — volatility expansion expected")

    if composite >= 7.0:
        outlook_parts.append("High composite risk — defensive positioning recommended")
    elif composite >= 5.0:
        outlook_parts.append("Elevated risk — monitoring for escalation")
    elif composite < 3.0:
        outlook_parts.append("Low risk environment — normal trading conditions")

    overlap = sessions.get("overlap")
    if overlap:
        outlook_parts.append("Session overlap active — expect elevated liquidity")

    outlook = ". ".join(outlook_parts) + "." if outlook_parts else "No notable signals for the next window."

    return {
        "id": review_id,
        "asset_class": asset_class,
        "market_regime": _market_regime_from_score(composite),
        "risk": _risk_label(composite),
        "confidence": _confidence_1_10(composite),
        "summary": summary,
        "outlook": outlook,
    }


async def publish_review(payload: dict[str, Any]) -> dict[str, Any] | None:
    """POST a market review to SigBalBot with contract-aware retry."""
    url = _load_review_url()
    key = _get_api_key()
    if not url or not key:
        log.debug("market-review: not configured, skipping publish")
        return None

    review_id = payload.get("id", "?")

    for attempt in range(_MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    url,
                    json=payload,
                    headers={"Authorization": f"Bearer {key}"},
                )

            status = resp.status_code
            body = _safe_response_json(resp)

            if status == 202:
                log.info(
                    "market-review: %s delivered → HTTP 202 event_id=%s telegram_sent=%s",
                    review_id, body.get("event_id", "?"), body.get("telegram_sent"),
                )
                return body

            if status == 200 and body.get("duplicate"):
                log.info("market-review: %s duplicate → HTTP 200 event_id=%s", review_id, body.get("event_id", "?"))
                return body

            if status == 200:
                log.info("market-review: %s → HTTP 200 body=%s", review_id, body)
                return body

            if status in (401, 403):
                log.error(
                    "market-review: PERMANENT AUTH ERROR for %s → HTTP %d. "
                    "Check SIGBALBOT_WEBHOOK_URL and SIGBALBOT_API_KEY.",
                    review_id, status,
                )
                return None

            if status == 400:
                log.error("market-review: PAYLOAD CONTRACT ERROR for %s → HTTP 400 body=%s", review_id, body)
                return None

            if status == 503 and body.get("retryable"):
                log.warning(
                    "market-review: retryable 503 for %s (attempt %d/%d)",
                    review_id, attempt + 1, _MAX_RETRIES,
                )
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** (attempt + 1))
                continue

            log.warning("market-review: HTTP %d for %s (attempt %d/%d) body=%s", status, review_id, attempt + 1, _MAX_RETRIES, body)
            if status >= 500 and attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(2 ** (attempt + 1))
                continue
            return None

        except Exception as exc:
            log.warning(
                "market-review: network error for %s (attempt %d/%d): %s",
                review_id, attempt + 1, _MAX_RETRIES, str(exc)[:120],
            )
            if attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(2 ** (attempt + 1))

    log.error("market-review: gave up publishing %s after %d attempts", review_id, _MAX_RETRIES)
    return None


def _safe_response_json(resp: httpx.Response) -> dict[str, Any]:
    try:
        return resp.json()
    except Exception:
        return {"_raw": resp.text[:500]}


async def _review_loop():
    """Background loop: publish a market review every 12 hours per asset class."""
    log.info("market-review: scheduler started (interval=%ds)", _REVIEW_INTERVAL_S)

    await asyncio.sleep(30)

    while True:
        for ac in _VALID_ASSET_CLASSES:
            review_id = current_review_id(asset_class=ac)

            if _last_published_ids.get(ac) == review_id:
                log.debug("market-review: %s already published this window, skipping", review_id)
                continue

            try:
                log.info("market-review: gathering context for %s", review_id)
                context = await gather_sentinel_context(asset_class=ac)
                if context is None:
                    continue

                payload = build_review_payload(context, review_id, asset_class=ac)
                result = await publish_review(payload)

                if result is not None:
                    _last_published_ids[ac] = review_id
                    log.info("market-review: %s completed successfully", review_id)
                else:
                    log.warning("market-review: %s publish failed, will retry next cycle", review_id)

            except Exception as exc:
                log.error("market-review: unexpected error for %s: %s", review_id, str(exc)[:200])

        await asyncio.sleep(_REVIEW_INTERVAL_S)


def start_scheduler() -> asyncio.Task | None:
    """Start the market review background scheduler.

    Guard: only one instance runs per process. Safe to call multiple times.
    """
    global _scheduler_task
    if not is_configured():
        log.info("market-review: not configured, scheduler not started")
        return None
    if _scheduler_task is not None and not _scheduler_task.done():
        log.debug("market-review: scheduler already running")
        return _scheduler_task
    _scheduler_task = asyncio.create_task(_review_loop())
    return _scheduler_task


def stop_scheduler():
    """Stop the background scheduler if running."""
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        _scheduler_task = None


async def run_single_review(asset_class: str = "crypto") -> dict[str, Any] | None:
    """Run one review cycle on demand (for testing / manual trigger)."""
    review_id = current_review_id(asset_class=asset_class)
    context = await gather_sentinel_context(asset_class=asset_class)
    if context is None:
        return None
    payload = build_review_payload(context, review_id, asset_class=asset_class)
    return await publish_review(payload)
