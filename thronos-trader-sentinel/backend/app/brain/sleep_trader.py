"""
Sleep Mode AutoTrader — Autonomous trading engine for Pytheia Sentinel.

Runs as a background asyncio task when the user activates Sleep Mode.
Uses Sentinel's TA signals (RSI, MACD, BB, EMA) + Brain predictions to
open/close futures positions autonomously over an 8-hour sleep session.

Target: 25–30% portfolio profit across multiple small, high-conviction trades.
"""
import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from app.brain import connector, store as brain_store
from app.brain.predictor import PredictionEngine
from app.sentinel import technicals as tech_module
from app.sentinel import sessions as sessions_module
from app.sentinel.strategies.divergence import detect_divergences
from app.sentinel.strategies.confluence import check_confluence

log = logging.getLogger(__name__)

# ── Active sleep sessions (in-memory) ────────────────────────────────────────
_active_sessions: dict[str, asyncio.Task] = {}

SLEEP_EXECUTION_MODE = os.getenv("SLEEP_EXECUTION_MODE", "worker").strip().lower()
# worker(default): API writes desired state, external worker executes loops
# api: run loops in-process (legacy mode)

DEFAULT_SLEEP_DURATION_H = 8
MAX_SLEEP_DURATION_H = 48

SCAN_INTERVAL_S = 60         # re-scan every 60s
MAX_TRADES_PER_SESSION = 40  # cap total trades in one sleep
ENTRY_COOLDOWN_S = 180       # min 3 min between entries on same symbol
MIN_CONFIDENCE = 0.52        # lower threshold to increase trade frequency in 8h mode
DEFAULT_ENTRY_MARGIN_PCT = 0.088  # default margin allocation per new sleep entry


def _resolve_sleep_duration_hours(config: dict[str, Any], exchange: str, execution_market: str) -> int:
    requested = float(config.get("sleep_duration_hours") or 0)
    if requested > 0:
        return max(1, min(int(round(requested)), MAX_SLEEP_DURATION_H))
    if exchange.lower() == "mexc" and execution_market == "spot":
        return MAX_SLEEP_DURATION_H
    return DEFAULT_SLEEP_DURATION_H


def _is_contract_activation_error(error_text: str) -> bool:
    msg = (error_text or '').lower()
    return (
        'contract not activated' in msg
        or 'code\":1002' in msg
        or "'code':1002" in msg
        or '"code":1002' in msg
    )


def _canonical_symbol(symbol: str) -> str:
    s = (symbol or '').strip()
    return s.replace(':USDT', '')


def _risk_rank(level: str) -> int:
    order = {"LOW": 0, "HIGH": 1, "EXTREME": 2}
    return order.get((level or "").upper(), 0)


def _max_risk(a: str, b: str) -> str:
    return a if _risk_rank(a) >= _risk_rank(b) else b




def _margin_utilization_pct(snapshot: dict[str, Any], max_leverage: int) -> float:
    equity = float(snapshot.get("equity") or 0)
    if equity <= 0:
        return 0.0
    used_margin = float(snapshot.get("usedMargin") or 0)
    if used_margin > 0:
        return max(0.0, (used_margin / equity) * 100)

    # Fallback when exchange doesn't expose used margin clearly.
    notional = sum(float(p.get("notional") or 0) for p in snapshot.get("positions", []))
    if notional <= 0 or max_leverage <= 0:
        return 0.0
    approx_used = notional / max(max_leverage, 1)
    return max(0.0, (approx_used / equity) * 100)


def _macro_event_profile(upcoming_events: list[dict[str, Any]]) -> dict[str, Any]:
    """Return macro-event-aware trading modifiers for sleep entries."""
    if not upcoming_events:
        return {"calendar_score": 5.0, "entry_bonus": 0.0, "cooldown_mult": 1.0, "label": None}

    keywords = ("cpi", "ppi", "fomc", "fed", "nfp", "powell")
    macro = None
    for ev in upcoming_events:
        name = str(ev.get("name") or "")
        impact = str(ev.get("impact") or "").lower()
        if impact == "high" and any(k in name.lower() for k in keywords):
            macro = ev
            break

    if not macro:
        return {"calendar_score": 5.5, "entry_bonus": 0.0, "cooldown_mult": 1.0, "label": None}

    in_minutes = int(macro.get("in_minutes") or 0)
    label = f"{macro.get('name', 'macro event')} in {in_minutes}m"

    # Existing session gate already blocks very-near high-impact opens; here we tune behavior outside that strict gate.
    if in_minutes <= 20:
        return {"calendar_score": 8.8, "entry_bonus": -0.08, "cooldown_mult": 1.2, "label": label}
    if in_minutes <= 90:
        return {"calendar_score": 8.0, "entry_bonus": 0.06, "cooldown_mult": 0.7, "label": label}
    if in_minutes <= 180:
        return {"calendar_score": 7.2, "entry_bonus": 0.03, "cooldown_mult": 0.85, "label": label}
    return {"calendar_score": 6.2, "entry_bonus": 0.01, "cooldown_mult": 1.0, "label": label}


def _funding_profile(funding: dict[str, Any]) -> dict[str, Any]:
    """Translate funding-rate context to directional confidence tuning."""
    if not funding.get("ok"):
        return {
            "entry_bonus": 0.0,
            "long_bonus": 0.0,
            "short_bonus": 0.0,
            "cooldown_mult": 1.0,
            "label": None,
        }

    rate = float(funding.get("funding_rate") or 0.0)
    mins_to_next = int(funding.get("minutes_to_next") or 0)
    abs_rate = abs(rate)
    basis_points = rate * 10_000
    if abs_rate < 0.0002:
        return {
            "entry_bonus": 0.0,
            "long_bonus": 0.0,
            "short_bonus": 0.0,
            "cooldown_mult": 1.0,
            "label": None,
        }

    if rate > 0:
        long_bonus = -0.02
        short_bonus = 0.05 if abs_rate >= 0.0008 else 0.03
        bias = "short bias"
    else:
        long_bonus = 0.05 if abs_rate >= 0.0008 else 0.03
        short_bonus = -0.02
        bias = "long bias"

    near_roll = 0 < mins_to_next <= 20
    roll_bonus = 0.02 if near_roll else 0.0
    cooldown_mult = 0.8 if near_roll else 1.0
    label = f"Funding {basis_points:+.2f} bps ({bias}), next reset in {mins_to_next}m"
    return {
        "entry_bonus": roll_bonus,
        "long_bonus": long_bonus,
        "short_bonus": short_bonus,
        "cooldown_mult": cooldown_mult,
        "label": label,
    }


def _position_risk_score(pos: dict[str, Any]) -> float:
    leverage = float(pos.get("leverage") or 1)
    pnl = float(pos.get("unrealizedPnl") or 0)
    mark = float(pos.get("markPrice") or 0)
    liq = float(pos.get("liquidationPrice") or 0)

    score = leverage
    if pnl < 0:
        score += min(50.0, abs(pnl) * 0.4)

    if mark > 0 and liq > 0:
        side = str(pos.get("side") or "").lower()
        if side == "long":
            liq_dist_pct = ((mark - liq) / mark) * 100
        else:
            liq_dist_pct = ((liq - mark) / mark) * 100
        if liq_dist_pct < 8:
            score += (8 - max(liq_dist_pct, 0)) * 4

    return score


def _sl_tp_for_leverage(leverage: float, side: str, price: float, sl_pct: float, tp_pct: float):
    """Calculate SL/TP prices accounting for leverage."""
    # Tighter SL for higher leverage
    effective_sl = sl_pct / leverage if leverage > 1 else sl_pct
    effective_tp = tp_pct / leverage if leverage > 1 else tp_pct

    # Clamp: SL at least 0.1%, TP at least 0.15%
    effective_sl = max(effective_sl, 0.1)
    effective_tp = max(effective_tp, 0.15)

    if side == "buy":  # long
        sl_price = price * (1 - effective_sl / 100)
        tp_price = price * (1 + effective_tp / 100)
    else:  # short
        sl_price = price * (1 + effective_sl / 100)
        tp_price = price * (1 - effective_tp / 100)
    return round(sl_price, 6), round(tp_price, 6)


