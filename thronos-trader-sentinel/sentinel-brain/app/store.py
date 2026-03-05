"""
Disk-backed persistence for Sentinel Brain.

All data lives under DISK_BASE (env: DISK_PATH, default /disckb):

  /disckb/models/       — pickled ML models (one .pkl per user)
  /disckb/history/      — per-user matched trade records + stats (JSON)
  /disckb/autotrader/   — per-user AutoTrader session config (JSON)
  /disckb/analysis/     — per-user LLM analysis memory snapshots (JSON)
  /disckb/subscriptions/— subscription fingerprints + metadata (JSON)
  /disckb/security/     — defensive security events (JSON)
"""
import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

DISK_BASE = Path(os.getenv("DISK_PATH", "/disckb"))

MODELS_DIR = DISK_BASE / "models"
HISTORY_DIR = DISK_BASE / "history"
AUTOTRADER_DIR = DISK_BASE / "autotrader"
ANALYSIS_DIR = DISK_BASE / "analysis"
SUBSCRIPTIONS_DIR = DISK_BASE / "subscriptions"
SECURITY_DIR = DISK_BASE / "security"


def _safe(user_id: str) -> str:
    """Sanitise user_id to a safe filename."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in user_id)


# ── Trade History ─────────────────────────────────────────────────────────────

def save_history(user_id: str, trades: list[dict], stats: dict) -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    path = HISTORY_DIR / f"{_safe(user_id)}.json"
    try:
        with open(path, "w") as f:
            json.dump({"trades": trades, "stats": stats}, f)
        log.info("[store] saved %d trade records for user=%s", len(trades), user_id)
    except Exception as exc:
        log.warning("[store] could not save history for user=%s: %s", user_id, exc)


def load_history(user_id: str) -> dict | None:
    path = HISTORY_DIR / f"{_safe(user_id)}.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as exc:
        log.warning("[store] could not load history for user=%s: %s", user_id, exc)
        return None


# ── LLM Analysis Memory ───────────────────────────────────────────────────────

def save_analysis_snapshot(user_id: str, snapshot: dict) -> None:
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    path = ANALYSIS_DIR / f"{_safe(user_id)}.json"

    existing = {
        "entries": [],
        "updated_at": snapshot.get("created_at") or snapshot.get("ts"),
    }
    if path.exists():
        try:
            with open(path) as f:
                existing = json.load(f)
        except Exception as exc:
            log.warning("[store] could not read analysis memory for user=%s: %s", user_id, exc)

    entries = existing.get("entries", [])
    entries.append(snapshot)
    # Keep bounded memory per user for stable disk usage.
    existing["entries"] = entries[-200:]
    existing["updated_at"] = snapshot.get("created_at") or snapshot.get("ts")

    try:
        with open(path, "w") as f:
            json.dump(existing, f)
        log.info("[store] saved analysis snapshot for user=%s (total=%d)", user_id, len(existing["entries"]))
    except Exception as exc:
        log.warning("[store] could not save analysis memory for user=%s: %s", user_id, exc)


def load_analysis_memory(user_id: str) -> dict | None:
    path = ANALYSIS_DIR / f"{_safe(user_id)}.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as exc:
        log.warning("[store] could not load analysis memory for user=%s: %s", user_id, exc)
        return None


# ── Subscription Fingerprints ────────────────────────────────────────────────

def save_subscription_fingerprint(sub_hash: str, payload: dict) -> None:
    SUBSCRIPTIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = SUBSCRIPTIONS_DIR / f"{_safe(sub_hash)}.json"
    try:
        with open(path, "w") as f:
            json.dump(payload, f)
        log.info("[store] saved subscription fingerprint=%s", sub_hash)
    except Exception as exc:
        log.warning("[store] could not save subscription fingerprint=%s: %s", sub_hash, exc)


def load_subscription_fingerprint(sub_hash: str) -> dict | None:
    path = SUBSCRIPTIONS_DIR / f"{_safe(sub_hash)}.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as exc:
        log.warning("[store] could not load subscription fingerprint=%s: %s", sub_hash, exc)
        return None


# ── Security Events (defensive only) ─────────────────────────────────────────

def append_security_event(user_id: str, event: dict) -> None:
    SECURITY_DIR.mkdir(parents=True, exist_ok=True)
    path = SECURITY_DIR / f"{_safe(user_id or 'anonymous')}.json"
    existing = {"events": [], "updated_at": event.get("created_at")}
    if path.exists():
        try:
            with open(path) as f:
                existing = json.load(f)
        except Exception as exc:
            log.warning("[store] could not read security log user=%s: %s", user_id, exc)

    events = existing.get("events", [])
    events.append(event)
    existing["events"] = events[-500:]
    existing["updated_at"] = event.get("created_at")

    try:
        with open(path, "w") as f:
            json.dump(existing, f)
        log.info("[store] appended security event user=%s total=%d", user_id, len(existing["events"]))
    except Exception as exc:
        log.warning("[store] could not append security event user=%s: %s", user_id, exc)


def load_security_events(user_id: str, limit: int = 100) -> list[dict]:
    path = SECURITY_DIR / f"{_safe(user_id or 'anonymous')}.json"
    if not path.exists():
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        return data.get("events", [])[-limit:]
    except Exception as exc:
        log.warning("[store] could not load security events user=%s: %s", user_id, exc)
        return []


# ── AutoTrader Sessions ───────────────────────────────────────────────────────

def save_autotrader(user_id: str, session: dict) -> None:
    AUTOTRADER_DIR.mkdir(parents=True, exist_ok=True)
    path = AUTOTRADER_DIR / f"{_safe(user_id)}.json"
    try:
        with open(path, "w") as f:
            json.dump(session, f)
        log.info("[store] saved autotrader session for user=%s", user_id)
    except Exception as exc:
        log.warning("[store] could not save autotrader for user=%s: %s", user_id, exc)


def load_autotrader(user_id: str) -> dict | None:
    path = AUTOTRADER_DIR / f"{_safe(user_id)}.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as exc:
        log.warning("[store] could not load autotrader for user=%s: %s", user_id, exc)
        return None


def delete_autotrader(user_id: str) -> None:
    path = AUTOTRADER_DIR / f"{_safe(user_id)}.json"
    path.unlink(missing_ok=True)
    log.info("[store] deleted autotrader session for user=%s", user_id)


def storage_status() -> dict:
    def _count_files(path: Path, suffix: str = "") -> int:
        if not path.exists():
            return 0
        pattern = f"*{suffix}" if suffix else "*"
        return sum(1 for _ in path.glob(pattern) if _.is_file())

    return {
        "disk_path": str(DISK_BASE),
        "history": {
            "path": str(HISTORY_DIR),
            "files": _count_files(HISTORY_DIR, ".json"),
            "exists": HISTORY_DIR.exists(),
        },
        "models": {
            "path": str(MODELS_DIR),
            "files": _count_files(MODELS_DIR, ".pkl"),
            "exists": MODELS_DIR.exists(),
        },
        "autotrader": {
            "path": str(AUTOTRADER_DIR),
            "files": _count_files(AUTOTRADER_DIR, ".json"),
            "exists": AUTOTRADER_DIR.exists(),
        },
        "analysis": {
            "path": str(ANALYSIS_DIR),
            "files": _count_files(ANALYSIS_DIR, ".json"),
            "exists": ANALYSIS_DIR.exists(),
        },
        "subscriptions": {
            "path": str(SUBSCRIPTIONS_DIR),
            "files": _count_files(SUBSCRIPTIONS_DIR, ".json"),
            "exists": SUBSCRIPTIONS_DIR.exists(),
        },
        "security": {
            "path": str(SECURITY_DIR),
            "files": _count_files(SECURITY_DIR, ".json"),
            "exists": SECURITY_DIR.exists(),
        },
    }
