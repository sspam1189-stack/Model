#!/usr/bin/env python3
"""
MLBstrikeouts/scripts/fade_vs_team.py
Grade the "pitcher vs specific team" FADE list: fade a pitcher -- bet the
OPPONENT's moneyline -- when he starts against one specific team.

The fade set is the UNION of:
  * curated matchups (FADE_VS_TEAM in fade_list.py) -- hand-picked, always faded;
  * auto-promoted matchups -- any pitcher with >= MIN_STARTS starts vs a team
    and an ERA >= FADE_ERA against them (the top of the watch ladder). Arms
    already faded all-venue on FADE_LIST are skipped (already covered).

The parallel watch tier (6 <= ERA < FADE_ERA) lives in fade_vs_team_watch.py,
so a matchup is on exactly one of the two lists.

Data only (writes mlb-fade-vs-team.json); not wired into the fade-ML model.
ERA comes from the season per-start game logs; results/odds from mlb-all-ml.json.

Usage:
    cd MLBstrikeouts
    python -m scripts.fade_vs_team
"""
import sys
import os
import json
import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))

from fade_ml_common import stake_for, profit_for
from fade_list import _norm, matched_entry, FADE_VS_TEAM, SEASON_START

MIN_STARTS = 2      # a matchup needs at least this many starts vs the team
FADE_ERA = 8.0      # ...and ERA >= this vs that team to AUTO-promote to a fade

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATHS = [
    os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "mlb-fade-vs-team.json")),
    os.path.normpath(os.path.join(
        SCRIPT_DIR, "..", "..", "PythonDashboard", "data", "mlb-fade-vs-team.json")),
]
GAMELOGS = os.path.normpath(os.path.join(
    SCRIPT_DIR, "..", "..", "data", "pitcher_cache", "mlb", "game_logs_2026.json"))


def _rec(rows):
    w = sum(1 for r in rows if r[1])
    n = len(rows)
    u = round(sum(r[0] for r in rows), 2)
    staked = sum(r[2] for r in rows)
    return {"w": w, "l": n - w, "u": u, "n": n,
            "roi": round(u / staked, 4) if staked else 0.0}


def _era_index(logs):
    """(normalized pitcher, opp) -> {er, ip, gs, name} from starts, season only."""
    era = defaultdict(lambda: {"er": 0, "ip": 0.0, "gs": 0, "name": ""})
    for r in logs:
        if not r.get("is_start"):
            continue
        d = r.get("game_date")
        if d and d < SEASON_START:
            continue
        e = era[(_norm(r.get("pitcher_name")), r.get("opp"))]
        e["er"] += r.get("er") or 0
        e["ip"] += r.get("ip") or 0.0
        e["gs"] += 1
        e["name"] = r.get("pitcher_name")
    return era


