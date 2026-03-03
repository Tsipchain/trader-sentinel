"""
Disk-backed persistence for Sentinel Brain.

All data lives under DISK_BASE (env: DISK_PATH, default /disckb):

  /disckb/models/       — pickled ML models (one .pkl per user)
  /disckb/history/      — per-user matched trade records + stats (JSON)
  /disckb/autotrader/   — per-user AutoTrader session config (JSON)
"""
import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

DISK_BASE = Path(os.getenv("DISK_PATH", "/disckb"))

MODELS_DIR     = DISK_BASE / "models"
HISTORY_DIR    = DISK_BASE / "history"
AUTOTRADER_DIR = DISK_BASE / "autotrader"


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
