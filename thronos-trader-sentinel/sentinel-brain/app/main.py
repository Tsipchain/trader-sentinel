"""
Sentinel Brain — FastAPI service
Connects to user exchange accounts, trains a personal MLP on their trade history,
and serves adaptive price-direction predictions.
"""
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .connector import fetch_user_trades
from .predictor import PredictionEngine

_engine = PredictionEngine()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


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


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"ok": True, "ts": time.time(), "users_with_models": _engine.user_count()}


@app.post("/api/brain/sync")
async def sync_trades(req: SyncRequest):
    """
    Fetch closed trade history from the user's exchange and train (or retrain)
    their personal MLP model.  Uses read-only credentials — never places orders.
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

    result = _engine.train(req.user_id, trades)
    return {"ok": True, **result}


@app.post("/api/brain/predict")
def predict(req: PredictRequest):
    """
    Return a trade-outcome prediction for the given market conditions.
    Uses the user's personal model if trained, otherwise falls back to a
    market-signal heuristic.
    """
    result = _engine.predict(req.user_id, {
        "rsi": req.rsi,
        "atr_score": req.atr_score,
        "geo_score": req.geo_score,
        "calendar_score": req.calendar_score,
    })
    return {"ok": True, **result}


@app.post("/api/brain/feedback")
def feedback(req: FeedbackRequest):
    """
    Feed back the actual outcome of a trade to adapt the model online.
    Call this after every closed position for continuous personalisation.
    """
    _engine.adapt(req.user_id, req.features, req.outcome)
    return {"ok": True, "message": "Model updated with trade outcome."}


@app.get("/api/brain/stats/{user_id}")
def stats(user_id: str):
    """Diagnostics for a user's model: accuracy, trade count, win rate."""
    return {"ok": True, **_engine.stats(user_id)}
