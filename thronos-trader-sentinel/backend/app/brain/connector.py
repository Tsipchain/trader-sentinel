"""
Connects to a user's CEX account via CCXT (read-only API keys) and
fetches account/trade information.

Supports both spot and futures (swap) markets.
"""
import logging
import time
from typing import Any

import ccxt.async_support as ccxt

log = logging.getLogger(__name__)

# Futures symbols to scan when no specific symbol is given
DEFAULT_FUTURES_SYMBOLS = [
    "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
    "BNB/USDT:USDT", "XRP/USDT:USDT", "DOGE/USDT:USDT",
    "ADA/USDT:USDT", "AVAX/USDT:USDT", "LINK/USDT:USDT",
    "DOT/USDT:USDT", "MATIC/USDT:USDT", "LTC/USDT:USDT",
    "UNI/USDT:USDT", "NEAR/USDT:USDT", "FIL/USDT:USDT",
    "ARB/USDT:USDT", "OP/USDT:USDT", "APT/USDT:USDT",
    "SUI/USDT:USDT", "PEPE/USDT:USDT", "WIF/USDT:USDT",
    "SHIB/USDT:USDT", "ATOM/USDT:USDT", "TRX/USDT:USDT",
]

DEFAULT_SPOT_SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT",
    "XRP/USDT", "DOGE/USDT", "ADA/USDT", "AVAX/USDT",
]


def _build_exchange(
    exchange: str,
    api_key: str,
    api_secret: str,
    passphrase: str | None = None,
    market_type: str = "spot",
) -> ccxt.Exchange:
    ExchangeClass = getattr(ccxt, exchange, None)
    if ExchangeClass is None:
        raise ValueError(f"Unsupported exchange: '{exchange}'. Use a valid ccxt exchange id.")

    params: dict[str, Any] = {"apiKey": api_key, "secret": api_secret}
    if passphrase:
        params["password"] = passphrase

    if market_type in ("futures", "swap"):
        params["options"] = {"defaultType": "swap"}

    return ExchangeClass(params)


async def fetch_user_trades(
    exchange: str,
    api_key: str,
    api_secret: str,
    symbol: str = "BTC/USDT",
    days: int = 30,
    market_type: str = "auto",
) -> list[dict[str, Any]]:
    """
    Fetch closed trades from the user's exchange account.
    Uses read-only credentials — never places or cancels orders.

    market_type:
      - "auto"    → try futures first, then spot (default)
      - "futures"  → only futures/swap
      - "spot"     → only spot
    """
    since_ms = int((time.time() - days * 86400) * 1000)
    all_trades: list[dict[str, Any]] = []

    if market_type in ("auto", "futures"):
        futures_trades = await _fetch_trades_for_type(
            exchange, api_key, api_secret, symbol, since_ms, "futures",
        )
        all_trades.extend(futures_trades)
        log.info("[connector] %s futures trades fetched from %s", len(futures_trades), exchange)

    if market_type in ("auto", "spot"):
        # Skip spot if we already got plenty from futures
        if market_type == "auto" and len(all_trades) >= 10:
            log.info("[connector] skipping spot — enough futures trades found")
        else:
            spot_trades = await _fetch_trades_for_type(
                exchange, api_key, api_secret, symbol, since_ms, "spot",
            )
            all_trades.extend(spot_trades)
            log.info("[connector] %s spot trades fetched from %s", len(spot_trades), exchange)

    # Deduplicate by trade id
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for t in all_trades:
        tid = str(t.get("id", ""))
        if tid and tid in seen:
            continue
        if tid:
            seen.add(tid)
        unique.append(t)

    return sorted(unique, key=lambda x: x["ts"])


async def _fetch_trades_for_type(
    exchange: str,
    api_key: str,
    api_secret: str,
    symbol: str,
    since_ms: int,
    market_type: str,
) -> list[dict[str, Any]]:
    """Fetch trades for a specific market type, scanning multiple symbols if needed."""
    ex = _build_exchange(exchange, api_key, api_secret, market_type=market_type)
    trades: list[dict[str, Any]] = []

    try:
        # Determine which symbols to scan
        if symbol and symbol != "BTC/USDT":
            # User specified a symbol — adapt it for the market type
            symbols_to_try = [_adapt_symbol(symbol, market_type)]
        else:
            # Scan common symbols
            symbols_to_try = DEFAULT_FUTURES_SYMBOLS if market_type == "futures" else DEFAULT_SPOT_SYMBOLS

        for sym in symbols_to_try:
            try:
                raw = await ex.fetch_my_trades(sym, since=since_ms, limit=500)
                if raw:
                    trades.extend(_normalise(raw, sym))
                    log.info("[connector] %s: %d trades for %s", market_type, len(raw), sym)
            except ccxt.BadSymbol:
                continue
            except ccxt.NotSupported:
                continue
            except Exception as e:
                err_msg = str(e).lower()
                if "symbol" in err_msg or "not found" in err_msg or "invalid" in err_msg:
                    continue
                log.warning("[connector] %s %s error for %s: %s", exchange, market_type, sym, e)
                continue

    finally:
        await ex.close()

    return trades


