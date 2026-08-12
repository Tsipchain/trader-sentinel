"""
Platinum Signal Enricher — adds lesson references to signal payloads.

When a signal matches lessons in the knowledge base (by symbol or detected
pattern), this module attaches a ``platinum_context`` block so SigBalBot can
include educational context in platinum-tier Telegram messages.
"""
from __future__ import annotations

import logging
from typing import Any

from app.brain import lesson_knowledge_base as lesson_kb

log = logging.getLogger(__name__)

_DIVERGENCE_TO_PATTERN: dict[str, str] = {
    "regular_bullish": "double_bottom",
    "regular_bearish": "double_top",
    "hidden_bullish": "bull_flag",
    "hidden_bearish": "bear_flag",
}


def enrich_signal(payload: dict[str, Any], ta: dict[str, Any]) -> dict[str, Any]:
    """Add ``platinum_context`` to a signal payload if matching lessons exist.

    Looks up lessons by:
      1. Symbol (e.g. BTC/USDT → crypto lessons)
      2. Detected divergence type → mapped pattern
      3. Detected confluence patterns

    Returns the same payload dict with ``platinum_context`` added (or without
    it if no lessons matched).
    """
    symbol = payload.get("symbol", "")
    if not symbol:
        return payload

    matched_lessons: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    symbol_lessons = lesson_kb.get_lessons_for_symbol(symbol)
    for lesson in symbol_lessons:
        lid = lesson.get("id", "")
        if lid not in seen_ids:
            matched_lessons.append(lesson)
            seen_ids.add(lid)

    detected_patterns = _extract_patterns(ta, payload)
    for pattern in detected_patterns:
        pattern_lessons = lesson_kb.get_lessons_for_pattern(pattern)
        for lesson in pattern_lessons:
            lid = lesson.get("id", "")
            if lid not in seen_ids:
                matched_lessons.append(lesson)
                seen_ids.add(lid)

    if not matched_lessons:
        return payload

    refs = []
    for lesson in matched_lessons[:5]:
        refs.append({
            "lesson_id": lesson.get("id"),
            "title": lesson.get("title"),
            "image_filename": lesson.get("image_filename"),
            "patterns": lesson.get("patterns", []),
            "timeframe": lesson.get("timeframe"),
        })

    payload["platinum_context"] = {
        "lesson_references": refs,
        "detected_patterns": detected_patterns,
        "match_count": len(matched_lessons),
    }

    log.info(
        "[platinum] enriched signal %s with %d lesson(s), patterns=%s",
        payload.get("id", "?"),
        len(refs),
        detected_patterns,
    )
    return payload


def _extract_patterns(ta: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    """Extract pattern type names from TA data and signal context."""
    patterns: list[str] = []

    div_type = ta.get("divergence", {}).get("type", "")
    if not div_type:
        reason = payload.get("reason", "")
        if "regular_bullish" in reason:
            div_type = "regular_bullish"
        elif "regular_bearish" in reason:
            div_type = "regular_bearish"
        elif "hidden_bullish" in reason:
            div_type = "hidden_bullish"
        elif "hidden_bearish" in reason:
            div_type = "hidden_bearish"

    mapped = _DIVERGENCE_TO_PATTERN.get(div_type)
    if mapped:
        patterns.append(mapped)

    confluence = ta.get("confluence", {})
    if confluence.get("level") == "strong":
        direction = confluence.get("direction", "")
        if direction == "bullish":
            patterns.append("ascending_triangle")
        elif direction == "bearish":
            patterns.append("descending_triangle")

    bb = ta.get("bb_position")
    if bb == "squeeze":
        patterns.append("wedge")
    elif bb in ("overbought", "oversold"):
        patterns.append("channel")

    return patterns
