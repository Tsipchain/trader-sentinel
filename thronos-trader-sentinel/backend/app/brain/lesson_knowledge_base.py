"""
Trading Lesson Knowledge Base — metadata index for course screenshots/photos.

Stores lesson metadata (title, description, patterns, asset class, timeframe)
in a JSON index file.  Images themselves live alongside the index in
LESSON_DATA_DIR; this module does NOT process them — it only indexes metadata
so the Sentinel signal evaluation pipeline can look up relevant lessons.

Index file layout (lessons_index.json):
  {
    "lessons": [
      {
        "id": "<uuid>",
        "title": "...",
        "description": "...",
        "patterns": ["head_and_shoulders", "bull_flag"],
        "asset_class": "crypto",
        "timeframe": "4h",
        "image_filename": "lesson_01.png",
        "created_at": "...",
        "updated_at": "..."
      },
      ...
    ],
    "updated_at": "..."
  }
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

LESSON_DATA_DIR = Path(os.getenv("LESSON_DATA_DIR", "data/trading_lessons"))

VALID_PATTERN_TYPES: set[str] = {
    "head_and_shoulders",
    "double_bottom",
    "double_top",
    "bull_flag",
    "bear_flag",
    "ascending_triangle",
    "descending_triangle",
    "cup_and_handle",
    "wedge",
    "channel",
}

# Common asset-class / symbol keywords used when matching lessons to symbols.
_ASSET_KEYWORDS: dict[str, list[str]] = {
    "crypto": ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK",
               "DOT", "MATIC", "USDT", "USDC", "BNB", "THR"],
    "forex": ["EUR", "USD", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"],
    "equities": ["SPX", "NDX", "DJI", "AAPL", "TSLA", "AMZN", "NVDA"],
    "commodities": ["GOLD", "SILVER", "OIL", "XAU", "XAG", "WTI"],
}


# ── Index helpers ────────────────────────────────────────────────────────────

def _index_path() -> Path:
    return LESSON_DATA_DIR / "lessons_index.json"


def _load_index() -> dict[str, Any]:
    path = _index_path()
    if not path.exists():
        return {"lessons": [], "updated_at": None}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as exc:
        log.warning("[lesson_kb] could not load index: %s", exc)
        return {"lessons": [], "updated_at": None}


def _save_index(index: dict[str, Any]) -> None:
    LESSON_DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = _index_path()
    index["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        with open(path, "w") as f:
            json.dump(index, f, indent=2)
        log.info("[lesson_kb] saved index (%d lessons)", len(index.get("lessons", [])))
    except Exception as exc:
        log.warning("[lesson_kb] could not save index: %s", exc)


# ── Public API ───────────────────────────────────────────────────────────────

def register_lesson(
    title: str,
    description: str,
    patterns: list[str],
    asset_class: str,
    timeframe: str,
    image_filename: str,
) -> dict[str, Any]:
    """Register (or update) a lesson in the index.

    If a lesson with the same *image_filename* already exists, it is updated
    rather than duplicated.

    Returns the lesson dict that was stored.
    """
    now = datetime.now(timezone.utc).isoformat()
    index = _load_index()
    lessons: list[dict[str, Any]] = index.get("lessons", [])

    # Normalise patterns to lowercase, filter to known types (unknown kept too
    # to stay forward-compatible, but a warning is logged).
    normalised_patterns: list[str] = []
    for p in patterns:
        p_lower = p.strip().lower()
        if p_lower and p_lower not in VALID_PATTERN_TYPES:
            log.warning("[lesson_kb] unknown pattern type: %s", p_lower)
        if p_lower:
            normalised_patterns.append(p_lower)

    # Check for existing lesson with same image_filename (update path).
    existing_idx: int | None = None
    for i, lesson in enumerate(lessons):
        if lesson.get("image_filename") == image_filename:
            existing_idx = i
            break

    lesson_entry: dict[str, Any] = {
        "id": str(uuid.uuid4()) if existing_idx is None else lessons[existing_idx]["id"],
        "title": title,
        "description": description,
        "patterns": normalised_patterns,
        "asset_class": asset_class.strip().lower(),
        "timeframe": timeframe.strip(),
        "image_filename": image_filename,
        "created_at": lessons[existing_idx]["created_at"] if existing_idx is not None else now,
        "updated_at": now,
    }

    if existing_idx is not None:
        lessons[existing_idx] = lesson_entry
        log.info("[lesson_kb] updated lesson '%s' (file=%s)", title, image_filename)
    else:
        lessons.append(lesson_entry)
        log.info("[lesson_kb] registered new lesson '%s' (file=%s)", title, image_filename)

    index["lessons"] = lessons
    _save_index(index)
    return lesson_entry


def get_lessons_for_symbol(symbol: str) -> list[dict[str, Any]]:
    """Return lessons whose asset_class matches the given trading symbol.

    Matching logic:
      1. Extract base asset from ccxt-style symbol (e.g. "BTC/USDT" -> "BTC").
      2. Look up which asset_class the base belongs to.
      3. Return all lessons with that asset_class.
    """
    index = _load_index()
    lessons = index.get("lessons", [])
    if not lessons:
        return []

    symbol_upper = symbol.upper().split(":")[0]  # strip futures suffix
    base = symbol_upper.split("/")[0] if "/" in symbol_upper else symbol_upper

    # Determine matching asset classes for this symbol.
    matching_classes: set[str] = set()
    for asset_class, keywords in _ASSET_KEYWORDS.items():
        if base in keywords:
            matching_classes.add(asset_class)

    if not matching_classes:
        # Fallback: if the symbol isn't in our keyword map, try matching
        # base text against lesson title/description.
        results = []
        for lesson in lessons:
            text = f"{lesson.get('title', '')} {lesson.get('description', '')}".upper()
            if base in text:
                results.append(lesson)
        return results

    return [l for l in lessons if l.get("asset_class") in matching_classes]


def get_lessons_for_pattern(pattern_type: str) -> list[dict[str, Any]]:
    """Return all lessons that reference the given pattern type."""
    index = _load_index()
    pattern_lower = pattern_type.strip().lower()
    return [
        l for l in index.get("lessons", [])
        if pattern_lower in l.get("patterns", [])
    ]


def get_lesson_count() -> int:
    """Return total number of registered lessons."""
    index = _load_index()
    return len(index.get("lessons", []))


def search_lessons(query: str) -> list[dict[str, Any]]:
    """Simple keyword search across title, description, and patterns.

    The query is split into tokens; a lesson matches if *all* tokens appear
    in its combined searchable text (case-insensitive).
    """
    index = _load_index()
    lessons = index.get("lessons", [])
    if not query or not query.strip():
        return lessons

    tokens = query.lower().split()
    results: list[dict[str, Any]] = []
    for lesson in lessons:
        searchable = " ".join([
            lesson.get("title", ""),
            lesson.get("description", ""),
            " ".join(lesson.get("patterns", [])),
            lesson.get("asset_class", ""),
            lesson.get("timeframe", ""),
        ]).lower()
        if all(tok in searchable for tok in tokens):
            results.append(lesson)
    return results


def list_all_lessons() -> list[dict[str, Any]]:
    """Return every registered lesson."""
    index = _load_index()
    return index.get("lessons", [])