def _adapt_symbol(symbol: str, market_type: str) -> str:
    """Convert a symbol to the correct format for the market type."""
    if market_type == "futures":
        # BTC/USDT → BTC/USDT:USDT for futures
        if ":USDT" not in symbol:
            return f"{symbol}:USDT"
    else:
        # BTC/USDT:USDT → BTC/USDT for spot
        if ":USDT" in symbol:
            return symbol.replace(":USDT", "")
    return symbol


async def fetch_exchange_snapshot(
    exchange: str,
    api_key: str,
    api_secret: str,
    passphrase: str | None = None,
) -> dict[str, Any]:
    """Fetch portfolio snapshot (balances/positions/margin) for AutoTrader UX."""
    # Try futures first for margin/positions, then spot for balances
    futures_data = await _fetch_snapshot_for_type(exchange, api_key, api_secret, passphrase, "futures")
    spot_data = await _fetch_snapshot_for_type(exchange, api_key, api_secret, passphrase, "spot")

    # Merge: use futures positions + equity, add spot balances
    positions = futures_data.get("positions", [])
    futures_balances = futures_data.get("balances", [])
    spot_balances = spot_data.get("balances", [])

    # Combine balances (prefer futures equity if available)
    all_balance_map: dict[str, dict] = {}
    for b in spot_balances:
        all_balance_map[b["asset"]] = b
    for b in futures_balances:
        asset = b["asset"]
        if asset in all_balance_map:
            all_balance_map[asset]["total"] += b["total"]
            all_balance_map[asset]["free"] += b["free"]
            all_balance_map[asset]["used"] += b["used"]
        else:
            all_balance_map[asset] = b

    equity = futures_data.get("equity", 0) + spot_data.get("equity", 0)
    used_margin = futures_data.get("usedMargin", 0)

    return {
        "equity": equity,
        "balances": list(all_balance_map.values()),
        "positions": positions,
        "usedMargin": used_margin,
        "maxLeverageBySymbol": futures_data.get("maxLeverageBySymbol", {}),
        "ts": time.time(),
    }


async def _fetch_snapshot_for_type(
    exchange: str,
    api_key: str,
    api_secret: str,
    passphrase: str | None,
    market_type: str,
) -> dict[str, Any]:
    """Fetch snapshot for a specific market type."""
    ex = _build_exchange(exchange, api_key, api_secret, passphrase, market_type)
    try:
        bal = await ex.fetch_balance()
        positions: list[dict[str, Any]] = []
        if market_type == "futures" and ex.has.get("fetchPositions"):
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
                "contracts": abs(contracts),
                "entryPrice": float(pos.get("entryPrice") or 0),
                "markPrice": float(pos.get("markPrice") or 0),
                "unrealizedPnl": float(pos.get("unrealizedPnl") or 0),
                "leverage": leverage,
                "marginMode": pos.get("marginMode") or pos.get("marginType") or "",
                "liquidationPrice": float(pos.get("liquidationPrice") or 0),
                "notional": float(pos.get("notional") or 0),
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
    except Exception as e:
        log.warning("[connector] snapshot error for %s %s: %s", exchange, market_type, e)
        return {"equity": 0, "balances": [], "positions": [], "usedMargin": 0, "maxLeverageBySymbol": {}, "ts": time.time()}
    finally:
        await ex.close()


