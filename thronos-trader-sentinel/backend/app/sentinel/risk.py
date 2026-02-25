"""
Composite Risk Aggregator — Francis-Monitor v1.0
-------------------------------------------------
Combines Calendar, Geopolitical, and Technical risk into a single
Early Warning score with actionable recommendations.

Weights (tunable via env):
    CALENDAR_WEIGHT  = 0.30  — historical event proximity
    GEO_WEIGHT       = 0.40  — live geopolitical news tension
    TECHNICAL_WEIGHT = 0.30  — RSI / volatility / fibonacci

Recommendation thresholds:
    0–3   → NEUTRAL      (normal market conditions)
    3–5   → WATCH        (elevated; monitor closely)
    5–7   → CAUTION      (multi-signal alignment; reduce risk)
    7–9   → DEFENSIVE    (strong signals; protect capital)
    9–10  → CRITICAL     (all signals aligned; maximum caution)
"""

from __future__ import annotations

import asyncio
import datetime
from dataclasses import dataclass
from typing import Any

from app.sentinel import calendar as cal_module
from app.sentinel import geo as geo_module
from app.sentinel import technicals as tech_module
from app.core.config import settings as app_settings


# ── Weights ────────────────────────────────────────────────────────────────────
CALENDAR_WEIGHT  = 0.30
GEO_WEIGHT       = 0.40
TECHNICAL_WEIGHT = 0.30

# ── Portfolio Recommendations ─────────────────────────────────────────────────
def _recommendation(score: float) -> dict[str, Any]:
    if score < 3.0:
        return {
            "level": "NEUTRAL",
            "description": "Normal market conditions. Standard allocation appropriate.",
            "action": "hold",
        }
    elif score < 5.0:
        return {
            "level": "WATCH",
            "description": "Elevated signals. Monitor geopolitical and technical developments.",
            "action": "monitor",
        }
    elif score < 7.0:
        return {
            "level": "CAUTION",
            "description": "Multiple signals aligning. Consider reducing speculative exposure.",
            "action": "reduce_risk",
        }
    elif score < 9.0:
        return {
            "level": "DEFENSIVE",
            "description": "Strong multi-factor alignment. Protect capital, increase hard-asset allocation.",
            "action": "defensive",
        }
    else:
        return {
            "level": "CRITICAL",
            "description": "All signals aligned. Maximum caution. Review all positions.",
            "action": "full_defensive",
        }


def _portfolio_guidance(score: float, tags: list[str]) -> list[dict]:
    """Generate asset-specific guidance based on score and active tags."""
    guidance = []

    has_energy  = any(t in tags for t in ["energy", "oil", "iran_region"])
    has_crypto  = any(t in tags for t in ["crypto", "exchange_risk", "stablecoin"])
    has_credit  = any(t in tags for t in ["credit", "banking", "systemic"])
    has_equity  = any(t in tags for t in ["equity", "crash", "seasonal"])

    if score >= 5.0 and has_energy:
        guidance.append({
            "asset": "Gold / Silver",
            "action": "accumulate",
            "note": "Energy tension historically correlates with hard-asset inflows.",
        })
        guidance.append({
            "asset": "Energy equities (XOM, CVX, BP)",
            "action": "watch_long",
            "note": "Supply-side risk may drive energy equity premiums.",
        })

    if score >= 6.0 and has_crypto:
        guidance.append({
            "asset": "Crypto (BTC/ETH/SOL/XRP)",
            "action": "reduce_leverage",
            "note": "Exchange or stablecoin risk signals active. Avoid leveraged positions.",
        })

    if score >= 7.0:
        guidance.append({
            "asset": "Cash / USD",
            "action": "increase",
            "note": "High composite score: maintain dry powder for post-event entries.",
        })

    if score >= 5.0 and has_equity:
        guidance.append({
            "asset": "Equities",
            "action": "hedge",
            "note": "Historical crash window or seasonal risk active. Consider protective puts.",
        })

    if score < 3.0:
        guidance.append({
            "asset": "All",
            "action": "hold",
            "note": "No significant risk signals detected.",
        })

    return guidance


