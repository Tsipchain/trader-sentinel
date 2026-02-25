"""
Calendar Risk Module
--------------------
Scores proximity to historically significant market / geopolitical dates.

Research basis:
- "October effect" & seasonal volatility studies (Bouman & Jacobsen 2002)
- Documented major market dislocations (Black Monday, Lehman, 9/11, Gulf War)
- Geopolitical event seasonality (oil-shock anniversaries, sanctions cycles)

Score: 0.0 (calm) → 10.0 (historically high-risk window)
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import List


@dataclass
class HistoricalEvent:
    label: str
    month: int          # 1-12
    day: int            # 1-31  (day of original event)
    window_days: int = 7  # ± days around anniversary to flag
    base_score: float = 5.0  # score contribution when in window
    tags: List[str] = field(default_factory=list)


# ── Historically documented market/geopolitical events ────────────────────────
# Sources: academic seasonality literature, Bloomberg event studies,
#          Federal Reserve historical data, NBER business cycle dates
EVENTS: list[HistoricalEvent] = [
    # ── Major equity crashes ─────────────────────────────────────────────────
    HistoricalEvent("Black Monday (1987)",       month=10, day=19, window_days=7,  base_score=7.0,
                    tags=["equity", "volatility", "crash"]),
    HistoricalEvent("Lehman Collapse (2008)",    month=9,  day=15, window_days=10, base_score=8.0,
                    tags=["credit", "equity", "systemic"]),
    HistoricalEvent("COVID Crash Low (2020)",    month=3,  day=20, window_days=14, base_score=6.0,
                    tags=["equity", "volatility", "macro"]),
    HistoricalEvent("Dot-com Peak (2000)",       month=3,  day=10, window_days=10, base_score=5.5,
                    tags=["tech", "equity"]),
    HistoricalEvent("Flash Crash (2010)",        month=5,  day=6,  window_days=5,  base_score=5.0,
                    tags=["equity", "algo", "liquidity"]),
    HistoricalEvent("SVB Collapse (2023)",       month=3,  day=10, window_days=7,  base_score=6.0,
                    tags=["banking", "credit", "crypto"]),

    # ── Oil / Energy shocks ──────────────────────────────────────────────────
    HistoricalEvent("1973 Oil Embargo Start",   month=10, day=17, window_days=10, base_score=6.5,
                    tags=["energy", "oil", "geopolitical"]),
    HistoricalEvent("Gulf War Oil Spike (1990)", month=8,  day=2,  window_days=10, base_score=6.0,
                    tags=["energy", "oil", "geopolitical", "iran_region"]),
    HistoricalEvent("Iran Nuclear Deal (2015)",  month=7,  day=14, window_days=7,  base_score=4.5,
                    tags=["geopolitical", "iran_region", "energy"]),
    HistoricalEvent("Soleimani Strike (2020)",   month=1,  day=3,  window_days=7,  base_score=7.0,
                    tags=["geopolitical", "iran_region", "energy", "oil"]),
    HistoricalEvent("Hormuz Tanker Crisis (2019)", month=6, day=13, window_days=14, base_score=5.5,
                    tags=["geopolitical", "iran_region", "energy", "oil"]),
    HistoricalEvent("Iran Sanctions Snap-Back (2018)", month=11, day=5, window_days=10, base_score=5.0,
                    tags=["geopolitical", "iran_region", "energy"]),

    # ── Crypto specific ──────────────────────────────────────────────────────
    HistoricalEvent("Mt. Gox Suspension (2014)", month=2,  day=7,  window_days=7,  base_score=5.0,
                    tags=["crypto", "exchange_risk"]),
    HistoricalEvent("FTX Collapse (2022)",        month=11, day=11, window_days=10, base_score=7.5,
                    tags=["crypto", "exchange_risk", "liquidity"]),
    HistoricalEvent("Luna/UST Collapse (2022)",   month=5,  day=9,  window_days=7,  base_score=7.0,
                    tags=["crypto", "stablecoin", "liquidity"]),
    HistoricalEvent("BTC Halving Season (Apr–May)", month=4, day=20, window_days=30, base_score=4.0,
                    tags=["crypto", "btc", "halving"]),

    # ── Seasonal / Calendar effects (academic) ───────────────────────────────
    # "Sell in May" effect — well-documented in academic literature
    HistoricalEvent("Sell-in-May Window Opens",  month=5,  day=1,  window_days=7,  base_score=3.5,
                    tags=["seasonal", "equity"]),
    # End-of-year tax-loss selling
    HistoricalEvent("Tax-Loss Selling Window",   month=12, day=15, window_days=14, base_score=3.5,
                    tags=["seasonal", "equity"]),
    # January effect reverse (post-rally exhaustion)
    HistoricalEvent("Post-January Exhaustion",   month=2,  day=10, window_days=10, base_score=3.0,
                    tags=["seasonal", "equity"]),

    # ── Geopolitical / Macro ─────────────────────────────────────────────────
    HistoricalEvent("9/11 Market Shock",          month=9,  day=11, window_days=5,  base_score=6.0,
                    tags=["geopolitical", "equity", "volatility"]),
    HistoricalEvent("Russia Ukraine Invasion (2022)", month=2, day=24, window_days=10, base_score=7.0,
                    tags=["geopolitical", "energy", "equity"]),
    HistoricalEvent("Bretton Woods Collapse (1971)", month=8, day=15, window_days=5, base_score=4.0,
                    tags=["macro", "currency"]),
]


@dataclass
class CalendarResult:
    score: float                    # 0–10
    active_events: list[dict]       # events currently in window
    nearest_event: str | None       # label of closest upcoming event
    nearest_days: int | None        # days until nearest event
    tags_active: list[str]          # deduplicated active tags


def _days_to_anniversary(event: HistoricalEvent, today: datetime.date) -> int:
    """Days until this year's anniversary of the event (0 if today is the day)."""
    try:
        anniversary = datetime.date(today.year, event.month, event.day)
    except ValueError:
        return 999  # e.g. Feb 29 in non-leap year

    diff = (anniversary - today).days
    if diff < 0:
        # Already passed this year — check next year
        try:
            anniversary = datetime.date(today.year + 1, event.month, event.day)
        except ValueError:
            return 999
        diff = (anniversary - today).days
    return diff