async def fetch_open_positions(
    exchange: str,
    api_key: str,
    api_secret: str,
    passphrase: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch currently open futures positions with live PnL.

    If the exchange doesn't return mark prices in positions, we fetch
    tickers to get the current price.
    """
    ex = _build_exchange(exchange, api_key, api_secret, passphrase, market_type="futures")
    try:
        positions: list[dict[str, Any]] = []
        if not ex.has.get("fetchPositions"):
            return positions

        raw_positions = await ex.fetch_positions()

        # Filter to only active positions first
        active_raw = []
        for pos in raw_positions:
            symbol = str(pos.get("symbol") or "")
            contracts = float(pos.get("contracts") or pos.get("positionAmt") or 0)
            if symbol and contracts != 0:
                active_raw.append(pos)

        if not active_raw:
            return positions

        # Fetch live prices for positions missing mark price
        symbols_needing_price = set()
        for pos in active_raw:
            mark = float(pos.get("markPrice") or 0)
            if mark <= 0:
                symbols_needing_price.add(str(pos.get("symbol", "")))

        live_prices: dict[str, float] = {}
        if symbols_needing_price:
            for sym in symbols_needing_price:
                try:
                    ticker = await ex.fetch_ticker(sym)
                    live_prices[sym] = float(ticker.get("last") or ticker.get("close") or 0)
                except Exception:
                    live_prices[sym] = 0.0

        for pos in active_raw:
            symbol = str(pos.get("symbol") or "")
            contracts = float(pos.get("contracts") or pos.get("positionAmt") or 0)
            entry_price = float(pos.get("entryPrice") or 0)
            mark_price = float(pos.get("markPrice") or 0)

            # Use live ticker price if mark price is missing
            if mark_price <= 0:
                mark_price = live_prices.get(symbol, 0.0)

            unrealized_pnl = float(pos.get("unrealizedPnl") or 0)
            leverage = float(pos.get("leverage") or 0)

            # Determine side — MEXC uses 'long'/'short', some exchanges use positive/negative contracts
            side = pos.get("side")
            if not side or side not in ("long", "short"):
                side = "long" if contracts > 0 else "short"

            abs_contracts = abs(contracts)
            notional = float(pos.get("notional") or 0)
            if notional <= 0:
                notional = abs_contracts * mark_price

            # Calculate PnL if exchange didn't provide it
            if unrealized_pnl == 0 and entry_price > 0 and mark_price > 0:
                if side == "long":
                    unrealized_pnl = (mark_price - entry_price) * abs_contracts
                else:
                    unrealized_pnl = (entry_price - mark_price) * abs_contracts

            # Calculate PnL percentage
            if entry_price > 0 and mark_price > 0:
                if side == "long":
                    pnl_pct = ((mark_price - entry_price) / entry_price) * 100 * leverage if leverage > 0 else ((mark_price - entry_price) / entry_price) * 100
                else:
                    pnl_pct = ((entry_price - mark_price) / entry_price) * 100 * leverage if leverage > 0 else ((entry_price - mark_price) / entry_price) * 100
            else:
                pnl_pct = 0.0

            positions.append({
                "symbol": symbol,
                "side": side,
                "contracts": abs_contracts,
                "entryPrice": entry_price,
                "markPrice": mark_price,
                "unrealizedPnl": round(unrealized_pnl, 6),
                "pnlPct": round(pnl_pct, 2),
                "leverage": leverage,
                "marginMode": pos.get("marginMode") or pos.get("marginType") or "",
                "liquidationPrice": float(pos.get("liquidationPrice") or 0),
                "notional": round(notional, 4),
                "timestamp": pos.get("timestamp") or int(time.time() * 1000),
            })

        return positions
    finally:
        await ex.close()


def _normalise(raw: list, symbol: str) -> list[dict[str, Any]]:
    trades = []
    for t in raw:
        # Use the actual symbol from the trade if available
        trade_symbol = t.get("symbol") or symbol
        trades.append({
            "id": t.get("id"),
            "ts": (t.get("timestamp") or 0) / 1000,
            "side": t.get("side"),             # "buy" | "sell"
            "price": float(t.get("price") or 0),
            "amount": float(t.get("amount") or 0),
            "cost": float(t.get("cost") or 0),
            "fee": float((t.get("fee") or {}).get("cost") or 0),
            "symbol": trade_symbol,
        })
    return sorted(trades, key=lambda x: x["ts"])


# ── Trade Execution (AutoTrader / Sleep Mode) ────────────────────────────────


async def set_leverage(
    exchange: str,
    api_key: str,
    api_secret: str,
    symbol: str,
    leverage: int,
    passphrase: str | None = None,
) -> bool:
    """Set leverage for a futures symbol. Returns True on success."""
    ex = _build_exchange(exchange, api_key, api_secret, passphrase, market_type="futures")
    try:
        futures_symbol = _adapt_symbol(symbol, "futures")
        await ex.set_leverage(leverage, futures_symbol)
        log.info("[exec] set leverage %dx for %s on %s", leverage, futures_symbol, exchange)
        return True
    except Exception as e:
        log.warning("[exec] set_leverage failed for %s: %s", symbol, e)
        return False
    finally:
        await ex.close()


async def create_market_order(
    exchange: str,
    api_key: str,
    api_secret: str,
    symbol: str,
    side: str,
    amount: float,
    passphrase: str | None = None,
    reduce_only: bool = False,
) -> dict[str, Any]:
    """Place a market order on futures. Returns order info."""
    ex = _build_exchange(exchange, api_key, api_secret, passphrase, market_type="futures")
    try:
        futures_symbol = _adapt_symbol(symbol, "futures")
        params: dict[str, Any] = {}
        if reduce_only:
            params["reduceOnly"] = True

        order = await ex.create_order(
            symbol=futures_symbol,
            type="market",
            side=side,  # "buy" or "sell"
            amount=amount,
            params=params,
        )
        log.info("[exec] market %s %s %.6f on %s → %s", side, futures_symbol, amount, exchange, order.get("id"))
        return {
            "ok": True,
            "id": order.get("id"),
            "symbol": futures_symbol,
            "side": side,
            "amount": amount,
            "price": float(order.get("average") or order.get("price") or 0),
            "cost": float(order.get("cost") or 0),
            "status": order.get("status"),
            "timestamp": order.get("timestamp"),
        }
    except Exception as e:
        log.error("[exec] market order failed %s %s: %s", side, symbol, e)
        return {"ok": False, "error": str(e)}
    finally:
        await ex.close()


async def create_stop_loss(
    exchange: str,
    api_key: str,
    api_secret: str,
    symbol: str,
    side: str,
    amount: float,
    stop_price: float,
    passphrase: str | None = None,
) -> dict[str, Any]:
    """Place a stop-loss order. side should be opposite to the position (sell for long, buy for short)."""
    ex = _build_exchange(exchange, api_key, api_secret, passphrase, market_type="futures")
    try:
        futures_symbol = _adapt_symbol(symbol, "futures")
        params: dict[str, Any] = {"stopPrice": stop_price, "reduceOnly": True}

        # MEXC and some exchanges use stop_market type
        order_type = "stop_market"
        try:
            order = await ex.create_order(
                symbol=futures_symbol,
                type=order_type,
                side=side,
                amount=amount,
                params=params,
            )
        except (ccxt.NotSupported, ccxt.InvalidOrder):
            # Fallback: try as regular stop order
            order = await ex.create_order(
                symbol=futures_symbol,
                type="stop",
                side=side,
                amount=amount,
                price=stop_price,
                params={"reduceOnly": True, "triggerPrice": stop_price},
            )

        log.info("[exec] SL %s %s @ %.6f on %s → %s", side, futures_symbol, stop_price, exchange, order.get("id"))
        return {"ok": True, "id": order.get("id"), "stop_price": stop_price}
    except Exception as e:
        log.warning("[exec] SL failed %s %s: %s", side, symbol, e)
        return {"ok": False, "error": str(e)}
    finally:
        await ex.close()


async def create_take_profit(
    exchange: str,
    api_key: str,
    api_secret: str,
    symbol: str,
    side: str,
    amount: float,
    tp_price: float,
    passphrase: str | None = None,
) -> dict[str, Any]:
    """Place a take-profit order."""
    ex = _build_exchange(exchange, api_key, api_secret, passphrase, market_type="futures")
    try:
        futures_symbol = _adapt_symbol(symbol, "futures")
        params: dict[str, Any] = {"stopPrice": tp_price, "reduceOnly": True}

        order_type = "take_profit_market"
        try:
            order = await ex.create_order(
                symbol=futures_symbol,
                type=order_type,
                side=side,
                amount=amount,
                params=params,
            )
        except (ccxt.NotSupported, ccxt.InvalidOrder):
            order = await ex.create_order(
                symbol=futures_symbol,
                type="limit",
                side=side,
                amount=amount,
                price=tp_price,
                params={"reduceOnly": True, "triggerPrice": tp_price},
            )

        log.info("[exec] TP %s %s @ %.6f on %s → %s", side, futures_symbol, tp_price, exchange, order.get("id"))
        return {"ok": True, "id": order.get("id"), "tp_price": tp_price}
    except Exception as e:
        log.warning("[exec] TP failed %s %s: %s", side, symbol, e)
        return {"ok": False, "error": str(e)}
    finally:
        await ex.close()


async def get_ticker_price(
    exchange: str,
    api_key: str,
    api_secret: str,
    symbol: str,
    passphrase: str | None = None,
) -> float:
    """Fetch current price for a symbol."""
    ex = _build_exchange(exchange, api_key, api_secret, passphrase, market_type="futures")
    try:
        futures_symbol = _adapt_symbol(symbol, "futures")
        ticker = await ex.fetch_ticker(futures_symbol)
        return float(ticker.get("last") or ticker.get("close") or 0)
    except Exception:
        return 0.0
    finally:
        await ex.close()
