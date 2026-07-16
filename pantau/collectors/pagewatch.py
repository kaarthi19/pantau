"""Jobs Tier-0 collector: content-hash diff on program / non-ATS pages.

Fetch the page, strip nav/footer/scripts to isolate the main content region,
SHA-256 it, compare to the hash stored in ``meta``. On change: emit one item and
update the stored hash. Fragile by design, acceptable at ~15 pages.

Each program dict: {name, url, tier(default 0)}.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from .. import store

# Strip whole elements whose content is boilerplate, then all remaining tags.
_DROP_BLOCKS = re.compile(
    r"<(script|style|nav|header|footer|noscript|svg)\b[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _content_hash(html: str) -> str:
    body = _DROP_BLOCKS.sub(" ", html or "")
    text = _WS.sub(" ", _TAGS.sub(" ", body)).strip()
    return hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()


def _meta_key(name: str) -> str:
    return f"pagewatch:{name}"


def collect(session, conn, programs: list[dict], on_error=None):
    """Diff each watched page. Returns (items, per-page run records).

    A changed page yields exactly one item; unchanged pages yield none. The
    very first sighting of a page seeds its hash silently (no false alert).
    """
    items: list[dict] = []
    records: list[tuple[str, bool, str]] = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for prog in programs or []:
        name = prog["name"]
        try:
            resp = session.get(prog["url"])
            resp.raise_for_status()
            new_hash = _content_hash(resp.text)
            key = _meta_key(name)
            prev = store.get_meta(conn, key)
            store.set_meta(conn, key, new_hash)
            if prev is None:
                records.append((f"pagewatch:{name}", True, "seeded"))
                continue
            if prev != new_hash:
                items.append({
                    "track": "jobs",
                    "source": name,
                    "source_type": "pagewatch",
                    "title": f"{name} page changed",
                    "url": prog["url"],
                    "doi": None,
                    "org": prog.get("org", name),
                    "location": None,
                    "deadline": None,
                    "tier": prog.get("tier", 0),
                    "published_at": today,
                    "summary": (
                        f"The watched page for {name} changed since the last run. "
                        "Click through to see what's new."
                    ),
                    "_id_key": f"pagewatch:{name}:{new_hash[:12]}",
                    "_always_alert": bool(prog.get("always_alert")),
                })
                records.append((f"pagewatch:{name}", True, "changed"))
            else:
                records.append((f"pagewatch:{name}", True, "unchanged"))
        except Exception as exc:  # noqa: BLE001
            records.append((f"pagewatch:{name}", False, f"{type(exc).__name__}: {exc}"))
            if on_error:
                on_error(name, exc)
    return items, records
