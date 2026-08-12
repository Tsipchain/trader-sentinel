"""
SigBalBot Context Poller — Polls the bidirectional context endpoint for
watchlist symbols and native signals, evaluates them through Sentinel
technical/risk logic, and posts actionable assessments back.

Polling contract v1.0:
  GET /api/v1/context/trader-sentinel?since=<cursor>&limit=25
  Authorization: Bearer <SIGBALBOT_API_KEY>

Response:
  { ok, contract_version, generated_at, watchlist[], native_signals[] }

Requirements:
  1. Server-side worker, not frontend.
  2. Persistent cursor (generated_at / created_at).
  3. Evaluate watchlist symbols through Sentinel TA/risk.
  4. Evaluate native signals as external confirmation candidates.
  5. Only POST actionable signals — not every polling cycle.
  6. Stable id across retries.
  7. No feedback loops — never reprocess already-evaluated items.
  8. Record source SigBalBot signal id in metadata.
  9. Comprehensive logging.
"""
import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from app.brain import store as brain_store
from app.brain import sigbalbot_publisher

log = logging.getLogger(__name__)

_CONTEXT_BASE_URL: str | None = None
_API_KEY: str | None = None
_TIMEOUT = 15
_MAX_RETRIES = 3
_MAX_PROCESSED_IDS = 500

POLL_INTERVAL_S = int(os.getenv("SIGBALBOT_POLL_INTERVAL_S", "120"))
CONTEXT_LIMIT = 25

_scheduler_task: asyncio.Task | None = None

# Signals Sentinel itself has emitted — never re-evaluate these.
_SENTINEL_ID_PREFIX = "sentinel-"


def _load_config() -> tuple[str, str]:
    global _CONTEXT_BASE_URL, _API_KEY
    if _CONTEXT_BASE_URL is None:
        webhook_url = os.getenv("SIGBALBOT_WEBHOOK_URL", "").strip()
        if webhook_url:
            root = webhook_url.rsplit("/signals/", 1)[0] if "/signals/" in webhook_url else webhook_url.rstrip("/")
            _CONTEXT_BASE_URL = f"{root}/context/trader-sentinel"
        else:
            _CONTEXT_BASE_URL = ""
        _API_KEY = os.getenv("SIGBALBOT_API_KEY", "").strip()
    return _CONTEXT_BASE_URL, _API_KEY


def is_configured() -> bool:
    url, key = _load_config()
    return bool(url and key)


def _is_sentinel_signal(signal_id: str | int) -> bool:
    """Return True if the signal originated from Sentinel (feedback loop guard)."""
    return str(signal_id).startswith(_SENTINEL_ID_PREFIX)


async def fetch_context(since: str | None = None) -> dict[str, Any] | None:
    """Poll the SigBalBot context endpoint.

    Returns the parsed response on success, None on failure.
    """
    url, key = _load_config()
    if not url or not key:
        return None

    params: dict[str, str | int] = {"limit": CONTEXT_LIMIT}
    if since:
        params["since"] = since

    for attempt in range(_MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    url,
                    params=params,
                    headers={"Authorization": f"Bearer {key}"},
                )

            if resp.status_code in (401, 403):
                log.error(
                    "sigbalbot-poller: AUTH ERROR → HTTP %d. Check SIGBALBOT_API_KEY.",
                    resp.status_code,
                )
                return None

            if resp.status_code == 200:
                body = resp.json()
                if body.get("ok"):
                    return body
                log.warning("sigbalbot-poller: ok=false in response: %s", str(body)[:200])
                return None

            if resp.status_code >= 500 and attempt < _MAX_RETRIES - 1:
                log.warning(
                    "sigbalbot-poller: HTTP %d (attempt %d/%d), retrying",
                    resp.status_code, attempt + 1, _MAX_RETRIES,
                )
                await asyncio.sleep(2 ** (attempt + 1))
                continue

            log.warning("sigbalbot-poller: HTTP %d, body=%s", resp.status_code, resp.text[:200])
            return None

        except Exception as exc:
            log.warning(
                "sigbalbot-poller: network error (attempt %d/%d): %s",
                attempt + 1, _MAX_RETRIES, str(exc)[:120],
            )
            if attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(2 ** (attempt + 1))

    return None


