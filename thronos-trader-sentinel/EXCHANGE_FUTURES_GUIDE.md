# MEXC + AutoTrader/Sleep Mode — Deep Study & Execution Plan (Futures + Spot)

## Στόχος

Να υπάρχει **ρεαλιστικό και υλοποιήσιμο σχέδιο** ώστε το AutoTrader/Sleep Mode να δουλεύει με αξιοπιστία:

- σε **futures** όπου επιτρέπεται πραγματικά order placement,
- και με **spot fallback / spot mode** όταν futures execution δεν είναι διαθέσιμο.

---

## 1) Τι συμβαίνει πρακτικά σήμερα (root causes)

### A. MEXC futures API order placement δεν είναι πάντα αξιόπιστο

Στην πράξη εμφανίζονται failure patterns όπως:

- `Contract not allow place order`
- constraints τύπου "contract activation required"
- inconsistent acceptance σε stop/take-profit order types

Αυτό σημαίνει ότι ένα bot μπορεί να κάνει:

- signal generation,
- polling,
- status/log updates,

αλλά να αποτυγχάνει **μόνο** στο critical σημείο: `create_order`.

### B. Το τρέχον execution path του Sleep Mode είναι futures-first

Ο υπάρχων μηχανισμός βασίζεται σε futures order primitives:

- `create_market_order`
- `create_limit_order`
- `create_stop_loss`
- `create_take_profit`
- `set_leverage`
- `set_margin_mode`

Άρα αν futures endpoint μπλοκάρει στο exchange/account επίπεδο, το strategy “τρέχει” αλλά δεν εκτελεί real trades.

### C. Operational mismatch σε cloud deployments

Συχνές αιτίες μηδενικού trading χωρίς obvious crash:

1. λάθος `BRAIN_URL` (δεν χτυπάς το σωστό brain service),
2. read-only keys αντί για trade-enabled keys,
3. IP whitelist mismatch,
4. in-memory session worker που χάνεται σε restart/scale-to-zero,
5. διαφορετικό process/container για state vs execution.

---

## 2) Exchange επιλογή για real trading (σήμερα)

| Exchange | Futures viability | Spot viability | Recommendation |
|---|---:|---:|---|
| OKX | ✅ υψηλή | ✅ υψηλή | **Primary** για production cloud setup |
| Bybit | ✅ υψηλή* | ✅ υψηλή | **Secondary** (region dependent) |
| Binance | ✅ υψηλή* | ✅ υψηλή | **Secondary** (region dependent) |
| MEXC | ⚠️ μεταβλητή | ✅ καλή | **Spot-first / Futures with strict gating** |

\* ανάλογα με region/compliance restrictions του server.

---

## 3) “Perfect” σχέδιο για MEXC στο δικό μας stack

## Phase 0 — Capability handshake (mandatory πριν enable)

Πριν ενεργοποιηθεί AutoTrader για χρήστη/exchange:

1. **Auth probe**: balance + account permissions.
2. **Market probe**:
   - futures `set_leverage` dry check,
   - futures tiny reduce-only/guarded test (αν επιτρέπεται policy-wise),
   - spot `create_order` tiny test.
3. **Order-type probe**:
   - support για `stop_market` / `take_profit_market` ή required fallback types.

**Result:** Persisted capability profile per user+exchange:

- `futures_enabled: true/false`
- `spot_enabled: true/false`
- `supports_sl_tp_native: true/false`
- `requires_contract_activation: true/false`
- `last_probe_at`

Χωρίς αυτό το handshake, δεν πρέπει να θεωρούμε ότι “το exchange δουλεύει”.

## Phase 1 — Execution router (market-aware)

Ο AutoTrader να μην θεωρεί futures by default. Να επιλέγει route:

- `route = futures` μόνο αν `futures_enabled == true`
- αλλιώς `route = spot`
- αλλιώς hard fail με clear user-facing reason

### Router policy

- **MEXC**: default `spot-first` εκτός αν επιβεβαιωθεί futures capability.
- **OKX/Bybit/Binance**: `futures-first`, με controlled fallback σε spot αν είναι μέρος της στρατηγικής.

## Phase 2 — Protective logic ανά market

### Για futures

- native SL/TP αν υποστηρίζεται,
- αλλιώς synthetic protection (watcher που κλείνει θέση με market exit σε trigger).

### Για spot

- No leverage assumptions,
- exposure caps με quote currency (USDT budget),
- optional OCO/conditional όπου υποστηρίζεται,
- fallback σε watcher-based risk exits.

## Phase 3 — Worker hardening (production reliability)

Μεταφορά Sleep Mode από in-process task σε dedicated worker:

- durable queue/scheduler,
- persisted session state,
- idempotent order submission keys,
- retry policy με exchange-aware backoff,
- dead-letter + alerting.

---

## 4) Minimum acceptance criteria (για να πούμε “δουλεύει σίγουρα”)

1. Για κάθε υποστηριζόμενο exchange/account: επιτυχές capability handshake.
2. 100% explicit routing decision (`futures` / `spot` / blocked with reason).
3. Κάθε open θέση έχει verify-able protection path (native ή synthetic).
4. Session survives restart χωρίς να χάνονται active trades από state.
5. Structured logs + metrics:
   - order submit success rate,
   - reject reasons by exchange/code,
   - fallback rate futures→spot,
   - protection placement success.

---

## 5) Προτεινόμενο rollout

1. **Week 1:** OKX stable futures baseline (small size).
2. **Week 2:** Bybit/Binance enable by region.
3. **Week 3:** MEXC spot-first production + futures only with successful probes.
4. **Week 4:** enable synthetic protection + full worker migration.

---

## 6) Άμεσες κινήσεις (practical next steps)

1. Προσθήκη persisted `exchange capability profile` (user+exchange scope).
2. Υλοποίηση `execution router` με explicit `market_mode` decision.
3. Προσθήκη `spot execution` path στο AutoTrader (όχι μόνο futures path).
4. Health panel στο mobile για:
   - route selected,
   - probe status,
   - γιατί μπλοκαρίστηκε execution.

Αυτό είναι το ελάχιστο σύνολο αλλαγών για να μεταβούμε από “ίσως κάνει trade” σε **προβλέψιμο, production-grade behavior**.
