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
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, Field
from starlette.status import HTTP_403_FORBIDDEN

from .connector import fetch_user_trades
from .predictor import PredictionEngine
from . import store

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

_engine = PredictionEngine()

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

class SyncRequest(BaseModel):
    user_id: str = Field(..., description="Unique user identifier (your choice)")
    exchange: str = Field(..., description="CCXT exchange id, e.g. 'binance', 'bybit', 'okx'")
    api_key: str = Field(..., description="Read-only API key from the exchange")
    api_secret: str = Field(..., description="Read-only API secret from the exchange")
    symbol: str = Field("BTC/USDT", description="Trading pair to analyse, e.g. 'ETH/USDT'")
    days: int = Field(30, ge=7, le=365, description="How many days of history to sync")


class PredictRequest(BaseModel):
    user_id: str
    rsi: float = Field(50.0, ge=0, le=100)
    atr_score: float = Field(5.0, ge=0, le=10)
    geo_score: float = Field(5.0, ge=0, le=10)
    calendar_score: float = Field(5.0, ge=0, le=10)


class FeedbackRequest(BaseModel):
    user_id: str
    features: list[float] = Field(..., description="Feature vector from the prediction that was acted upon")
    outcome: int = Field(..., ge=0, le=1, description="1 = profitable trade, 0 = loss")


class AutoTraderEnableRequest(BaseModel):
    user_id: str
    exchange: str
    api_key: str
    api_secret: str
    symbols: list[str] = Field(default=["BTC/USDT"])
    stop_loss_pct: float = Field(2.0, ge=0.1, le=50)
    take_profit_pct: float = Field(4.0, ge=0.1, le=100)
    max_position_pct: float = Field(10.0, ge=0.1, le=100)
    max_open_trades: int = Field(3, ge=1, le=20)


class AutoTraderDisableRequest(BaseModel):
    user_id: str


class AutoTraderCloseRequest(BaseModel):
    user_id: str
    trade_id: str


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


@app.post("/api/brain/sync")
async def sync_trades(req: SyncRequest, _: str = Security(verify_api_key)):
    """
    Fetch closed trade history from the user's exchange, train (or retrain)
    their personal MLP model, and persist matched trade records to the volume.
    Uses read-only credentials — never places orders.
    """
    try:
        trades = await fetch_user_trades(
            exchange=req.exchange,
            api_key=req.api_key,
            api_secret=req.api_secret,
            symbol=req.symbol,
            days=req.days,
        )
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        raise HTTPException(502, detail=f"Exchange error: {e}")

    if not trades:
        raise HTTPException(400, detail="No trades found for this pair/period. Try a longer window or different symbol.")

    # Train ML model → saves .pkl to /disckb/models/
    result = _engine.train(req.user_id, trades)

    # Build matched pairs and persist to /disckb/history/
    records, hist_stats = _build_trade_records(trades, req.exchange)
    if records:
        store.save_history(req.user_id, records, hist_stats)

    return {"ok": True, **result}


@app.post("/api/brain/predict")
def predict(req: PredictRequest, _: str = Security(verify_api_key)):
    """
    Return a trade-outcome prediction for the given market conditions.
    Uses the user's personal model if trained, otherwise falls back to a
    market-signal heuristic.
    """
    result = _engine.predict(req.user_id, {
        "rsi":            req.rsi,
        "atr_score":      req.atr_score,
        "geo_score":      req.geo_score,
        "calendar_score": req.calendar_score,
    })
    return {"ok": True, **result}


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
    return {"ok": True, **model_stats, **hist_stats}


@app.get("/api/brain/history/{user_id}")
def get_history(user_id: str, limit: int = 50, _: str = Security(verify_api_key)):
    """Return the persisted matched trade records for a user (newest last)."""
    hist = store.load_history(user_id)
    if not hist:
        return {"ok": True, "trades": [], "stats": {}}
    trades = hist.get("trades", [])[-limit:]
    return {"ok": True, "trades": trades, "stats": hist.get("stats", {})}


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
def autotrader_enable(req: AutoTraderEnableRequest, _: str = Security(verify_api_key)):
    """
    Persist an AutoTrader session to the volume. Exchange credentials are stored
    inside the Railway volume file. Actual order execution is handled by the
    background worker process which polls this session config.
    """
    session = {
        "enabled":    True,
        "enabled_at": time.time(),
        "config": {
            "exchange":         req.exchange,
            "api_key":          req.api_key,
            "api_secret":       req.api_secret,
            "symbols":          req.symbols,
            "stop_loss_pct":    req.stop_loss_pct,
            "take_profit_pct":  req.take_profit_pct,
            "max_position_pct": req.max_position_pct,
            "max_open_trades":  req.max_open_trades,
        },
        "active_trades": [],
        "log": [
            f"AutoTrader enabled at {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}"
        ],
    }
    store.save_autotrader(req.user_id, session)
    return {"ok": True, "message": "AutoTrader enabled. Sentinel AI will monitor and execute trades."}


@app.post("/api/brain/autotrader/disable")
def autotrader_disable(req: AutoTraderDisableRequest, _: str = Security(verify_api_key)):
    """Disable AutoTrader. Open positions remain unchanged on the exchange."""
    session = store.load_autotrader(req.user_id)
    if session:
        session["enabled"] = False
        session.setdefault("log", []).append(
            f"AutoTrader disabled at {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}"
        )
        store.save_autotrader(req.user_id, session)
    return {"ok": True, "message": "AutoTrader disabled. Open positions remain on your exchange."}


@app.post("/api/brain/autotrader/close")
def autotrader_close(req: AutoTraderCloseRequest, _: str = Security(verify_api_key)):
    """
    Request market-close of a specific AutoTrader-managed trade.
    Removes the entry from the active trades list in the persisted session.
    """
    session = store.load_autotrader(req.user_id)
    if not session:
        raise HTTPException(404, detail="No AutoTrader session found for this user.")
    before = len(session.get("active_trades", []))
    session["active_trades"] = [
        t for t in session.get("active_trades", []) if t.get("id") != req.trade_id
    ]
    session.setdefault("log", []).append(
        f"Close requested for trade {req.trade_id} at {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}"
    )
    store.save_autotrader(req.user_id, session)
    return {"ok": True, "removed": before - len(session["active_trades"])}
