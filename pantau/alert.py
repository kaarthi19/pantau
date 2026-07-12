"""Immediate email alerts for high-scoring Tier-0/1 jobs items.

After scoring, any unalerted jobs item with tier in {0,1} and score >=
alert_threshold gets an individual email in the same run. Capped by
alerts.max_per_run; overflow is carried by the digest. On the very first run
alerts are suppressed entirely (and marked) so a backfill flood can't fire —
the first digest carries everything.
"""
from __future__ import annotations

from . import store, mailer


def _candidates(conn, threshold: int):
    return conn.execute(
        "SELECT * FROM items WHERE track='jobs' AND alerted_at IS NULL "
        "AND tier IN (0,1) AND score >= ? "
        "ORDER BY score DESC, COALESCE(published_at, fetched_at) DESC",
        (threshold,),
    ).fetchall()


def _email_bodies(row) -> tuple[str, str]:
    org = row["org"] or row["source"] or ""
    title = row["title"] or ""
    loc = row["location"] or "—"
    deadline = row["deadline"] or "—"
    visa = row["visa"] or "unknown"
    why = row["rationale"] or ""
    url = row["url"] or ""
    text = (
        f"{title}\n{org} · {loc}\n"
        f"Score {row['score']}/10 · deadline {deadline} · visa: {visa}\n"
        f"{why}\n{url}\n"
    )
    html = (
        f'<h2 style="margin:0 0 4px"><a href="{url}">{title}</a></h2>'
        f'<p style="margin:0;color:#555">{org} · {loc}</p>'
        f'<p style="margin:6px 0">Score <b>{row["score"]}/10</b> · '
        f'deadline {deadline} · visa: <b>{visa}</b></p>'
        f'<p style="margin:6px 0">{why}</p>'
        f'<p><a href="{url}">{url}</a></p>'
    )
    return html, text


def run(conn, cfg: dict, first_run: bool) -> dict:
    alerts_cfg = cfg.get("alerts", {})
    if not alerts_cfg.get("enabled", True):
        return {"sent": 0, "note": "alerts disabled"}

    threshold = cfg["tracks"]["jobs"]["alert_threshold"]
    rows = _candidates(conn, threshold)
    if not rows:
        return {"sent": 0, "note": "no candidates"}

    if first_run:
        for r in rows:
            store.mark_alerted(conn, r["id"])
        return {"sent": 0, "note": f"first run — suppressed {len(rows)} (digest carries them)"}

    if not mailer.creds_present():
        return {"sent": 0, "note": "no SMTP creds — alerts skipped (digest carries them)"}

    recipient = cfg["digest"]["recipient"]
    cap = alerts_cfg.get("max_per_run", 3)
    sent = 0
    for r in rows:
        if sent >= cap:
            break
        org = r["org"] or r["source"] or ""
        subject = f"Pantau alert — {org}: {r['title']}"
        html, text = _email_bodies(r)
        try:
            if mailer.send_email(recipient, subject, html, text):
                store.mark_alerted(conn, r["id"])
                sent += 1
        except Exception:  # noqa: BLE001 — an alert failure must never fail the run
            break
    overflow = max(0, len(rows) - sent)
    return {"sent": sent, "note": f"{sent} sent, {overflow} deferred to digest"}