async def _evaluate_watchlist_symbol(item: dict[str, Any]) -> dict[str, Any] | None:
    """Run a watchlist symbol through Sentinel TA + risk logic.

    Returns an actionable signal dict if Sentinel produces a recommendation,
    None otherwise.
    """
    from app.sentinel import technicals as tech_module
    from app.sentinel import risk as risk_module
    from app.brain.sleep_trader import _evaluate_entry

    symbol = item.get("symbol", "")
    if not symbol:
        return None

    try:
        ta_result = await tech_module.calculate(symbol)
        ta: dict[str, Any] = {
            "rsi": ta_result.rsi_14,
            "rsi_signal": ta_result.rsi_signal,
            "macd_trend": ta_result.macd_trend,
            "macd_histogram": ta_result.macd_histogram,
            "bb_signal": ta_result.bb_signal,
            "bb_pct": ta_result.bb_pct,
            "ema_cross": ta_result.ema_cross,
            "williams_r": ta_result.williams_r,
            "williams_signal": ta_result.williams_r_signal,
            "score": ta_result.score,
            "price": ta_result.current_price,
            "error": ta_result.error,
        }
    except Exception as exc:
        log.warning("sigbalbot-poller: TA failed for %s: %s", symbol, str(exc)[:100])
        return None

    if ta.get("error") or not ta.get("price"):
        return None

    side, confidence = _evaluate_entry(ta)
    if not side or confidence < 0.55:
        return None

    risk_label = "STANDARD"
    try:
        risk_report = await risk_module.generate_report(symbol=symbol)
        composite = risk_report.composite_score
        if composite >= 7.0:
            risk_label = "HIGH"
        elif composite >= 5.0:
            risk_label = "ELEVATED"
    except Exception:
        pass

    direction = "LONG" if side == "buy" else "SHORT"
    mode = item.get("mode", "watch")
    label = item.get("label", symbol.split("/")[0])

    signal_id = f"sentinel-ctx-{symbol.replace('/', '-')}-{direction}-{int(time.time())}"

    confirmations = []
    if ta.get("rsi") is not None:
        confirmations.append(f"RSI {ta['rsi']:.0f}")
    if ta.get("macd_trend"):
        confirmations.append(f"MACD {ta['macd_trend']}")
    if ta.get("ema_cross") and ta["ema_cross"] != "none":
        confirmations.append(f"EMA {ta['ema_cross']}")

    return {
        "id": signal_id,
        "symbol": symbol,
        "signal": direction,
        "timeframe": "1h",
        "price": ta["price"],
        "confidence": round(confidence * 100),
        "risk": risk_label,
        "reason": f"SigBalBot watchlist ({mode}:{label}) confirmed by Sentinel TA",
        "strategy": "sigbalbot-context-watchlist",
        "market_regime": ta.get("macd_trend", "unknown"),
        "confluence_score": round(confidence, 4),
        "model_version": "pytheia-sentinel-v0.2",
        "confirmations": confirmations,
        "entry": ta["price"],
        "tp1": None,
        "tp2": None,
        "sl": None,
        "metadata": {
            "source": "sigbalbot_context",
            "watchlist_mode": mode,
            "watchlist_label": label,
            "sigbalbot_market_cap": item.get("market_cap_usd"),
            "sigbalbot_volume_24h": item.get("volume_24h"),
            "sigbalbot_liquidity": item.get("liquidity_usd"),
            "sigbalbot_last_scan_signal": item.get("last_scan_signal"),
        },
    }


