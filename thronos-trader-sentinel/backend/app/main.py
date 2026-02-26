import asyncio
import json
import logging
import os
import time
from typing import Any

from fastapi import FastAPI, Query, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response
from fastapi.security.api_key import APIKeyHeader
from starlette.status import HTTP_403_FORBIDDEN

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request

from app.core.config import settings
from app.providers.cex import CEXProvider
from app.providers.dex import DexScreenerProvider
from app.tts.google_tts import synthesize as g_tts
from app.sentinel import calendar as cal_module
from app.sentinel import geo as geo_module
from app.sentinel import technicals as tech_module
from app.sentinel import risk as risk_module

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Thronos Trader Sentinel", version="0.1.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── API Key Auth ──────────────────────────────────────────────────────────────
_API_KEY = os.getenv("API_KEY", "")
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(key: str = Security(_api_key_header)):
    if _API_KEY and key != _API_KEY:
        from fastapi import HTTPException as _HTTPException
        raise _HTTPException(status_code=HTTP_403_FORBIDDEN, detail="Invalid or missing API key")
    return key

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_cex = CEXProvider([v for v in settings.cex_venues.split(",")], settings.cex_min_interval_ms)
_dex = DexScreenerProvider(enabled=settings.dexscreener_enabled)


@app.on_event("shutdown")
async def _shutdown():
    await _cex.close()
    await _dex.close()


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "ts": int(time.time())}


@app.get("/api/market/snapshot")
@limiter.limit("60/minute")
async def market_snapshot(request: Request, symbol: str = Query(..., description="ccxt style, e.g. BTC/USDT"), _: str = Security(verify_api_key)) -> dict[str, Any]:
    ts = int(time.time())
    cex_ticks = await _cex.snapshot(symbol)
    dex_tick = await _dex.snapshot(symbol)

    venues: list[dict[str, Any]] = []
    for t in cex_ticks:
        venues.append({
            "venue": t.venue,
            "kind": t.kind,
            "last": t.last,
            "bid": t.bid,
            "ask": t.ask,
            "ts": t.ts,
        })
    if dex_tick:
        venues.append({
            "venue": dex_tick.venue,
            "kind": dex_tick.kind,
            "last": dex_tick.last,
            "pair": dex_tick.pair,
            "chain": dex_tick.chain,
            "dex": dex_tick.dex,
            "liquidity_usd": dex_tick.liquidity_usd,
            "ts": dex_tick.ts,
        })

    return {"ok": True, "symbol": symbol, "ts": ts, "venues": venues}


@app.get("/api/market/arb")
@limiter.limit("30/minute")
async def market_arb(request: Request, symbol: str = Query(...), _: str = Security(verify_api_key)) -> dict[str, Any]:
    snap = await market_snapshot(symbol)
    venues = snap.get("venues") or []

    best_bid = None
    best_bid_venue = None
    best_ask = None
    best_ask_venue = None
    dex_last = None

    for v in venues:
        if v.get("kind") == "dex":
            dex_last = v.get("last")
            continue
        bid = v.get("bid")
        ask = v.get("ask")
        if isinstance(bid, (int, float)):
            if best_bid is None or bid > best_bid:
                best_bid = bid
                best_bid_venue = v.get("venue")
        if isinstance(ask, (int, float)):
            if best_ask is None or ask < best_ask:
                best_ask = ask
                best_ask_venue = v.get("venue")

    spread = None
    if best_bid is not None and best_ask is not None:
        spread = best_bid - best_ask

    cex_vs_dex = None
    if dex_last is not None and best_ask is not None:
        cex_vs_dex = dex_last - best_ask

    return {
        "ok": True,
        "symbol": symbol,
        "best_bid": best_bid,
        "best_bid_venue": best_bid_venue,
        "best_ask": best_ask,
        "best_ask_venue": best_ask_venue,
        "spread": spread,
        "dex_last": dex_last,
        "dex_minus_best_ask": cex_vs_dex,
        "ts": int(time.time()),
    }


@app.get("/api/market/stream")
async def market_stream(symbol: str = Query(...), interval_ms: int = Query(1000, ge=250, le=60000), _: str = Security(verify_api_key)):
    async def gen():
        while True:
            payload = await market_snapshot(symbol)
            yield "event: snapshot\n"
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            await asyncio.sleep(interval_ms / 1000)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/sentinel/risk")
@limiter.limit("20/minute")
async def sentinel_risk(request: Request, symbol: str = Query("BTC/USDT"), _: str = Security(verify_api_key)) -> dict[str, Any]:
    """
    Francis-Monitor: Composite Early Warning Risk Report.

    Combines:
    - Calendar Risk  (historical market event proximity)
    - Geopolitical Risk (live news sentiment — Iran/Energy/Conflict)
    - Technical Risk (RSI, ATR volatility, Fibonacci levels)

    Returns composite score 0–10, recommendation level, alerts, and
    asset-specific portfolio guidance.
    """
    report = await risk_module.generate_report(symbol=symbol)
    return {
        "ok": True,
        "composite_score": report.composite_score,
        "recommendation": report.recommendation,
        "alerts": report.alerts,
        "portfolio_guidance": report.portfolio_guidance,
        "scores": {
            "calendar":   report.calendar_score,
            "geo":        report.geo_score,
            "technical":  report.technical_score,
        },
        "detail": {
            "calendar":   report.calendar_detail,
            "geo":        report.geo_detail,
            "technical":  report.technical_detail,
        },
        "ts": report.ts,
    }


@app.get("/api/sentinel/calendar")
async def sentinel_calendar(_: str = Security(verify_api_key)) -> dict[str, Any]:
    """Historical event calendar proximity — standalone endpoint."""
    result = cal_module.calculate()
    return {
        "ok": True,
        "score": result.score,
        "active_events": result.active_events,
        "nearest_event": result.nearest_event,
        "nearest_days": result.nearest_days,
        "tags_active": result.tags_active,
    }


@app.get("/api/sentinel/geo")
@limiter.limit("10/minute")
async def sentinel_geo(request: Request, _: str = Security(verify_api_key)) -> dict[str, Any]:
    """Live geopolitical news sentiment — standalone endpoint."""
    result = await geo_module.calculate()
    return {
        "ok": True,
        "score": result.score,
        "top_headlines": result.headlines_scored,
        "top_keywords": result.top_keywords_hit,
        "total_checked": result.total_headlines_checked,
        "nyt_used": result.nyt_used,
        "cached": result.cached,
        "error": result.error,
    }


@app.get("/api/sentinel/technicals")
@limiter.limit("20/minute")
async def sentinel_technicals(request: Request, symbol: str = Query("BTC/USDT"), _: str = Security(verify_api_key)) -> dict[str, Any]:
    """Technical analysis risk for a symbol — standalone endpoint."""
    result = await tech_module.calculate(symbol)
    return {
        "ok": True,
        "symbol": result.symbol,
        "score": result.score,
        "current_price": result.current_price,
        "rsi_14": result.rsi_14,
        "rsi_signal": result.rsi_signal,
        "volatility_score": result.volatility_score,
        "nearest_fib": result.nearest_fib,
        "fib_levels": result.fib_levels,
        "cycle_deviation_pct": result.cycle_deviation,
        "error": result.error,
    }


@app.get("/api/tts")
async def tts(text: str = Query(...), lang: str = Query("en-US"), voice: str = Query("en-US-Neural2-D")):
    if not settings.google_tts_enabled:
        return {"ok": False, "error": "GOOGLE_TTS_ENABLED=false"}
    audio = g_tts(text=text, language=lang, voice=voice)
    return Response(content=audio, media_type="audio/mpeg")
