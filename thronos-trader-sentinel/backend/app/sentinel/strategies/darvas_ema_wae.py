"""
Darvas Box + EMA Cloud + Waddah Attar Explosion Strategy
---------------------------------------------------------
Converted from TradingView Pine Script v5 to Python for Sentinel integration.

Original: "Darvas Box, EMA Cloud & Waddah Attar Explosion Strategy"
Adapted for real-time signal generation in the Pytheia Sentinel.

Components:
  1. Darvas Box — Identifies price breakout boxes (support/resistance)
     - Box forms when price consolidates, breaks out on close > TopBox
     - Green box = bullish breakout, Red box = bearish breakdown

  2. EMA Cloud — Dual EMA (fast 54, slow 200) trend filter
     - Green cloud = uptrend, Red cloud = downtrend
     - Acts as primary trend direction filter

  3. Waddah Attar Explosion — Momentum explosion detector
     - Combines MACD sensitivity with Bollinger Band width
     - TrendUp/TrendDown bars vs ExplosionLine threshold
     - Dead Zone filter eliminates low-volatility noise

  4. Heikin Ashi — Smoothed candles for trend detection and trailing SL

Entry Rules:
  LONG:  close > TopBox AND trendUp > explosion AND trendUp > dead_zone
         AND box is green AND EMA cloud is green
  SHORT: close < BottomBox AND trendDown > explosion AND trendDown > dead_zone
         AND box is red AND EMA cloud is red

Risk Management:
  - TP1 at 0.75x risk-reward, TP2 at 1.5x risk-reward
  - Trailing SL: moves to breakeven at TP1, moves to TP1 at TP2
  - SL at opposite box boundary
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class DarvasBox:
    """Current Darvas Box state."""
    top: float = 0.0
    bottom: float = 0.0
    is_green: bool = False    # price above top
    is_red: bool = False      # price below bottom
    is_yellow: bool = False   # price inside box


@dataclass
class EMACloud:
    """EMA Cloud state."""
    fast_ema: float = 0.0
    slow_ema: float = 0.0
    is_green: bool = False  # fast > slow = uptrend
    is_red: bool = False    # fast < slow = downtrend


@dataclass
class WaddahAttar:
    """Waddah Attar Explosion state."""
    trend_up: float = 0.0
    trend_down: float = 0.0
    explosion_line: float = 0.0
    dead_zone: float = 0.0
    is_exploding_up: bool = False    # trendUp > explosion AND trendUp > dead_zone
    is_exploding_down: bool = False  # trendDown > explosion AND trendDown > dead_zone


@dataclass
class HeikinAshi:
    """Heikin Ashi candle data."""
    ha_open: float = 0.0
    ha_close: float = 0.0
    ha_high: float = 0.0
    ha_low: float = 0.0


@dataclass
class DarvasEmaWaeResult:
    """Full strategy analysis result."""
    # Signal
    signal: str = "neutral"         # "long", "short", "neutral"
    confidence: float = 0.0         # 0.0 to 1.0
    direction_vote: float = 0.0     # positive = long, negative = short

    # Components
    darvas: DarvasBox = field(default_factory=DarvasBox)
    ema_cloud: EMACloud = field(default_factory=EMACloud)
    wae: WaddahAttar = field(default_factory=WaddahAttar)
    heikin_ashi: HeikinAshi = field(default_factory=HeikinAshi)

    # Risk Management
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit_1: float = 0.0
    take_profit_2: float = 0.0
    risk_reward_1: float = 0.75
    risk_reward_2: float = 1.5

    # Strategy details for chart rendering
    indicators: dict = field(default_factory=dict)
    description: str = ""


# ── Indicator Calculations ───────────────────────────────────────────────────

def _ema(values: list[float], period: int) -> list[float]:
    """Exponential Moving Average series."""
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    result = [sum(values[:period]) / period]
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def _sma(values: list[float], period: int) -> float | None:
    """Simple Moving Average of last N values."""
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _stdev(values: list[float], period: int) -> float:
    """Standard deviation of last N values."""
    if len(values) < period:
        return 0.0
    window = values[-period:]
    mean = sum(window) / period
    variance = sum((x - mean) ** 2 for x in window) / period
    return variance ** 0.5


def _rma(values: list[float], period: int) -> list[float]:
    """Wilder's Running Moving Average (RMA)."""
    if len(values) < period:
        return []
    alpha = 1.0 / period
    result = [sum(values[:period]) / period]
    for v in values[period:]:
        result.append(alpha * v + (1 - alpha) * result[-1])
    return result


