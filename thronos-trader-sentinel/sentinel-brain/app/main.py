"""
Sentinel Brain — FastAPI service
Connects to user exchange accounts, trains a personal MLP on their trade history,
and serves adaptive price-direction predictions.

All persistent data (models, history, autotrader sessions) is written to the
Railway volume at DISK_PATH (default /disckb) via store.py.
"""
import logging
import os
import time
from datetime import datetime, timezone
from hashlib import sha256
from contextlib import asynccontextmanager
from urllib import parse, request

from fastapi import Body, FastAPI, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, Field
from starlette.status import HTTP_403_FORBIDDEN

from .connector import fetch_exchange_snapshot, fetch_user_trades
from .predictor import PredictionEngine
from . import store

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

_engine = PredictionEngine()


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
        out[ex] = {
            "enabled": is_enabled,
            "reason": "" if is_enabled else reason,
        }
    return out

# ── API Key Auth ──────────────────────────────────────────────────────────────
_API_KEY = os.getenv("API_KEY", "")
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(key: str = Security(_api_key_header)):
    if _API_KEY and key != _API_KEY:
        from fastapi import HTTPException as _HTTPException
        raise _HTTPException(status_code=HTTP_403_FORBIDDEN, detail="Invalid or missing API key")
    return key


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("[brain] starting up — %d model(s) pre-loaded", _engine.user_count())
    yield
    log.info("[brain] shutting down")