async def _evaluate_native_signal(signal: dict[str, Any]) -> dict[str, Any] | None:
    """Evaluate a SigBalBot native signal as external confirmation.

    Cross-references against Sentinel's own TA to decide whether to act.
    """
    from app.sentinel import technicals as tech_module
    from app.brain.sleep_trader import _evaluate_entry

    sig_id = signal.get("id")
    symbol = signal.get("symbol", "")
    sigbal_signal = signal.get("signal", "")
    sigbal_confidence = int(signal.get("confidence", 0))
    sigbal_timeframe = signal.get("timeframe", "15m")

    if not symbol or not sigbal_signal:
        return None

    # Map SigBalBot signal to direction
    sigbal_direction = None
    if sigbal_signal in ("AGG_LONG", "LONG", "BUY"):
        sigbal_direction = "LONG"
    elif sigbal_signal in ("AGG_SHORT", "SHORT", "SELL"):
        sigbal_direction = "SHORT"
    else:
        log.debug("sigbalbot-poller: skipping non-directional signal %s: %s", sig_id, sigbal_signal)
        return None

    try:
        ta_result = await tech_module.calculate(symbol)
        ta: dict[str, Any] = {
            "rsi": ta_result.rsi_14,
            "rsi_signal": ta_result.rsi_signal,
            "macd_trend": ta_result.macd_trend,
            "macd_histogram": ta_result.macd_histogram,
            "bb_signal": ta_result.bb_signal,
            "bb_pct": ta_result.bb_pct,
            "ema_cross": ta_result.ema_cross,
            "williams_r": ta_result.williams_r,
            "williams_signal": ta_result.williams_r_signal,
            "score": ta_result.score,
            "price": ta_result.current_price,
            "error": ta_result.error,
        }
    except Exception as exc:
        log.warning("sigbalbot-poller: TA failed for native signal %s (%s): %s", sig_id, symbol, str(exc)[:100])
        return None

    if ta.get("error") or not ta.get("price"):
        return None

    sentinel_side, sentinel_confidence = _evaluate_entry(ta)

    # Check agreement: SigBalBot and Sentinel must agree on direction
    sentinel_direction = None
    if sentinel_side == "buy":
        sentinel_direction = "LONG"
    elif sentinel_side == "sell":
        sentinel_direction = "SHORT"

    if sentinel_direction != sigbal_direction:
        log.info(
            "sigbalbot-poller: native signal %s (%s %s) DISAGREES with Sentinel (%s, conf=%.2f) — skipping",
            sig_id, symbol, sigbal_direction, sentinel_direction or "NONE", sentinel_confidence,
        )
        return None

    # Both agree — boost confidence from external confirmation
    combined_confidence = min(sentinel_confidence + 0.1, 1.0)
    if sigbal_confidence >= 80:
        combined_confidence = min(combined_confidence + 0.05, 1.0)

    if combined_confidence < 0.55:
        return None

    signal_id = f"sentinel-ctx-native-{symbol.replace('/', '-')}-{sigbal_direction}-{int(time.time())}"

    risk_label = signal.get("risk", "STANDARD")

    confirmations = []
    if ta.get("rsi") is not None:
        confirmations.append(f"RSI {ta['rsi']:.0f}")
    if ta.get("macd_trend"):
        confirmations.append(f"MACD {ta['macd_trend']}")
    if ta.get("ema_cross") and ta["ema_cross"] != "none":
        confirmations.append(f"EMA {ta['ema_cross']}")
    confirmations.append(f"SigBalBot {sigbal_signal} ({sigbal_confidence}%)")

    return {
        "id": signal_id,
        "symbol": symbol,
        "signal": sigbal_direction,
        "timeframe": sigbal_timeframe,
        "price": ta["price"],
        "confidence": round(combined_confidence * 100),
        "risk": risk_label,
        "reason": f"SigBalBot native signal ({sigbal_signal}) confirmed by Sentinel TA",
        "strategy": "sigbalbot-context-native",
        "market_regime": ta.get("macd_trend", "unknown"),
        "confluence_score": round(combined_confidence, 4),
        "model_version": "pytheia-sentinel-v0.2",
        "confirmations": confirmations,
        "entry": ta["price"],
        "tp1": None,
        "tp2": None,
        "sl": None,
        "metadata": {
            "source": "sigbalbot_context_native",
            "sigbalbot_signal_id": sig_id,
            "sigbalbot_signal": sigbal_signal,
            "sigbalbot_confidence": sigbal_confidence,
            "sigbalbot_timeframe": sigbal_timeframe,
            "sigbalbot_reason": signal.get("reason"),
        },
    }


