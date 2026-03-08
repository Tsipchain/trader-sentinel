"""
Market Sessions Module — Global market open/close timing for trade intelligence.

Tracks major equity + futures market sessions worldwide.
Key insight: The first 15 minutes after a market opens ("opening range")
typically confirms the session's directional trend.

Sessions tracked:
- Asia/Pacific: Tokyo, Shanghai, Hong Kong, Sydney
- Europe: London, Frankfurt
- Americas: New York (NYSE/NASDAQ), CME (futures)
- Crypto: 24/7 but follows traditional session volume patterns

Used by:
- Sleep trader (optimal entry timing)
- Sentinel signals (session-aware alerts)
- Risk module (cross-session volatility)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import List

log = logging.getLogger(__name__)


@dataclass
class MarketSession:
    name: str
    region: str           # "asia", "europe", "americas"
    open_utc: tuple[int, int]   # (hour, minute) UTC
    close_utc: tuple[int, int]  # (hour, minute) UTC
    impact: str           # "high", "medium", "low"
    futures_linked: bool = True
    crypto_correlation: float = 0.5  # how much this session moves crypto
    tags: list[str] = field(default_factory=list)


# ── Major global market sessions (UTC times) ─────────────────────────────────
# Note: These are approximate — DST shifts ±1h seasonally
SESSIONS: list[MarketSession] = [
    # Asia/Pacific
    MarketSession("Tokyo (TSE/Nikkei)", "asia",
                  open_utc=(0, 0), close_utc=(6, 0),
                  impact="medium", crypto_correlation=0.35,
                  tags=["equity", "yen", "asia"]),
    MarketSession("Shanghai (SSE)", "asia",
                  open_utc=(1, 30), close_utc=(7, 0),
                  impact="medium", crypto_correlation=0.25,
                  tags=["equity", "yuan", "asia"]),
    MarketSession("Hong Kong (HKEX)", "asia",
                  open_utc=(1, 30), close_utc=(8, 0),
                  impact="medium", crypto_correlation=0.3,
                  tags=["equity", "hkd", "asia"]),
    MarketSession("Sydney (ASX)", "asia",
                  open_utc=(23, 0), close_utc=(5, 0),
                  impact="low", crypto_correlation=0.15,
                  tags=["equity", "aud", "asia"]),

    # Europe
    MarketSession("London (LSE/FTSE)", "europe",
                  open_utc=(8, 0), close_utc=(16, 30),
                  impact="high", crypto_correlation=0.55,
                  tags=["equity", "forex", "gbp", "europe"]),
    MarketSession("Frankfurt (DAX/Xetra)", "europe",
                  open_utc=(7, 0), close_utc=(15, 30),
                  impact="high", crypto_correlation=0.5,
                  tags=["equity", "eur", "europe"]),

    # Americas
    MarketSession("New York (NYSE/NASDAQ)", "americas",
                  open_utc=(14, 30), close_utc=(21, 0),
                  impact="high", crypto_correlation=0.75,
                  tags=["equity", "usd", "americas"]),
    MarketSession("CME Futures", "americas",
                  open_utc=(23, 0), close_utc=(22, 0),  # nearly 24h with breaks
                  impact="high", futures_linked=True, crypto_correlation=0.7,
                  tags=["futures", "usd", "americas"]),
    MarketSession("US Pre-Market", "americas",
                  open_utc=(9, 0), close_utc=(14, 30),
                  impact="medium", crypto_correlation=0.4,
                  tags=["premarket", "usd", "americas"]),
    MarketSession("US After-Hours", "americas",
                  open_utc=(21, 0), close_utc=(1, 0),
                  impact="medium", crypto_correlation=0.35,
                  tags=["afterhours", "usd", "americas"]),
]


# ── Key daily events that impact crypto ───────────────────────────────────────
@dataclass
class DailyEvent:
    name: str
    hour_utc: int
    minute_utc: int = 0
    impact: str = "medium"
    description: str = ""


DAILY_EVENTS: list[DailyEvent] = [
    DailyEvent("CME BTC Futures Open", 23, 0, "high",
               "CME gap fill / gap creation — watch first 15min for direction"),
    DailyEvent("NYSE Opening Bell", 14, 30, "high",
               "US equities open — crypto correlates within first 15min"),
    DailyEvent("London Open", 8, 0, "high",
               "EU session start — forex + crypto volume spike"),
    DailyEvent("US Daily Candle Close", 0, 0, "medium",
               "Daily candle close on most exchanges — key for TA"),
    DailyEvent("Tokyo Open", 0, 0, "medium",
               "Asian session start — often sets overnight range"),
    DailyEvent("NYSE Close", 21, 0, "medium",
               "US equities close — after-hours drift can move crypto"),
    DailyEvent("London Close", 16, 30, "medium",
               "EU session end — volume typically drops"),
    DailyEvent("Frankfurt Open", 7, 0, "medium",
               "European futures begin — can shift overnight range"),
]


@dataclass
class SessionStatus:
    """Current state of a market session."""
    session: MarketSession
    is_open: bool
    phase: str  # "pre_open", "opening_range", "mid_session", "closing", "closed"
    minutes_since_open: int
    minutes_to_close: int
    minutes_to_open: int  # if closed, how long until next open


@dataclass
class MarketSessionReport:
    """Full market sessions analysis."""
    timestamp: float
    utc_time: str
    active_sessions: list[dict]
    upcoming_events: list[dict]
    opening_range_active: list[dict]  # sessions in first 15 min
    closing_sessions: list[dict]      # sessions closing within 30 min
    session_overlap: str              # description of overlapping sessions
    trading_recommendation: str
    volume_expectation: str           # "low", "medium", "high", "peak"
    crypto_impact_score: float        # 0-10 aggregate session impact on crypto


def _is_session_open(session: MarketSession, now: datetime) -> bool:
    """Check if a session is currently open."""
    h, m = now.hour, now.minute
    current_minutes = h * 60 + m
    open_minutes = session.open_utc[0] * 60 + session.open_utc[1]
    close_minutes = session.close_utc[0] * 60 + session.close_utc[1]

    if open_minutes < close_minutes:
        # Normal session (e.g., 8:00 - 16:30)
        return open_minutes <= current_minutes < close_minutes
    else:
        # Overnight session (e.g., 23:00 - 22:00 for CME)
        return current_minutes >= open_minutes or current_minutes < close_minutes


def _minutes_since_open(session: MarketSession, now: datetime) -> int:
    current_minutes = now.hour * 60 + now.minute
    open_minutes = session.open_utc[0] * 60 + session.open_utc[1]

    if current_minutes >= open_minutes:
        return current_minutes - open_minutes
    else:
        # Overnight wrap
        return (24 * 60 - open_minutes) + current_minutes


def _minutes_to_close(session: MarketSession, now: datetime) -> int:
    current_minutes = now.hour * 60 + now.minute
    close_minutes = session.close_utc[0] * 60 + session.close_utc[1]

    if current_minutes < close_minutes:
        return close_minutes - current_minutes
    else:
        return (24 * 60 - current_minutes) + close_minutes


def _minutes_to_open(session: MarketSession, now: datetime) -> int:
    current_minutes = now.hour * 60 + now.minute
    open_minutes = session.open_utc[0] * 60 + session.open_utc[1]

    if current_minutes < open_minutes:
        return open_minutes - current_minutes
    else:
        return (24 * 60 - current_minutes) + open_minutes


def _get_phase(session: MarketSession, now: datetime) -> str:
    if not _is_session_open(session, now):
        mto = _minutes_to_open(session, now)
        if mto <= 30:
            return "pre_open"
        return "closed"

    mins = _minutes_since_open(session, now)
    mtc = _minutes_to_close(session, now)

    if mins <= 15:
        return "opening_range"
    elif mtc <= 30:
        return "closing"
    else:
        return "mid_session"


def calculate(now: datetime | None = None) -> MarketSessionReport:
    """Analyze current global market session state."""
    if now is None:
        now = datetime.now(timezone.utc)

    active: list[dict] = []
    opening_range: list[dict] = []
    closing: list[dict] = []
    total_crypto_impact = 0.0
    active_count = 0

    for session in SESSIONS:
        is_open = _is_session_open(session, now)
        phase = _get_phase(session, now)
        mins_open = _minutes_since_open(session, now) if is_open else 0
        mins_close = _minutes_to_close(session, now) if is_open else 0
        mins_to_open_val = _minutes_to_open(session, now) if not is_open else 0

        entry = {
            "name": session.name,
            "region": session.region,
            "is_open": is_open,
            "phase": phase,
            "minutes_since_open": mins_open,
            "minutes_to_close": mins_close,
            "minutes_to_open": mins_to_open_val,
            "impact": session.impact,
            "crypto_correlation": session.crypto_correlation,
        }

        if is_open:
            active.append(entry)
            active_count += 1
            total_crypto_impact += session.crypto_correlation

        if phase == "opening_range":
            opening_range.append({
                **entry,
                "note": f"{session.name} in opening range ({mins_open}min) — first 15 minutes confirm session trend. Watch for direction.",
            })

        if phase == "closing":
            closing.append({
                **entry,
                "note": f"{session.name} closing in {mins_close}min — expect position squaring and potential reversals.",
            })

        if phase == "pre_open":
            active.append(entry)  # show pre-open sessions too

    # Upcoming events in next 2 hours
    upcoming: list[dict] = []
    for event in DAILY_EVENTS:
        event_minutes = event.hour_utc * 60 + event.minute_utc
        current_minutes = now.hour * 60 + now.minute
        diff = event_minutes - current_minutes
        if diff < 0:
            diff += 24 * 60
        if diff <= 120:  # within 2 hours
            upcoming.append({
                "name": event.name,
                "in_minutes": diff,
                "impact": event.impact,
                "description": event.description,
            })
    upcoming.sort(key=lambda x: x["in_minutes"])

    # Session overlap analysis
    active_regions = set(s["region"] for s in active if s["is_open"])
    if "americas" in active_regions and "europe" in active_regions:
        overlap = "US-EU overlap — PEAK volume zone for crypto"
    elif "europe" in active_regions and "asia" in active_regions:
        overlap = "EU-Asia overlap — moderate volume transition"
    elif "americas" in active_regions:
        overlap = "US session dominant — high crypto correlation"
    elif "europe" in active_regions:
        overlap = "EU session active — moderate crypto activity"
    elif "asia" in active_regions:
        overlap = "Asian session — lower crypto volatility, range-bound typical"
    else:
        overlap = "Inter-session gap — low volume, wider spreads"

    # Volume expectation
    if "americas" in active_regions and "europe" in active_regions:
        volume = "peak"
    elif "americas" in active_regions:
        volume = "high"
    elif "europe" in active_regions:
        volume = "medium"
    elif active_count > 0:
        volume = "medium"
    else:
        volume = "low"

    # Crypto impact score (0-10)
    crypto_score = min(10.0, total_crypto_impact * 5)
    if opening_range:
        crypto_score = min(10.0, crypto_score + 2.0)  # opening ranges are high-impact
    if closing:
        crypto_score = min(10.0, crypto_score + 0.5)

    # Trading recommendation
    if opening_range:
        names = ", ".join(s["name"].split("(")[0].strip() for s in opening_range)
        recommendation = f"OPENING RANGE: {names} — wait for 15min candle close to confirm direction before entering. High-probability setup."
    elif volume == "peak":
        recommendation = "PEAK VOLUME: US-EU overlap active — best liquidity for entries/exits. Tighter spreads, faster fills."
    elif volume == "high":
        recommendation = "HIGH VOLUME: US session active — strong crypto correlation. Follow S&P/Nasdaq direction for bias."
    elif closing:
        names = ", ".join(s["name"].split("(")[0].strip() for s in closing)
        recommendation = f"SESSION CLOSING: {names} — expect profit-taking and potential mean-reversion moves."
    elif volume == "low":
        recommendation = "LOW VOLUME: Inter-session gap — wider spreads, avoid large entries. Wait for next major session open."
    else:
        recommendation = "MODERATE VOLUME: Standard conditions. Follow technical signals."

    return MarketSessionReport(
        timestamp=now.timestamp(),
        utc_time=now.strftime("%Y-%m-%d %H:%M UTC"),
        active_sessions=active,
        upcoming_events=upcoming,
        opening_range_active=opening_range,
        closing_sessions=closing,
        session_overlap=overlap,
        trading_recommendation=recommendation,
        volume_expectation=volume,
        crypto_impact_score=round(crypto_score, 1),
    )


def get_session_bias(now: datetime | None = None) -> dict:
    """Quick summary for the sleep trader — which direction do sessions favor?

    Returns:
        {
            "should_trade": bool,
            "volume": str,
            "opening_range": bool,
            "bias_note": str,
            "crypto_impact": float,
            "wait_minutes": int,  # 0 = trade now, >0 = wait for opening range
        }
    """
    report = calculate(now)

    # If opening range is active, signal to be cautious — wait for confirmation
    if report.opening_range_active:
        max_mins = max(s["minutes_since_open"] for s in report.opening_range_active)
        wait = max(0, 15 - max_mins)
        return {
            "should_trade": wait == 0,
            "volume": report.volume_expectation,
            "opening_range": True,
            "bias_note": report.trading_recommendation,
            "crypto_impact": report.crypto_impact_score,
            "wait_minutes": wait,
        }

    # Check if major session opens soon (within 20 min) — wait for it
    for event in report.upcoming_events:
        if event["impact"] == "high" and event["in_minutes"] <= 20:
            return {
                "should_trade": False,
                "volume": report.volume_expectation,
                "opening_range": False,
                "bias_note": f"Waiting for {event['name']} in {event['in_minutes']}min — {event['description']}",
                "crypto_impact": report.crypto_impact_score,
                "wait_minutes": event["in_minutes"],
            }

    return {
        "should_trade": report.volume_expectation != "low",
        "volume": report.volume_expectation,
        "opening_range": False,
        "bias_note": report.trading_recommendation,
        "crypto_impact": report.crypto_impact_score,
        "wait_minutes": 0,
    }
