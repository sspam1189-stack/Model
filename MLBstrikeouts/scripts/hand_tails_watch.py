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

from fade_ml_common import stake_for, profit_for, load_props_index
from hand_tails import tail_entry, HAND_MIN, opp_lineup_counts

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "data", "pitcher_cache", "mlb"))
OUTPUT_PATHS = [
    os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "mlb-hand-tails-watch.json")),
    os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "PythonDashboard", "data", "mlb-hand-tails-watch.json")),
]

MIN_GAMES = 3      # need at least this many qualifying starts to appear
MIN_UNITS = 2.5    # stronger side must clear +this many units to appear


def _norm(s):
    s = unicodedata.normalize("NFD", s or "").lower()
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", s)).strip()


def _load(name_glob):
    fs = sorted(glob.glob(os.path.join(CACHE_DIR, name_glob)))
    return json.load(open(fs[-1], encoding="utf-8")) if fs else {}


def build():
    allml_path = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "mlb-all-ml.json"))
    allml = json.load(open(allml_path))
    games = allml.get("games", [])
    today_games = allml.get("today", [])
    # Gate to the same game universe the live grader (run_daily_hand_tails)
    # uses: only dates with a props slate in props_index. Without this the
    # watchlist replays extra slate-less days (e.g. Opening Day) the grader
    # never grades, so its records wouldn't match the active ledger.
    slate_dates = {d for d in load_props_index() if d}
    settled = [g for g in games
               if g.get("home_win") is not None and g.get("date") in slate_dates]
    era_adv = _load("pitcher_advanced_starter_2026_thru_*.json")
    ERA = {_norm(v.get("player_name")): v.get("ERA") for v in era_adv.values()}

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
            # Same lineup source the live grader uses (batting_orders cache +
            # live-handedness resolution for batters missing from bat_sides), so
            # the watchlist qualifies exactly the games run_daily_hand_tails does.
            L, R = opp_lineup_counts(opp, g["date"])
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
        cands.append({
            "pitcher": name, "hand": hand, "games": n,
            "suggest": action, "suggestUnits": best_u,
            "fade": {"w": fw, "l": n - fw, "u": fu},
            "take": {"w": tw, "l": n - tw, "u": tu},
            "era": ERA.get(nk),
        })
    cands.sort(key=lambda c: -c["suggestUnits"])
    by_name = {_norm(c["pitcher"]): c for c in cands}

    # Today's watch plays: watch-candidate arms starting today against a
    # qualifying opposite-hand lineup, applying the candidate's suggested side.
    # Review-only (never bet) -- surfaced in the dashboard's Today's plays with a
    # WATCH tag so a leaderboard name that fires today isn't missed. Only appears
    # once today's lineup is posted and clears HAND_MIN (same gate as the active
    # list); before that the arm is starting but not yet qualifying.
    today = []
    for g in today_games:
        for side in ("home", "away"):
            p, hand = g.get(side + "_pitcher"), g.get(side + "_hand")
            if not p or hand not in ("L", "R"):
                continue
            if tail_entry(p)[0]:          # active list -> real bet, not watch
                continue
            c = by_name.get(_norm(p))
            if not c:
                continue
            home, away = g.get("home"), g.get("away")
            arm_team = home if side == "home" else away
            opp_team = away if side == "home" else home
            # Today's game isn't in the season boxscore cache; use the shared
            # opp_lineup_counts (posted-lineup + live-handedness fallback).
            L, R = opp_lineup_counts(opp_team, g.get("date"))
            if L is None or not ((L >= HAND_MIN) if hand == "R" else (R >= HAND_MIN)):
                continue
            action = c["suggest"]
            sel = opp_team if action == "fade" else arm_team
            odds = g.get("home_ml") if sel == home else g.get("away_ml")
            today.append({
                "date": g.get("date"), "commence": g.get("commence"),
                "betType": "watch_" + action, "action": action, "watch": True,
                "pitcher": p, "hand": hand,
                "arm_team": arm_team, "opp_team": opp_team,
                "selection": sel, "home": home, "away": away,
                "oppLefty": L, "oppRighty": R,
                "odds": odds, "suggest": action,
                "suggestUnits": c["suggestUnits"], "games": c["games"],
                "result": "pending",
            })
    today.sort(key=lambda b: b.get("commence") or "")

    payload = {
        "sport": "MLB", "type": "hand-tails-watch",
        "generated": datetime.datetime.now(datetime.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "handMin": HAND_MIN, "minGames": MIN_GAMES, "minUnits": MIN_UNITS,
        "note": "Shadow review list -- arms NOT on the active hand-tails list "
                "showing a handedness-conditional edge. In-sample season replay; "
                "watch, don't bet. Promote by editing scripts/hand_tails.py.",
        "today": today,
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