def _true_range(candles: list[list]) -> list[float]:
    """Calculate True Range series from OHLCV candles."""
    trs = []
    for i in range(1, len(candles)):
        h = candles[i][2]
        l = candles[i][3]
        prev_c = candles[i - 1][4]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)
    return trs


def _highest(values: list[float], period: int, offset: int = 0) -> float:
    """Highest value in last N periods with optional offset."""
    start = max(0, len(values) - period - offset)
    end = len(values) - offset
    if start >= end or end <= 0:
        return 0.0
    return max(values[start:end])


def _lowest(values: list[float], period: int, offset: int = 0) -> float:
    """Lowest value in last N periods with optional offset."""
    start = max(0, len(values) - period - offset)
    end = len(values) - offset
    if start >= end or end <= 0:
        return 0.0
    return min(values[start:end])


# ── Darvas Box Calculation ───────────────────────────────────────────────────

def _calculate_darvas_box(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    box_period: int = 5,
) -> DarvasBox:
    """Calculate current Darvas Box boundaries.

    The Darvas Box forms when:
    - A new high is made
    - Price then consolidates for box_period bars without breaking that high
    - TopBox = the new high, BottomBox = lowest low during consolidation
    """
    if len(highs) < box_period + 5:
        return DarvasBox()

    # Find the highest high in recent periods (k1, k2, k3 from Pine)
    k1 = _highest(highs, box_period)
    k2 = _highest(highs, box_period - 1)
    k3 = _highest(highs, box_period - 2)

    # Box condition: k3 < k2 (consolidation pattern)
    box_condition = k3 < k2

    # Find TopBox and BottomBox
    # Search backwards for the most recent box formation
    top_box = 0.0
    bottom_box = 0.0

    for i in range(len(highs) - box_period, max(0, len(highs) - 60), -1):
        if i < box_period:
            break
        local_high = max(highs[i - box_period:i])
        prev_high = max(highs[i - box_period - 1:i - 1]) if i > box_period else local_high
        local_low = min(lows[i - box_period:i])

        # Check if a new high was made and then consolidated
        if highs[i - 1] > prev_high:
            # Check consolidation in next bars
            k3_local = max(highs[i:min(i + box_period - 2, len(highs))])
            k2_local = max(highs[i:min(i + box_period - 1, len(highs))])
            if k3_local < k2_local:
                top_box = highs[i - 1]
                bottom_box = local_low
                break

    if top_box == 0 and bottom_box == 0:
        # Fallback: use recent range
        top_box = _highest(highs, box_period * 2)
        bottom_box = _lowest(lows, box_period * 2)

    current_close = closes[-1]
    return DarvasBox(
        top=top_box,
        bottom=bottom_box,
        is_green=current_close > top_box,
        is_red=current_close < bottom_box,
        is_yellow=bottom_box <= current_close <= top_box,
    )


# ── EMA Cloud Calculation ───────────────────────────────────────────────────

def _calculate_ema_cloud(
    closes: list[float],
    fast_period: int = 54,
    slow_period: int = 200,
) -> EMACloud:
    """Calculate dual EMA cloud."""
    ema_fast_series = _ema(closes, fast_period)
    ema_slow_series = _ema(closes, slow_period)

    if not ema_fast_series or not ema_slow_series:
        return EMACloud()

    fast_val = ema_fast_series[-1]
    slow_val = ema_slow_series[-1]

    return EMACloud(
        fast_ema=round(fast_val, 6),
        slow_ema=round(slow_val, 6),
        is_green=fast_val > slow_val,
        is_red=fast_val < slow_val,
    )


# ── Waddah Attar Explosion Calculation ──────────────────────────────────────

def _calculate_wae(
    closes: list[float],
    sensitivity: int = 150,
    fast_length: int = 20,
    slow_length: int = 40,
    channel_length: int = 20,
    bb_mult: float = 2.0,
    candles: list[list] | None = None,
) -> WaddahAttar:
    """Calculate Waddah Attar Explosion V2."""
    if len(closes) < slow_length + 10:
        return WaddahAttar()

    # MACD difference * sensitivity
    def calc_macd(source: list[float], fast: int, slow: int) -> float:
        fast_ema = _ema(source, fast)
        slow_ema = _ema(source, slow)
        if not fast_ema or not slow_ema:
            return 0.0
        return fast_ema[-1] - slow_ema[-1]

    macd_current = calc_macd(closes, fast_length, slow_length)
    macd_prev = calc_macd(closes[:-1], fast_length, slow_length)
    t1 = (macd_current - macd_prev) * sensitivity

    # Bollinger Band width (explosion line)
    bb_sma = _sma(closes, channel_length)
    bb_std = _stdev(closes, channel_length)
    if bb_sma is not None:
        bb_upper = bb_sma + bb_mult * bb_std
        bb_lower = bb_sma - bb_mult * bb_std
        e1 = bb_upper - bb_lower
    else:
        e1 = 0.0

    # Dead Zone: RMA of True Range * 3.7
    dead_zone = 0.0
    if candles and len(candles) > 100:
        tr = _true_range(candles)
        if tr:
            rma_values = _rma(tr, 100)
            if rma_values:
                dead_zone = rma_values[-1] * 3.7

    trend_up = max(t1, 0.0)
    trend_down = max(-t1, 0.0)

    return WaddahAttar(
        trend_up=round(trend_up, 4),
        trend_down=round(trend_down, 4),
        explosion_line=round(e1, 4),
        dead_zone=round(dead_zone, 4),
        is_exploding_up=trend_up > e1 and trend_up > dead_zone,
        is_exploding_down=trend_down > e1 and trend_down > dead_zone,
    )


