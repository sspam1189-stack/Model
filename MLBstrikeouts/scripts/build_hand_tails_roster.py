#!/usr/bin/env python3
"""
MLBstrikeouts/scripts/build_hand_tails_roster.py
Hand-tails PROMOTION DETECTOR (notifier), not the bet list.

The bet list is now MANUALLY CURATED (hand-tails-manual.json) -- the user adds
and removes arms by hand. This script does NOT drive that list. Instead it
computes, walk-forward, which arms currently clear the qualifying bar (record on
platoon-disadvantage starts -- RHP vs HAND_MIN+ lefty bats, LHP vs HAND_MIN+
righty bats -- reaching MIN_GAMES starts AND >= MIN_UNITS cumulative fade units)
and diffs that against the manual list:

  * promotions -- arms qualified today but NOT on the manual list. Surfaced as a
    dashboard badge so the user can decide whether to add them. Never auto-added.
  * demotions  -- manual-list arms whose record has slipped back under the bar.
    Advisory "consider removing" note; the arm stays bet until the user removes it.

Output: MLBstrikeouts/data/hand-tails-roster.json
  { "rule": {...}, "promotions": [...], "demotions": [...],
    "arms": { "<name>": {"hand", "windows": [[add, remove|null], ...]} } }
run_daily_hand_tails.py embeds promotions/demotions into the ledger the dashboard
reads; the `arms` windows are kept for reference/audit of the walk-forward math.

Usage:  cd MLBstrikeouts && python -m scripts.build_hand_tails_roster
"""
import os
import sys
import json
import datetime

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "sources"))

from hand_tails import opp_lineup_counts, HAND_MIN
from fade_ml_common import profit_for, load_props_index

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ALLML = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "mlb-all-ml.json"))
OUTPUT_PATHS = [
    os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "hand-tails-roster.json")),
    os.path.normpath(os.path.join(
        SCRIPT_DIR, "..", "..", "PythonDashboard", "data", "hand-tails-roster.json")),
]

MIN_GAMES = 4     # qualifying platoon-disadvantage starts before an arm can fade
MIN_UNITS = 3.0   # cumulative fade units the record must clear to be active


def _platoon_games():
    """Per pitcher: chronological list of (date, hand, fade_profit) on
    platoon-disadvantage starts (RHP vs HAND_MIN+ lefties, LHP vs HAND_MIN+
    righties). Fade = bet the opponent ML; profit is that bet's 1u P&L."""
    allml = json.load(open(ALLML, encoding="utf-8"))
    slate = {d for d in load_props_index() if d}
    games = [g for g in allml.get("games", [])
             if g.get("home_win") is not None and g.get("date") in slate]
    games.sort(key=lambda g: (g.get("date"), g.get("commence") or ""))
    per = {}
    for g in games:
        home, away = g.get("home"), g.get("away")
        for side in ("home", "away"):
            p, hand = g.get(side + "_pitcher"), g.get(side + "_hand")
            if not p or hand not in ("L", "R"):
                continue
            opp = away if side == "home" else home
            lefty, righty = opp_lineup_counts(opp, g["date"])
            if lefty is None:
                continue
            heavy = (lefty >= HAND_MIN) if hand == "R" else (righty >= HAND_MIN)
            if not heavy:
                continue
            opp_ml = g.get(("away" if side == "home" else "home") + "_ml")
            if opp_ml is None:
                continue
            pitcher_won = g["home_win"] if side == "home" else (not g["home_win"])
            fade_won = not pitcher_won
            per.setdefault((p, hand), []).append(
                (g["date"], profit_for(opp_ml, fade_won)))
    return per


def _windows(games, today):
    """Walk forward: active on a start iff the record over PRIOR platoon starts
    already clears MIN_GAMES + MIN_UNITS. Returns active [add, remove) windows;
    remove is None while still active. If the arm's record slipped below the bar
    on its most recent start(s), a start TODAY would not qualify, so the window
    is closed as of `today` (its already-graded historical starts stay in)."""
    cum_g, cum_u = 0, 0.0
    active = False
    open_date = None
    windows = []
    for date, profit in games:
        qualified = cum_g >= MIN_GAMES and cum_u >= MIN_UNITS
        if qualified and not active:
            active, open_date = True, date          # add
        elif not qualified and active:
            active = False
            windows.append([open_date, date])        # remove (this start not bet)
            open_date = None
        cum_g += 1
        cum_u += profit
    if active:
        still = cum_g >= MIN_GAMES and cum_u >= MIN_UNITS
        windows.append([open_date, None if still else today])
    return windows


