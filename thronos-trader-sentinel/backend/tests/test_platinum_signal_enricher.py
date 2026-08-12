"""Tests for the Platinum Signal Enricher."""
import os
import tempfile
from pathlib import Path

import pytest

_tmp_dir = tempfile.mkdtemp(prefix="platinum_enricher_test_")
os.environ["LESSON_DATA_DIR"] = _tmp_dir

import app.brain.lesson_knowledge_base as kb
import app.brain.platinum_signal_enricher as enricher

kb.LESSON_DATA_DIR = Path(_tmp_dir)


@pytest.fixture(autouse=True)
def _clean_index():
    index_path = kb._index_path()
    if index_path.exists():
        index_path.unlink()
    yield
    if index_path.exists():
        index_path.unlink()


def _register_btc_lesson():
    return kb.register_lesson(
        title="BTC Head & Shoulders",
        description="Classic reversal on Bitcoin",
        patterns=["head_and_shoulders", "double_top"],
        asset_class="crypto",
        timeframe="4h",
        image_filename="btc_hs.png",
    )


def _register_forex_lesson():
    return kb.register_lesson(
        title="EUR/USD Channel Play",
        description="Channel breakout on forex",
        patterns=["channel"],
        asset_class="forex",
        timeframe="1d",
        image_filename="eurusd_channel.png",
    )


def _base_payload(symbol="BTC/USDT"):
    return {
        "id": "sentinel-BTC-USDT-LONG-1234",
        "symbol": symbol,
        "signal": "LONG",
        "confidence": 0.72,
        "entry": 50000,
        "tp1": 52000,
        "sl": 49000,
        "reason": "sentinel-composite",
    }


def _base_ta(**overrides):
    ta = {
        "rsi": 45,
        "macd_trend": "bullish",
        "ema_cross": "golden",
        "bb_position": "neutral",
    }
    ta.update(overrides)
    return ta


# ── Symbol matching ─────────────────────────────────────────────────────────

def test_enriches_with_symbol_lessons():
    _register_btc_lesson()
    payload = _base_payload()
    ta = _base_ta()

    result = enricher.enrich_signal(payload, ta)

    assert "platinum_context" in result
    ctx = result["platinum_context"]
    assert ctx["match_count"] >= 1
    refs = ctx["lesson_references"]
    assert any(r["title"] == "BTC Head & Shoulders" for r in refs)


def test_no_enrichment_when_no_lessons():
    payload = _base_payload()
    ta = _base_ta()

    result = enricher.enrich_signal(payload, ta)

    assert "platinum_context" not in result


def test_no_enrichment_for_unmatched_symbol():
    _register_forex_lesson()
    payload = _base_payload("BTC/USDT")
    ta = _base_ta()

    result = enricher.enrich_signal(payload, ta)

    assert "platinum_context" not in result


def test_enriches_forex_symbol():
    _register_forex_lesson()
    payload = _base_payload("EUR/USD")
    ta = _base_ta(bb_position="overbought")

    result = enricher.enrich_signal(payload, ta)

    assert "platinum_context" in result
    assert result["platinum_context"]["match_count"] >= 1


# ── Pattern matching ────────────────────────────────────────────────────────

def test_divergence_maps_to_pattern():
    kb.register_lesson("Double Bottom Setup", "desc", ["double_bottom"], "crypto", "4h", "db.png")
    payload = _base_payload()
    ta = _base_ta(divergence={"type": "regular_bullish"})

    result = enricher.enrich_signal(payload, ta)

    assert "platinum_context" in result
    assert "double_bottom" in result["platinum_context"]["detected_patterns"]


def test_bearish_divergence_maps_to_double_top():
    kb.register_lesson("Double Top Warning", "desc", ["double_top"], "crypto", "4h", "dt.png")
    payload = _base_payload()
    ta = _base_ta(divergence={"type": "regular_bearish"})

    result = enricher.enrich_signal(payload, ta)

    assert "platinum_context" in result
    assert "double_top" in result["platinum_context"]["detected_patterns"]


def test_bb_squeeze_maps_to_wedge():
    kb.register_lesson("Wedge Breakout", "desc", ["wedge"], "crypto", "1h", "wedge.png")
    payload = _base_payload()
    ta = _base_ta(bb_position="squeeze")

    result = enricher.enrich_signal(payload, ta)

    assert "platinum_context" in result
    assert "wedge" in result["platinum_context"]["detected_patterns"]


def test_strong_bullish_confluence_maps_to_ascending_triangle():
    kb.register_lesson("Ascending Triangle", "desc", ["ascending_triangle"], "crypto", "4h", "at.png")
    payload = _base_payload()
    ta = _base_ta(confluence={"level": "strong", "direction": "bullish"})

    result = enricher.enrich_signal(payload, ta)

    assert "platinum_context" in result
    assert "ascending_triangle" in result["platinum_context"]["detected_patterns"]


# ── Dedup and limits ────────────────────────────────────────────────────────

def test_no_duplicate_lessons_in_refs():
    _register_btc_lesson()
    payload = _base_payload()
    ta = _base_ta(divergence={"type": "regular_bearish"})

    result = enricher.enrich_signal(payload, ta)

    ctx = result["platinum_context"]
    ids = [r["lesson_id"] for r in ctx["lesson_references"]]
    assert len(ids) == len(set(ids))


def test_max_five_lesson_refs():
    for i in range(8):
        kb.register_lesson(f"Lesson {i}", "desc", ["bull_flag"], "crypto", "4h", f"img{i}.png")
    payload = _base_payload()
    ta = _base_ta()

    result = enricher.enrich_signal(payload, ta)

    assert len(result["platinum_context"]["lesson_references"]) <= 5


# ── Ref structure ───────────────────────────────────────────────────────────

def test_lesson_ref_has_required_fields():
    _register_btc_lesson()
    payload = _base_payload()
    ta = _base_ta()

    result = enricher.enrich_signal(payload, ta)

    ref = result["platinum_context"]["lesson_references"][0]
    assert "lesson_id" in ref
    assert "title" in ref
    assert "image_filename" in ref
    assert "patterns" in ref
    assert "timeframe" in ref


# ── Empty symbol ────────────────────────────────────────────────────────────

def test_empty_symbol_returns_payload_unchanged():
    _register_btc_lesson()
    payload = _base_payload("")
    ta = _base_ta()

    result = enricher.enrich_signal(payload, ta)

    assert "platinum_context" not in result


# ── Reason-based pattern extraction ─────────────────────────────────────────

def test_extracts_pattern_from_reason_string():
    kb.register_lesson("Bull Flag Play", "desc", ["bull_flag"], "crypto", "1h", "bf.png")
    payload = _base_payload()
    payload["reason"] = "hidden_bullish (2x confirmed)"
    ta = _base_ta()

    result = enricher.enrich_signal(payload, ta)

    assert "platinum_context" in result
    assert "bull_flag" in result["platinum_context"]["detected_patterns"]