# ── Heikin Ashi Calculation ─────────────────────────────────────────────────

def _calculate_heikin_ashi(candles: list[list]) -> HeikinAshi:
    """Calculate current Heikin Ashi candle."""
    if len(candles) < 2:
        return HeikinAshi()

    opens = [c[1] for c in candles]
    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]
    closes = [c[4] for c in candles]

    # HA calculations
    ha_close = (opens[-1] + highs[-1] + lows[-1] + closes[-1]) / 4

    # Previous HA open (recursive, bootstrap from first candle)
    ha_open_prev = (opens[-2] + closes[-2]) / 2
    for i in range(2, min(len(candles), 20)):
        idx = len(candles) - i
        ha_c = (opens[idx] + highs[idx] + lows[idx] + closes[idx]) / 4
        ha_open_prev = (ha_open_prev + ha_c) / 2

    ha_open = (ha_open_prev + (opens[-2] + highs[-2] + lows[-2] + closes[-2]) / 4) / 2
    ha_high = max(highs[-1], ha_open, ha_close)
    ha_low = min(lows[-1], ha_open, ha_close)

    return HeikinAshi(
        ha_open=round(ha_open, 6),
        ha_close=round(ha_close, 6),
        ha_high=round(ha_high, 6),
        ha_low=round(ha_low, 6),
    )


# ── Main Strategy Function ──────────────────────────────────────────────────

