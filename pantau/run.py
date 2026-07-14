"""Pantau orchestrator: collect -> store -> filter -> render -> alert -> digest.

Every stage is idempotent and every source is wrapped so a single failure logs
and continues rather than killing the run. Scoring auto-selects keyword mode
when ANTHROPIC_API_KEY is unset, so the whole pipeline is buildable and testable
with no key.

Usage:
    python -m pantau.run                 # full pipeline
    python -m pantau.run --dry-run       # collect + per-source counts, no writes
    python -m pantau.run --stage collect # a single stage (state persists in the db)
    python -m pantau.run --force-sweep   # force the monthly agentic sweep
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

import yaml

from . import store, filter as flt, render as render_mod, alert as alert_mod, digest as digest_mod
from .net import PoliteSession, CONTACT_EMAIL
from .collectors import openalex, arxiv, gnews, rss, ats, pagewatch, sweep, library

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_all(config_path: str) -> dict:
    cfg = _load_yaml(config_path)
    sources = _load_yaml(os.path.join(ROOT, "registry", "sources.yaml"))
    orgs = _load_yaml(os.path.join(ROOT, "registry", "orgs.yaml"))
    programs = _load_yaml(os.path.join(ROOT, "registry", "programs.yaml"))
    return {"cfg": cfg, "sources": sources, "orgs": orgs, "programs": programs}


# --- collection -------------------------------------------------------------

def _library_due(conn, lib_cfg: dict) -> bool:
    """Library seeding runs at most every `every_days` (default 7) — a weekly
    shortlist, not a per-run job. conn=None (dry-run) always runs."""
    if conn is None:
        return True
    every = int(lib_cfg.get("every_days", 7))
    last = store.get_meta(conn, "last_library_run")
    if not last:
        return True
    try:
        last_dt = datetime.strptime(last[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return (datetime.now(timezone.utc) - last_dt).days >= every


def collect_all(reg: dict, conn, window_days: int) -> tuple[list[dict], list[tuple]]:
    """Return (items, run_records). run_records = (source, count, ok, note)."""
    sess = PoliteSession()
    sources = reg["sources"]
    research = sources.get("research", {})
    items: list[dict] = []
    records: list[tuple] = []

    def guarded(name, fn):
        try:
            got = fn()
            records.append((name, len(got), True, f"{len(got)} items"))
            items.extend(got)
        except Exception as exc:  # noqa: BLE001
            records.append((name, 0, False, f"{type(exc).__name__}: {exc}"))

    oa = research.get("openalex", {})
    guarded("openalex", lambda: openalex.collect(
        sess, oa.get("queries", []), oa.get("issns", []), oa.get("authors", []),
        window_days, CONTACT_EMAIL))
    ax = research.get("arxiv", {})
    guarded("arxiv", lambda: arxiv.collect(
        sess, ax.get("categories", []), ax.get("terms", []), window_days))
    guarded("gnews", lambda: gnews.collect(
        sess, research.get("gnews", {}).get("queries", []), window_days))

    # research org feeds + jobs Tier-2 aggregators share the generic rss reader
    feeds = list(research.get("feeds", []))
    feeds += list(sources.get("jobs", {}).get("aggregators", []))
    feed_items, feed_records = rss.collect(sess, feeds, window_days)
    items.extend(feed_items)
    for name, ok, note in feed_records:
        records.append((f"rss:{name}", 0, ok, note))

    # jobs Tier-1 ATS
    ats_items, ats_records = ats.collect(sess, reg["orgs"].get("tier1", []))
    items.extend(ats_items)
    for name, ok, note in ats_records:
        records.append((f"ats:{name}", 0, ok, note))

    # jobs Tier-0 + non-ATS orgs via content-diff pagewatch
    watch = list(reg["orgs"].get("pagewatch", []))
    for prog in reg["programs"].get("tier0", []):
        if prog.get("watch_url"):
            watch.append({"name": prog["name"], "url": prog["watch_url"],
                          "tier": 0, "org": prog.get("org", prog["name"])})
    pw_items, pw_records = pagewatch.collect(sess, conn, watch)
    items.extend(pw_items)
    for name, ok, note in pw_records:
        records.append((name, 0, ok, note))

    # research: weekly library-seeded discovery (Zotero) -> research-track items
    lib_cfg = reg["cfg"].get("library", {})
    if lib_cfg.get("enabled") and _library_due(conn, lib_cfg):
        guarded("library", lambda: library.collect(sess, lib_cfg, CONTACT_EMAIL))
        if conn is not None:
            store.set_meta(conn, "last_library_run", store.now_iso())

    return items, records


# --- scoring ----------------------------------------------------------------

def _row_to_item(row) -> dict:
    return {k: row[k] for k in row.keys()}


def score_track(conn, cfg: dict, reg: dict, track: str, mode: str) -> dict:
    keywords = reg["sources"].get("keywords", {})
    rows = store.unscored(conn, track)
    items = [_row_to_item(r) for r in rows]
    if not items:
        return {"scored": 0, "prefiltered": 0}

    passing, prefiltered = [], 0
    for it in items:
        ok, hits = flt.prefilter(it, track, keywords)
        it["prefilter_hits"] = hits
        if ok:
            passing.append(it)
        else:
            store.apply_score(conn, it["id"], 0, "none", "failed prefilter",
                              None if track == "research" else "unknown",
                              "prefilter", prefilter_hits=hits)
            prefiltered += 1

    # jobs are time-sensitive -> score first; then research newest-first
    if track == "research":
        passing.sort(key=lambda x: x.get("published_at") or x.get("fetched_at") or "",
                     reverse=True)

    cap = cfg["scoring"].get("max_llm_items_per_run", 150)
    to_score = passing[:cap] if mode == "haiku" else passing
    deferred = len(passing) - len(to_score)

    if mode == "haiku":
        prompt = _read(os.path.join(ROOT, cfg["tracks"][track]["prompt"]))
        results = flt.haiku_score(
            to_score, track, cfg["scoring"]["model"], prompt,
            cfg["scoring"].get("batch_size", 20))
    else:
        results = [flt.keyword_score(it, track, keywords) for it in to_score]

    for res in results:
        store.apply_score(conn, res["id"], res["score"], res["tag"],
                          res["rationale"], res.get("visa"), res["scorer"])
    return {"scored": len(results), "prefiltered": prefiltered, "deferred": deferred}


# --- pipeline ---------------------------------------------------------------

def run_pipeline(args) -> int:
    reg = load_all(args.config)
    cfg = reg["cfg"]
    window_days = cfg.get("window_days", 7)
    db_path = args.db or os.path.join(ROOT, "data", "pantau.db")

    if args.dry_run:
        conn = store.connect(":memory:")
        items, records = collect_all(reg, conn, window_days)
        print("=== DRY RUN — per-source counts (no writes) ===")
        for name, count, ok, note in records:
            flag = "ok " if ok else "FAIL"
            print(f"  [{flag}] {name}: {note}")
        print(f"Total items collected: {len(items)}")
        return 0

    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = store.connect(db_path)
    first_run = store.get_meta(conn, "bootstrapped") != "1"
    mode = flt.scoring_mode(cfg["scoring"].get("mode", "keyword"))
    stage = args.stage
    summary: list[str] = []

    if stage in ("all", "collect"):
        items, records = collect_all(reg, conn, window_days)
        new = store.upsert_items(conn, items)
        for name, count, ok, note in records:
            store.record_run(conn, name, count, 0, ok, note)
        summary.append(f"collect: {len(items)} fetched, {new} new")

    if stage in ("all", "score"):
        if reg["cfg"]["sweep"].get("enabled", True) and stage == "all":
            profile = _read(os.path.join(ROOT, cfg["tracks"]["jobs"]["prompt"]))
            sw_items, ran, note = sweep.run_if_due(conn, cfg["sweep"], profile,
                                                   force=args.force_sweep)
            if ran:
                store.upsert_items(conn, sw_items)
                store.record_run(conn, "sweep", len(sw_items), len(sw_items), True, note)
            summary.append(f"sweep: {note}")
        for track in ("jobs", "research"):
            res = score_track(conn, cfg, reg, track, mode)
            summary.append(f"score[{track}/{mode}]: {res}")

    if stage in ("all", "render"):
        out = render_mod.render(conn, cfg, os.path.join(ROOT, "docs", "index.html"))
        summary.append(f"render: {out}")

    if stage in ("all", "digest"):
        summary.append(f"alert: {alert_mod.run(conn, cfg, first_run)}")
        summary.append(f"digest: {digest_mod.run(conn, cfg, force=args.force_digest)}")

    if stage == "all":
        removed = store.prune(conn, cfg["tracks"]["research"]["show_threshold"],
                              cfg.get("prune", {}).get("keep_days", 180))
        if removed:
            summary.append(f"prune: {removed} rows removed")
        store.set_meta(conn, "bootstrapped", "1")

    print("\n".join(summary))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="pantau")
    p.add_argument("--config", default=os.path.join(ROOT, "config.yaml"))
    p.add_argument("--db", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force-sweep", action="store_true")
    p.add_argument("--force-digest", action="store_true")
    p.add_argument("--stage", default="all",
                   choices=["all", "collect", "score", "render", "digest"])
    return run_pipeline(p.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
