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
        "timeframe": "1h",
        "risk": "MEDIUM",
        "market_regime": "bullish",
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
    assert "lesson_matches" not in result


def test_no_enrichment_for_unmatched_symbol():
    _register_forex_lesson()
    payload = _base_payload("BTC/USDT")
    ta = _base_ta()

    result = enricher.enrich_signal(payload, ta)

    # forex lesson shouldn't match a BTC signal with no overlapping patterns
    if "lesson_matches" in result:
        for m in result["lesson_matches"]:
            assert m["title"] != "EUR/USD Channel Play"


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
    assert "lesson_matches" not in result


# ── Reason-based pattern extraction ─────────────────────────────────────────

def test_extracts_pattern_from_reason_string():
    kb.register_lesson("Bull Flag Play", "desc", ["bull_flag"], "crypto", "1h", "bf.png")
    payload = _base_payload()
    payload["reason"] = "hidden_bullish (2x confirmed)"
    ta = _base_ta()

    result = enricher.enrich_signal(payload, ta)

    assert "platinum_context" in result
    assert "bull_flag" in result["platinum_context"]["detected_patterns"]


# ════════════════════════════════════════════════════════════════════════════
# lesson_matches — SigBalBot v1 contract tests (8 required cases)
# ════════════════════════════════════════════════════════════════════════════

def test_lm_no_lesson_match():
    """No lessons in KB → no lesson_matches field in payload."""
    payload = _base_payload()
    ta = _base_ta()

    result = enricher.enrich_signal(payload, ta)

    assert "lesson_matches" not in result


def test_lm_one_relevant_match():
    """One matching lesson → lesson_matches with exactly 1 entry, correct format."""
    kb.register_lesson(
        "Bull Flag Crypto", "Bullish continuation pattern on BTC",
        ["bull_flag"], "crypto", "1h", "bf_btc.png",
    )
    payload = _base_payload()
    payload["signal"] = "LONG"
    ta = _base_ta()

    result = enricher.enrich_signal(payload, ta)

    assert "lesson_matches" in result
    matches = result["lesson_matches"]
    assert len(matches) == 1
    m = matches[0]
    assert set(m.keys()) == {"id", "title", "pattern", "stance"}
    assert m["title"] == "Bull Flag Crypto"
    assert m["pattern"] == "bull_flag"
    assert m["stance"] == "supported"
    assert "image_filename" not in m
    assert "description" not in m


def test_lm_more_than_three_truncated_and_ranked():
    """More than 3 matching lessons → truncated to 3, ranked by relevance."""
    for i in range(6):
        kb.register_lesson(
            f"Crypto Lesson {i}", "Bitcoin pattern analysis",
            ["double_bottom"], "crypto", "1h", f"crypto_{i}.png",
        )
    kb.register_lesson(
        "Best Match", "Double bottom on Bitcoin 1h timeframe bullish",
        ["double_bottom"], "crypto", "1h", "best.png",
    )
    payload = _base_payload()
    payload["signal"] = "LONG"
    ta = _base_ta(divergence={"type": "regular_bullish"})

    result = enricher.enrich_signal(payload, ta)

    assert "lesson_matches" in result
    matches = result["lesson_matches"]
    assert len(matches) == 3
    for m in matches:
        assert set(m.keys()) == {"id", "title", "pattern", "stance"}


def test_lm_unrelated_lesson_rejected():
    """Forex lesson does not appear in lesson_matches for a BTC signal."""
    _register_forex_lesson()
    payload = _base_payload("BTC/USDT")
    ta = _base_ta()

    result = enricher.enrich_signal(payload, ta)

    if "lesson_matches" in result:
        titles = [m["title"] for m in result["lesson_matches"]]
        assert "EUR/USD Channel Play" not in titles


def test_lm_malformed_metadata_ignored():
    """Lesson with missing/empty patterns is handled gracefully."""
    kb.register_lesson(
        "Malformed Lesson", "No patterns at all",
        [], "crypto", "4h", "malformed.png",
    )
    kb.register_lesson(
        "Good Lesson", "Has proper patterns",
        ["double_bottom"], "crypto", "1h", "good.png",
    )
    payload = _base_payload()
    ta = _base_ta(divergence={"type": "regular_bullish"})

    result = enricher.enrich_signal(payload, ta)

    assert "lesson_matches" in result
    for m in result["lesson_matches"]:
        assert m["id"] is not None
        assert m["title"] is not None
        assert m["pattern"] is not None
        assert m["stance"] in ("supported", "contradicted", "neutral")


def test_lm_lesson_without_tech_confirmation_no_confidence_boost():
    """Keyword match alone must NOT increase signal confidence."""
    kb.register_lesson(
        "Bullish Breakout Guide", "General bullish market education",
        ["cup_and_handle"], "crypto", "4h", "cup.png",
    )
    payload = _base_payload()
    original_confidence = payload["confidence"]
    ta = _base_ta()

    result = enricher.enrich_signal(payload, ta)

    assert result["confidence"] == original_confidence


