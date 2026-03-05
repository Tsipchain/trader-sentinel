import asyncio
import logging
import re
import time
from dataclasses import dataclass

import importlib
import importlib.util
import httpx

log = logging.getLogger(__name__)

_CCXT_MODULE = None


def _ccxt_module():
    global _CCXT_MODULE
    if _CCXT_MODULE is False:
        return None
    if _CCXT_MODULE is None:
        try:
            # find_spec can import package metadata and fail on partially broken installs.
            if importlib.util.find_spec("ccxt.async_support") is None:
                log.warning("ccxt.async_support unavailable; CEXProvider will run with empty CEX set")
                _CCXT_MODULE = False
                return None
            _CCXT_MODULE = importlib.import_module("ccxt.async_support")
        except ModuleNotFoundError as exc:
            log.warning("ccxt async support disabled due to missing dependency: %s", exc)
            _CCXT_MODULE = False
            return None
        except Exception as exc:
            log.warning("ccxt async support disabled due to import failure: %s", exc)
            _CCXT_MODULE = False
            return None
    return _CCXT_MODULE


@dataclass
class VenueTick:
    venue: str
    kind: str  # "cex"
    last: float | None
    bid: float | None
    ask: float | None
    ts: int

class CEXProvider:
    """CEX prices via ccxt.async_support. Public tickers only (no keys).

    Notes:
    - Different exchanges sometimes use different market symbols.
      We assume the user passes ccxt-style symbols (e.g. BTC/USDT).
    """

    def __init__(self, venues: list[str], min_interval_ms: int = 600):
        self.venues = [v.strip().lower() for v in venues if v.strip()]
        self.min_interval_ms = max(0, int(min_interval_ms))
        self._exchanges: dict[str, object] = {}
        self._last_fetch_ms: dict[str, int] = {}
        self._disabled_reason: dict[str, str] = {}

    async def start(self):
        ccxt = _ccxt_module()
        if ccxt is None:
            self._exchanges = {}
            return

        for v in self.venues:
            if v in self._exchanges:
                continue
            if not hasattr(ccxt, v):
                # Unknown in ccxt
                continue
            ex_class = getattr(ccxt, v)
            self._exchanges[v] = ex_class({
                "enableRateLimit": True,
                "timeout": 15_000,
            })

    async def close(self):
        for ex in self._exchanges.values():
            try:
                await ex.close()
            except Exception:
                pass

    async def _maybe_throttle(self, venue: str):
        if self.min_interval_ms <= 0:
            return
        now = int(time.time() * 1000)
        last = self._last_fetch_ms.get(venue, 0)
        wait = self.min_interval_ms - (now - last)
        if wait > 0:
            await asyncio.sleep(wait / 1000)

    async def fetch_ticker(self, venue: str, symbol: str) -> VenueTick:
        ts = int(time.time())
        if venue in self._disabled_reason:
            return VenueTick(venue=venue, kind="cex", last=None, bid=None, ask=None, ts=ts)

        ex = self._exchanges.get(venue)
        if not ex:
            return VenueTick(venue=venue, kind="cex", last=None, bid=None, ask=None, ts=ts)

        try:
            await self._maybe_throttle(venue)
            self._last_fetch_ms[venue] = int(time.time() * 1000)
            t = await ex.fetch_ticker(symbol)
            last = _to_float(t.get("last"))
            bid = _to_float(t.get("bid"))
            ask = _to_float(t.get("ask"))
            return VenueTick(venue=venue, kind="cex", last=last, bid=bid, ask=ask, ts=ts)
        except Exception as exc:
            if _is_geo_block(exc):
                reason = str(exc)
                self._disabled_reason[venue] = reason[:200]
                try:
                    await ex.close()
                except Exception:
                    pass
                self._exchanges.pop(venue, None)
                log.warning("CEX venue disabled due to geo block: %s (%s)", venue, self._disabled_reason[venue])
                return VenueTick(venue=venue, kind="cex", last=None, bid=None, ask=None, ts=ts)
            log.warning("CCXT ticker failed for %s/%s: %s", venue, symbol, exc)

        # CCXT failed — try OKX REST directly (geo-robust)
        if venue == "okx":
            try:
                last, bid, ask = await _okx_http_ticker(symbol)
                return VenueTick(venue="okx", kind="cex", last=last, bid=bid, ask=ask, ts=ts)
            except Exception as exc:
                log.warning("OKX HTTP ticker failed for %s: %s", symbol, exc)

        return VenueTick(venue=venue, kind="cex", last=None, bid=None, ask=None, ts=ts)

    async def snapshot(self, symbol: str) -> list[VenueTick]:
        try:
            await self.start()
        except Exception as exc:
            log.warning("CEXProvider start failed; returning empty snapshot: %s", exc)
            self._exchanges = {}
            return []

        tasks = [self.fetch_ticker(v, symbol) for v in self.venues if v in self._exchanges]
        if not tasks:
            return []
        return await asyncio.gather(*tasks)


async def _okx_http_ticker(symbol: str) -> tuple[float | None, float | None, float | None]:
    """Fetch last/bid/ask from OKX REST API without CCXT.
    Returns (last, bid, ask); raises on any failure.
    """
    inst_id = symbol.replace("/", "-") if "/" in symbol else f"{symbol}-USDT"
    url = f"https://www.okx.com/api/v5/market/ticker?instId={inst_id}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
    rows = data.get("data") or []
    if not rows:
        raise ValueError(f"OKX HTTP ticker: no data for {symbol}")
    row = rows[0]
    return _to_float(row.get("last")), _to_float(row.get("bidPx")), _to_float(row.get("askPx"))


def _to_float(x):
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def _is_geo_block(exc: Exception) -> bool:
    msg = str(exc).lower()
    patterns = [
        r"restricted location",
        r"block access from your country",
        r"cloudfront distribution is configured to block",
        r"service unavailable from a restricted location",
        r"\b451\b",
    ]
    return any(re.search(p, msg) for p in patterns)
