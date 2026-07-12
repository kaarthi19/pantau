"""Daily email digest: jobs block on top, research below.

Trigger: once per UTC day, on the first run at/after send_hour_utc, tracked in
``meta.last_digest_sent_at``. Zero items overall -> skip (meta not advanced, so a
later run the same day can still send). A digest failure never fails the run.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import store, mailer

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")


def _env():
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "j2"]),
    )


def is_due(conn, send_hour_utc: int, force: bool = False) -> bool:
    if force:
        return True
    now = datetime.now(timezone.utc)
    last = store.get_meta(conn, "last_digest_sent_at")
    if last and last[:10] == now.strftime("%Y-%m-%d"):
        return False
    return now.hour >= send_hour_utc


def _since(conn) -> str:
    last = store.get_meta(conn, "last_digest_sent_at")
    floor = (datetime.now(timezone.utc) - timedelta(hours=36)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if last and last > floor:
        return last
    return floor


def build(conn, cfg: dict) -> dict | None:
    since = _since(conn)
    jobs_cfg = cfg["tracks"]["jobs"]
    show_j = jobs_cfg["show_threshold"]
    research_cfg = cfg["tracks"]["research"]
    show_r = research_cfg["show_threshold"]

    tier01 = conn.execute(
        "SELECT * FROM items WHERE track='jobs' AND tier IN (0,1) AND score IS NOT NULL "
        "AND fetched_at >= ? ORDER BY score DESC, tier ASC", (since,),
    ).fetchall()
    tier2 = conn.execute(
        "SELECT * FROM items WHERE track='jobs' AND (tier=2 OR tier IS NULL) "
        "AND score >= ? AND fetched_at >= ? ORDER BY score DESC", (show_j, since),
    ).fetchall()
    research = conn.execute(
        "SELECT * FROM items WHERE track='research' AND score >= ? "
        "AND fetched_at >= ? ORDER BY score DESC", (show_r, since),
    ).fetchall()

    n_jobs = len(tier01) + len(tier2)
    n_research = len(research)
    if n_jobs == 0 and n_research == 0:
        return None

    tags = research_cfg["tags"]
    groups: list[dict] = []
    for tag, meta in tags.items():
        rows = [r for r in research if (r["tag"] or "none") == tag]
        if rows:
            groups.append({"label": meta["label"], "color": meta["color"],
                           "entries": [_shape_research(r) for r in rows]})
    ungrouped = [r for r in research if (r["tag"] or "none") not in tags]
    if ungrouped:
        groups.append({"label": "Other", "color": "#888888",
                       "entries": [_shape_research(r) for r in ungrouped]})

    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    synthesis = _maybe_synthesis(cfg, tier01, tier2, research)
    return {
        "date": date,
        "n_jobs": n_jobs,
        "n_research": n_research,
        "tier01": [_shape_job(r) for r in tier01],
        "tier2": [_shape_job(r) for r in tier2],
        "research_groups": groups,
        "synthesis": synthesis,
        "failures": store.recent_source_failures(conn, streak=3),
        "subject": f"Pantau — {n_jobs} jobs · {n_research} research · {date}",
    }


def _shape_job(r) -> dict:
    return {
        "title": r["title"] or "", "url": r["url"] or "",
        "org": r["org"] or r["source"] or "", "location": r["location"] or "",
        "score": r["score"], "tag": r["tag"] or "", "tier": r["tier"],
        "deadline": _deadline_label(r["deadline"]), "visa": r["visa"] or "unknown",
        "why": r["rationale"] or "",
    }


def _shape_research(r) -> dict:
    return {
        "title": r["title"] or "", "url": r["url"] or "",
        "source": r["source"] or "", "score": r["score"], "why": r["rationale"] or "",
    }


def _deadline_label(deadline: str | None) -> str:
    if not deadline:
        return ""
    try:
        d = datetime.strptime(deadline[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return deadline
    days = (d - datetime.now(timezone.utc)).days
    if days < 0:
        return f"closed ({deadline})"
    return f"closes in {days}d ({deadline})"


def _maybe_synthesis(cfg, tier01, tier2, research) -> str | None:
    if cfg["digest"].get("synthesis") != "sonnet" or not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
        client = anthropic.Anthropic()
        lines = [f"- [{r['track'] if 'track' in r.keys() else 'jobs'}] {r['title']}"
                 for r in list(tier01) + list(tier2) + list(research)][:40]
        resp = client.messages.create(
            model=cfg["digest"].get("synthesis_model", "claude-sonnet-4-6"),
            max_tokens=300, temperature=0,
            messages=[{"role": "user", "content":
                       "In 2-3 sentences, tell me what matters most in today's "
                       "research + jobs digest for a power-systems PhD focused on "
                       "SE Asia energy transition:\n" + "\n".join(lines)}],
        )
        return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
    except Exception:  # noqa: BLE001 — synthesis is best-effort
        return None


def _text(ctx: dict) -> str:
    out = [f"Pantau digest — {ctx['date']}",
           f"{ctx['n_jobs']} jobs · {ctx['n_research']} research", ""]
    if ctx.get("synthesis"):
        out += [ctx["synthesis"], ""]
    if ctx["tier01"] or ctx["tier2"]:
        out.append("== JOBS ==")
        for j in ctx["tier01"] + ctx["tier2"]:
            dl = f" · {j['deadline']}" if j["deadline"] else ""
            out.append(f"[{j['score']}] {j['title']} — {j['org']} ({j['location']})"
                       f" · visa:{j['visa']}{dl}\n    {j['why']}\n    {j['url']}")
        out.append("")
    for g in ctx["research_groups"]:
        out.append(f"== {g['label']} ==")
        for it in g["entries"]:
            out.append(f"[{it['score']}] {it['title']} — {it['source']}\n"
                       f"    {it['why']}\n    {it['url']}")
        out.append("")
    if ctx["failures"]:
        out.append("Source failures (3 consecutive): " + ", ".join(ctx["failures"]))
    return "\n".join(out)


def run(conn, cfg: dict, force: bool = False) -> dict:
    if not cfg["digest"].get("enabled", True):
        return {"sent": False, "note": "digest disabled"}
    if not is_due(conn, cfg["digest"].get("send_hour_utc", 0), force):
        return {"sent": False, "note": "not due yet today"}

    ctx = build(conn, cfg)
    if ctx is None:
        return {"sent": False, "note": "no items — skipped"}

    html = _env().get_template("digest.html.j2").render(**ctx)
    text = _text(ctx)
    try:
        ok = mailer.send_email(cfg["digest"]["recipient"], ctx["subject"], html, text)
    except Exception as exc:  # noqa: BLE001 — digest failure must never fail the run
        return {"sent": False, "note": f"send error: {type(exc).__name__}: {exc}"}
    if ok:
        store.set_meta(conn, "last_digest_sent_at", store.now_iso())
        return {"sent": True, "note": ctx["subject"]}
    return {"sent": False, "note": "no SMTP creds — digest skipped"}