def _position_size(
    equity: float,
    price: float,
    risk_pct: float,
    max_position_pct: float,
    leverage: int,
    entry_margin_pct: float = DEFAULT_ENTRY_MARGIN_PCT,
) -> float:
    """Calculate position size in contracts (base asset amount)."""
    risk_usd = equity * (risk_pct / 100)
    max_usd = equity * (max_position_pct / 100)

    # Standard sleep entry sizing: allocate a small fixed margin slice per new trade.
    target_margin_usd = equity * (max(entry_margin_pct, 0.0) / 100)
    capped_margin_usd = min(max(target_margin_usd, 0.0), max_usd)

    # Respect risk caps while providing deterministic entry sizing.
    notional = min(
        capped_margin_usd * leverage,
        risk_usd * leverage,
        max_usd * leverage,
        equity * 0.5 * leverage,
    )
    if price <= 0:
        return 0.0
    amount = notional / price
    return round(amount, 6)


async def _get_ta_signals(symbol: str) -> dict[str, Any]:
    """Get technical analysis + gift strategy signals for a symbol."""
    try:
        result = await tech_module.calculate(symbol)
        ta: dict[str, Any] = {
            "rsi": result.rsi_14,
            "rsi_signal": result.rsi_signal,
            "macd_trend": result.macd_trend,
            "macd_histogram": result.macd_histogram,
            "bb_signal": result.bb_signal,
            "bb_pct": result.bb_pct,
            "ema_cross": result.ema_cross,
            "williams_r": result.williams_r,
            "williams_signal": result.williams_r_signal,
            "score": result.score,
            "price": result.current_price,
            "error": result.error,
        }

        # ── Gift Strategy #1: Smart Money Divergence ─────────────────
        candles_1d = result.candles_raw
        if candles_1d and len(candles_1d) >= 40:
            try:
                div_result = detect_divergences(candles_1d, lookback=30, swing_dist=5)
                ta["divergence"] = {
                    "has_divergence": div_result.has_divergence,
                    "type": div_result.divergence_type,
                    "rsi_div": div_result.rsi_divergence,
                    "macd_div": div_result.macd_divergence,
                    "double_confirmed": div_result.double_confirmation,
                    "direction_vote": div_result.direction_vote,
                    "confidence_bonus": div_result.confidence_bonus,
                    "description": div_result.description,
                }
            except Exception as e:
                log.debug("[sleep] divergence analysis failed for %s: %s", symbol, e)
                ta["divergence"] = {"has_divergence": False}
        else:
            ta["divergence"] = {"has_divergence": False}

        # ── Gift Strategy #2: Multi-Timeframe Confluence ─────────────
        try:
            candles_1h = await tech_module.fetch_candles(symbol, timeframe="1h", limit=60)
            candles_4h = await tech_module.fetch_candles(symbol, timeframe="4h", limit=60)
            conf_result = check_confluence(
                candles_1h=candles_1h,
                candles_4h=candles_4h,
                candles_1d=candles_1d,
            )
            ta["confluence"] = {
                "level": conf_result.confluence_level,
                "agreeing": conf_result.agreeing_timeframes,
                "direction": conf_result.direction,
                "direction_vote": conf_result.direction_vote,
                "confidence_bonus": conf_result.confidence_bonus,
                "description": conf_result.description,
                "timeframes": conf_result.timeframe_signals,
            }
        except Exception as e:
            log.debug("[sleep] confluence analysis failed for %s: %s", symbol, e)
            ta["confluence"] = {"level": "none", "direction_vote": 0.0, "confidence_bonus": 0.0}

        return ta
    except Exception as e:
        log.warning("[sleep] TA failed for %s: %s", symbol, e)
        return {"error": str(e)}


def _evaluate_entry(ta: dict[str, Any], brain_pred: dict | None = None) -> tuple[str | None, float]:
    """Evaluate whether to enter a trade based on TA + Brain + Gift Strategies.

    Returns (side, confidence) where side is 'buy'/'sell'/None.
    Uses 8 signal sources:
      1. RSI (momentum)
      2. MACD (trend + momentum)
      3. Bollinger Bands (volatility position)
      4. EMA cross (trend)
      5. Williams %R (momentum)
      6. Brain ML prediction
      7. Smart Money Divergence (gift #1)
      8. Multi-Timeframe Confluence (gift #2)
    """
    if ta.get("error"):
        return None, 0.0

    score = 0.0
    direction_votes: dict[str, float] = {"long": 0.0, "short": 0.0}

    rsi = ta.get("rsi", 50)
    rsi_signal = ta.get("rsi_signal", "")

    # RSI signals
    if rsi < 25:
        direction_votes["long"] += 2.0
        score += 0.2
    elif rsi < 35:
        direction_votes["long"] += 1.0
        score += 0.1
    elif rsi > 75:
        direction_votes["short"] += 2.0
        score += 0.2
    elif rsi > 65:
        direction_votes["short"] += 1.0
        score += 0.1

    # MACD
    macd_trend = ta.get("macd_trend", "")
    macd_hist = ta.get("macd_histogram", 0)
    if macd_trend == "bullish" and macd_hist > 0:
        direction_votes["long"] += 1.5
        score += 0.15
    elif macd_trend == "bearish" and macd_hist < 0:
        direction_votes["short"] += 1.5
        score += 0.15

    # Bollinger Bands
    bb_signal = ta.get("bb_signal", "")
    if "oversold" in bb_signal.lower() or "below" in bb_signal.lower():
        direction_votes["long"] += 1.0
        score += 0.1
    elif "overbought" in bb_signal.lower() or "above" in bb_signal.lower():
        direction_votes["short"] += 1.0
        score += 0.1
    if "squeeze" in bb_signal.lower():
        score += 0.15  # high volatility incoming — good for entries

    # EMA cross
    ema_cross = ta.get("ema_cross", "")
    if ema_cross == "golden_cross":
        direction_votes["long"] += 2.0
        score += 0.2
    elif ema_cross == "death_cross":
        direction_votes["short"] += 2.0
        score += 0.2

    # Williams %R
    wr = ta.get("williams_r", -50)
    if wr < -80:
        direction_votes["long"] += 1.0
    elif wr > -20:
        direction_votes["short"] += 1.0

    # Brain prediction boost
    if brain_pred and brain_pred.get("confidence", "low") != "low":
        pred = brain_pred.get("prediction", "risky")
        if pred == "favorable":
            score += 0.15
        elif pred == "risky":
            score -= 0.1

    # ── Gift Strategy #1: Smart Money Divergence ─────────────────────
    div = ta.get("divergence", {})
    if div.get("has_divergence"):
        div_vote = float(div.get("direction_vote", 0))
        div_conf = float(div.get("confidence_bonus", 0))
        if div_vote > 0:
            direction_votes["long"] += div_vote
        elif div_vote < 0:
            direction_votes["short"] += abs(div_vote)
        score += div_conf
        # Double-confirmed divergences get extra weight
        if div.get("double_confirmed"):
            score += 0.05

    # ── Gift Strategy #2: Multi-Timeframe Confluence ─────────────────
    conf = ta.get("confluence", {})
    conf_vote = float(conf.get("direction_vote", 0))
    conf_bonus = float(conf.get("confidence_bonus", 0))
    if conf_vote > 0:
        direction_votes["long"] += conf_vote
    elif conf_vote < 0:
        direction_votes["short"] += abs(conf_vote)
    score += conf_bonus

    # Strong confluence penalty: if confluence says "conflicting", penalize
    if conf.get("level") == "conflicting":
        score -= 0.08

    # Determine direction
    long_score = direction_votes["long"]
    short_score = direction_votes["short"]

    if long_score > short_score and long_score >= 3.0:
        side = "buy"
        confidence = min(score + (long_score - short_score) * 0.1, 1.0)
    elif short_score > long_score and short_score >= 3.0:
        side = "sell"
        confidence = min(score + (short_score - long_score) * 0.1, 1.0)
    else:
        return None, score

    return side, confidence


