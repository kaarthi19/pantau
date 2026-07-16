"""Library-seeded discovery from your Zotero library.

"Study my library, then find the recent papers I should read": take the DOIs in
your reference library, resolve them on OpenAlex, and surface RECENT papers that
  (a) CITE something in your library  — new work building on what you read, and
  (b) are written BY the authors you read most.
Those candidates flow through the normal scorer; digest.py pulls the top-N of
them into a dedicated "From your library" section.

The library DOIs come from one of two sources (config.library.source):
  * "zotero_api" — fetched from zotero.org over the read-only Web API each run
    (no manual export). Works for a personal OR a shared lab GROUP library.
  * "bib"        — a local Zotero/BibTeX export at library/zotero.bib.

Either way only DOIs are sent to OpenAlex; the library itself is never committed.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone, timedelta

from .openalex import _work_to_item

OPENALEX = "https://api.openalex.org/works"
ZOTERO = "https://api.zotero.org"
_DOI_RE = re.compile(r'doi\s*=\s*[{"]\s*([^}"]+?)\s*[}"]', re.IGNORECASE)


def _clean_doi(raw: str) -> str:
    d = (raw or "").strip().lower()
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", d)


def parse_dois(path: str) -> list[str]:
    """Extract unique, cleaned DOIs from a local .bib file."""
    with open(path, encoding="utf-8", errors="ignore") as f:
        text = f.read()
    out = [_clean_doi(x) for x in _DOI_RE.findall(text)]
    return list(dict.fromkeys(d for d in out if d))


def zotero_dois(session, lib_cfg: dict) -> list[str]:
    """Fetch DOIs from a Zotero library over the read-only Web API (paginated).

    Needs library.zotero_library_id (+ type user|group) and, for a private
    library, a read-only key in the ZOTERO_API_KEY env var / Actions secret.
    """
    lib_id = str(lib_cfg.get("zotero_library_id", "")).strip()
    if not lib_id or lib_id == "SET_ME":
        return []
    lib_type = lib_cfg.get("zotero_library_type", "user")
    base = f"{ZOTERO}/{lib_type}s/{lib_id}/items"
    headers = {"Zotero-API-Version": "3"}
    key = os.environ.get("ZOTERO_API_KEY")
    if key:
        headers["Zotero-API-Key"] = key

    dois: list[str] = []
    start, limit = 0, 100
    for _ in range(50):  # hard cap 5000 items
        params = {"format": "json", "limit": limit, "start": start, "itemType": "-attachment"}
        resp = session.get(base, params=params, headers=headers)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        for it in batch:
            data = it.get("data") or {}
            d = _clean_doi(data.get("DOI") or _doi_from_extra(data.get("extra", "")))
            if d:
                dois.append(d)
        if len(batch) < limit:
            break
        start += limit
    return list(dict.fromkeys(dois))


def _doi_from_extra(extra: str) -> str:
    m = re.search(r'doi:\s*(\S+)', extra or "", re.IGNORECASE)
    return m.group(1) if m else ""


def fetch_dois(session, lib_cfg: dict) -> list[str]:
    if lib_cfg.get("source", "bib") == "zotero_api":
        return zotero_dois(session, lib_cfg)
    path = lib_cfg.get("bib_path", "library/zotero.bib")
    return parse_dois(path) if os.path.exists(path) else []


# --- discovery --------------------------------------------------------------

def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _short(oa_id: str) -> str:
    return (oa_id or "").rsplit("/", 1)[-1]


def _works(session, filter_clause: str, mailto: str, per_page: int = 50) -> list[dict]:
    params = {"per-page": per_page, "mailto": mailto, "filter": filter_clause}
    resp = session.get(OPENALEX, params=params)
    resp.raise_for_status()
    return resp.json().get("results", [])


def collect(session, lib_cfg: dict, mailto: str) -> list[dict]:
    if not lib_cfg.get("enabled"):
        return []
    dois = fetch_dois(session, lib_cfg)
    if not dois:
        return []

    discovery_days = int(lib_cfg.get("discovery_days", 120))
    max_authors = int(lib_cfg.get("max_authors", 15))
    max_seed_works = int(lib_cfg.get("max_seed_works", 200))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=discovery_days)).strftime("%Y-%m-%d")

    # 1) Resolve library DOIs -> OpenAlex work ids + author frequencies.
    lib_work_ids: list[str] = []
    author_count: dict[str, int] = {}
    for batch in _chunks(dois, 50):
        for w in _works(session, "doi:" + "|".join(batch), mailto):
            lib_work_ids.append(_short(w.get("id")))
            for a in w.get("authorships", []):
                aid = _short((a.get("author") or {}).get("id"))
                if aid:
                    author_count[aid] = author_count.get(aid, 0) + 1
    if not lib_work_ids:
        return []
    lib_set = set(lib_work_ids)

    items: list[dict] = []
    seen: set[str] = set()

    def add(work: dict, source: str):
        wid = _short(work.get("id"))
        if wid in lib_set or wid in seen:
            return
        seen.add(wid)
        items.append(_work_to_item(work, source))

    # 2) Recent papers CITING the library (bibliographic descendants).
    for batch in _chunks(lib_work_ids[:max_seed_works], 50):
        clause = f"cites:{'|'.join(batch)},from_publication_date:{cutoff}"
        for w in _works(session, clause, mailto):
            add(w, "library: cites your library")

    # 3) Recent papers BY the authors you read most.
    top_authors = [a for a, _ in sorted(author_count.items(), key=lambda kv: -kv[1])[:max_authors]]
    if top_authors:
        clause = (f"authorships.author.id:{'|'.join(top_authors)},"
                  f"from_publication_date:{cutoff},type:article")
        for w in _works(session, clause, mailto):
            add(w, "library: author you read")

    return items
