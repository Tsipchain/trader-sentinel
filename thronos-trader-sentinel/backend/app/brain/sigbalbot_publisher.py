"""
SigBalBot Webhook Publisher — Sends finalized signals to the SigBalBot Telegram bot.

Integration contract v1.0:
  - 202 = delivered, telegram_sent
  - 200 + duplicate=true = already delivered (dedup on id)
  - 503 + retryable=true = temporary Telegram failure, retry with backoff
  - 401/403 = permanent auth error, do NOT retry
  - 400 = payload/contract error, do NOT retry, log response body

The same ``id`` is sent on every retry so SigBalBot deduplicates.
"""
import asyncio
import logging
import os
from typing import Any

import httpx

log = logging.getLogger(__name__)

_WEBHOOK_URL: str | None = None
_API_KEY: str | None = None
_TIMEOUT = 10
_MAX_RETRIES = 3

_VALID_SIGNALS = {"LONG", "SHORT", "BUY", "SELL", "HOLD", "ACCUMULATION", "DISTRIBUTION"}


def _load_config() -> tuple[str, str]:
    global _WEBHOOK_URL, _API_KEY
    if _WEBHOOK_URL is None:
        _WEBHOOK_URL = os.getenv("SIGBALBOT_WEBHOOK_URL", "").strip()
        _API_KEY = os.getenv("SIGBALBOT_API_KEY", "").strip()
    return _WEBHOOK_URL, _API_KEY


def is_configured() -> bool:
    url, key = _load_config()
    return bool(url and key)


def _normalize_symbol(symbol: str) -> str:
    s = (symbol or "").strip()
    s = s.replace(":USDT", "")
    if "/" not in s and len(s) > 3:
        for quote in ("USDT", "BUSD", "USDC"):
            if s.endswith(quote):
                s = s[: -len(quote)] + "/" + quote
                break
    return s


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
        tp2 = round(
            entry_price + tp_distance * 1.618 if side == "buy" else entry_price - tp_distance * 1.618,
            6,
        )

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
    if ta_summary.get("rsi") is not None:
        confirmations.append(f"RSI {ta_summary['rsi']:.0f}")
    if ta_summary.get("macd"):
        confirmations.append(f"MACD {ta_summary['macd']}")
    if ta_summary.get("ema"):
        confirmations.append(f"EMA {ta_summary['ema']}")

    symbol = _normalize_symbol(trade.get("symbol", ""))
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

    assert direction in _VALID_SIGNALS, f"signal '{direction}' not in {_VALID_SIGNALS}"

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
    """POST a signal to SigBalBot with contract-aware retry.

    Returns the response JSON on success, None on failure.
    The same ``id`` is sent on every retry so SigBalBot deduplicates.
    """
    url, key = _load_config()
    if not url or not key:
        log.debug("sigbalbot: not configured, skipping publish")
        return None

    signal_id = payload.get("id", "?")

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

            # 202 — delivered successfully
            if status == 202:
                log.info(
                    "sigbalbot: signal %s delivered → HTTP 202 event_id=%s telegram_sent=%s outcomes=%s",
                    signal_id,
                    body.get("event_id", "?"),
                    body.get("telegram_sent"),
                    body.get("outcomes_scheduled"),
                )
                return body

            # 200 — duplicate (already delivered, dedup on id)
            if status == 200 and body.get("duplicate"):
                log.info(
                    "sigbalbot: signal %s duplicate → HTTP 200 event_id=%s",
                    signal_id,
                    body.get("event_id", "?"),
                )
                return body

            # 200 — other success
            if status == 200:
                log.info("sigbalbot: signal %s → HTTP 200 body=%s", signal_id, body)
                return body

            # 401/403 — permanent auth/config error, do NOT retry
            if status in (401, 403):
                log.error(
                    "sigbalbot: PERMANENT AUTH ERROR for signal %s → HTTP %d. "
                    "Check SIGBALBOT_WEBHOOK_URL and SIGBALBOT_API_KEY configuration.",
                    signal_id,
                    status,
                )
                return None

            # 400 — payload/contract error, do NOT retry, log body
            if status == 400:
                log.error(
                    "sigbalbot: PAYLOAD CONTRACT ERROR for signal %s → HTTP 400 body=%s",
                    signal_id,
                    body,
                )
                return None

            # 503 — retryable (temporary Telegram failure)
            if status == 503 and body.get("retryable"):
                log.warning(
                    "sigbalbot: retryable 503 for signal %s (attempt %d/%d) event_id=%s",
                    signal_id,
                    attempt + 1,
                    _MAX_RETRIES,
                    body.get("event_id", "?"),
                )
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** (attempt + 1))
                continue

            # Any other HTTP error
            log.warning(
                "sigbalbot: HTTP %d for signal %s (attempt %d/%d) body=%s",
                status,
                signal_id,
                attempt + 1,
                _MAX_RETRIES,
                body,
            )
            if status >= 500 and attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(2 ** (attempt + 1))
                continue
            return None

        except Exception as exc:
            log.warning(
                "sigbalbot: network error for signal %s (attempt %d/%d): %s",
                signal_id,
                attempt + 1,
                _MAX_RETRIES,
                str(exc)[:120],
            )
            if attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(2 ** (attempt + 1))

    log.error(
        "sigbalbot: gave up publishing signal %s after %d attempts",
        signal_id,
        _MAX_RETRIES,
    )
    return None


def _safe_response_json(resp: httpx.Response) -> dict[str, Any]:
    try:
        return resp.json()
    except Exception:
        return {"_raw": resp.text[:500]}


async def check_status() -> dict[str, Any]:
    """Check connectivity to SigBalBot's status endpoint.

    Makes a real authenticated GET to:
      {SIGBALBOT_WEBHOOK_URL}/status
    e.g. https://sigbalbot.up.railway.app/api/v1/signals/trader-sentinel/status
    """
    url, key = _load_config()
    if not url or not key:
        return {"ok": False, "error": "not_configured"}

    status_url = url.rstrip("/") + "/status"

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
