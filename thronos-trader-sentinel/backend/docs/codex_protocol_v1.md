# Codex Protocol v1.0 — Trader-Sentinel ↔ SigBalBot Integration

**Version:** 1.0
**Last updated:** 2026-08-12

---

## Overview

Trader-Sentinel connects to SigBalBot (a Telegram relay bot on Railway) via a
bidirectional REST contract. Sentinel polls context, evaluates it through its
8-source technical/risk pipeline, and posts finalized signals and market reviews
back to SigBalBot for Telegram distribution.

```
Thronos (wallet-snapshots API)
        │
        ▼
   SigBalBot (relay, 5-min cache)
        │
        ▼
Trader-Sentinel (consumer, evaluator)
        │
        ▼
   SigBalBot (POST signals / market-reviews)
        │
        ▼
   Telegram subscribers
```

---

## 1. Authentication

All requests use Bearer token authentication.

```
Authorization: Bearer <SIGBALBOT_API_KEY>
```

**Security rules:**
- Never log `SIGBALBOT_API_KEY` or complete `Authorization` headers.
- Never log, store, or transmit wallet private keys or seed phrases.
- SigBalBot must never send or store a treasury seed phrase/private key.
- Signing must happen in a separate signer or multisig process.

---

## 2. Capability Discovery

Before polling, Sentinel checks SigBalBot's status endpoint.

### Request

```
GET /api/v1/signals/trader-sentinel/status
Authorization: Bearer <SIGBALBOT_API_KEY>
```

### Response

```json
{
  "ok": true,
  "contract_version": "1.0",
  "capabilities": {
    "context_feed": "/api/v1/context/trader-sentinel",
    "signal_post": "/api/v1/signals/trader-sentinel",
    "market_review": "/api/v1/market-review/trader-sentinel"
  }
}
```

### Validation

- `contract_version` must be in `{"1.0"}`. Unknown versions halt polling.
- `capabilities.context_feed` must contain `/context/trader-sentinel`.
- Polling only starts after capability confirmation succeeds.

---

## 3. Context Polling

### Request

```
GET /api/v1/context/trader-sentinel?since=<cursor>&limit=25
Authorization: Bearer <SIGBALBOT_API_KEY>
```

| Parameter | Type   | Description                             |
|-----------|--------|-----------------------------------------|
| `since`   | string | ISO 8601 cursor from previous response  |
| `limit`   | int    | Max items per poll (default 25)         |

### Response

```json
{
  "ok": true,
  "contract_version": "1.0",
  "generated_at": "2026-08-12T10:00:00Z",
  "watchlist": [
    {
      "symbol": "BTC/USDT",
      "mode": "watch",
      "label": "BTC",
      "market_cap_usd": 1200000000000,
      "volume_24h": 35000000000,
      "liquidity_usd": 500000000,
      "last_scan_signal": "AGG_LONG",
      "last_scan_at": "2026-08-12T09:55:00Z"
    }
  ],
  "native_signals": [
    {
      "id": "sigbal-BTC-USDT-LONG-1723456789",
      "symbol": "BTC/USDT",
      "signal": "AGG_LONG",
      "confidence": 82,
      "timeframe": "15m",
      "reason": "Multi-indicator confluence",
      "created_at": "2026-08-12T09:55:00Z"
    }
  ],
  "wallet_snapshots": [
    {
      "address": "THR0A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6A7",
      "tier": "pro",
      "rewards_multiplier": 1.5,
      "active": true,
      "verified": true,
      "expires_at": 1723542000,
      "thr_balance": 250.0,
      "snapshot_at": 1723455600
    }
  ],
  "wallet_snapshot_status": {
    "state": "fresh",
    "cached_at": "2026-08-12T09:55:00Z",
    "subscriber_count": 42,
    "next_refresh_at": "2026-08-12T10:00:00Z"
  }
}
```

#### Wallet snapshot status states

| State           | Meaning                                   |
|-----------------|-------------------------------------------|
| `fresh`         | Snapshot fetched within the last 5 minutes |
| `stale`         | Cached but older than 5 minutes            |
| `unconfigured`  | Thronos API URL not set on SigBalBot       |
| `unavailable`   | Thronos API returned an error              |

#### Cursor behavior

- `generated_at` from each response becomes the next `since` value.
- For native signals, the latest `created_at` also advances the cursor.
- Sentinel persists the cursor and a bounded list of processed IDs (max 500).

---

## 4. Signal POST

### Request

```
POST /api/v1/signals/trader-sentinel
Authorization: Bearer <SIGBALBOT_API_KEY>
Content-Type: application/json
```

### Payload (17 required fields)

