"""
Connects to a user's CEX account via CCXT (read-only API keys) and
fetches their closed trade history for a given symbol.
"""
import time
from typing import Any

import ccxt.async_support as ccxt


async def fetch_user_trades(
    exchange: str,
    api_key: str,
    api_secret: str,
    symbol: str,
    days: int = 30,
) -> list[dict[str, Any]]:
    """
    Fetch closed trades for `symbol` from the user's exchange account.
    Uses read-only credentials — never places or cancels orders.
    """
    ExchangeClass = getattr(ccxt, exchange, None)
    if ExchangeClass is None:
        raise ValueError(f"Unsupported exchange: '{exchange}'. Use a valid ccxt exchange id.")

    ex: ccxt.Exchange = ExchangeClass({"apiKey": api_key, "secret": api_secret})
    try:
        since_ms = int((time.time() - days * 86400) * 1000)
        raw = await ex.fetch_my_trades(symbol, since=since_ms, limit=500)
        return _normalise(raw, symbol)
    finally:
        await ex.close()


def _normalise(raw: list, symbol: str) -> list[dict[str, Any]]:
    trades = []
    for t in raw:
        trades.append({
            "id": t.get("id"),
            "ts": (t.get("timestamp") or 0) / 1000,
            "side": t.get("side"),             # "buy" | "sell"
            "price": float(t.get("price") or 0),
            "amount": float(t.get("amount") or 0),
            "cost": float(t.get("cost") or 0),
            "fee": float((t.get("fee") or {}).get("cost") or 0),
            "symbol": symbol,
        })
    return sorted(trades, key=lambda x: x["ts"])
