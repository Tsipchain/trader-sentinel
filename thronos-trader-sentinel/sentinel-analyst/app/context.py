"""
Polls the backend risk/technicals endpoints on a schedule and builds
a structured text context that the LLM analyst can reason over.
"""
import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import httpx

log = logging.getLogger(__name__)

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8081")
POLL_INTERVAL_S = int(os.getenv("POLL_INTERVAL_S", "300"))  # 5 min
DEFAULT_SYMBOL = os.getenv("DEFAULT_SYMBOL", "BTC/USDT")


@dataclass
class MarketContext:
    ts: float
    symbol: str
    risk_score: float
    recommendation: str
    geo_score: float
    calendar_score: float
    tech_score: float
    alerts: list[str]
    top_headlines: list[str]
    rsi: Optional[float]
    atr_score: Optional[float]
    active_events: list[str]

    def to_text(self) -> str:
        alerts_text = "\n".join(f"  - {a}" for a in self.alerts[:6]) or "  None"
        headlines_text = "\n".join(f"  - {h}" for h in self.top_headlines[:5]) or "  None"
        events_text = "\n".join(f"  - {e}" for e in self.active_events[:4]) or "  None"
        rsi_str = f"{self.rsi:.1f}" if self.rsi is not None else "N/A"
        atr_str = f"{self.atr_score:.1f}/10" if self.atr_score is not None else "N/A"
        return (
            f"=== Thronos Market Intelligence — {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(self.ts))} ===\n"
            f"Symbol: {self.symbol}\n\n"
            f"COMPOSITE RISK SCORE: {self.risk_score:.1f}/10  →  {self.recommendation}\n\n"
            f"Component Scores:\n"
            f"  Geopolitical : {self.geo_score:.1f}/10\n"
            f"  Calendar     : {self.calendar_score:.1f}/10\n"
            f"  Technical    : {self.tech_score:.1f}/10\n\n"
            f"Technical Indicators:\n"
            f"  RSI (14)          : {rsi_str}\n"
            f"  ATR Volatility    : {atr_str}\n\n"
            f"Active Calendar Events:\n{events_text}\n\n"
            f"Active Alerts:\n{alerts_text}\n\n"
            f"Top Geopolitical Headlines:\n{headlines_text}"
        )


class ContextManager:
    def __init__(self) -> None:
        self._context: Optional[MarketContext] = None
        self._updated_at: float = 0

    def latest(self) -> Optional[str]:
        return self._context.to_text() if self._context else None

    def age_seconds(self) -> float:
        return -1 if self._updated_at == 0 else time.time() - self._updated_at

    async def _get_json(self, client: httpx.AsyncClient, url: str, params: dict[str, str]) -> Optional[dict]:
        api_key = os.getenv("API_KEY", "")
        headers = {"X-API-Key": api_key} if api_key else {}
        r = await client.get(url, params=params, headers=headers, timeout=20)

        if r.status_code != 200:
            log.warning("Backend request failed status=%s body=%s", r.status_code, r.text[:200])
            return None

        try:
            data = r.json()
        except Exception:
            log.warning("Non-JSON response %s: %s", r.status_code, r.text[:200])
            return None

        if not isinstance(data, dict):
            log.warning("Unexpected payload type: %s", type(data))
            return None
        return data

    async def refresh_once(self) -> None:
        async with httpx.AsyncClient(timeout=30) as client:
            risk, tech = await asyncio.gather(
                self._get_json(client, f"{BACKEND_URL}/api/sentinel/risk", {"symbol": DEFAULT_SYMBOL}),
                self._get_json(client, f"{BACKEND_URL}/api/sentinel/technicals", {"symbol": DEFAULT_SYMBOL}),
            )

        if risk is None or tech is None:
            log.warning("Skipping context refresh due to backend errors")
            return

        scores = risk.get("scores", {})
        detail = risk.get("detail", {})

        # Alerts — accept both string and dict shapes
        alerts = [
            a.get("message", str(a)) if isinstance(a, dict) else str(a)
            for a in risk.get("alerts", [])
        ]

        # Geo headlines
        headlines: list[str] = []
        for h in detail.get("geo", {}).get("top_headlines", [])[:5]:
            headlines.append(h.get("headline", h.get("title", str(h))) if isinstance(h, dict) else str(h))

        # Active calendar events
        active_events: list[str] = []
        for ev in detail.get("calendar", {}).get("active_events", [])[:4]:
            active_events.append(ev.get("label", ev.get("name", str(ev))) if isinstance(ev, dict) else str(ev))

        # Technical indicators — backend returns rsi_14 (float) and volatility_score (float) directly
        rsi: Optional[float] = None
        atr_score: Optional[float] = None
        if tech.get("ok"):
            rsi_raw = tech.get("rsi_14")
            if isinstance(rsi_raw, (int, float)):
                rsi = float(rsi_raw)
            vol_raw = tech.get("volatility_score")
            if isinstance(vol_raw, (int, float)):
                atr_score = float(vol_raw)

        self._context = MarketContext(
            ts=risk.get("ts", time.time()),
            symbol=DEFAULT_SYMBOL,
            risk_score=risk.get("composite_score", 0.0),
            recommendation=risk.get("recommendation", {}).get("level", "UNKNOWN"),
            geo_score=scores.get("geo", 0.0),
            calendar_score=scores.get("calendar", 0.0),
            tech_score=scores.get("technical", 0.0),
            alerts=alerts,
            top_headlines=headlines,
            rsi=rsi,
            atr_score=atr_score,
            active_events=active_events,
        )
        self._updated_at = time.time()
        log.info("[context] updated — risk=%.1f rec=%s", self._context.risk_score, self._context.recommendation)

    async def run_loop(self) -> None:
        await self.refresh_once()
        while True:
            try:
                await self.refresh_once()
            except Exception as exc:
                log.exception("[context] fetch error: %s", exc)
            await asyncio.sleep(POLL_INTERVAL_S)