```json
{
  "id": "sentinel-BTC-USDT-LONG-1723456789",
  "symbol": "BTC/USDT",
  "signal": "LONG",
  "timeframe": "1h",
  "price": 65000.50,
  "confidence": 78,
  "risk": "MEDIUM",
  "reason": "hidden_bullish (2x confirmed)",
  "strategy": "sleep-mode-autotrader",
  "market_regime": "bullish",
  "confluence_score": 0.1500,
  "model_version": "pytheia-sentinel-v0.2",
  "confirmations": ["RSI 42", "MACD bullish", "EMA golden"],
  "entry": 65000.50,
  "tp1": 67000.00,
  "tp2": 68236.00,
  "sl": 63500.00
}
```

#### Field reference

| Field              | Type       | Description                                  |
|--------------------|------------|----------------------------------------------|
| `id`               | string     | Deterministic: `sentinel-{BASE}-{QUOTE}-{SIGNAL}-{ts}` |
| `symbol`           | string     | Normalized pair, e.g. `BTC/USDT`             |
| `signal`           | string     | One of: LONG, SHORT, BUY, SELL, HOLD, ACCUMULATION, DISTRIBUTION |
| `timeframe`        | string     | Chart timeframe, e.g. `1h`, `4h`             |
| `price`            | float      | Entry/current price                          |
| `confidence`       | int        | 0–100 confidence score                       |
| `risk`             | string     | LOW, MEDIUM, HIGH, EXTREME                   |
| `reason`           | string     | Strategy/pattern description                 |
| `strategy`         | string     | Strategy identifier                          |
| `market_regime`    | string     | Current regime from TA                       |
| `confluence_score` | float      | 0.0–1.0 confluence strength                  |
| `model_version`    | string     | Always `pytheia-sentinel-v0.2`               |
| `confirmations`    | string[]   | Human-readable indicator confirmations       |
| `entry`            | float      | Entry price                                  |
| `tp1`              | float/null | Take profit 1                                |
| `tp2`              | float/null | Take profit 2 (1.618× TP1 distance)          |
| `sl`               | float/null | Stop loss                                    |

#### Optional enrichment: `platinum_context`

When lesson-aware enrichment matches, the payload includes:

```json
{
  "platinum_context": {
    "match_count": 2,
    "detected_patterns": ["double_bottom", "bull_flag"],
    "lesson_references": [
      {
        "lesson_id": "les_abc123",
        "title": "Double Bottom Setup",
        "image_filename": "db.png",
        "patterns": ["double_bottom"],
        "timeframe": "4h"
      }
    ]
  }
}
```

Max 5 lesson references per signal. Deduplicated by `lesson_id`.

---

## 5. Market Review POST

### Request

```
POST /api/v1/market-review/trader-sentinel
Authorization: Bearer <SIGBALBOT_API_KEY>
Content-Type: application/json
```

### Payload (6 fields)

```json
{
  "id": "sentinel-review-20260812-am",
  "market_regime": "CAUTIOUS",
  "risk": "HIGH",
  "confidence": 4,
  "summary": "BTC $65,000 (RSI 42, MACD bullish, BB neutral) | Risk: REDUCE — ...",
  "outlook": "BTC RSI neutral. Elevated risk — monitoring for escalation."
}
```

| Field           | Type   | Description                                           |
|-----------------|--------|-------------------------------------------------------|
| `id`            | string | `sentinel-review-YYYYMMDD-{am\|pm}` (deterministic)   |
| `market_regime` | string | RISK-ON, NEUTRAL, CAUTIOUS, RISK-OFF                  |
| `risk`          | string | LOW, MEDIUM, HIGH, EXTREME                            |
| `confidence`    | int    | 1–10 (inverse of composite risk score)                |
| `summary`       | string | Multi-asset TA + risk + session summary               |
| `outlook`       | string | Forward-looking commentary                            |

Published every 12 hours (AM/PM windows).

---

## 6. HTTP Response Contract

All POST endpoints share the same response semantics:

| Status | Condition              | Action                                  |
|--------|------------------------|-----------------------------------------|
| 202    | Delivered              | Success. `telegram_sent`, `event_id` returned. |
| 200    | `duplicate: true`      | Already delivered (dedup on `id`). Success. |
| 200    | Other                  | Success variant.                         |
| 400    | Payload/contract error | Do NOT retry. Log response body.         |
| 401    | Auth error             | Permanent. Do NOT retry. Check API key.  |
| 403    | Auth error             | Permanent. Do NOT retry. Check config.   |
| 503    | `retryable: true`      | Temporary Telegram failure. Retry with exponential backoff. |
| 5xx    | Server error           | Retry with exponential backoff (max 3 attempts). |

