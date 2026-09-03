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

# thin-sample is deliberately NOT here, though slate_wrc_form emits it as a
# flag (season GS < THIN_SAMPLE_GS). It marks "this arm has fewer than five
# starts on the year", which in April and May is most of the league: it fires
# on 923 of 2,010 gradeable games, 46% of the season. That is a season-stage
# marker, not a defect in the data the way a swingman's missing relief work is.
#
# Measured full-season 2026-09-02 rather than assumed. Alone it goes 321-341
# -7.7% to the under against a -3.5% blind baseline, and -1.7% to the over
# (p=0.073) against -6.0%. Adding it here would newly flag 662 games on top of
# the current 510 and bet them at -7.7%, so it would more than double the card
# to lose money faster.
#
# One real finding, recorded so it is not rediscovered as new: inside the
# swingman rule thin-sample sorts the winners -- swingman+thin-sample is
# 99-68 +13.3% (n=167) against 67-56 +3.3% (n=123) for swingman without it.
# The reading is that a reliever-turned-starter early in his year is where the
# market has least to price on. NOT acted on: the halves run -9.1/+27.5, and
# it is a split taken after the fact on 167 games.

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


# PER-COMBO VERDICTS (user, 2026-09-01). Replaces the single "swingman
# present -> under" rule with an explicit decision per configuration, taken
# after reading the full-season grid. Recorded here rather than derived from
# the live numbers on purpose: a verdict is a DECISION, and deriving it would
# let a cell silently flip sides on a week of variance. `main` prints a drift
# warning when the live ROI stops agreeing with the verdict.
#
# What the user accepted, and the honest caveat: this is a post-hoc carve of a
# validated aggregate (swingman-present was 166-124 +9.1%, p=0.0047 over 290
# games). Cells are kept or dropped on their own p-values, which is the thing
# that produced the rust-over mirage when done to a 15-slate window. It is done
# here on the full season, but the multiple-comparison cost is real: 16 tests
# were scanned, so ~0.8 cells should look significant by chance.
#
# "under"/"over" = bet that side. None = no play.
COMBO_VERDICTS = {
    # Cleared the gate outright.
    "swingman":                            "under",   # 38-23 +19.0% p=0.030
    # Carded on the user's call at p<0.20 rather than p<0.05.
    "swingman+opener":                     "under",   # 23-16 +13.3% p=0.118
    "swingman+stale-window":               "over",    # 30-20 +14.7% p=0.059
    "layoff":                              "under",   # 41-34  +4.8% p=0.199
    # Carded 2026-09-01 (user) despite failing the gate: +3.4% beats the
    # -3.5% under baseline by ~7pts but p=0.296, i.e. three runs in ten look
    # this good by chance. The over side of the same cell is -12.4%, so the
    # direction at least agrees with every other swingman combo.
    "swingman+layoff+opener":              "under",   # 27-23  +3.4% p=0.296
    # Measured and failed -- flat or worse than baseline on a real sample.
    "swingman+layoff":                     None,      # 24-22  -0.7% p=0.438
    "opener":                              None,      # 25-21  +3.0% p=0.330
    "stale-window":                        None,      # 42-44  -6.5% p=0.604
    # Thin swingman stacks (n<25): no verdict of their own, played as the
    # swingman rule until they have one. All three run +29% to +73% under.
    "swingman+opener+stale-window":        "under",   # 13-6  +29.0% n=19
    "swingman+layoff+stale-window":        "under",   # 10-3  +46.7% n=13
    "swingman+layoff+opener+stale-window": "under",   # 11-1  +73.2% n=12
    # Thin rust combos (n<=5): nothing to read, and the rust side measured
    # dead in aggregate. No play.
    "layoff+opener":                       None,
    "layoff+stale-window":                 None,
    "opener+stale-window":                 None,
    "layoff+opener+stale-window":          None,
}


