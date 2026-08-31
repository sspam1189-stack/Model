#!/usr/bin/env python3
"""
backtest_scout.py — grade scout card rules against history BEFORE they touch a bet.

Why this exists (2026-08-26): the scout tier had no validation harness. Rules
were amended same-day off two or three games -- the 8/25 amendment retiring the
flag clause was written on a 2-0 benched record and went 0-2 within 24 hours --
while the fade-ML model next door could quote 633 graded bets on demand. The
first run of this harness found that the four-window offense ladder, which was
the ENTIRE card rule at the time, has no measurable relationship to runs:
cold-aligned offenses scored 4.17 runs a game, hot-aligned ones 4.00.

Data source: the daily ``mlb-slate-scout.json`` snapshots already committed to
git, one per slate date, joined to finals from ``mlb-all-ml.json``. Nothing is
re-derived, so a backtest here sees exactly the payload the card saw that
morning -- including the value-suppressed thin cells and the flags.

Three rules this harness enforces on itself, learned the hard way:

  1. EVERY result is reported against a BASELINE (bet every under / every over
     / every home dog in the same games). In the 8/21-8/26 window blind unders
     ran +9.1% ROI, so an under rule at +12% is noise wearing a suit.
  2. Sample size is printed and small samples are labelled. Under 25 plays the
     harness prints NOISE and refuses to call anything an edge.
  3. Grading uses the ACTUAL prices carried in the payload (over_ml/under_ml,
     away_ml/home_ml), not an assumed -110, because the market moving to the
     read is most of what kills these rules.

Usage:
    python3 scripts/backtest_scout.py report            # standard battery
    python3 scripts/backtest_scout.py report --rule ladder_both_cold
    python3 scripts/backtest_scout.py rules             # list testable rules
    python3 scripts/backtest_scout.py history --refresh # rebuild the cache

Adding a rule: write a function taking (game) and returning one of
"over"/"under"/"away"/"home"/None, then register it in RULES. Run it here and
shadow-track it live before it is allowed on the card.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(SCRIPT_DIR, "..", ".."))
SCOUT_REL = "MLBstrikeouts/data/mlb-slate-scout.json"
ALLML = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "mlb-all-ml.json"))
CACHE = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "scout-backtest-cache.json"))

# Below this many graded plays a result is reported but never called an edge.
MIN_PLAYS_FOR_SIGNAL = 25


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

def _git(*args):
    return subprocess.run(["git", "-C", REPO, *args],
                          capture_output=True, text=True).stdout


def build_history(limit=400):
    """One record per (slate date, game): the morning payload + the final.

    Walks the commit history of the scout payload newest-first and keeps the
    FIRST snapshot seen for each slate date -- that is the last publish of that
    day, i.e. the payload closest to first pitch, which is what a bettor acted
    on. Games without a final (or without a total) are dropped.
    """
    finals = {}
    for g in json.load(open(ALLML, encoding="utf-8")).get("games", []):
        if g.get("away_score") is not None and g.get("home_score") is not None:
            finals[(g["date"], g["away"], g["home"])] = (g["away_score"], g["home_score"])

    seen, out = set(), []
    for sha in _git("log", f"-{limit}", "--format=%H", "--", SCOUT_REL).split():
        raw = _git("show", f"{sha}:{SCOUT_REL}")
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        date = payload.get("date")
        if not date or date in seen:
            continue
        seen.add(date)
        for g in payload.get("slate", []):
            key = (date, g.get("away"), g.get("home"))
            if key not in finals:
                continue
            away_runs, home_runs = finals[key]
            rec = {
                "date": date, "matchup": g.get("matchup"),
                "away": g.get("away"), "home": g.get("home"),
                "away_runs": away_runs, "home_runs": home_runs,
                "total_runs": away_runs + home_runs,
                "line": g.get("total"), "over_ml": g.get("over_ml"),
                "under_ml": g.get("under_ml"), "away_ml": g.get("away_ml"),
                "home_ml": g.get("home_ml"), "sides": {},
            }
            for side in ("away", "home"):
                s = (g.get("sides") or {}).get(side) or {}
                w = s.get("opp_wrc_windows") or {}
                form = s.get("form") or {}
                pen = s.get("pen") or {}
                rec["sides"][side] = {
                    "pitcher": s.get("pitcher"), "resolved": s.get("resolved"),
                    "hand": s.get("hand"), "flags": s.get("flags") or [],
                    "mismatch": s.get("mismatch"),
                    "platoon_gap": s.get("opp_platoon_gap"),
                    # The offense this starter faces, and the runs it scored.
                    "opp_team": s.get("opponent_offense"),
                    "opp_runs": home_runs if side == "away" else away_runs,
                    "ladder": [(w.get(k) or {}).get("wrcplus")
                               for k in ("last30", "last20", "last15", "last7")],
                    "ladder_pa": [(w.get(k) or {}).get("pa")
                                  for k in ("last30", "last20", "last15", "last7")],
                    "l5_era": (form.get("recent") or {}).get("era"),
                    "l5_whip": (form.get("recent") or {}).get("whip"),
                    "l5_ip_gs": (form.get("recent") or {}).get("ip_per_gs"),
                    "pen_rank": (pen.get("last30") or {}).get("rank"),
                    "pen30": (pen.get("last30") or {}).get("era"),
                    "pen7": (pen.get("last7") or {}).get("era"),
                }
            out.append(rec)
    out.sort(key=lambda r: (r["date"], r["matchup"] or ""))
    return out


def load_history(refresh=False):
    if not refresh and os.path.exists(CACHE):
        blob = json.load(open(CACHE, encoding="utf-8"))
        if blob.get("games"):
            return blob["games"]
    games = build_history()
    json.dump({"note": "Derived cache: git snapshots of mlb-slate-scout.json "
                       "joined to finals. Rebuild with `history --refresh`.",
               "games": games}, open(CACHE, "w"), indent=1)
    return games


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------

def american_profit(odds, won):
    """Profit on a 1u-risk bet. None odds fall back to -110."""
    if odds is None:
        odds = -110
    if not won:
        return -1.0
    return odds / 100.0 if odds > 0 else 100.0 / -odds


def settle(game, pick):
    """(won, profit) for a pick, or None when the bet pushes/can't grade."""
    if pick in ("over", "under"):
        line = game.get("line")
        if line is None:
            return None
        actual = game["total_runs"]
        if actual == line:
            return None                      # push
        won = actual > line if pick == "over" else actual < line
        odds = game.get("over_ml") if pick == "over" else game.get("under_ml")
        return won, american_profit(odds, won)
    if pick in ("away", "home"):
        won = ((game["away_runs"] > game["home_runs"]) if pick == "away"
               else (game["home_runs"] > game["away_runs"]))
        odds = game.get(f"{pick}_ml")
        return won, american_profit(odds, won)
    return None


