#!/usr/bin/env python3
"""
pyNBAPROPS/scripts/fetch_stats.py
Fetch and cache NBA player stats + advanced stats + team defense stats.

Run this BEFORE the backtest to populate the cache. The backtest then
reads from cache only — no live API calls during backtesting.

Usage:
    cd pyNBAPROPS
    python -m scripts.fetch_stats
    python -m scripts.fetch_stats --season 2025-26
    python -m scripts.fetch_stats --season 2025-26 --date-to 2026-03-01
    python -m scripts.fetch_stats --season 2024-25 --season 2025-26   # multiple seasons
"""

import sys
import os
import time
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from sources.nba_player_stats import (
    fetch_player_game_logs,
    fetch_player_advanced_stats,
    fetch_team_def_stats,
)
from defaults import current_season


def fetch_all_stats(seasons=None, date_to=None, force=False):
    """
    Fetch and cache all stats for one or more seasons.

    Parameters
    ----------
    seasons : list[str] or None
        NBA season strings (e.g. ['2024-25', '2025-26']).
        Defaults to current season.
    date_to : str or None
        Optional date cutoff (YYYY-MM-DD). Only applies to the last season.
    force : bool
        If True, bypass cache freshness checks (re-fetch everything).
    """
    if not seasons:
        seasons = [current_season()]

    print(f"\n{'='*60}")
    print(f"  NBA STATS FETCH & CACHE")
    print(f"  Seasons: {', '.join(seasons)}")
    if date_to:
        print(f"  Date cutoff: {date_to}")
    print(f"{'='*60}")

    for i, season in enumerate(seasons):
        is_last = (i == len(seasons) - 1)
        dt = date_to if is_last else None

        print(f"\n  --- Season: {season} ---")

        # 1. Basic player game logs
        print(f"\n  [1/3] Fetching player game logs...")
        try:
            logs = fetch_player_game_logs(season=season, date_to=dt)
            print(f"  OK: {len(logs)} player-game logs cached")
        except Exception as e:
            print(f"  ERROR: {e}")

        time.sleep(2)  # Respect NBA.com rate limits

        # 2. Advanced player stats (season-to-date)
        print(f"\n  [2/3] Fetching advanced player stats (USG%, TS%, PACE, etc.)...")
        try:
            adv = fetch_player_advanced_stats(season=season, date_to=dt)
            print(f"  OK: {len(adv)} players with advanced stats cached")
        except Exception as e:
            print(f"  ERROR: {e}")

        time.sleep(2)

        # 3. Team defensive stats
        print(f"\n  [3/3] Fetching team defensive stats...")
        try:
            teams = fetch_team_def_stats(season=season, date_to=dt)
            print(f"  OK: {len(teams)} teams with defensive stats cached")
        except Exception as e:
            print(f"  ERROR: {e}")

        time.sleep(2)

    print(f"\n{'='*60}")
    print(f"  DONE — All stats cached.")
    print(f"  Cache dir: pyNBAPROPS/data/player_cache/")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Fetch and cache NBA player stats for backtesting"
    )
    parser.add_argument(
        "--season", type=str, action="append", default=None,
        help="NBA season string (e.g. '2025-26'). Can be repeated for multiple seasons."
    )
    parser.add_argument(
        "--date-to", type=str, default=None,
        help="Date cutoff in YYYY-MM-DD format (applies to last season only)"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Force re-fetch even if cache is fresh"
    )
    args = parser.parse_args()

    fetch_all_stats(
        seasons=args.season,
        date_to=args.date_to,
        force=args.force,
    )
