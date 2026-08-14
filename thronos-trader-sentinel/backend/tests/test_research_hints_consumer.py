"""
Research Hints Consumer — 12 test cases covering:

  1. capability discovery
  2. missing research_hints (backward compat)
  3. malformed research_hints
  4. keyword caps and case-insensitive deduplication
  5. restart-safe event deduplication
  6. durable cursor recovery
  7. stale-event rejection (>72h)
  8. unverified-source rejection
  9. keyword match not producing a trade signal
  10. verified event enriching risk context
  11. no native-signal echo (feedback loop guard)
  12. secrets absent from logs/admin/API responses
"""
import ast
import hashlib
import json
import os
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch, MagicMock

import pytest

_tmp_dir = tempfile.mkdtemp(prefix="research_hints_test_")
os.environ.setdefault("DISK_PATH", _tmp_dir)

import app.brain.research_hints_consumer as rhc
import app.brain.store as brain_store


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset module-level state between tests."""
    rhc.research_hints_enabled = False
    rhc._active_keywords = []
    rhc._active_asset_classes = []
    rhc._stats.update({
        "accepted": 0,
        "rejected": 0,
        "duplicates": 0,
        "last_verified_event": None,
        "last_error_code": None,
    })
    yield


def _fresh_event_at() -> str:
    """Return an ISO timestamp 1 hour ago (within the 72h window)."""
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def _stale_event_at() -> str:
    """Return an ISO timestamp 4 days ago (outside the 72h window)."""
    return (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()


def _make_verified_event(
    title: str = "FOMC Rate Decision",
    category: str = "macro",
    event_type: str = "rate_decision",
    source: str = "reuters",
    source_url: str = "https://example.com/fomc",
    event_at: str | None = None,
) -> dict[str, Any]:
    """Build and verify a research event."""
    evt = rhc.build_research_event(
        category=category,
        event_type=event_type,
        title=title,
        source=source,
        source_url=source_url,
        published_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        event_at=event_at or _fresh_event_at(),
        affected_symbols=["BTC/USDT"],
        importance="high",
        summary="Fed holds rates steady.",
    )
    assert evt is not None
    return rhc.verify_event(evt)


# ── Test 1: Capability Discovery ─────────────────────────────────────────────

def test_capability_discovery_enabled():
    """research_hints capability is detected from status body."""
    body = {
        "contract_version": "1.0",
        "capabilities": {
            "context_feed": "/api/v1/context/trader-sentinel",
            "research_hints": True,
        },
    }
    result = rhc.check_research_hints_capability(body)
    assert result is True
    assert rhc.research_hints_enabled is True


def test_capability_discovery_disabled():
    """research_hints missing or false leaves module disabled."""
    body_missing = {"capabilities": {"context_feed": "/api/v1/context/trader-sentinel"}}
    assert rhc.check_research_hints_capability(body_missing) is False
    assert rhc.research_hints_enabled is False

    body_false = {"capabilities": {"research_hints": False}}
    assert rhc.check_research_hints_capability(body_false) is False


# ── Test 2: Missing research_hints (backward compat) ─────────────────────────

def test_missing_research_hints_returns_none():
    """Context without research_hints returns None — no crash, no events."""
    context = {
        "ok": True,
        "watchlist": [{"symbol": "BTC/USDT"}],
    }
    parsed = rhc.parse_research_hints(context)
    assert parsed is None


def test_process_without_capability_gate():
    """process_research_hints is a no-op when capability is disabled."""
    rhc.research_hints_enabled = False
    context = {
        "research_hints": {
            "version": "1.0",
            "keywords": ["cpi"],
            "asset_classes": ["crypto"],
            "purpose": "calendar-and-research-attention",
        },
    }
    events, ids = rhc.process_research_hints(context, set())
    assert events == []


# ── Test 3: Malformed research_hints ──────────────────────────────────────────

def test_malformed_hints_string():
    """research_hints as a string is rejected gracefully."""
    context = {"research_hints": "not a dict"}
    parsed = rhc.parse_research_hints(context)
    assert parsed is None
    assert rhc._stats["last_error_code"] == "MALFORMED_HINTS"


def test_malformed_hints_list():
    """research_hints as a list is rejected gracefully."""
    context = {"research_hints": ["cpi", "fomc"]}
    parsed = rhc.parse_research_hints(context)
    assert parsed is None
    assert rhc._stats["last_error_code"] == "MALFORMED_HINTS"


def test_unsupported_version():
    """Unsupported hint version is rejected."""
    context = {"research_hints": {"version": "99.0", "keywords": ["cpi"]}}
    parsed = rhc.parse_research_hints(context)
    assert parsed is None
    assert rhc._stats["last_error_code"] == "UNSUPPORTED_VERSION"


# ── Test 4: Keyword Caps and Case-Insensitive Deduplication ───────────────────

def test_keyword_cap_at_60():
    """Keywords beyond the 60-item cap are dropped."""
    keywords = [f"kw_{i}" for i in range(100)]
    context = {
        "research_hints": {
            "version": "1.0",
            "keywords": keywords,
            "asset_classes": ["crypto"],
            "purpose": "calendar-and-research-attention",
        },
    }
    parsed = rhc.parse_research_hints(context)
    assert parsed is not None
    assert len(parsed["keywords"]) == 60


def test_keyword_case_insensitive_dedup():
    """CPI, cpi, Cpi are treated as duplicates; first form survives."""
    context = {
        "research_hints": {
            "version": "1.0",
            "keywords": ["CPI", "cpi", "Cpi", "FOMC", "fomc"],
            "asset_classes": ["crypto"],
        },
    }
    parsed = rhc.parse_research_hints(context)
    assert parsed is not None
    assert len(parsed["keywords"]) == 2
    assert parsed["keywords"][0] == "CPI"
    assert parsed["keywords"][1] == "FOMC"


def test_keyword_length_truncation():
    """Keywords longer than 60 chars are truncated, not rejected."""
    long_kw = "a" * 100
    context = {
        "research_hints": {
            "version": "1.0",
            "keywords": [long_kw],
        },
    }
    parsed = rhc.parse_research_hints(context)
    assert parsed is not None
    assert len(parsed["keywords"][0]) == 60


# ── Test 5: Restart-Safe Event Deduplication ──────────────────────────────────

def test_deterministic_event_id():
    """Same inputs always produce the same event_id."""
    id1 = rhc.generate_event_id("macro", "rate_decision", "FOMC Hold", "2025-01-15T18:00:00Z")
    id2 = rhc.generate_event_id("macro", "rate_decision", "FOMC Hold", "2025-01-15T18:00:00Z")
    assert id1 == id2
    assert id1.startswith("evt-")


def test_duplicate_event_rejected_by_accept():
    """An event already in processed_ids is rejected as duplicate."""
    evt = _make_verified_event()
    processed = set()
    assert rhc.accept_event(evt, processed) is True
    assert rhc._stats["accepted"] == 1

    assert rhc.accept_event(evt, processed) is False
    assert rhc._stats["duplicates"] == 1


# ── Test 6: Durable Cursor Recovery ──────────────────────────────────────────

def test_processed_event_ids_persist_and_reload():
    """Processed event IDs survive save/load cycle."""
    ids = ["evt-abc123", "evt-def456", "evt-ghi789"]
    brain_store.save_processed_event_ids(ids)
    loaded = brain_store.load_processed_event_ids()
    assert loaded == ids


def test_processed_event_ids_bounded():
    """Processed IDs are bounded to prevent unbounded disk growth."""
    ids = [f"evt-{i:06d}" for i in range(2000)]
    brain_store.save_processed_event_ids(ids)
    loaded = brain_store.load_processed_event_ids()
    assert len(loaded) == 1000
    assert loaded[0] == "evt-001000"


# ── Test 7: Stale-Event Rejection ─────────────────────────────────────────────

def test_stale_event_rejected():
    """Events older than 72 hours are rejected."""
    evt = rhc.build_research_event(
        category="macro",
        event_type="rate_decision",
        title="Old FOMC",
        source="reuters",
        source_url="https://example.com",
        published_at="2020-01-01T00:00:00Z",
        event_at=_stale_event_at(),
        affected_symbols=["BTC/USDT"],
    )
    assert evt is None
    assert rhc._stats["last_error_code"] == "STALE_EVENT"


def test_fresh_event_accepted():
    """Events within 72 hours are built successfully."""
    evt = rhc.build_research_event(
        category="macro",
        event_type="rate_decision",
        title="Fresh FOMC",
        source="reuters",
        source_url="https://example.com",
        published_at=_fresh_event_at(),
        event_at=_fresh_event_at(),
        affected_symbols=["BTC/USDT"],
    )
    assert evt is not None
    assert evt["event_id"].startswith("evt-")


# ── Test 8: Unverified-Source Rejection ───────────────────────────────────────

def test_unverified_event_not_accepted():
    """Events without source_url fail verification and are rejected by accept_event."""
    evt = rhc.build_research_event(
        category="macro",
        event_type="rate_decision",
        title="Unverified FOMC",
        source="rumor",
        source_url="",
        published_at="",
        event_at=_fresh_event_at(),
        affected_symbols=["BTC/USDT"],
    )
    assert evt is not None
    verified = rhc.verify_event(evt)
    assert verified["verification_status"] == "unverified"
    assert verified["confidence"] == 0.0

    processed = set()
    accepted = rhc.accept_event(verified, processed)
    assert accepted is False
    assert rhc._stats["last_error_code"] == "UNVERIFIED"


def test_verified_event_accepted():
    """Events with source, source_url, and published_at pass verification."""
    evt = _make_verified_event()
    assert evt["verification_status"] == "verified"
    assert evt["confidence"] == 0.7


# ── Test 9: Keyword Match Not Producing a Trade ──────────────────────────────

def test_keyword_match_never_creates_signal():
    """process_research_hints returns events, NOT trade signals.

    The return type is (list[event_dict], set[str]) — never a signal
    with 'id', 'signal' (LONG/SHORT), 'entry', 'sl', 'tp1' keys.
    """
    rhc.research_hints_enabled = True
    context = {
        "research_hints": {
            "version": "1.0",
            "keywords": ["cpi", "fomc", "funding rate"],
            "asset_classes": ["crypto"],
            "purpose": "calendar-and-research-attention",
        },
    }
    events, updated_ids = rhc.process_research_hints(context, set())
    for evt in events:
        assert "signal" not in evt or evt.get("signal") not in ("LONG", "SHORT")
        assert "entry" not in evt
        assert "sl" not in evt
        assert "tp1" not in evt


def test_classify_keyword_categories():
    """Keywords are classified into the correct category."""
    assert rhc.classify_keyword("funding rate") == "crypto"
    assert rhc.classify_keyword("CPI") == "macro"
    assert rhc.classify_keyword("earnings") == "equities"
    assert rhc.classify_keyword("gold") == "metals"
    assert rhc.classify_keyword("unknown-keyword-xyz") == "macro"


# ── Test 10: Verified Event Enriches Risk Context ─────────────────────────────

def test_verified_high_importance_enriches_risk():
    """High-importance verified events raise composite_score and set warning."""
    risk_data = {"composite_score": 5.0, "market_regime": "bullish"}
    events = [_make_verified_event(title="FOMC Rate Hike")]

    enriched = rhc.enrich_risk_context(risk_data, events)
    assert enriched["event_risk_warning"] is True
    assert enriched["composite_score"] == 5.5
    assert enriched["research_events_count"] == 1
    assert enriched["high_importance_events"] == 1


def test_unverified_events_do_not_enrich_risk():
    """Unverified events leave risk_data unchanged."""
    risk_data = {"composite_score": 5.0}
    evt = rhc.build_research_event(
        category="macro",
        event_type="rumor",
        title="Unverified rumor",
        source="anon",
        source_url="",
        published_at="",
        event_at=_fresh_event_at(),
        affected_symbols=[],
    )
    assert evt is not None
    enriched = rhc.enrich_risk_context(risk_data, [evt])
    assert enriched["composite_score"] == 5.0
    assert "event_risk_warning" not in enriched


def test_enrichment_caps_at_10():
    """composite_score is capped at 10.0 even with many high-importance events."""
    risk_data = {"composite_score": 9.0}
    events = [_make_verified_event(title=f"Event {i}") for i in range(10)]
    enriched = rhc.enrich_risk_context(risk_data, events)
    assert enriched["composite_score"] == 10.0


# ── Test 11: No Native-Signal Echo ────────────────────────────────────────────

def test_no_native_signal_echo():
    """Research events do not contain sentinel signal IDs or trade directions.

    The feedback loop guard in the poller (_is_sentinel_signal) prevents
    re-evaluating Sentinel's own signals. Research events must never carry
    signal-like fields that could be confused with trade signals.
    """
    evt = _make_verified_event()
    assert not str(evt.get("event_id", "")).startswith("sentinel-")
    assert "signal" not in evt
    assert evt.get("event_type") != "LONG"
    assert evt.get("event_type") != "SHORT"


def test_event_id_prefix_is_evt():
    """All event IDs use the 'evt-' prefix, never 'sentinel-'."""
    eid = rhc.generate_event_id("crypto", "listing", "New Token", "2025-06-01T00:00:00Z")
    assert eid.startswith("evt-")
    assert not eid.startswith("sentinel-")


# ── Test 12: Secrets Absent from Logs/Admin/API ──────────────────────────────

def test_diagnostics_contain_no_secrets():
    """get_diagnostics() output has no API keys, tokens, or credentials."""
    rhc.research_hints_enabled = True
    rhc._active_keywords = ["cpi", "fomc"]
    rhc._active_asset_classes = ["crypto", "metals"]
    rhc._stats["accepted"] = 5
    rhc._stats["last_verified_event"] = "FOMC Hold"

    diag = rhc.get_diagnostics()

    diag_str = json.dumps(diag).lower()
    for forbidden in (
        "api_key", "api-key", "bearer", "authorization",
        "private_key", "mnemonic", "seed_phrase", "secret",
        "password", "token",
    ):
        assert forbidden not in diag_str, f"diagnostics contains forbidden term: {forbidden}"

    expected_keys = {
        "research_hints_enabled", "active_keywords_count",
        "active_asset_classes", "accepted", "rejected",
        "duplicates", "last_verified_event", "last_error_code",
    }
    assert set(diag.keys()) == expected_keys


def test_research_event_contains_no_secrets():
    """Built research events must not contain API keys or auth material."""
    evt = _make_verified_event()
    evt_str = json.dumps(evt).lower()
    for forbidden in (
        "api_key", "bearer", "authorization", "private_key",
        "mnemonic", "seed_phrase", "secret_key", "sigbalbot_api_key",
    ):
        assert forbidden not in evt_str, f"event contains forbidden term: {forbidden}"


def test_source_code_no_secret_logging():
    """research_hints_consumer.py must not log API keys or auth headers."""
    source_path = (
        Path(__file__).resolve().parent.parent
        / "app" / "brain" / "research_hints_consumer.py"
    )
    assert source_path.exists(), f"missing: {source_path}"
    source = source_path.read_text()

    tree = ast.parse(source)
    forbidden_attrs = {"api_key", "private_key", "mnemonic", "seed_phrase", "secret_key"}
    sink_names = {"print", "log"}

    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr
            if isinstance(node.func.value, ast.Name) and node.func.value.id in sink_names:
                func_name = f"{node.func.value.id}.{node.func.attr}"

        is_print = func_name == "print"
        is_log = any(func_name.startswith(f"{s}.") for s in sink_names)
        if not (is_print or is_log):
            continue

        for arg in ast.walk(node):
            if isinstance(arg, ast.Attribute) and arg.attr in forbidden_attrs:
                violations.append(
                    f"line {node.lineno}: {func_name}() references .{arg.attr}"
                )
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                val_lower = arg.value.lower()
                for term in ("authorization", "bearer", "api_key", "private_key"):
                    if term in val_lower:
                        violations.append(
                            f"line {node.lineno}: {func_name}() contains '{arg.value[:40]}'"
                        )

    assert violations == [], (
        "research_hints_consumer.py leaks secret material:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )
