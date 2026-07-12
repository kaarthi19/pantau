"""Generate calendar/pantau.ics from Tier-0 cycle programs (run once, commit).

Zero runtime code: the pipeline never touches this. Each cycle program gets a
VEVENT on the 1st of its cycle month with a 14-day-prior reminder (VALARM).
Dates are approximate cycle anchors — refine as Phase 0-J verifies real dates.

    python scripts/make_ics.py
"""
import os
from datetime import datetime, timezone

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _next_occurrence(month: int, now: datetime) -> str:
    year = now.year if month >= now.month else now.year + 1
    return f"{year}{month:02d}01"


def build_ics(programs: list[dict], now: datetime) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//pantau//tier0//EN",
             "CALSCALE:GREGORIAN"]
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    for i, prog in enumerate(programs):
        month = prog.get("cycle_month")
        if not prog.get("ics") or not month:
            continue
        date = _next_occurrence(int(month), now)
        uid = f"pantau-{i}-{date}@pantau"
        summary = f"{prog['name']} — cycle window opens"
        note = prog.get("note", "").replace("\n", " ")
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{date}",
            f"SUMMARY:{summary}",
            f"DESCRIPTION:{note}",
            "BEGIN:VALARM",
            "TRIGGER:-P14D",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{prog['name']} deadline approaching (~14 days)",
            "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def main():
    with open(os.path.join(ROOT, "registry", "programs.yaml"), encoding="utf-8") as f:
        programs = (yaml.safe_load(f) or {}).get("tier0", [])
    ics = build_ics(programs, datetime.now(timezone.utc))
    out = os.path.join(ROOT, "calendar", "pantau.ics")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(ics)
    print(f"wrote {out} ({ics.count('BEGIN:VEVENT')} events)")


if __name__ == "__main__":
    main()