@dataclass
class RiskReport:
    composite_score: float
    calendar_score: float
    geo_score: float
    technical_score: float
    recommendation: dict
    portfolio_guidance: list[dict]
    alerts: list[str]
    calendar_detail: dict
    geo_detail: dict
    technical_detail: dict
    ts: int


async def generate_report(symbol: str = "BTC/USDT") -> RiskReport:
    # Run all three modules concurrently
    cal_result, geo_result, tech_result = await asyncio.gather(
        asyncio.get_event_loop().run_in_executor(None, cal_module.calculate),
        geo_module.calculate(),
        tech_module.calculate(symbol),
    )

    # ── Composite Score ────────────────────────────────────────────────────────
    composite = round(
        cal_result.score  * CALENDAR_WEIGHT +
        geo_result.score  * GEO_WEIGHT +
        tech_result.score * TECHNICAL_WEIGHT,
        2
    )

    # ── Alerts ─────────────────────────────────────────────────────────────────
    alerts: list[str] = []

    if cal_result.score >= 6.0:
        labels = [e["label"] for e in cal_result.active_events[:3]]
        alerts.append(f"Calendar: High-risk window — {', '.join(labels)}")
    elif cal_result.active_events:
        labels = [e["label"] for e in cal_result.active_events[:2]]
        alerts.append(f"Calendar: Active event window — {', '.join(labels)}")

    if cal_result.nearest_event and cal_result.nearest_days is not None and cal_result.nearest_days <= 14:
        alerts.append(
            f"Upcoming: '{cal_result.nearest_event}' in {cal_result.nearest_days} day(s)"
        )

    if geo_result.score >= 5.0:
        alerts.append(f"Geopolitical: Elevated tension (score {geo_result.score}/10) — "
                      f"top keywords: {', '.join(geo_result.top_keywords_hit[:4])}")
    elif geo_result.error:
        alerts.append(f"Geopolitical: Feed unavailable ({geo_result.error})")

    if tech_result.rsi_14 and tech_result.rsi_14 >= 75:
        alerts.append(f"{symbol} RSI={tech_result.rsi_14} — overbought territory")
    elif tech_result.rsi_14 and tech_result.rsi_14 <= 25:
        alerts.append(f"{symbol} RSI={tech_result.rsi_14} — oversold territory")

    if tech_result.volatility_score >= 7.0:
        alerts.append(f"{symbol} ATR volatility elevated (score {tech_result.volatility_score}/10)")

    if tech_result.nearest_fib and tech_result.nearest_fib["distance_pct"] < 1.0:
        fib = tech_result.nearest_fib
        alerts.append(
            f"{symbol} within {fib['distance_pct']}% of Fibonacci {fib['ratio']} level "
            f"(${fib['price']:,.2f})"
        )

    # Merge tags for portfolio guidance
    all_tags = list(cal_result.tags_active) + list(geo_result.top_keywords_hit)

    return RiskReport(
        composite_score=composite,
        calendar_score=cal_result.score,
        geo_score=geo_result.score,
        technical_score=tech_result.score,
        recommendation=_recommendation(composite),
        portfolio_guidance=_portfolio_guidance(composite, all_tags),
        alerts=alerts,
        calendar_detail={
            "active_events": cal_result.active_events,
            "nearest_event": cal_result.nearest_event,
            "nearest_days": cal_result.nearest_days,
            "tags": cal_result.tags_active,
        },
        geo_detail={
            "top_headlines": geo_result.headlines_scored,
            "top_keywords": geo_result.top_keywords_hit,
            "total_checked": geo_result.total_headlines_checked,
            "cached": geo_result.cached,
        },
        technical_detail={
            "current_price": tech_result.current_price,
            "rsi_14": tech_result.rsi_14,
            "rsi_signal": tech_result.rsi_signal,
            "volatility_score": tech_result.volatility_score,
            "nearest_fib": tech_result.nearest_fib,
            "cycle_deviation_pct": tech_result.cycle_deviation,
            "fib_levels": tech_result.fib_levels,
        },
        ts=int(datetime.datetime.utcnow().timestamp()),
    )