def run_rule(games, rule):
    plays = []
    for g in games:
        pick = rule(g)
        if not pick:
            continue
        res = settle(g, pick)
        if res is None:
            continue
        won, profit = res
        plays.append({"game": g, "pick": pick, "won": won, "profit": profit})
    return plays


def summarize(plays):
    n = len(plays)
    if not n:
        return {"n": 0, "w": 0, "l": 0, "units": 0.0, "roi": 0.0}
    w = sum(1 for p in plays if p["won"])
    units = sum(p["profit"] for p in plays)
    return {"n": n, "w": w, "l": n - w, "units": units, "roi": units / n}


def baseline_for(games, plays):
    """What blind betting the same DIRECTION over the same games returns.

    A rule only has an edge to the extent it beats this. Totals rules are
    compared to betting that side in every game with a line; ML rules to
    betting that side in every game.
    """
    if not plays:
        return None
    picks = {p["pick"] for p in plays}
    if len(picks) != 1:
        return None
    pick = picks.pop()
    return summarize(run_rule(games, lambda g: pick))


# ---------------------------------------------------------------------------
# Rules under test
# ---------------------------------------------------------------------------

def _cls(ladder, cold=90, hot=110):
    if any(v is None for v in ladder):
        return None
    if all(v <= cold for v in ladder):
        return "cold"
    if all(v >= hot for v in ladder):
        return "hot"
    return "mid"


def ladder_both_cold(g):
    """THE 8/25 CARD RULE. Both offenses cold-aligned across all four windows."""
    a, h = (_cls(g["sides"][s]["ladder"]) for s in ("away", "home"))
    return "under" if a == "cold" and h == "cold" else None


def ladder_both_hot(g):
    """THE 8/25 CARD RULE, over side."""
    a, h = (_cls(g["sides"][s]["ladder"]) for s in ("away", "home"))
    return "over" if a == "hot" and h == "hot" else None


def ladder_any_cold(g):
    """Looser: one cold-aligned offense is enough."""
    a, h = (_cls(g["sides"][s]["ladder"]) for s in ("away", "home"))
    return "under" if "cold" in (a, h) else None