MANUAL_PATH = os.path.normpath(os.path.join(
    SCRIPT_DIR, "..", "data", "hand-tails-manual.json"))


def _manual_list():
    """{name: {"hand", "since"}} from the manually curated fade list."""
    try:
        with open(MANUAL_PATH, encoding="utf-8") as f:
            return json.load(f).get("arms", {})
    except Exception:
        return {}


def build():
    today = datetime.date.today().isoformat()
    per = _platoon_games()
    arms = {}
    records = {}  # name -> full-season cumulative platoon-fade record
    for (name, hand), games in per.items():
        records[name] = {"hand": hand, "games": len(games),
                         "units": round(sum(p for _d, p in games), 2)}
        wins = _windows(games, today)
        if wins:
            arms[name] = {"hand": hand, "windows": wins}

    # Currently qualified = arm whose most-recent window is still open (its
    # cumulative platoon-fade record clears the bar as of today).
    qualified_now = {n for n, a in arms.items()
                     if a["windows"] and a["windows"][-1][1] is None}
    manual = _manual_list()
    manual_names = set(manual)

    # PROMOTIONS: qualified today but NOT on the manual list -> notify the user
    # (dashboard badge). The user decides whether to add them; nothing is auto-added.
    promotions = []
    for n in sorted(qualified_now - manual_names):
        r = records.get(n, {})
        promotions.append({
            "name": n, "hand": r.get("hand"), "games": r.get("games"),
            "units": r.get("units"), "since": arms[n]["windows"][-1][0]})

    # DEMOTIONS: on the manual list AND its cumulative platoon-fade record has
    # actually fallen under the bar -- enough sample (>= MIN_GAMES starts) but
    # < MIN_UNITS. That is the true "consider removing" signal. We do NOT flag an
    # arm merely for lacking an open walk-forward window: a freshly added arm can
    # clear the bar cumulatively yet have no forward start yet (it hasn't pitched
    # since being added), and must not be shown as under the bar.
    demotions = []
    for n in sorted(manual_names):
        r = records.get(n, {})
        if r.get("games", 0) >= MIN_GAMES and r.get("units", 0.0) < MIN_UNITS:
            demotions.append({
                "name": n, "hand": manual[n].get("hand") or r.get("hand"),
                "games": r.get("games"), "units": r.get("units")})

    payload = {
        "sport": "MLB", "type": "hand-tails-roster",
        "generated": datetime.datetime.now(datetime.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "handMin": HAND_MIN,
        "rule": {"minGames": MIN_GAMES, "minUnits": MIN_UNITS},
        "note": "Walk-forward qualifier computation used to NOTIFY (promotion "
                "candidates), not to drive the bet list. The bet list is the "
                "manually curated hand-tails-manual.json. `promotions` are arms "
                "that clear minGames+minUnits but are not on the manual list; "
                "`demotions` are manual-list arms whose record slipped under the bar.",
        "manualCount": len(manual_names),
        "qualifiedCount": len(qualified_now),
        "promotions": promotions,
        "demotions": demotions,
        "arms": dict(sorted(arms.items())),
    }
    for path in OUTPUT_PATHS:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(payload, f, indent=2)
    return payload


if __name__ == "__main__":
    pay = build()
    print(f"[hand-tails-roster] {pay['qualifiedCount']} arms currently clear "
          f">= {MIN_GAMES}gs & +{MIN_UNITS}u; manual list has {pay['manualCount']}")
    if pay["promotions"]:
        print(f"  ** {len(pay['promotions'])} PROMOTION CANDIDATE(S) -- not on manual list:")
        for p in pay["promotions"]:
            print(f"     + {p['name']:<22} {p['hand']}  "
                  f"{p['games']}gs {p['units']:+.2f}u  (qualified since {p['since']})")
    else:
        print("  no promotion candidates (every qualifier is already on the manual list)")
    if pay["demotions"]:
        print(f"  ~ {len(pay['demotions'])} on manual list no longer clearing the bar "
              f"(consider removing):")
        for d in pay["demotions"]:
            print(f"     - {d['name']:<22} {d['hand']}  "
                  f"{d.get('games')}gs {d.get('units'):+.2f}u")
