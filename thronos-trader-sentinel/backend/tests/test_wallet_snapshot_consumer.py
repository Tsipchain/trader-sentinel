"""Tests for the Wallet Snapshot Consumer."""
import os
import tempfile
import time
from pathlib import Path

import pytest

_tmp_dir = tempfile.mkdtemp(prefix="wallet_snap_test_")
os.environ["WALLET_SNAPSHOT_DIR"] = _tmp_dir

import app.brain.wallet_snapshot_consumer as wsc

wsc.SNAPSHOT_DIR = Path(_tmp_dir)


@pytest.fixture(autouse=True)
def _clean_snapshot():
    path = wsc._snapshot_path()
    if path.exists():
        path.unlink()
    yield
    if path.exists():
        path.unlink()


def _sample_context(n_subscribers=3, include_snapshots=True):
    if not include_snapshots:
        return {"ok": True, "watchlist": [], "native_signals": []}
    snapshots = []
    for i in range(n_subscribers):
        snapshots.append({
            "address": f"THR{'A' * 39}{i}",
            "tier": "pro" if i % 2 == 0 else "starter",
            "rewards_multiplier": 1.5 if i % 2 == 0 else 1.0,
            "active": True,
            "verified": True,
            "expires_at": int(time.time()) + 86400,
            "thr_balance": 100.0 + i * 50,
            "snapshot_at": int(time.time()),
        })
    return {
        "ok": True,
        "wallet_snapshots": snapshots,
        "generated_at": "2026-01-01 12:00:00 UTC",
        "contract_version": "1.0",
    }


# ── Ingestion ──────────────────────────────────────────────────────────────

def test_ingest_wallet_snapshots_list():
    ctx = _sample_context(3)
    count = wsc.ingest_wallet_snapshots(ctx)
    assert count == 3


def test_ingest_wallet_snapshots_dict():
    ctx = {
        "wallet_snapshots": {
            "THR_ADDR_1": {"tier": "pro", "active": True},
            "THR_ADDR_2": {"tier": "starter", "active": True},
        }
    }
    count = wsc.ingest_wallet_snapshots(ctx)
    assert count == 2


def test_ingest_no_snapshots_returns_zero():
    ctx = _sample_context(include_snapshots=False)
    count = wsc.ingest_wallet_snapshots(ctx)
    assert count == 0


def test_ingest_persists_to_disk():
    ctx = _sample_context(2)
    wsc.ingest_wallet_snapshots(ctx)
    snap = wsc._load_snapshot()
    assert len(snap["subscribers"]) == 2
    assert snap["fetched_at"] > 0


# ── Active subscriber count ───────────────────────────────────────────────

def test_active_subscriber_count():
    ctx = _sample_context(5)
    wsc.ingest_wallet_snapshots(ctx)
    assert wsc.get_active_subscriber_count() == 5


def test_active_count_excludes_expired():
    ctx = _sample_context(2)
    ctx["wallet_snapshots"][0]["expires_at"] = int(time.time()) - 3600
    wsc.ingest_wallet_snapshots(ctx)
    assert wsc.get_active_subscriber_count() == 1


def test_active_count_empty_snapshot():
    assert wsc.get_active_subscriber_count() == 0


# ── Tier distribution ─────────────────────────────────────────────────────

def test_subscriber_tiers():
    ctx = _sample_context(4)
    wsc.ingest_wallet_snapshots(ctx)
    tiers = wsc.get_subscriber_tiers()
    assert "pro" in tiers
    assert "starter" in tiers
    assert sum(tiers.values()) == 4


# ── Confidence boost ──────────────────────────────────────────────────────

def test_confidence_boost_zero_subscribers():
    assert wsc.get_confidence_boost() == 0.0


def test_confidence_boost_few_subscribers():
    ctx = _sample_context(5)
    wsc.ingest_wallet_snapshots(ctx)
    assert wsc.get_confidence_boost() == 0.01


def test_confidence_boost_medium_subscribers():
    ctx = _sample_context(25)
    wsc.ingest_wallet_snapshots(ctx)
    assert wsc.get_confidence_boost() == 0.02


def test_confidence_boost_many_subscribers():
    ctx = _sample_context(55)
    wsc.ingest_wallet_snapshots(ctx)
    assert wsc.get_confidence_boost() == 0.03


# ── Freshness ─────────────────────────────────────────────────────────────

def test_fresh_snapshot():
    ctx = _sample_context(1)
    wsc.ingest_wallet_snapshots(ctx)
    assert wsc.is_snapshot_fresh() is True


def test_stale_snapshot():
    ctx = _sample_context(1)
    wsc.ingest_wallet_snapshots(ctx)
    snap = wsc._load_snapshot()
    snap["fetched_at"] = time.time() - 10000
    wsc._save_snapshot(snap)
    assert wsc.is_snapshot_fresh() is False


def test_no_snapshot_is_not_fresh():
    assert wsc.is_snapshot_fresh() is False


# ── Status ────────────────────────────────────────────────────────────────

def test_snapshot_status_empty():
    status = wsc.get_snapshot_status()
    assert status["has_snapshot"] is False
    assert status["total_subscribers"] == 0
    assert status["fresh"] is False


def test_snapshot_status_with_data():
    ctx = _sample_context(10)
    wsc.ingest_wallet_snapshots(ctx)
    status = wsc.get_snapshot_status()
    assert status["has_snapshot"] is True
    assert status["total_subscribers"] == 10
    assert status["active_subscribers"] == 10
    assert status["fresh"] is True
    assert "pro" in status["tiers"]
    assert status["confidence_boost"] == 0.01
