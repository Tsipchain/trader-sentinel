"""
Connects to a user's CEX account via CCXT (read-only API keys) and
fetches account/trade information.
"""
import time
from typing import Any

import ccxt.async_support as ccxt


def _build_exchange(exchange: str, api_key: str, api_secret: str, passphrase: str | None = None) -> ccxt.Exchange:
    ExchangeClass = getattr(ccxt, exchange, None)
    if ExchangeClass is None:
        raise ValueError(f"Unsupported exchange: '{exchange}'. Use a valid ccxt exchange id.")

    params: dict[str, Any] = {"apiKey": api_key, "secret": api_secret}
    if passphrase:
        params["password"] = passphrase
    return ExchangeClass(params)


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
    ex = _build_exchange(exchange, api_key, api_secret)
    try:
        since_ms = int((time.time() - days * 86400) * 1000)
        raw = await ex.fetch_my_trades(symbol, since=since_ms, limit=500)
        return _normalise(raw, symbol)
    finally:
        await ex.close()


async def fetch_exchange_snapshot(
    exchange: str,
    api_key: str,
    api_secret: str,
    passphrase: str | None = None,
) -> dict[str, Any]:
    """Fetch portfolio snapshot (balances/positions/margin) for AutoTrader UX."""
    ex = _build_exchange(exchange, api_key, api_secret, passphrase)
    try:
        bal = await ex.fetch_balance()
        positions: list[dict[str, Any]] = []
        if ex.has.get("fetchPositions"):
            try:
                positions = await ex.fetch_positions()
            except Exception:
                positions = []

        totals = bal.get("total") or {}
        frees = bal.get("free") or {}
        useds = bal.get("used") or {}

        balances = []
        for asset, total in totals.items():
            total_f = float(total or 0)
            if total_f <= 0:
                continue
            balances.append({
                "asset": str(asset),
                "total": total_f,
                "free": float(frees.get(asset) or 0),
                "used": float(useds.get(asset) or 0),
            })

        used_margin = float(useds.get("USDT") or bal.get("usedMargin") or 0)
        equity = float(
            totals.get("USDT")
            or (bal.get("USDT") or {}).get("total")
            or sum(b.get("total", 0) for b in balances)
            or 0
        )

        normalized_positions = []
        max_lev_by_symbol: dict[str, float] = {}
        for pos in positions:
            symbol = str(pos.get("symbol") or "")
            contracts = float(pos.get("contracts") or pos.get("positionAmt") or 0)
            if not symbol or contracts == 0:
                continue
            leverage = float(pos.get("leverage") or 0)
            normalized_positions.append({
                "symbol": symbol,
                "side": pos.get("side") or ("long" if contracts > 0 else "short"),
                "contracts": contracts,
                "entryPrice": float(pos.get("entryPrice") or 0),
                "markPrice": float(pos.get("markPrice") or 0),
                "unrealizedPnl": float(pos.get("unrealizedPnl") or 0),
                "leverage": leverage,
                "marginMode": pos.get("marginMode") or pos.get("marginType") or "",
            })
            if leverage > (max_lev_by_symbol.get(symbol) or 0):
                max_lev_by_symbol[symbol] = leverage

        return {
            "equity": equity,
            "balances": balances,
            "positions": normalized_positions,
            "usedMargin": used_margin,
            "maxLeverageBySymbol": max_lev_by_symbol,
            "ts": time.time(),
        }
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
