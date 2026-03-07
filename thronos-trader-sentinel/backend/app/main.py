import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, Field
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

from app.brain.connector import fetch_exchange_snapshot, fetch_user_trades
from app.brain.predictor import PredictionEngine
from app.brain import store as brain_store

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Pytheia — Thronos Trader Sentinel", version="0.2.0")
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

# ── Brain engine (integrated) ────────────────────────────────────────────────
_brain_engine = PredictionEngine()
log.info("[brain] integrated — %d model(s) pre-loaded", _brain_engine.user_count())


def _exchange_availability() -> dict[str, dict[str, str | bool]]:
    blocked = {
        ex.strip().lower()
        for ex in os.getenv("EXCHANGE_BLOCKED", "binance,bybit").split(",")
        if ex.strip()
    }
    reason = "Execution not available from server region. Use OKX/MEXC or Local Executor."
    out: dict[str, dict[str, str | bool]] = {}
    for ex in ["binance", "bybit", "okx", "mexc"]:
        is_enabled = ex not in blocked
        out[ex] = {"enabled": is_enabled, "reason": "" if is_enabled else reason}
    return out


# ── Brain request / response models ──────────────────────────────────────────

class FeedbackRequest(BaseModel):
    user_id: str
    features: list[float] = Field(..., description="Feature vector from the prediction that was acted upon")
    outcome: int = Field(..., ge=0, le=1, description="1 = profitable trade, 0 = loss")


class AnalysisSnapshotRequest(BaseModel):
    user_id: str
    kind: str = Field(default="analyst", description="analysis type, e.g. analyst/briefing/risk")
    content: dict = Field(default_factory=dict)
    symbol: str = Field(default="BTC/USDT")


class SubscriptionRegisterRequest(BaseModel):
    user_id: str
    tier: str = Field(default="free")
    source: str = Field(default="mobile")
    wallet_address: str = Field(default="")


class SecurityEventRequest(BaseModel):
    user_id: str = Field(default="anonymous")
    event_type: str
    severity: str = Field(default="medium")
    source_ip: str = Field(default="")
    details: dict = Field(default_factory=dict)


class TelegramSignalRequest(BaseModel):
    user_id: str
    tier: str = Field(default="free")
    signal_type: str
    symbol: str
    message: str
    timestamp: int


@app.on_event("shutdown")
async def _shutdown():
    await _cex.close()
    await _dex.close()
    await tech_module.close_exchange_pool()


@app.get("/robots.txt", include_in_schema=False)
async def robots_txt():
    content = "User-agent: *\nDisallow: /\n"
    return Response(content=content, media_type="text/plain")


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "ts": int(time.time())}


async def _snapshot_data(symbol: str) -> dict[str, Any]:
    """Core snapshot logic shared by the snapshot and arb endpoints."""
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


@app.get("/api/market/snapshot")
@limiter.limit("60/minute")
async def market_snapshot(request: Request, symbol: str = Query(..., description="ccxt style, e.g. BTC/USDT"), _: str = Security(verify_api_key)) -> dict[str, Any]:
    return await _snapshot_data(symbol)


@app.get("/api/market/arb")
@limiter.limit("30/minute")
async def market_arb(request: Request, symbol: str = Query(...), _: str = Security(verify_api_key)) -> dict[str, Any]:
    snap = await _snapshot_data(symbol)
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
            payload = await _snapshot_data(symbol)
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
        # RSI
        "rsi_14": result.rsi_14,
        "rsi_signal": result.rsi_signal,
        # Volatility
        "volatility_score": result.volatility_score,
        # MACD (12/26/9)
        "macd": {
            "line": result.macd_line,
            "signal": result.macd_signal,
            "histogram": result.macd_histogram,
            "trend": result.macd_trend,
        },
        # Bollinger Bands (20, 2σ)
        "bollinger_bands": {
            "upper": result.bb_upper,
            "middle": result.bb_middle,
            "lower": result.bb_lower,
            "pct_b": result.bb_pct,
            "signal": result.bb_signal,
        },
        # EMA crossover
        "ema": {
            "ema_20": result.ema_20,
            "ema_50": result.ema_50,
            "cross": result.ema_cross,
        },
        # Williams %R (14)
        "williams_r": {
            "value": result.williams_r,
            "signal": result.williams_r_signal,
        },
        # Fibonacci
        "nearest_fib": result.nearest_fib,
        "fib_levels": result.fib_levels,
        # Cycle
        "cycle_deviation_pct": result.cycle_deviation,
        "error": result.error,
    }


