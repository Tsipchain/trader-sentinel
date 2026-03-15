"""
Multi-Timeframe Confluence (MTC) — Gift Strategy #2
----------------------------------------------------
Cross-validates trading signals across multiple timeframes to filter
out noise and only confirm high-probability entries.

Core principle: A signal is only valid when 2+ timeframes agree.
- 1h timeframe:  Short-term momentum (fast entries)
- 4h timeframe:  Medium-term trend (main bias)
- 1d timeframe:  Long-term direction (filter)

Confluence scoring:
  - All 3 timeframes agree:  +3.0 direction votes, +0.15 confidence
  - 2 of 3 agree:           +1.5 direction votes, +0.08 confidence
  - Mixed/conflicting:       0.0 votes, -0.05 confidence (signal rejected)

This strategy dramatically reduces false signals by requiring
cross-timeframe agreement before entry.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class TimeframeSignal:
    """Signal from a single timeframe."""
    timeframe: str           # "1h", "4h", "1d"
    direction: str           # "long", "short", "neutral"
    strength: float          # 0.0 to 1.0
    rsi: float | None = None
    macd_trend: str = "unknown"
    ema_cross: str = "unknown"
    bb_signal: str = "unknown"


@dataclass
class ConfluenceResult:
    """Result of multi-timeframe confluence analysis."""
    confluence_level: str = "none"     # "strong", "moderate", "weak", "conflicting", "none"
    agreeing_timeframes: int = 0       # how many TFs agree
    direction: str = "neutral"         # consensus direction: "long", "short", "neutral"
    direction_vote: float = 0.0        # votes to add to entry evaluator
    confidence_bonus: float = 0.0      # confidence modifier
    timeframe_signals: list[dict] = field(default_factory=list)
    description: str = ""


def _analyze_single_timeframe(candles: list[list]) -> TimeframeSignal | None:
    """Analyze a set of candles and return directional signal.

    Expects OHLCV candles: [[ts, open, high, low, close, volume], ...]
    """
    if not candles or len(candles) < 26:
        return None

    closes = [c[4] for c in candles]
    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]

    # RSI (14)
    rsi = _rsi(closes)

    # EMA 20/50
    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    if ema20 and ema50:
        ema_cross = "bullish" if ema20[-1] > ema50[-1] else "bearish"
    else:
        ema_cross = "unknown"

    # MACD
    macd_line, macd_signal, macd_hist = _macd(closes)
    if macd_line is not None and macd_signal is not None:
        macd_trend = "bullish" if macd_line > macd_signal else "bearish"
    else:
        macd_trend = "unknown"

    # Bollinger Bands position
    bb_pct = _bollinger_pct(closes)
    if bb_pct is not None:
        if bb_pct >= 80:
            bb_signal = "overbought"
        elif bb_pct <= 20:
            bb_signal = "oversold"
        else:
            bb_signal = "neutral"
    else:
        bb_signal = "unknown"

    # Vote for direction
    long_votes = 0.0
    short_votes = 0.0

    # RSI
    if rsi is not None:
        if rsi < 30:
            long_votes += 1.0
        elif rsi < 40:
            long_votes += 0.5
        elif rsi > 70:
            short_votes += 1.0
        elif rsi > 60:
            short_votes += 0.5

    # EMA
    if ema_cross == "bullish":
        long_votes += 1.0
    elif ema_cross == "bearish":
        short_votes += 1.0

    # MACD
    if macd_trend == "bullish":
        long_votes += 1.0
    elif macd_trend == "bearish":
        short_votes += 1.0

    # BB
    if bb_signal == "oversold":
        long_votes += 0.5
    elif bb_signal == "overbought":
        short_votes += 0.5

    # Determine direction
    total = long_votes + short_votes
    if total == 0:
        direction = "neutral"
        strength = 0.0
    elif long_votes > short_votes:
        direction = "long"
        strength = min(1.0, (long_votes - short_votes) / max(total, 1))
    elif short_votes > long_votes:
        direction = "short"
        strength = min(1.0, (short_votes - long_votes) / max(total, 1))
    else:
        direction = "neutral"
        strength = 0.0

    return TimeframeSignal(
        timeframe="",  # set by caller
        direction=direction,
        strength=strength,
        rsi=round(rsi, 2) if rsi else None,
        macd_trend=macd_trend,
        ema_cross=ema_cross,
        bb_signal=bb_signal,
    )


def check_confluence(
    candles_1h: list[list] | None = None,
    candles_4h: list[list] | None = None,
    candles_1d: list[list] | None = None,
) -> ConfluenceResult:
    """Check multi-timeframe confluence across 1h, 4h, and 1d candles.

    At minimum, 2 timeframes are needed for confluence analysis.
    The 1d candles come from the existing TA pipeline (already fetched).

    Args:
        candles_1h: 1-hour OHLCV candles (at least 50 candles)
        candles_4h: 4-hour OHLCV candles (at least 50 candles)
        candles_1d: 1-day OHLCV candles (at least 26 candles)

    Returns:
        ConfluenceResult with direction votes and confidence modifiers
    """
    signals: list[TimeframeSignal] = []

    for label, candles in [("1h", candles_1h), ("4h", candles_4h), ("1d", candles_1d)]:
        if candles and len(candles) >= 26:
            sig = _analyze_single_timeframe(candles)
            if sig:
                sig.timeframe = label
                signals.append(sig)

    if len(signals) < 2:
        return ConfluenceResult(
            confluence_level="none",
            description=f"Only {len(signals)} timeframe(s) available — need 2+ for confluence",
        )

    # Count directional agreement
    directions = [s.direction for s in signals if s.direction != "neutral"]
    long_count = sum(1 for d in directions if d == "long")
    short_count = sum(1 for d in directions if d == "short")
    total_directional = long_count + short_count

    result = ConfluenceResult(
        timeframe_signals=[
            {
                "timeframe": s.timeframe,
                "direction": s.direction,
                "strength": round(s.strength, 2),
                "rsi": s.rsi,
                "macd_trend": s.macd_trend,
                "ema_cross": s.ema_cross,
            }
            for s in signals
        ],
    )

    if total_directional == 0:
        result.confluence_level = "weak"
        result.direction = "neutral"
        result.description = "All timeframes neutral — no directional bias"
        return result

    # Determine consensus
    if long_count == len(signals):
        # All agree long
        result.confluence_level = "strong"
        result.agreeing_timeframes = len(signals)
        result.direction = "long"
        result.direction_vote = 3.0
        result.confidence_bonus = 0.15
        avg_strength = sum(s.strength for s in signals) / len(signals)
        result.description = (
            f"STRONG confluence: all {len(signals)} timeframes agree LONG "
            f"(avg strength {avg_strength:.0%})"
        )

    elif short_count == len(signals):
        # All agree short
        result.confluence_level = "strong"
        result.agreeing_timeframes = len(signals)
        result.direction = "short"
        result.direction_vote = -3.0
        result.confidence_bonus = 0.15
        avg_strength = sum(s.strength for s in signals) / len(signals)
        result.description = (
            f"STRONG confluence: all {len(signals)} timeframes agree SHORT "
            f"(avg strength {avg_strength:.0%})"
        )

    elif long_count >= 2:
        # Majority long
        result.confluence_level = "moderate"
        result.agreeing_timeframes = long_count
        result.direction = "long"
        result.direction_vote = 1.5
        result.confidence_bonus = 0.08
        result.description = (
            f"Moderate confluence: {long_count}/{len(signals)} timeframes agree LONG"
        )

    elif short_count >= 2:
        # Majority short
        result.confluence_level = "moderate"
        result.agreeing_timeframes = short_count
        result.direction = "short"
        result.direction_vote = -1.5
        result.confidence_bonus = 0.08
        result.description = (
            f"Moderate confluence: {short_count}/{len(signals)} timeframes agree SHORT"
        )

    else:
        # Conflicting signals
        result.confluence_level = "conflicting"
        result.direction = "neutral"
        result.direction_vote = 0.0
        result.confidence_bonus = -0.05
        result.description = (
            f"Conflicting: {long_count} long vs {short_count} short — "
            "no clear multi-TF consensus, reducing confidence"
        )

    return result


# ── Indicator helpers (standalone, no dependency on technicals.py) ────────────

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
    return 100 - (100 / (1 + rs))


def _ema(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    result = [sum(values[:period]) / period]
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def _macd(closes: list[float], fast: int = 12, slow: int = 26,
          signal: int = 9) -> tuple[float | None, float | None, float | None]:
    if len(closes) < slow + signal:
        return None, None, None
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    if not ema_fast or not ema_slow:
        return None, None, None
    offset = slow - fast
    macd_line = [ema_fast[i + offset] - ema_slow[i] for i in range(len(ema_slow))]
    sig_line = _ema(macd_line, signal)
    if not sig_line:
        return None, None, None
    return macd_line[-1], sig_line[-1], macd_line[-1] - sig_line[-1]


def _bollinger_pct(closes: list[float], period: int = 20, std_dev: float = 2.0) -> float | None:
    if len(closes) < period:
        return None
    window = closes[-period:]
    middle = sum(window) / period
    variance = sum((x - middle) ** 2 for x in window) / period
    std = variance ** 0.5
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    if upper == lower:
        return 50.0
    return ((closes[-1] - lower) / (upper - lower)) * 100
