#!/usr/bin/env python3
"""
MLBstrikeouts/scripts/build_all_ml.py
Build the "MLB ALL ML" dataset: every game this season with its FanDuel
closing moneylines, both starters + their throwing hand, and the result.

Powers the dashboard's "MLB ALL ML" tab, which pivots each game into two
team-side views so you can filter by team, home/away, favorite/underdog, and
opposing-starter handedness (any combination) and grade betting that team's ML.

Reads from cache only (schedule + ML odds already cached); the sole network
call is one MLB roster fetch for pitcher handedness. Re-runnable and cheap.

Usage:
    cd MLBstrikeouts
    python -m scripts.build_all_ml
    python -m scripts.build_all_ml --start 2026-04-05 --end 2026-07-20
"""

import sys
import os
import json
import re
import argparse
import datetime
import unicodedata

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "sources"))

import requests
from fade_ml_common import SCRIPT_DIR, odds_row_for
from sources.mlb_schedule import fetch_schedule
from sources.odds_ml_theoddsapi import load_ml_cache

SEASON_START = "2026-03-25"  # MLB 2026 Opening Day (regular season); spring
                             # training ended 3/24, so the schedule cache from
                             # 3/25 on is regular-season games only.
STATS_API = "https://statsapi.mlb.com/api/v1"

OUTPUT_PATHS = [
    os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "mlb-all-ml.json")),
    os.path.normpath(os.path.join(
        SCRIPT_DIR, "..", "..", "PythonDashboard", "data", "mlb-all-ml.json")),
]


def _norm(s):
    s = unicodedata.normalize("NFD", (s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z ]", " ", s).strip()


def handedness_map(season):
    """name(normalized) -> 'L'/'R' for every pitcher active this season.

    One MLB Stats API call for the whole player pool.
    """
    url = f"{STATS_API}/sports/1/players?season={season}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    out = {}
    for p in r.json().get("people", []):
        code = (p.get("pitchHand") or {}).get("code")
        if code:
            out[_norm(p.get("fullName"))] = code
    return out


def _slate_date():
    """Canonical 'today' = the current props slate date (matches Fade tab)."""
    path = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "mlb-props.json"))
    try:
        with open(path, "r") as f:
            return json.load(f).get("date")
    except Exception:
        return None


def _daterange(start, end):
    d = datetime.date.fromisoformat(start)
    last = datetime.date.fromisoformat(end)
    while d <= last:
        yield d.strftime("%Y%m%d")
        d += datetime.timedelta(days=1)


def main():
    ap = argparse.ArgumentParser(description="Build the ALL-ML dataset")
    ap.add_argument("--start", default=SEASON_START, help="YYYY-MM-DD")
    ap.add_argument("--end", default=None, help="YYYY-MM-DD (default: today UTC)")
    args = ap.parse_args()
    start = args.start
    end = args.end or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    season = start[:4]

    hand = handedness_map(season)
    games = []
    no_price = graded = 0
    for date_key in _daterange(start, end):
        # Use the schedule's official local date (the date_key we fetched
        # under), not commence[:10]: a UTC date rolls West-coast night games to
        # the next day and mis-buckets the day/week/month filters.
        date_iso = f"{date_key[:4]}-{date_key[4:6]}-{date_key[6:8]}"
        sched = fetch_schedule(date_key) or []
        odds_rows = load_ml_cache(date_key) or []
        for g in sched:
            if not g.get("home") or not g.get("away"):
                continue  # skip non-team events (e.g. the All-Star Game: AL/NL)
            if g.get("void") or not g.get("final") or g.get("home_win") is None:
                continue  # only settled games are gradeable
            od = odds_row_for(odds_rows, g)
            if not od or od.get("home_ml") is None or od.get("away_ml") is None:
                no_price += 1
                continue
            games.append({
                "date": date_iso,
                "commence": g.get("commence"),
                "gamePk": g.get("gamePk"),
                "home": g["home"], "away": g["away"],
                "home_ml": od["home_ml"], "away_ml": od["away_ml"],
                "home_pitcher": g.get("home_pitcher"),
                "away_pitcher": g.get("away_pitcher"),
                "home_hand": hand.get(_norm(g.get("home_pitcher"))),
                "away_hand": hand.get(_norm(g.get("away_pitcher"))),
                "home_win": g["home_win"],
            })
            graded += 1

    games.sort(key=lambda x: (x["date"], x.get("commence") or ""))

    # Today's slate (all games + cached odds), graded if already final. Odds
    # come from the same FanDuel cache the Fade pipeline populates each run.
    today = []
    today_iso = _slate_date()
    if today_iso:
        dk = today_iso.replace("-", "")
        odds_rows = load_ml_cache(dk) or []
        for g in fetch_schedule(dk) or []:
            if not g.get("home") or not g.get("away"):
                continue
            od = odds_row_for(odds_rows, g)
            if not od or od.get("home_ml") is None or od.get("away_ml") is None:
                continue
            today.append({
                "date": today_iso, "commence": g.get("commence"),
                "gamePk": g.get("gamePk"),
                "home": g["home"], "away": g["away"],
                "home_ml": od["home_ml"], "away_ml": od["away_ml"],
                "total_line": od.get("total_line"),
                "home_pitcher": g.get("home_pitcher"),
                "away_pitcher": g.get("away_pitcher"),
                "home_hand": hand.get(_norm(g.get("home_pitcher"))),
                "away_hand": hand.get(_norm(g.get("away_pitcher"))),
                "home_win": g.get("home_win"),  # None until final
                "final": bool(g.get("final")),
            })
        today.sort(key=lambda x: x.get("commence") or "")

    payload = {
        "sport": "MLB", "type": "all-ml",
        "generated": datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seasonStart": start,
        "today": today,
        "games": games,
    }
    for path in OUTPUT_PATHS:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(payload, f, indent=2)

    unresolved = sum(1 for g in games
                     if g["home_hand"] is None or g["away_hand"] is None)
    print(f"ALL-ML {start}..{end}: {graded} settled games written "
          f"({no_price} skipped no-price). Starters w/o hand: {unresolved}.")


if __name__ == "__main__":
    main()
