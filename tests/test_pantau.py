"""Fixture-driven tests: normalization, dedupe, prefilter/tier gating, ATS
parsing, pagewatch diff, JSON robustness, dashboard privacy, digest/alert guards.

Runs entirely offline — no live HTTP, no ANTHROPIC_API_KEY.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
FIX = os.path.join(ROOT, "tests", "fixtures")

from pantau import store, filter as flt, render, alert, digest
from pantau.net import normalize_url, normalize_title
from pantau.collectors import ats, pagewatch, sweep


# --- fakes -----------------------------------------------------------------

class FakeResp:
    def __init__(self, payload=None, text=None):
        self._payload = payload
        self.text = text
    def raise_for_status(self):
        pass
    def json(self):
        return self._payload


class FakeSession:
    """Returns a queued response regardless of URL/params."""
    def __init__(self, resp):
        self._resp = resp
    def get(self, url, **kw):
        r = self._resp
        return r(url) if callable(r) else r


def load_fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def sample_cfg():
    import yaml
    with open(os.path.join(ROOT, "config.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


# --- URL / id normalization ------------------------------------------------

def test_normalize_url_strips_tracking_and_slash():
    a = normalize_url("HTTPS://Example.com/Jobs/123/?utm_source=x&gh_src=y")
    b = normalize_url("https://example.com/Jobs/123")
    assert a == b == "https://example.com/Jobs/123"


def test_normalize_url_sorts_query():
    assert normalize_url("https://x.com/a?b=2&a=1") == "https://x.com/a?a=1&b=2"


def test_compute_id_precedence():
    assert store.compute_id({"doi": "10.1/AB", "url": "u"}) == \
        store.compute_id({"doi": "10.1/ab"})
    assert store.compute_id({"_id_key": "greenhouse:acme:1"}) != \
        store.compute_id({"_id_key": "greenhouse:acme:2"})


# --- dedupe ----------------------------------------------------------------

def test_upsert_dedupe_and_near_dupe():
    conn = store.connect(":memory:")
    item = {"track": "jobs", "org": "Acme", "title": "Analyst", "location": "DC",
            "url": "https://a.com/1", "_id_key": "greenhouse:acme:1", "tier": 1}
    assert store.upsert_items(conn, [item]) == 1
    assert store.upsert_items(conn, [item]) == 0          # same id
    migrated = dict(item, _id_key="lever:acme:99", url="https://a.com/new")
    assert store.upsert_items(conn, [migrated]) == 0      # near-dupe org/title/loc


# --- prefilter / tier gating -----------------------------------------------

KW = {
    "research": {"topic": ["grid", "power"], "exclude": ["horoscope"], "tags": {}},
    "jobs": {"topic": ["energy", "fellowship"], "exclude": [], "tags": {}},
}


def test_prefilter_research_paper_always_passes():
    ok, _ = flt.prefilter({"source_type": "paper", "title": "x", "summary": ""},
                          "research", KW)
    assert ok


def test_prefilter_research_news_needs_topic_and_excludes():
    assert not flt.prefilter({"source_type": "news", "title": "cats", "summary": ""},
                             "research", KW)[0]
    assert flt.prefilter({"source_type": "news", "title": "power grid", "summary": ""},
                         "research", KW)[0]
    assert not flt.prefilter({"source_type": "news", "title": "grid horoscope", "summary": ""},
                             "research", KW)[0]


def test_prefilter_jobs_tier_bypass():
    assert flt.prefilter({"tier": 1, "title": "anything", "summary": ""}, "jobs", KW)[0]
    assert flt.prefilter({"tier": 0, "title": "anything", "summary": ""}, "jobs", KW)[0]
    assert not flt.prefilter({"tier": 2, "title": "anything", "summary": ""}, "jobs", KW)[0]
    assert flt.prefilter({"tier": 2, "title": "energy fellowship", "summary": ""}, "jobs", KW)[0]


# --- keyword scoring -------------------------------------------------------

def test_keyword_score_tags_and_ranks():
    import yaml
    with open(os.path.join(ROOT, "registry", "sources.yaml"), encoding="utf-8") as f:
        kw = yaml.safe_load(f)["keywords"]
    hot = {"id": "1", "title": "PLN delays Sumatra-Java interconnection",
           "summary": "Indonesia grid dispatch reform", "org": None, "location": None}
    cold = {"id": "2", "title": "Ohio utility files rate case",
            "summary": "grid upgrades in Ohio", "org": None, "location": None}
    hot_r = flt.keyword_score(hot, "research", kw)
    cold_r = flt.keyword_score(cold, "research", kw)
    assert hot_r["score"] > cold_r["score"]
    assert hot_r["tag"] != "none"
    assert hot_r["visa"] is None  # research carries no visa


def test_clamp_result_validates_tag_visa_score():
    r = flt._clamp_result({"id": "x", "score": 99, "tag": "bogus", "visa": "maybe",
                           "why": "y"}, "jobs", "haiku")
    assert r["score"] == 10 and r["tag"] == "none" and r["visa"] == "unknown"


# --- ATS parsing -----------------------------------------------------------

def test_ats_greenhouse_parse():
    sess = FakeSession(FakeResp(json.loads(load_fixture("greenhouse.json"))))
    org = {"name": "Acme", "ats": "greenhouse", "slug": "acme", "tier": 1}
    items = ats.collect_org(sess, org)
    assert len(items) == 2
    it = items[0]
    assert it["_id_key"] == "greenhouse:acme:4012345"
    assert it["track"] == "jobs" and it["tier"] == 1
    assert it["location"] == "Oakland, CA"
    assert "<p>" not in it["summary"]  # html stripped


def test_ats_lever_and_ashby_parse():
    lever = ats.collect_org(FakeSession(FakeResp(json.loads(load_fixture("lever.json")))),
                            {"name": "L", "ats": "lever", "slug": "l"})
    assert lever[0]["_id_key"] == "lever:l:e1a2b3c4"
    assert lever[0]["published_at"]  # ms epoch -> date
    ashby = ats.collect_org(FakeSession(FakeResp(json.loads(load_fixture("ashby.json")))),
                            {"name": "A", "ats": "ashby", "slug": "a"})
    assert ashby[0]["_id_key"] == "ashby:a:z9y8x7"
    assert ashby[0]["title"].startswith("Program Officer")


def test_ats_workable_and_breezy_parse():
    wk = ats.collect_org(FakeSession(FakeResp(json.loads(load_fixture("workable.json")))),
                         {"name": "CW", "ats": "workable", "slug": "489687"})
    assert wk[0]["_id_key"] == "workable:489687:869818CC50"
    assert "remote" in wk[0]["location"]           # telecommuting flag folded in
    assert wk[0]["title"].startswith("Program Manager")

    bz = ats.collect_org(FakeSession(FakeResp(json.loads(load_fixture("breezy.json")))),
                         {"name": "CATF", "ats": "breezy", "slug": "clean-air-task-force"})
    assert bz[0]["_id_key"] == "breezy:clean-air-task-force:c84ff844edb5"
    assert "<p>" not in bz[0]["summary"]            # html stripped
    assert bz[0]["published_at"] == "2026-06-22"


# --- pagewatch diff --------------------------------------------------------

def test_pagewatch_seeds_then_detects_change():
    conn = store.connect(":memory:")
    v1, v2 = load_fixture("page_v1.html"), load_fixture("page_v2.html")
    prog = [{"name": "Prog", "url": "https://x/careers", "tier": 0}]

    items, _ = pagewatch.collect(FakeSession(FakeResp(text=v1)), conn, prog)
    assert items == []  # first sight seeds silently

    items, _ = pagewatch.collect(FakeSession(FakeResp(text=v1)), conn, prog)
    assert items == []  # unchanged

    items, _ = pagewatch.collect(FakeSession(FakeResp(text=v2)), conn, prog)
    assert len(items) == 1 and items[0]["tier"] == 0
    assert "changed" in items[0]["title"]


# --- JSON robustness -------------------------------------------------------

def test_sweep_extracts_json_from_fences_and_prose():
    text = "Here you go:\n```json\n[{\"title\":\"X\",\"url\":\"https://x\"}]\n```\nDone."
    got = sweep._extract_json_array(text)
    assert got and got[0]["url"] == "https://x"
    assert sweep._extract_json_array("no json here") == []


# --- dashboard privacy (no jobs) -------------------------------------------

def test_dashboard_assert_rejects_jobs_row():
    with pytest.raises(AssertionError):
        render._assert_no_jobs([{"track": "jobs"}])


def test_dashboard_excludes_jobs_rows(tmp_path):
    conn = store.connect(":memory:")
    store.upsert_items(conn, [
        {"track": "research", "title": "PLN grid study", "url": "https://r/1",
         "source": "arxiv", "source_type": "paper", "published_at": "2026-07-10"},
        {"track": "jobs", "title": "SECRET Program Officer role", "url": "https://j/1",
         "source": "Acme", "source_type": "ats", "tier": 1, "_id_key": "greenhouse:acme:1",
         "published_at": "2026-07-10"},
    ])
    store.apply_score(conn, store.compute_id({"_id_key": "greenhouse:acme:1"}),
                      9, "philanthropy", "why", "silent", "keyword")
    rid = store.compute_id({"track": "research", "url": "https://r/1"})
    store.apply_score(conn, rid, 9, "ch3-garuda", "why", None, "keyword")

    out = render.render(conn, sample_cfg(), str(tmp_path / "index.html"))
    html = open(out, encoding="utf-8").read()
    assert "PLN grid study" in html
    assert "SECRET" not in html          # no jobs leak
    assert "Program Officer" not in html


# --- digest / alert guards -------------------------------------------------

def test_digest_once_per_utc_day():
    conn = store.connect(":memory:")
    assert digest.is_due(conn, send_hour_utc=0)      # nothing sent yet
    store.set_meta(conn, "last_digest_sent_at", store.now_iso())
    assert not digest.is_due(conn, send_hour_utc=0)  # already sent today
    assert digest.is_due(conn, send_hour_utc=0, force=True)


def test_digest_skips_when_empty():
    conn = store.connect(":memory:")
    assert digest.build(conn, sample_cfg()) is None


def test_alert_first_run_suppresses(monkeypatch):
    conn = store.connect(":memory:")
    store.upsert_items(conn, [
        {"track": "jobs", "title": "Analyst", "url": "https://j/1", "source": "Acme",
         "source_type": "ats", "tier": 1, "_id_key": "greenhouse:acme:1"}])
    jid = store.compute_id({"_id_key": "greenhouse:acme:1"})
    store.apply_score(conn, jid, 9, "philanthropy", "why", "silent", "keyword")

    res = alert.run(conn, sample_cfg(), first_run=True)
    assert res["sent"] == 0
    row = conn.execute("SELECT alerted_at FROM items WHERE id=?", (jid,)).fetchone()
    assert row["alerted_at"] is not None  # suppressed but marked
