"""
Geopolitical Risk Module
------------------------
Fetches recent headlines from free RSS feeds (Reuters, Al-Jazeera, AP)
and scores them for geopolitical tension using keyword matching + weighting.

Focus areas:
- Middle East / Iran / Strait of Hormuz  → energy supply risk
- Sanctions / nuclear negotiations       → macro / oil risk
- Cyber infrastructure threats           → systemic risk
- General military/conflict escalation   → volatility risk

Score: 0.0 (calm) → 10.0 (high tension)
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Optional
from xml.etree import ElementTree

import httpx


# ── RSS Feeds (public, no auth) ────────────────────────────────────────────────
RSS_FEEDS = [
    # Reuters world news
    "https://feeds.reuters.com/reuters/worldNews",
    # Reuters energy/oil
    "https://feeds.reuters.com/reuters/businessNews",
    # AP top news
    "https://feeds.apnews.com/apnews/topnews",
    # Al Jazeera English
    "https://www.aljazeera.com/xml/rss/all.xml",
]

# ── Keyword groups with weights ────────────────────────────────────────────────
KEYWORD_GROUPS: list[tuple[float, list[str]]] = [
    # High-weight: direct conflict / military action
    (3.0, [
        "missile strike", "air strike", "military attack", "naval blockade",
        "war declaration", "invasion", "airstrike", "ballistic missile",
    ]),
    # High-weight: energy infrastructure
    (2.5, [
        "strait of hormuz", "oil tanker", "pipeline attack", "oil facility",
        "aramco attack", "energy infrastructure", "oil supply disruption",
    ]),
    # Medium-weight: Iran specific
    (2.0, [
        "iran", "tehran", "irgc", "iran nuclear", "iran enrichment",
        "iran sanctions", "iranian", "persian gulf",
    ]),
    # Medium-weight: broader Middle East escalation
    (1.8, [
        "israel attack", "hezbollah", "hamas", "yemen houthi",
        "red sea attack", "gulf escalation",
    ]),
    # Medium-weight: cyber / systemic
    (1.5, [
        "cyberattack", "cyber attack", "grid attack", "infrastructure hack",
        "ransomware grid", "power grid attack", "swift hack",
    ]),
    # Low-weight: general tension signals
    (1.0, [
        "sanctions", "embargo", "nuclear talks", "ceasefire collapse",
        "military drill", "naval exercise", "warship", "escalation",
        "oil price surge", "energy crisis",
    ]),
    # Very low: background noise / diplomatic language
    (0.4, [
        "tensions", "conflict", "dispute", "warning", "threat",
        "standoff", "confrontation",
    ]),
]

_FEED_TIMEOUT = 8.0          # seconds per feed
_CACHE_TTL    = 300          # 5-min cache to avoid hammering RSS


@dataclass
class GeoResult:
    score: float                      # 0–10
    headlines_scored: list[dict]      # top matching headlines
    total_headlines_checked: int
    top_keywords_hit: list[str]
    cached: bool = False
    error: str | None = None


# ── Simple in-memory cache ─────────────────────────────────────────────────────
_cache: dict[str, tuple[float, GeoResult]] = {}


async def _fetch_feed(client: httpx.AsyncClient, url: str) -> list[str]:
    """Fetch an RSS feed and return list of headline strings."""
    try:
        r = await client.get(url, timeout=_FEED_TIMEOUT,
                             follow_redirects=True,
                             headers={"User-Agent": "ThronosTrader/1.0 (+sentinel)"})
        r.raise_for_status()
        root = ElementTree.fromstring(r.text)
        titles: list[str] = []
        # RSS 2.0 and Atom
        for item in root.iter("item"):
            t = item.findtext("title")
            d = item.findtext("description") or ""
            if t:
                titles.append(f"{t} {d}")
        for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
            t = entry.findtext("{http://www.w3.org/2005/Atom}title")
            s = entry.findtext("{http://www.w3.org/2005/Atom}summary") or ""
            if t:
                titles.append(f"{t} {s}")
        return titles
    except Exception:
        return []


def _score_headline(text: str) -> tuple[float, list[str]]:
    """Return (score, matched_keywords) for a single headline text."""
    lower = text.lower()
    total = 0.0
    hits: list[str] = []
    for weight, keywords in KEYWORD_GROUPS:
        for kw in keywords:
            if kw in lower:
                total += weight
                hits.append(kw)
    return total, hits


async def calculate(use_cache: bool = True) -> GeoResult:
    cache_key = "geo"
    if use_cache and cache_key in _cache:
        ts, cached_result = _cache[cache_key]
        if time.time() - ts < _CACHE_TTL:
            cached_result.cached = True
            return cached_result

    all_headlines: list[str] = []
    errors: list[str] = []

    async with httpx.AsyncClient() as client:
        tasks = [_fetch_feed(client, url) for url in RSS_FEEDS]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    for r in results:
        if isinstance(r, list):
            all_headlines.extend(r)
        else:
            errors.append(str(r))

    if not all_headlines:
        result = GeoResult(
            score=0.0,
            headlines_scored=[],
            total_headlines_checked=0,
            top_keywords_hit=[],
            error="No feeds available: " + "; ".join(errors) if errors else "No headlines fetched",
        )
        _cache[cache_key] = (time.time(), result)
        return result

    scored: list[tuple[float, str, list[str]]] = []
    for h in all_headlines:
        s, hits = _score_headline(h)
        if s > 0:
            scored.append((s, h, hits))

    scored.sort(key=lambda x: -x[0])

    # Aggregate: sum top-25 scores, normalise to 0–10
    top_n = scored[:25]
    raw = sum(s for s, _, _ in top_n)
    # Empirical normalisation: ~15 points = moderate tension (5/10)
    normalised = min(10.0, raw / 3.0)
    normalised = round(normalised, 2)

    all_keywords: list[str] = []
    for _, _, hits in top_n:
        all_keywords.extend(hits)
    from collections import Counter
    top_kw = [kw for kw, _ in Counter(all_keywords).most_common(10)]

    headlines_out = [
        {
            "headline": h[:200],
            "score": round(s, 2),
            "keywords": hits[:6],
        }
        for s, h, hits in top_n[:10]
    ]

    result = GeoResult(
        score=normalised,
        headlines_scored=headlines_out,
        total_headlines_checked=len(all_headlines),
        top_keywords_hit=top_kw,
        error=None,
    )
    _cache[cache_key] = (time.time(), result)
    return result