# ── Brain Routes (integrated) ────────────────────────────────────────────────


def _build_trade_records(trades: list[dict], exchange: str) -> tuple[list[dict], dict]:
    """Match buy→sell pairs chronologically and return (records, stats)."""
    buys = [t for t in trades if t.get("side") == "buy"]
    sells = [t for t in trades if t.get("side") == "sell"]

    records: list[dict] = []
    pnl_list: list[float] = []
    symbol_count: dict[str, int] = {}

    for buy in buys:
        later_sells = [s for s in sells if s["ts"] > buy["ts"]]
        if not later_sells:
            continue
        sell = min(later_sells, key=lambda s: s["ts"])
        entry = buy["price"]
        exit_ = sell["price"]
        qty = buy["amount"]
        pnl_pct = (exit_ - entry) / entry * 100 if entry > 0 else 0.0
        pnl_usd = (exit_ - entry) * qty - buy.get("fee", 0.0) - sell.get("fee", 0.0)
        sym = buy["symbol"]

        records.append({
            "id": f"{buy['id']}-{sell['id']}",
            "symbol": sym,
            "side": "BUY",
            "entryPrice": round(entry, 8),
            "exitPrice": round(exit_, 8),
            "quantity": qty,
            "pnl": round(pnl_pct, 4),
            "pnlUsd": round(pnl_usd, 4),
            "openedAt": int(buy["ts"] * 1000),
            "closedAt": int(sell["ts"] * 1000),
            "exchange": exchange,
        })
        pnl_list.append(pnl_pct)
        symbol_count[sym] = symbol_count.get(sym, 0) + 1

    if not records:
        return records, {}

    wins = sum(1 for p in pnl_list if p > 0)
    most_traded = max(symbol_count, key=lambda s: symbol_count[s])
    hist_stats = {
        "total_trades": len(records),
        "win_rate": round(wins / len(records), 3),
        "total_pnl_usd": round(sum(r["pnlUsd"] for r in records), 4),
        "avg_pnl_pct": round(sum(pnl_list) / len(pnl_list), 4),
        "best_trade_pct": round(max(pnl_list), 4),
        "worst_trade_pct": round(min(pnl_list), 4),
        "most_traded_symbol": most_traded,
    }
    return records, hist_stats


@app.get("/api/brain/storage/status")
def brain_storage_status(_: str = Security(verify_api_key)):
    return {"ok": True, **brain_store.storage_status()}


@app.get("/api/brain/exchange/availability")
def brain_exchange_availability(_: str = Security(verify_api_key)):
    return {"ok": True, "exchanges": _exchange_availability()}


@app.post("/api/brain/exchange/snapshot")
async def brain_exchange_snapshot(payload: dict = Body(default_factory=dict), _: str = Security(verify_api_key)):
    exchange = (payload.get("exchange") or "").lower()
    api_key = payload.get("api_key") or payload.get("apiKey")
    api_secret = payload.get("api_secret") or payload.get("apiSecret")
    passphrase = payload.get("passphrase")
    availability = _exchange_availability()

    if not exchange or not api_key or not api_secret:
        return {"ok": False, "snapshot": None, "error": "Missing exchange credentials", "exchanges": availability}

    if exchange in availability and not bool(availability[exchange].get("enabled")):
        return {"ok": False, "snapshot": None, "error": availability[exchange].get("reason") or "Execution unavailable", "blocked": True, "exchanges": availability}

    try:
        snapshot = await fetch_exchange_snapshot(exchange, api_key, api_secret, passphrase)
        return {"ok": True, "snapshot": snapshot, "exchanges": availability}
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        msg = str(e)
        return {"ok": False, "snapshot": None, "error": msg, "blocked": "restricted" in msg.lower() or "forbidden" in msg.lower(), "exchanges": availability}


