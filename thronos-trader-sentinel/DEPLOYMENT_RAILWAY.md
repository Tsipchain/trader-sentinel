# Railway deployment quick guide (backend + analyst + brain)

Use this when mobile shows warnings like:

- `Configured BRAIN_URL appears to be the main backend (/api/sentinel/*), not Sentinel Brain (/api/brain/*)`
- `Brain route returned 404`

These warnings mean the **Brain service URL points to the wrong runtime** (usually main backend or analyst service).

---

## 1) Correct Start Commands

> If you deploy with each service `Dockerfile`, Railway can leave **Start Command empty**.
> If you want explicit custom commands, use these:

### Sentinel Backend (market/sentinel)
```bash
sh -c 'uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8081}'
```
Working directory: `thronos-trader-sentinel/backend`

### Sentinel Analyst
```bash
sh -c 'uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8082}'
```
Working directory: `thronos-trader-sentinel/sentinel-analyst`

### Sentinel Brain
```bash
sh -c 'uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8083}'
```
Working directory: `thronos-trader-sentinel/sentinel-brain`

---

## 2) Build context must match each service

For the Brain service, make sure Railway builds from:

- root directory: `thronos-trader-sentinel/sentinel-brain`
- Dockerfile: `thronos-trader-sentinel/sentinel-brain/Dockerfile`

If Railway builds from repo root Dockerfile by mistake, you get backend routes (`/api/sentinel/*`) and **no** `/api/brain/*` routes.

---

## 3) Required env vars

Set these on **Brain** service:

- `API_KEY` (same key used by mobile and other services)
- `DISK_PATH=/disckb` (or your mounted volume path)
- optional: `EXCHANGE_BLOCKED=binance,bybit`

Set these on **mobile/EAS**:

- `EXPO_PUBLIC_API_URL=https://<backend-host>`
- `EXPO_PUBLIC_ANALYST_URL=https://<analyst-host>`
- `EXPO_PUBLIC_BRAIN_URL=https://<brain-host>`
- `EXPO_PUBLIC_API_KEY=<same API_KEY>`

---

## 4) Verify endpoints before APK build

Run quick checks:

```bash
curl -s https://<brain-host>/health
curl -s https://<brain-host>/openapi.json
curl -s -H "X-API-Key: <API_KEY>" https://<brain-host>/api/brain/storage/status
curl -s -H "X-API-Key: <API_KEY>" https://<brain-host>/api/brain/exchange/availability
```

Expected: brain host includes `/api/brain/*` routes and does **not** look like only `/api/sentinel/*`.

---

## 5) Why this fixes your current warning

The mobile warning is correct: it appears when `BRAIN_URL` resolves to a service whose OpenAPI shape is backend-like (`/api/sentinel/*`, `/api/market/*`) instead of brain-like (`/api/brain/*`).

Fixing build context + start command + `EXPO_PUBLIC_BRAIN_URL` removes the warning and allows AutoTrader sync to hit real Brain endpoints.


## 6) Fix for `Invalid value for --port: $PORT`

If Railway Start Command is set as:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

`$PORT` may be passed literally (no shell interpolation), and Uvicorn fails with:

```
Error: Invalid value for --port: "$PORT" is not a valid integer.
```

Use one of these instead:

```bash
sh -c 'uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8083}'
```

or leave Start Command empty and use the Dockerfile `CMD` (already shell-safe in sentinel-brain Dockerfile).