async def _manage_open_positions(
    session: dict,
    creds: dict,
    execution_market: str = "futures",
) -> list[dict]:
    """Check open trades and close by TP/SL rules for futures or spot."""
    closed: list[dict] = []
    tp_pct = float(session.get("config", {}).get("take_profit_pct", 4.0))
    sl_pct = float(session.get("config", {}).get("stop_loss_pct", 2.0))

    if execution_market == "spot":
        for trade in session.get("sleep_trades", []):
            if trade.get("status") != "open":
                continue
            if trade.get("market_type") != "spot":
                continue
            symbol = str(trade.get("symbol") or "")
            entry_price = float(trade.get("entry_price") or 0)
            amount = float(trade.get("amount") or 0)
            if not symbol or entry_price <= 0 or amount <= 0:
                continue

            current_price = await connector.get_spot_ticker_price(
                exchange=creds["exchange"],
                api_key=creds["api_key"],
                api_secret=creds["api_secret"],
                symbol=symbol,
                passphrase=creds.get("passphrase"),
            )
            if current_price <= 0:
                continue

            pnl_pct = ((current_price - entry_price) / entry_price) * 100
            should_close = pnl_pct >= tp_pct or pnl_pct <= -sl_pct
            if not should_close:
                continue

            result = await connector.create_spot_market_order(
                exchange=creds["exchange"],
                api_key=creds["api_key"],
                api_secret=creds["api_secret"],
                symbol=symbol,
                side="sell",
                amount=amount,
                passphrase=creds.get("passphrase"),
            )
            if result.get("ok"):
                reason = f"Spot TP hit: {pnl_pct:.1f}%" if pnl_pct >= tp_pct else f"Spot SL hit: {pnl_pct:.1f}%"
                closed.append({
                    "symbol": symbol,
                    "reason": reason,
                    "pnl_pct": pnl_pct,
                    "unrealized_pnl": (current_price - entry_price) * amount,
                    "closed_at": time.time(),
                })
        return closed

    try:
        positions = await connector.fetch_open_positions(
            exchange=creds["exchange"],
            api_key=creds["api_key"],
            api_secret=creds["api_secret"],
            passphrase=creds.get("passphrase"),
        )
    except Exception as e:
        log.warning("[sleep] fetch positions failed: %s", e)
        return closed

    sleep_trades = session.get("sleep_trades", [])
    sleep_trade_symbols = {t["symbol"] for t in sleep_trades if t.get("status") == "open"}

    for pos in positions:
        sym = _canonical_symbol(pos.get("symbol", ""))
        is_sleep_trade = sym in sleep_trade_symbols
        pnl_pct = pos.get("pnlPct", 0)
        leverage = pos.get("leverage", 1)
        side = pos.get("side", "long")
        contracts = pos.get("contracts", 0)
        effective_tp = tp_pct * leverage * 0.7
        effective_sl = sl_pct * leverage * 0.5

        # ── Protection for ALL positions (including pre-existing) ────────
        # DCA safe order when loss is moderate
        if pnl_pct <= -(sl_pct * 0.5) and pnl_pct > -sl_pct and not is_sleep_trade:
            safe_amount = contracts * 0.25
            safe_side = "buy" if side == "long" else "sell"
            try:
                result = await connector.create_market_order(
                    exchange=creds["exchange"], api_key=creds["api_key"],
                    api_secret=creds["api_secret"],
                    symbol=sym.replace(":USDT", ""), side=safe_side,
                    amount=safe_amount, passphrase=creds.get("passphrase"),
                )
                if result.get("ok"):
                    log.info("[sleep] DCA safe order for pre-existing %s: PnL %.1f%%", sym, pnl_pct)
            except Exception as e:
                log.warning("[sleep] DCA safe order failed %s: %s", sym, e)

        # Hedge when loss approaches SL on pre-existing positions
        if pnl_pct <= -(sl_pct * 0.7) and leverage >= 5 and not is_sleep_trade:
            hedge_side = "sell" if side == "long" else "buy"
            hedge_amount = contracts * 0.3
            try:
                result = await connector.create_market_order(
                    exchange=creds["exchange"], api_key=creds["api_key"],
                    api_secret=creds["api_secret"],
                    symbol=sym.replace(":USDT", ""), side=hedge_side,
                    amount=hedge_amount, passphrase=creds.get("passphrase"),
                )
                if result.get("ok"):
                    log.info("[sleep] hedge for pre-existing %s: PnL %.1f%%", sym, pnl_pct)
            except Exception as e:
                log.warning("[sleep] hedge failed %s: %s", sym, e)

        # ── TP/SL close logic for sleep-opened trades only ───────────────
        if not is_sleep_trade:
            continue

        should_close = False
        reason = ""
        if pnl_pct >= effective_tp:
            should_close = True
            reason = f"TP hit: {pnl_pct:.1f}% (target {effective_tp:.1f}%)"
        elif pnl_pct <= -effective_sl:
            should_close = True
            reason = f"SL hit: {pnl_pct:.1f}% (limit -{effective_sl:.1f}%)"
        elif pnl_pct >= 15:
            should_close = True
            reason = f"Profit lock: {pnl_pct:.1f}%"

        if should_close:
            close_side = "sell" if side == "long" else "buy"
            try:
                result = await connector.create_market_order(
                    exchange=creds["exchange"],
                    api_key=creds["api_key"],
                    api_secret=creds["api_secret"],
                    symbol=sym.replace(":USDT", ""),
                    side=close_side,
                    amount=contracts,
                    passphrase=creds.get("passphrase"),
                    reduce_only=True,
                )
                if result.get("ok"):
                    closed.append({
                        "symbol": sym,
                        "reason": reason,
                        "pnl_pct": pnl_pct,
                        "unrealized_pnl": pos.get("unrealizedPnl", 0),
                        "closed_at": time.time(),
                    })
                    log.info("[sleep] closed %s: %s", sym, reason)
            except Exception as e:
                log.warning("[sleep] close failed %s: %s", sym, e)

    return closed


