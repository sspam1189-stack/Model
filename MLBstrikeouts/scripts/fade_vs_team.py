#!/usr/bin/env python3
"""
MLBstrikeouts/scripts/fade_vs_team.py
Grade the MATCHUP fade list (FADE_VS_TEAM in fade_list.py): fade a pitcher --
bet the OPPONENT's moneyline -- ONLY when he starts against one specific team.

This is a "pitcher vs team" angle, separate from the venue-based fade list and
NOT wired into the fade-ML model. It just produces a record + today's matching
matchups as data (mlb-fade-vs-team.json), for review.

Reads mlb-all-ml.json (settled games + FanDuel ML + results) and grades betting
the opponent flat 1u, from SEASON_START. Also enriches each matchup with the
pitcher's ERA vs that team from the season game logs, when available.

Usage:
    cd MLBstrikeouts
    python -m scripts.fade_vs_team
"""
import sys
import os
import json
import datetime

sys.path.insert(0, os.path.dirname(__file__))

from fade_ml_common import stake_for, profit_for
from fade_list import FADE_VS_TEAM, SEASON_START, _norm

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATHS = [
    os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "mlb-fade-vs-team.json")),
    os.path.normpath(os.path.join(
        SCRIPT_DIR, "..", "..", "PythonDashboard", "data", "mlb-fade-vs-team.json")),
]
# Season per-start game logs live in the repo-root pitcher cache.
GAMELOGS = os.path.normpath(os.path.join(
    SCRIPT_DIR, "..", "..", "data", "pitcher_cache", "mlb", "game_logs_2026.json"))


def _matches(entry_name, player):
    """True if every token of ``entry_name`` appears in ``player``'s name."""
    et = _norm(entry_name).split()
    pt = set(_norm(player).split())
    return bool(et) and all(t in pt for t in et)


def _rec(rows):
    w = sum(1 for r in rows if r[1])
    n = len(rows)
    u = round(sum(r[0] for r in rows), 2)
    staked = sum(r[2] for r in rows)
    return {"w": w, "l": n - w, "u": u, "n": n,
            "roi": round(u / staked, 4) if staked else 0.0}


def _era_vs(logs, entry_name, opp):
    """(era, ip, er, starts) for ``entry_name`` vs ``opp`` from game logs, or
    (None, 0, 0, 0) if we have no starts on record."""
    er = ip = starts = 0
    for r in logs:
        if not r.get("is_start"):
            continue
        if r.get("opp") != opp:
            continue
        if not _matches(entry_name, r.get("pitcher_name") or ""):
            continue
        er += r.get("er") or 0
        ip += r.get("ip") or 0.0
        starts += 1
    era = round(er * 9.0 / ip, 2) if ip else None
    return era, round(ip, 1), er, starts


def build():
    allml = json.load(open(os.path.normpath(
        os.path.join(SCRIPT_DIR, "..", "data", "mlb-all-ml.json")), encoding="utf-8"))
    settled = [g for g in allml.get("games", []) if g.get("home_win") is not None]
    today_games = allml.get("today", [])
    try:
        logs = json.load(open(GAMELOGS, encoding="utf-8"))
    except Exception:
        logs = []

    entries = []
    all_rows = []
    for name, opp_list in FADE_VS_TEAM.items():
        for opp in opp_list:
            rows = []
            starts = []
            arm_team = None
            for g in settled:
                d = g.get("date")
                if d and d < SEASON_START:
                    continue
                for side in ("home", "away"):
                    p = g.get(side + "_pitcher") or ""
                    if not _matches(name, p):
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
            era, ip, er, gl_starts = _era_vs(logs, name, opp)
            entries.append({
                "pitcher": name, "opp": opp, "arm_team": arm_team,
                "record": _rec(rows),
                "eraVsOpp": era, "ipVsOpp": ip, "erVsOpp": er, "startsVsOpp": gl_starts,
                "starts": starts,
            })
    entries.sort(key=lambda e: -e["record"]["u"])

    # Today's matching matchups -> pending plays (bet the opponent).
    today = []
    for g in today_games:
        for side in ("home", "away"):
            p = g.get(side + "_pitcher") or ""
            for name, opp_list in FADE_VS_TEAM.items():
                if not _matches(name, p):
                    continue
                oppside = "away" if side == "home" else "home"
                opp = g.get(oppside)
                if opp not in opp_list:
                    continue
                era, ip, er, gl_starts = _era_vs(logs, name, opp)
                today.append({
                    "date": g.get("date"), "commence": g.get("commence"),
                    "betType": "fade_vs_team", "pitcher": p,
                    "arm_team": g.get(side), "opp": opp, "selection": opp,
                    "venue": side, "matchup": g.get("away") + " @ " + g.get("home"),
                    "odds": g.get(oppside + "_ml"),
                    "eraVsOpp": era, "startsVsOpp": gl_starts,
                    "result": "pending",
                })
    today.sort(key=lambda x: x.get("commence") or "")

    payload = {
        "sport": "MLB", "type": "fade-vs-team",
        "generated": datetime.datetime.now(datetime.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seasonStart": SEASON_START,
        "note": "Matchup fade -- bet the listed opponent's ML when the pitcher "
                "starts against that specific team. Manually curated; in-sample "
                "season replay (flat 1u), not wired into the fade-ML model. "
                "eraVsOpp is the pitcher's season ERA vs that team.",
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
    print(f"[fade-vs-team] {len(pay['entries'])} matchups | overall "
          f"{o['w']}-{o['l']} {o['u']:+.2f}u ({o['roi'] * 100:.1f}% ROI) "
          f"| {len(pay['today'])} today")
    for e in pay["entries"]:
        r = e["record"]
        era = "—" if e["eraVsOpp"] is None else f"{e['eraVsOpp']:.2f}"
        print(f"  {e['pitcher']:16s} vs {e['opp']:3s}  {r['w']}-{r['l']} "
              f"{r['u']:+.2f}u {r['roi'] * 100:+.1f}%  | ERA vs {e['opp']}: {era} "
              f"({e['startsVsOpp']} gs)")
