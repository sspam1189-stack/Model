#!/usr/bin/env python3
"""
MLBstrikeouts/scripts/build_hand_tails_roster.py
WALK-FORWARD handedness-fade roster.

The active hand-tails list is now RULE-DEFINED, not hand-picked: an arm is a
fade once its record on platoon-disadvantage starts (RHP vs HAND_MIN+ lefty
bats, LHP vs HAND_MIN+ righty bats) reaches MIN_GAMES starts AND >= MIN_UNITS
fade units -- and it is only faded FORWARD from the day it first qualified.

This removes the in-sample overfitting of a hardcoded list: an arm is selected
on its PAST record, then bet only on FUTURE starts. If its (cumulative) fade
record later slips back below the unit bar, it drops off (remove date), and can
re-qualify later -- so each arm carries a list of active [add, remove) windows.

Output: MLBstrikeouts/data/hand-tails-roster.json
  { "rule": {...}, "arms": { "<name>": {"hand": "L/R",
      "windows": [["YYYY-MM-DD", "YYYY-MM-DD" | null], ...]}, ... } }

run_daily_hand_tails.py grades only games inside an arm's active windows, so
the ledger it produces is a genuine paper-forward test.

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


def build():
    today = datetime.date.today().isoformat()
    per = _platoon_games()
    arms = {}
    for (name, hand), games in per.items():
        wins = _windows(games, today)
        if wins:
            arms[name] = {"hand": hand, "windows": wins}
    payload = {
        "sport": "MLB", "type": "hand-tails-roster",
        "generated": datetime.datetime.now(datetime.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "handMin": HAND_MIN,
        "rule": {"minGames": MIN_GAMES, "minUnits": MIN_UNITS},
        "note": "Walk-forward: an arm is faded only from the day its platoon-"
                "disadvantage fade record first cleared minGames + minUnits, and "
                "only on FUTURE starts (each [add, remove) window). Not in-sample.",
        "arms": dict(sorted(arms.items())),
    }
    for path in OUTPUT_PATHS:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(payload, f, indent=2)
    return payload


if __name__ == "__main__":
    pay = build()
    n_active = sum(1 for a in pay["arms"].values()
                   if a["windows"] and a["windows"][-1][1] is None)
    print(f"[hand-tails-roster] {len(pay['arms'])} arms have qualified "
          f"(>= {MIN_GAMES}gs & +{MIN_UNITS}u); {n_active} currently active")
    for name, a in pay["arms"].items():
        cur = a["windows"][-1]
        state = "ACTIVE since " + cur[0] if cur[1] is None else "off (last " + cur[1] + ")"
        print(f"  {name:<24} {a['hand']}  {state}")
