#!/usr/bin/env python3
"""
MLBstrikeouts/scripts/fade_vs_team_watch.py
Auto-screened WATCH list for the "pitcher vs specific team" fade angle.

Surfaces every (pitcher, opponent-team) pair where the pitcher has been hit
hard by that team -- MIN_STARTS+ starts against them with an ERA >= MIN_ERA --
as a candidate to fade (bet that opponent) the next time the matchup comes up.
Excludes arms already faded (on FADE_LIST all-venue) and matchups already
curated on FADE_VS_TEAM, since those are covered. The fade record vs that team
is attached for review; it is NOT a screen filter (a bad-ERA matchup can still
have a thin/negative fade sample -- that's what "watch" means).

Data only: writes mlb-fade-vs-team-watch.json, not wired into the fade model.

ERA comes from the season per-start game logs; results/odds from mlb-all-ml.json.

Usage:
    cd MLBstrikeouts
    python -m scripts.fade_vs_team_watch
"""
import sys
import os
import json
import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))

from fade_ml_common import stake_for, profit_for
from fade_list import _norm, matched_entry, FADE_VS_TEAM, SEASON_START

MIN_STARTS = 2     # >= this many starts vs the team
MIN_ERA = 6.0      # ...and an ERA vs that team at or above this (watch floor)
FADE_ERA = 8.0     # ...but below this -- ERA >= FADE_ERA auto-promotes to the
                   # FADE list (fade_vs_team.py), so the watch band is [6, 8).

# Arms removed from this watch screen (user-curated). A removed arm KEEPS his
# candidate row -- flagged "excluded": true so his history stays visible for
# review -- but is never surfaced as a today's play. Token-matched like
# fade_list entries (all tokens must appear in the pitcher's name).
WATCH_EXCLUDE = [
    "Max Fried",  # removed 2026-08-09 (user request); history stays
]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATHS = [
    os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "mlb-fade-vs-team-watch.json")),
    os.path.normpath(os.path.join(
        SCRIPT_DIR, "..", "..", "PythonDashboard", "data", "mlb-fade-vs-team-watch.json")),
]
GAMELOGS = os.path.normpath(os.path.join(
    SCRIPT_DIR, "..", "..", "data", "pitcher_cache", "mlb", "game_logs_2026.json"))


_EXCLUDE_TOKENS = [toks for toks in (_norm(e).split() for e in WATCH_EXCLUDE) if toks]


def _excluded(name):
    toks = set(_norm(name).split())
    return any(all(t in toks for t in entry) for entry in _EXCLUDE_TOKENS)


def _rec(rows):
    w = sum(1 for r in rows if r[1])
    n = len(rows)
    u = round(sum(r[0] for r in rows), 2)
    staked = sum(r[2] for r in rows)
    return {"w": w, "l": n - w, "u": u, "n": n,
            "roi": round(u / staked, 4) if staked else 0.0}