def pens_both_hot(g):
    """Both bullpens under 3.00 over the last 7 days."""
    p = [g["sides"][s]["pen7"] for s in ("away", "home")]
    if any(v is None for v in p):
        return None
    return "under" if max(p) < 3.0 else None


def pens_both_leaking(g):
    """Both bullpens over 4.00 over the last 7 days."""
    p = [g["sides"][s]["pen7"] for s in ("away", "home")]
    if any(v is None for v in p):
        return None
    return "over" if min(p) > 4.0 else None


def short_starters_bad_pens(g):
    """Both sides hand 4+ innings to a leaking pen -- the 'bullpen game' over."""
    hits = 0
    for s in ("away", "home"):
        d = g["sides"][s]
        if d["l5_ip_gs"] is not None and d["pen7"] is not None \
                and d["l5_ip_gs"] < 5.2 and d["pen7"] > 4.0:
            hits += 1
    return "over" if hits == 2 else None


def workhorse_elite_pens(g):
    """Both sides pair a 5.7+ IP starter with a sub-3.00 pen."""
    hits = 0
    for s in ("away", "home"):
        d = g["sides"][s]
        if d["l5_ip_gs"] is not None and d["pen7"] is not None \
                and d["l5_ip_gs"] >= 5.7 and d["pen7"] < 3.0:
            hits += 1
    return "under" if hits == 2 else None


def combo_cold_and_pens(g):
    """The alt-build shape: cold ladder AND the run-suppression side agrees."""
    ok = 0
    for s in ("away", "home"):
        d = g["sides"][s]
        if _cls(d["ladder"]) == "cold" and d["pen7"] is not None and d["pen7"] < 3.5:
            ok += 1
    return "under" if ok == 2 else None


def flagged_starter_under(g):
    """The clause we RETIRED on 8/25: a named data defect on either starter.

    Kept as a rule so the retirement can be re-examined with evidence rather
    than argued from the last two results.
    """
    defect = ("layoff", "stale-window", "opener", "swingman")
    for s in ("away", "home"):
        if any(f.startswith(defect) for f in g["sides"][s]["flags"]):
            return "under"
    return None


def either_starter_cold_over(g):
    """Disjunction over the one monotone field (proposed 2026-08-31).

    The field report shows runs allowed climbing 3.80 / 4.16 / 5.08 across
    the L5-ERA buckets while the offense ladder shows nothing, yet every
    over rule in the battery is a two-sided conjunction that can't reach 25
    plays. This is the disjunctive over: either starter above 4.50 over his
    last five starts -> over. Deliberately mirrors flagged_starter_under's
    shape so a failure means the field, not the sample size."""
    for s in ("away", "home"):
        e = g["sides"][s]["l5_era"]
        if e is not None and e > 4.50:
            return "over"
    return None


def unresolved_starter(g):
    """Either probable unresolved -- tests whether the market misprices these."""
    for s in ("away", "home"):
        if g["sides"][s].get("resolved") is False:
            return "under"
    return None


RULES = {
    "ladder_both_cold": ladder_both_cold,
    "ladder_both_hot": ladder_both_hot,
    "ladder_any_cold": ladder_any_cold,
    "pens_both_hot": pens_both_hot,
    "pens_both_leaking": pens_both_leaking,
    "short_starters_bad_pens": short_starters_bad_pens,
    "workhorse_elite_pens": workhorse_elite_pens,
    "combo_cold_and_pens": combo_cold_and_pens,
    "flagged_starter_under": flagged_starter_under,
    "either_starter_cold_over": either_starter_cold_over,
    "unresolved_starter": unresolved_starter,
}


# ---------------------------------------------------------------------------
# Descriptive: does a field move runs at all?
# ---------------------------------------------------------------------------

