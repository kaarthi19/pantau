"""Shared RSS/Atom collector.

Used for two things:
  * research org feeds (Ember, IEEFA, IESR, …)  -> track=research, source_type=org
  * jobs Tier-2 aggregator feeds (Climatebase, …) -> track=jobs, source_type=aggregator, tier=2

Each feed dict: {name, url, track, source_type, tier(optional), org(optional)}.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import feedparser

from ..net import strip_html


def _published(entry) -> str:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        return datetime(*parsed[:6]).strftime("%Y-%m-%d")
    return ""


def _within_window(entry, cutoff: datetime) -> bool:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return True
    return datetime(*parsed[:6], tzinfo=timezone.utc) >= cutoff


def collect_feed(session, feed_cfg: dict, window_days: int) -> list[dict]:
    resp = session.get(feed_cfg["url"])
    resp.raise_for_status()
    parsed = feedparser.parse(resp.text)
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    track = feed_cfg.get("track", "research")
    source_type = feed_cfg.get("source_type", "org" if track == "research" else "aggregator")
    tier = feed_cfg.get("tier", 2 if track == "jobs" else None)

    items: list[dict] = []
    for e in parsed.entries:
        if not _within_window(e, cutoff):
            continue
        items.append({
            "track": track,
            "source": feed_cfg["name"],
            "source_type": source_type,
            "title": (e.get("title") or "").strip(),
            "url": e.get("link", ""),
            "doi": None,
            "published_at": _published(e),
            "summary": strip_html(e.get("summary", "") or e.get("description", "")),
            "org": feed_cfg.get("org"),
            "location": None,
            "deadline": None,
            "tier": tier,
        })
    return items


def collect(session, feeds: list[dict], window_days: int,
            on_error=None) -> tuple[list[dict], list[tuple[str, bool, str]]]:
    """Collect a list of feeds. Returns (items, per-feed run records).

    A single broken feed never aborts the batch — it is logged and skipped.
    """
    items: list[dict] = []
    records: list[tuple[str, bool, str]] = []
    for feed_cfg in feeds or []:
        name = feed_cfg["name"]
        try:
            got = collect_feed(session, feed_cfg, window_days)
            items.extend(got)
            records.append((name, True, f"{len(got)} items"))
        except Exception as exc:  # noqa: BLE001 — one feed must never kill the run
            records.append((name, False, f"{type(exc).__name__}: {exc}"))
            if on_error:
                on_error(name, exc)
    return items, records
