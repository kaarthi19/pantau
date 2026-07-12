"""OpenAlex collector: papers by title/abstract search query and by journal ISSN.

Abstracts come back as an ``abstract_inverted_index`` (token -> [positions]); we
rebuild plain text from it. VERIFY the ISSN filter key against the live API at
build time — OpenAlex uses ``primary_location.source.issn``.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from ..net import strip_html

API = "https://api.openalex.org/works"


def _from_date(window_days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=window_days)).strftime("%Y-%m-%d")


def _reconstruct_abstract(inv_index: dict | None) -> str:
    if not inv_index:
        return ""
    positions: list[tuple[int, str]] = []
    for word, idxs in inv_index.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort()
    return strip_html(" ".join(w for _, w in positions))


def _work_to_item(work: dict, source_name: str) -> dict:
    doi = work.get("doi") or ""
    if doi.startswith("https://doi.org/"):
        doi = doi[len("https://doi.org/"):]
    loc = (work.get("primary_location") or {})
    landing = loc.get("landing_page_url") or work.get("id") or ""
    return {
        "track": "research",
        "source": source_name,
        "source_type": "paper",
        "title": work.get("title") or "(untitled)",
        "url": landing,
        "doi": doi or None,
        "published_at": work.get("publication_date") or "",
        "summary": _reconstruct_abstract(work.get("abstract_inverted_index")),
        "org": None, "location": None, "deadline": None, "tier": None,
    }


def _query(session, filter_clause: str, mailto: str) -> list[dict]:
    # OpenAlex expects every filter (incl. the date window) in ONE comma-joined
    # ``filter`` param — passing from_publication_date as its own query key 400s.
    params = {"per-page": 25, "mailto": mailto, "filter": filter_clause}
    resp = session.get(API, params=params)
    resp.raise_for_status()
    return resp.json().get("results", [])


def collect(session, queries: list[str], issns: list[str], authors: list[str],
            window_days: int, mailto: str) -> list[dict]:
    window = f"from_publication_date:{_from_date(window_days)}"
    items: list[dict] = []

    for q in queries or []:
        clause = f"{window},title_and_abstract.search:{q}"
        for w in _query(session, clause, mailto):
            items.append(_work_to_item(w, f"openalex:{q}"))

    for issn in issns or []:
        clause = f"{window},primary_location.source.issn:{issn}"
        for w in _query(session, clause, mailto):
            items.append(_work_to_item(w, f"openalex:issn:{issn}"))

    for author_id in authors or []:
        clause = f"{window},author.id:{author_id}"
        for w in _query(session, clause, mailto):
            items.append(_work_to_item(w, f"openalex:author:{author_id}"))

    return items
