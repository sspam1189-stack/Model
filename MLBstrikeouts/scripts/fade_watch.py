#!/usr/bin/env python3
"""
MLBstrikeouts/scripts/fade_watch.py
Shadow WATCHLIST for the venue-fade idea (analog of hand_tails_watch.py).

Scans every starter's fade record (bet the OPPONENT's ML) split by the arm's
venue (home vs away), EXCLUDES arms already on the active fade list, and surfaces
those whose HOME or AWAY side clears MIN_GAMES + MIN_UNITS. suggest = 'home' or
'away' if only that side qualifies, 'all' if BOTH do. Nothing here is bet -- it
is a review leaderboard so you can decide whether to promote an arm into
fade_list.py (and on which venue side).

Reads mlb-all-ml.json (all scheduled starters + FanDuel odds + results). Season
replay from SEASON_START, so it is an in-sample estimate -- a list to watch, not
a proven edge.

Usage:
    cd MLBstrikeouts
    python -m scripts.fade_watch
"""
import sys
import os
import json
import datetime

sys.path.insert(0, os.path.dirname(__file__))

from fade_ml_common import stake_for, profit_for
from fade_list import matched_entry, _norm, SEASON_START

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATHS = [
    os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "mlb-fade-watch.json")),
    os.path.normpath(os.path.join(
        SCRIPT_DIR, "..", "..", "PythonDashboard", "data", "mlb-fade-watch.json")),
]

MIN_GAMES = 3      # a venue side needs at least this many fades to qualify
MIN_UNITS = 2.5    # ...and must clear +this many units on that side


def _rec(rows):
    """rows: list of (profit, won_bet, stake) -> {w,l,u,n,roi}."""
    w = sum(1 for r in rows if r[1])
    n = len(rows)
    u = round(sum(r[0] for r in rows), 2)
    staked = sum(r[2] for r in rows)
    return {"w": w, "l": n - w, "u": u, "n": n,
            "roi": round(u / staked, 4) if staked else 0.0}


def build():
    allml_path = os.path.normpath(
        os.path.join(SCRIPT_DIR, "..", "data", "mlb-all-ml.json"))
    allml = json.load(open(allml_path, encoding="utf-8"))
    settled = [g for g in allml.get("games", []) if g.get("home_win") is not None]
    today_games = allml.get("today", [])

    # per pitcher -> {'home': [(profit,won,stake)...], 'away': [...]}. The arm's
    # venue is the side he starts on; the fade bets his opponent's ML.
    per = {}
    team_by_key = {}   # key -> (latest_date, team_abbr) so the row shows his team
    for g in settled:
        d = g.get("date")
        if d and d < SEASON_START:
            continue
        for side in ("home", "away"):
            p = g.get(side + "_pitcher")
            if not p or matched_entry(p):          # on the active fade list -> skip
                continue
            opp_side = "away" if side == "home" else "home"
            odds = g.get(opp_side + "_ml")
            if odds is None:
                continue
            opp_won = (not g["home_win"]) if side == "home" else g["home_win"]
            key = (_norm(p), p)
            per.setdefault(key, {"home": [], "away": []})
            per[key][side].append((profit_for(odds, opp_won), opp_won, stake_for(odds)))
            arm_team = g.get(side)                  # the arm's team (his venue side)
            prev = team_by_key.get(key)
            if arm_team and (prev is None or (d or "") >= prev[0]):
                team_by_key[key] = ((d or ""), arm_team)

    cands = []
    for (nk, name), vs in per.items():
        home, away = _rec(vs["home"]), _rec(vs["away"])
        home_ok = home["n"] >= MIN_GAMES and home["u"] >= MIN_UNITS
        away_ok = away["n"] >= MIN_GAMES and away["u"] >= MIN_UNITS
        if not (home_ok or away_ok):
            continue
        suggest = "all" if (home_ok and away_ok) else ("home" if home_ok else "away")
        best_u = round((home["u"] if home_ok else 0) + (away["u"] if away_ok else 0), 2)
        cands.append({
            "pitcher": name, "team": team_by_key.get((nk, name), ("", None))[1],
            "suggest": suggest, "suggestUnits": best_u,
            "all": _rec(vs["home"] + vs["away"]), "home": home, "away": away,
        })
    cands.sort(key=lambda c: -c["suggestUnits"])
    by_name = {_norm(c["pitcher"]): c for c in cands}

    # Today's watch plays: a watch-candidate arm starting today on a qualifying
    # venue side -> surface the fade (bet the opponent) with a WATCH tag. Review
    # only, never bet.
    today = []
    for g in today_games:
        for side in ("home", "away"):
            p = g.get(side + "_pitcher")
            if not p or matched_entry(p):          # active -> real bet, not watch
                continue
            c = by_name.get(_norm(p))
            if not c:
                continue
            if c["suggest"] != "all" and c["suggest"] != side:
                continue                            # arm qualifies, but not on this side
            home_t, away_t = g.get("home"), g.get("away")
            arm_team = home_t if side == "home" else away_t
            opp_team = away_t if side == "home" else home_t
            odds = g.get("away_ml") if side == "home" else g.get("home_ml")
            today.append({
                "date": g.get("date"), "commence": g.get("commence"),
                "betType": "fade_watch", "watch": True, "pitcher": p,
                "arm_team": arm_team, "fadeTeam": arm_team, "selection": opp_team,
                "home": home_t, "away": away_t, "venue": side, "odds": odds,
                "suggest": c["suggest"], "suggestUnits": c["suggestUnits"],
                "games": (c["home"]["n"] if side == "home" else c["away"]["n"]),
                "result": "pending",
            })
    today.sort(key=lambda b: b.get("commence") or "")

    payload = {
        "sport": "MLB", "type": "fade-watch",
        "generated": datetime.datetime.now(datetime.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "minGames": MIN_GAMES, "minUnits": MIN_UNITS,
        "note": "Shadow review list -- arms NOT on the active fade list whose "
                "home or away fade clears the bar (>= %d games, >= +%.1fu). "
                "In-sample season replay; watch, don't bet. Promote by editing "
                "scripts/fade_list.py." % (MIN_GAMES, MIN_UNITS),
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
    print(f"[fade-watch] {len(pay['candidates'])} watch candidates "
          f"(>= {MIN_GAMES} games, qualifying side >= +{MIN_UNITS}u)")
    for c in pay["candidates"][:15]:
        print(f"  {c['pitcher']:20s} {c['suggest']:4s} +{c['suggestUnits']:.2f}u "
              f"| home {c['home']['w']}-{c['home']['l']} {c['home']['u']:+.2f}u "
              f"| away {c['away']['w']}-{c['away']['l']} {c['away']['u']:+.2f}u")
