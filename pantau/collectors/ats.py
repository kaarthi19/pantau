"""Jobs Tier-1 collector: public JSON ATS endpoints (no auth).

  Greenhouse: https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
  Lever:      https://api.lever.co/v0/postings/{slug}?mode=json
  Ashby:      https://api.ashbyhq.com/posting-api/job-board/{slug}
  Workable:   https://apply.workable.com/api/v1/widget/accounts/{slug}   ({slug}=numeric account id)
  Breezy:     https://{slug}.breezy.hr/json

Job identity = the ATS job id (stable across content edits). We store the seed
``{platform}:{slug}:{job_id}`` so re-runs never re-emit an unchanged posting and
a content edit on a known id is ignored.
"""
from __future__ import annotations

from ..net import strip_html


def _greenhouse(session, slug: str) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    data = _get_json(session, url)
    out = []
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name")
        out.append({
            "job_id": str(j.get("id")),
            "title": j.get("title", ""),
            "location": loc,
            "url": j.get("absolute_url", ""),
            "summary": strip_html(j.get("content", "")),
            "published_at": (j.get("updated_at") or "")[:10],
        })
    return out


def _lever(session, slug: str) -> list[dict]:
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    data = _get_json(session, url)
    out = []
    for j in data if isinstance(data, list) else []:
        loc = (j.get("categories") or {}).get("location")
        created = j.get("createdAt")
        published = ""
        if isinstance(created, (int, float)):
            from datetime import datetime, timezone
            published = datetime.fromtimestamp(created / 1000, timezone.utc).strftime("%Y-%m-%d")
        out.append({
            "job_id": str(j.get("id")),
            "title": j.get("text", ""),
            "location": loc,
            "url": j.get("hostedUrl", ""),
            "summary": strip_html(j.get("descriptionPlain") or j.get("description", "")),
            "published_at": published,
        })
    return out


def _ashby(session, slug: str) -> list[dict]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    data = _get_json(session, url)
    out = []
    for j in data.get("jobs", []):
        loc = j.get("location") or (j.get("address") or {}).get("postalAddress", {}).get("addressLocality")
        out.append({
            "job_id": str(j.get("id") or j.get("jobId") or j.get("jobPostingId")),
            "title": j.get("title", ""),
            "location": loc,
            "url": j.get("jobUrl") or j.get("applyUrl", ""),
            "summary": strip_html(j.get("descriptionPlain") or j.get("descriptionHtml", "")),
            "published_at": (j.get("publishedAt") or "")[:10],
        })
    return out


def _workable(session, slug: str) -> list[dict]:
    # Legacy widget API keyed by numeric account id. The list lacks a full
    # description; compose a short summary from department/industry/location.
    url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
    data = _get_json(session, url)
    out = []
    for j in data.get("jobs", []):
        loc = ", ".join(p for p in (j.get("city"), j.get("state"), j.get("country")) if p)
        if j.get("telecommuting"):
            loc = (loc + " · remote").lstrip(" ·")
        bits = [b for b in (j.get("department"), j.get("industry")) if b]
        out.append({
            "job_id": str(j.get("shortcode") or j.get("id")),
            "title": j.get("title", ""),
            "location": loc or None,
            "url": j.get("url") or j.get("shortlink", ""),
            "summary": strip_html(" · ".join(bits)),
            "published_at": (j.get("published_on") or j.get("created_at") or "")[:10],
        })
    return out


def _breezy(session, slug: str) -> list[dict]:
    url = f"https://{slug}.breezy.hr/json"
    data = _get_json(session, url)
    rows = data if isinstance(data, list) else data.get("positions", [])
    out = []
    for j in rows:
        loc = (j.get("location") or {})
        loc_name = loc.get("name") or (loc.get("country") or {}).get("name")
        if loc.get("is_remote"):
            loc_name = ((loc_name or "") + " · remote").lstrip(" ·")
        out.append({
            "job_id": str(j.get("id")),
            "title": j.get("name", ""),
            "location": loc_name or None,
            "url": j.get("url", ""),
            "summary": strip_html(j.get("description", "") or (j.get("department") or "")),
            "published_at": (j.get("published_date") or "")[:10],
        })
    return out


_FETCHERS = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby,
             "workable": _workable, "breezy": _breezy}


def _get_json(session, url: str):
    resp = session.get(url)
    resp.raise_for_status()
    return resp.json()


def collect_org(session, org: dict) -> list[dict]:
    """Collect one Tier-1 org. Raises on transport/parse error (caught by run.py)."""
    platform = (org.get("ats") or "").lower()
    slug = org.get("slug")
    fetcher = _FETCHERS.get(platform)
    if not fetcher or not slug:
        return []  # non-ATS orgs (pagewatch/workday) are handled elsewhere
    tier = org.get("tier", 1)
    always_alert = bool(org.get("always_alert"))
    raw = fetcher(session, slug)
    items = []
    for r in raw:
        items.append({
            "track": "jobs",
            "source": org["name"],
            "source_type": "ats",
            "title": r["title"],
            "url": r["url"],
            "doi": None,
            "org": org["name"],
            "location": r.get("location"),
            "deadline": None,
            "tier": tier,
            "published_at": r.get("published_at", ""),
            "summary": r.get("summary", ""),
            "_id_key": f"{platform}:{slug}:{r['job_id']}",
            "_always_alert": always_alert,
        })
    return items


def collect(session, orgs: list[dict], on_error=None):
    """Poll all ATS orgs. Returns (items, per-org run records).

    Per-org try/except — one broken board never kills the run.
    """
    items: list[dict] = []
    records: list[tuple[str, bool, str]] = []
    for org in orgs or []:
        name = org.get("name", "?")
        if (org.get("ats") or "").lower() not in _FETCHERS:
            continue
        try:
            got = collect_org(session, org)
            items.extend(got)
            records.append((name, True, f"{len(got)} postings"))
        except Exception as exc:  # noqa: BLE001
            records.append((name, False, f"{type(exc).__name__}: {exc}"))
            if on_error:
                on_error(name, exc)
    return items, records
