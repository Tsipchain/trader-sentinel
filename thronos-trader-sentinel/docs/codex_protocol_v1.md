# Codex Protocol v1.0 — SigBalBot ↔ Sentinel Integration Contract

## Overview

Trader Sentinel (Pytheia) communicates with SigBalBot via a bidirectional HTTP contract.
SigBalBot acts as the relay between Thronos (governance/wallet) and Sentinel (TA/signals).

**Direction A — Context Feed (SigBalBot → Sentinel):**
Sentinel polls SigBalBot for watchlist symbols, native signals, and wallet snapshots.

**Direction B — Signal POST (Sentinel → SigBalBot):**
Sentinel publishes finalized, actionable signals back to SigBalBot for Telegram delivery.

---

## 1. Authentication

All endpoints require Bearer token authentication.

```
Authorization: Bearer <SIGBALBOT_API_KEY>
```

- Tokens are never sent as query parameters.
- Tokens are never logged.
- `401` / `403` responses indicate permanent auth failure — do NOT retry.

**Environment variables:**
| Variable | Purpose |
|---|---|
| `SIGBALBOT_WEBHOOK_URL` | Base URL for signal POST (e.g. `https://sigbalbot.up.railway.app/api/v1/signals/trader-sentinel`) |
| `SIGBALBOT_API_KEY` | Bearer token for all SigBalBot API calls |

---

## 2. Capability Check (startup gate)

Before polling begins, Sentinel verifies that SigBalBot exposes the context feed.

### `GET {SIGBALBOT_WEBHOOK_URL}/status`

**Request:**
```
Authorization: Bearer <SIGBALBOT_API_KEY>
```

**Response (200):**
```json
{
  "ok": true,
  "receiver": "sigbalbot",
  "configured": true,
  "contract_version": "1.0",
  "capabilities": {
    "context_feed": "/api/v1/context/trader-sentinel"
  },
  "last_event": null
}
```

**Validation rules:**
1. `contract_version` must be in `{"1.0"}` (supported set).
2. `capabilities.context_feed` must contain `/context/trader-sentinel`.
3. If either check fails, context polling does NOT start — retries at `POLL_INTERVAL_S`.

---

## 3. Context Feed — `GET /api/v1/context/trader-sentinel`

Sentinel polls this endpoint to receive watchlist symbols, native signals, and wallet snapshots.

### Request

```
GET /api/v1/context/trader-sentinel?since=<cursor>&limit=25
Authorization: Bearer <SIGBALBOT_API_KEY>
```

| Parameter | Type | Description |
|---|---|---|
| `since` | string (optional) | Cursor from previous response's `generated_at`. Omit on first poll. |
| `limit` | integer | Max items per page (default: 25). |

### Response (200)

```json
{
  "ok": true,
  "contract_version": "1.0",
  "generated_at": "2026-01-15T12:00:00Z",
  "watchlist": [
    {
      "symbol": "BTC/USDT",
      "mode": "watch",
      "label": "BTC",
      "market_cap_usd": 1200000000000,
      "volume_24h": 35000000000,
      "liquidity_usd": 500000000,
      "last_scan_signal": "LONG",
      "last_scan_at": "2026-01-15T11:55:00Z"
    }
  ],
  "native_signals": [
    {
      "id": "sigbal-BTC-USDT-AGG_LONG-1736942400",
      "symbol": "BTC/USDT",
      "signal": "AGG_LONG",
      "confidence": 85,
      "timeframe": "15m",
      "risk": "STANDARD",
      "reason": "Multi-indicator convergence",
      "created_at": "2026-01-15T11:58:00Z"
    }
  ],
  "wallet_snapshots": [
    {
      "address": "THR...",
      "tier": "pro",
      "rewards_multiplier": 1.5,
      "active": true,
      "verified": true,
      "expires_at": 1736942400,
      "thr_balance": 150.0,
      "snapshot_at": 1736938800
    }
  ]
}
```

### HTTP behavior

| Status | Meaning | Action |
|---|---|---|
| `200` | Success | Parse body, validate `contract_version`, process items. |
| `200` with `ok: false` | Logical error | Log warning, skip cycle. |
| `401` / `403` | Auth error | Log error, stop. Do NOT retry. |
| `503` / `5xx` | Temporary failure | Retry up to 3 times with exponential backoff (2s, 4s). |

### Contract version validation

The response's `contract_version` is checked against `_SUPPORTED_CONTRACT_VERSIONS = {"1.0"}`.
Unsupported versions cause the response to be rejected entirely — this is a hard gate.