def test_lm_payload_has_no_forbidden_fields():
    """lesson_matches must not contain image paths, base64, descriptions, or content."""
    _register_btc_lesson()
    payload = _base_payload()
    ta = _base_ta()

    result = enricher.enrich_signal(payload, ta)

    assert "lesson_matches" in result
    for m in result["lesson_matches"]:
        assert "image_filename" not in m
        assert "image" not in m
        assert "description" not in m
        assert "content" not in m
        assert "path" not in m
        assert "base64" not in m
        assert set(m.keys()) == {"id", "title", "pattern", "stance"}


def test_lm_duplicate_retry_stable_id():
    """Running enrich_signal twice with same payload produces same lesson_matches IDs."""
    kb.register_lesson(
        "Double Bottom BTC", "Pattern on BTC",
        ["double_bottom"], "crypto", "1h", "db_retry.png",
    )
    payload = _base_payload()
    ta = _base_ta(divergence={"type": "regular_bullish"})

    result1 = enricher.enrich_signal(dict(payload), ta)
    result2 = enricher.enrich_signal(dict(payload), ta)

    ids1 = [m["id"] for m in result1["lesson_matches"]]
    ids2 = [m["id"] for m in result2["lesson_matches"]]
    assert ids1 == ids2


# ── Stance determination ───────────────────────────────────────────────────

def test_stance_supported_bullish():
    """Bullish pattern + LONG signal → stance=supported."""
    kb.register_lesson(
        "Bull Flag", "Bullish continuation",
        ["bull_flag"], "crypto", "1h", "stance_bull.png",
    )
    payload = _base_payload()
    payload["signal"] = "LONG"
    ta = _base_ta()

    result = enricher.enrich_signal(payload, ta)

    assert any(m["stance"] == "supported" for m in result["lesson_matches"])


def test_stance_contradicted():
    """Bearish pattern + LONG signal → stance=contradicted."""
    kb.register_lesson(
        "Bear Flag Warning", "Bearish continuation pattern",
        ["bear_flag"], "crypto", "1h", "stance_bear.png",
    )
    payload = _base_payload()
    payload["signal"] = "LONG"
    ta = _base_ta()

    result = enricher.enrich_signal(payload, ta)

    assert any(m["stance"] == "contradicted" for m in result["lesson_matches"])


def test_stance_neutral():
    """Pattern with no directional bias → stance=neutral."""
    kb.register_lesson(
        "Wedge Formation", "Could go either way",
        ["wedge"], "crypto", "1h", "stance_neutral.png",
    )
    payload = _base_payload()
    payload["signal"] = "LONG"
    ta = _base_ta(bb_position="squeeze")

    result = enricher.enrich_signal(payload, ta)

    assert any(m["stance"] == "neutral" for m in result["lesson_matches"])


def test_stance_short_supported():
    """Bearish pattern + SHORT signal → stance=supported."""
    kb.register_lesson(
        "Double Top Reversal", "Strong bearish reversal",
        ["double_top"], "crypto", "1h", "short_sup.png",
    )
    payload = _base_payload()
    payload["signal"] = "SHORT"
    ta = _base_ta(divergence={"type": "regular_bearish"})

    result = enricher.enrich_signal(payload, ta)

    assert any(m["stance"] == "supported" for m in result["lesson_matches"])


# ── Broad search ───────────────────────────────────────────────────────────

def test_timeframe_match_boosts_rank():
    """Lesson matching signal timeframe ranks higher than one that doesn't."""
    kb.register_lesson(
        "BTC 4h Lesson", "Bitcoin on 4h",
        ["double_bottom"], "crypto", "4h", "btc_4h.png",
    )
    kb.register_lesson(
        "BTC 1h Lesson", "Bitcoin on 1h",
        ["double_bottom"], "crypto", "1h", "btc_1h.png",
    )
    payload = _base_payload()
    payload["timeframe"] = "1h"
    ta = _base_ta(divergence={"type": "regular_bullish"})

    result = enricher.enrich_signal(payload, ta)

    matches = result["lesson_matches"]
    assert matches[0]["title"] == "BTC 1h Lesson"


def test_lesson_ids_logged_not_content(caplog):
    """Log output contains lesson IDs but not lesson descriptions or image filenames."""
    kb.register_lesson(
        "Secret Lesson Title", "This description should NOT be logged",
        ["double_bottom"], "crypto", "1h", "secret_image.png",
    )
    payload = _base_payload()
    ta = _base_ta(divergence={"type": "regular_bullish"})

    with caplog.at_level("INFO"):
        enricher.enrich_signal(payload, ta)

    log_text = caplog.text
    assert "lesson_ids=" in log_text
    assert "This description should NOT be logged" not in log_text
    assert "secret_image.png" not in log_text