@app.post("/api/brain/sync")
async def brain_sync_trades(payload: dict = Body(default_factory=dict), _: str = Security(verify_api_key)):
    """Fetch closed trade history, train personal MLP model, persist matched trade records."""
    user_id = payload.get("user_id") or payload.get("userId") or "anonymous"
    exchange = payload.get("exchange")
    api_key = payload.get("api_key") or payload.get("apiKey")
    api_secret = payload.get("api_secret") or payload.get("apiSecret")
    symbol = payload.get("symbol") or "BTC/USDT"
    days = int(payload.get("days") or 30)

    if not exchange or not api_key or not api_secret:
        return {"ok": True, "trained": False, "trade_count": 0}

    try:
        trades = await fetch_user_trades(exchange=exchange, api_key=api_key, api_secret=api_secret, symbol=symbol, days=days)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        raise HTTPException(502, detail=f"Exchange error: {e}")

    if not trades:
        raise HTTPException(400, detail="No trades found for this pair/period. Try a longer window or different symbol.")

    result = _brain_engine.train(user_id, trades)
    records, hist_stats = _build_trade_records(trades, exchange)
    if records:
        brain_store.save_history(user_id, records, hist_stats)

    return {"ok": True, **result}


@app.post("/api/brain/predict")
def brain_predict(payload: dict = Body(default_factory=dict), _: str = Security(verify_api_key)):
    """Return trade-outcome prediction for given market conditions."""
    user_id = payload.get("user_id") or payload.get("userId") or "anonymous"
    rsi = float(payload.get("rsi") or 50.0)
    atr_score = float(payload.get("atr_score") or payload.get("atrScore") or 5.0)
    geo_score = float(payload.get("geo_score") or payload.get("geoScore") or 5.0)
    calendar_score = float(payload.get("calendar_score") or payload.get("calendarScore") or 5.0)

    try:
        result = _brain_engine.predict(user_id, {"rsi": rsi, "atr_score": atr_score, "geo_score": geo_score, "calendar_score": calendar_score})
        if user_id != "anonymous":
            brain_store.save_analysis_snapshot(user_id, {
                "kind": "brain_predict",
                "symbol": payload.get("symbol") or "BTC/USDT",
                "content": {"features": {"rsi": rsi, "atr_score": atr_score, "geo_score": geo_score, "calendar_score": calendar_score}, "result": result},
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        return {"ok": True, **result}
    except Exception:
        return {"ok": True, "prediction": "risky", "probability": 0.5, "confidence": "low", "model": "stub"}


@app.post("/api/brain/feedback")
def brain_feedback(req: FeedbackRequest, _: str = Security(verify_api_key)):
    """Feed back actual outcome of a trade to adapt the model online."""
    _brain_engine.adapt(req.user_id, req.features, req.outcome)
    return {"ok": True, "message": "Model updated with trade outcome."}


@app.get("/api/brain/stats/{user_id}")
def brain_stats(user_id: str, _: str = Security(verify_api_key)):
    model_stats = _brain_engine.stats(user_id)
    hist = brain_store.load_history(user_id)
    hist_stats = hist["stats"] if hist else {}
    defaults = {"total_trades": 0, "win_rate": 0.0, "total_pnl_usd": 0.0, "avg_pnl_pct": 0.0, "best_trade_pct": 0.0, "worst_trade_pct": 0.0, "most_traded_symbol": ""}
    return {"ok": True, **defaults, **model_stats, **hist_stats}


@app.get("/api/brain/history/{user_id}")
def brain_history(user_id: str, limit: int = 50, _: str = Security(verify_api_key)):
    hist = brain_store.load_history(user_id)
    if not hist:
        return {"ok": True, "trades": [], "stats": {}}
    trades = hist.get("trades", [])[-limit:]
    return {"ok": True, "trades": trades, "stats": hist.get("stats", {})}


@app.post("/api/brain/analysis/snapshot")
def brain_save_analysis(req: AnalysisSnapshotRequest, _: str = Security(verify_api_key)):
    snapshot = {"kind": req.kind, "symbol": req.symbol, "content": req.content, "created_at": datetime.now(timezone.utc).isoformat()}
    brain_store.save_analysis_snapshot(req.user_id, snapshot)
    return {"ok": True}


@app.get("/api/brain/analysis/{user_id}")
def brain_get_analysis(user_id: str, limit: int = 30, _: str = Security(verify_api_key)):
    memory = brain_store.load_analysis_memory(user_id)
    if not memory:
        return {"ok": True, "entries": [], "updated_at": None}
    entries = memory.get("entries", [])[-limit:]
    return {"ok": True, "entries": entries, "updated_at": memory.get("updated_at")}


@app.post("/api/brain/subscription/register")
def brain_register_subscription(req: SubscriptionRegisterRequest, _: str = Security(verify_api_key)):
    material = f"{req.user_id}|{req.tier}|{req.source}|{req.wallet_address}".lower()
    sub_hash = sha256(material.encode("utf-8")).hexdigest()
    payload = {"user_id": req.user_id, "tier": req.tier, "source": req.source, "wallet_address": req.wallet_address, "hash": sub_hash, "updated_at": datetime.now(timezone.utc).isoformat()}
    brain_store.save_subscription_fingerprint(sub_hash, payload)
    return {"ok": True, "hash": sub_hash}


@app.get("/api/brain/subscription/{sub_hash}")
def brain_get_subscription(sub_hash: str, _: str = Security(verify_api_key)):
    data = brain_store.load_subscription_fingerprint(sub_hash)
    if not data:
        return {"ok": False, "message": "not found"}
    return {"ok": True, "subscription": data}


@app.post("/api/brain/security/event")
def brain_security_event(req: SecurityEventRequest, _: str = Security(verify_api_key)):
    event = {"event_type": req.event_type, "severity": req.severity.lower(), "source_ip": req.source_ip, "details": req.details, "created_at": datetime.now(timezone.utc).isoformat()}
    brain_store.append_security_event(req.user_id, event)
    recent = brain_store.load_security_events(req.user_id, limit=25)
    high_count = sum(1 for e in recent if str(e.get("severity", "")).lower() in {"high", "critical"})
    action = "monitor"
    if high_count >= 3:
        action = "throttle"
    if high_count >= 6:
        action = "block_temporarily"
    return {"ok": True, "recommended_action": action, "recent_high_events": high_count}


@app.get("/api/brain/security/events/{user_id}")
def brain_security_events(user_id: str, limit: int = 50, _: str = Security(verify_api_key)):
    return {"ok": True, "events": brain_store.load_security_events(user_id, limit=limit)}


def _send_telegram_paid_signal(payload: TelegramSignalRequest) -> tuple[bool, str]:
    from urllib import parse, request as urllib_request
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_ids_raw = os.getenv("TELEGRAM_CHAT_IDS", "").strip()
    if not token or not chat_ids_raw:
        return False, "telegram_not_configured"
    tier = (payload.tier or "").lower()
    if tier == "free":
        return False, "free_tier_no_telegram"
    text = f"\U0001f514 {payload.signal_type.upper()} signal\nSymbol: {payload.symbol}\nTier: {payload.tier}\nUser: {payload.user_id}\n{payload.message[:800]}"
    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
    chat_ids = [c.strip() for c in chat_ids_raw.split(",") if c.strip()]
    sent = 0
    for chat_id in chat_ids:
        try:
            body = parse.urlencode({"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}).encode("utf-8")
            req = urllib_request.Request(endpoint, data=body, method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            with urllib_request.urlopen(req, timeout=6) as resp:
                if 200 <= getattr(resp, "status", 500) < 300:
                    sent += 1
        except Exception as exc:
            log.warning("telegram send failed for chat_id=%s: %s", chat_id, exc)
    if sent == 0:
        return False, "telegram_send_failed"
    return True, f"sent_to_{sent}_chat(s)"


@app.post("/api/brain/telegram/signal")
def brain_telegram_signal(req: TelegramSignalRequest, _: str = Security(verify_api_key)):
    ok, detail = _send_telegram_paid_signal(req)
    return {"ok": ok, "detail": detail}


# ── AutoTrader endpoints ──────────────────────────────────────────────────────

@app.get("/api/brain/autotrader/{user_id}")
def brain_autotrader_status(user_id: str, _: str = Security(verify_api_key)):
    session = brain_store.load_autotrader(user_id)
    if not session:
        return {"ok": True, "enabled": False, "active_trades": [], "log": []}
    return {"ok": True, "enabled": session.get("enabled", False), "config": session.get("config", {}), "active_trades": session.get("active_trades", []), "log": session.get("log", [])}


@app.post("/api/brain/autotrader/enable")
def brain_autotrader_enable(payload: dict = Body(default_factory=dict), _: str = Security(verify_api_key)):
    user_id = payload.get("user_id") or payload.get("userId")
    if not user_id:
        return {"ok": True}
    session = {
        "enabled": True,
        "enabled_at": time.time(),
        "config": {
            "exchange": payload.get("exchange", ""),
            "api_key": payload.get("api_key") or payload.get("apiKey") or "",
            "api_secret": payload.get("api_secret") or payload.get("apiSecret") or "",
            "passphrase": payload.get("passphrase") or "",
            "symbols": payload.get("symbols") or ["BTC/USDT"],
            "stop_loss_pct": float(payload.get("stop_loss_pct") or payload.get("stopLossPct") or 2.0),
            "take_profit_pct": float(payload.get("take_profit_pct") or payload.get("takeProfitPct") or 4.0),
            "max_position_pct": float(payload.get("max_position_pct") or payload.get("maxPositionPct") or 10.0),
            "max_open_trades": int(payload.get("max_open_trades") or payload.get("maxOpenTrades") or 3),
            "margin_mode": payload.get("margin_mode") or payload.get("marginMode") or "isolated",
            "max_leverage": float(payload.get("max_leverage") or payload.get("maxLeverage") or 3),
            "risk_per_trade_pct": float(payload.get("risk_per_trade_pct") or payload.get("riskPerTradePct") or 1),
            "max_total_exposure_pct": float(payload.get("max_total_exposure_pct") or payload.get("maxTotalExposurePct") or 25),
        },
        "active_trades": [],
        "log": [f"AutoTrader enabled at {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}"],
    }
    brain_store.save_autotrader(user_id, session)
    return {"ok": True}


@app.post("/api/brain/autotrader/disable")
def brain_autotrader_disable(payload: dict = Body(default_factory=dict), _: str = Security(verify_api_key)):
    user_id = payload.get("user_id") or payload.get("userId")
    if not user_id:
        return {"ok": True}
    session = brain_store.load_autotrader(user_id)
    if session:
        session["enabled"] = False
        session.setdefault("log", []).append(f"AutoTrader disabled at {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}")
        brain_store.save_autotrader(user_id, session)
    return {"ok": True}


@app.post("/api/brain/autotrader/close")
def brain_autotrader_close(payload: dict = Body(default_factory=dict), _: str = Security(verify_api_key)):
    user_id = payload.get("user_id") or payload.get("userId")
    trade_id = payload.get("trade_id") or payload.get("tradeId")
    if not user_id:
        return {"ok": True}
    session = brain_store.load_autotrader(user_id)
    if not session:
        return {"ok": True}
    session["active_trades"] = [t for t in session.get("active_trades", []) if t.get("id") != trade_id]
    session.setdefault("log", []).append(f"Close requested for trade {trade_id} at {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}")
    brain_store.save_autotrader(user_id, session)
    return {"ok": True}


@app.get("/api/tts")
async def tts(text: str = Query(...), lang: str = Query("en-US"), voice: str = Query("en-US-Neural2-D")):
    if not settings.google_tts_enabled:
        return {"ok": False, "error": "GOOGLE_TTS_ENABLED=false"}
    audio = g_tts(text=text, language=lang, voice=voice)
    return Response(content=audio, media_type="audio/mpeg")
