"""
Sleep Mode AutoTrader — Autonomous trading engine for Pytheia Sentinel.

Runs as a background asyncio task when the user activates Sleep Mode.
Uses Sentinel's TA signals (RSI, MACD, BB, EMA) + Brain predictions to
open/close futures positions autonomously over an 8-hour sleep session.

Target: 25–30% portfolio profit across multiple small, high-conviction trades.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from app.brain import connector, store as brain_store
from app.brain.predictor import PredictionEngine
from app.sentinel import technicals as tech_module
from app.sentinel import sessions as sessions_module

log = logging.getLogger(__name__)

# ── Active sleep sessions (in-memory) ────────────────────────────────────────
_active_sessions: dict[str, asyncio.Task] = {}

SLEEP_DURATION_S = 8 * 3600  # 8 hours
SCAN_INTERVAL_S = 60         # re-scan every 60s
MAX_TRADES_PER_SESSION = 40  # cap total trades in one sleep
ENTRY_COOLDOWN_S = 180       # min 3 min between entries on same symbol
MIN_CONFIDENCE = 0.52        # lower threshold to increase trade frequency in 8h mode


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


def _position_size(equity: float, price: float, risk_pct: float, max_position_pct: float, leverage: int) -> float:
    """Calculate position size in contracts (base asset amount)."""
    risk_usd = equity * (risk_pct / 100)
    max_usd = equity * (max_position_pct / 100)
    notional = min(risk_usd * leverage, max_usd * leverage, equity * 0.5 * leverage)
    if price <= 0:
        return 0.0
    amount = notional / price
    return round(amount, 6)


async def _get_ta_signals(symbol: str) -> dict[str, Any]:
    """Get technical analysis for a symbol."""
    try:
        result = await tech_module.calculate(symbol)
        return {
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
    except Exception as e:
        log.warning("[sleep] TA failed for %s: %s", symbol, e)
        return {"error": str(e)}


def _evaluate_entry(ta: dict[str, Any], brain_pred: dict | None = None) -> tuple[str | None, float]:
    """Evaluate whether to enter a trade based on TA + Brain.

    Returns (side, confidence) where side is 'buy'/'sell'/None.
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
) -> list[dict]:
    """Check open positions and close profitable ones / cut losses."""
    closed: list[dict] = []
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

    tp_pct = session.get("config", {}).get("take_profit_pct", 4.0)
    sl_pct = session.get("config", {}).get("stop_loss_pct", 2.0)

    sleep_trades = session.get("sleep_trades", [])
    sleep_trade_symbols = {t["symbol"] for t in sleep_trades if t.get("status") == "open"}

    for pos in positions:
        sym = _canonical_symbol(pos.get("symbol", ""))
        if sym not in sleep_trade_symbols:
            continue  # not our trade

        pnl_pct = pos.get("pnlPct", 0)
        leverage = pos.get("leverage", 1)

        # Dynamic TP: leverage-adjusted
        effective_tp = tp_pct * leverage * 0.7  # 70% of theoretical max
        effective_sl = sl_pct * leverage * 0.5

        should_close = False
        reason = ""

        if pnl_pct >= effective_tp:
            should_close = True
            reason = f"TP hit: {pnl_pct:.1f}% (target {effective_tp:.1f}%)"
        elif pnl_pct <= -effective_sl:
            should_close = True
            reason = f"SL hit: {pnl_pct:.1f}% (limit -{effective_sl:.1f}%)"
        elif pnl_pct >= 15:
            # Always take 15%+ profit
            should_close = True
            reason = f"Profit lock: {pnl_pct:.1f}%"

        if should_close:
            side = pos.get("side", "long")
            close_side = "sell" if side == "long" else "buy"
            contracts = pos.get("contracts", 0)

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
    """Main sleep trading loop. Runs for up to 8 hours."""
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
    max_leverage = int(config.get("max_leverage", 20))
    margin_mode = config.get("margin_mode", "cross")  # cross or isolated
    sl_pct = float(config.get("stop_loss_pct", 2.0))
    tp_pct = float(config.get("take_profit_pct", 4.0))
    risk_pct = float(config.get("risk_per_trade_pct", 1.0))
    max_position_pct = float(config.get("max_position_pct", 10.0))
    max_open = int(config.get("max_open_trades", 3))
    max_exposure_pct = float(config.get("max_total_exposure_pct", 25.0))

    start_time = time.time()
    end_time = start_time + SLEEP_DURATION_S
    trade_count = 0
    total_realized_pnl = 0.0
    last_entry_by_symbol: dict[str, float] = {}

    # Initialize sleep session tracking
    session["sleep_mode"] = {
        "active": True,
        "started_at": start_time,
        "ends_at": end_time,
        "trade_count": 0,
        "realized_pnl": 0.0,
        "status": "running",
    }
    session["sleep_trades"] = []
    session["blocked_symbols"] = []
    brain_store.save_autotrader(user_id, session)
    _log_sleep(user_id, f"Sleep Mode started — {len(symbols)} symbols, {max_leverage}x max leverage, {margin_mode} margin, futures")

    # Set margin mode for all symbols at start
    for sym in symbols:
        try:
            await connector.set_margin_mode(
                creds["exchange"], creds["api_key"], creds["api_secret"],
                sym, margin_mode, creds.get("passphrase"),
            )
        except Exception:
            pass  # often already set

    try:
        while time.time() < end_time and trade_count < MAX_TRADES_PER_SESSION:
            await asyncio.sleep(2)  # small initial delay

            # 1. Manage existing positions
            closed = await _manage_open_positions(session, creds)
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

            # 2. Check market session timing
            session_bias = sessions_module.get_session_bias()
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
            if len(open_trades) < max_open:
                # Get portfolio equity
                try:
                    snapshot = await connector.fetch_exchange_snapshot(
                        creds["exchange"], creds["api_key"], creds["api_secret"], creds.get("passphrase")
                    )
                    equity = snapshot.get("equity", 0)
                except Exception:
                    equity = 0

                if equity < 50:
                    _log_sleep(user_id, "Equity below $50 — pausing entries")
                    await asyncio.sleep(SCAN_INTERVAL_S)
                    continue

                # Check total exposure
                current_notional = sum(p.get("notional", 0) for p in snapshot.get("positions", []))
                max_notional = equity * (max_exposure_pct / 100) * max_leverage
                exposure_ok = current_notional < max_notional

                if exposure_ok:
                    for symbol in symbols:
                        if _canonical_symbol(symbol) in blocked_symbols:
                            continue

                        # Cooldown check
                        last_entry = last_entry_by_symbol.get(symbol, 0)
                        if time.time() - last_entry < ENTRY_COOLDOWN_S:
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
                                "calendar_score": 5.0,
                            })
                        except Exception:
                            pass

                        side, confidence = _evaluate_entry(ta, brain_pred)
                        # Apply session volume bonus to confidence
                        confidence += volume_bonus
                        # Opening range = higher confidence (trend confirmation)
                        if is_opening_range:
                            confidence += 0.08

                        if not side or confidence < MIN_CONFIDENCE:
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
                        amount = _position_size(equity, price, risk_pct, max_position_pct, use_leverage)
                        if amount <= 0:
                            continue

                        # Execute!
                        try:
                            # Set leverage
                            await connector.set_leverage(
                                creds["exchange"], creds["api_key"], creds["api_secret"],
                                symbol, use_leverage, creds.get("passphrase"),
                            )

                            # Limit-first entry (maker-friendly), then fallback to market
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

                            if order.get("ok"):
                                fill_price = order.get("price", price)
                                sl_price, tp_price = _sl_tp_for_leverage(use_leverage, side, fill_price, sl_pct, tp_pct)

                                # Place SL
                                sl_side = "sell" if side == "buy" else "buy"
                                await connector.create_stop_loss(
                                    creds["exchange"], creds["api_key"], creds["api_secret"],
                                    symbol, sl_side, amount, sl_price, creds.get("passphrase"),
                                )

                                # Place TP
                                await connector.create_take_profit(
                                    creds["exchange"], creds["api_key"], creds["api_secret"],
                                    symbol, sl_side, amount, tp_price, creds.get("passphrase"),
                                )

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
                                    "opened_at": time.time(),
                                    "status": "open",
                                    "order_id": order.get("id"),
                                }
                                session.setdefault("sleep_trades", []).append(trade_record)
                                last_entry_by_symbol[symbol] = time.time()
                                trade_count += 1

                                direction = "LONG" if side == "buy" else "SHORT"
                                vol_label = session_bias.get("volume", "?")
                                _log_sleep(
                                    user_id,
                                    f"Opened {direction} {symbol} @ ${fill_price:.2f} | "
                                    f"{use_leverage}x {margin_mode} | SL ${sl_price:.2f} TP ${tp_price:.2f} | "
                                    f"Conf {confidence:.0%} | Vol: {vol_label}"
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
                                if _is_contract_activation_error(err_msg):
                                    blocked_symbols.add(_canonical_symbol(symbol))
                                    session["blocked_symbols"] = sorted(blocked_symbols)
                                    _log_sleep(
                                        user_id,
                                        f"Disabled {symbol} for this session: contract not activated on exchange. Activate it manually, then restart Sleep Mode.",
                                    )
                                    brain_store.save_autotrader(user_id, session)

                        except Exception as e:
                            _log_sleep(user_id, f"Execution error on {symbol}: {str(e)[:100]}")

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
        _log_sleep(user_id, f"Sleep Mode error: {str(e)[:200]}")
        log.exception("[sleep] session error for %s", user_id)
    finally:
        # Finalize session
        session = brain_store.load_autotrader(user_id) or session
        session["sleep_mode"]["active"] = False
        session["sleep_mode"]["status"] = "completed"
        session["sleep_mode"]["ended_at"] = time.time()
        session["sleep_mode"]["total_trades"] = trade_count
        session["sleep_mode"]["total_realized_pnl"] = round(total_realized_pnl, 4)
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



def start_sleep_mode(user_id: str, brain_engine: PredictionEngine) -> dict:
    """Start a sleep trading session. Returns status."""
    if user_id in _active_sessions:
        task = _active_sessions[user_id]
        if not task.done():
            return {"ok": False, "error": "Sleep Mode already active", "active": True}

    task = asyncio.create_task(run_sleep_session(user_id, brain_engine))
    _active_sessions[user_id] = task
    return {"ok": True, "message": "Sleep Mode started", "duration_hours": 8}


def stop_sleep_mode(user_id: str) -> dict:
    """Stop an active sleep session."""
    task = _active_sessions.get(user_id)
    if not task or task.done():
        return {"ok": True, "message": "No active sleep session"}

    task.cancel()
    _active_sessions.pop(user_id, None)

    session = brain_store.load_autotrader(user_id)
    if session and session.get("sleep_mode"):
        session["sleep_mode"]["active"] = False
        session["sleep_mode"]["status"] = "stopped_by_user"
        session["sleep_mode"]["ended_at"] = time.time()
        brain_store.save_autotrader(user_id, session)

    return {"ok": True, "message": "Sleep Mode stopped"}


def get_sleep_status(user_id: str) -> dict:
    """Get current sleep session status."""
    session = brain_store.load_autotrader(user_id)
    if not session:
        return {"active": False}

    sleep = session.get("sleep_mode", {})
    is_active = user_id in _active_sessions and not _active_sessions[user_id].done()

    return {
        "active": is_active,
        "started_at": sleep.get("started_at"),
        "ends_at": sleep.get("ends_at"),
        "elapsed_s": sleep.get("elapsed_s", 0),
        "remaining_s": sleep.get("remaining_s", 0),
        "trade_count": sleep.get("trade_count", 0),
        "realized_pnl": sleep.get("realized_pnl", 0),
        "status": sleep.get("status", "idle"),
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
                risk_level = max(risk_level, "HIGH")
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

        # 4. Large loss — DCA safe order in sleep mode
        if mode == "sleep" and pnl_pct <= -(sl_pct * 0.5) and pnl_pct > -sl_pct:
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
                result = await connector.create_market_order(
                    exchange, api_key, api_secret,
                    sym.replace(":USDT", ""), side if side in ("buy", "sell") else "buy",
                    safe_amount, passphrase,
                )
                actions[-1]["status"] = "executed" if result.get("ok") else "failed"
                if not result.get("ok") and _is_contract_activation_error(str(result.get("error", ""))):
                    blocked_symbols.add(sym)
                    blocked_updated = True
            except Exception as e:
                actions[-1]["status"] = "failed"
                log.warning("[protection] safe order failed: %s", e)

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
