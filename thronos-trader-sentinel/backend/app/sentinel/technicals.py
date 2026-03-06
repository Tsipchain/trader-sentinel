"""
Technical Risk Module
---------------------
Fetches OHLCV candles from a CEX provider and calculates:
- RSI (14-period)            — momentum / overbought-oversold
- ATR-based Volatility       — normalised to 0-10
- Fibonacci Retracement      — proximity to key levels
- 30-day Cycle deviation     — distance from 30-day price midpoint
- MACD (12/26/9)             — trend direction and momentum
- Bollinger Bands (20, 2σ)   — price position within recent range
- EMA 20 / EMA 50 crossover  — short vs medium-term trend
- Williams %R (14)           — momentum oscillator (-100 to 0)

Score: 0.0 (calm/normal) → 10.0 (extreme — high RSI, high vol, at fib resistance)
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

log = logging.getLogger(__name__)


_CCXT_MODULE = None


def _ccxt_module():
    global _CCXT_MODULE
    if _CCXT_MODULE is False:
        return None
    if _CCXT_MODULE is None:
        try:
            if importlib.util.find_spec("ccxt.async_support") is None:
                log.warning("ccxt.async_support unavailable; technical OHLCV will use HTTP fallback where possible")
                _CCXT_MODULE = False
                return None
            _CCXT_MODULE = importlib.import_module("ccxt.async_support")
        except ModuleNotFoundError as exc:
            log.warning("ccxt async_support disabled for technicals due to missing dependency: %s", exc)
            _CCXT_MODULE = False
            return None
        except Exception as exc:
            log.warning("ccxt async_support disabled for technicals due to import failure: %s", exc)
            _CCXT_MODULE = False
            return None
    return _CCXT_MODULE


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
    # RSI
    rsi_14: float | None      # 0–100
    rsi_signal: str           # "overbought" | "oversold" | "neutral"
    # Volatility
    volatility_score: float   # 0–10  (ATR / price normalised)
    # Fibonacci
    fib_levels: list[dict]
    nearest_fib: dict | None
    # Cycle
    cycle_deviation: float | None  # % from 30-day midpoint
    # MACD
    macd_line: float | None = None
    macd_signal: float | None = None
    macd_histogram: float | None = None
    macd_trend: str = "unknown"   # "bullish" | "bearish" | "neutral"
    # Bollinger Bands
    bb_upper: float | None = None
    bb_middle: float | None = None
    bb_lower: float | None = None
    bb_pct: float | None = None   # 0–100 position within bands
    bb_signal: str = "unknown"    # "overbought" | "oversold" | "neutral"
    # EMA
    ema_20: float | None = None
    ema_50: float | None = None
    ema_cross: str = "unknown"    # "bullish" | "bearish"
    # Williams %R
    williams_r: float | None = None
    williams_r_signal: str = "unknown"  # "overbought" | "oversold" | "neutral"
    error: str | None = None


_FALLBACK_EXCHANGES = ["binance", "bybit", "okx", "mexc"]
_BLOCKED_EXCHANGES_UNTIL: dict[str, float] = {}
_BLOCK_SECONDS = 1800

# ── Shared exchange pool (avoids creating a new instance per request) ────────
_EXCHANGE_POOL: dict[str, object] = {}


def _get_or_create_exchange(exchange_id: str):
    """Return a cached async CCXT exchange, creating one if needed."""
    if exchange_id in _EXCHANGE_POOL:
        return _EXCHANGE_POOL[exchange_id]
    ccxt = _ccxt_module()
    if ccxt is None:
        raise ValueError("ccxt async module unavailable")
    ex_class = getattr(ccxt, exchange_id, None)
    if ex_class is None:
        raise ValueError(f"Unknown exchange: {exchange_id}")
    ex = ex_class({"enableRateLimit": True, "timeout": 20_000})
    _EXCHANGE_POOL[exchange_id] = ex
    return ex


async def close_exchange_pool():
    """Gracefully close all pooled exchange instances (call on shutdown)."""
    for ex_id, ex in list(_EXCHANGE_POOL.items()):
        try:
            await ex.close()
        except Exception:
            pass
    _EXCHANGE_POOL.clear()


async def _fetch_ohlcv_from(exchange_id: str, symbol: str,
                             timeframe: str, limit: int) -> list[list]:
    """Try one exchange; return candles or raise."""
    ex = _get_or_create_exchange(exchange_id)
    try:
        candles = await ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        return candles or []
    except Exception as exc:
        # If the exchange is broken (e.g. session closed), evict and re-raise
        if "session is closed" in str(exc).lower():
            _EXCHANGE_POOL.pop(exchange_id, None)
        raise


async def _fetch_ohlcv_okx_http(symbol: str, limit: int) -> list[list]:
    """Direct OKX REST fallback — no CCXT, plain httpx.
    OKX returns rows newest-first; we reverse to chronological order.
    Row format: [ts, open, high, low, close, vol, volCcy, volCcyQuote, confirm]
    """
    inst_id = symbol.replace("/", "-") if "/" in symbol else f"{symbol}-USDT"
    url = (
        f"https://www.okx.com/api/v5/market/candles"
        f"?instId={inst_id}&bar=1D&limit={limit}"
    )
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
    if data.get("code") != "0" or not data.get("data"):
        raise ValueError(f"OKX HTTP error: {data.get('msg', 'empty response')}")
    return [
        [int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]
        for r in reversed(data["data"])
    ]


async def _fetch_ohlcv(symbol: str, exchange_id: str = "binance",
                       timeframe: str = "1d", limit: int = 60) -> list[list]:
    """Fetch daily OHLCV candles, falling back through exchanges on failure.

    Chain: CCXT binance → CCXT bybit → CCXT okx → direct OKX HTTP (httpx)
    """
    order = [exchange_id] + [e for e in _FALLBACK_EXCHANGES if e != exchange_id]

    # If ccxt cannot be imported, skip noisy per-exchange attempts and jump to HTTP fallback.
    if _ccxt_module() is None:
        order = []

    for ex_id in order:
        blocked_until = _BLOCKED_EXCHANGES_UNTIL.get(ex_id, 0)
        if blocked_until > time.time():
            continue
        try:
            candles = await _fetch_ohlcv_from(ex_id, symbol, timeframe, limit)
            if candles:
                return candles
        except Exception as exc:
            if _is_geo_block(exc):
                _BLOCKED_EXCHANGES_UNTIL[ex_id] = time.time() + _BLOCK_SECONDS
                log.warning("OHLCV venue temporarily disabled for %ss due to geo block: %s", _BLOCK_SECONDS, ex_id)
            log.warning("OHLCV fetch failed on %s for %s: %s", ex_id, symbol, exc)

    # All CCXT paths failed — try direct OKX REST as last resort
    try:
        candles = await _fetch_ohlcv_okx_http(symbol, limit)
        if candles:
            log.info("OHLCV fetched via direct OKX HTTP for %s", symbol)
            return candles
    except Exception as exc:
        log.warning("OHLCV direct OKX HTTP failed for %s: %s", symbol, exc)

    return []


# ── Indicator helpers ──────────────────────────────────────────────────────────

def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def _is_geo_block(exc: Exception) -> bool:
    msg = str(exc).lower()
    patterns = [
        r"restricted location",
        r"block access from your country",
        r"cloudfront distribution is configured to block",
        r"service unavailable from a restricted location",
        r"\b451\b",
        r"\b403\b.*forbidden",
        r"403 error",
    ]
    return any(re.search(p, msg) for p in patterns)


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


def _ema(values: list[float], period: int) -> list[float]:
    """Exponential Moving Average; returns [] if insufficient data."""
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    result = [sum(values[:period]) / period]
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def _macd(closes: list[float], fast: int = 12, slow: int = 26,
          signal: int = 9) -> tuple[float | None, float | None, float | None]:
    """Returns (macd_line, signal_line, histogram) for the most recent candle."""
    if len(closes) < slow + signal:
        return None, None, None
    ema_fast = _ema(closes, fast)   # len = len(closes) - fast + 1
    ema_slow = _ema(closes, slow)   # len = len(closes) - slow + 1
    if not ema_fast or not ema_slow:
        return None, None, None
    # Align: ema_fast is longer by (slow - fast) entries
    offset = slow - fast
    macd_line = [ema_fast[i + offset] - ema_slow[i] for i in range(len(ema_slow))]
    sig_line = _ema(macd_line, signal)
    if not sig_line:
        return None, None, None
    m = macd_line[-1]
    s = sig_line[-1]
    return round(m, 6), round(s, 6), round(m - s, 6)


def _bollinger(closes: list[float], period: int = 20,
               std_dev: float = 2.0) -> tuple[float | None, float | None, float | None, float | None]:
    """Returns (upper, middle, lower, bb_pct).
    bb_pct = (price - lower) / (upper - lower) * 100 — 0 at lower band, 100 at upper.
    """
    if len(closes) < period:
        return None, None, None, None
    window = closes[-period:]
    middle = sum(window) / period
    variance = sum((x - middle) ** 2 for x in window) / period
    std = variance ** 0.5
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    current = closes[-1]
    bb_pct = ((current - lower) / (upper - lower) * 100) if upper != lower else 50.0
    return round(upper, 4), round(middle, 4), round(lower, 4), round(bb_pct, 2)


def _williams_r(candles: list[list], period: int = 14) -> float | None:
    """Williams %R: -100 (oversold) to 0 (overbought)."""
    if len(candles) < period:
        return None
    window = candles[-period:]
    highest_high = max(c[2] for c in window)
    lowest_low = min(c[3] for c in window)
    if highest_high == lowest_low:
        return -50.0
    wr = (highest_high - candles[-1][4]) / (highest_high - lowest_low) * -100
    return round(wr, 2)


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


# ── Main calculation ───────────────────────────────────────────────────────────

async def calculate(symbol: str = "BTC/USDT") -> TechnicalResult:
    cache_key = symbol
    if cache_key in _CACHE:
        ts, cached = _CACHE[cache_key]
        if time.time() - ts < _CACHE_TTL:
            return cached

    candles = await _fetch_ohlcv(symbol, limit=60)

    if not candles or len(candles) < 20:
        log.error(
            "Insufficient candle data for %s: got %d candles from all exchanges",
            symbol, len(candles),
        )
        return TechnicalResult(
            score=0.0, symbol=symbol, current_price=None,
            rsi_14=None, rsi_signal="unknown",
            volatility_score=0.0, fib_levels=[], nearest_fib=None,
            cycle_deviation=None, error="Insufficient candle data",
        )

    closes  = [c[4] for c in candles]
    highs   = [c[2] for c in candles]
    lows    = [c[3] for c in candles]
    current = closes[-1]

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
        rsi_score  = abs(rsi - 50) / 10

    # ── ATR Volatility ────────────────────────────────────────────────────────
    atr = _atr(candles)
    if atr and current > 0:
        atr_pct = atr / current * 100
        volatility_score = round(min(10.0, atr_pct * 1.6), 2)
    else:
        volatility_score = 0.0

    # ── Fibonacci Levels (60-day range) ──────────────────────────────────────
    period_high = max(highs[-60:])
    period_low  = min(lows[-60:])
    fib_levels, nearest_fib = _fibonacci_levels(period_high, period_low, current)

    fib_score = 0.0
    if nearest_fib:
        dist  = nearest_fib["distance_pct"]
        ratio = nearest_fib["ratio"]
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

    # ── MACD (12/26/9) ────────────────────────────────────────────────────────
    macd_line, macd_signal, macd_histogram = _macd(closes)
    if macd_line is not None and macd_histogram is not None:
        if macd_line > macd_signal:
            macd_trend = "bullish"
            macd_score = 0.0   # bullish = lower risk
        else:
            macd_trend = "bearish"
            # Score proportional to how far below signal
            macd_score = min(5.0, abs(macd_histogram) / (abs(macd_line) + 1e-9) * 5)
    else:
        macd_trend = "unknown"
        macd_score = 0.0

    # ── Bollinger Bands (20, 2σ) ──────────────────────────────────────────────
    bb_upper, bb_middle, bb_lower, bb_pct = _bollinger(closes)
    if bb_pct is not None:
        if bb_pct >= 80:
            bb_signal = "overbought"
            bb_score  = min(5.0, (bb_pct - 80) / 4)
        elif bb_pct <= 20:
            bb_signal = "oversold"
            bb_score  = min(5.0, (20 - bb_pct) / 4)
        else:
            bb_signal = "neutral"
            bb_score  = 0.0
    else:
        bb_signal = "unknown"
        bb_score  = 0.0

    # ── EMA 20 / EMA 50 crossover ─────────────────────────────────────────────
    ema20_series = _ema(closes, 20)
    ema50_series = _ema(closes, 50)
    ema_20 = round(ema20_series[-1], 4) if ema20_series else None
    ema_50 = round(ema50_series[-1], 4) if ema50_series else None
    if ema_20 is not None and ema_50 is not None:
        ema_cross = "bullish" if ema_20 > ema_50 else "bearish"
    else:
        ema_cross = "unknown"

    # ── Williams %R (14) ──────────────────────────────────────────────────────
    wr = _williams_r(candles)
    if wr is not None:
        if wr >= -20:
            williams_r_signal = "overbought"
            wr_score = min(5.0, (wr + 20) / 4)
        elif wr <= -80:
            williams_r_signal = "oversold"
            wr_score = min(5.0, (-80 - wr) / 4)
        else:
            williams_r_signal = "neutral"
            wr_score = 0.0
    else:
        williams_r_signal = "unknown"
        wr_score = 0.0

    # ── Composite Technical Score ─────────────────────────────────────────────
    # Weights: RSI 30%, Volatility 22%, Fib 13%, MACD 15%, BB 10%, Williams %R 10%
    score = round(min(10.0, (
        rsi_score        * 0.30
        + volatility_score * 0.22
        + fib_score        * 0.13
        + macd_score       * 0.15
        + bb_score         * 0.10
        + wr_score         * 0.10
    )), 2)

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
        macd_line=macd_line,
        macd_signal=macd_signal,
        macd_histogram=macd_histogram,
        macd_trend=macd_trend,
        bb_upper=bb_upper,
        bb_middle=bb_middle,
        bb_lower=bb_lower,
        bb_pct=bb_pct,
        bb_signal=bb_signal,
        ema_20=ema_20,
        ema_50=ema_50,
        ema_cross=ema_cross,
        williams_r=wr,
        williams_r_signal=williams_r_signal,
    )
    _CACHE[cache_key] = (time.time(), result)
    return result