def build():
    allml = json.load(open(os.path.normpath(
        os.path.join(SCRIPT_DIR, "..", "data", "mlb-all-ml.json")), encoding="utf-8"))
    settled = [g for g in allml.get("games", []) if g.get("home_win") is not None]
    today_games = allml.get("today", [])
    try:
        logs = json.load(open(GAMELOGS, encoding="utf-8"))
    except Exception:
        logs = []
    era = _era_index(logs)

    def era_for(nk, opp):
        e = era.get((nk, opp))
        if not e or e["ip"] <= 0:
            return None, 0.0, 0, 0
        return round(e["er"] * 9.0 / e["ip"], 2), round(e["ip"], 1), e["er"], e["gs"]

    # Fade set keyed by (normalized name, opp) -> {"name", "opp", "source"}.
    fadeset = {}
    for name, opp_list in FADE_VS_TEAM.items():          # curated
        for opp in opp_list:
            fadeset[(_norm(name), opp)] = {"name": name, "opp": opp, "source": "curated"}
    for (nk, opp), e in era.items():                     # auto-promoted (ERA >= FADE_ERA)
        if e["gs"] < MIN_STARTS or e["ip"] <= 0:
            continue
        if e["er"] * 9.0 / e["ip"] < FADE_ERA:
            continue
        if matched_entry(e["name"]):                     # already faded all-venue
            continue
        key = (nk, opp)
        if key in fadeset:
            fadeset[key]["source"] = "both"
        else:
            fadeset[key] = {"name": e["name"], "opp": opp, "source": "auto"}

    entries = []
    all_rows = []
    for (nk, opp), meta in fadeset.items():
        rows = []
        starts = []
        arm_team = None
        for g in settled:
            d = g.get("date")
            if d and d < SEASON_START:
                continue
            for side in ("home", "away"):
                p = g.get(side + "_pitcher") or ""
                if _norm(p) != nk:
                    continue
                oppside = "away" if side == "home" else "home"
                if g.get(oppside) != opp:
                    continue
                odds = g.get(oppside + "_ml")
                if odds is None:
                    continue
                opp_won = (not g["home_win"]) if side == "home" else g["home_win"]
                rows.append((profit_for(odds, opp_won), opp_won, stake_for(odds)))
                arm_team = g.get(side)
                starts.append({
                    "date": d, "venue": side,
                    "matchup": g.get("away") + " @ " + g.get("home"),
                    "selection": opp, "opp_ml": odds,
                    "result": "win" if opp_won else "loss",
                })
        all_rows.extend(rows)
        era_val, ip, er, gs = era_for(nk, opp)
        entries.append({
            "pitcher": meta["name"], "opp": opp, "arm_team": arm_team,
            "source": meta["source"], "record": _rec(rows),
            "eraVsOpp": era_val, "ipVsOpp": ip, "erVsOpp": er, "startsVsOpp": gs,
            "starts": starts,
        })
    entries.sort(key=lambda e: -e["record"]["u"])

    fade_keys = set(fadeset)
    today = []
    for g in today_games:
        for side in ("home", "away"):
            p = g.get(side + "_pitcher") or ""
            oppside = "away" if side == "home" else "home"
            opp = g.get(oppside)
            if (_norm(p), opp) not in fade_keys:
                continue
            era_val, ip, er, gs = era_for(_norm(p), opp)
            today.append({
                "date": g.get("date"), "commence": g.get("commence"),
                "betType": "fade_vs_team", "pitcher": p,
                "arm_team": g.get(side), "opp": opp, "selection": opp,
                "venue": side, "matchup": g.get("away") + " @ " + g.get("home"),
                "odds": g.get(oppside + "_ml"),
                "eraVsOpp": era_val, "startsVsOpp": gs,
                "result": "pending",
            })
    today.sort(key=lambda x: x.get("commence") or "")

    payload = {
        "sport": "MLB", "type": "fade-vs-team",
        "generated": datetime.datetime.now(datetime.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seasonStart": SEASON_START,
        "minStarts": MIN_STARTS, "fadeEra": FADE_ERA,
        "note": "Pitcher-vs-team FADE: bet the opponent's ML when the pitcher "
                "faces that team. Set = curated (FADE_VS_TEAM) + auto-promoted "
                "(>= %d starts vs the team, ERA >= %.1f vs them). In-sample "
                "season replay (flat 1u); data only, not wired into the fade "
                "model. Watch tier (6 <= ERA < %.1f) is in "
                "mlb-fade-vs-team-watch.json." % (MIN_STARTS, FADE_ERA, FADE_ERA),
        "overall": _rec(all_rows),
        "today": today,
        "entries": entries,
    }
    for path in OUTPUT_PATHS:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(payload, f, indent=2)
    return payload


if __name__ == "__main__":
    pay = build()
    o = pay["overall"]
    print(f"[fade-vs-team] {len(pay['entries'])} fade matchups (curated + ERA>={FADE_ERA}) "
          f"| overall {o['w']}-{o['l']} {o['u']:+.2f}u ({o['roi'] * 100:.1f}% ROI) "
          f"| {len(pay['today'])} today")
    for e in pay["entries"]:
        r = e["record"]
        era = "—" if e["eraVsOpp"] is None else f"{e['eraVsOpp']:.2f}"
        print(f"  {e['pitcher']:18s} vs {e['opp']:3s}  {r['w']}-{r['l']} {r['u']:+.2f}u "
              f"| ERA {era} ({e['startsVsOpp']}gs) [{e['source']}]")