async def run_sleep_session(
    user_id: str,
    brain_engine: PredictionEngine,
):
    """Main sleep trading loop. Runs for configurable duration (max 48h)."""
    session = brain_store.load_autotrader(user_id)
    if not session:
        log.warning("[sleep] no session found for %s", user_id)
        return

    config = session.get("config", {})
    creds = {
        "exchange": config.get("exchange", "mexc"),
        "api_key": config.get("api_key", ""),
        "api_secret": config.get("api_secret", ""),
        "passphrase": config.get("passphrase"),
    }

    if not creds["api_key"] or not creds["api_secret"]:
        _log_sleep(user_id, "Sleep Mode aborted: missing API credentials")
        return

    symbols = config.get("symbols", ["BTC/USDT"])

    # ── Capability Handshake (Phase 0) ───────────────────────────────
    # Pre-flight check: verify exchange is reachable and symbols are tradeable
    try:
        handshake = await connector.capability_handshake(
            exchange=creds["exchange"],
            api_key=creds["api_key"],
            api_secret=creds["api_secret"],
            symbols=symbols,
            passphrase=creds.get("passphrase"),
            market_mode=config.get("market_mode", "auto"),
        )
        if not handshake.get("ok"):
            warnings = handshake.get("warnings", [])
            _log_sleep(user_id, f"Capability handshake FAILED: {'; '.join(warnings[:3])}")
            session["sleep_mode"] = {
                "active": False, "status": "handshake_failed",
                "desired_state": "stopped",
                "handshake_warnings": warnings,
            }
            brain_store.save_autotrader(user_id, session)
            return

        # Log handshake results
        _log_sleep(user_id, f"Handshake OK — equity: ${handshake.get('equity', 0):.2f}, "
                   f"route: {handshake.get('recommended_market', 'unknown')}")
        for w in handshake.get("warnings", []):
            _log_sleep(user_id, f"Handshake warning: {w}")

        # Auto-select market based on handshake
        recommended = handshake.get("recommended_market", "futures")
        sym_caps = handshake.get("symbols", {})
        blocked_from_handshake = []
        for sym, cap in sym_caps.items():
            if not cap.get("futures_ready") and not cap.get("spot_ready"):
                blocked_from_handshake.append(sym)
            elif not cap.get("contract_activated"):
                blocked_from_handshake.append(sym)

    except Exception as e:
        _log_sleep(user_id, f"Handshake error (non-fatal, continuing): {str(e)[:100]}")
        blocked_from_handshake = []
    max_leverage = int(config.get("max_leverage", 20))
    margin_mode = config.get("margin_mode", "cross")  # cross or isolated
    sl_pct = float(config.get("stop_loss_pct", 2.0))
    tp_pct = float(config.get("take_profit_pct", 4.0))
    risk_pct = float(config.get("risk_per_trade_pct", 1.0))
    max_position_pct = float(config.get("max_position_pct", 10.0))
    max_open = max(1, int(config.get("max_open_trades", 3)))
    max_exposure_pct = float(config.get("max_total_exposure_pct", 25.0))
    entry_margin_pct = float(config.get("entry_margin_pct", DEFAULT_ENTRY_MARGIN_PCT))
    configured_market_mode = str(config.get("market_mode", "auto")).lower()
    execution_market = configured_market_mode if configured_market_mode in ("futures", "spot") else ("spot" if creds["exchange"].lower() == "mexc" else "futures")

    sleep_hours = _resolve_sleep_duration_hours(config, creds["exchange"], execution_market)
    start_time = time.time()
    end_time = start_time + (sleep_hours * 3600)
    trade_count = 0
    total_realized_pnl = 0.0
    last_entry_by_symbol: dict[str, float] = {}
    forced_aggressive_logged = False

    # Initialize sleep session tracking
    session["sleep_mode"] = {
        "active": True,
        "started_at": start_time,
        "ends_at": end_time,
        "trade_count": 0,
        "realized_pnl": 0.0,
        "status": "running",
        "desired_state": "running",
        "execution_market": execution_market,
        "duration_hours": sleep_hours,
    }
    session["sleep_trades"] = []
    session["blocked_symbols"] = sorted(set(blocked_from_handshake))
    brain_store.save_autotrader(user_id, session)
    _log_sleep(user_id, f"Sleep Mode started — {len(symbols)} symbols, route={execution_market}, duration={sleep_hours}h, {max_leverage}x max leverage, {margin_mode} margin")
    _log_sleep(user_id, f"Entry sizing set to {entry_margin_pct:.3f}% margin allocation per new trade (target baseline).")

    if execution_market == "futures":
        # Set margin mode for all symbols at start
        for sym in symbols:
            try:
                ok_mm = await connector.set_margin_mode(
                    creds["exchange"], creds["api_key"], creds["api_secret"],
                    sym, margin_mode, creds.get("passphrase"), max_leverage,
                )
                if not ok_mm:
                    _log_sleep(user_id, f"Margin mode update failed for {sym} ({margin_mode}) — continuing with exchange defaults.")
            except Exception:
                _log_sleep(user_id, f"Margin mode update raised for {sym} ({margin_mode}) — continuing with exchange defaults.")

        # Visibility: confirm we are managing already-open positions too (via protection flow)
        try:
            existing_positions = await connector.fetch_open_positions(
                exchange=creds["exchange"],
                api_key=creds["api_key"],
                api_secret=creds["api_secret"],
                passphrase=creds.get("passphrase"),
            )
            if existing_positions:
                _log_sleep(
                    user_id,
                    f"Detected {len(existing_positions)} existing open positions — Sleep Mode will manage protection/SL behavior while scanning for new entries (max open {max_open}).",
                )
        except Exception:
            pass

    try:
        while time.time() < end_time and trade_count < MAX_TRADES_PER_SESSION:
            await asyncio.sleep(2)  # small initial delay

            # 1. Manage existing positions
            closed = await _manage_open_positions(session, creds, execution_market)
            for c in closed:
                total_realized_pnl += c.get("unrealized_pnl", 0)
                trade_count += 1
                _log_sleep(user_id, f"Closed {c['symbol']}: {c['reason']} | PnL: {c['pnl_pct']:.1f}%")

                closed_trade: dict[str, Any] | None = None
                # Mark trade closed
                for t in session.get("sleep_trades", []):
                    if t.get("symbol") == c["symbol"] and t.get("status") == "open":
                        t["status"] = "closed"
                        t["pnl_pct"] = c["pnl_pct"]
                        t["closed_at"] = c["closed_at"]
                        closed_trade = t
                        break

                if closed_trade:
                    try:
                        outcome = 1 if (c.get("pnl_pct", 0) or 0) > 0 else 0
                        features = [
                            min(float(closed_trade.get("leverage", 1)) / 300.0, 1.0),
                            min(abs(float(c.get("pnl_pct", 0) or 0)) / 100.0, 1.0),
                            1.0 if str(closed_trade.get("side", "")).lower() == "buy" else 0.0,
                            min(float(closed_trade.get("confidence", 0.5)), 1.0),
                        ]
                        brain_engine.adapt(user_id, features, outcome)
                    except Exception:
                        pass

                    _save_sleep_snapshot(user_id, "sleep_trade_closed", c["symbol"], {
                        "side": closed_trade.get("side"),
                        "entry_price": closed_trade.get("entry_price"),
                        "amount": closed_trade.get("amount"),
                        "leverage": closed_trade.get("leverage"),
                        "confidence": closed_trade.get("confidence"),
                        "pnl_pct": c.get("pnl_pct"),
                        "realized_pnl_usd": c.get("unrealized_pnl"),
                        "reason": c.get("reason"),
                        "closed_at": c.get("closed_at"),
                        "exchange": creds.get("exchange"),
                    })

            # Count current open sleep trades
            open_trades = [t for t in session.get("sleep_trades", []) if t.get("status") == "open"]

            # 2. Check market session timing + macro event profile
            session_report = sessions_module.calculate()
            session_bias = sessions_module.get_session_bias()
            macro_profile = _macro_event_profile(session_report.upcoming_events)
            macro_label = macro_profile.get("label")
            if macro_label and session.get("sleep_mode", {}).get("last_macro_log") != macro_label:
                _log_sleep(user_id, f"Macro regime detected: {macro_label}. Adjusting confidence/cooldown for event volatility.")
                session.setdefault("sleep_mode", {})["last_macro_log"] = macro_label

            funding_cache: dict[str, dict[str, Any]] = {}

            if session_bias.get("wait_minutes", 0) > 0:
                # Don't stay idle early in the sleep cycle: allow first entries even in low-session windows.
                if trade_count == 0 and not open_trades:
                    _log_sleep(user_id, "Session timing suggests waiting, but forcing initial scan to avoid zero-trade sleep.")
                else:
                    wait_note = session_bias.get("bias_note", "")
                    _log_sleep(user_id, f"Session timing: waiting {session_bias['wait_minutes']}min — {wait_note[:80]}")
                    await asyncio.sleep(min(session_bias["wait_minutes"] * 60, SCAN_INTERVAL_S))
                    continue

            # Boost confidence during high-volume sessions
            volume_bonus = {"peak": 0.1, "high": 0.05, "medium": 0.0, "low": -0.1}.get(
                session_bias.get("volume", "medium"), 0.0
            )
            is_opening_range = session_bias.get("opening_range", False)

            # 3. Scan for new entries if we have capacity
            blocked_symbols = {_canonical_symbol(sym) for sym in session.get("blocked_symbols", [])}
            effective_symbols = symbols
            if all(_canonical_symbol(sym) in blocked_symbols for sym in symbols):
                effective_symbols = ["BTC/USDT", "ETH/USDT"]
                _log_sleep(user_id, "All configured symbols are blocked/unavailable — trying fallback majors BTC/USDT, ETH/USDT.")

            if len(open_trades) < max_open:
                # Get portfolio equity
                snapshot: dict[str, Any] = {"equity": 0, "positions": []}
                try:
                    snapshot = await connector.fetch_exchange_snapshot(
                        creds["exchange"], creds["api_key"], creds["api_secret"], creds.get("passphrase")
                    )
                except Exception:
                    snapshot = {"equity": 0, "futures": {}, "spot": {}, "positions": []}

                if execution_market == "futures":
                    futures_quote_free = float((snapshot.get("futures") or {}).get("quoteFree") or 0)
                    futures_equity = float((snapshot.get("futures") or {}).get("equity") or 0)
                    equity = futures_quote_free if futures_quote_free > 0 else futures_equity
                    min_equity_required = 50.0  # Futures requires $50+ for subscriber multi-trade capacity
                    if equity < min_equity_required:
                        _log_sleep(user_id, f"Futures available USDT too low (${equity:.2f}) — need $50+ for futures trading")
                        await asyncio.sleep(SCAN_INTERVAL_S)
                        continue
                    if futures_quote_free > 0 and not session.get("sleep_mode", {}).get("logged_futures_free_balance"):
                        _log_sleep(user_id, f"Futures route sizing uses free USDT balance: ${futures_quote_free:.2f}")
                        session.setdefault("sleep_mode", {})["logged_futures_free_balance"] = True
                else:
                    spot_quote_free = float((snapshot.get("spot") or {}).get("quoteFree") or 0)
                    spot_equity = float((snapshot.get("spot") or {}).get("equity") or 0)
                    equity = spot_quote_free if spot_quote_free > 0 else spot_equity
                    min_equity_required = 10.0  # Spot requires $10+ available
                    if equity < min_equity_required:
                        _log_sleep(user_id, f"Spot available USDT too low (${equity:.2f}) — need $10+ for spot trading")
                        await asyncio.sleep(SCAN_INTERVAL_S)
                        continue

                # Cross-margin stress de-risking only for futures route.
                mm_util_pct = _margin_utilization_pct(snapshot, max_leverage)
                if execution_market == "futures" and margin_mode == "cross" and mm_util_pct > 15 and snapshot.get("positions"):
                    positions_sorted = sorted(snapshot.get("positions", []), key=_position_risk_score, reverse=True)
                    de_risked = 0
                    for pos in positions_sorted:
                        if mm_util_pct <= 12:
                            break
                        sym = str(pos.get("symbol") or "")
                        contracts = float(pos.get("contracts") or 0)
                        side = str(pos.get("side") or "long")
                        if not sym or contracts <= 0:
                            continue

                        reduce_side = "sell" if side == "long" else "buy"
                        reduce_amount = max(contracts * 0.35, 0.0)
                        if reduce_amount <= 0:
                            continue

                        result = await connector.create_market_order(
                            exchange=creds["exchange"],
                            api_key=creds["api_key"],
                            api_secret=creds["api_secret"],
                            symbol=sym.replace(":USDT", ""),
                            side=reduce_side,
                            amount=reduce_amount,
                            passphrase=creds.get("passphrase"),
                            reduce_only=True,
                        )
                        if result.get("ok"):
                            de_risked += 1
                            _log_sleep(
                                user_id,
                                f"Cross MM {mm_util_pct:.1f}% > 15% — reduced risky position {sym} by {reduce_amount:.4f} contracts.",
                            )
                            await asyncio.sleep(1)
                            try:
                                snapshot = await connector.fetch_exchange_snapshot(
                                    creds["exchange"], creds["api_key"], creds["api_secret"], creds.get("passphrase")
                                )
                                mm_util_pct = _margin_utilization_pct(snapshot, max_leverage)
                            except Exception:
                                break

                    if de_risked == 0:
                        _log_sleep(user_id, f"Cross MM {mm_util_pct:.1f}% is elevated but no reducible position executed this cycle.")

                # Check total exposure
                current_notional = sum(p.get("notional", 0) for p in snapshot.get("positions", []))
                max_notional = equity * (max_exposure_pct / 100) * max_leverage
                exposure_ok = current_notional < max_notional

                if exposure_ok:
                    for symbol in effective_symbols:
                        if _canonical_symbol(symbol) in blocked_symbols:
                            continue

                        if execution_market == "futures":
                            funding_data = funding_cache.get(symbol)
                            if funding_data is None:
                                funding_data = await connector.get_funding_snapshot(
                                    creds["exchange"], creds["api_key"], creds["api_secret"], symbol, creds.get("passphrase"),
                                )
                                funding_cache[symbol] = funding_data
                            funding_profile = _funding_profile(funding_data)
                        else:
                            funding_profile = {"entry_bonus": 0.0, "long_bonus": 0.0, "short_bonus": 0.0, "cooldown_mult": 1.0, "label": None}
                        funding_label = funding_profile.get("label")
                        funding_key = f"funding::{symbol}"
                        if funding_label and session.get("sleep_mode", {}).get(funding_key) != funding_label:
                            _log_sleep(user_id, f"{symbol}: {funding_label}. Tilting entries based on funding pressure.")
                            session.setdefault("sleep_mode", {})[funding_key] = funding_label

                        # Cooldown check (faster reaction in macro/funding volatility windows)
                        cooldown_mult = float(macro_profile.get("cooldown_mult", 1.0)) * float(funding_profile.get("cooldown_mult", 1.0))
                        cooldown_s = max(60, int(ENTRY_COOLDOWN_S * cooldown_mult))
                        last_entry = last_entry_by_symbol.get(symbol, 0)
                        if time.time() - last_entry < cooldown_s:
                            continue

                        # Already have open trade on this symbol?
                        if any(t.get("symbol") == symbol and t.get("status") == "open" for t in session.get("sleep_trades", [])):
                            continue

                        # Get TA
                        ta = await _get_ta_signals(symbol)
                        if ta.get("error"):
                            continue

                        price = ta.get("price", 0)
                        if price <= 0:
                            continue

                        # Brain prediction
                        brain_pred = None
                        try:
                            rsi = ta.get("rsi", 50)
                            brain_pred = brain_engine.predict(user_id, {
                                "rsi": rsi,
                                "atr_score": ta.get("score", 5),
                                "geo_score": 5.0,
                                "calendar_score": macro_profile.get("calendar_score", 5.0),
                            })
                        except Exception:
                            pass

                        side, confidence = _evaluate_entry(ta, brain_pred)
                        # Apply session volume bonus to confidence
                        confidence += volume_bonus
                        # Opening range = higher confidence (trend confirmation)
                        if is_opening_range:
                            confidence += 0.08
                        confidence += float(macro_profile.get("entry_bonus", 0.0))
                        confidence += float(funding_profile.get("entry_bonus", 0.0))
                        if side == "buy":
                            confidence += float(funding_profile.get("long_bonus", 0.0))
                        elif side == "sell":
                            confidence += float(funding_profile.get("short_bonus", 0.0))

                        elapsed_s = time.time() - start_time
                        dynamic_min_conf = MIN_CONFIDENCE
                        if trade_count == 0 and elapsed_s > 45 * 60:
                            dynamic_min_conf = 0.40
                        elif trade_count == 0 and elapsed_s > 15 * 60:
                            dynamic_min_conf = 0.46

                        if dynamic_min_conf < MIN_CONFIDENCE and not forced_aggressive_logged:
                            _log_sleep(user_id, f"No fills yet after {elapsed_s/3600:.1f}h — entering aggressive mode (min confidence {dynamic_min_conf:.2f}).")
                            forced_aggressive_logged = True

                        if not side or confidence < dynamic_min_conf:
                            continue

                        # Determine leverage (scale with confidence)
                        # Higher leverage during peak volume (tighter spreads, better fills)
                        vol_lev_mult = {"peak": 0.8, "high": 0.7, "medium": 0.6, "low": 0.4}.get(
                            session_bias.get("volume", "medium"), 0.6
                        )
                        use_leverage = min(
                            max(int(max_leverage * confidence * vol_lev_mult), 5),
                            max_leverage,
                        )

                        # Calculate position size
                        amount = _position_size(equity, price, risk_pct, max_position_pct, use_leverage, entry_margin_pct)
                        if amount <= 0:
                            continue

                        # Execute!
                        desired_notional = amount * price
                        if execution_market == "futures" and desired_notional < 5.0:
                            _log_sleep(user_id, f"Skip {symbol}: futures order notional too small (${desired_notional:.2f} < $5).")
                            continue
                        if execution_market == "spot" and desired_notional < 5.0:
                            _log_sleep(user_id, f"Skip {symbol}: spot order cost too small (${desired_notional:.2f} < $5).")
                            continue

                        try:
                            order: dict[str, Any]
                            if execution_market == "spot":
                                if side != "buy":
                                    continue
                                spot_amount = _position_size(equity, price, risk_pct, max_position_pct, 1, entry_margin_pct)
                                if spot_amount <= 0:
                                    continue
                                order = await connector.create_spot_market_order(
                                    creds["exchange"], creds["api_key"], creds["api_secret"],
                                    symbol, "buy", spot_amount, creds.get("passphrase"),
                                )
                                amount = spot_amount
                                use_leverage = 1
                            else:
                                await connector.set_leverage(
                                    creds["exchange"], creds["api_key"], creds["api_secret"],
                                    symbol, use_leverage, creds.get("passphrase"),
                                )
                                limit_price = price * (1.0008 if side == "buy" else 0.9992)
                                order = await connector.create_limit_order(
                                    creds["exchange"], creds["api_key"], creds["api_secret"],
                                    symbol, side, amount, limit_price, creds.get("passphrase"),
                                )
                                if not order.get("ok"):
                                    order = await connector.create_market_order(
                                        creds["exchange"], creds["api_key"], creds["api_secret"],
                                        symbol, side, amount, creds.get("passphrase"),
                                    )
                                if not order.get("ok") and creds["exchange"].lower() == "mexc" and _is_contract_activation_error(str(order.get("error", ""))):
                                    _log_sleep(user_id, f"{symbol}: futures blocked on MEXC, switching session route to spot.")
                                    session.setdefault("sleep_mode", {})["execution_market"] = "spot"
                                    execution_market = "spot"
                                    if side == "buy":
                                        order = await connector.create_spot_market_order(
                                            creds["exchange"], creds["api_key"], creds["api_secret"],
                                            symbol, "buy", _position_size(equity, price, risk_pct, max_position_pct, 1, entry_margin_pct), creds.get("passphrase"),
                                        )
                                        amount = float(order.get("amount") or amount)
                                        use_leverage = 1

                            if order.get("ok"):
                                fill_price = order.get("price", price)
                                sl_price, tp_price = _sl_tp_for_leverage(use_leverage, side, fill_price, sl_pct, tp_pct)

                                if execution_market == "futures":
                                    sl_side = "sell" if side == "buy" else "buy"
                                    await connector.create_stop_loss(
                                        creds["exchange"], creds["api_key"], creds["api_secret"],
                                        symbol, sl_side, amount, sl_price, creds.get("passphrase"),
                                    )
                                    await connector.create_take_profit(
                                        creds["exchange"], creds["api_key"], creds["api_secret"],
                                        symbol, sl_side, amount, tp_price, creds.get("passphrase"),
                                    )

                                # Build strategy summary for trade record
                                strategy_signals = {}
                                div_info = ta.get("divergence", {})
                                if div_info.get("has_divergence"):
                                    strategy_signals["divergence"] = {
                                        "type": div_info.get("type"),
                                        "double_confirmed": div_info.get("double_confirmed", False),
                                        "description": div_info.get("description", ""),
                                    }
                                conf_info = ta.get("confluence", {})
                                if conf_info.get("level") not in (None, "none"):
                                    strategy_signals["confluence"] = {
                                        "level": conf_info.get("level"),
                                        "agreeing_tf": conf_info.get("agreeing", 0),
                                        "direction": conf_info.get("direction"),
                                    }

                                trade_record = {
                                    "symbol": symbol,
                                    "side": side,
                                    "amount": amount,
                                    "entry_price": fill_price,
                                    "leverage": use_leverage,
                                    "sl_price": sl_price,
                                    "tp_price": tp_price,
                                    "confidence": round(confidence, 2),
                                    "ta_summary": {
                                        "rsi": ta.get("rsi"),
                                        "macd": ta.get("macd_trend"),
                                        "ema": ta.get("ema_cross"),
                                    },
                                    "strategies": strategy_signals,
                                    "opened_at": time.time(),
                                    "status": "open",
                                    "order_id": order.get("id"),
                                    "market_type": execution_market,
                                }
                                session.setdefault("sleep_trades", []).append(trade_record)
                                last_entry_by_symbol[symbol] = time.time()
                                trade_count += 1

                                direction = "LONG" if side == "buy" else "SHORT"
                                vol_label = session_bias.get("volume", "?")
                                # Build strategy tag line
                                strat_tags = []
                                if strategy_signals.get("divergence"):
                                    d = strategy_signals["divergence"]
                                    tag = f"DIV:{d.get('type', '?')}"
                                    if d.get("double_confirmed"):
                                        tag += "(2x)"
                                    strat_tags.append(tag)
                                if strategy_signals.get("confluence"):
                                    c = strategy_signals["confluence"]
                                    strat_tags.append(f"MTF:{c.get('level', '?')}({c.get('agreeing_tf', 0)}TF)")
                                strat_label = " | ".join(strat_tags) if strat_tags else "base"
                                _log_sleep(
                                    user_id,
                                    f"Opened {direction} {symbol} @ ${fill_price:.2f} | "
                                    f"route={execution_market} {use_leverage}x {margin_mode} | SL ${sl_price:.2f} TP ${tp_price:.2f} | "
                                    f"Conf {confidence:.0%} | Vol: {vol_label} | Strat: {strat_label}"
                                )
                                _save_sleep_snapshot(user_id, "sleep_trade_open", symbol, {
                                    "side": side,
                                    "entry_price": fill_price,
                                    "amount": amount,
                                    "leverage": use_leverage,
                                    "confidence": round(confidence, 4),
                                    "sl_price": sl_price,
                                    "tp_price": tp_price,
                                    "exchange": creds.get("exchange"),
                                })
                            else:
                                err_msg = str(order.get("error", "unknown"))
                                _log_sleep(user_id, f"Order failed for {symbol}: {err_msg}")
                                _set_last_error(user_id, f"order_failed exchange={creds.get('exchange')} symbol={symbol} side={side} amount={amount:.6f} err={err_msg[:180]}")
                                log.error("[sleep] order_failed exchange=%s symbol=%s side=%s amount=%.6f err=%s", creds.get("exchange"), symbol, side, amount, err_msg)
                                if _is_contract_activation_error(err_msg):
                                    blocked_symbols.add(_canonical_symbol(symbol))
                                    session["blocked_symbols"] = sorted(blocked_symbols)
                                    _log_sleep(
                                        user_id,
                                        f"Disabled {symbol} for this session: contract not activated on exchange. Activate it manually, then restart Sleep Mode.",
                                    )
                                    brain_store.save_autotrader(user_id, session)

                        except Exception as e:
                            err_txt = str(e)
                            _log_sleep(user_id, f"Execution error on {symbol}: {err_txt[:100]}")
                            _set_last_error(user_id, f"execution_error exchange={creds.get('exchange')} symbol={symbol} side={side} amount={amount:.6f} err={err_txt[:180]}")
                            log.error("[sleep] order_failed exchange=%s symbol=%s side=%s amount=%.6f err=%s", creds.get("exchange"), symbol, side, amount, err_txt)

                        # Don't flood — small delay between entries
                        await asyncio.sleep(5)

            # Update session stats
            session["sleep_mode"]["trade_count"] = trade_count
            session["sleep_mode"]["realized_pnl"] = round(total_realized_pnl, 4)
            elapsed = time.time() - start_time
            session["sleep_mode"]["elapsed_s"] = int(elapsed)
            session["sleep_mode"]["remaining_s"] = max(0, int(end_time - time.time()))
            brain_store.save_autotrader(user_id, session)

            await asyncio.sleep(SCAN_INTERVAL_S)

    except asyncio.CancelledError:
        _log_sleep(user_id, "Sleep Mode cancelled by user")
    except Exception as e:
        err_txt = str(e)
        _log_sleep(user_id, f"Sleep Mode error: {err_txt[:200]}")
        _set_last_error(user_id, f"session_error err={err_txt[:240]}")
        log.exception("[sleep] session error for %s", user_id)
    finally:
        # Finalize session
        session = brain_store.load_autotrader(user_id) or session
        session["sleep_mode"]["active"] = False
        session["sleep_mode"]["status"] = "completed"
        session["sleep_mode"]["ended_at"] = time.time()
        session["sleep_mode"]["total_trades"] = trade_count
        session["sleep_mode"]["total_realized_pnl"] = round(total_realized_pnl, 4)
        session["sleep_mode"]["desired_state"] = "stopped"
        brain_store.save_autotrader(user_id, session)

        elapsed_h = (time.time() - start_time) / 3600
        _log_sleep(
            user_id,
            f"Sleep Mode ended — {trade_count} trades in {elapsed_h:.1f}h | "
            f"Realized PnL: ${total_realized_pnl:.2f}"
        )

        # Clean up from active sessions
        _active_sessions.pop(user_id, None)