### Retry behavior

- Max 3 attempts per request.
- Exponential backoff: 2s, 4s between retries.
- Same `id` on every retry ensures SigBalBot deduplicates.
- Context polling also retries on 5xx with the same backoff.

---

## 7. Stable Event IDs

All IDs are deterministic to enable deduplication:

| Type            | Format                                           | Example                                |
|-----------------|--------------------------------------------------|----------------------------------------|
| Trade signal    | `sentinel-{BASE}-{QUOTE}-{SIGNAL}-{opened_at}`  | `sentinel-BTC-USDT-LONG-1723456789`    |
| Context signal  | `sentinel-ctx-{BASE}-{QUOTE}-{SIGNAL}-{time}`   | `sentinel-ctx-BTC-USDT-LONG-1723456789`|
| Native signal   | `sentinel-ctx-native-{BASE}-{QUOTE}-{SIGNAL}-{time}` | `sentinel-ctx-native-SOL-USDT-SHORT-1723456789` |
| Market review   | `sentinel-review-{YYYYMMDD}-{am\|pm}`            | `sentinel-review-20260812-am`          |

---

## 8. Wallet Snapshot Chain

```
Thronos server.py
  GET /api/sigbalbot/wallet-snapshots (admin-authed)
  → Returns active subscriber wallet data
        │
        ▼
SigBalBot (Railway)
  Polls Thronos every 5 minutes, caches result
  Strips private fields before relaying
  Includes wallet_snapshots[] and wallet_snapshot_status in context response
        │
        ▼
Trader-Sentinel
  wallet_snapshot_consumer.py ingests wallet_snapshots from context
  Stores to data/wallet_snapshots/latest_snapshot.json
  Max snapshot age: 2 hours
  Provides: subscriber count, tier distribution, confidence boost
```

### Confidence boost from subscriber count

| Active subscribers | Boost  |
|--------------------|--------|
| 0–4                | +0.00  |
| 5–19               | +0.01  |
| 20–49              | +0.02  |
| 50+                | +0.03  |

### Subscriber tiers

| Tier    | Rewards multiplier |
|---------|--------------------|
| starter | 1.0×               |
| pro     | 1.5×               |
| elite   | 2.5×               |
| whale   | 5.0×               |

---

## 9. Feedback Loop Guard

Sentinel prefixes all its signal IDs with `sentinel-`. When polling context,
any native signal whose `id` starts with `sentinel-` is skipped to prevent
feedback loops (Sentinel re-evaluating its own signals).

---

## 10. Safety Constraints

1. **No automatic live trading** from the mobile wallet app.
2. **No automatic wallet signing** — all signing is user-initiated or multisig.
3. **No private-key handling** over the network — keys stay on-device.
4. **No automatic reward broadcast** from the mobile app.
5. **No auto-pay** for: expired subscribers, unapproved subscribers, users
   without wallet snapshots, unverified registrations, promoter commissions.
6. Allocation state machine: `pending → approved → submitted → confirmed/failed`.
   Only `approved` allocations can be submitted for broadcast.
7. Idempotency keys are SHA256-based: `SHA256(batch_id:address)`.

---

## 11. Environment Variables

### Trader-Sentinel (Railway)

| Variable                    | Description                                    |
|-----------------------------|------------------------------------------------|
| `SIGBALBOT_WEBHOOK_URL`     | Full URL to `/api/v1/signals/trader-sentinel`  |
| `SIGBALBOT_API_KEY`         | Bearer token for SigBalBot authentication      |
| `SIGBALBOT_POLL_INTERVAL_S` | Polling interval in seconds (default: 120)     |
| `WALLET_SNAPSHOT_DIR`       | Local storage dir (default: `data/wallet_snapshots`) |

### SigBalBot (Railway)

| Variable              | Description                                   |
|-----------------------|-----------------------------------------------|
| `THRONOS_API_URL`     | Thronos server base URL                       |
| `THRONOS_ADMIN_API_KEY` | Admin API key for wallet-snapshot endpoint   |

### Thronos (Railway)

| Variable              | Description                                   |
|-----------------------|-----------------------------------------------|
| `ADMIN_API_KEY`       | Admin authentication for internal APIs        |

---

## 12. Contract Version Negotiation

- Sentinel only accepts `contract_version` values in `{"1.0"}`.
- If SigBalBot upgrades to a new contract version, Sentinel will refuse to
  poll until its `_SUPPORTED_CONTRACT_VERSIONS` set is updated.
- Both status and context responses include `contract_version`.
- Unknown versions produce an error log and halt the polling cycle.
