"""SQLite storage: schema, idempotent upsert with dedupe, run log, meta kv.

Dedupe strategy (v2):
  * Primary id  = sha1 of a stable seed:
      - doi                          -> "doi:<lower doi>"
      - ATS job                      -> "<platform>:<slug>:<job_id>"  (set by ats.py)
      - pagewatch / sweep / rss      -> collector-supplied ``_id_key``
      - fallback                     -> "url:<normalized url>"
  * Secondary guard (jobs only): (org, normalized_title, location) — survives an
    org migrating boards, so the same posting under a new ATS id is not re-alerted.

Scores are write-once; an item that already has a score is never re-scored, and
an item with ``alerted_at`` set is never re-alerted.
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone

from .net import normalize_url, normalize_title

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id             TEXT PRIMARY KEY,
  track          TEXT, source TEXT, source_type TEXT,
  title          TEXT, url TEXT, doi TEXT,
  org            TEXT, location TEXT, deadline TEXT, tier INTEGER,
  published_at   TEXT, fetched_at TEXT,
  summary        TEXT,
  prefilter_hits INTEGER,
  score          INTEGER, tag TEXT, rationale TEXT, visa TEXT,
  scored_at      TEXT, scorer TEXT,
  alerted_at     TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  run_at TEXT, source TEXT, fetched INTEGER, new INTEGER, ok INTEGER, note TEXT
);
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY, value TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_track_score ON items(track, score);
CREATE INDEX IF NOT EXISTS idx_items_published ON items(published_at);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def _sha1(seed: str) -> str:
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()


def compute_id(item: dict) -> str:
    seed = item.get("_id_key")
    if not seed:
        if item.get("doi"):
            seed = "doi:" + str(item["doi"]).strip().lower()
        else:
            seed = "url:" + normalize_url(item.get("url", ""))
    return _sha1(seed)


# --- meta kv ---------------------------------------------------------------

def get_meta(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    conn.commit()


# --- upsert ----------------------------------------------------------------

_COLUMNS = (
    "id", "track", "source", "source_type", "title", "url", "doi", "org",
    "location", "deadline", "tier", "published_at", "fetched_at", "summary",
    "prefilter_hits", "score", "tag", "rationale", "visa", "scored_at",
    "scorer", "alerted_at",
)


def _jobs_dupe_exists(conn: sqlite3.Connection, item: dict) -> bool:
    """Near-dupe guard for jobs: same org + normalized title + location."""
    org = (item.get("org") or "").strip().lower()
    if not org:
        return False
    ntitle = normalize_title(item.get("title", ""))
    loc = (item.get("location") or "").strip().lower()
    rows = conn.execute(
        "SELECT title, location FROM items WHERE track='jobs' AND lower(org)=?",
        (org,),
    ).fetchall()
    for r in rows:
        if normalize_title(r["title"] or "") == ntitle and \
                (r["location"] or "").strip().lower() == loc:
            return True
    return False


def upsert_items(conn: sqlite3.Connection, items: list[dict]) -> int:
    """Insert unseen items. Returns count of genuinely new rows.

    Existing rows are left untouched (scores/alerts are write-once).
    """
    new = 0
    fetched_at = now_iso()
    for item in items:
        item = dict(item)
        item["id"] = compute_id(item)
        exists = conn.execute(
            "SELECT 1 FROM items WHERE id=?", (item["id"],)
        ).fetchone()
        if exists:
            continue
        if item.get("track") == "jobs" and _jobs_dupe_exists(conn, item):
            continue
        item.setdefault("fetched_at", fetched_at)
        row = {c: item.get(c) for c in _COLUMNS}
        placeholders = ", ".join("?" for _ in _COLUMNS)
        conn.execute(
            f"INSERT INTO items ({', '.join(_COLUMNS)}) VALUES ({placeholders})",
            tuple(row[c] for c in _COLUMNS),
        )
        new += 1
    conn.commit()
    return new


def record_run(conn, source: str, fetched: int, new: int, ok: bool, note: str = "") -> None:
    conn.execute(
        "INSERT INTO runs(run_at, source, fetched, new, ok, note) VALUES(?,?,?,?,?,?)",
        (now_iso(), source, fetched, new, 1 if ok else 0, note),
    )
    conn.commit()


# --- queries used by filter / render / digest ------------------------------

def unscored(conn, track: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM items WHERE track=? AND score IS NULL", (track,)
    ).fetchall()


def apply_score(conn, item_id: str, score: int, tag: str, rationale: str,
                visa: str, scorer: str, prefilter_hits: int | None = None) -> None:
    """Write a score once. No-op if the row already carries one."""
    fields = "score=?, tag=?, rationale=?, visa=?, scored_at=?, scorer=?"
    args = [score, tag, rationale, visa, now_iso(), scorer]
    if prefilter_hits is not None:
        fields += ", prefilter_hits=?"
        args.append(prefilter_hits)
    args.append(item_id)
    conn.execute(
        f"UPDATE items SET {fields} WHERE id=? AND score IS NULL", tuple(args)
    )
    conn.commit()


def mark_alerted(conn, item_id: str) -> None:
    conn.execute(
        "UPDATE items SET alerted_at=? WHERE id=?", (now_iso(), item_id)
    )
    conn.commit()


def recent_source_failures(conn, streak: int = 3) -> list[str]:
    """Sources whose last ``streak`` runs all failed — surfaced in the digest."""
    sources = [r["source"] for r in conn.execute(
        "SELECT DISTINCT source FROM runs").fetchall()]
    flagged = []
    for src in sources:
        rows = conn.execute(
            "SELECT ok FROM runs WHERE source=? ORDER BY run_at DESC LIMIT ?",
            (src, streak),
        ).fetchall()
        if len(rows) >= streak and all(r["ok"] == 0 for r in rows):
            flagged.append(src)
    return flagged


# --- maintenance -----------------------------------------------------------

def prune(conn, threshold: int, keep_days: int) -> int:
    """Delete low-value rows older than keep_days. Returns rows removed."""
    cutoff = _days_ago_iso(keep_days)
    cur = conn.execute(
        "DELETE FROM items WHERE (score IS NULL OR score < ?) "
        "AND COALESCE(published_at, fetched_at) < ?",
        (threshold, cutoff),
    )
    conn.commit()
    return cur.rowcount


def _days_ago_iso(days: int) -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def vacuum(conn) -> None:
    conn.execute("VACUUM")
    conn.commit()
