#!/usr/bin/env python3
"""
backfill_conflict_skips.py — apply the conflict policy to the WHOLE season.

ONE-SHOT, and it REWRITES HISTORY. Read that twice before running it again.

The conflict rule -- when two carded rules land on opposite sides of a game's
total, no side is taken -- reached its current shape in three steps on
2026-09-03: the over had been passed since 9/2, both straight totals were
added, then the parlay. The last two were dated forward so the ledger would
keep the bets that were actually made.

The user asked for the whole season instead (2026-09-03): one policy, one
record, no date seam in the middle of it. This script is what made the
history match, and CONFLICT_PARLAY_FROM was deleted the same day.

WHAT IT DOES. Walks every ledger row, groups carded non-shadow rows that have
a side of the total (a parlay's second leg counts as an under), and where a
game carries BOTH an over and an under marks every one of them not_bet +
conflict_skip. Rows keep their real result -- they stay in the rule's W-L,
which is what the replay measures -- and come out of the units, which is what
was actually risked. Not PUSH: these games had results.

This DELIBERATELY ignores _locked. The daily path must never touch a settled
row; this one exists to do exactly that, once, on the user's instruction.

Detection spans every carded rule with a total side, scout and non-scout
alike, which is what the daily logger already does -- flag-plays unders
conflict with starter-over-run overs the same way pickem-under does.

Idempotent: a row already carrying the note is left alone, so a second run
reports nothing and changes nothing.

Usage:  cd MLBstrikeouts && python -m scripts.backfill_conflict_skips [--apply]
"""
import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "sources"))

import scout_card_log as LEDGER_IO
from log_daily_qualifiers import NOTE, total_side
from rule_status import RULE_STATUS

# The note the over-only policy wrote, 2026-09-02 to 09-03. Stripped rather
# than left in place: a row that says "over not taken" AND "neither side
# taken" reads as two policies arguing on one line.
OLD_NOTE = "Conflicting under carded on this game; over not taken." 

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.normpath(os.path.join(HERE, "..", "data",
                                       "scout-card-log.json"))


def conflicted(entries):
    """Rows on games where two carded rules disagree about the total."""
    by_game = collections.defaultdict(list)
    for e in entries:
        if (total_side(e)
                and RULE_STATUS.get(e.get("rule")) == "card"
                and not e.get("shadow")):
            by_game[(e.get("date"), e.get("gamePk") or e.get("game"))].append(e)
    out = []
    for rows in by_game.values():
        if {"over", "under"} <= {total_side(e) for e in rows}:
            out.extend(rows)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write the ledger (default is a dry run)")
    args = ap.parse_args()

    with open(LEDGER, encoding="utf-8") as fh:
        blob = json.load(fh)
    entries = blob["entries"] if isinstance(blob, dict) else blob

    changed, units = [], 0.0
    for e in conflicted(entries):
        if e.get("conflict_skip") and NOTE in (e.get("basis") or ""):
            continue
        if not e.get("not_bet") and not e.get("backfilled"):
            units += e.get("profit") or 0.0
        e["not_bet"] = True
        e["conflict_skip"] = True
        basis = (e.get("basis") or "").replace(OLD_NOTE, "").strip()
        if NOTE not in basis:
            basis = (basis + " " + NOTE).strip()
        e["basis"] = basis
        changed.append(e)

    by_rule = collections.Counter((e["rule"], e.get("market")) for e in changed)
    for (rule, market), n in sorted(by_rule.items()):
        print(f"  {rule:24}{market:8}{n:>4} rows")
    print(f"\n  {len(changed)} rows marked; "
          f"{units:+.2f}u removed from the live record "
          f"(backfilled rows carry none either way)")

    if args.apply:
        LEDGER_IO._save(blob)          # same writer the ledger always uses
        print(f"  written -> {LEDGER}")
    else:
        print("  (dry run -- nothing written; pass --apply)")


if __name__ == "__main__":
    main()
