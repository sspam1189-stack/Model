#!/usr/bin/env python3
"""
MLBstrikeouts/scripts/run_daily_ml.py
Daily fade-list moneyline pipeline. Mirrors run_daily.py mechanics.

Stages:
  0. Grade prior bets (season .. yesterday) from the ML cache + results.
  1. Fetch today's FanDuel moneylines (direct FD API; Odds API fallback).
  2. Build today's fade plays -> pending picks (bet opponent's ML).
  3. Write mlb-fade-ml.json to both data locations.

Odds are FanDuel-only. Today's games freeze once started, so the last
pre-first-pitch run captures the closing number (same as the K model).

Usage:
    cd MLBstrikeouts
    python -m scripts.run_daily_ml
    python -m scripts.run_daily_ml --date 20260717
"""

import sys
import os
import json
import argparse
import datetime

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "sources"))

from fade_ml_common import (
    load_props_index, starts_from_rows, fade_games, match_game,
    odds_row_for, build_bets_for_game, build_payload, write_outputs, SCRIPT_DIR,
)
from ml_backfill import grade_date
from sources.mlb_schedule import fetch_schedule
from sources.odds_ml_theoddsapi import (
    load_ml_cache, save_ml_cache,
)


def _today_slate():
    """Return (date_key, date_iso, todayRows) from mlb-props.json."""
    path = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "mlb-props.json"))
    with open(path, "r") as f:
        data = json.load(f)
    date_iso = data.get("date")
    proj = data.get("todayProjections") or []
    probs = [{"player": g.get("player"), "team": g.get("team"),
              "opp": g.get("opp"), "market": "strikeouts"}
             for g in (data.get("todayProbables") or [])]
    return date_iso.replace("-", ""), date_iso, proj + probs


def _fetch_today_ml(date_key):
    """FanDuel ML rows for today: direct FD API, Odds API fallback."""
    try:
        from sources.odds_fanduel import fetch_fanduel_mlb_ml
        rows = fetch_fanduel_mlb_ml(date_key=None)
        if rows:
            return rows
    except Exception as e:
        print(f"  [ml] FanDuel API failed ({e}); falling back to Odds API")
    try:
        from sources.odds_ml_theoddsapi import fetch_mlb_ml_live
        return fetch_mlb_ml_live(date_key)
    except Exception as e:
        print(f"  [ml] Odds API live fetch failed: {e}")
        return []


def main():
    ap = argparse.ArgumentParser(description="Daily fade-list ML pipeline")
    ap.add_argument("--date", default=None, help="Slate date YYYYMMDD (default: props slate)")
    args = ap.parse_args()

    props_index = load_props_index()
    if args.date:
        date_key = args.date
        date_iso = f"{date_key[:4]}-{date_key[4:6]}-{date_key[6:8]}"
        today_rows = props_index.get(date_iso, [])
    else:
        date_key, date_iso, today_rows = _today_slate()

    # Stage 0 — grade history up to yesterday (cache-only, fast).
    dates = sorted(d for d in props_index if d and d < date_iso)
    bets = []
    for d in dates:
        bets.extend(grade_date(d.replace("-", ""), d, props_index))

    # Stage 1 — today's FanDuel MLs + totals + schedule.
    games = fetch_schedule(date_key)
    ml_rows = _fetch_today_ml(date_key)
    ml_by_matchup = {frozenset((r.get("home"), r.get("away"))): r for r in ml_rows}

    # Persist today's fade-game odds into the per-date cache (freeze rule).
    fgs = fade_games(starts_from_rows(today_rows), date_iso)
    cache_rows = []
    today = []
    for fg in fgs:
        pitcher = fg["pitchers"][0] if fg["pitchers"] else None
        g = match_game(games, fg["teams"], pitcher)
        if not g:
            continue
        started = bool(g.get("final") or (not _is_preview(g)))
        mr = ml_by_matchup.get(frozenset((g["home"], g["away"])))
        if mr:
            cache_rows.append({
                "date": date_iso, "commence": g.get("commence"),
                "home": g["home"], "away": g["away"],
                "home_ml": mr["home_ml"], "away_ml": mr["away_ml"],
                "total_line": mr.get("total_line"), "over_ml": mr.get("over_ml"),
                "under_ml": mr.get("under_ml"),
                "book": "fanduel", "source": mr.get("source", "fanduel_api"),
                "started": started,
            })
    if cache_rows:
        save_ml_cache(date_key, cache_rows, freeze_started=True)

    # Stage 2 — build today's picks (grade any that already finished).
    odds_rows = load_ml_cache(date_key) or []
    for fg in fgs:
        pitcher = fg["pitchers"][0] if fg["pitchers"] else None
        g = match_game(games, fg["teams"], pitcher)
        od = odds_row_for(odds_rows, g) if g else None
        for b in build_bets_for_game(date_iso, fg, g, od):
            if b["result"] in ("WIN", "LOSS", "VOID"):
                bets.append(b)
            else:  # pending / SKIP
                today.append(b)

    payload = build_payload(bets, today)
    write_outputs(payload)
    s = payload["summary"]
    pend = sum(1 for t in today if t["result"] == "pending")
    print(f"[fade-ml] {date_iso}: {pend} pending picks. "
          f"Season {s['wins']}-{s['losses']}, {s['units']:+.2f}u, "
          f"ROI {s['roi']*100:+.1f}%")


def _is_preview(g):
    st = (g.get("status") or "").lower()
    return "preview" in st or "scheduled" in st or "warmup" in st or "pre-game" in st


if __name__ == "__main__":
    main()
