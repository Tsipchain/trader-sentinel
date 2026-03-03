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

    def __init__(self, enabled: bool = True, timeout_s: int = 10):
        self.enabled = enabled
        self.timeout_s = timeout_s
        self._client = httpx.AsyncClient(timeout=timeout_s)

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

            return DexTick(venue="dexscreener", kind="dex", last=price, pair=pair, chain=chain, dex=dex, liquidity_usd=liq, ts=ts)
        except Exception:
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