def analyze(
    candles: list[list],
    box_period: int = 5,
    ema_fast: int = 54,
    ema_slow: int = 200,
    wae_sensitivity: int = 150,
    wae_fast: int = 20,
    wae_slow: int = 40,
    bb_channel: int = 20,
    bb_mult: float = 2.0,
    rr_tp1: float = 0.75,
    rr_tp2: float = 1.5,
) -> DarvasEmaWaeResult:
    """Run the full Darvas Box + EMA Cloud + WAE strategy analysis.

    Args:
        candles: OHLCV data [[ts, open, high, low, close, volume], ...]
        box_period: Darvas Box lookback period
        ema_fast/ema_slow: EMA Cloud periods
        wae_*: Waddah Attar Explosion parameters
        rr_tp1/rr_tp2: Risk-reward ratios for TP levels

    Returns:
        DarvasEmaWaeResult with signal, components, and risk levels
    """
    if not candles or len(candles) < max(ema_slow, 60) + 10:
        return DarvasEmaWaeResult(description="Insufficient data for Darvas/EMA/WAE analysis")

    closes = [c[4] for c in candles]
    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]
    current_price = closes[-1]

    # Calculate all components
    darvas = _calculate_darvas_box(highs, lows, closes, box_period)
    ema_cloud = _calculate_ema_cloud(closes, ema_fast, ema_slow)
    wae = _calculate_wae(closes, wae_sensitivity, wae_fast, wae_slow, bb_channel, bb_mult, candles)
    ha = _calculate_heikin_ashi(candles)

    result = DarvasEmaWaeResult(
        darvas=darvas,
        ema_cloud=ema_cloud,
        wae=wae,
        heikin_ashi=ha,
        risk_reward_1=rr_tp1,
        risk_reward_2=rr_tp2,
    )

    # ── Entry Logic (from Pine Script) ─────────────────────────────────────
    # LONG: close > TopBox AND trendUp > explosion AND trendUp > dead_zone
    #       AND box green AND EMA cloud green AND NOT yellow (inside box)
    long_condition = (
        darvas.is_green
        and wae.is_exploding_up
        and ema_cloud.is_green
        and not darvas.is_yellow
    )

    # SHORT: close < BottomBox AND trendDown > explosion AND trendDown > dead_zone
    #        AND box red AND EMA cloud red AND NOT yellow
    short_condition = (
        darvas.is_red
        and wae.is_exploding_down
        and ema_cloud.is_red
        and not darvas.is_yellow
    )

    # Calculate signal strength based on component alignment
    alignment_score = 0.0
    components_aligned = []

    if long_condition:
        result.signal = "long"
        # Score based on how strongly each component agrees
        alignment_score += 0.25  # Darvas breakout
        components_aligned.append("Darvas breakout")

        if wae.trend_up > wae.explosion_line * 1.5:
            alignment_score += 0.2  # Strong WAE explosion
            components_aligned.append("Strong WAE explosion")
        else:
            alignment_score += 0.1
            components_aligned.append("WAE explosion")

        if ema_cloud.fast_ema > ema_cloud.slow_ema * 1.01:
            alignment_score += 0.2  # Strong EMA trend
            components_aligned.append("Strong EMA uptrend")
        else:
            alignment_score += 0.1
            components_aligned.append("EMA uptrend")

        # HA confirmation
        if ha.ha_close > ha.ha_open:
            alignment_score += 0.15
            components_aligned.append("HA bullish")

        # Momentum strength
        if wae.trend_up > 0 and wae.dead_zone > 0:
            momentum_ratio = wae.trend_up / max(wae.dead_zone, 0.001)
            alignment_score += min(0.15, momentum_ratio * 0.02)

        result.direction_vote = 3.5
        result.confidence = min(alignment_score, 1.0)
        result.entry_price = current_price
        result.stop_loss = darvas.bottom
        risk = current_price - darvas.bottom
        result.take_profit_1 = round(current_price + risk * rr_tp1, 6)
        result.take_profit_2 = round(current_price + risk * rr_tp2, 6)
        result.description = f"LONG signal: {', '.join(components_aligned)}"

    elif short_condition:
        result.signal = "short"
        alignment_score += 0.25
        components_aligned.append("Darvas breakdown")

        if wae.trend_down > wae.explosion_line * 1.5:
            alignment_score += 0.2
            components_aligned.append("Strong WAE explosion")
        else:
            alignment_score += 0.1
            components_aligned.append("WAE explosion")

        if ema_cloud.slow_ema > ema_cloud.fast_ema * 1.01:
            alignment_score += 0.2
            components_aligned.append("Strong EMA downtrend")
        else:
            alignment_score += 0.1
            components_aligned.append("EMA downtrend")

        if ha.ha_close < ha.ha_open:
            alignment_score += 0.15
            components_aligned.append("HA bearish")

        if wae.trend_down > 0 and wae.dead_zone > 0:
            momentum_ratio = wae.trend_down / max(wae.dead_zone, 0.001)
            alignment_score += min(0.15, momentum_ratio * 0.02)

        result.direction_vote = -3.5
        result.confidence = min(alignment_score, 1.0)
        result.entry_price = current_price
        result.stop_loss = darvas.top
        risk = darvas.top - current_price
        result.take_profit_1 = round(current_price - risk * rr_tp1, 6)
        result.take_profit_2 = round(current_price - risk * rr_tp2, 6)
        result.description = f"SHORT signal: {', '.join(components_aligned)}"

    else:
        # No clear signal — check for partial alignment
        partial = []
        if darvas.is_green:
            partial.append("Darvas green")
        elif darvas.is_red:
            partial.append("Darvas red")
        if ema_cloud.is_green:
            partial.append("EMA green")
        elif ema_cloud.is_red:
            partial.append("EMA red")
        if wae.is_exploding_up:
            partial.append("WAE up")
        elif wae.is_exploding_down:
            partial.append("WAE down")

        result.description = f"Neutral — partial signals: {', '.join(partial) if partial else 'none'}"

    # Store indicators for chart rendering (Pro+ users)
    result.indicators = {
        "darvas_top": darvas.top,
        "darvas_bottom": darvas.bottom,
        "ema_fast": ema_cloud.fast_ema,
        "ema_slow": ema_cloud.slow_ema,
        "ema_cloud_color": "green" if ema_cloud.is_green else "red" if ema_cloud.is_red else "neutral",
        "wae_trend_up": wae.trend_up,
        "wae_trend_down": wae.trend_down,
        "wae_explosion": wae.explosion_line,
        "wae_dead_zone": wae.dead_zone,
        "ha_open": ha.ha_open,
        "ha_close": ha.ha_close,
        "ha_high": ha.ha_high,
        "ha_low": ha.ha_low,
        "tp1": result.take_profit_1,
        "tp2": result.take_profit_2,
        "sl": result.stop_loss,
    }

    return result
