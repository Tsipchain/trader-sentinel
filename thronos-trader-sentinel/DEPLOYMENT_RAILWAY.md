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


## 7) If service is "Online" but `/api/brain/*` still returns 404

If logs look like this:

- `GET /health -> 200`
- `GET /openapi.json -> 200`
- `GET /api/brain/storage/status -> 404`
- `POST /api/brain/sync -> 404`

then your service is running, but **not the Sentinel Brain app**.

### Quick diagnosis

```bash
curl -s https://<brain-host>/openapi.json
```

If `info.title` looks like `Thronos Trader Sentinel` (or paths mostly `/api/sentinel/*`, `/api/market/*`), you are running backend app, not brain app.

For Brain, `openapi.json` should include `/api/brain/*` paths (e.g. `/api/brain/sync`, `/api/brain/storage/status`, `/api/brain/exchange/snapshot`).

### Fix

1. Railway service root directory must be `thronos-trader-sentinel/sentinel-brain`.
2. Dockerfile must be `thronos-trader-sentinel/sentinel-brain/Dockerfile`.
3. Start command should be empty (use Dockerfile CMD) **or**:
   ```bash
   sh -c 'uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8083}'
   ```
4. Redeploy and re-run the curl checks from section 4.


## 8) If Railway shows **Queued** for a long time

`Queued` usually means Railway has not started your build yet (capacity/concurrency),
not that your code is failing to boot.

### What to do

1. Open the service → **Deployments** and cancel older queued/running deploys.
2. Keep only the latest commit deployment for each service (backend/analyst/brain).
3. Trigger **Redeploy latest** once.
4. Check project usage limits (build minutes/concurrency) in Railway project settings.
5. If multiple services are auto-deploying from one push, deploy Brain alone first, then the rest.

### Quick check after queue clears

```bash
curl -s https://<brain-host>/
curl -s https://<brain-host>/health
curl -s https://<brain-host>/api/brain/health
```

All should return HTTP 200 JSON. If they do, initialization is complete and the queue issue was platform-side.


## 9) If status says **"Queued due to upstream GitHub issues"**

This is a GitHub integration outage/delay between Railway and GitHub webhooks, not an app bug.

### Immediate workaround (recommended)

1. In Railway service settings, temporarily disable **Auto Deploy on Git push**.
2. Trigger a manual deploy from latest source (or redeploy latest successful build/image).
3. Once deployment succeeds, re-enable auto deploy.

### Queue cleanup

- Cancel older queued deployments (#93, #94 etc.) and keep only one latest deploy request.
- If GitHub-triggered deploys keep queueing, wait for GitHub status recovery and deploy manually in the meantime.

### Validate after manual deploy

```bash
curl -s https://<brain-host>/
curl -s https://<brain-host>/health
curl -s https://<brain-host>/api/brain/health
```

If these return `ok: true`, AutoTrader backend is healthy; queueing is external to runtime code.
