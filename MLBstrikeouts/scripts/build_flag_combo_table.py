#!/usr/bin/env python3
"""
build_flag_combo_table.py — the flag-combo performance grid, rebuilt daily.

Every defect-flag rule this repo has ever carded was argued from a table like
this one, and each time the table was recomputed by hand in a session and then
evaporated. On 2026-09-01 that cost real money twice in one day: a rust-only
OVER rule cleared a 33-game backtest, a walk-forward split AND a permutation
test (p=0.0027) on the 15-slate snapshot cache, went to shadow, and died the
same afternoon when the full season was replayed (107-113, -7.1%, p=0.57).

So the grid is a build artifact now. It is recomputed from scratch on every
daily run against EVERY gradeable game of the season, and the dashboard reads
it instead of a number somebody remembered.

How the flags are recovered for past games: they are NOT read from the payload
snapshots (those only exist from 2026-08-17, which is exactly the window that
manufactured the rust-over mirage). ``role_flags`` is replayed as-of each game
date from the pitcher game logs, the same way the live builder computes it, so
a game on 2026-04-12 gets the flags it would have carried that morning.

Grading uses the real closing-ish prices carried in mlb-all-ml.json
(over_ml/under_ml), never an assumed -110 -- the market moving to a read is
most of what kills these rules.

Output: MLBstrikeouts/data/flag-combo-table.json (+ the dashboard copy)
  { generated, span, games, baselines, combos[], flags[] }

Usage:  cd MLBstrikeouts && python -m scripts.build_flag_combo_table
"""
import json
import os
import sys
import datetime

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "sources"))

import slate_wrc_form as swf

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ALLML = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "mlb-all-ml.json"))
OUTPUT_PATHS = [
    os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "flag-combo-table.json")),
    os.path.normpath(os.path.join(
        SCRIPT_DIR, "..", "..", "PythonDashboard", "data", "flag-combo-table.json")),
]

DEFECTS = ("layoff", "stale-window", "opener", "swingman")

# The card rule as of 2026-09-02: a swingman flag anywhere on the game.
# Everything else is measurement.
CARD_REQUIRES = "swingman"

# Canonical combo naming (user, 2026-09-01): the card requirement leads, then
# the rest alphabetically -- "swingman+opener+stale-window", never
# "opener+stale-window+swingman". One spelling per combo everywhere it is
# written (this table, the card chips, the ledger's `combo` tag) so a cell can
# be tracked across the dashboard, the log and the backtest without a lookup.
COMBO_ORDER = (CARD_REQUIRES,) + tuple(sorted(k for k in DEFECTS
                                              if k != CARD_REQUIRES))


def canonical_combo(kinds):
    """The one spelling for a set of defect kinds."""
    return "+".join(k for k in COMBO_ORDER if k in kinds)


def profit(ml, won):
    if not won:
        return -1.0
    return ml / 100.0 if ml > 0 else 100.0 / -ml


def gradeable_games():
    games = [g for g in json.load(open(ALLML, encoding="utf-8")).get("games", [])
             if g.get("away_score") is not None and g.get("home_score") is not None
             and g.get("total_line") is not None
             and g.get("over_ml") is not None and g.get("under_ml") is not None
             and g.get("away_pitcher") and g.get("home_pitcher")]
    games.sort(key=lambda g: g["date"])
    return games


def flag_kinds(game, starts_by, apps_by):
    """The defect kinds this game would have carried on its own date."""
    kinds = set()
    for side in ("away", "home"):
        listed = game[f"{side}_pitcher"]
        name = swf.resolve_pitcher(listed, starts_by, game[side]) or listed
        form = swf.form_for(starts_by.get(name, []), apps_by.get(name, []),
                            game["date"])
        for f in swf.role_flags(form, game["date"]):
            for k in DEFECTS:
                if f.startswith(k):
                    kinds.add(k)
    return kinds


