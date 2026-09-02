#!/usr/bin/env python3
"""
build_plays_feed.py — recent logged plays and how they graded, for the tab.

Both "today's plays" panels could only ever show tonight. To see whether
yesterday's card actually won you had to run the ledger report in a terminal,
which is the same gap that let the 8/30 card sit ungraded for two days.

So the daily run publishes the last DAYS days of ledger entries in the shape
the panels already render: one row per logged play, with its result and the
profit computed from its own stored price. The tab adds a date selector and
reads this for anything that is not tonight.

Deliberately a projection of the ledger, not a second copy of it. Nothing here
is computed -- results and profit come straight from scout-card-log.json,
which the auto-grader settles from the finals. If the two ever disagree the
ledger is right and this file is stale.

Groups follow scripts/rule_status.py: "scout" for the mismatch-model rules,
"non-scout" for the systems read off mlb-all-ml.json. Each panel filters to
its own group, so the same feed serves both.

Output: MLBstrikeouts/data/plays-feed.json (+ the dashboard copy)

Usage:  cd MLBstrikeouts && python -m scripts.build_plays_feed
"""
import collections
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from rule_status import RULES, RULE_GROUP

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data",
                                       "scout-card-log.json"))
OUTPUT_PATHS = [
    os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "plays-feed.json")),
    os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "PythonDashboard",
                                  "data", "plays-feed.json")),
]

DAYS = 30          # how far back the selector can reach
FIELDS = ("date", "play", "market", "line", "price", "rule", "result",
          "profit", "game", "gamePk", "commence", "basis", "combo")


def display_name(rule):
    """The rule's canonical name, or the raw key for a retired one."""
    r = RULES.get(rule)
    return r[1] if r else rule


def main():
    with open(LEDGER, encoding="utf-8") as fh:
        blob = json.load(fh)
    entries = [e for e in blob.get("entries", []) if not e.get("no_play")]
    dates = sorted({e["date"] for e in entries})[-DAYS:]
    keep = set(dates)

    by_date = collections.defaultdict(list)
    for e in entries:
        if e["date"] not in keep:
            continue
        row = {k: e.get(k) for k in FIELDS if e.get(k) is not None}
        row["rule"] = e.get("rule")
        row["name"] = display_name(e.get("rule"))
        row["group"] = RULE_GROUP.get(e.get("rule"), "scout")
        for flag in ("shadow", "not_bet", "backfilled", "auto", "non_scout"):
            if e.get(flag):
                row[flag] = True
        by_date[e["date"]].append(row)

    def _wl(rows):
        g = [r for r in rows if r.get("result") in ("WIN", "LOSS", "PUSH")]
        return (sum(1 for r in g if r["result"] == "WIN"),
                sum(1 for r in g if r["result"] == "LOSS"),
                sum(1 for r in g if r["result"] == "PUSH"))

    def tally(rows):
        """The day's record, with backfilled rows held out of it entirely.

        Backfilled plays were replayed after the fact and never wagered, so
        counting them would report both a record and money that never
        happened -- the ledger report excludes them for the same reason. They
        get their own sub-tally so the tab can show them under their own
        heading. `n` counts every row, backfilled included, because it is what
        decides whether there is anything to render at all.
        """
        live = [r for r in rows if not r.get("backfilled")]
        back = [r for r in rows if r.get("backfilled")]
        w, l, p = _wl(live)
        bw, bl, bp = _wl(back)
        # Units follow the ledger's own convention: shadow and not-bet rows
        # stay in the W-L and out of the money.
        u = sum(r.get("profit") or 0 for r in live
                if not r.get("not_bet") and not r.get("shadow"))
        return {"w": w, "l": l, "push": p,
                "pending": sum(1 for r in live if r.get("result") == "pending"),
                "n": len(rows), "live_n": len(live), "units": round(u, 2),
                "backfilled": {"w": bw, "l": bl, "push": bp, "n": len(back)}}

    days = []
    for d in sorted(dates, reverse=True):
        rows = sorted(by_date[d], key=lambda r: (str(r.get("commence") or "~"),
                                                 r.get("name") or "",
                                                 r.get("play") or ""))
        days.append({
            "date": d,
            "rows": rows,
            "all": tally(rows),
            "scout": tally([r for r in rows if r["group"] == "scout"]),
            "non_scout": tally([r for r in rows if r["group"] == "non-scout"]),
        })

    out = {
        "sport": "MLB",
        "type": "plays-feed",
        "generated": datetime.datetime.now(datetime.timezone.utc)
                             .isoformat(timespec="seconds").replace("+00:00", "Z"),
        "days_kept": DAYS,
        "note": ("A projection of MLBstrikeouts/data/scout-card-log.json for "
                 "the dashboard's date selector. Results and profit are the "
                 "ledger's own, settled by scripts/grade_scout_ledger.py from "
                 "the finals; nothing is recomputed here. Units exclude shadow "
                 "and not-bet rows, which stay in the W-L. Backfilled rows "
                 "(replayed after the fact, never wagered) are held out of the "
                 "record entirely and tallied separately."),
        "days": days,
    }
    for path in OUTPUT_PATHS:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(out, fh, indent=1)
    graded = sum(d["all"]["w"] + d["all"]["l"] for d in days)
    print(f"plays feed: {len(days)} days, {sum(len(d['rows']) for d in days)} "
          f"rows ({graded} graded) -> {len(OUTPUT_PATHS)} paths")


if __name__ == "__main__":
    main()
