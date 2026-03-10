"""
Feature engineering for the prediction model.

Two feature spaces:
  trade_features(trade)   — time/side/size features from a historical trade
  market_features(signals) — normalised real-time market signals
"""
import math
from datetime import datetime, timezone
from typing import Any

TRADE_FEATURE_DIM = 4
MARKET_FEATURE_DIM = 4
TOTAL_FEATURE_DIM = TRADE_FEATURE_DIM + MARKET_FEATURE_DIM  # 8


def trade_features(trade: dict[str, Any]) -> list[float]:
    """Time- and size-based features extracted from one historical trade."""
    ts = trade.get("ts", 0)
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    side = 1.0 if trade.get("side") == "buy" else 0.0
    hour_norm = dt.hour / 23.0
    dow_norm = dt.weekday() / 6.0
    log_cost = math.log1p(trade.get("cost", 0)) / 15.0   # normalise log-scale cost
    return [side, hour_norm, dow_norm, log_cost]


def market_features(signals: dict[str, float]) -> list[float]:
    """Convert live market signals dict to a normalised feature vector."""
    rsi = float(signals.get("rsi", 50.0)) / 100.0
    atr = float(signals.get("atr_score", 5.0)) / 10.0
    geo = float(signals.get("geo_score", 5.0)) / 10.0
    cal = float(signals.get("calendar_score", 5.0)) / 10.0
    return [rsi, atr, geo, cal]


def build_dataset(
    trades: list[dict[str, Any]],
) -> tuple[list[list[float]], list[int]]:
    """
    Match buy→sell pairs chronologically and build (X, y).
    y = 1 if sell_price > buy_price (profitable round-trip), else 0.
    Only trade_features (no live market signals) — we don't have market
    data at historical trade times.
    """
    buys = [t for t in trades if t.get("side") == "buy"]
    sells = [t for t in trades if t.get("side") == "sell"]

    X: list[list[float]] = []
    y: list[int] = []

    for buy in buys:
        later_sells = [s for s in sells if s["ts"] > buy["ts"]]
        if not later_sells:
            continue
        sell = min(later_sells, key=lambda s: s["ts"])
        label = 1 if sell["price"] > buy["price"] else 0
        X.append(trade_features(buy))
        y.append(label)

    return X, y
