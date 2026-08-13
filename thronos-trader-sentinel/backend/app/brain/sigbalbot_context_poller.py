"""
SigBalBot Context Poller — Polls the bidirectional context endpoint for
watchlist symbols and native signals, evaluates them through Sentinel
technical/risk logic, and posts actionable assessments back.

Polling contract v1.0:
  GET /api/v1/context/trader-sentinel?since=<cursor>&limit=25
  Authorization: Bearer <SIGBALBOT_API_KEY>

Response:
  { ok, contract_version, generated_at, watchlist[], native_signals[],
    wallet_snapshots[], wallet_snapshot_status }

Protocol (codex integration):
  1. Call GET /api/v1/signals/trader-sentinel/status first.
  2. Verify capabilities.context_feed == "/api/v1/context/trader-sentinel".
  3. Only start context polling when that capability is present.
  4. Treat event types separately:
     - technical/risk polling: internal context only
     - market review: editorial context/news (handled by market_review_publisher)
     - finalized trade signal: inbound signal and ML outcome source
  5. Log pulled watchlist count and native signal IDs, but do not POST every poll.
  6. Only POST to /api/v1/signals/trader-sentinel after a finalized actionable assessment.
  7. 200 duplicate market-review response is success, not failure.
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
from app.brain import platinum_signal_enricher
from app.brain import wallet_snapshot_consumer

log = logging.getLogger(__name__)

_CONTEXT_BASE_URL: str | None = None
_API_KEY: str | None = None
_TIMEOUT = 15
_MAX_RETRIES = 3
_MAX_PROCESSED_IDS = 500

POLL_INTERVAL_S = int(os.getenv("SIGBALBOT_POLL_INTERVAL_S", "120"))
CONTEXT_LIMIT = 25

_scheduler_task: asyncio.Task | None = None
_context_capability_confirmed: bool = False
_contract_version: str | None = None

_SUPPORTED_CONTRACT_VERSIONS = {"1.0"}

_VALID_ASSET_CLASSES: set[str] = {"crypto", "equities", "metals"}

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


def _get_status_url() -> str:
    webhook_url = os.getenv("SIGBALBOT_WEBHOOK_URL", "").strip()
    if webhook_url:
        return webhook_url.rstrip("/") + "/status"
    return ""


def is_configured() -> bool:
    url, key = _load_config()
    return bool(url and key)


async def check_context_capability() -> bool:
    """Verify SigBalBot exposes the context_feed capability.

    Calls GET /api/v1/signals/trader-sentinel/status and checks:
      1. contract_version is in _SUPPORTED_CONTRACT_VERSIONS
      2. capabilities.context_feed contains "/context/trader-sentinel"
    """
    global _context_capability_confirmed, _contract_version
    status_url = _get_status_url()
    _, key = _load_config()
    if not status_url or not key:
        return False

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                status_url,
                headers={"Authorization": f"Bearer {key}"},
            )
        if resp.status_code != 200:
            log.warning("sigbalbot-poller: status endpoint HTTP %d — context feed not yet available", resp.status_code)
            return False

        body = resp.json()

        version = body.get("contract_version", "")
        if version and version not in _SUPPORTED_CONTRACT_VERSIONS:
            log.warning(
                "sigbalbot-poller: unsupported contract_version=%s (supported=%s)",
                version, _SUPPORTED_CONTRACT_VERSIONS,
            )
            return False

        capabilities = body.get("capabilities", {})
        context_feed = capabilities.get("context_feed", "")

        if "/context/trader-sentinel" in context_feed:
            _context_capability_confirmed = True
            _contract_version = version or "unknown"
            log.info(
                "sigbalbot-poller: context_feed capability confirmed: %s (contract_version=%s)",
                context_feed, _contract_version,
            )
            return True

        log.info("sigbalbot-poller: context_feed capability not present yet (capabilities=%s)", capabilities)
        return False

    except Exception as exc:
        log.warning("sigbalbot-poller: could not check status endpoint: %s", str(exc)[:120])
        return False


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
                if not body.get("ok"):
                    log.warning("sigbalbot-poller: ok=false in response: %s", str(body)[:200])
                    return None
                cv = body.get("contract_version", "")
                if cv and cv not in _SUPPORTED_CONTRACT_VERSIONS:
                    log.error(
                        "sigbalbot-poller: context response contract_version=%s unsupported (supported=%s)",
                        cv, _SUPPORTED_CONTRACT_VERSIONS,
                    )
                    return None
                return body

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


_HUNTER_EXCHANGES: set[str] = {"mexc", "bybit"}


def _parse_exchanges(raw: str | None) -> list[str]:
    """Parse comma-separated exchange list, normalised to lowercase."""
    if not raw:
        return []
    return [e.strip().lower() for e in str(raw).split(",") if e.strip()]


def _verify_hunter_exchanges(exchanges: list[str]) -> tuple[bool, bool, list[str]]:
    """Check mandatory hunter exchange coverage.

    Returns (hunter_verified, has_binance, risk_reasons).
    hunter_verified is True only when both MEXC and Bybit are present.
    """
    ex_set = set(exchanges)
    has_mexc = "mexc" in ex_set
    has_bybit = "bybit" in ex_set
    has_binance = "binance" in ex_set

    hunter_verified = has_mexc and has_bybit
    reasons: list[str] = []
    if not has_mexc:
        reasons.append("missing MEXC verification")
    if not has_bybit:
        reasons.append("missing Bybit verification")

    return hunter_verified, has_binance, reasons


def _assess_sniper_risk(
    item: dict[str, Any],
    exchanges: list[str],
    hunter_verified: bool,
    has_binance: bool,
    risk_label: str,
) -> tuple[str, bool, bool, list[str]]:
    """Produce sentinel verdict and risk reasons for a sniper candidate.

    Returns (verdict, market_cap_known, has_liquidity, risk_reasons).
    verdict is CONFIRM / CAUTION / REJECT.
    """
    reasons: list[str] = []

    market_cap = item.get("market_cap_usd")
    market_cap_known = market_cap is not None
    if not market_cap_known:
        reasons.append("market cap unknown")
    elif market_cap < 1_000_000:
        reasons.append("micro cap (<$1M)")

    liquidity = item.get("liquidity_usd")
    has_liquidity = liquidity is not None and liquidity > 0
    if not has_liquidity:
        reasons.append("liquidity missing or zero")
    elif liquidity < 50_000:
        reasons.append("low liquidity")

    volume = item.get("volume_24h")
    if volume is not None and has_liquidity and liquidity and liquidity > 0:
        vol_liq_ratio = volume / liquidity
        if vol_liq_ratio > 50:
            reasons.append("volume/liquidity ratio anomalous")

    if not hunter_verified:
        reasons.append("hunter exchanges incomplete")

    if risk_label in ("HIGH", "EXTREME"):
        reasons.append("elevated composite risk")

    if not hunter_verified:
        verdict = "REJECT"
    elif len(reasons) >= 3:
        verdict = "REJECT"
    elif reasons:
        verdict = "CAUTION"
    else:
        verdict = "CONFIRM"

    return verdict, market_cap_known, has_liquidity, reasons


async def _evaluate_watchlist_symbol(item: dict[str, Any]) -> dict[str, Any] | None:
    """Run a watchlist symbol through Sentinel TA + risk logic.

    Returns an actionable signal dict if Sentinel produces a recommendation,
    None otherwise. Admin watchlist acceptance is candidate selection only —
    technical, risk, and liquidity confirmation are still required.
    """
    from app.sentinel import technicals as tech_module
    from app.sentinel import risk as risk_module
    from app.brain.sleep_trader import _evaluate_entry

    symbol = item.get("symbol", "")
    if not symbol:
        return None

    asset_class = (item.get("asset_class") or "crypto").strip().lower()
    if asset_class not in _VALID_ASSET_CLASSES:
        log.warning("sigbalbot-poller: invalid asset_class=%s for %s, defaulting to crypto", asset_class, symbol)
        asset_class = "crypto"

    mode = item.get("mode", "watch")
    label = item.get("label", symbol.split("/")[0])
    exchanges = _parse_exchanges(item.get("exchanges"))
    hunter_verified, has_binance, exchange_risks = _verify_hunter_exchanges(exchanges)

    if mode == "sniper" and not hunter_verified:
        log.info(
            "sigbalbot-poller: sniper %s missing hunter exchanges (%s), lowering to CAUTION",
            symbol, ",".join(exchanges) or "none",
        )

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

    verdict, market_cap_known, has_liquidity, risk_reasons = _assess_sniper_risk(
        item, exchanges, hunter_verified, has_binance, risk_label,
    )

    if mode == "sniper" and verdict == "REJECT":
        log.info(
            "sigbalbot-poller: sniper %s REJECTED — reasons=%s",
            symbol, risk_reasons,
        )
        return None

    direction = "LONG" if side == "buy" else "SHORT"
    signal_id = f"sentinel-ctx-{symbol.replace('/', '-')}-{direction}-{int(time.time())}"

    confirmations = []
    if ta.get("rsi") is not None:
        confirmations.append(f"RSI {ta['rsi']:.0f}")
    if ta.get("macd_trend"):
        confirmations.append(f"MACD {ta['macd_trend']}")
    if ta.get("ema_cross") and ta["ema_cross"] != "none":
        confirmations.append(f"EMA {ta['ema_cross']}")
    if has_binance:
        confirmations.append("Binance listing confirmed")

    return {
        "id": signal_id,
        "symbol": symbol,
        "signal": direction,
        "asset_class": asset_class,
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
            "asset_class": asset_class,
            "watchlist_mode": mode,
            "watchlist_label": label,
            "source_exchanges": exchanges,
            "market_cap_usd": item.get("market_cap_usd"),
            "market_cap_known": market_cap_known,
            "volume_24h": item.get("volume_24h"),
            "liquidity_usd": item.get("liquidity_usd"),
            "liquidity_definition": "visible_order_book_depth_2pct",
            "sentinel_verdict": verdict,
            "sentinel_risk_reasons": risk_reasons,
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

    asset_class = (signal.get("asset_class") or "crypto").strip().lower()
    if asset_class not in _VALID_ASSET_CLASSES:
        asset_class = "crypto"

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
        "asset_class": asset_class,
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
            "asset_class": asset_class,
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

    snap_count = wallet_snapshot_consumer.ingest_wallet_snapshots(context)
    if snap_count:
        log.info("sigbalbot-poller: ingested %d wallet snapshots", snap_count)
    summary["wallet_snapshots_ingested"] = snap_count

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
                sig = platinum_signal_enricher.enrich_signal(sig, sig)
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
    """Background loop: poll SigBalBot context endpoint at regular intervals.

    Protocol: checks capabilities.context_feed from the status endpoint before
    starting to poll. If not yet available, retries each interval until confirmed.
    """
    global _context_capability_confirmed
    log.info("sigbalbot-poller: scheduler started (interval=%ds)", POLL_INTERVAL_S)

    await asyncio.sleep(15)

    while True:
        try:
            if not _context_capability_confirmed:
                available = await check_context_capability()
                if not available:
                    log.info("sigbalbot-poller: context_feed not available yet, will retry in %ds", POLL_INTERVAL_S)
                    await asyncio.sleep(POLL_INTERVAL_S)
                    continue

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
        "context_capability_confirmed": _context_capability_confirmed,
        "contract_version": _contract_version,
        "poll_interval_s": POLL_INTERVAL_S,
        "last_cursor": cursor_data.get("last_cursor"),
        "last_poll_at": cursor_data.get("last_poll_at"),
        "processed_ids_count": len(cursor_data.get("processed_signal_ids", [])),
        "last_summary": cursor_data.get("last_summary"),
        "wallet_snapshot": wallet_snapshot_consumer.get_snapshot_status(),
    }
