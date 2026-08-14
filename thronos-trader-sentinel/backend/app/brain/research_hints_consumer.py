"""
Research Hints Consumer — processes optional ``research_hints`` from
the SigBalBot context feed for calendar/research enrichment.

Capability gate:
  Only enabled when GET /api/v1/signals/trader-sentinel/status returns
  capabilities.research_hints == true.

Keyword contract (v1.0):
  research_hints.keywords: list[str]  — max 60, each max 60 chars, deduped case-insensitively
  research_hints.asset_classes: list[str]  — subset of {crypto, equities, metals}
  research_hints.purpose: str  — must be "calendar-and-research-attention"
  research_hints.instruction: str  — advisory only, not executed

Event model:
  Verified discoveries produce a normalized ResearchEvent stored durably.
  event_id is deterministic (hash of category + event_type + title + event_at)
  so restarts/re-polls never duplicate.

Safety:
  - Keyword match alone NEVER creates a trade signal.
  - Events may adjust risk, confidence, regime, suppression — not direction.
  - No API keys, tokens, seeds, or auth headers in events, logs, or diagnostics.
"""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

_MAX_KEYWORDS = 60
_MAX_KEYWORD_LEN = 60
_SUPPORTED_HINT_VERSIONS = {"1.0"}
_VALID_ASSET_CLASSES: set[str] = {"crypto", "equities", "metals"}
_VALID_CATEGORIES: set[str] = {"macro", "crypto", "equities", "metals"}
_VALID_IMPORTANCE: set[str] = {"low", "medium", "high"}
_VALID_VERIFICATION: set[str] = {"verified", "unverified", "conflicting"}
_MAX_EVENT_AGE_S = 72 * 3600

_MACRO_KEYWORDS: set[str] = {
    "cpi", "ppi", "payrolls", "unemployment", "fomc", "fed",
    "interest rate", "treasury yield", "risk-on", "risk-off",
    "gdp", "inflation", "nonfarm", "central bank",
}

_CRYPTO_KEYWORDS: set[str] = {
    "funding rate", "open interest", "liquidation", "token unlock",
    "exchange listing", "delisting", "etf", "stablecoin",
    "on-chain", "whale", "halving",
}

_EQUITIES_KEYWORDS: set[str] = {
    "earnings", "guidance", "sec filing", "dividend", "stock split",
    "analyst", "pre-market", "after-hours", "index rebalancing",
    "ipo", "buyback",
}

_METALS_KEYWORDS: set[str] = {
    "dxy", "real yield", "central bank purchase", "comex",
    "london fix", "geopolitical", "gold", "silver", "platinum",
    "palladium", "precious metal",
}

research_hints_enabled: bool = False
_active_keywords: list[str] = []
_active_asset_classes: list[str] = []
_stats: dict[str, Any] = {
    "accepted": 0,
    "rejected": 0,
    "duplicates": 0,
    "last_verified_event": None,
    "last_error_code": None,
}


def check_research_hints_capability(status_body: dict[str, Any]) -> bool:
    """Check if the status endpoint advertises research_hints capability."""
    global research_hints_enabled
    capabilities = status_body.get("capabilities", {})
    enabled = capabilities.get("research_hints") is True
    research_hints_enabled = enabled
    if enabled:
        log.info("research-hints: capability confirmed")
    return enabled


def parse_research_hints(context: dict[str, Any]) -> dict[str, Any] | None:
    """Extract and validate the research_hints block from a context response.

    Returns the validated hints dict, or None if absent/invalid.
    """
    global _active_keywords, _active_asset_classes

    hints = context.get("research_hints")
    if hints is None:
        return None

    if not isinstance(hints, dict):
        log.warning("research-hints: malformed research_hints (not a dict), ignoring")
        _stats["last_error_code"] = "MALFORMED_HINTS"
        return None

    version = hints.get("version", "")
    if version and version not in _SUPPORTED_HINT_VERSIONS:
        log.warning("research-hints: unsupported version=%s", version)
        _stats["last_error_code"] = "UNSUPPORTED_VERSION"
        return None

    purpose = hints.get("purpose", "")
    if purpose and purpose != "calendar-and-research-attention":
        log.warning("research-hints: unexpected purpose=%s", purpose)

    raw_keywords = hints.get("keywords", [])
    if not isinstance(raw_keywords, list):
        raw_keywords = []

    seen: set[str] = set()
    keywords: list[str] = []
    for kw in raw_keywords:
        if not isinstance(kw, str) or not kw.strip():
            continue
        normalized = kw.strip()[:_MAX_KEYWORD_LEN]
        key = normalized.lower()
        if key not in seen:
            seen.add(key)
            keywords.append(normalized)
        if len(keywords) >= _MAX_KEYWORDS:
            break

    asset_classes = []
    for ac in hints.get("asset_classes", []):
        if isinstance(ac, str) and ac.strip().lower() in _VALID_ASSET_CLASSES:
            asset_classes.append(ac.strip().lower())

    _active_keywords = keywords
    _active_asset_classes = asset_classes or list(_VALID_ASSET_CLASSES)

    return {
        "version": version or "1.0",
        "keywords": keywords,
        "asset_classes": _active_asset_classes,
        "instruction": str(hints.get("instruction", ""))[:500],
    }


def classify_keyword(keyword: str) -> str:
    """Classify a keyword into macro/crypto/equities/metals."""
    kw = keyword.lower()
    if kw in _CRYPTO_KEYWORDS or any(ck in kw for ck in _CRYPTO_KEYWORDS):
        return "crypto"
    if kw in _EQUITIES_KEYWORDS or any(ek in kw for ek in _EQUITIES_KEYWORDS):
        return "equities"
    if kw in _METALS_KEYWORDS or any(mk in kw for mk in _METALS_KEYWORDS):
        return "metals"
    if kw in _MACRO_KEYWORDS or any(mk in kw for mk in _MACRO_KEYWORDS):
        return "macro"
    return "macro"