### Cursor persistence

- `generated_at` from the response becomes the next `since` cursor.
- For native signals, `created_at` also updates the cursor if it's newer.
- Cursors are persisted to disk at `{DISK_PATH}/sigbalbot_context/cursor.json`.
- Processed signal IDs are stored alongside the cursor (bounded to 500 entries).

---

## 4. Signal POST — `POST /api/v1/signals/trader-sentinel`

Sentinel posts finalized actionable signals for Telegram delivery.

### Request

```
POST /api/v1/signals/trader-sentinel
Authorization: Bearer <SIGBALBOT_API_KEY>
Content-Type: application/json
```

### Payload schema

```json
{
  "id": "sentinel-BTC-USDT-LONG-1736942400",
  "symbol": "BTC/USDT",
  "signal": "LONG",
  "timeframe": "1h",
  "price": 65432.10,
  "confidence": 0.72,
  "risk": "HIGH",
  "reason": "regular_bullish (2x confirmed) + MTF strong (3TF)",
  "strategy": "sleep-mode-autotrader",
  "market_regime": "bullish",
  "confluence_score": 0.15,
  "model_version": "pytheia-sentinel-v0.2",
  "confirmations": ["RSI 32", "MACD bullish", "EMA golden_cross"],
  "entry": 65432.10,
  "tp1": 66088.42,
  "tp2": 67493.18,
  "sl": 64777.88
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | yes | Deterministic: `sentinel-{SYMBOL}-{DIRECTION}-{opened_at}`. Same id on retries enables dedup. |
| `symbol` | string | yes | Normalized pair (e.g. `BTC/USDT`). Futures suffix `:USDT` stripped. |
| `signal` | string | yes | Direction. Valid: `LONG`, `SHORT` (from sleep_trader). Extended set: `BUY`, `SELL`, `HOLD`, `ACCUMULATION`, `DISTRIBUTION`. |
| `timeframe` | string | yes | Always `1h` for sleep_trader signals. |
| `price` | float | yes | Entry price at signal creation. |
| `confidence` | float | yes | 0.0–1.0 confidence score. |
| `risk` | string | yes | `LOW` (lev 1-4), `MEDIUM` (5-9), `HIGH` (10-19), `EXTREME` (20+). |
| `reason` | string | yes | Strategy description. |
| `strategy` | string | yes | Strategy identifier (e.g. `sleep-mode-autotrader`, `sigbalbot-context-watchlist`). |
| `market_regime` | string | yes | From MACD trend (e.g. `bullish`, `bearish`, `unknown`). |
| `confluence_score` | float | yes | Confluence bonus from multi-timeframe agreement. |
| `model_version` | string | yes | Always `pytheia-sentinel-v0.2`. |
| `confirmations` | string[] | yes | Human-readable TA confirmations (e.g. `["RSI 32", "MACD bullish"]`). |
| `entry` | float | yes | Entry price. |
| `tp1` | float | yes | Take profit 1. |
| `tp2` | float | nullable | Take profit 2 (1.618x Fibonacci extension). |
| `sl` | float | yes | Stop loss. |

### Symbol normalization

| Input | Output |
|---|---|
| `BTC/USDT` | `BTC/USDT` |
| `BTC/USDT:USDT` | `BTC/USDT` |
| `ETHUSDT` | `ETH/USDT` |

### Signal ID format

```
sentinel-{BASE}-{QUOTE}-{DIRECTION}-{opened_at_unix}
```

The deterministic ID ensures:
- Same signal produces same ID across retries (SigBalBot deduplicates on `id`).
- The `sentinel-` prefix triggers the feedback loop guard (never re-evaluated by Sentinel).

### HTTP response contract

| Status | Body | Meaning | Action |
|---|---|---|---|
| `202` | `{ ok: true, duplicate: false, telegram_sent: true, event_id, outcomes_scheduled, contract_version }` | Delivered successfully | Log, return body. |
| `200` | `{ ok: true, duplicate: true, event_id, contract_version }` | Already delivered (dedup) | Log as duplicate, return body. No retry needed. |
| `503` | `{ ok: false, retryable: true, event_id, contract_version }` | Temporary Telegram failure | Retry up to 3 times with exponential backoff (2s, 4s). |
| `401` | `{ error: "unauthorized" }` | Permanent auth error | Log error, return None. Do NOT retry. |
| `403` | `{ error: "forbidden" }` | Permanent auth error | Log error, return None. Do NOT retry. |
| `400` | `{ error: "<field error>", contract_version }` | Payload/contract error | Log body, return None. Do NOT retry. |

---

## 5. Feedback Loop Guard

Signals with IDs prefixed `sentinel-` are never re-evaluated by Sentinel.
This prevents an infinite loop where Sentinel publishes a signal, SigBalBot relays it back
as a native signal, and Sentinel evaluates it again.

```python
_SENTINEL_ID_PREFIX = "sentinel-"