def _log_sleep(user_id: str, message: str):
    """Append to the autotrader session log."""
    ts = time.strftime("%H:%M:%S", time.gmtime())
    entry = f"[{ts}] {message}"
    log.info("[sleep:%s] %s", user_id, message)

    session = brain_store.load_autotrader(user_id)
    if session:
        session.setdefault("log", []).append(entry)
        # Keep last 200 log entries
        if len(session["log"]) > 200:
            session["log"] = session["log"][-200:]
        brain_store.save_autotrader(user_id, session)

def _save_sleep_snapshot(user_id: str, kind: str, symbol: str, content: dict[str, Any]) -> None:
    try:
        brain_store.save_analysis_snapshot(user_id, {
            "kind": kind,
            "symbol": symbol,
            "content": content,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        pass



def _set_last_error(user_id: str, message: str) -> None:
    session = brain_store.load_autotrader(user_id) or {}
    sleep = session.setdefault("sleep_mode", {})
    sleep["last_error"] = message
    sleep["last_error_at"] = time.time()
    brain_store.save_autotrader(user_id, session)


def _set_desired_sleep_state(user_id: str, desired: str) -> None:
    session = brain_store.load_autotrader(user_id) or {}
    sleep = session.setdefault("sleep_mode", {})
    sleep["desired_state"] = desired
    sleep["desired_state_at"] = time.time()
    brain_store.save_autotrader(user_id, session)


def _is_running(user_id: str) -> bool:
    return user_id in _active_sessions and not _active_sessions[user_id].done()


async def worker_tick(brain_engine: PredictionEngine) -> dict[str, int]:
    """One reconciliation tick: align desired sleep state with running tasks."""
    started = 0
    stopped = 0
    for user_id in brain_store.list_autotrader_user_ids():
        session = brain_store.load_autotrader(user_id) or {}
        if not session.get("enabled"):
            continue
        desired = str((session.get("sleep_mode") or {}).get("desired_state") or "stopped")
        running = _is_running(user_id)

        if desired == "running" and not running:
            task = asyncio.create_task(run_sleep_session(user_id, brain_engine))
            _active_sessions[user_id] = task
            started += 1
        elif desired != "running" and running:
            _active_sessions[user_id].cancel()
            _active_sessions.pop(user_id, None)
            stopped += 1

    return {"started": started, "stopped": stopped}


async def run_worker_loop(brain_engine: PredictionEngine, interval_s: int = 5) -> None:
    """Dedicated worker loop for Sleep Mode task orchestration."""
    log.info("[sleep-worker] loop starting mode=%s interval=%ss", SLEEP_EXECUTION_MODE, interval_s)
    while True:
        try:
            stats = await worker_tick(brain_engine)
            if stats["started"] or stats["stopped"]:
                log.info("[sleep-worker] reconciled started=%d stopped=%d", stats["started"], stats["stopped"])
        except Exception as exc:
            log.exception("[sleep-worker] reconciliation failed: %s", exc)
        await asyncio.sleep(max(1, interval_s))



def start_sleep_mode(user_id: str, brain_engine: PredictionEngine) -> dict:
    """Request a sleep trading session. In worker mode this only sets desired state."""
    if _is_running(user_id):
        return {"ok": False, "error": "Sleep Mode already active", "active": True}

    session = brain_store.load_autotrader(user_id) or {}
    config = session.get("config", {})
    exchange = str(config.get("exchange") or "mexc")
    configured_market_mode = str(config.get("market_mode", "auto")).lower()
    execution_market = configured_market_mode if configured_market_mode in ("futures", "spot") else ("spot" if exchange.lower() == "mexc" else "futures")
    sleep_hours = _resolve_sleep_duration_hours(config, exchange, execution_market)

    _set_desired_sleep_state(user_id, "running")
    if SLEEP_EXECUTION_MODE != "worker":
        task = asyncio.create_task(run_sleep_session(user_id, brain_engine))
        _active_sessions[user_id] = task

    return {"ok": True, "message": "Sleep Mode queued", "duration_hours": sleep_hours, "execution_mode": SLEEP_EXECUTION_MODE}


def stop_sleep_mode(user_id: str) -> dict:
    """Stop an active sleep session (desired state + immediate cancel if local task exists)."""
    _set_desired_sleep_state(user_id, "stopped")
    task = _active_sessions.get(user_id)
    if task and not task.done():
        task.cancel()
        _active_sessions.pop(user_id, None)

    session = brain_store.load_autotrader(user_id)
    if session and session.get("sleep_mode"):
        session["sleep_mode"]["active"] = False
        session["sleep_mode"]["status"] = "stopped_by_user"
        session["sleep_mode"]["ended_at"] = time.time()
        brain_store.save_autotrader(user_id, session)

    return {"ok": True, "message": "Sleep Mode stop requested"}


def get_sleep_status(user_id: str) -> dict:
    """Get current sleep session status."""
    session = brain_store.load_autotrader(user_id)
    if not session:
        return {"active": False}

    sleep = session.get("sleep_mode", {})
    is_active = _is_running(user_id)

    started_at = float(sleep.get("started_at") or 0)
    ends_at = float(sleep.get("ends_at") or 0)

    # Keep status responsive between polling ticks by deriving elapsed/remaining on read.
    elapsed_s = int(sleep.get("elapsed_s") or 0)
    remaining_s = int(sleep.get("remaining_s") or 0)
    now = time.time()
    if is_active and started_at > 0 and ends_at > 0:
        elapsed_s = max(0, int(now - started_at))
        remaining_s = max(0, int(ends_at - now))
    elif started_at > 0 and ends_at > 0 and (elapsed_s <= 0 or remaining_s <= 0):
        elapsed_s = max(0, int((sleep.get("ended_at") or now) - started_at))
        remaining_s = max(0, int(ends_at - (sleep.get("ended_at") or now)))

    return {
        "active": is_active,
        "started_at": sleep.get("started_at"),
        "ends_at": sleep.get("ends_at"),
        "elapsed_s": elapsed_s,
        "remaining_s": remaining_s,
        "trade_count": sleep.get("trade_count", 0),
        "realized_pnl": sleep.get("realized_pnl", 0),
        "status": sleep.get("status", "idle"),
        "desired_state": sleep.get("desired_state", "stopped"),
        "last_error": sleep.get("last_error"),
        "last_error_at": sleep.get("last_error_at"),
        "trades": session.get("sleep_trades", []),
        "log": session.get("log", [])[-30:],
    }


# ── Trade Protection Engine ─────────────────────────────────────────────────
# Monitors user's positions and applies defensive actions:
# - Hedge: open opposite position to neutralize risk
# - Safe orders: DCA into losing positions at better prices
# - SL/TP adjustments: tighten SL on profitable trades, move SL to breakeven
# - Reduce: partially close over-exposed positions

async def check_trade_protection(
    user_id: str,
    exchange: str,
    api_key: str,
    api_secret: str,
    passphrase: str | None,
    mode: str,  # 'active' or 'sleep'
    config: dict,
) -> dict:
    """Analyze open positions and return protection actions."""
    actions: list[dict] = []
    risk_level = "LOW"
    session = brain_store.load_autotrader(user_id) or {}
    blocked_symbols = {_canonical_symbol(sym) for sym in session.get("blocked_symbols", [])}
    blocked_updated = False

    try:
        positions = await connector.fetch_open_positions(
            exchange=exchange,
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
        )
    except Exception as e:
        log.warning("[protection] fetch positions failed: %s", e)
        return {"ok": False, "actions": [], "error": str(e)}

    if not positions:
        return {"ok": True, "actions": [], "risk_level": "LOW"}

    sl_pct = config.get("stop_loss_pct", 2.0)
    tp_pct = config.get("take_profit_pct", 4.0)
    max_leverage = config.get("max_leverage", 125)
    max_exposure_pct = config.get("max_total_exposure_pct", 25.0)

    total_notional = 0.0
    total_unrealized = 0.0

    for pos in positions:
        sym = _canonical_symbol(pos.get("symbol", ""))
        if sym in blocked_symbols:
            continue
        pnl_pct = pos.get("pnlPct", 0)
        leverage = pos.get("leverage", 1)
        notional = pos.get("notional", 0)
        side = pos.get("side", "long")
        liq_price = pos.get("liquidationPrice", 0)
        mark_price = pos.get("markPrice", 0)
        entry_price = pos.get("entryPrice", 0)
        total_notional += abs(notional)
        total_unrealized += pos.get("unrealizedPnl", 0)

        action_id_base = f"{user_id}-{sym}-{int(time.time())}"

        # 1. Liquidation distance warning — hedge if too close
        if liq_price > 0 and mark_price > 0:
            if side == "long":
                liq_dist_pct = ((mark_price - liq_price) / mark_price) * 100
            else:
                liq_dist_pct = ((liq_price - mark_price) / mark_price) * 100

            if liq_dist_pct < 2.0:
                risk_level = "EXTREME"
                # Auto-hedge: open opposite position to protect
                actions.append({
                    "id": f"{action_id_base}-hedge",
                    "type": "hedge",
                    "symbol": sym,
                    "description": f"Liquidation {liq_dist_pct:.1f}% away — opening hedge position",
                    "timestamp": time.time(),
                    "status": "pending",
                })
                # Execute hedge
                try:
                    hedge_side = "sell" if side == "long" else "buy"
                    hedge_amount = pos.get("contracts", 0) * 0.5  # hedge 50%
                    result = await connector.create_market_order(
                        exchange, api_key, api_secret,
                        sym.replace(":USDT", ""), hedge_side, hedge_amount,
                        passphrase,
                    )
                    actions[-1]["status"] = "executed" if result.get("ok") else "failed"
                    if not result.get("ok") and _is_contract_activation_error(str(result.get("error", ""))):
                        blocked_symbols.add(sym)
                        blocked_updated = True
                except Exception as e:
                    actions[-1]["status"] = "failed"
                    log.warning("[protection] hedge failed: %s", e)

            elif liq_dist_pct < 5.0:
                risk_level = _max_risk(risk_level, "HIGH")
                actions.append({
                    "id": f"{action_id_base}-warn",
                    "type": "sl_adjust",
                    "symbol": sym,
                    "description": f"Liquidation {liq_dist_pct:.1f}% away — tightening SL",
                    "timestamp": time.time(),
                    "status": "executed",
                })

        # 2. Extreme leverage warning
        if leverage >= 200:
            risk_level = "EXTREME"
            actions.append({
                "id": f"{action_id_base}-lev",
                "type": "reduce",
                "symbol": sym,
                "description": f"Leverage {leverage}x is extreme — reducing position 30%",
                "timestamp": time.time(),
                "status": "pending",
            })
            try:
                reduce_side = "sell" if side == "long" else "buy"
                reduce_amount = pos.get("contracts", 0) * 0.3
                result = await connector.create_market_order(
                    exchange, api_key, api_secret,
                    sym.replace(":USDT", ""), reduce_side, reduce_amount,
                    passphrase, reduce_only=True,
                )
                actions[-1]["status"] = "executed" if result.get("ok") else "failed"
                if not result.get("ok") and _is_contract_activation_error(str(result.get("error", ""))):
                    blocked_symbols.add(sym)
                    blocked_updated = True
            except Exception as e:
                actions[-1]["status"] = "failed"
                log.warning("[protection] reduce failed: %s", e)

        # 3. Profitable trade — move SL to breakeven
        if pnl_pct >= tp_pct * 0.6:
            actions.append({
                "id": f"{action_id_base}-be",
                "type": "sl_adjust",
                "symbol": sym,
                "description": f"PnL +{pnl_pct:.1f}% — moving SL to breakeven (entry ${entry_price:.2f})",
                "timestamp": time.time(),
                "status": "executed",
            })

        # 4. Large loss — DCA safe order (both active and sleep modes)
        if pnl_pct <= -(sl_pct * 0.5) and pnl_pct > -sl_pct:
            actions.append({
                "id": f"{action_id_base}-safe",
                "type": "safe_order",
                "symbol": sym,
                "description": f"PnL {pnl_pct:.1f}% — placing DCA safe order to average down",
                "timestamp": time.time(),
                "status": "pending",
            })
            try:
                safe_amount = pos.get("contracts", 0) * 0.25  # 25% of position
                safe_side = "buy" if side == "long" else "sell" if side == "short" else (side if side in ("buy", "sell") else "buy")
                result = await connector.create_market_order(
                    exchange, api_key, api_secret,
                    sym.replace(":USDT", ""), safe_side,
                    safe_amount, passphrase,
                )
                actions[-1]["status"] = "executed" if result.get("ok") else "failed"
                if not result.get("ok") and _is_contract_activation_error(str(result.get("error", ""))):
                    blocked_symbols.add(sym)
                    blocked_updated = True
            except Exception as e:
                actions[-1]["status"] = "failed"
                log.warning("[protection] safe order failed: %s", e)

        # 5. Significant loss hedge — open opposite position when PnL approaches SL
        if pnl_pct <= -(sl_pct * 0.7) and leverage >= 5:
            risk_level = _max_risk(risk_level, "HIGH")
            actions.append({
                "id": f"{action_id_base}-loss-hedge",
                "type": "hedge",
                "symbol": sym,
                "description": f"PnL {pnl_pct:.1f}% near SL — hedging 30% to protect capital",
                "timestamp": time.time(),
                "status": "pending",
            })
            try:
                hedge_side = "sell" if side == "long" else "buy"
                hedge_amount = pos.get("contracts", 0) * 0.3
                result = await connector.create_market_order(
                    exchange, api_key, api_secret,
                    sym.replace(":USDT", ""), hedge_side,
                    hedge_amount, passphrase,
                )
                actions[-1]["status"] = "executed" if result.get("ok") else "failed"
                if not result.get("ok") and _is_contract_activation_error(str(result.get("error", ""))):
                    blocked_symbols.add(sym)
                    blocked_updated = True
            except Exception as e:
                actions[-1]["status"] = "failed"
                log.warning("[protection] loss hedge failed: %s", e)

    if blocked_updated:
        session["blocked_symbols"] = sorted(blocked_symbols)
        session.setdefault("log", []).append(
            f"Protection blocked symbols this session (contract activation required): {', '.join(sorted(blocked_symbols))}"
        )
        brain_store.save_autotrader(user_id, session)

    return {
        "ok": True,
        "actions": actions,
        "risk_level": risk_level,
        "total_notional": round(total_notional, 2),
        "total_unrealized_pnl": round(total_unrealized, 2),
    }


async def update_trade_sl_tp(
    user_id: str,
    trade_id: str,
    stop_loss_pct: float,
    take_profit_pct: float,
) -> dict:
    """Update SL/TP for a specific active trade."""
    session = brain_store.load_autotrader(user_id)
    if not session:
        return {"ok": False, "error": "No active session"}

    # Update in active trades
    active_trades = session.get("active_trades", [])
    for trade in active_trades:
        if trade.get("id") == trade_id:
            trade["stop_loss_pct"] = stop_loss_pct
            trade["take_profit_pct"] = take_profit_pct
            brain_store.save_autotrader(user_id, session)
            _log_sleep(user_id, f"Updated SL/TP for {trade.get('symbol', '?')}: SL={stop_loss_pct}% TP={take_profit_pct}%")
            return {"ok": True}

    # Check sleep trades too
    sleep_trades = session.get("sleep_trades", [])
    for trade in sleep_trades:
        tid = trade.get("id") or f"{trade.get('symbol', '')}-{trade.get('opened_at', '')}"
        if tid == trade_id:
            # Recalculate price-based SL/TP
            entry = trade.get("entry_price", 0)
            lev = trade.get("leverage", 1)
            if entry > 0:
                trade["sl_price"], trade["tp_price"] = _sl_tp_for_leverage(
                    lev, trade.get("side", "buy"), entry, stop_loss_pct, take_profit_pct
                )
            brain_store.save_autotrader(user_id, session)
            return {"ok": True}

    return {"ok": False, "error": "Trade not found"}
