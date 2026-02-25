"""
Geopolitical Risk Module
------------------------
Fetches recent headlines from two sources:

1. NYT Article Search API (developer.nytimes.com) — primary, structured
   Queries specific topics: Iran, energy, sanctions, military escalation.
   Requires NYT_API_KEY env var.

2. Free RSS Feeds (Reuters, AP, Al-Jazeera) — fallback / supplement
   Always fetched regardless of NYT key availability.

Scoring: keyword-weighted matching across all headlines.
Score: 0.0 (calm) → 10.0 (high tension)
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import Counter
from dataclasses import dataclass
from typing import Optional
from xml.etree import ElementTree

import httpx

from app.core.config import settings


# ── NYT Article Search queries (targeted) ─────────────────────────────────────
# Each query targets a specific risk theme. We fetch the 10 most recent articles
# per query. NYT rate limit: 10 req/min, 4000 req/day — plenty.
NYT_QUERIES = [
    ("iran sanctions military",        "Iran / Hormuz tension"),
    ("oil supply disruption",          "Energy supply risk"),
    ("middle east escalation war",     "ME military escalation"),
    ("cyber attack infrastructure",    "Cyber / systemic risk"),
    ("global financial crisis bank",   "Banking / financial risk"),
    ("nuclear weapons treaty",         "Nuclear escalation"),
]

NYT_SEARCH_URL = "https://api.nytimes.com/svc/search/v2/articlesearch.json"

# ── RSS Feeds (public, no auth) ────────────────────────────────────────────────
RSS_FEEDS = [
    "https://feeds.reuters.com/reuters/worldNews",
    "https://feeds.reuters.com/reuters/businessNews",
    "https://feeds.apnews.com/apnews/topnews",
    "https://www.aljazeera.com/xml/rss/all.xml",
]

# ── Keyword groups with weights ────────────────────────────────────────────────
KEYWORD_GROUPS: list[tuple[float, list[str]]] = [
    (3.0, [
        "missile strike", "air strike", "military attack", "naval blockade",
        "war declaration", "invasion", "airstrike", "ballistic missile",
    ]),
    (2.5, [
        "strait of hormuz", "oil tanker", "pipeline attack", "oil facility",
        "aramco attack", "energy infrastructure", "oil supply disruption",
    ]),
    (2.0, [
        "iran", "tehran", "irgc", "iran nuclear", "iran enrichment",
        "iran sanctions", "iranian", "persian gulf",
    ]),
    (1.8, [
        "israel attack", "hezbollah", "hamas", "yemen houthi",
        "red sea attack", "gulf escalation",
    ]),
    (1.5, [
        "cyberattack", "cyber attack", "grid attack", "infrastructure hack",
        "ransomware grid", "power grid attack", "swift hack",
    ]),
    (1.0, [
        "sanctions", "embargo", "nuclear talks", "ceasefire collapse",
        "military drill", "naval exercise", "warship", "escalation",
        "oil price surge", "energy crisis",
    ]),
    (0.4, [
        "tensions", "conflict", "dispute", "warning", "threat",
        "standoff", "confrontation",
    ]),
]

_FEED_TIMEOUT = 8.0
_CACHE_TTL    = 300   # 5 min


@dataclass
class GeoResult:
    score: float
    headlines_scored: list[dict]
    total_headlines_checked: int
    top_keywords_hit: list[str]
    nyt_used: bool = False
    cached: bool = False
    error: str | None = None


_cache: dict[str, tuple[float, GeoResult]] = {}


# ── NYT fetch ──────────────────────────────────────────────────────────────────
async def _fetch_nyt_query(
    client: httpx.AsyncClient,
    query: str,
    label: str,
    api_key: str,
) -> list[str]:
    """Fetch article snippets from NYT Article Search for one query."""
    try:
        params = {
            "q": query,
            "sort": "newest",
            "fl": "headline,abstract,snippet",
            "api-key": api_key,
        }
        r = await client.get(
            NYT_SEARCH_URL,
            params=params,
            timeout=10.0,
            follow_redirects=True,
        )
        r.raise_for_status()
        data = r.json()
        docs = data.get("response", {}).get("docs", [])
        texts: list[str] = []
        for doc in docs:
            headline = doc.get("headline", {}).get("main", "")
            abstract = doc.get("abstract", "") or ""
            snippet  = doc.get("snippet", "")  or ""
            combined = f"{headline} {abstract} {snippet}"
            if combined.strip():
                texts.append(combined)
        return texts
    except Exception:
        return []


async def _fetch_nyt_all(api_key: str) -> list[str]:
    """Fetch all NYT queries concurrently."""
    async with httpx.AsyncClient() as client:
        tasks = [
            _fetch_nyt_query(client, q, label, api_key)
            for q, label in NYT_QUERIES
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    headlines: list[str] = []
    for r in results:
        if isinstance(r, list):
            headlines.extend(r)
    return headlines


# ── RSS fetch ──────────────────────────────────────────────────────────────────
async def _fetch_rss_feed(client: httpx.AsyncClient, url: str) -> list[str]:
    try:
        r = await client.get(
            url, timeout=_FEED_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "ThronosTrader/1.0 (+sentinel)"},
        )
        r.raise_for_status()
        root = ElementTree.fromstring(r.text)
        titles: list[str] = []
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


async def _fetch_rss_all() -> list[str]:
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *[_fetch_rss_feed(client, url) for url in RSS_FEEDS],
            return_exceptions=True,
        )
    headlines: list[str] = []
    for r in results:
        if isinstance(r, list):
            headlines.extend(r)
    return headlines


# ── Scoring ────────────────────────────────────────────────────────────────────
def _score_headline(text: str) -> tuple[float, list[str]]:
    lower = text.lower()
    total = 0.0
    hits: list[str] = []
    for weight, keywords in KEYWORD_GROUPS:
        for kw in keywords:
            if kw in lower:
                total += weight
                hits.append(kw)
    return total, hits


# ── Main entry point ───────────────────────────────────────────────────────────
async def calculate(use_cache: bool = True) -> GeoResult:
    cache_key = "geo"
    if use_cache and cache_key in _cache:
        ts, cached_result = _cache[cache_key]
        if time.time() - ts < _CACHE_TTL:
            cached_result.cached = True
            return cached_result

    api_key = settings.nyt_api_key
    nyt_used = bool(api_key)

    async def _empty() -> list[str]:
        return []

    # Fetch NYT and RSS concurrently
    nyt_task = _fetch_nyt_all(api_key) if api_key else _empty()
    rss_task = _fetch_rss_all()

    nyt_headlines, rss_headlines = await asyncio.gather(nyt_task, rss_task)

    # NYT articles are higher quality: weight their score contribution x1.5
    all_scored: list[tuple[float, str, list[str], float]] = []  # (raw_score, text, hits, weight)

    for h in nyt_headlines:
        s, hits = _score_headline(h)
        if s > 0:
            all_scored.append((s * 1.5, h, hits, 1.5))  # NYT boost

    for h in rss_headlines:
        s, hits = _score_headline(h)
        if s > 0:
            all_scored.append((s, h, hits, 1.0))

    total_checked = len(nyt_headlines) + len(rss_headlines)

    if not all_scored:
        result = GeoResult(
            score=0.0,
            headlines_scored=[],
            total_headlines_checked=total_checked,
            top_keywords_hit=[],
            nyt_used=nyt_used,
            error="No matching headlines found" if total_checked > 0 else "No feeds available",
        )
        _cache[cache_key] = (time.time(), result)
        return result

    all_scored.sort(key=lambda x: -x[0])
    top_n = all_scored[:25]

    raw = sum(s for s, _, _, _ in top_n)
    normalised = min(10.0, raw / 3.0)
    normalised = round(normalised, 2)

    all_keywords: list[str] = []
    for _, _, hits, _ in top_n:
        all_keywords.extend(hits)
    top_kw = [kw for kw, _ in Counter(all_keywords).most_common(10)]

    headlines_out = [
        {
            "headline": h[:200],
            "score": round(s, 2),
            "keywords": hits[:6],
            "source": "nyt" if w == 1.5 else "rss",
        }
        for s, h, hits, w in top_n[:10]
    ]

    result = GeoResult(
        score=normalised,
        headlines_scored=headlines_out,
        total_headlines_checked=total_checked,
        top_keywords_hit=top_kw,
        nyt_used=nyt_used,
        error=None,
    )
    _cache[cache_key] = (time.time(), result)
    return result
