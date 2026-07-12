"""Jobs Tier-0 monthly sweep: one agentic web-search call for new programs.

Runs on the first pipeline invocation of each calendar month (tracked in
``meta.last_sweep_month``). Uses the Messages API with the server-side web
search tool. VERIFY the tool ``type`` string at build time — for Sonnet 4.6 it
is ``web_search_20260209`` (dynamic filtering). Any failure -> skip silently
until next month.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

from .. import store

WEB_SEARCH_TOOL = "web_search_20260209"

PROMPT_TEMPLATE = """\
Search the web for climate/energy career opportunities announced in roughly the
last 45 days that fit the profile below. Focus on things structured job boards
miss: (1) new fellowships, scout programs, practitioner residencies, or
early-career funder/cohort programs; (2) newly launched funds or programs on the
Asia energy transition likely to hire soon. US-based or Southeast-Asia-relevant.
Open to non-US citizens where stated (flag eligibility when unclear).

CANDIDATE PROFILE
{profile}

Return ONLY a JSON array (no prose, no markdown fences), each element:
{{"title": "...", "url": "https://...", "org": "...", "deadline": "YYYY-MM-DD or null",
  "why": "<one line fit assessment; state if eligibility is unclear>"}}
"""


def _this_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def is_due(conn, force: bool = False) -> bool:
    if force:
        return True
    return store.get_meta(conn, "last_sweep_month") != _this_month()


def _extract_json_array(text: str) -> list[dict]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _run_search(cfg: dict, profile: str) -> list[dict]:
    import anthropic

    client = anthropic.Anthropic()
    prompt = PROMPT_TEMPLATE.format(profile=profile)
    messages = [{"role": "user", "content": prompt}]
    tools = [{
        "type": WEB_SEARCH_TOOL,
        "name": "web_search",
        "max_uses": cfg.get("max_web_searches", 8),
    }]
    text_parts: list[str] = []
    for _ in range(6):  # bounded pause_turn continuation
        resp = client.messages.create(
            model=cfg.get("model", "claude-sonnet-4-6"),
            max_tokens=4096,
            tools=tools,
            messages=messages,
        )
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text_parts.append(block.text)
        if resp.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": resp.content})
            continue
        break
    return _extract_json_array("\n".join(text_parts))


def run_if_due(conn, cfg: dict, profile: str, force: bool = False):
    """Returns (items, ran, note). Marks the month done only on a clean run."""
    if not cfg.get("enabled", True):
        return [], False, "sweep disabled"
    if not is_due(conn, force):
        return [], False, "already ran this month"
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return [], False, "no ANTHROPIC_API_KEY — sweep skipped"

    try:
        raw = _run_search(cfg, profile)
    except Exception as exc:  # noqa: BLE001 — sweep failure must never fail the run
        return [], False, f"{type(exc).__name__}: {exc}"

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    items = []
    for r in raw:
        url = (r.get("url") or "").strip()
        if not url:
            continue
        deadline = r.get("deadline")
        if deadline in ("null", "", "unknown", "N/A"):
            deadline = None
        items.append({
            "track": "jobs",
            "source": "sweep",
            "source_type": "sweep",
            "title": r.get("title", "(untitled)"),
            "url": url,
            "doi": None,
            "org": r.get("org"),
            "location": None,
            "deadline": deadline,
            "tier": 0,
            "published_at": today,
            "summary": r.get("why", ""),
        })
    store.set_meta(conn, "last_sweep_month", _this_month())
    return items, True, f"{len(items)} candidates"
