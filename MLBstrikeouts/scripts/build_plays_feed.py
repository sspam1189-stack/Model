#!/usr/bin/env python3
"""
build_plays_feed.py — the scout bet log: every logged play and how it graded.

The tab could only ever show tonight. To see whether yesterday's card won you
had to run the ledger report in a terminal, which is the same gap that let the
8/30 card sit ungraded for two days.

A date selector was tried first and was the wrong shape: it answered "what
happened on one day" when the question is nearly always "how is this rule
doing". So this publishes the WHOLE ledger as a flat bet log instead, in the
shape the fade-ML and props tabs already use -- one row per logged play,
newest first, filterable by rule, status, result and date, with the record for
the current filter in the header.

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

FIELDS = ("date", "play", "market", "line", "price", "rule", "result",
          "profit", "game", "gamePk", "commence", "basis", "combo")


# Rules retired before the 2026-09-01 naming migration, which never had an
# entry in rule_status. Their rows are still in the ledger, so the bet log's
# Rule dropdown would otherwise list raw keys next to proper names.
LEGACY_NAMES = {
    "scout-ml-both-halves-aligned": "Aligned ML (old name)",
    "card-grade-total-both-aligned": "Both-aligned total (retired)",
    "card-grade-total-aligned-plus-flag": "Aligned + flag total (retired)",
    "flag-plays-legacy": "Flag Plays (old name)",
}


def display_name(rule):
    """The rule's canonical name, its legacy label, or the raw key."""
    r = RULES.get(rule)
    return r[1] if r else LEGACY_NAMES.get(rule, rule)


def main():
    with open(LEDGER, encoding="utf-8") as fh:
        blob = json.load(fh)
    entries = [e for e in blob.get("entries", []) if not e.get("no_play")]

    bets = []
    for e in entries:
        row = {k: e.get(k) for k in FIELDS if e.get(k) is not None}
        row["rule"] = e.get("rule")
        row["name"] = display_name(e.get("rule"))
        row["group"] = RULE_GROUP.get(e.get("rule"), "scout")
        # One status per row, so the tab can filter on it without re-deriving
        # the ledger's precedence rules in JavaScript.
        #
        # BACKFILLED COUNTS AS CARD (user, 2026-09-02). These rows were
        # replayed after the fact rather than wagered, so they were held out
        # of the units at first and every total read 0.00u, which made a log
        # of 735 rows look empty. They now carry their profit like any card
        # row. The `backfilled` flag is kept on the row so the Status filter
        # can still isolate them, and scout_card_log.py's own report is
        # untouched -- the ledger still separates hindsight from money that
        # was actually risked.
        # SHADOW AND NOT-BET ARE ONE STATUS (user, 2026-09-02). They arrive
        # for different reasons -- a shadow rule is auditioning, a not-bet row
        # is a card rule whose wager was missed -- but in a bet log they mean
        # the same thing: the rule fired and no money was risked. Splitting
        # them just gave two dropdown entries that both read "no units". The
        # underlying flags stay on the row.
        row["kind"] = ("not_bet" if (e.get("not_bet") or e.get("shadow"))
                       else "card")
        if e.get("backfilled"):
            row["backfilled"] = True
        bets.append(row)
    # Newest first, and within a day by first pitch -- the order a bet log is
    # read in.
    bets.sort(key=lambda r: (r.get("date") or "", str(r.get("commence") or "")),
              reverse=True)

    def tally(rows):
        graded = [r for r in rows if r.get("result") in ("WIN", "LOSS", "PUSH")]
        w = sum(1 for r in graded if r["result"] == "WIN")
        l = sum(1 for r in graded if r["result"] == "LOSS")
        p = sum(1 for r in graded if r["result"] == "PUSH")
        # Card rows carry units, backfilled ones included since they are
        # card now. Shadow and not-bet stay out: those fired live and were
        # deliberately not wagered.
        u = sum(r.get("profit") or 0 for r in graded if r.get("kind") == "card")
        return {"w": w, "l": l, "push": p, "n": len(rows),
                "pending": sum(1 for r in rows if r.get("result") == "pending"),
                "units": round(u, 2)}

    by_rule = collections.defaultdict(list)
    for r in bets:
        by_rule[r["rule"]].append(r)

    out = {
        "sport": "MLB",
        "type": "plays-feed",
        "generated": datetime.datetime.now(datetime.timezone.utc)
                             .isoformat(timespec="seconds").replace("+00:00", "Z"),
        "note": ("The scout bet log: a projection of "
                 "MLBstrikeouts/data/scout-card-log.json. Results and profit "
                 "are the ledger's own, settled by grade_scout_ledger.py from "
                 "the finals; nothing is recomputed here. `kind` is one of "
                 "card / shadow / not_bet / backfilled, and only card rows "
                 "carry units; not-bet rows (which include what used to be "
                 "tracked separately as shadow) fired live with no money on "
                 "them, so they stay out of the units. "
                 "Backfilled rows count as card (user, 2026-09-02) and keep a "
                 "`backfilled` flag so they can still be isolated."),
        "bets": bets,
        "summary": {
            "all": tally(bets),
            "card": tally([r for r in bets if r["kind"] == "card"]),
            "by_rule": {k: tally(v) for k, v in sorted(by_rule.items())},
        },
    }
    for path in OUTPUT_PATHS:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(out, fh, indent=1)
    c = out["summary"]["card"]
    # "of which" has to mean of the CARD rows, not of every row -- once the
    # shadow systems were backfilled the all-rows count exceeded the card
    # count and the line read as an impossible subset.
    n_card = sum(1 for b in bets if b["kind"] == "card")
    n_card_bf = sum(1 for b in bets if b["kind"] == "card" and b.get("backfilled"))
    n_bf = sum(1 for b in bets if b.get("backfilled"))
    print(f"bet log: {len(bets)} rows ({n_card} card, of which {n_card_bf} "
          f"backfilled; {n_bf - n_card_bf} more backfilled at no stake) · "
          f"card record {c['w']}-{c['l']} {c['units']:+.2f}u "
          f"-> {len(OUTPUT_PATHS)} paths")


if __name__ == "__main__":
    main()
