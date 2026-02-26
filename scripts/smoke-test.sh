#!/usr/bin/env bash
# =============================================================================
# Thronos Trader Sentinel — Smoke Test
# Runs in ~10 min against a live staging/prod environment.
#
# Usage:
#   export API_KEY="your-shared-secret"          # same as services' API_KEY env var
#   export BACKEND_URL="https://sentinel.thronoschain.org"
#   export ANALYST_URL="https://alanisys.up.railway.app"
#   export BRAIN_URL="https://alanisys.up.railway.app"
#   bash scripts/smoke-test.sh
#
# Exit code 0 = all tests passed. Non-zero = at least one failure.
# =============================================================================
set -euo pipefail

BACKEND_URL="${BACKEND_URL:-https://sentinel.thronoschain.org}"
ANALYST_URL="${ANALYST_URL:-https://alanisys.up.railway.app}"
BRAIN_URL="${BRAIN_URL:-https://alanisys.up.railway.app}"
API_KEY="${API_KEY:-}"
SYMBOL="${SYMBOL:-BTC%2FUSDT}"

PASS=0
FAIL=0
WARN=0

# ── Helpers ──────────────────────────────────────────────────────────────────
green()  { echo -e "\033[32m[PASS]\033[0m $*"; }
red()    { echo -e "\033[31m[FAIL]\033[0m $*"; }
yellow() { echo -e "\033[33m[WARN]\033[0m $*"; }
blue()   { echo -e "\033[34m[INFO]\033[0m $*"; }

assert_http() {
  local label="$1" url="$2" expected="$3"
  shift 3
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" "$@" "$url")
  if [ "$code" = "$expected" ]; then
    green "$label → HTTP $code"
    PASS=$((PASS+1))
  else
    red "$label → expected HTTP $expected, got HTTP $code"
    FAIL=$((FAIL+1))
  fi
}

assert_json_key() {
  local label="$1" url="$2" key="$3"
  shift 3
  local body
  body=$(curl -s "$@" "$url")
  if echo "$body" | grep -q "\"$key\""; then
    green "$label → response contains '$key'"
    PASS=$((PASS+1))
  else
    red "$label → response missing '$key'. Body: ${body:0:200}"
    FAIL=$((FAIL+1))
  fi
}

AUTH=()
if [ -n "$API_KEY" ]; then
  AUTH=(-H "X-API-Key: $API_KEY")
fi

echo ""
blue "=========================================="
blue " Thronos Trader Sentinel — Smoke Test"
blue " $(date -u '+%Y-%m-%d %H:%M UTC')"
blue "=========================================="
blue "BACKEND  : $BACKEND_URL"
blue "ANALYST  : $ANALYST_URL"
blue "BRAIN    : $BRAIN_URL"
blue "API_KEY  : ${API_KEY:+set (${#API_KEY} chars)}${API_KEY:-NOT SET — auth tests will be skipped}"
echo ""

# =============================================================================
# 1. HEALTH CHECKS (no auth needed)
# =============================================================================
blue "── 1. Health checks (unauthenticated) ──"
assert_http  "Backend /health"  "$BACKEND_URL/health"  200
assert_http  "Analyst /health"  "$ANALYST_URL/health"  200
assert_http  "Brain   /health"  "$BRAIN_URL/health"    200

# =============================================================================
# 2. AUTH — 403 when API key is missing/wrong (only if API_KEY is configured)
# =============================================================================
echo ""
blue "── 2. Authentication guards ──"
if [ -n "$API_KEY" ]; then
  assert_http "Backend /api/market/snapshot — no key → 403" \
    "$BACKEND_URL/api/market/snapshot?symbol=$SYMBOL" 403
  assert_http "Backend /api/market/snapshot — wrong key → 403" \
    "$BACKEND_URL/api/market/snapshot?symbol=$SYMBOL" 403 \
    -H "X-API-Key: wrong-key-xyz"
  assert_http "Analyst /api/analyst/briefing — no key → 403" \
    "$ANALYST_URL/api/analyst/briefing" 403
  assert_http "Brain /api/brain/predict — no key → 403" \
    "$BRAIN_URL/api/brain/predict" 403
else
  yellow "API_KEY not set — skipping auth guard tests"
  WARN=$((WARN+1))
fi

# =============================================================================
# 3. AUTHENTICATED API CALLS
# =============================================================================
echo ""
blue "── 3. Authenticated endpoints ──"
assert_http "Backend /api/market/snapshot" \
  "$BACKEND_URL/api/market/snapshot?symbol=$SYMBOL" 200 "${AUTH[@]}"
assert_json_key "snapshot has 'venues'" \
  "$BACKEND_URL/api/market/snapshot?symbol=$SYMBOL" "venues" "${AUTH[@]}"

assert_http "Backend /api/market/arb" \
  "$BACKEND_URL/api/market/arb?symbol=$SYMBOL" 200 "${AUTH[@]}"
assert_json_key "arb has 'best_bid'" \
  "$BACKEND_URL/api/market/arb?symbol=$SYMBOL" "best_bid" "${AUTH[@]}"

assert_http "Backend /api/sentinel/risk" \
  "$BACKEND_URL/api/sentinel/risk?symbol=$SYMBOL" 200 "${AUTH[@]}"
assert_json_key "risk has 'composite_score'" \
  "$BACKEND_URL/api/sentinel/risk?symbol=$SYMBOL" "composite_score" "${AUTH[@]}"

assert_http "Backend /api/sentinel/technicals" \
  "$BACKEND_URL/api/sentinel/technicals?symbol=$SYMBOL" 200 "${AUTH[@]}"
assert_json_key "technicals has 'rsi_14'" \
  "$BACKEND_URL/api/sentinel/technicals?symbol=$SYMBOL" "rsi_14" "${AUTH[@]}"

