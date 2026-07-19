#!/usr/bin/env python3
"""
MLBstrikeouts/scripts/ml_backfill.py
Walk-forward backfill of fade-list moneyline bets.

For each date, reads the fade starters (mlb-props.json), the FanDuel closing
ML (cached by fetch_ml_odds.py), and the game result (MLB schedule), then
grades each bet. Reads odds from cache only -- run fetch_ml_odds.py first.

Bet rule: fade-list pitcher starts -> bet the opponent's ML. Both starters on
the fade list -> skip (recorded as SKIP, not a bet). Voids (postponed / no
price) are excluded from the record.

Usage:
    cd MLBstrikeouts
    python -m scripts.ml_backfill
    python -m scripts.ml_backfill --start-date 2026-05-12 --end-date 2026-05-18
"""

import sys
import os
import argparse
import datetime

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "sources"))

from fade_ml_common import (
    load_props_index, starts_from_rows, fade_games, match_game,
    odds_row_for, build_bets_for_game, build_payload, write_outputs,
    load_existing,
)
from sources.mlb_schedule import fetch_schedule
from sources.odds_ml_theoddsapi import load_ml_cache


def _daterange(start, end):
    d = datetime.date.fromisoformat(start)
    last = datetime.date.fromisoformat(end)
    while d <= last:
        yield d.strftime("%Y%m%d"), d.isoformat()
        d += datetime.timedelta(days=1)


def grade_date(date_key, date_iso, props_index):
    """Return the list of graded bet records for one date."""
    rows = props_index.get(date_iso, [])
    fgs = fade_games(starts_from_rows(rows), date_iso)
    if not fgs:
        return []
    games = fetch_schedule(date_key)
    odds_rows = load_ml_cache(date_key) or []
    bets = []
    for fg in fgs:
        pitcher = fg["pitchers"][0] if fg["pitchers"] else None
        g = match_game(games, fg["teams"], pitcher)
        od = odds_row_for(odds_rows, g) if g else None
        bets.extend(build_bets_for_game(date_iso, fg, g, od))
    return bets


def main():
    ap = argparse.ArgumentParser(description="Backfill fade-list ML bets")
    ap.add_argument("--start-date", default=None,
                    help="YYYY-MM-DD (default: earliest fade date in props)")
    ap.add_argument("--end-date", default=None,
                    help="YYYY-MM-DD (default: yesterday)")
    args = ap.parse_args()

    props_index = load_props_index()
    dates = sorted(d for d in props_index if d)
    start = args.start_date or dates[0]
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    end = args.end_date or yesterday

    all_bets = []
    for date_key, date_iso in _daterange(start, end):
        all_bets.extend(grade_date(date_key, date_iso, props_index))

    # Preserve any still-pending today[] picks from the live path.
    existing = load_existing() or {}
    today = existing.get("today", [])

    payload = build_payload(all_bets, today)
    write_outputs(payload)
    s = payload["summary"]
    graded = s["wins"] + s["losses"]
    print(f"Backfill {start}..{end}: {s['wins']}-{s['losses']} "
          f"({graded} bets, {s['voids']} void), "
          f"{s['units']:+.2f}u on {s['staked']:.2f}u risked, "
          f"ROI {s['roi']*100:+.1f}%")


if __name__ == "__main__":
    main()
