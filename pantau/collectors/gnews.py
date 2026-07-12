"""Google News RSS collector: one search feed per configured query.

Search feed shape: https://news.google.com/rss/search?q=<query>&hl=en-US&gl=US&ceid=US:en
Politeness note: this only reads the public RSS endpoint — it never logs in.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus

import feedparser

from ..net import strip_html

BASE = "https://news.google.com/rss/search"


def _within_window(entry, cutoff: datetime) -> bool:
    parsed = entry.get("published_parsed")
    if not parsed:
        return True
    dt = datetime(*parsed[:6], tzinfo=timezone.utc)
    return dt >= cutoff


def collect(session, queries: list[str], window_days: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    items: list[dict] = []
    for q in queries or []:
        url = f"{BASE}?q={quote_plus(q)}&hl=en-US&gl=US&ceid=US:en"
        resp = session.get(url)
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)
        for e in feed.entries:
            if not _within_window(e, cutoff):
                continue
            published = ""
            if e.get("published_parsed"):
                published = datetime(*e["published_parsed"][:6]).strftime("%Y-%m-%d")
            items.append({
                "track": "research",
                "source": f"gnews:{q}",
                "source_type": "news",
                "title": (e.get("title") or "").strip(),
                "url": e.get("link", ""),
                "doi": None,
                "published_at": published,
                "summary": strip_html(e.get("summary", "")),
                "org": None, "location": None, "deadline": None, "tier": None,
            })
    return items
