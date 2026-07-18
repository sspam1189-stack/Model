#!/usr/bin/env python3
"""
MLBstrikeouts/scripts/fetch_ml_odds.py
Pre-fetch and cache FanDuel closing moneylines for fade-list games.

Mirrors fetch_odds.py: run this BEFORE ml_backfill.py to populate the
per-date ML cache (data/odds_cache/mlb_ml/mlb_ml_<date>.json). The backfill
then reads from cache only -- zero API calls.

Only games with a fade-list starter are fetched (the bet universe). Each such
game costs ~10 Odds API credits for one closing snapshot; already-cached
games are FREE (skipped).

Usage:
    cd MLBstrikeouts
    python -m scripts.fetch_ml_odds --start 2026-04-05 --end 2026-07-16
    python -m scripts.fetch_ml_odds --start 2026-05-15            # single date
    python -m scripts.fetch_ml_odds --start 2026-04-05 --end 2026-07-16 --dry-run
"""

import sys
import os
import time
import argparse
import datetime

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "sources"))

from fade_ml_common import (
    load_props_index, starts_from_rows, fade_games, match_game,
)
from sources.mlb_schedule import fetch_schedule
from sources.odds_ml_theoddsapi import (
    load_ml_cache, save_ml_cache, historical_closing_odds, _row_key,
)


def _daterange(start, end):
    d = datetime.date.fromisoformat(start)
    last = datetime.date.fromisoformat(end)
    while d <= last:
        yield d.strftime("%Y%m%d"), d.isoformat()
        d += datetime.timedelta(days=1)


def main():
    ap = argparse.ArgumentParser(
        description="Pre-fetch FanDuel closing MLs for fade-list games")
    ap.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    ap.add_argument("--end", default=None, help="End date (default: start)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--delay", type=float, default=0.5,
                    help="Seconds between snapshot calls (default 0.5)")
    args = ap.parse_args()
    end = args.end or args.start

    props_index = load_props_index()
    total_fetched = total_cached = total_missing = 0

    for date_key, date_iso in _daterange(args.start, end):
        rows = props_index.get(date_iso, [])
        fgs = fade_games(starts_from_rows(rows), date_iso)  # single + mutual
        if not fgs:
            continue
        games = fetch_schedule(date_key)
        cache = load_ml_cache(date_key) or []
        cached_keys = {_row_key(r) for r in cache}

        new_rows = []
        for fg in fgs:
            pitcher = fg["pitchers"][0] if fg["pitchers"] else None
            g = match_game(games, fg["teams"], pitcher)
            if not g or not g.get("commence"):
                total_missing += 1
                print(f"  [{date_iso}] no schedule game for {'/'.join(fg['pitchers'])}")
                continue
            key = (g["away"], g["home"], g["commence"])
            if key in cached_keys:
                total_cached += 1
                continue
            tag = "MUTUAL " if fg["mutual"] else ""
            if args.dry_run:
                print(f"  [{date_iso}] WOULD fetch {tag}{g['away']}@{g['home']} "
                      f"({g['commence']}) for {'/'.join(fg['pitchers'])}")
                total_fetched += 1
                continue
            row = historical_closing_odds(g["commence"], g["home"], g["away"])
            if not row:
                total_missing += 1
                print(f"  [{date_iso}] no FanDuel odds {g['away']}@{g['home']}")
                continue
            row.update({
                "date": date_iso, "commence": g["commence"],
                "home": g["home"], "away": g["away"], "started": True,
            })
            new_rows.append(row)
            cached_keys.add(key)
            total_fetched += 1
            tl = row.get("total_line")
            print(f"  [{date_iso}] {tag}{g['away']} {row['away_ml']:+d} @ "
                  f"{g['home']} {row['home_ml']:+d}  O/U {tl}  "
                  f"({'/'.join(fg['pitchers'])})")
            time.sleep(args.delay)

        if new_rows and not args.dry_run:
            save_ml_cache(date_key, new_rows, freeze_started=False)

    print(f"\nDone. fetched={total_fetched} cached={total_cached} "
          f"missing={total_missing}")


if __name__ == "__main__":
    main()
