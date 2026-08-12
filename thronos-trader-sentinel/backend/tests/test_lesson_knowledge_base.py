"""
Tests for the Trading Lesson Knowledge Base.

Covers:
  - Register a lesson and verify storage
  - Search by symbol returns relevant lessons
  - Search by pattern type
  - Keyword search across title/description
  - Empty results when no matches
  - Lesson count tracking
  - Duplicate lesson handling (same filename = update, not duplicate)
"""
import json
import os
import tempfile
from pathlib import Path

import pytest

# Point LESSON_DATA_DIR to a temp directory before importing the module,
# so tests never touch real data.
_tmp_dir = tempfile.mkdtemp(prefix="lesson_kb_test_")
os.environ["LESSON_DATA_DIR"] = _tmp_dir

import app.brain.lesson_knowledge_base as kb

# Override the module-level constant so all helpers use the temp dir.
kb.LESSON_DATA_DIR = Path(_tmp_dir)


@pytest.fixture(autouse=True)
def _clean_index():
    """Wipe the index before every test for isolation."""
    index_path = kb._index_path()
    if index_path.exists():
        index_path.unlink()
    yield
    if index_path.exists():
        index_path.unlink()


# ── Registration ────────────────────────────────────────────────────────────

def test_register_lesson_and_verify_stored():
    result = kb.register_lesson(
        title="Head & Shoulders on BTC 4H",
        description="Classic reversal pattern on Bitcoin 4-hour chart",
        patterns=["head_and_shoulders"],
        asset_class="crypto",
        timeframe="4h",
        image_filename="btc_hs_4h.png",
    )
    assert result["title"] == "Head & Shoulders on BTC 4H"
    assert result["patterns"] == ["head_and_shoulders"]
    assert result["asset_class"] == "crypto"
    assert result["timeframe"] == "4h"
    assert result["image_filename"] == "btc_hs_4h.png"
    assert "id" in result
    assert "created_at" in result
    assert "updated_at" in result

    # Verify persisted to disk
    raw = json.loads(kb._index_path().read_text())
    assert len(raw["lessons"]) == 1
    assert raw["lessons"][0]["title"] == "Head & Shoulders on BTC 4H"


def test_register_multiple_lessons():
    kb.register_lesson("Lesson 1", "desc", ["bull_flag"], "crypto", "1h", "img1.png")
    kb.register_lesson("Lesson 2", "desc", ["bear_flag"], "crypto", "1d", "img2.png")
    kb.register_lesson("Lesson 3", "desc", ["wedge"], "forex", "4h", "img3.png")
    assert kb.get_lesson_count() == 3


# ── Duplicate handling ──────────────────────────────────────────────────────

def test_duplicate_filename_updates_instead_of_duplicating():
    first = kb.register_lesson(
        title="Original Title",
        description="Original desc",
        patterns=["bull_flag"],
        asset_class="crypto",
        timeframe="1h",
        image_filename="same_file.png",
    )
    original_id = first["id"]

    second = kb.register_lesson(
        title="Updated Title",
        description="Updated desc",
        patterns=["bull_flag", "channel"],
        asset_class="crypto",
        timeframe="4h",
        image_filename="same_file.png",
    )

    # Same ID preserved, count unchanged
    assert second["id"] == original_id
    assert kb.get_lesson_count() == 1
    assert second["title"] == "Updated Title"
    assert second["timeframe"] == "4h"
    assert set(second["patterns"]) == {"bull_flag", "channel"}

    # created_at should be preserved from the original
    assert second["created_at"] == first["created_at"]


# ── Search by symbol ────────────────────────────────────────────────────────

def test_get_lessons_for_symbol_crypto():
    kb.register_lesson("BTC pattern", "Bitcoin breakout", ["double_bottom"], "crypto", "1h", "btc1.png")
    kb.register_lesson("EURUSD setup", "Forex channel", ["channel"], "forex", "4h", "eur1.png")

    results = kb.get_lessons_for_symbol("BTC/USDT")
    assert len(results) == 1
    assert results[0]["title"] == "BTC pattern"


def test_get_lessons_for_symbol_forex():
    kb.register_lesson("BTC pattern", "Bitcoin breakout", ["double_bottom"], "crypto", "1h", "btc1.png")
    kb.register_lesson("EURUSD setup", "Forex channel", ["channel"], "forex", "4h", "eur1.png")

    results = kb.get_lessons_for_symbol("EUR/USD")
    assert len(results) == 1
    assert results[0]["title"] == "EURUSD setup"


def test_get_lessons_for_symbol_futures_suffix():
    kb.register_lesson("ETH lesson", "Ethereum wedge", ["wedge"], "crypto", "1d", "eth1.png")
    results = kb.get_lessons_for_symbol("ETH/USDT:USDT")
    assert len(results) == 1
    assert results[0]["title"] == "ETH lesson"


