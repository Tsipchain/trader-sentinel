"""
Wallet Snapshot Consumer — ingests subscriber wallet snapshots from SigBalBot.

SigBalBot includes wallet_snapshots in its context endpoint response.
This module stores them locally and exposes subscriber metadata for
signal evaluation (tier-weighted confidence, subscriber count awareness).
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SNAPSHOT_DIR = Path(os.getenv("WALLET_SNAPSHOT_DIR", "data/wallet_snapshots"))
_SNAPSHOT_FILE = "latest_snapshot.json"
_MAX_SNAPSHOT_AGE_S = 7200  # 2 hours — stale after this


def _snapshot_path() -> Path:
    return SNAPSHOT_DIR / _SNAPSHOT_FILE


def _load_snapshot() -> dict[str, Any]:
    path = _snapshot_path()
    if not path.exists():
        return {"subscribers": {}, "fetched_at": 0}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as exc:
        log.warning("[wallet_snap] could not load snapshot: %s", exc)
        return {"subscribers": {}, "fetched_at": 0}


def _save_snapshot(data: dict[str, Any]) -> None:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = _snapshot_path()
    data["saved_at"] = time.time()
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        count = len(data.get("subscribers", {}))
        log.info("[wallet_snap] saved snapshot (%d subscribers)", count)
    except Exception as exc:
        log.warning("[wallet_snap] could not save snapshot: %s", exc)


def ingest_wallet_snapshots(context_response: dict[str, Any]) -> int:
    """Extract wallet_snapshots from a SigBalBot context response and store.

    Returns the number of subscribers ingested.
    """
    snapshots = context_response.get("wallet_snapshots")
    if not snapshots:
        return 0

    if isinstance(snapshots, list):
        subscribers = {}
        for snap in snapshots:
            addr = snap.get("address", "")
            if addr:
                subscribers[addr] = {
                    "tier": snap.get("tier", "starter"),
                    "rewards_multiplier": snap.get("rewards_multiplier", 1.0),
                    "active": snap.get("active", True),
                    "verified": snap.get("verified", False),
                    "expires_at": snap.get("expires_at"),
                    "thr_balance": snap.get("thr_balance"),
                    "snapshot_at": snap.get("snapshot_at"),
                }
    elif isinstance(snapshots, dict):
        subscribers = snapshots
    else:
        log.warning("[wallet_snap] unexpected wallet_snapshots type: %s", type(snapshots).__name__)
        return 0

    data = {
        "subscribers": subscribers,
        "fetched_at": time.time(),
        "source_generated_at": context_response.get("generated_at"),
        "contract_version": context_response.get("contract_version"),
    }
    _save_snapshot(data)
    return len(subscribers)


def get_active_subscriber_count() -> int:
    """Return count of active (non-expired) subscribers from the latest snapshot."""
    snap = _load_snapshot()
    now = time.time()
    count = 0
    for addr, sub in snap.get("subscribers", {}).items():
        if sub.get("active", True):
            expires = sub.get("expires_at")
            if expires is None or expires > now:
                count += 1
    return count


def get_subscriber_tiers() -> dict[str, int]:
    """Return tier distribution from the latest snapshot."""
    snap = _load_snapshot()
    tiers: dict[str, int] = {}
    now = time.time()
    for addr, sub in snap.get("subscribers", {}).items():
        if sub.get("active", True):
            expires = sub.get("expires_at")
            if expires is None or expires > now:
                tier = sub.get("tier", "starter")
                tiers[tier] = tiers.get(tier, 0) + 1
    return tiers


def get_confidence_boost() -> float:
    """Return a confidence boost based on active subscriber count.

    More subscribers = more validation demand = higher quality bar.
    Used by the signal evaluation pipeline to adjust minimum confidence.
    """
    count = get_active_subscriber_count()
    if count >= 50:
        return 0.03
    elif count >= 20:
        return 0.02
    elif count >= 5:
        return 0.01
    return 0.0


def is_snapshot_fresh() -> bool:
    """Check if the latest snapshot is within the max age window."""
    snap = _load_snapshot()
    fetched = snap.get("fetched_at", 0)
    return (time.time() - fetched) < _MAX_SNAPSHOT_AGE_S


def get_snapshot_status() -> dict[str, Any]:
    """Return snapshot health status for observability."""
    snap = _load_snapshot()
    fetched = snap.get("fetched_at", 0)
    sub_count = len(snap.get("subscribers", {}))
    active = get_active_subscriber_count()
    return {
        "has_snapshot": sub_count > 0,
        "total_subscribers": sub_count,
        "active_subscribers": active,
        "tiers": get_subscriber_tiers(),
        "fetched_at": fetched,
        "age_seconds": round(time.time() - fetched) if fetched else None,
        "fresh": is_snapshot_fresh(),
        "confidence_boost": get_confidence_boost(),
    }
