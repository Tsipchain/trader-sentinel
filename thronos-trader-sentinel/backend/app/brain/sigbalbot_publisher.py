"""
SigBalBot Webhook Publisher — Sends finalized signals to the SigBalBot Telegram bot.

Integration contract: POST signal payloads to SIGBALBOT_WEBHOOK_URL after
the Sleep Mode AutoTrader finalizes and saves a trade. SigBalBot deduplicates
on ``id``, so retries with the same id are safe.
"""
import asyncio
import logging
import os
from typing import Any

import httpx

log = logging.getLogger(__name__)

_WEBHOOK_URL = None
_API_KEY = None
_TIMEOUT = 10
_MAX_RETRIES = 3


def _load_config() -> tuple[str, str]:
    global _WEBHOOK_URL, _API_KEY
    if _WEBHOOK_URL is None:
        _WEBHOOK_URL = os.getenv("SIGBALBOT_WEBHOOK_URL", "").strip()
        _API_KEY = os.getenv("SIGBALBOT_API_KEY", "").strip()
    return _WEBHOOK_URL, _API_KEY


def is_configured() -> bool:
    url, key = _load_config()
    return bool(url and key)


def build_signal_payload(trade: dict[str, Any], ta: dict[str, Any]) -> dict[str, Any]:
    """Build the SigBalBot signal payload from a finalized sleep trade + TA data."""
    side = trade.get("side", "")
    direction = "LONG" if side == "buy" else "SHORT"
    entry_price = trade.get("entry_price", 0)
    sl_price = trade.get("sl_price", 0)
    tp_price = trade.get("tp_price", 0)

    tp2 = None
    if entry_price and tp_price:
        tp_distance = abs(tp_price - entry_price)
        tp2 = round(entry_price + tp_distance * 1.618, 6) if side == "buy" else round(entry_price - tp_distance * 1.618, 6)

    strategies = trade.get("strategies", {})
    strat_parts = []
    if strategies.get("divergence"):
        d = strategies["divergence"]
        tag = d.get("type", "divergence")
        if d.get("double_confirmed"):
            tag += " (2x confirmed)"
        strat_parts.append(tag)
    if strategies.get("confluence"):
        c = strategies["confluence"]
        strat_parts.append(f"MTF {c.get('level', '')} ({c.get('agreeing_tf', 0)}TF)")
    strategy_label = " + ".join(strat_parts) if strat_parts else "sentinel-composite"

    ta_summary = trade.get("ta_summary", {})
    confirmations = []
    if ta_summary.get("rsi"):
        confirmations.append(f"RSI {ta_summary['rsi']:.0f}")
    if ta_summary.get("macd"):
        confirmations.append(f"MACD {ta_summary['macd']}")
    if ta_summary.get("ema"):
        confirmations.append(f"EMA {ta_summary['ema']}")

    symbol = trade.get("symbol", "")
    signal_id = f"sentinel-{symbol.replace('/', '-')}-{direction}-{int(trade.get('opened_at', 0))}"

    risk = "LOW"
    leverage = trade.get("leverage", 1)
    if leverage >= 20:
        risk = "EXTREME"
    elif leverage >= 10:
        risk = "HIGH"
    elif leverage >= 5:
        risk = "MEDIUM"

    confluence_info = strategies.get("confluence", {})
    confluence_score = float(confluence_info.get("confidence_bonus", 0))
    if confluence_info.get("level") == "strong":
        confluence_score = max(confluence_score, 0.15)

    return {
        "id": signal_id,
        "symbol": symbol,
        "signal": direction,
        "timeframe": "1h",
        "price": entry_price,
        "confidence": trade.get("confidence", 0),
        "risk": risk,
        "reason": strategy_label,
        "strategy": "sleep-mode-autotrader",
        "market_regime": ta.get("macd_trend", "unknown"),
        "confluence_score": round(confluence_score, 4),
        "model_version": "pytheia-sentinel-v0.2",
        "confirmations": confirmations,
        "entry": entry_price,
        "tp1": tp_price,
        "tp2": tp2,
        "sl": sl_price,
    }


async def publish_signal(payload: dict[str, Any]) -> dict[str, Any] | None:
    """POST a signal to SigBalBot with exponential-backoff retry.

    Returns the response JSON on success, None on failure.
    The same ``id`` is sent on every retry so SigBalBot deduplicates.
    """
    url, key = _load_config()
    if not url or not key:
        log.debug("sigbalbot: not configured, skipping publish")
        return None

    masked_key = key[:4] + "***" if len(key) > 4 else "***"
    signal_id = payload.get("id", "?")

    for attempt in range(_MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    url,
                    json=payload,
                    headers={"Authorization": f"Bearer {key}"},
                )
                resp.raise_for_status()
                result = resp.json()
                log.info("sigbalbot: published signal %s → HTTP %d", signal_id, resp.status_code)
                return result
        except httpx.HTTPStatusError as exc:
            log.warning(
                "sigbalbot: HTTP %d for signal %s (attempt %d/%d)",
                exc.response.status_code, signal_id, attempt + 1, _MAX_RETRIES,
            )
        except Exception as exc:
            log.warning(
                "sigbalbot: network error for signal %s (attempt %d/%d): %s",
                signal_id, attempt + 1, _MAX_RETRIES, str(exc)[:120],
            )

        if attempt < _MAX_RETRIES - 1:
            backoff = 2 ** (attempt + 1)
            await asyncio.sleep(backoff)

    log.error("sigbalbot: gave up publishing signal %s after %d attempts", signal_id, _MAX_RETRIES)
    return None


async def check_status() -> dict[str, Any]:
    """Check connectivity to SigBalBot's status endpoint."""
    url, key = _load_config()
    if not url or not key:
        return {"ok": False, "error": "not_configured"}

    base = url.rsplit("/", 2)[0] if "/signals" in url else url.rstrip("/")
    status_url = f"{base}/signals/trader-sentinel/status"

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                status_url,
                headers={"Authorization": f"Bearer {key}"},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}
