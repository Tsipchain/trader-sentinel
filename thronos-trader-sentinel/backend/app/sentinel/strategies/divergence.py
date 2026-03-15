"""
Smart Money Divergence Detection (SMDD) — Gift Strategy #1
-----------------------------------------------------------
Detects divergences between price action and momentum oscillators
to identify institutional accumulation/distribution patterns.

Types detected:
  - Regular Bullish Divergence:  price makes lower low, RSI makes higher low
    → Smart money is accumulating. Strong long signal.
  - Regular Bearish Divergence:  price makes higher high, RSI makes lower high
    → Smart money is distributing. Strong short signal.
  - Hidden Bullish Divergence:   price makes higher low, RSI makes lower low
    → Trend continuation signal in uptrend.
  - Hidden Bearish Divergence:   price makes lower high, RSI makes higher high
    → Trend continuation signal in downtrend.

Also checks MACD histogram divergences for double confirmation.
When both RSI and MACD diverge in the same direction, confidence is highest.

Score output: direction votes + confidence bonus for the entry evaluator.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# Minimum swing distance (in candle indices) to consider a valid swing
MIN_SWING_LOOKBACK = 5
MAX_SWING_LOOKBACK = 30


@dataclass
class DivergenceResult:
    """Result of divergence analysis."""
    has_divergence: bool = False
    divergence_type: str = "none"          # "regular_bullish", "regular_bearish",
                                            # "hidden_bullish", "hidden_bearish", "none"
    rsi_divergence: bool = False
    macd_divergence: bool = False
    double_confirmation: bool = False       # both RSI and MACD diverge same direction
    direction_vote: float = 0.0             # positive = long, negative = short
    confidence_bonus: float = 0.0           # extra confidence to add
    description: str = ""
    swing_points: list[dict] = field(default_factory=list)


def _find_swing_lows(values: list[float], min_dist: int = 5) -> list[tuple[int, float]]:
    """Find local minima (swing lows) in a series."""
    swings = []
    for i in range(min_dist, len(values) - min_dist):
        window_left = values[max(0, i - min_dist):i]
        window_right = values[i + 1:i + min_dist + 1]
        if window_left and window_right:
            if values[i] <= min(window_left) and values[i] <= min(window_right):
                swings.append((i, values[i]))
    return swings


def _find_swing_highs(values: list[float], min_dist: int = 5) -> list[tuple[int, float]]:
    """Find local maxima (swing highs) in a series."""
    swings = []
    for i in range(min_dist, len(values) - min_dist):
        window_left = values[max(0, i - min_dist):i]
        window_right = values[i + 1:i + min_dist + 1]
        if window_left and window_right:
            if values[i] >= max(window_left) and values[i] >= max(window_right):
                swings.append((i, values[i]))
    return swings


def _rsi_series(closes: list[float], period: int = 14) -> list[float]:
    """Calculate RSI for every point in the series (returns list same length as closes)."""
    if len(closes) < period + 1:
        return []

    rsi_values = [50.0] * (period + 1)  # fill initial values with neutral

    gains = []
    losses = []
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
            rsi_values.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(100 - (100 / (1 + rs)))

    return rsi_values


def _macd_histogram_series(closes: list[float], fast: int = 12,
                           slow: int = 26, signal: int = 9) -> list[float]:
    """Calculate MACD histogram series."""
    if len(closes) < slow + signal:
        return []

    def ema(values: list[float], period: int) -> list[float]:
        if len(values) < period:
            return []
        k = 2.0 / (period + 1)
        result = [sum(values[:period]) / period]
        for v in values[period:]:
            result.append(v * k + result[-1] * (1 - k))
        return result

    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    if not ema_fast or not ema_slow:
        return []

    offset = slow - fast
    macd_line = [ema_fast[i + offset] - ema_slow[i] for i in range(len(ema_slow))]
    sig_line = ema(macd_line, signal)
    if not sig_line:
        return []

    # Align histogram to end of closes
    sig_offset = len(macd_line) - len(sig_line)
    histogram = [macd_line[i + sig_offset] - sig_line[i] for i in range(len(sig_line))]

    # Pad front to align with closes
    pad = len(closes) - len(histogram)
    return [0.0] * pad + histogram


def detect_divergences(
    candles: list[list],
    lookback: int = 30,
    swing_dist: int = 5,
) -> DivergenceResult:
    """Detect smart money divergences from OHLCV candle data.

    Args:
        candles: OHLCV candle data [[ts, o, h, l, c, v], ...]
        lookback: How many recent candles to analyze for divergences
        swing_dist: Minimum distance between swing points

    Returns:
        DivergenceResult with divergence info and directional votes
    """
    if len(candles) < 40:
        return DivergenceResult(description="Insufficient data for divergence analysis")

    closes = [c[4] for c in candles]
    lows = [c[3] for c in candles]
    highs = [c[2] for c in candles]

    # Calculate indicator series
    rsi = _rsi_series(closes)
    macd_hist = _macd_histogram_series(closes)

    if len(rsi) < lookback or len(macd_hist) < lookback:
        return DivergenceResult(description="Insufficient indicator data")

    # Use last N candles for swing detection
    recent_closes = closes[-lookback:]
    recent_lows = lows[-lookback:]
    recent_highs = highs[-lookback:]
    recent_rsi = rsi[-lookback:]
    recent_macd = macd_hist[-lookback:]

    # Find swing points in price and RSI
    price_swing_lows = _find_swing_lows(recent_lows, min_dist=swing_dist)
    price_swing_highs = _find_swing_highs(recent_highs, min_dist=swing_dist)
    rsi_swing_lows = _find_swing_lows(recent_rsi, min_dist=swing_dist)
    rsi_swing_highs = _find_swing_highs(recent_rsi, min_dist=swing_dist)
    macd_swing_lows = _find_swing_lows(recent_macd, min_dist=swing_dist)
    macd_swing_highs = _find_swing_highs(recent_macd, min_dist=swing_dist)

    result = DivergenceResult()

    # ── Check RSI Divergences ────────────────────────────────────────────
    # Regular Bullish: price lower low + RSI higher low
    if len(price_swing_lows) >= 2 and len(rsi_swing_lows) >= 2:
        p1, p2 = price_swing_lows[-2], price_swing_lows[-1]
        r1, r2 = rsi_swing_lows[-2], rsi_swing_lows[-1]
        if p2[1] < p1[1] and r2[1] > r1[1]:
            result.rsi_divergence = True
            result.has_divergence = True
            result.divergence_type = "regular_bullish"
            result.direction_vote = 2.5
            result.confidence_bonus = 0.12
            result.description = (
                f"Regular Bullish Divergence: price lower low "
                f"({p1[1]:.2f}→{p2[1]:.2f}) but RSI higher low "
                f"({r1[1]:.1f}→{r2[1]:.1f}) — smart money accumulating"
            )
        # Hidden Bullish: price higher low + RSI lower low (trend continuation)
        elif p2[1] > p1[1] and r2[1] < r1[1]:
            result.rsi_divergence = True
            result.has_divergence = True
            result.divergence_type = "hidden_bullish"
            result.direction_vote = 1.5
            result.confidence_bonus = 0.08
            result.description = (
                f"Hidden Bullish Divergence: price higher low "
                f"({p1[1]:.2f}→{p2[1]:.2f}) but RSI lower low "
                f"({r1[1]:.1f}→{r2[1]:.1f}) — uptrend continuation signal"
            )

    # Regular Bearish: price higher high + RSI lower high
    if not result.has_divergence and len(price_swing_highs) >= 2 and len(rsi_swing_highs) >= 2:
        p1, p2 = price_swing_highs[-2], price_swing_highs[-1]
        r1, r2 = rsi_swing_highs[-2], rsi_swing_highs[-1]
        if p2[1] > p1[1] and r2[1] < r1[1]:
            result.rsi_divergence = True
            result.has_divergence = True
            result.divergence_type = "regular_bearish"
            result.direction_vote = -2.5
            result.confidence_bonus = 0.12
            result.description = (
                f"Regular Bearish Divergence: price higher high "
                f"({p1[1]:.2f}→{p2[1]:.2f}) but RSI lower high "
                f"({r1[1]:.1f}→{r2[1]:.1f}) — smart money distributing"
            )
        # Hidden Bearish: price lower high + RSI higher high
        elif p2[1] < p1[1] and r2[1] > r1[1]:
            result.rsi_divergence = True
            result.has_divergence = True
            result.divergence_type = "hidden_bearish"
            result.direction_vote = -1.5
            result.confidence_bonus = 0.08
            result.description = (
                f"Hidden Bearish Divergence: price lower high "
                f"({p1[1]:.2f}→{p2[1]:.2f}) but RSI higher high "
                f"({r1[1]:.1f}→{r2[1]:.1f}) — downtrend continuation signal"
            )

    # ── Check MACD Histogram Divergences ─────────────────────────────────
    macd_div_direction = 0.0
    if len(price_swing_lows) >= 2 and len(macd_swing_lows) >= 2:
        p1, p2 = price_swing_lows[-2], price_swing_lows[-1]
        m1, m2 = macd_swing_lows[-2], macd_swing_lows[-1]
        if p2[1] < p1[1] and m2[1] > m1[1]:
            result.macd_divergence = True
            macd_div_direction = 1.5  # bullish
            if not result.has_divergence:
                result.has_divergence = True
                result.divergence_type = "regular_bullish"
                result.direction_vote = 1.5
                result.confidence_bonus = 0.08
                result.description = (
                    f"MACD Bullish Divergence: price lower low but MACD histogram higher low"
                )

    if not result.macd_divergence and len(price_swing_highs) >= 2 and len(macd_swing_highs) >= 2:
        p1, p2 = price_swing_highs[-2], price_swing_highs[-1]
        m1, m2 = macd_swing_highs[-2], macd_swing_highs[-1]
        if p2[1] > p1[1] and m2[1] < m1[1]:
            result.macd_divergence = True
            macd_div_direction = -1.5  # bearish
            if not result.has_divergence:
                result.has_divergence = True
                result.divergence_type = "regular_bearish"
                result.direction_vote = -1.5
                result.confidence_bonus = 0.08
                result.description = (
                    f"MACD Bearish Divergence: price higher high but MACD histogram lower high"
                )

    # ── Double Confirmation ──────────────────────────────────────────────
    # When RSI and MACD diverge in same direction → highest confidence
    if result.rsi_divergence and result.macd_divergence:
        rsi_bullish = result.direction_vote > 0
        macd_bullish = macd_div_direction > 0
        if rsi_bullish == macd_bullish:
            result.double_confirmation = True
            result.confidence_bonus = 0.18  # upgrade from 0.12
            result.direction_vote *= 1.3    # amplify the vote
            result.description += " | DOUBLE CONFIRMED with MACD histogram"

    # Store swing points for debugging/visualization
    result.swing_points = [
        {"type": "price_low", "points": [(i, v) for i, v in price_swing_lows[-3:]]},
        {"type": "price_high", "points": [(i, v) for i, v in price_swing_highs[-3:]]},
        {"type": "rsi_low", "points": [(i, v) for i, v in rsi_swing_lows[-3:]]},
        {"type": "rsi_high", "points": [(i, v) for i, v in rsi_swing_highs[-3:]]},
    ]

    return result