app = FastAPI(
    title="Sentinel Brain",
    version="0.1.0",
    description="Personal neural-network prediction engine for Thronos Trader Sentinel",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / Response models ─────────────────────────────────────────────────

class FeedbackRequest(BaseModel):
    user_id: str
    features: list[float] = Field(..., description="Feature vector from the prediction that was acted upon")
    outcome: int = Field(..., ge=0, le=1, description="1 = profitable trade, 0 = loss")


class AnalysisSnapshotRequest(BaseModel):
    user_id: str
    kind: str = Field(default="analyst", description="analysis type, e.g. analyst/briefing/risk")
    content: dict = Field(default_factory=dict, description="Arbitrary JSON payload to keep for comparison")
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


# ── Trade history helpers ─────────────────────────────────────────────────────

def _build_trade_records(trades: list[dict], exchange: str) -> tuple[list[dict], dict]:
    """
    Match buy→sell pairs chronologically and return (records, stats).
    Uses the same pairing logic as features.build_dataset so the data is
    consistent with what the ML model was trained on.
    """
    buys  = [t for t in trades if t.get("side") == "buy"]
    sells = [t for t in trades if t.get("side") == "sell"]

    records: list[dict] = []
    pnl_list: list[float] = []
    symbol_count: dict[str, int] = {}

    for buy in buys:
        later_sells = [s for s in sells if s["ts"] > buy["ts"]]
        if not later_sells:
            continue
        sell = min(later_sells, key=lambda s: s["ts"])

        entry   = buy["price"]
        exit_   = sell["price"]
        qty     = buy["amount"]
        pnl_pct = (exit_ - entry) / entry * 100 if entry > 0 else 0.0
        pnl_usd = (exit_ - entry) * qty - buy.get("fee", 0.0) - sell.get("fee", 0.0)
        sym     = buy["symbol"]

        records.append({
            "id":         f"{buy['id']}-{sell['id']}",
            "symbol":     sym,
            "side":       "BUY",
            "entryPrice": round(entry, 8),
            "exitPrice":  round(exit_, 8),
            "quantity":   qty,
            "pnl":        round(pnl_pct, 4),
            "pnlUsd":     round(pnl_usd, 4),
            "openedAt":   int(buy["ts"] * 1000),
            "closedAt":   int(sell["ts"] * 1000),
            "exchange":   exchange,
        })
        pnl_list.append(pnl_pct)
        symbol_count[sym] = symbol_count.get(sym, 0) + 1

    if not records:
        return records, {}

    wins        = sum(1 for p in pnl_list if p > 0)
    most_traded = max(symbol_count, key=lambda s: symbol_count[s])

    hist_stats = {
        "total_trades":       len(records),
        "win_rate":           round(wins / len(records), 3),
        "total_pnl_usd":      round(sum(r["pnlUsd"] for r in records), 4),
        "avg_pnl_pct":        round(sum(pnl_list) / len(pnl_list), 4),
        "best_trade_pct":     round(max(pnl_list), 4),
        "worst_trade_pct":    round(min(pnl_list), 4),
        "most_traded_symbol": most_traded,
    }
    return records, hist_stats


# ── Core endpoints ────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"ok": True, "ts": time.time(), "users_with_models": _engine.user_count()}


@app.get("/api/brain/storage/status")
def storage_status(_: str = Security(verify_api_key)):
    """Visibility endpoint to verify Railway volume persistence for Brain memory."""
    return {"ok": True, **store.storage_status()}


@app.get("/api/brain/exchange/availability")
def exchange_availability(_: str = Security(verify_api_key)):
    return {"ok": True, "exchanges": _exchange_availability()}


@app.post("/api/brain/exchange/snapshot")
async def exchange_snapshot(payload: dict = Body(default_factory=dict), _: str = Security(verify_api_key)):
    exchange = (payload.get("exchange") or "").lower()
    api_key = payload.get("api_key") or payload.get("apiKey")
    api_secret = payload.get("api_secret") or payload.get("apiSecret")
    passphrase = payload.get("passphrase")
    availability = _exchange_availability()

    if not exchange or not api_key or not api_secret:
        return {
            "ok": False,
            "snapshot": None,
            "error": "Missing exchange credentials",
            "exchanges": availability,
        }

    if exchange in availability and not bool(availability[exchange].get("enabled")):
        return {
            "ok": False,
            "snapshot": None,
            "error": availability[exchange].get("reason") or "Execution unavailable",
            "blocked": True,
            "exchanges": availability,
        }

    try:
        snapshot = await fetch_exchange_snapshot(exchange, api_key, api_secret, passphrase)
        return {"ok": True, "snapshot": snapshot, "exchanges": availability}
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        msg = str(e)
        return {
            "ok": False,
            "snapshot": None,
            "error": msg,
            "blocked": "restricted" in msg.lower() or "forbidden" in msg.lower(),
            "exchanges": availability,
        }


@app.post("/api/brain/sync")
async def sync_trades(payload: dict = Body(default_factory=dict), _: str = Security(verify_api_key)):
    """
    Fetch closed trade history from the user's exchange, train (or retrain)
    their personal MLP model, and persist matched trade records to the volume.
    Uses read-only credentials — never places orders.
    """
    user_id = payload.get("user_id") or payload.get("userId") or "anonymous"
    exchange = payload.get("exchange")
    api_key = payload.get("api_key") or payload.get("apiKey")
    api_secret = payload.get("api_secret") or payload.get("apiSecret")
    symbol = payload.get("symbol") or "BTC/USDT"
    days = int(payload.get("days") or 30)

    if not exchange or not api_key or not api_secret:
        return {"ok": True, "trained": False, "trade_count": 0}

    try:
        trades = await fetch_user_trades(
            exchange=exchange,
            api_key=api_key,
            api_secret=api_secret,
            symbol=symbol,
            days=days,
        )
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        raise HTTPException(502, detail=f"Exchange error: {e}")

    if not trades:
        raise HTTPException(400, detail="No trades found for this pair/period. Try a longer window or different symbol.")

    # Train ML model → saves .pkl to /disckb/models/
    result = _engine.train(user_id, trades)

    # Build matched pairs and persist to /disckb/history/
    records, hist_stats = _build_trade_records(trades, exchange)
    if records:
        store.save_history(user_id, records, hist_stats)

    return {"ok": True, **result}


@app.post("/api/brain/predict")
def predict(payload: dict = Body(default_factory=dict), _: str = Security(verify_api_key)):
    """
    Return a trade-outcome prediction for the given market conditions.
    Uses the user's personal model if trained, otherwise falls back to a
    market-signal heuristic.
    """
    user_id = payload.get("user_id") or payload.get("userId") or "anonymous"
    rsi = float(payload.get("rsi") or 50.0)
    atr_score = float(payload.get("atr_score") or payload.get("atrScore") or 5.0)
    geo_score = float(payload.get("geo_score") or payload.get("geoScore") or 5.0)
    calendar_score = float(payload.get("calendar_score") or payload.get("calendarScore") or 5.0)

    try:
        result = _engine.predict(user_id, {
            "rsi": rsi,
            "atr_score": atr_score,
            "geo_score": geo_score,
            "calendar_score": calendar_score,
        })
        if user_id != "anonymous":
            store.save_analysis_snapshot(user_id, {
                "kind": "brain_predict",
                "symbol": payload.get("symbol") or "BTC/USDT",
                "content": {
                    "features": {
                        "rsi": rsi,
                        "atr_score": atr_score,
                        "geo_score": geo_score,
                        "calendar_score": calendar_score,
                    },
                    "result": result,
                },
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        return {"ok": True, **result}
    except Exception:
        return {"ok": True, "prediction": "risky", "probability": 0.5, "confidence": "low", "model": "stub"}


@app.post("/api/brain/feedback")
def feedback(req: FeedbackRequest, _: str = Security(verify_api_key)):
    """
    Feed back the actual outcome of a trade to adapt the model online.
    Call this after every closed position for continuous personalisation.
    """
    _engine.adapt(req.user_id, req.features, req.outcome)
    return {"ok": True, "message": "Model updated with trade outcome."}


@app.get("/api/brain/stats/{user_id}")
def stats(user_id: str, _: str = Security(verify_api_key)):
    """
    Diagnostics for a user's model merged with aggregated trade statistics
    from the persisted history (total P&L, win rate, best/worst trade, etc.).
    """
    model_stats = _engine.stats(user_id)
    hist        = store.load_history(user_id)
    hist_stats  = hist["stats"] if hist else {}
    defaults = {
        "total_trades": 0,
        "win_rate": 0.0,
        "total_pnl_usd": 0.0,
        "avg_pnl_pct": 0.0,
        "best_trade_pct": 0.0,
        "worst_trade_pct": 0.0,
        "most_traded_symbol": "",
    }
    return {"ok": True, **defaults, **model_stats, **hist_stats}


@app.get("/api/brain/history/{user_id}")
def get_history(user_id: str, limit: int = 50, _: str = Security(verify_api_key)):
    """Return the persisted matched trade records for a user (newest last)."""
    hist = store.load_history(user_id)
    if not hist:
        return {"ok": True, "trades": [], "stats": {}}
    trades = hist.get("trades", [])[-limit:]
    return {"ok": True, "trades": trades, "stats": hist.get("stats", {})}


@app.post("/api/brain/analysis/snapshot")
def save_analysis_snapshot(req: AnalysisSnapshotRequest, _: str = Security(verify_api_key)):
    """Persist LLM analysis output per user for historical comparison and model review."""
    snapshot = {
        "kind": req.kind,
        "symbol": req.symbol,
        "content": req.content,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    store.save_analysis_snapshot(req.user_id, snapshot)
    return {"ok": True}


@app.get("/api/brain/analysis/{user_id}")
def get_analysis_history(user_id: str, limit: int = 30, _: str = Security(verify_api_key)):
    """Fetch persisted LLM analysis memory (newest last) for comparison across time."""
    memory = store.load_analysis_memory(user_id)
    if not memory:
        return {"ok": True, "entries": [], "updated_at": None}
    entries = memory.get("entries", [])[-limit:]
    return {"ok": True, "entries": entries, "updated_at": memory.get("updated_at")}


@app.post("/api/brain/subscription/register")
def register_subscription(req: SubscriptionRegisterRequest, _: str = Security(verify_api_key)):
    """Persist a deterministic subscription fingerprint so free/paid records are traceable."""
    material = f"{req.user_id}|{req.tier}|{req.source}|{req.wallet_address}".lower()
    sub_hash = sha256(material.encode("utf-8")).hexdigest()
    payload = {
        "user_id": req.user_id,
        "tier": req.tier,
        "source": req.source,
        "wallet_address": req.wallet_address,
        "hash": sub_hash,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    store.save_subscription_fingerprint(sub_hash, payload)
    return {"ok": True, "hash": sub_hash}


@app.get("/api/brain/subscription/{sub_hash}")
def get_subscription(sub_hash: str, _: str = Security(verify_api_key)):
    data = store.load_subscription_fingerprint(sub_hash)
    if not data:
        return {"ok": False, "message": "not found"}
    return {"ok": True, "subscription": data}


@app.post("/api/brain/security/event")
def record_security_event(req: SecurityEventRequest, _: str = Security(verify_api_key)):
    """Defensive security telemetry (detection + response hints). No offensive actions."""
    event = {
        "event_type": req.event_type,
        "severity": req.severity.lower(),
        "source_ip": req.source_ip,
        "details": req.details,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    store.append_security_event(req.user_id, event)
    recent = store.load_security_events(req.user_id, limit=25)
    high_count = sum(1 for e in recent if str(e.get("severity", "")).lower() in {"high", "critical"})
    action = "monitor"
    if high_count >= 3:
        action = "throttle"
    if high_count >= 6:
        action = "block_temporarily"
    return {"ok": True, "recommended_action": action, "recent_high_events": high_count}


@app.get("/api/brain/security/events/{user_id}")
def get_security_events(user_id: str, limit: int = 50, _: str = Security(verify_api_key)):
    return {"ok": True, "events": store.load_security_events(user_id, limit=limit)}


def _send_telegram_paid_signal(payload: TelegramSignalRequest) -> tuple[bool, str]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_ids_raw = os.getenv("TELEGRAM_CHAT_IDS", "").strip()
    if not token or not chat_ids_raw:
        return False, "telegram_not_configured"

    tier = (payload.tier or "").lower()
    if tier == "free":
        return False, "free_tier_no_telegram"

    text = (
        f"🔔 {payload.signal_type.upper()} signal\n"
        f"Symbol: {payload.symbol}\n"
        f"Tier: {payload.tier}\n"
        f"User: {payload.user_id}\n"
        f"{payload.message[:800]}"
    )
    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"

    chat_ids = [c.strip() for c in chat_ids_raw.split(",") if c.strip()]
    sent = 0
    for chat_id in chat_ids:
        try:
            body = parse.urlencode({
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": "true",
            }).encode("utf-8")
            req = request.Request(endpoint, data=body, method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            with request.urlopen(req, timeout=6) as resp:
                if 200 <= getattr(resp, "status", 500) < 300:
                    sent += 1
        except Exception as exc:
            log.warning("telegram send failed for chat_id=%s: %s", chat_id, exc)

    if sent == 0:
        return False, "telegram_send_failed"
    return True, f"sent_to_{sent}_chat(s)"


@app.post("/api/brain/telegram/signal")
def telegram_signal(req: TelegramSignalRequest, _: str = Security(verify_api_key)):
    ok, detail = _send_telegram_paid_signal(req)
    return {"ok": ok, "detail": detail}


# ── AutoTrader endpoints ──────────────────────────────────────────────────────

@app.get("/api/brain/autotrader/{user_id}")
def autotrader_status(user_id: str, _: str = Security(verify_api_key)):
    """Return AutoTrader session status and config for a user."""
    session = store.load_autotrader(user_id)
    if not session:
        return {"ok": True, "enabled": False, "active_trades": [], "log": []}
    return {
        "ok":            True,
        "enabled":       session.get("enabled", False),
        "config":        session.get("config", {}),
        "active_trades": session.get("active_trades", []),
        "log":           session.get("log", []),
    }


@app.post("/api/brain/autotrader/enable")
def autotrader_enable(payload: dict = Body(default_factory=dict), _: str = Security(verify_api_key)):
    """
    Persist an AutoTrader session to the volume. Exchange credentials are stored
    inside the Railway volume file. Actual order execution is handled by the
    background worker process which polls this session config.
    """
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
            "max_leverage": float(payload.get("max_leverage") or payload.get("maxLeverage") or payload.get("leverage") or 3),
            "risk_per_trade_pct": float(payload.get("risk_per_trade_pct") or payload.get("riskPerTradePct") or 1),
            "max_total_exposure_pct": float(payload.get("max_total_exposure_pct") or payload.get("maxTotalExposurePct") or 25),
        },
        "active_trades": [],
        "log": [f"AutoTrader enabled at {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}"],
    }
    store.save_autotrader(user_id, session)
    return {"ok": True}


@app.post("/api/brain/autotrader/disable")
def autotrader_disable(payload: dict = Body(default_factory=dict), _: str = Security(verify_api_key)):
    """Disable AutoTrader. Open positions remain unchanged on the exchange."""
    user_id = payload.get("user_id") or payload.get("userId")
    if not user_id:
        return {"ok": True}

    session = store.load_autotrader(user_id)
    if session:
        session["enabled"] = False
        session.setdefault("log", []).append(
            f"AutoTrader disabled at {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}"
        )
        store.save_autotrader(user_id, session)
    return {"ok": True}


@app.post("/api/brain/autotrader/close")
def autotrader_close(payload: dict = Body(default_factory=dict), _: str = Security(verify_api_key)):
    """
    Request market-close of a specific AutoTrader-managed trade.
    Removes the entry from the active trades list in the persisted session.
    """
    user_id = payload.get("user_id") or payload.get("userId")
    trade_id = payload.get("trade_id") or payload.get("tradeId")
    if not user_id:
        return {"ok": True}

    session = store.load_autotrader(user_id)
    if not session:
        return {"ok": True}

    session["active_trades"] = [
        t for t in session.get("active_trades", []) if t.get("id") != trade_id
    ]
    session.setdefault("log", []).append(
        f"Close requested for trade {trade_id} at {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}"
    )
    store.save_autotrader(user_id, session)
    return {"ok": True}