async def poll_and_evaluate() -> dict[str, Any]:
    """One polling cycle: fetch context, evaluate, post actionable signals.

    Returns a summary dict with counts for logging/status.
    """
    cursor_data = brain_store.load_sigbalbot_cursor()
    last_cursor = cursor_data.get("last_cursor")
    processed_ids: list[str] = cursor_data.get("processed_signal_ids", [])
    processed_set = set(str(i) for i in processed_ids)

    summary: dict[str, Any] = {
        "ts": time.time(),
        "context_items": 0,
        "watchlist_evaluated": 0,
        "native_signals_evaluated": 0,
        "signals_posted": 0,
        "skipped_duplicates": 0,
        "skipped_feedback_loop": 0,
    }

    context = await fetch_context(since=last_cursor)
    if context is None:
        summary["error"] = "fetch_failed"
        return summary

    watchlist = context.get("watchlist", [])
    native_signals = context.get("native_signals", [])
    generated_at = context.get("generated_at")

    summary["context_items"] = len(watchlist) + len(native_signals)
    log.info(
        "sigbalbot-poller: pulled %d watchlist + %d native signals (cursor=%s)",
        len(watchlist), len(native_signals), last_cursor or "INITIAL",
    )

    new_cursor = generated_at or last_cursor
    new_processed: list[str] = list(processed_ids)
    actionable_signals: list[dict[str, Any]] = []

    # Evaluate watchlist symbols
    for item in watchlist:
        symbol = item.get("symbol", "?")
        wl_key = f"wl:{symbol}:{item.get('last_scan_at', '')}"

        if wl_key in processed_set:
            summary["skipped_duplicates"] += 1
            continue

        summary["watchlist_evaluated"] += 1
        log.info("sigbalbot-poller: evaluating watchlist symbol=%s mode=%s", symbol, item.get("mode"))

        result = await _evaluate_watchlist_symbol(item)
        if result:
            actionable_signals.append(result)
            log.info(
                "sigbalbot-poller: watchlist %s → %s (conf=%s)",
                symbol, result["signal"], result["confidence"],
            )

        new_processed.append(wl_key)

    # Evaluate native signals
    for signal in native_signals:
        sig_id = str(signal.get("id", ""))

        # Feedback loop guard
        if _is_sentinel_signal(sig_id):
            summary["skipped_feedback_loop"] += 1
            log.debug("sigbalbot-poller: skipping own signal %s (feedback loop guard)", sig_id)
            continue

        if sig_id in processed_set:
            summary["skipped_duplicates"] += 1
            continue

        summary["native_signals_evaluated"] += 1
        log.info(
            "sigbalbot-poller: evaluating native signal id=%s symbol=%s signal=%s conf=%s",
            sig_id, signal.get("symbol"), signal.get("signal"), signal.get("confidence"),
        )

        result = await _evaluate_native_signal(signal)
        if result:
            actionable_signals.append(result)
            log.info(
                "sigbalbot-poller: native signal %s → %s %s (conf=%s)",
                sig_id, signal.get("symbol"), result["signal"], result["confidence"],
            )

        # Update cursor from the latest signal's created_at
        sig_created = signal.get("created_at")
        if sig_created and (not new_cursor or sig_created > new_cursor):
            new_cursor = sig_created

        new_processed.append(sig_id)

    # Post actionable signals
    for sig in actionable_signals:
        if sigbalbot_publisher.is_configured():
            try:
                await sigbalbot_publisher.publish_signal(sig)
                summary["signals_posted"] += 1
                log.info(
                    "sigbalbot-poller: POSTED signal %s → %s %s",
                    sig["id"], sig["symbol"], sig["signal"],
                )
            except Exception as exc:
                log.warning("sigbalbot-poller: failed to post signal %s: %s", sig["id"], str(exc)[:100])
        else:
            log.info(
                "sigbalbot-poller: actionable signal %s → %s %s (publisher not configured, logged only)",
                sig["id"], sig["symbol"], sig["signal"],
            )
            summary["signals_posted"] += 1

    # Persist cursor and processed IDs (bounded to prevent unbounded growth)
    if len(new_processed) > _MAX_PROCESSED_IDS:
        new_processed = new_processed[-_MAX_PROCESSED_IDS:]

    brain_store.save_sigbalbot_cursor({
        "last_cursor": new_cursor,
        "processed_signal_ids": new_processed,
        "last_poll_at": datetime.now(timezone.utc).isoformat(),
        "last_summary": summary,
    })

    log.info(
        "sigbalbot-poller: cycle done — evaluated %d watchlist, %d native, posted %d signals, "
        "skipped %d dupes, %d feedback-loop",
        summary["watchlist_evaluated"],
        summary["native_signals_evaluated"],
        summary["signals_posted"],
        summary["skipped_duplicates"],
        summary["skipped_feedback_loop"],
    )

    return summary


async def _poll_loop():
    """Background loop: poll SigBalBot context endpoint at regular intervals."""
    log.info("sigbalbot-poller: scheduler started (interval=%ds)", POLL_INTERVAL_S)

    # Small startup delay for modules to initialize
    await asyncio.sleep(15)

    while True:
        try:
            await poll_and_evaluate()
        except Exception as exc:
            log.error("sigbalbot-poller: unexpected error in poll loop: %s", str(exc)[:200])
        await asyncio.sleep(POLL_INTERVAL_S)


def start_poller() -> asyncio.Task | None:
    """Start the context polling background scheduler.

    Guard: only one instance runs per process.
    """
    global _scheduler_task
    if not is_configured():
        log.info("sigbalbot-poller: not configured, scheduler not started")
        return None
    if _scheduler_task is not None and not _scheduler_task.done():
        log.debug("sigbalbot-poller: scheduler already running")
        return _scheduler_task
    _scheduler_task = asyncio.create_task(_poll_loop())
    return _scheduler_task


def stop_poller():
    """Stop the background poller if running."""
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        _scheduler_task = None


def get_poller_status() -> dict[str, Any]:
    """Return current poller status for the /api/sigbalbot/status endpoint."""
    cursor_data = brain_store.load_sigbalbot_cursor()
    return {
        "configured": is_configured(),
        "scheduler_running": _scheduler_task is not None and not _scheduler_task.done(),
        "poll_interval_s": POLL_INTERVAL_S,
        "last_cursor": cursor_data.get("last_cursor"),
        "last_poll_at": cursor_data.get("last_poll_at"),
        "processed_ids_count": len(cursor_data.get("processed_signal_ids", [])),
        "last_summary": cursor_data.get("last_summary"),
    }
