import time
from dataclasses import dataclass

import httpx


@dataclass
class DexTick:
    venue: str
    kind: str  # "dex"
    last: float | None
    pair: str | None
    chain: str | None
    dex: str | None
    liquidity_usd: float | None
    ts: int


class DexScreenerProvider:
    BASE = "https://api.dexscreener.com/latest/dex"

    def __init__(self, enabled: bool = True, timeout_s: int = 10, cache_ttl_s: int = 20):
        self.enabled = enabled
        self.timeout_s = timeout_s
        self.cache_ttl_s = max(1, int(cache_ttl_s))
        self._client = httpx.AsyncClient(timeout=timeout_s)
        self._cache: dict[str, tuple[float, DexTick]] = {}
        self._cooldown_until: float = 0

    async def close(self):
        try:
            await self._client.aclose()
        except Exception:
            pass

    async def snapshot(self, symbol: str) -> DexTick | None:
        """Best-effort DEX price using DexScreener search.

        For BTC/USDT this might return WBTC pairs depending on what's liquid.
        """
        if not self.enabled:
            return None

        q = _dex_query(symbol)
        ts = int(time.time())
        now = time.time()

        cached = self._cache.get(symbol)
        if cached and (now - cached[0] < self.cache_ttl_s):
            return cached[1]

        if now < self._cooldown_until:
            if cached:
                return cached[1]
            return DexTick(venue="dexscreener", kind="dex", last=None, pair=None, chain=None, dex=None, liquidity_usd=None, ts=ts)

        try:
            r = await self._client.get(f"{self.BASE}/search", params={"q": q})
            r.raise_for_status()
            data = r.json()
            pairs = data.get("pairs") or []
            if not pairs:
                return DexTick(venue="dexscreener", kind="dex", last=None, pair=None, chain=None, dex=None, liquidity_usd=None, ts=ts)

            # Filter to pairs whose base/quote token symbols match the requested symbol
            # so a search for "ETH USDT" doesn't return a random low-cap token.
            base_sym, quote_sym = _split_symbol(symbol)
            matched = [
                p for p in pairs
                if (p.get("baseToken", {}).get("symbol", "").upper() == base_sym
                    and p.get("quoteToken", {}).get("symbol", "").upper() == quote_sym)
            ]
            # Fall back to unfiltered list if nothing matched (e.g. WETH instead of ETH)
            candidates = matched if matched else pairs

            # pick most liquid
            best = max(candidates, key=lambda p: _to_float((p.get("liquidity") or {}).get("usd")) or 0)
            price = _to_float(best.get("priceUsd"))
            liq = _to_float((best.get("liquidity") or {}).get("usd"))
            pair = best.get("pairAddress") or best.get("url")
            chain = best.get("chainId")
            dex = best.get("dexId")

            tick = DexTick(venue="dexscreener", kind="dex", last=price, pair=pair, chain=chain, dex=dex, liquidity_usd=liq, ts=ts)
            self._cache[symbol] = (now, tick)
            return tick
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                self._cooldown_until = now + 60
            if cached:
                return cached[1]
            return DexTick(venue="dexscreener", kind="dex", last=None, pair=None, chain=None, dex=None, liquidity_usd=None, ts=ts)
        except Exception:
            if cached:
                return cached[1]
            return DexTick(venue="dexscreener", kind="dex", last=None, pair=None, chain=None, dex=None, liquidity_usd=None, ts=ts)


def _dex_query(symbol: str) -> str:
    # "BTC/USDT" -> "BTC USDT"
    return symbol.replace("/", " ").strip()


def _split_symbol(symbol: str) -> tuple[str, str]:
    """Return (base, quote) uppercased. 'ETH/USDT' -> ('ETH', 'USDT'), 'ETH' -> ('ETH', 'USDT')."""
    if "/" in symbol:
        base, quote = symbol.split("/", 1)
        return base.upper(), quote.upper()
    return symbol.upper(), "USDT"


def _to_float(x):
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None
