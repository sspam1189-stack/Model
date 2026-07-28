#!/usr/bin/env python3
"""
MLBstrikeouts/scripts/hand_tails_watch.py
Shadow WATCHLIST for the handedness fade/tail idea.

Scans every starter's fade/take record on platoon-disadvantage games
(RHP vs HAND_MIN+ lefty bats, LHP vs HAND_MIN+ righty bats), EXCLUDES arms
already on the active hand_tails list, and writes the candidates (ranked by
their stronger side) to mlb-hand-tails-watch.json. Nothing here is bet -- it
is a review list so you can decide later whether to promote an arm.

Reads mlb-all-ml.json (all scheduled starters + FanDuel odds + results) plus
the K-model bat_sides / batting_orders caches. Season replay, so it is an
in-sample estimate -- treat it as a leaderboard to watch, not a proven edge.

Usage:
    cd MLBstrikeouts
    python -m scripts.hand_tails_watch
"""
import sys
import os
import json
import glob
import datetime
import unicodedata
import re

sys.path.insert(0, os.path.dirname(__file__))

from fade_ml_common import stake_for, profit_for
from hand_tails import tail_entry, HAND_MIN
from fade_list import matched_entry

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "data", "pitcher_cache", "mlb"))
OUTPUT_PATHS = [
    os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "mlb-hand-tails-watch.json")),
    os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "PythonDashboard", "data", "mlb-hand-tails-watch.json")),
]

MIN_GAMES = 5      # need at least this many qualifying starts to appear
MIN_UNITS = 2.0    # stronger side must clear +this many units to appear


def _norm(s):
    s = unicodedata.normalize("NFD", s or "").lower()
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", s)).strip()


def _load(name_glob):
    fs = sorted(glob.glob(os.path.join(CACHE_DIR, name_glob)))
    return json.load(open(fs[-1], encoding="utf-8")) if fs else {}


def build():
    allml_path = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "mlb-all-ml.json"))
    games = json.load(open(allml_path)).get("games", [])
    settled = [g for g in games if g.get("home_win") is not None]
    bat = _load("player_bat_sides_*.json")
    bo = _load("batting_orders_*.json")
    era_adv = _load("pitcher_advanced_starter_2026_thru_*.json")
    ERA = {_norm(v.get("player_name")): v.get("ERA") for v in era_adv.values()}

    def opp_counts(team, date):
        lu = (bo.get(date) or {}).get(team)
        if not lu:
            return None, None
        return (sum(1 for p in lu if bat.get(str(p)) in ("L", "S")),
                sum(1 for p in lu if bat.get(str(p)) in ("R", "S")))

    # per pitcher -> list of (fade_profit, take_profit, stake_opp, stake_team, won_team)
    per = {}
    for g in settled:
        for side in ("home", "away"):
            p, hand = g.get(side + "_pitcher"), g.get(side + "_hand")
            if not p or hand not in ("L", "R"):
                continue
            if tail_entry(p)[0]:          # already on the active list -> skip
                continue
            opp = g["away"] if side == "home" else g["home"]
            L, R = opp_counts(opp, g["date"])
            if L is None:
                continue
            heavy = (L >= HAND_MIN) if hand == "R" else (R >= HAND_MIN)
            if not heavy:
                continue
            to = g.get(side + "_ml")
            oo = g.get(("away" if side == "home" else "home") + "_ml")
            if to is None or oo is None:
                continue
            pw = g["home_win"] if side == "home" else (not g["home_win"])
            per.setdefault((_norm(p), p, hand), []).append(
                (profit_for(oo, not pw), profit_for(to, pw),
                 stake_for(oo), stake_for(to), pw))

    cands = []
    for (nk, name, hand), rs in per.items():
        if len(rs) < MIN_GAMES:
            continue
        fu = round(sum(r[0] for r in rs), 2)
        tu = round(sum(r[1] for r in rs), 2)
        fw = sum(1 for r in rs if not r[4])   # fade wins when team loses
        tw = sum(1 for r in rs if r[4])
        n = len(rs)
        action = "fade" if fu >= tu else "take"
        best_u = max(fu, tu)
        if best_u < MIN_UNITS:
            continue
        # A FADE suggestion for an arm already on the fade list is redundant
        # (already faded elsewhere) -- only surface it if the TAKE side is the
        # stronger, genuinely-new direction.
        if action == "fade" and matched_entry(name):
            continue
        cands.append({
            "pitcher": name, "hand": hand, "games": n,
            "suggest": action, "suggestUnits": best_u,
            "fade": {"w": fw, "l": n - fw, "u": fu},
            "take": {"w": tw, "l": n - tw, "u": tu},
            "era": ERA.get(nk),
        })
    cands.sort(key=lambda c: -c["suggestUnits"])

    payload = {
        "sport": "MLB", "type": "hand-tails-watch",
        "generated": datetime.datetime.now(datetime.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "handMin": HAND_MIN, "minGames": MIN_GAMES, "minUnits": MIN_UNITS,
        "note": "Shadow review list -- arms NOT on the active hand-tails list "
                "showing a handedness-conditional edge. In-sample season replay; "
                "watch, don't bet. Promote by editing scripts/hand_tails.py.",
        "candidates": cands,
    }
    for path in OUTPUT_PATHS:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(payload, f, indent=2)
    return payload


if __name__ == "__main__":
    pay = build()
    print(f"[hand-tails-watch] {len(pay['candidates'])} watch candidates "
          f"(>= {MIN_GAMES} games, best side >= +{MIN_UNITS}u)")
    for c in pay["candidates"][:12]:
        b = c["fade"] if c["suggest"] == "fade" else c["take"]
        print(f"  {c['pitcher']:22s} {c['hand']} {c['suggest']:4s} "
              f"{b['w']}-{b['l']} {b['u']:+.2f}u  ({c['games']}gs, ERA "
              f"{round(c['era'],2) if c['era'] else '-'})")
