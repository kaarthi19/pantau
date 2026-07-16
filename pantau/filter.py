"""Two-stage relevance filter, run once per track.

Stage 1 — prefilter (zero cost):
  research: papers always pass; news/org need >=1 topic hit; an exclude-term hit
            is a hard zero.
  jobs:     Tier-0/1 items bypass the prefilter entirely (time-sensitive);
            Tier-2 aggregator items need >=1 jobs-keyword hit.

Stage 2 — scorer:
  haiku:    batched, temperature 0, strict-JSON, one parse retry.
  keyword:  per-track weighted keyword scoring ($0 fallback; visa='unknown').

Items that fail the prefilter are written as score 0 (scorer='prefilter') so
they are never re-queued to the LLM.
"""
from __future__ import annotations

import json
import os

RESEARCH_TAGS = ("ch3-garuda", "ch4-reform", "captive-coal",
                 "apg-regional", "stakeholder", "methods")
JOBS_TAGS = ("philanthropy", "intermediary", "mdb", "fellowship",
             "policy-org", "academic", "industry")
VISA_VALUES = ("sponsor-friendly", "silent", "citizens-only", "unknown")


# --- Stage 1: prefilter -----------------------------------------------------

def _hits(text: str, terms: list[str]) -> int:
    return sum(1 for t in terms if t and t.lower() in text)


def prefilter(item: dict, track: str, kw: dict) -> tuple[bool, int]:
    """Return (passes, topic_hit_count)."""
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    tkw = kw.get(track, {})
    exclude = tkw.get("exclude", [])
    topic = tkw.get("topic", [])
    n = _hits(text, topic)

    if track == "jobs":
        tier = item.get("tier")
        if tier in (0, 1):
            return True, n          # Tier-0/1 always bypass the prefilter
        return n >= 1, n            # Tier-2 aggregator needs a topic hit

    # research
    if _hits(text, exclude) > 0 and item.get("source_type") != "paper":
        return False, n
    if item.get("source_type") == "paper":
        return True, n
    return n >= 1, n               # news/org need a topic hit


# --- Stage 2b: keyword scorer ----------------------------------------------

def keyword_score(item: dict, track: str, kw: dict) -> dict:
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    tkw = kw.get(track, {})
    if _hits(text, tkw.get("exclude", [])) > 0:
        return _result(item, 0, "none", "excluded topic", track, "keyword")

    tag_terms = tkw.get("tags", {})
    tag_hits = {tag: _hits(text, terms) for tag, terms in tag_terms.items()}
    best_tag, best = ("none", 0)
    if tag_hits:
        best_tag = max(tag_hits, key=tag_hits.get)
        best = tag_hits[best_tag]
    topic = _hits(text, tkw.get("topic", []))
    region = _hits(text, tkw.get("region", []))
    score = min(10, best * 3 + topic + region)
    tag = best_tag if best > 0 else "none"
    why = f"keyword match: {best} tag / {topic} topic / {region} region hits"
    return _result(item, score, tag, why, track, "keyword")


def _result(item, score, tag, why, track, scorer) -> dict:
    out = {
        "id": item["id"],
        "score": int(max(0, min(10, score))),
        "tag": tag if tag in (RESEARCH_TAGS if track == "research" else JOBS_TAGS) else "none",
        "rationale": why,
        "scorer": scorer,
    }
    out["visa"] = None if track == "research" else "unknown"
    return out


# --- Stage 2a: Haiku scorer -------------------------------------------------

def _clamp_result(raw: dict, track: str, scorer: str) -> dict:
    tag = raw.get("tag", "none")
    allowed = RESEARCH_TAGS if track == "research" else JOBS_TAGS
    if tag not in allowed:
        tag = "none"
    try:
        score = int(raw.get("score", 0))
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(10, score))
    visa = None
    if track == "jobs":
        visa = raw.get("visa", "unknown")
        if visa not in VISA_VALUES:
            visa = "unknown"
    return {
        "id": raw.get("id"),
        "score": score,
        "tag": tag,
        "rationale": (raw.get("why") or raw.get("rationale") or "")[:280],
        "visa": visa,
        "scorer": scorer,
    }


def _batch_payload(items: list[dict]) -> str:
    slim = [{
        "id": it["id"],
        "title": it.get("title", ""),
        "org": it.get("org"),
        "location": it.get("location"),
        "summary": (it.get("summary") or "")[:1000],
    } for it in items]
    return json.dumps(slim, ensure_ascii=False)


def haiku_score(items: list[dict], track: str, model: str, system_prompt: str,
                batch_size: int) -> list[dict]:
    import anthropic

    client = anthropic.Anthropic()
    results: list[dict] = []
    for start in range(0, len(items), batch_size):
        batch = items[start:start + batch_size]
        by_id = {it["id"]: it for it in batch}
        user = ("Score every item below. Return ONLY a JSON array, one object "
                "per item, no markdown fences.\n\nITEMS:\n" + _batch_payload(batch))
        parsed = _call_and_parse(client, model, system_prompt, user)
        if parsed is None:  # one retry with a firmer nudge
            parsed = _call_and_parse(
                client, model, system_prompt,
                user + "\n\nReturn valid JSON only — an array of objects.")
        if parsed is None:
            continue  # leave unscored; next run retries
        for raw in parsed:
            if raw.get("id") in by_id:
                results.append(_clamp_result(raw, track, "haiku"))
    return results


def _call_and_parse(client, model, system_prompt, user) -> list[dict] | None:
    try:
        resp = client.messages.create(
            model=model, max_tokens=2048, temperature=0,
            system=system_prompt,
            messages=[{"role": "user", "content": user}],
        )
    except Exception:  # noqa: BLE001 — SDK already backs off 429/5xx
        return None
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
    text = text.replace("```json", "").replace("```", "").strip()
    s, e = text.find("["), text.rfind("]")
    if s == -1 or e == -1:
        return None
    try:
        data = json.loads(text[s:e + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


def scoring_mode(configured: str) -> str:
    """Auto-fall back to keyword mode when no API key is present."""
    if configured == "haiku" and not os.environ.get("ANTHROPIC_API_KEY"):
        return "keyword"
    return configured