# Combos moved to SHADOW from a DATE rather than deleted, so the change
# applies to slates on and after it and yesterday's card still reads the way
# it was bet. Same shape as the fade roster's dated segments.
#
# Shadow is not retirement. The combo keeps its measured side, keeps
# qualifying, and keeps being written to the ledger -- as a not-bet row
# carrying no units. That is the difference that matters: a retired combo
# stops producing evidence, a shadowed one goes on producing it while
# costing nothing. If the record firms up it can be carded again from a
# date, using the same map.
#
#   layoff (alone)  SHADOW FROM 2026-09-02 (user). 41-34 +4.8% under over 75
#                   games at the time of the call, against a -3.5% blind
#                   baseline -- positive, but it
#                   never cleared significance (p=0.21) and it is the only
#                   combo with no swingman in it that was ever carded, so it
#                   was carrying the card rule's one exception on the weakest
#                   evidence on the grid. It stays measured and tracked; it
#                   just stops being bet.
#
#   swingman+stale-window
#                   SHADOW FROM 2026-09-03 (user). 30-20 +14.7% p=0.059 on
#                   the season grid -- the largest edge of any cell that
#                   never cleared the gate, and the ONLY combo on the board
#                   played to the OVER. That is what makes it worth
#                   shadowing rather than keeping: every other verdict on
#                   this grid is an under, so the one contrarian cell is
#                   also the one with no neighbour to corroborate it. If it
#                   is real, the story is that swingman says the arm is
#                   current while stale-window says his line is two months
#                   old; if it is not, it is the cell a 16-test scan is
#                   expected to throw up, and CLAUDE.md already records that
#                   ~0.8 cells should look significant by chance and that
#                   only swingman-alone (p=0.030) actually cleared.
#
#                   Live record before the call: 0-1. SD @ CIN on 9/1
#                   (Vasquez swingman-2g, Lodolo stale-window-46d) went
#                   O9 and lost. One game settles nothing either way -- it
#                   is listed because it is the whole live sample.
#
#                   It keeps its "over" side in COMBO_VERDICTS, so it goes
#                   on qualifying and logging as a no-stake row. If the over
#                   thesis firms up over 15-20 tracked plays it can be
#                   carded again from a date, using this same map.
VERDICT_SHADOW = {"layoff": "2026-09-02",
                  "swingman+stale-window": "2026-09-03"}


def _today():
    return datetime.date.today().isoformat()


def shadow_from(combo, as_of=None):
    """The date this combo went to shadow, or None if it is not shadowed.

    Returns the date only once that date has arrived, so a slate before it
    still reads as carded.
    """
    off = VERDICT_SHADOW.get(combo)
    return off if off and (as_of or _today()) >= off else None


def verdict_for(combo):
    """Measured side for a combo, or None where no side was ever carded.

    Unknown combos default to the swingman rule so a never-before-seen flag
    mix still behaves sensibly. Undated on purpose: a shadowed combo keeps
    its side here -- the side is what makes a tracked no-stake row
    meaningful -- so a caller deciding whether to BET must check
    ``shadow_from`` as well. Only the stake is dated, not the side.
    """
    if combo in COMBO_VERDICTS:
        return COMBO_VERDICTS[combo]
    return "under" if CARD_REQUIRES in combo.split("+") else None


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

    today = _today()
    out_combos = []
    for combo, arr in combos.items():
        kinds = combo.split("+")
        side = verdict_for(combo)
        shadowed = shadow_from(combo, today)
        out_combos.append({
            "combo": combo,
            "kinds": kinds,
            # The measured side for this configuration, or None where none
            # was ever carded. `carded` is the narrower question -- has a
            # side AND is not shadowed -- and is what decides whether the
            # tab bets the row.
            "verdict": side,
            "carded": side is not None and not shadowed,
            "shadow": bool(shadowed),
            "shadow_from": shadowed,
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
    # Section first (carded, then shadow, then no-play), lexicographic INSIDE
    # each section. Sorting purely lexicographically interleaved them --
    # swingman+layoff is a no-play but sorts between swingman and
    # swingman+layoff+opener, so the carded block got cut into three by
    # header rows. Shadow sits between the two: it has a side, it just isn't
    # bet, so it does not belong under a header reading "measured flat".
    def _section(c):
        return 0 if c["carded"] else (1 if c["shadow"] else 2)

    out_combos.sort(key=lambda c: (_section(c),
                                   tuple(idx[k] for k in c["kinds"])))

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
        # Resolved AS OF today, so the daily logger and the tab both read the
        # sides that apply to tonight's slate without knowing about dated
        # shadow moves. `verdicts` is the SIDE; `verdicts_shadow` is the list
        # of combos whose side is tracked but not bet tonight. A consumer that
        # only reads `verdicts` will bet a shadowed combo, so both are
        # required to decide a stake.
        "verdicts": {k: verdict_for(k) for k in COMBO_VERDICTS},
        "verdicts_declared": {k: v for k, v in COMBO_VERDICTS.items()},
        "verdicts_shadow": sorted(k for k in VERDICT_SHADOW
                                  if shadow_from(k, today)),
        "verdicts_shadow_from": VERDICT_SHADOW,
        "verdicts_as_of": today,
        "verdicts_from": "2026-09-01",
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