def field_report(games):
    """Runs scored/allowed bucketed by scout field -- signal before betting.

    A field that cannot separate run environments will never beat a price, so
    this runs first and cheaply kills bad rule ideas.
    """
    sides = [d for g in games for d in g["sides"].values()]
    print("\nDoes the field move runs? (bucketed means, before any price)")

    buckets = {}
    for d in sides:
        c = _cls(d["ladder"])
        if c and d["opp_runs"] is not None:
            buckets.setdefault(c, []).append(d["opp_runs"])
    print("\n  offense ladder -> runs that offense SCORED")
    for c in ("cold", "mid", "hot"):
        b = buckets.get(c, [])
        if b:
            print(f"    {c:5} n={len(b):4}  {st.mean(b):.2f} runs")

    print("\n  opposing pen L7 ERA -> runs that side ALLOWED")
    for lo, hi, lab in ((0, 3.0, "hot  <3.00"), (3.0, 4.5, "mid"), (4.5, 99, "leak >4.50")):
        b = [d["opp_runs"] for d in sides
             if d["pen7"] is not None and lo <= d["pen7"] < hi and d["opp_runs"] is not None]
        if b:
            print(f"    {lab:11} n={len(b):4}  {st.mean(b):.2f} runs")

    print("\n  starter last-5 ERA -> runs that side ALLOWED")
    for lo, hi, lab in ((0, 3.0, "sub-3.00"), (3.0, 4.5, "mid"), (4.5, 99, "over 4.50")):
        b = [d["opp_runs"] for d in sides
             if d["l5_era"] is not None and lo <= d["l5_era"] < hi and d["opp_runs"] is not None]
        if b:
            print(f"    {lab:11} n={len(b):4}  {st.mean(b):.2f} runs")


def print_rule(name, games, plays, verbose=False):
    s = summarize(plays)
    if not s["n"]:
        print(f"  {name:26} no qualifying games")
        return
    base = baseline_for(games, plays)
    edge = (s["roi"] - base["roi"]) * 100 if base else None
    tag = "NOISE" if s["n"] < MIN_PLAYS_FOR_SIGNAL else ("EDGE " if (edge or 0) > 3 else "     ")
    line = (f"  {name:26} {s['w']:>3}-{s['l']:<3} n={s['n']:<4} "
            f"{s['units']:+7.2f}u  ROI {100*s['roi']:+6.1f}%")
    if base:
        line += f" | baseline {100*base['roi']:+6.1f}% -> edge {edge:+6.1f}pts"
    print(f"{tag}{line}")
    if s["n"] < MIN_PLAYS_FOR_SIGNAL:
        print(f"{'':5}  {'':26} (under {MIN_PLAYS_FOR_SIGNAL} plays: not an edge, "
              f"a sample)")
    if verbose:
        for p in plays:
            g = p["game"]
            print(f"{'':7}{g['date']}  {g['matchup']:12} {p['pick']:5} "
                  f"line {g['line']}  actual {g['total_runs']:>2}  "
                  f"{'WIN ' if p['won'] else 'LOSS'} {p['profit']:+.2f}u")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("report", help="run the rule battery")
    p.add_argument("--rule", help="grade one rule only")
    p.add_argument("--verbose", "-v", action="store_true", help="list every play")
    p.add_argument("--refresh", action="store_true", help="rebuild the cache first")

    sub.add_parser("rules", help="list testable rules")
    p = sub.add_parser("history", help="show/rebuild the joined history")
    p.add_argument("--refresh", action="store_true")

    args = ap.parse_args()

    if args.cmd == "rules":
        for name, fn in RULES.items():
            doc = (fn.__doc__ or "").strip().splitlines()[0]
            print(f"  {name:26} {doc}")
        return 0

    games = load_history(refresh=getattr(args, "refresh", False))
    if not games:
        print("no history -- are the scout snapshots committed?")
        return 1
    dates = sorted({g["date"] for g in games})

    if args.cmd == "history":
        print(f"{len(games)} graded games over {len(dates)} slates "
              f"({dates[0]} .. {dates[-1]})")
        return 0

    print(f"Scout backtest — {len(games)} games, {len(dates)} slates "
          f"({dates[0]} .. {dates[-1]})")
    print("Prices are the payload's own over_ml/under_ml; 1u risk per play.")

    base_u = summarize(run_rule(games, lambda g: "under"))
    base_o = summarize(run_rule(games, lambda g: "over"))
    print(f"\nBASELINES  bet every under: {base_u['w']}-{base_u['l']} "
          f"ROI {100*base_u['roi']:+.1f}%   |   every over: {base_o['w']}-{base_o['l']} "
          f"ROI {100*base_o['roi']:+.1f}%")
    if abs(base_u["roi"]) > 0.05:
        print("  ^ this window is directionally skewed. Judge rules by the EDGE "
              "column, never raw ROI.")

    names = [args.rule] if args.rule else list(RULES)
    print("\nRULES")
    for name in names:
        fn = RULES.get(name)
        if not fn:
            print(f"  unknown rule {name!r}; see `rules`")
            return 1
        print_rule(name, games, run_rule(games, fn), verbose=args.verbose)

    if not args.rule:
        field_report(games)
        print("\nA rule is card-eligible only when it clears "
              f"{MIN_PLAYS_FOR_SIGNAL} plays AND beats its baseline, and then "
              "only after shadow-tracking live.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