def build_rows():
    logs = swf._load(swf.GAME_LOGS)
    starts_by = swf.organize_starts(logs)
    apps_by = swf.organize_appearances(logs)
    rows, pushes = [], 0
    for g in gradeable_games():
        total = g["away_score"] + g["home_score"]
        line = g["total_line"]
        if total == line:
            pushes += 1
            continue
        over_won = total > line
        rows.append({
            "date": g["date"],
            "kinds": flag_kinds(g, starts_by, apps_by),
            "over_won": over_won,
            "pu": profit(g["under_ml"], not over_won),
            "po": profit(g["over_ml"], over_won),
        })
    return rows, pushes


def summarize(rows, key):
    """(w, l, units, roi) for a set of games bet to `key` ('pu' under/'po' over)."""
    if not rows:
        return None
    wins = sum(1 for r in rows
               if (r["over_won"] if key == "po" else not r["over_won"]))
    units = sum(r[key] for r in rows)
    return {"w": wins, "l": len(rows) - wins, "n": len(rows),
            "units": round(units, 2), "roi": round(units / len(rows) * 100, 1)}


def main():
    rows, pushes = build_rows()
    flagged = [r for r in rows if r["kinds"]]

    combos = {}
    for r in flagged:
        combos.setdefault(canonical_combo(r["kinds"]), []).append(r)

    out_combos = []
    for combo, arr in combos.items():
        kinds = combo.split("+")
        out_combos.append({
            "combo": combo,
            "kinds": kinds,
            # A combo is on the card when it satisfies the live rule.
            "carded": CARD_REQUIRES in kinds,
            "under": summarize(arr, "pu"),
            "over": summarize(arr, "po"),
        })
    # Reading order (user, 2026-09-01): lexicographic over COMBO_ORDER, i.e.
    # A, A+B, A+B+C, A+B+C+D, A+B+D, A+C, A+C+D, A+D, B, B+C, ... Every combo
    # containing a flag sits in that flag's block, deepest-first, so the grid
    # enumerates the whole space in one pass with no group headers eating
    # rows. The card side falls out on top for free: swingman is A, so every
    # carded combo is an A-row.
    idx = {k: i for i, k in enumerate(COMBO_ORDER)}
    out_combos.sort(key=lambda c: tuple(idx[k] for k in c["kinds"]))

    out_flags = [{
        "flag": k,
        "under": summarize([r for r in flagged if k in r["kinds"]], "pu"),
        "over": summarize([r for r in flagged if k in r["kinds"]], "po"),
    } for k in DEFECTS]

    blob = {
        "sport": "MLB",
        "type": "flag-combo-table",
        "generated": datetime.datetime.now(datetime.timezone.utc)
                             .isoformat(timespec="seconds").replace("+00:00", "Z"),
        "span": {"from": rows[0]["date"], "to": rows[-1]["date"]} if rows else {},
        "games": {"gradeable": len(rows), "flagged": len(flagged),
                  "pushes_dropped": pushes},
        "card_requires": CARD_REQUIRES,
        "note": ("Flags replayed as-of each game date from the pitcher game logs "
                 "(not from payload snapshots, which only start 2026-08-17 and "
                 "produced the rust-over mirage). Graded at the payload's own "
                 "over_ml/under_ml, 1u risk. A combo row counts a game once, "
                 "under its exact flag set; a flag row counts a game under every "
                 "flag it carries, so flag rows overlap and combo rows do not."),
        "baselines": {"under": summarize(rows, "pu"), "over": summarize(rows, "po")},
        "combos": out_combos,
        "flags": out_flags,
    }

    for path in OUTPUT_PATHS:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(blob, fh, indent=1)
    print(f"flag-combo table: {len(flagged)} flagged of {len(rows)} gradeable "
          f"({blob['span'].get('from')}..{blob['span'].get('to')}), "
          f"{len(out_combos)} combos -> {len(OUTPUT_PATHS)} paths")


if __name__ == "__main__":
    main()
