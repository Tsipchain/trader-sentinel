"""
Platinum Signal Enricher — adds lesson references to signal payloads.

When a signal matches lessons in the knowledge base (by symbol, detected
pattern, timeframe, market regime, risk, or direction keywords), this module
attaches:
  - ``platinum_context`` — legacy block with up to 5 lesson refs (backward compat)
  - ``lesson_matches``  — SigBalBot v1 format: max 3 ranked {id, title, pattern, stance}

Safety rules:
  - No image bytes/base64, no filesystem paths, no course content in lesson_matches.
  - Only metadata: id, title, pattern.
  - Keyword match alone does NOT boost confidence.
  - Lesson IDs are logged, not lesson content.
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

_BULLISH_PATTERNS: set[str] = {
    "double_bottom", "bull_flag", "ascending_triangle",
    "cup_and_handle",
}

_BEARISH_PATTERNS: set[str] = {
    "double_top", "bear_flag", "descending_triangle",
    "head_and_shoulders",
}


def _determine_stance(
    lesson_patterns: list[str],
    signal_direction: str,
) -> str:
    """Determine whether a lesson supports, contradicts, or is neutral to the signal."""
    direction_upper = (signal_direction or "").upper()
    is_bullish_signal = direction_upper in ("LONG", "BUY", "ACCUMULATION")
    is_bearish_signal = direction_upper in ("SHORT", "SELL", "DISTRIBUTION")

    has_bullish = any(p in _BULLISH_PATTERNS for p in lesson_patterns)
    has_bearish = any(p in _BEARISH_PATTERNS for p in lesson_patterns)

    if is_bullish_signal:
        if has_bullish and not has_bearish:
            return "supported"
        if has_bearish and not has_bullish:
            return "contradicted"
    elif is_bearish_signal:
        if has_bearish and not has_bullish:
            return "supported"
        if has_bullish and not has_bearish:
            return "contradicted"

    return "neutral"


def _score_lesson(
    lesson: dict[str, Any],
    symbol: str,
    detected_patterns: list[str],
    timeframe: str,
    market_regime: str,
    risk: str,
    direction: str,
) -> float:
    """Score a lesson for ranking. Higher = more relevant.

    Scoring weights:
      pattern match:  10 pts each (strongest signal)
      symbol match:    5 pts
      timeframe match: 3 pts
      direction-aligned stance: 2 pts
      keyword hits (regime/risk): 1 pt each (never boosts confidence)
    """
    score = 0.0
    lesson_patterns = [p.lower() for p in lesson.get("patterns", [])]

    for dp in detected_patterns:
        if dp.lower() in lesson_patterns:
            score += 10.0

    base_asset = symbol.upper().split("/")[0] if "/" in symbol else symbol.upper()
    lesson_text = f"{lesson.get('title', '')} {lesson.get('description', '')}".upper()
    if base_asset and base_asset in lesson_text:
        score += 5.0

    lesson_tf = (lesson.get("timeframe") or "").strip().lower()
    if timeframe and lesson_tf == timeframe.strip().lower():
        score += 3.0

    stance = _determine_stance(lesson_patterns, direction)
    if stance == "supported":
        score += 2.0
    elif stance == "contradicted":
        score -= 1.0

    regime_lower = (market_regime or "").lower()
    risk_lower = (risk or "").lower()
    desc_lower = lesson_text.lower()
    if regime_lower and regime_lower in desc_lower:
        score += 1.0
    if risk_lower and risk_lower in desc_lower:
        score += 1.0

    return score


def _build_search_queries(
    symbol: str,
    detected_patterns: list[str],
    timeframe: str,
    market_regime: str,
    risk: str,
    direction: str,
) -> list[str]:
    """Build keyword search queries from signal context for broader lesson matching."""
    queries: list[str] = []
    base = symbol.upper().split("/")[0] if "/" in symbol else symbol.upper()

    if timeframe:
        queries.append(f"{base} {timeframe}")
    if market_regime and market_regime != "unknown":
        queries.append(f"{base} {market_regime}")
    if risk:
        queries.append(f"{risk} risk")
    if direction:
        dir_word = "bullish" if direction.upper() in ("LONG", "BUY", "ACCUMULATION") else "bearish"
        queries.append(dir_word)

    return queries


def enrich_signal(payload: dict[str, Any], ta: dict[str, Any]) -> dict[str, Any]:
    """Add ``platinum_context`` and ``lesson_matches`` to a signal payload.

    Looks up lessons by:
      1. Symbol (e.g. BTC/USDT -> crypto lessons)
      2. Detected divergence type -> mapped pattern
      3. Detected confluence patterns
      4. Timeframe, market regime, risk, direction keywords (broad search)

    Returns the same payload dict with enrichment added (or without
    it if no lessons matched).
    """
    symbol = payload.get("symbol", "")
    if not symbol:
        return payload

    matched_lessons: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def _add_lessons(lessons: list[dict[str, Any]]) -> None:
        for lesson in lessons:
            lid = lesson.get("id", "")
            if lid and lid not in seen_ids:
                matched_lessons.append(lesson)
                seen_ids.add(lid)

    symbol_lessons = lesson_kb.get_lessons_for_symbol(symbol)
    _add_lessons(symbol_lessons)

    detected_patterns = _extract_patterns(ta, payload)
    for pattern in detected_patterns:
        pattern_lessons = lesson_kb.get_lessons_for_pattern(pattern)
        _add_lessons(pattern_lessons)

    timeframe = payload.get("timeframe", "")
    market_regime = payload.get("market_regime", ta.get("macd_trend", ""))
    risk = payload.get("risk", "")
    direction = payload.get("signal", "")

    queries = _build_search_queries(
        symbol, detected_patterns, timeframe, market_regime, risk, direction,
    )
    for q in queries:
        _add_lessons(lesson_kb.search_lessons(q))

    if not matched_lessons:
        return payload

    # --- Legacy platinum_context (backward compat, max 5) ---
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

    # --- lesson_matches: SigBalBot v1 format (max 3, metadata only) ---
    scored = []
    for lesson in matched_lessons:
        s = _score_lesson(
            lesson, symbol, detected_patterns, timeframe,
            market_regime, risk, direction,
        )
        scored.append((s, lesson))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:3]

    lesson_matches = []
    for _score, lesson in top:
        lesson_patterns = lesson.get("patterns", [])
        primary_pattern = lesson_patterns[0] if lesson_patterns else "general"
        stance = _determine_stance(lesson_patterns, direction)
        lesson_matches.append({
            "id": lesson.get("id"),
            "title": lesson.get("title"),
            "pattern": primary_pattern,
            "stance": stance,
        })

    payload["lesson_matches"] = lesson_matches

    matched_ids = [m["id"] for m in lesson_matches]
    log.info(
        "[platinum] enriched signal %s with %d lesson(s), lesson_ids=%s, patterns=%s",
        payload.get("id", "?"),
        len(lesson_matches),
        matched_ids,
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
