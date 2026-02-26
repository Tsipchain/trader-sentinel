"""
Sentinel LLM Analyst — FastAPI service
Polls the backend every POLL_INTERVAL_S seconds, builds a market context,
and exposes LLM-powered analysis endpoints.
"""
import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from starlette.status import HTTP_403_FORBIDDEN

from .analyst import ask_analyst, get_briefing
from .context import ContextManager

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ── API Key Auth ──────────────────────────────────────────────────────────────
_API_KEY = os.getenv("API_KEY", "")
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(key: str = Security(_api_key_header)):
    if _API_KEY and key != _API_KEY:
        from fastapi import HTTPException as _HTTPException
        raise _HTTPException(status_code=HTTP_403_FORBIDDEN, detail="Invalid or missing API key")
    return key

_ctx = ContextManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_ctx.run_loop())
    yield
    task.cancel()


app = FastAPI(
    title="Sentinel LLM Analyst",
    version="0.1.0",
    description="LLM-powered market intelligence layer for Thronos Trader Sentinel",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"ok": True, "ts": time.time(), "context_age_s": _ctx.age_seconds()}


@app.get("/api/analyst/briefing")
async def briefing(_: str = Security(verify_api_key)):
    """
    Returns a short LLM-generated trading briefing based on the latest
    cached market context (risk score, drivers, recommended action).
    """
    ctx = _ctx.latest()
    if not ctx:
        raise HTTPException(503, detail="Context not ready — backend poll pending")
    return await get_briefing(ctx)


@app.get("/api/analyst/ask")
async def ask(q: str = Query(..., description="Natural-language question about current market"), _: str = Security(verify_api_key)):
    """
    Ask any market-related question; Claude answers using the live context.
    Example: /api/analyst/ask?q=Should I long ETH right now?
    """
    ctx = _ctx.latest()
    if not ctx:
        raise HTTPException(503, detail="Context not ready — backend poll pending")
    return await ask_analyst(ctx, q)


@app.get("/api/analyst/context")
def raw_context(_: str = Security(verify_api_key)):
    """Return the raw text context used by the LLM (useful for debugging)."""
    return {"ok": True, "context": _ctx.latest(), "age_s": _ctx.age_seconds()}
