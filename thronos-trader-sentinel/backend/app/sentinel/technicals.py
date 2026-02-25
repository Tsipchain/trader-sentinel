"""
Technical Risk Module
---------------------
Fetches OHLCV candles from a CEX provider and calculates:
- RSI (14-period) — momentum / overbought-oversold
- ATR-based Volatility — normalised to 0-10
- Fibonacci Retracement levels — proximity to key levels
- 30-day Cycle deviation — distance from 30/60/90-day price midpoints

Score: 0.0 (calm/normal) → 10.0 (extreme — high RSI, high vol, at fib resistance)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import ccxt.async_support as ccxt


# ── Fibonacci retracement levels (standard: 0.236, 0.382, 0.5, 0.618, 0.786) ─
FIB_LEVELS = [0.0, 0.236, 0.382, 0.500, 0.618, 0.786, 1.0]

_CACHE: dict[str, tuple[float, "TechnicalResult"]] = {}
_CACHE_TTL = 120  # 2 minutes


@dataclass
class FibLevel:
    ratio: float
    price: float
    distance_pct: float   # % distance from current price


@dataclass
class TechnicalResult:
    score: float              # 0–10
    symbol: str
    current_price: float | None
    rsi_14: float | None      # 0–100
    rsi_signal: str           # "overbought" | "oversold" | "neutral"
    volatility_score: float   # 0–10  (ATR / price normalised)
    fib_levels: list[dict]    # nearest fib levels with prices
    nearest_fib: dict | None  # closest fib level
    cycle_deviation: float | None  # % from 30-day midpoint
    error: str | None = None


async def _fetch_ohlcv(symbol: str, exchange_id: str = "binance",
                       timeframe: str = "1d", limit: int = 60) -> list[list]:
    """Fetch daily OHLCV candles. Returns list of [ts, o, h, l, c, v]."""
    ex_class = getattr(ccxt, exchange_id, None)
    if ex_class is None:
        return []
    ex = ex_class({"enableRateLimit": True, "timeout": 15_000})
    try:
        candles = await ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        return candles or []
    except Exception:
        return []
    finally:
        try:
            await ex.close()
        except Exception:
            pass


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
    # Wilder smoothing
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def _atr(candles: list[list], period: int = 14) -> float | None:
    """Average True Range."""
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h = candles[i][2]
        l = candles[i][3]
        prev_c = candles[i - 1][4]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)
    if len(trs) < period:
        return None
    atr = sum(trs[-period:]) / period
    return atr


def _fibonacci_levels(high: float, low: float, current: float) -> tuple[list[dict], dict | None]:
    span = high - low
    if span <= 0:
        return [], None
    levels = []
    for ratio in FIB_LEVELS:
        price = low + span * ratio
        dist_pct = abs(current - price) / current * 100
        levels.append({
            "ratio": ratio,
            "price": round(price, 4),
            "distance_pct": round(dist_pct, 2),
        })
    nearest = min(levels, key=lambda x: x["distance_pct"])
    return levels, nearest


async def calculate(symbol: str = "BTC/USDT") -> TechnicalResult:
    cache_key = symbol
    if cache_key in _CACHE:
        ts, cached = _CACHE[cache_key]
        if time.time() - ts < _CACHE_TTL:
            return cached

    candles = await _fetch_ohlcv(symbol, limit=60)

    if not candles or len(candles) < 20:
        result = TechnicalResult(
            score=0.0, symbol=symbol, current_price=None,
            rsi_14=None, rsi_signal="unknown",
            volatility_score=0.0, fib_levels=[], nearest_fib=None,
            cycle_deviation=None, error="Insufficient candle data",
        )
        _CACHE[cache_key] = (time.time(), result)
        return result

    closes    = [c[4] for c in candles]
    highs     = [c[2] for c in candles]
    lows      = [c[3] for c in candles]
    current   = closes[-1]

    # ── RSI ──────────────────────────────────────────────────────────────────
    rsi = _rsi(closes)
    if rsi is None:
        rsi_signal = "unknown"
        rsi_score  = 0.0
    elif rsi >= 75:
        rsi_signal = "overbought"
        rsi_score  = min(10.0, (rsi - 50) / 5)
    elif rsi <= 25:
        rsi_signal = "oversold"
        rsi_score  = min(10.0, (50 - rsi) / 5)
    else:
        rsi_signal = "neutral"
        rsi_score  = abs(rsi - 50) / 10  # 0–5 in neutral zone

    # ── ATR Volatility ────────────────────────────────────────────────────────
    atr = _atr(candles)
    if atr and current > 0:
        atr_pct = atr / current * 100   # daily ATR as % of price
        # Normalise: 1% ATR = calm(2), 3% = moderate(5), 6%+ = extreme(10)
        volatility_score = min(10.0, atr_pct * 1.6)
        volatility_score = round(volatility_score, 2)
    else:
        volatility_score = 0.0

    # ── Fibonacci Levels (60-day range) ──────────────────────────────────────
    period_high = max(highs[-60:])
    period_low  = min(lows[-60:])
    fib_levels, nearest_fib = _fibonacci_levels(period_high, period_low, current)

    # Score: higher if very close to a key fib level (0.618 or 0.786 = resistance)
    fib_score = 0.0
    if nearest_fib:
        dist = nearest_fib["distance_pct"]
        ratio = nearest_fib["ratio"]
        # Key resistance levels: 0.618 and 0.786
        weight = 2.0 if ratio in (0.618, 0.786, 0.382) else 1.0
        if dist < 0.5:
            fib_score = 8.0 * weight / 2
        elif dist < 1.5:
            fib_score = 5.0 * weight / 2
        elif dist < 3.0:
            fib_score = 2.0 * weight / 2

    # ── 30-day Cycle Midpoint Deviation ───────────────────────────────────────
    if len(closes) >= 30:
        mid_30 = (max(closes[-30:]) + min(closes[-30:])) / 2
        deviation_pct = (current - mid_30) / mid_30 * 100
    else:
        deviation_pct = None

    # ── Composite Technical Score ─────────────────────────────────────────────
    score = round(min(10.0, (rsi_score * 0.45 + volatility_score * 0.35 + fib_score * 0.20)), 2)

    result = TechnicalResult(
        score=score,
        symbol=symbol,
        current_price=round(current, 4),
        rsi_14=rsi,
        rsi_signal=rsi_signal,
        volatility_score=volatility_score,
        fib_levels=fib_levels,
        nearest_fib=nearest_fib,
        cycle_deviation=round(deviation_pct, 2) if deviation_pct is not None else None,
    )
    _CACHE[cache_key] = (time.time(), result)
    return result
