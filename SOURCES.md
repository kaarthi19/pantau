# SOURCES — Phase 0 verification log

What was checked, kept, and dropped, with dates. Update this whenever you add or
retire a source.

## Verification run — 2026-07-12 (Claude Code, via `curl`)

### Research APIs — VERIFIED
| API | Check | Result |
|-----|-------|--------|
| OpenAlex works (`title.search`) | live GET | 200, ~301k results |
| OpenAlex ISSN filter (`primary_location.source.issn:0301-4215`) | live GET | 200, ~17k — **syntax confirmed** |
| arXiv Atom (`export.arxiv.org/api/query`, https) | live GET | 200, valid Atom |
| Google News RSS search | live GET | 200, ~100 items |

Other ISSNs in `sources.yaml` (0306-2619 Applied Energy, 0140-9883 Energy
Economics, 0885-8950 IEEE TPWRS) are plausible but **not yet ISSN-verified** —
tagged VERIFY.

### Research org feeds — VERIFIED (return XML with entries)
| Source | Feed | Note |
|--------|------|------|
| IESR | `https://iesr.or.id/feed` | ~9 items |
| Carbon Brief | `https://www.carbonbrief.org/feed/` | needs browser UA (set in `net.py`) |
| Mongabay | `https://news.mongabay.com/feed/` | needs browser UA |
| Mongabay Indonesia | `https://www.mongabay.co.id/feed/` | Indonesian-language |
| ADB | `https://www.adb.org/rss.xml` | use `/rss.xml`, not `/news/rss` |
| Zero Carbon Analytics | `https://zerocarbon-analytics.org/feed/` | ~10 items |

### Research feeds — DROPPED / DEFERRED
| Source | Reason |
|--------|--------|
| Ember | Cloudflare JS challenge (403) — needs headless fetch |
| IEEFA | Cloudflare JS challenge (403) |
| TransitionZero | 429 bot block |
| Global Energy Monitor | no discoverable RSS feed |
| Reccessary | no discoverable RSS feed |

These are commented out in `sources.yaml`; revisit with a Cloudflare-aware
fetcher (Phase 2).

### Jobs Tier-1 ATS — VERIFIED (public Greenhouse board API)
| Org | Slug | Note |
|-----|------|------|
| Energy Innovation | `energyinnovation` | live postings |
| Bezos Earth Fund | `bezosearthfund` | ~15 postings |
| Quadrature Climate Foundation | `qcf` | EU GH cluster; boards-api slug works |
| Breakthrough Energy | `breakthroughenergy` | live postings |
| Elemental Excelerator | `elementalimpact` | note the slug |
| World Resources Institute | `wri` | 200 but 0 postings at check time |

### Jobs Tier-1 — resolved by careers-page inspection (2026-07-12)
A first pass brute-forced name-slugs and failed. Second pass fetched each org's
actual careers page and extracted the ATS token from the embed/iframe/JS, then
hit the live API. Outcomes:

| Org | ATS found | Resolution |
|-----|-----------|------------|
| GEAPP | Greenhouse | slug `globalenergyallianceforpeopleandplanetgeappllc` — **6 postings** (careers page is `/jobs/`, not `/careers/`) |
| ClimateWorks | Workable (legacy `whr`) | numeric account `489687` via `apply.workable.com/api/v1/widget/accounts/489687` — **3 postings** (added a Workable fetcher) |
| Clean Air Task Force | Breezy | `clean-air-task-force.breezy.hr/json` — **5 postings** (added a Breezy fetcher) |
| RMI | Workday (`rockymountain.wd1`) | no clean public GET → pagewatch `/about/careers/` |
| Climate Policy Initiative | none (WordPress) | pagewatch |
| Hewlett | none (WordPress) | pagewatch |
| Packard | Cornerstone (`packard.csod.com`) | pagewatch |
| Sequoia Climate | none (links to LinkedIn Jobs) | pagewatch |
| Growald | JazzHR (shared consultant board) | pagewatch `growaldclimatefund.org/careers` |
| Energy Foundation | Paylocity | pagewatch |

All 10 are now wired in `orgs.yaml` (3 polled via ATS API, 7 via pagewatch — all
careers URLs confirmed reachable). Two new fetchers (`workable`, `breezy`) added
to `ats.py` with fixtures + tests.

**LBNL** resolved too: real portal is `jobs.lbl.gov` (Taleo) — `eta.lbl.gov`
403s and there is no clean public JSON. Watch URL set to
`jobs.lbl.gov/jobs/search?keyword=energy`, but the Taleo results are JS-rendered
(identical HTML for any keyword), so pagewatch is **best-effort** — it will only
catch shell changes, not new postings. Recommend a LinkedIn/Taleo saved-search
email alert for reliable LBNL coverage (feeds the Phase-2 IMAP module).

### Test fixtures (guaranteed-live boards, for reference)
Greenhouse: `anthropic`, `stripe`, `gitlab`. Lever: `gopuff`, `palantir`,
`spotify`. Ashby: `ramp`, `notion`, `openai`. Used to sanity-check the fetchers
against real boards; the repo's unit tests use static fixtures under
`tests/fixtures/`.

## Owner still to do (Phase 0 inputs — cannot be coded)
1. Verify Tier-0 program dates + citizenship eligibility in `programs.yaml`.
2. Verify the remaining journal ISSNs (only 0301-4215 confirmed).
3. Add any real Tier-2 aggregator feeds under `jobs.aggregators`.
4. Set a LinkedIn/Taleo email alert for LBNL (pagewatch is best-effort there),
   and sanity-check the other pagewatch careers pages produce useful diffs
   (JS-heavy pages like RMI's Workday may render little in raw HTML).
