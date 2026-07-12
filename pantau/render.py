"""Render the public dashboard — RESEARCH TRACK ONLY.

The GitHub Pages site is publicly reachable even from a private repo, so the
jobs track must never reach this template. ``_assert_no_jobs`` enforces that at
render time and has a dedicated test.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "j2"]),
    )


def _assert_no_jobs(rows) -> None:
    for r in rows:
        if (r["track"] if _is_row(r) else r.get("track")) != "research":
            raise AssertionError("jobs-track row leaked into the research dashboard")


def _is_row(r) -> bool:
    try:
        r["track"]
        return True
    except (KeyError, TypeError):
        return False


def _get(r, key, default=None):
    try:
        val = r[key]
        return val if val is not None else default
    except (KeyError, IndexError, TypeError):
        return default


def build_context(conn, cfg: dict) -> dict:
    research = cfg["tracks"]["research"]
    show = research["show_threshold"]
    highlight = research["highlight_threshold"]
    render_days = cfg.get("render_days", 14)

    cutoff = (datetime.now(timezone.utc) - timedelta(days=render_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    top_cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")

    listing = conn.execute(
        "SELECT * FROM items WHERE track='research' AND score >= ? "
        "AND COALESCE(published_at, fetched_at) >= ? "
        "ORDER BY COALESCE(published_at, fetched_at) DESC, score DESC LIMIT 300",
        (show, cutoff),
    ).fetchall()
    top = conn.execute(
        "SELECT * FROM items WHERE track='research' AND score >= ? "
        "AND COALESCE(published_at, fetched_at) >= ? "
        "ORDER BY score DESC, COALESCE(published_at, fetched_at) DESC LIMIT 8",
        (highlight, top_cutoff),
    ).fetchall()

    _assert_no_jobs(listing)
    _assert_no_jobs(top)

    tags = research["tags"]

    def shape(r):
        tag = _get(r, "tag", "none")
        meta = tags.get(tag, {"label": tag, "color": "#888888"})
        return {
            "title": _get(r, "title", ""),
            "url": _get(r, "url", ""),
            "source": _get(r, "source", ""),
            "score": _get(r, "score", 0),
            "rationale": _get(r, "rationale", ""),
            "date": _get(r, "published_at") or _get(r, "fetched_at", ""),
            "tag_label": meta["label"],
            "tag_color": meta["color"],
        }

    failures = _source_failures(conn)
    return {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "top_picks": [shape(r) for r in top],
        "items": [shape(r) for r in listing],
        "total": len(listing),
        "scorer_mode": _latest_scorer(conn),
        "failures": failures,
    }


def _source_failures(conn):
    from . import store
    return store.recent_source_failures(conn, streak=3)


def _latest_scorer(conn) -> str:
    row = conn.execute(
        "SELECT scorer FROM items WHERE scorer IS NOT NULL AND scorer != 'prefilter' "
        "ORDER BY scored_at DESC LIMIT 1"
    ).fetchone()
    return row["scorer"] if row else "keyword"


def render(conn, cfg: dict, out_path: str = "docs/index.html") -> str:
    context = build_context(conn, cfg)
    html = _env().get_template("dashboard.html.j2").render(**context)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path
