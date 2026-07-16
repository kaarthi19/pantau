# Pantau

A single, low-maintenance pipeline that watches two things for one researcher:

- **Research track** — papers, preprints, news, and org reports on Southeast-Asia
  power-sector coordination. Surfaced on a phone-first **public dashboard** and in
  a daily email.
- **Jobs track** — climate/energy fellowships, funder/intermediary roles, and MDB
  programs. Surfaced **only by email** (immediate alerts + the daily digest).

One engine, two tracks: `collect → store → filter (keyword prefilter → Haiku
scoring) → surface`. Runs on GitHub Actions every 4 hours. State lives in SQLite,
committed back to the repo by the bot.

> **Privacy split.** GitHub Pages is publicly reachable even from a private repo,
> so the dashboard renders the **research track only**. Jobs data never touches
> `docs/`. A render-time assertion and a unit test enforce this.

## Setup

1. **Create the repo private, push, enable Actions.** It contains a personal
   career profile and a relationship-annotated org registry — keep it private.
2. **Enable Pages:** Settings → Pages → Source = `main` / `docs/`. Note the URL —
   it is **public**, which is why it carries research only. (Pages on a private
   repo needs GitHub Pro; you have it via GitHub Education.)
3. **Add secrets** (Settings → Secrets → Actions). Everything degrades cleanly
   when absent:
   - `ANTHROPIC_API_KEY` — optional day 1. Without it the pipeline runs in
     **keyword mode** ($0). Add it to switch to Haiku scoring + the monthly sweep.
   - `DIGEST_SMTP_USER` / `DIGEST_SMTP_PASS` — a Gmail address + **app password**
     (the account needs 2FA). Powers both the alert and digest emails. Without
     them, email is skipped and the dashboard still updates.
4. **Set the recipient:** edit `digest.recipient` in `config.yaml`.
5. **Import the calendar:** run `make ics`, commit `calendar/pantau.ics`, and
   import it into Google Calendar once (Tier-0 program cycles + 14-day reminders).

## Local dev

```bash
make install      # pip install -r requirements.txt
make dry-run      # collect + per-source counts, no writes
make run          # full pipeline in keyword mode
make sweep        # force the monthly agentic fellowship sweep (needs API key)
make test         # offline unit tests
open docs/index.html
```

`ANTHROPIC_API_KEY` unset ⇒ keyword mode automatically. SMTP secrets unset ⇒
email skipped. The whole system is buildable and testable with no keys.

## Tuning (all in YAML, no code)

- **Thresholds** — `config.yaml → tracks.{research,jobs}.{show,highlight,alert}_threshold`.
- **Add a Tier-1 org** — one stanza in `registry/orgs.yaml`
  (`{name, ats, slug, always_alert}`). Only rows with a real `ats`+`slug` are
  polled; `ats: VERIFY` rows are documented intent, skipped until filled.
- **Add a research source** — a Google News query, journal ISSN, arXiv term, or
  org RSS feed in `registry/sources.yaml`.
- **Edit a rubric** — `prompts/research.md` or `prompts/jobs.md` (candidate
  profile + scoring rubric + few-shots). Used verbatim as the scorer system
  prompt.
- **Keyword lists** — `registry/sources.yaml → keywords` drive both the prefilter
  and the $0 keyword scorer.

## How it behaves

- **Tiering.** Tier-0 = fellowships/fixed-cycle programs (calendar + page-diff +
  monthly sweep). Tier-1 = watched orgs polled via ATS; new postings **bypass the
  prefilter** and can trigger a same-run alert email. Tier-2 = aggregator feeds,
  digest-only.
- **Alerts.** Unalerted jobs item, tier ∈ {0,1}, score ≥ `alert_threshold` → one
  email in the same run, capped by `alerts.max_per_run`. The **first ever run
  suppresses alerts** (marks them) so a backfill can't flood you — the first
  digest carries everything.
- **Digest.** Once per UTC day, first run ≥ `send_hour_utc`. Jobs block on top
  (Tier-0/1 always, then Tier-2 ≥ threshold, each with deadline + visa flag),
  research grouped by workstream below. Zero items ⇒ skipped.
- **Resilience.** A failing source logs and continues. A Tier-1 source failing 3
  consecutive runs gets one line in the digest/dashboard footer
  (`check {org} board`) rather than dying silently.
- **Library seeding (opt-in, research track).** Point `config.yaml → library` at
  your Zotero library and once a week the radar surfaces recent papers that
  **cite your library** or are **by the authors you read most** — a *From your
  library · top N* section in the digest. Use `source: zotero_api` (fetches from
  zotero.org via a read-only key in the `ZOTERO_API_KEY` secret + your numeric
  `zotero_library_id`) or `source: bib` (a local `library/zotero.bib`). Only DOIs
  are sent to OpenAlex; the library/`.bib` is git-ignored. These items are
  **email-only** — they never render on the public dashboard (they reveal your
  reading focus).
- **Cost.** ~$3–5/month with Haiku scoring; $0 in keyword mode. OpenAlex/arXiv/
  Google News/RSS/**Zotero** are all free; only Claude scoring uses paid API credits.

## Layout

```
pantau/            engine (run.py orchestrator, collectors/, store, filter, render, alert, digest)
registry/          Phase-0 curation: sources.yaml, orgs.yaml, programs.yaml
prompts/           research.md, jobs.md — scorer system prompts
config.yaml        thresholds, models, digest/alert/sweep config
docs/index.html    public dashboard (research only) — served by Pages
data/pantau.db     SQLite state — committed by the bot each run
tests/             offline fixture-driven tests
scripts/make_ics.py  one-off Tier-0 calendar generator
```

Scheduled workflows pause after ~60 days of repo inactivity; the bot's state
commits each run keep it alive. See `SOURCES.md` for the source verification log.
