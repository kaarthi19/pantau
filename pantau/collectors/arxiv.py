"""arXiv collector: recent preprints from configured categories/terms via the Atom API."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import feedparser

API = "https://export.arxiv.org/api/query"


def _build_query(categories: list[str], terms: list[str]) -> str:
    clauses = []
    for cat in categories or []:
        clauses.append(f"cat:{cat}")
    for term in terms or []:
        clauses.append(f'all:"{term}"')
    return " OR ".join(clauses) if clauses else "cat:eess.SY"


def _within_window(published: str, cutoff: datetime) -> bool:
    if not published:
        return True
    try:
        dt = datetime.strptime(published, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return dt >= cutoff


def collect(session, categories: list[str], terms: list[str],
            window_days: int, max_results: int = 50) -> list[dict]:
    query = _build_query(categories, terms)
    params = {
        "search_query": query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    resp = session.get(API, params=params)
    resp.raise_for_status()
    feed = feedparser.parse(resp.text)
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

    items: list[dict] = []
    for e in feed.entries:
        published = e.get("published", "")
        if not _within_window(published, cutoff):
            continue
        doi = e.get("arxiv_doi")
        items.append({
            "track": "research",
            "source": "arxiv",
            "source_type": "paper",
            "title": (e.get("title") or "").replace("\n", " ").strip(),
            "url": e.get("link", ""),
            "doi": doi or None,
            "published_at": published[:10] if published else "",
            "summary": (e.get("summary") or "").replace("\n", " ").strip()[:1200],
            "org": None, "location": None, "deadline": None, "tier": None,
        })
    return items
