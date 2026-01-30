# Thronos Trader Sentinel (CEX + DEX Live Price Compare)

A lightweight FastAPI microservice that aggregates **CEX + DEX** prices and exposes a consistent API.

This is meant to be the “data spine” for the Thronos real‑time trading assistant:
- **Price comparisons** (best bid/ask across venues)
- **Simple arb spreads** (CEX↔CEX and CEX↔DEX)
- **Streaming** via SSE (Server‑Sent Events)
- Optional **Google TTS** hook for voice alerts (bring your own credentials)

## Venues (v1)
### CEX (via `ccxt`)
- Binance
- Bybit
- OKX
- MEXC

### DEX (v1)
- DexScreener (public search/price discovery)

> You can extend DEX quoting later with 0x / 1inch (EVM) and Jupiter (Solana) using the same pattern.

---

## API
### Health
- `GET /health`

### Snapshot (multi‑venue)
- `GET /api/market/snapshot?symbol=BTC/USDT`

Returns a normalized array:
```json
{
  "ok": true,
  "symbol": "BTC/USDT",
  "ts": 1730000000,
  "venues": [
    {"venue":"binance","kind":"cex","last":0,"bid":0,"ask":0,"ts":0},
    {"venue":"dexscreener","kind":"dex","last":0,"pair":"...","chain":"...","ts":0}
  ]
}
```

### Arb view
- `GET /api/market/arb?symbol=BTC/USDT`

Computes best bid/ask and spread.

### Stream (SSE)
- `GET /api/market/stream?symbol=BTC/USDT&interval_ms=1000`

Sends `event: snapshot` lines every interval.

---

## Run locally
```bash
cd backend
cp .env.example .env
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8081
```

## Environment
See `.env.example`.

## Notes
- Public price endpoints do **not** require exchange keys.
- For private endpoints (positions, orders) you can add keys later.
- SSE is simple and works well behind a reverse proxy.