def generate_event_id(
    category: str, event_type: str, title: str, event_at: str,
) -> str:
    """Generate a deterministic event ID from event attributes."""
    raw = f"{category}:{event_type}:{title}:{event_at}".lower()
    return f"evt-{hashlib.sha256(raw.encode()).hexdigest()[:16]}"


def build_research_event(
    category: str,
    event_type: str,
    title: str,
    source: str,
    source_url: str,
    published_at: str,
    event_at: str,
    affected_symbols: list[str],
    importance: str = "medium",
    summary: str = "",
) -> dict[str, Any] | None:
    """Build a normalized research event, applying validation rules.

    Returns None if the event fails validation (stale, undated, etc.).
    """
    if not title or not source or not event_at:
        _stats["rejected"] += 1
        _stats["last_error_code"] = "MISSING_REQUIRED_FIELDS"
        return None

    if category not in _VALID_CATEGORIES:
        category = "macro"

    if importance not in _VALID_IMPORTANCE:
        importance = "medium"

    try:
        evt_dt = datetime.fromisoformat(event_at.replace("Z", "+00:00"))
        age_s = (datetime.now(timezone.utc) - evt_dt).total_seconds()
        if age_s > _MAX_EVENT_AGE_S:
            _stats["rejected"] += 1
            _stats["last_error_code"] = "STALE_EVENT"
            log.debug("research-hints: rejecting stale event '%s' (age=%ds)", title[:40], age_s)
            return None
    except (ValueError, TypeError):
        _stats["rejected"] += 1
        _stats["last_error_code"] = "INVALID_DATE"
        return None

    event_id = generate_event_id(category, event_type, title, event_at)

    return {
        "event_id": event_id,
        "category": category,
        "event_type": event_type,
        "title": title,
        "source": source,
        "source_url": source_url,
        "published_at": published_at,
        "event_at": event_at,
        "affected_symbols": affected_symbols or [],
        "importance": importance,
        "confidence": 0.0,
        "verification_status": "unverified",
        "summary": summary[:500] if summary else "",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def verify_event(event: dict[str, Any]) -> dict[str, Any]:
    """Apply verification rules to a research event.

    A verified event has source, source_url, published_at, and event_at.
    """
    source = event.get("source", "")
    source_url = event.get("source_url", "")
    published_at = event.get("published_at", "")

    if source and source_url and published_at:
        event["verification_status"] = "verified"
        event["confidence"] = 0.7
    else:
        event["verification_status"] = "unverified"
        event["confidence"] = 0.0

    return event


def process_research_hints(
    context: dict[str, Any],
    processed_event_ids: set[str],
) -> tuple[list[dict[str, Any]], set[str]]:
    """Process research_hints from a context response.

    Returns (new_events, updated_processed_ids).
    Does NOT create trade signals — events enrich risk/calendar context only.
    """
    if not research_hints_enabled:
        return [], processed_event_ids

    parsed = parse_research_hints(context)
    if parsed is None:
        return [], processed_event_ids

    new_events: list[dict[str, Any]] = []
    updated_ids = set(processed_event_ids)

    log.info(
        "research-hints: processing %d keywords for %s",
        len(parsed["keywords"]),
        parsed["asset_classes"],
    )

    return new_events, updated_ids


def enrich_risk_context(
    risk_data: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Enrich risk context with verified research events.

    May adjust: risk score, confidence, market_regime, warnings.
    Must NOT create LONG/SHORT decisions.
    """
    if not events:
        return risk_data

    verified = [e for e in events if e.get("verification_status") == "verified"]
    if not verified:
        return risk_data

    high_importance = [e for e in verified if e.get("importance") == "high"]

    enriched = dict(risk_data)
    enriched["research_events_count"] = len(verified)

    if high_importance:
        enriched["event_risk_warning"] = True
        enriched["high_importance_events"] = len(high_importance)
        current_score = enriched.get("composite_score", 5.0)
        enriched["composite_score"] = min(10.0, current_score + 0.5 * len(high_importance))

    return enriched


def get_diagnostics() -> dict[str, Any]:
    """Return admin diagnostics for research hints — no secrets."""
    return {
        "research_hints_enabled": research_hints_enabled,
        "active_keywords_count": len(_active_keywords),
        "active_asset_classes": list(_active_asset_classes),
        "accepted": _stats["accepted"],
        "rejected": _stats["rejected"],
        "duplicates": _stats["duplicates"],
        "last_verified_event": _stats["last_verified_event"],
        "last_error_code": _stats["last_error_code"],
    }


def accept_event(
    event: dict[str, Any],
    processed_event_ids: set[str],
) -> bool:
    """Accept a verified event into the store, deduplicating by event_id.

    Returns True if accepted, False if duplicate or unverified.
    """
    event_id = event.get("event_id", "")
    if not event_id:
        _stats["rejected"] += 1
        return False

    if event_id in processed_event_ids:
        _stats["duplicates"] += 1
        return False

    if event.get("verification_status") != "verified":
        _stats["rejected"] += 1
        _stats["last_error_code"] = "UNVERIFIED"
        return False

    from app.brain import store as brain_store
    brain_store.append_research_event(event)
    processed_event_ids.add(event_id)
    _stats["accepted"] += 1
    _stats["last_verified_event"] = event.get("title", "")[:60]

    log.info(
        "research-hints: accepted event_id=%s category=%s title=%s",
        event_id, event.get("category"), event.get("title", "")[:40],
    )
    return True