def _is_sentinel_signal(signal_id: str) -> bool:
    return str(signal_id).startswith(_SENTINEL_ID_PREFIX)
```

---

## 6. Wallet Snapshot Consumer

Wallet snapshots arrive in the context feed response under `wallet_snapshots`.

### Ingestion

- Accepts both list format (with `address` field per entry) and dict format (keyed by address).
- Persists to `{WALLET_SNAPSHOT_DIR}/latest_snapshot.json`.
- Max snapshot age: 7200 seconds (2 hours) before considered stale.

### Subscriber-aware confidence

| Active subscribers | Confidence boost |
|---|---|
| 0–4 | 0.00 |
| 5–19 | 0.01 |
| 20–49 | 0.02 |
| 50+ | 0.03 |

### Subscriber fields

| Field | Type | Description |
|---|---|---|
| `address` | string | THR wallet address |
| `tier` | string | Subscription tier (e.g. `starter`, `pro`) |
| `rewards_multiplier` | float | Reward multiplier for this tier |
| `active` | bool | Whether the subscription is active |
| `verified` | bool | Whether the wallet is verified |
| `expires_at` | int (unix) | Subscription expiry timestamp |
| `thr_balance` | float | THR token balance |
| `snapshot_at` | int (unix) | When this snapshot was taken |

---

## 7. Duplicate Detection

### Watchlist dedup
Key: `wl:{symbol}:{last_scan_at}` — same symbol+scan time is skipped.

### Native signal dedup
Key: signal `id` — stored in `processed_signal_ids` (bounded to 500 entries).

### Signal POST dedup
The deterministic `id` field means SigBalBot deduplicates on the server side.
A `200 + duplicate: true` response confirms the signal was already delivered.

---

## 8. Retry Policy

| Scenario | Max attempts | Backoff |
|---|---|---|
| Context feed `5xx` | 3 | Exponential: 2s, 4s |
| Signal POST `503 + retryable` | 3 | Exponential: 2s, 4s |
| Signal POST `401/403/400` | 1 | No retry (permanent) |
| Network error (any endpoint) | 3 | Exponential: 2s, 4s |

---

## 9. Safety Rules

1. **No automatic trading.** Signals are informational. No live order execution.
2. **No private key handling.** Sentinel never accesses wallet private keys.
3. **No automatic reward broadcast.** Reward distribution is a manual governance action.
4. **No API key leakage.** Bearer tokens are never logged, never sent as query params.
5. **Contract version gate.** Unsupported `contract_version` values reject the entire response.
6. **Feedback loop guard.** Signals with `sentinel-` prefix are never re-evaluated.
7. **Bounded state.** Processed IDs capped at 500 to prevent unbounded memory growth.

---

## 10. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `SIGBALBOT_WEBHOOK_URL` | yes | — | Signal POST endpoint URL |
| `SIGBALBOT_API_KEY` | yes | — | Bearer token for SigBalBot API |
| `SIGBALBOT_POLL_INTERVAL_S` | no | `120` | Polling interval in seconds |
| `WALLET_SNAPSHOT_DIR` | no | `data/wallet_snapshots` | Local snapshot storage path |
| `DISK_PATH` | no | `/disckb` | Base path for all disk persistence |

---

## 11. E2E Data Flow

```
Thronos (wallet/governance)
    │
    ├── wallet snapshots ──► SigBalBot (collects + bundles)
    │
    ▼
SigBalBot context endpoint
  GET /api/v1/context/trader-sentinel
    │
    ├── watchlist[]          ──► Sentinel evaluates via TA + risk
    ├── native_signals[]     ──► Sentinel cross-references with own TA
    └── wallet_snapshots[]   ──► Sentinel stores for confidence boost
                                    │
                                    ▼
                             Sentinel produces actionable signal
                                    │
                                    ▼
                      POST /api/v1/signals/trader-sentinel
                                    │
                                    ▼
                         SigBalBot delivers via Telegram
```
