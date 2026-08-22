#!/usr/bin/env python3
"""
MLBstrikeouts/scripts/build_slate_scout.py
Emit today's slate scouting payload for the dashboard's "MLB Slate Scout" tab.

Wraps ``slate_wrc_form.build_report`` and writes ``mlb-slate-scout.json`` to
both data directories, the same two-copy pattern build_all_ml.py and
build_team_woba_splits.py use.

This is a SCOUTING view, not a model. Nothing here is a pick, and nothing
downstream reads it — the payload carries an explicit ``advisory`` flag so the
tab can say so on its face. The mismatch score it ranks by has real
baseball signal and no market edge (it correlates +0.079 with runs scored but
+0.331 with the market total, leaving +0.001 against the closing number), so
the tab exists to show WHERE the mismatches and the data problems are, not to
tell anyone what to bet.

Failure is non-fatal by design: any error building the report leaves the
previous payload in place rather than writing a broken one, matching the
fail-open convention used elsewhere in the daily run.

Usage:
    cd MLBstrikeouts
    python -m scripts.build_slate_scout
    python -m scripts.build_slate_scout --date 2026-08-16
"""
import argparse
import datetime
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import slate_wrc_form as S  # noqa: E402

OUTPUT_PATHS = [
    os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "mlb-slate-scout.json")),
    os.path.normpath(os.path.join(
        SCRIPT_DIR, "..", "..", "PythonDashboard", "data", "mlb-slate-scout.json")),
]

# Shown on the tab so the view carries its own caveats rather than relying on
# anyone remembering them.
NOTES = [
    "Scouting only — the mismatch ranking is NOT a betting signal. "
    "Backtested over 1818 games it returns -1.8% ROI; the information is "
    "already in the closing total.",
    "Flags are the useful part: a starter back from a layoff still shows a "
    "full last-5-start line describing a different month.",
    "Swingmen carry a second form line. The last-5-START rates skip relief "
    "work entirely, so a bullpen stint reads as a collapse until you see the "
    "relief innings beside it.",
    "Each side carries its own team's bullpen windows (L30/L20/L15/L7 "
    "calendar days, rank = league ERA position that window). Totals lean on "
    "3-4 relief innings a night; a short starter in front of a leaking pen "
    "and a workhorse in front of a locked-down one are different bets at the "
    "same number. Advisory like everything else here.",
]


def build(date_iso):
    report = S.build_report(date_iso)
    for game in report.get("slate", []):
        for side in ("away", "home"):
            s = game["sides"].get(side) or {}
            form = s.get("form") or {}
            # Trim the per-start detail the tab does not render, keeping the
            # payload small enough to fetch on every tab switch. recent_all
            # and relief survive the trim: for a swingman they are the only
            # honest recent-form numbers on the card.
            s["form"] = {k: form.get(k) for k in
                         ("season", "recent", "hot", "recent_all", "relief")}
    report["generated"] = (datetime.datetime.now(datetime.timezone.utc)
                           .isoformat(timespec="seconds").replace("+00:00", "Z"))
    report["sport"] = "MLB"
    report["type"] = "slate-scout"
    report["advisory"] = True
    report["notes"] = NOTES
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    args = ap.parse_args()

    try:
        payload = build(args.date)
    except Exception as exc:  # fail open — keep whatever is already published
        print(f"slate scout build failed ({type(exc).__name__}: {exc}) — "
              f"leaving the existing payload in place")
        return 0

    if not payload.get("slate"):
        print(f"no games for {args.date} — leaving the existing payload in place")
        return 0

    for path in OUTPUT_PATHS:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(payload, fh, indent=2)
    print(f"slate scout: {payload['games']} games for {payload['date']} "
          f"-> {len(OUTPUT_PATHS)} paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
