import asyncio
import json
import time
from typing import Any

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response

from app.core.config import settings
from app.providers.cex import CEXProvider
from app.providers.dex import DexScreenerProvider
from app.tts.google_tts import synthesize as g_tts

app = FastAPI(title="Thronos Trader Sentinel", version="0.1.0")

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
async def market_snapshot(symbol: str = Query(..., description="ccxt style, e.g. BTC/USDT")) -> dict[str, Any]:
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
async def market_arb(symbol: str = Query(...)) -> dict[str, Any]:
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
async def market_stream(symbol: str = Query(...), interval_ms: int = Query(1000, ge=250, le=60000)):
    async def gen():
        while True:
            payload = await market_snapshot(symbol)
            yield "event: snapshot\n"
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            await asyncio.sleep(interval_ms / 1000)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/tts")
async def tts(text: str = Query(...), lang: str = Query("en-US"), voice: str = Query("en-US-Neural2-D")):
    if not settings.google_tts_enabled:
        return {"ok": False, "error": "GOOGLE_TTS_ENABLED=false"}
    audio = g_tts(text=text, language=lang, voice=voice)
    return Response(content=audio, media_type="audio/mpeg")
