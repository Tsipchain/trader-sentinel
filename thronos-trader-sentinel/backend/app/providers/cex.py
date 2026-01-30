import asyncio
import time
from dataclasses import dataclass

import ccxt.async_support as ccxt


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
        self._exchanges: dict[str, ccxt.Exchange] = {}
        self._last_fetch_ms: dict[str, int] = {}

    async def start(self):
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
        except Exception:
            return VenueTick(venue=venue, kind="cex", last=None, bid=None, ask=None, ts=ts)

    async def snapshot(self, symbol: str) -> list[VenueTick]:
        await self.start()
        tasks = [self.fetch_ticker(v, symbol) for v in self.venues if v in self._exchanges]
        if not tasks:
            return []
        return await asyncio.gather(*tasks)


def _to_float(x):
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None