def _in_window(event: HistoricalEvent, today: datetime.date) -> bool:
    try:
        anniversary = datetime.date(today.year, event.month, event.day)
    except ValueError:
        return False
    return abs((today - anniversary).days) <= event.window_days


def calculate(today: datetime.date | None = None) -> CalendarResult:
    if today is None:
        today = datetime.date.today()

    active: list[dict] = []
    tags_set: set[str] = set()
    raw_score = 0.0

    for ev in EVENTS:
        if _in_window(ev, today):
            days_away = _days_to_anniversary(ev, today)
            # Proximity multiplier: max at anniversary, tapers at window edges
            try:
                anniversary = datetime.date(today.year, ev.month, ev.day)
            except ValueError:
                continue
            dist = abs((today - anniversary).days)
            proximity = 1.0 - (dist / (ev.window_days + 1))
            contribution = ev.base_score * proximity
            raw_score += contribution
            active.append({
                "label": ev.label,
                "score_contribution": round(contribution, 2),
                "days_from_anniversary": dist,
                "tags": ev.tags,
            })
            tags_set.update(ev.tags)

    # Normalise: cap at 10, non-linear scaling
    normalised = min(10.0, raw_score * 0.8) if raw_score > 0 else 0.0
    normalised = round(normalised, 2)

    # Nearest upcoming event
    upcoming = sorted(
        [(ev, _days_to_anniversary(ev, today)) for ev in EVENTS],
        key=lambda x: x[1]
    )
    nearest_ev, nearest_days = upcoming[0] if upcoming else (None, None)

    return CalendarResult(
        score=normalised,
        active_events=active,
        nearest_event=nearest_ev.label if nearest_ev else None,
        nearest_days=nearest_days,
        tags_active=sorted(tags_set),
    )