def test_get_lessons_for_symbol_no_match():
    kb.register_lesson("BTC pattern", "Bitcoin", ["bull_flag"], "crypto", "1h", "btc1.png")
    results = kb.get_lessons_for_symbol("AAPL")
    # AAPL is in equities, no equities lessons registered
    assert len(results) == 0


def test_get_lessons_for_symbol_empty_index():
    results = kb.get_lessons_for_symbol("BTC/USDT")
    assert results == []


# ── Search by pattern ───────────────────────────────────────────────────────

def test_get_lessons_for_pattern():
    kb.register_lesson("L1", "desc", ["head_and_shoulders", "double_top"], "crypto", "4h", "l1.png")
    kb.register_lesson("L2", "desc", ["bull_flag"], "crypto", "1h", "l2.png")
    kb.register_lesson("L3", "desc", ["head_and_shoulders"], "forex", "1d", "l3.png")

    results = kb.get_lessons_for_pattern("head_and_shoulders")
    assert len(results) == 2
    titles = {r["title"] for r in results}
    assert titles == {"L1", "L3"}


def test_get_lessons_for_pattern_case_insensitive():
    kb.register_lesson("L1", "desc", ["Bull_Flag"], "crypto", "1h", "l1.png")
    results = kb.get_lessons_for_pattern("BULL_FLAG")
    assert len(results) == 1


def test_get_lessons_for_pattern_no_match():
    kb.register_lesson("L1", "desc", ["bull_flag"], "crypto", "1h", "l1.png")
    results = kb.get_lessons_for_pattern("cup_and_handle")
    assert len(results) == 0


# ── Keyword search ──────────────────────────────────────────────────────────

def test_search_lessons_by_title():
    kb.register_lesson("Breakout Strategy", "How to trade breakouts", ["bull_flag"], "crypto", "1h", "brk.png")
    kb.register_lesson("Reversal Patterns", "Head and shoulders intro", ["head_and_shoulders"], "crypto", "4h", "rev.png")

    results = kb.search_lessons("breakout")
    assert len(results) == 1
    assert results[0]["title"] == "Breakout Strategy"


def test_search_lessons_by_description():
    kb.register_lesson("L1", "This covers the ascending triangle pattern", ["ascending_triangle"], "crypto", "1h", "at.png")
    results = kb.search_lessons("ascending triangle")
    assert len(results) == 1


def test_search_lessons_multi_token():
    kb.register_lesson("BTC Double Bottom", "Crypto reversal on Bitcoin", ["double_bottom"], "crypto", "4h", "db.png")
    kb.register_lesson("ETH Bull Flag", "Ethereum continuation", ["bull_flag"], "crypto", "1h", "bf.png")

    results = kb.search_lessons("crypto reversal")
    assert len(results) == 1
    assert results[0]["title"] == "BTC Double Bottom"


def test_search_lessons_empty_query_returns_all():
    kb.register_lesson("L1", "desc1", ["bull_flag"], "crypto", "1h", "l1.png")
    kb.register_lesson("L2", "desc2", ["bear_flag"], "forex", "4h", "l2.png")
    results = kb.search_lessons("")
    assert len(results) == 2


def test_search_lessons_no_match():
    kb.register_lesson("L1", "desc", ["bull_flag"], "crypto", "1h", "l1.png")
    results = kb.search_lessons("nonexistent_keyword_xyz")
    assert len(results) == 0


# ── Lesson count ────────────────────────────────────────────────────────────

def test_lesson_count_empty():
    assert kb.get_lesson_count() == 0


def test_lesson_count_after_registrations():
    kb.register_lesson("L1", "d", ["bull_flag"], "crypto", "1h", "a.png")
    assert kb.get_lesson_count() == 1
    kb.register_lesson("L2", "d", ["bear_flag"], "crypto", "4h", "b.png")
    assert kb.get_lesson_count() == 2
    # Update existing should NOT increase count
    kb.register_lesson("L1 updated", "d", ["bull_flag"], "crypto", "1h", "a.png")
    assert kb.get_lesson_count() == 2


# ── Pattern validation ──────────────────────────────────────────────────────

def test_unknown_pattern_still_stored(caplog):
    """Unknown patterns are stored (forward-compat) but a warning is logged."""
    import logging
    with caplog.at_level(logging.WARNING):
        result = kb.register_lesson("L1", "d", ["unknown_pattern"], "crypto", "1h", "u.png")
    assert "unknown_pattern" in result["patterns"]
    assert any("unknown pattern type" in r.getMessage() for r in caplog.records)


# ── list_all_lessons ────────────────────────────────────────────────────────

def test_list_all_lessons():
    kb.register_lesson("L1", "d1", ["bull_flag"], "crypto", "1h", "a.png")
    kb.register_lesson("L2", "d2", ["bear_flag"], "forex", "4h", "b.png")
    all_lessons = kb.list_all_lessons()
    assert len(all_lessons) == 2
    titles = {l["title"] for l in all_lessons}
    assert titles == {"L1", "L2"}