assert_http "Backend /api/sentinel/calendar" \
  "$BACKEND_URL/api/sentinel/calendar" 200 "${AUTH[@]}"
assert_http "Backend /api/sentinel/geo" \
  "$BACKEND_URL/api/sentinel/geo" 200 "${AUTH[@]}"

# =============================================================================
# 4. ANALYST SERVICE
# =============================================================================
echo ""
blue "── 4. Analyst service ──"
# Context age check
context_body=$(curl -s "${AUTH[@]}" "$ANALYST_URL/api/analyst/context")
age=$(echo "$context_body" | grep -o '"age_s":[^,}]*' | cut -d: -f2 | tr -d ' ')
if [ -n "$age" ] && [ "$age" != "null" ] && [ "${age%.*}" -lt 600 ] 2>/dev/null; then
  green "Analyst context age: ${age}s (< 10 min)"
  PASS=$((PASS+1))
else
  yellow "Analyst context age: ${age:-unknown} — may be stale or not yet polled"
  WARN=$((WARN+1))
fi

assert_http "Analyst /api/analyst/briefing" \
  "$ANALYST_URL/api/analyst/briefing" 200 "${AUTH[@]}"
assert_json_key "briefing has 'briefing'" \
  "$ANALYST_URL/api/analyst/briefing" "briefing" "${AUTH[@]}"

assert_http "Analyst /api/analyst/ask" \
  "$ANALYST_URL/api/analyst/ask?q=What+is+the+current+risk+level%3F" 200 "${AUTH[@]}"

# =============================================================================
# 5. BRAIN SERVICE
# =============================================================================
echo ""
blue "── 5. Brain service ──"
PREDICT_BODY='{"user_id":"smoke-test","rsi":55.0,"atr_score":3.0,"geo_score":3.0,"calendar_score":2.0}'
predict_code=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST -H "Content-Type: application/json" \
  "${AUTH[@]}" -d "$PREDICT_BODY" \
  "$BRAIN_URL/api/brain/predict")
if [ "$predict_code" = "200" ]; then
  green "Brain /api/brain/predict → HTTP 200"
  PASS=$((PASS+1))
else
  red "Brain /api/brain/predict → HTTP $predict_code"
  FAIL=$((FAIL+1))
fi

predict_body=$(curl -s -X POST -H "Content-Type: application/json" \
  "${AUTH[@]}" -d "$PREDICT_BODY" \
  "$BRAIN_URL/api/brain/predict")
if echo "$predict_body" | grep -q '"prediction"'; then
  green "Brain predict response has 'prediction'"
  PASS=$((PASS+1))
else
  red "Brain predict missing 'prediction'. Body: ${predict_body:0:200}"
  FAIL=$((FAIL+1))
fi

# Stats for the smoke-test user (heuristic fallback, should always work)
assert_http "Brain /api/brain/stats/smoke-test" \
  "$BRAIN_URL/api/brain/stats/smoke-test" 200 "${AUTH[@]}"

# =============================================================================
# 6. RATE LIMITING (only realistic to test with many rapid requests)
# =============================================================================
echo ""
blue "── 6. Rate limit check (10 rapid geo requests — limit is 10/min) ──"
got_429=false
for i in $(seq 1 12); do
  code=$(curl -s -o /dev/null -w "%{http_code}" "${AUTH[@]}" \
    "$BACKEND_URL/api/sentinel/geo")
  if [ "$code" = "429" ]; then
    got_429=true
    break
  fi
done
if $got_429; then
  green "Rate limiting active — received 429 after rapid requests"
  PASS=$((PASS+1))
else
  yellow "No 429 received in 12 rapid geo requests (may need more hits or IP-based limiter requires same IP)"
  WARN=$((WARN+1))
fi

# =============================================================================
# 7. SSE STREAM (10-second sample)
# =============================================================================
echo ""
blue "── 7. SSE stream (10s sample) ──"
sse_out=$(curl -s --max-time 10 "${AUTH[@]}" \
  "$BACKEND_URL/api/market/stream?symbol=$SYMBOL&interval_ms=3000" 2>/dev/null | head -c 2000 || true)
if echo "$sse_out" | grep -q '"venues"'; then
  green "SSE stream delivering snapshot events"
  PASS=$((PASS+1))
else
  red "SSE stream produced no parseable snapshot in 10s. Output: ${sse_out:0:200}"
  FAIL=$((FAIL+1))
fi

# =============================================================================
# 8. MODEL PERSISTENCE PATH (brain)
# =============================================================================
echo ""
blue "── 8. Model persistence ──"
health_body=$(curl -s "${AUTH[@]:-}" "$BRAIN_URL/health")
models_count=$(echo "$health_body" | grep -o '"users_with_models":[0-9]*' | cut -d: -f2 || echo "0")
blue "Brain currently has $models_count pre-loaded model(s)"
if echo "$health_body" | grep -q '"users_with_models"'; then
  green "Brain /health exposes users_with_models counter"
  PASS=$((PASS+1))
else
  yellow "Brain /health missing users_with_models key (old binary deployed?)"
  WARN=$((WARN+1))
fi

# =============================================================================
# SUMMARY
# =============================================================================
echo ""
blue "=========================================="
TOTAL=$((PASS+FAIL+WARN))
echo -e "  Total : $TOTAL   \033[32mPASS\033[0m: $PASS   \033[31mFAIL\033[0m: $FAIL   \033[33mWARN\033[0m: $WARN"
blue "=========================================="
echo ""

if [ "$FAIL" -gt 0 ]; then
  red "$FAIL test(s) failed — check output above."
  exit 1
else
  green "All required tests passed."
  exit 0
fi