def build():
    allml = json.load(open(os.path.normpath(
        os.path.join(SCRIPT_DIR, "..", "data", "mlb-all-ml.json")), encoding="utf-8"))
    settled = [g for g in allml.get("games", []) if g.get("home_win") is not None]
    today_games = allml.get("today", [])
    logs = json.load(open(GAMELOGS, encoding="utf-8"))

    # ERA vs opp, keyed by (normalized pitcher, opp). Keep a display name.
    # Dedupe by game_id: the logs occasionally repeat a start (same game_id),
    # which would double-count er/ip and inflate gs past the MIN_STARTS gate.
    era = defaultdict(lambda: {"er": 0, "ip": 0.0, "gs": 0, "name": ""})
    seen = set()
    for r in logs:
        if not r.get("is_start"):
            continue
        d = r.get("game_date")
        if d and d < SEASON_START:
            continue
        nk, opp, gid = _norm(r.get("pitcher_name")), r.get("opp"), r.get("game_id")
        if gid is not None:
            if (nk, opp, gid) in seen:
                continue
            seen.add((nk, opp, gid))
        k = (nk, opp)
        e = era[k]
        e["er"] += r.get("er") or 0
        e["ip"] += r.get("ip") or 0.0
        e["gs"] += 1
        e["name"] = r.get("pitcher_name")

    # Fade record vs opp, same key. Also remember the arm's team.
    fade = defaultdict(list)
    arm_team = {}
    for g in settled:
        d = g.get("date")
        if d and d < SEASON_START:
            continue
        for side in ("home", "away"):
            p = g.get(side + "_pitcher")
            if not p:
                continue
            oppside = "away" if side == "home" else "home"
            opp = g.get(oppside)
            odds = g.get(oppside + "_ml")
            if odds is None:
                continue
            opp_won = (not g["home_win"]) if side == "home" else g["home_win"]
            k = (_norm(p), opp)
            fade[k].append((profit_for(odds, opp_won), opp_won, stake_for(odds)))
            arm_team[k] = g.get(side)

    curated = {(_norm(n), t) for n, ts in FADE_VS_TEAM.items() for t in ts}

    cands = []
    for k, e in era.items():
        nk, opp = k
        if e["gs"] < MIN_STARTS or e["ip"] <= 0:
            continue
        era_val = round(e["er"] * 9.0 / e["ip"], 2)
        if era_val < MIN_ERA or era_val >= FADE_ERA:   # watch band [6, 8); >=8 is a fade
            continue
        if matched_entry(e["name"]):        # already faded all-venue
            continue
        if k in curated:                    # already on the curated vs-team list
            continue
        cand = {
            "pitcher": e["name"], "opp": opp, "arm_team": arm_team.get(k),
            "eraVsOpp": era_val, "ipVsOpp": round(e["ip"], 1),
            "erVsOpp": e["er"], "startsVsOpp": e["gs"],
            "fadeRecord": _rec(fade.get(k, [])),
        }
        if _excluded(e["name"]):            # user-removed: keep history, flag it
            cand["excluded"] = True
        cands.append(cand)
    # Worst ERA first (the strongest "gets hit" signal).
    cands.sort(key=lambda c: -c["eraVsOpp"])

    # Today's matchups that hit a watch candidate -> pending review play.
    cand_keys = {(_norm(c["pitcher"]), c["opp"]): c for c in cands}
    today = []
    for g in today_games:
        for side in ("home", "away"):
            p = g.get(side + "_pitcher") or ""
            oppside = "away" if side == "home" else "home"
            opp = g.get(oppside)
            c = cand_keys.get((_norm(p), opp))
            if not c or c.get("excluded"):  # removed arms never surface as plays
                continue
            today.append({
                "date": g.get("date"), "commence": g.get("commence"),
                "betType": "fade_vs_team_watch", "pitcher": p,
                "arm_team": g.get(side), "opp": opp, "selection": opp,
                "venue": side, "matchup": g.get("away") + " @ " + g.get("home"),
                "odds": g.get(oppside + "_ml"),
                "eraVsOpp": c["eraVsOpp"], "startsVsOpp": c["startsVsOpp"],
                "result": "pending",
            })
    today.sort(key=lambda x: x.get("commence") or "")

    payload = {
        "sport": "MLB", "type": "fade-vs-team-watch",
        "generated": datetime.datetime.now(datetime.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seasonStart": SEASON_START,
        "minStarts": MIN_STARTS, "minEra": MIN_ERA, "fadeEra": FADE_ERA,
        "note": "Auto-screened WATCH tier for the pitcher-vs-team fade angle: "
                ">= %d starts vs the team with %.1f <= ERA < %.1f vs them "
                "(ERA >= %.1f auto-promotes to the FADE list, mlb-fade-vs-team.json), "
                "excluding arms already faded and matchups already curated. "
                "fadeRecord is the in-sample fade result (bet the opponent), "
                "shown for review -- not a screen filter. Watch, don't bet."
                % (MIN_STARTS, MIN_ERA, FADE_ERA, FADE_ERA),
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
    print(f"[fade-vs-team-watch] {len(pay['candidates'])} candidates "
          f"(>= {MIN_STARTS} gs vs team, ERA >= {MIN_ERA}) | {len(pay['today'])} today")
    for c in pay["candidates"]:
        r = c["fadeRecord"]
        print(f"  {c['pitcher']:20s} vs {c['opp']:3s}  ERA {c['eraVsOpp']:5.2f} "
              f"({c['startsVsOpp']}gs/{c['ipVsOpp']:.0f}ip)  fade {r['w']}-{r['l']} "
              f"{r['u']:+.2f}u")
