#!/usr/bin/env python3
"""
Pre-fetch and cache all per-date data needed for walk-forward backfill.

Run this ONCE before props_backfill.py so the backfill runs entirely from
cache — no API calls during the walk-forward loop.

Caches:
  1. Game logs + batting orders (single pass over all boxscores)
  2. Player bat sides (single bulk API call)
  3. Lineup handedness per date (computed from cached batting orders)
  4. Weather per date (1 API call per date)
  5. Pitcher handedness splits (1 API call per pitcher)
  6. Advanced stats, sabermetrics, team batting, team pitching (already cached)

Usage:
    cd MLBstrikeouts
    python -m scripts.prefetch_backfill_data
    python -m scripts.prefetch_backfill_data --season 2026
"""

import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from sources.mlb_stats import (
    fetch_pitcher_game_logs, fetch_pitcher_advanced_stats,
    fetch_pitcher_sabermetrics, fetch_team_batting_stats,
    fetch_team_pitching_stats, fetch_player_bat_sides,
    fetch_lineup_handedness, fetch_pitcher_handedness_splits,
    CACHE_DIR,
)
from sources.weather import fetch_game_weather
from defaults import current_season


def prefetch(season=None):
    season = season or current_season()
    print(f"\n{'='*60}")
    print(f"  PRE-FETCH BACKFILL DATA — Season {season}")
    print(f"{'='*60}")

    # 1. Game logs (also extracts + caches batting orders per date)
    print(f"\n  [1/7] Fetching game logs + batting orders...")
    all_logs = fetch_pitcher_game_logs(season=season)
    if not all_logs:
        print("  No game logs found.")
        return

    all_dates = sorted(set(
        g.get("game_date", "") for g in all_logs if g.get("game_date")
    ))
    all_pitcher_ids = set()
    for g in all_logs:
        pid = g.get("pitcher_id") or g.get("player_id")
        if pid:
            all_pitcher_ids.add(pid)
    print(f"  {len(all_logs)} starts, {len(all_dates)} dates, {len(all_pitcher_ids)} pitchers")

    # 2. Player bat sides (single API call)
    print(f"\n  [2/7] Fetching player bat sides...")
    bat_sides = fetch_player_bat_sides(season=season)
    print(f"  {len(bat_sides)} players")

    # 3. Lineup handedness per date (from cached batting orders — no API calls)
    print(f"\n  [3/7] Computing lineup handedness for {len(all_dates)} dates...")
    cached_count = 0
    computed_count = 0
    for d in all_dates:
        cache_path = CACHE_DIR / f"lineup_handedness_{d}.json"
        if cache_path.exists():
            cached_count += 1
            continue
        lineup = fetch_lineup_handedness(d, bat_sides=bat_sides, season=season)
        computed_count += 1
    print(f"  {computed_count} computed, {cached_count} already cached")

    # 4. Weather per date
    print(f"\n  [4/7] Fetching weather for {len(all_dates)} dates...")
    cached_count = 0
    fetched_count = 0
    for d in all_dates:
        cache_key = d.replace("-", "")
        cache_path = CACHE_DIR / f"weather_{cache_key}.json"
        if cache_path.exists():
            cached_count += 1
            continue
        fetch_game_weather(d)
        fetched_count += 1
    print(f"  {fetched_count} fetched, {cached_count} already cached")

    # 5. Pitcher handedness splits
    print(f"\n  [5/7] Fetching pitcher splits for {len(all_pitcher_ids)} pitchers...")
    cached_count = 0
    fetched_count = 0
    for pid in sorted(all_pitcher_ids):
        cache_path = CACHE_DIR / f"handedness_{pid}_{season}.json"
        if cache_path.exists():
            cached_count += 1
            continue
        fetch_pitcher_handedness_splits(pid, season=season)
        fetched_count += 1
        if fetched_count % 20 == 0:
            print(f"    {fetched_count} fetched...")
    print(f"  {fetched_count} fetched, {cached_count} already cached")

    # 6. Season-level stats (usually already cached)
    print(f"\n  [6/7] Ensuring season stats are cached...")
    adv = fetch_pitcher_advanced_stats(season=season)
    print(f"  Advanced stats: {len(adv)} pitchers")
    sab = fetch_pitcher_sabermetrics(season=season)
    print(f"  Sabermetrics: {len(sab)} pitchers")
    tb = fetch_team_batting_stats(season=season)
    print(f"  Team batting: {len(tb)} teams")
    tp = fetch_team_pitching_stats(season=season)
    print(f"  Team pitching: {len(tp)} teams")

    # 7. Summary
    print(f"\n  [7/7] Verifying cache completeness...")
    import glob
    weather_files = glob.glob(str(CACHE_DIR / "weather_*.json"))
    lineup_files = glob.glob(str(CACHE_DIR / "lineup_handedness_*.json"))
    split_files = glob.glob(str(CACHE_DIR / f"handedness_*_{season}.json"))
    odds_files = glob.glob(
        str(CACHE_DIR.parent.parent / "props_cache" / "mlb" / "mlb_props_*.json")
    )

    print(f"\n  {'='*50}")
    print(f"  CACHE STATUS")
    print(f"  {'='*50}")
    print(f"  Game logs:         {'YES':>5}")
    print(f"  Batting orders:    {'YES':>5}")
    print(f"  Player bat sides:  {len(bat_sides):>5} players")
    print(f"  Lineup handedness: {len(lineup_files):>5} / {len(all_dates)} dates")
    print(f"  Weather:           {len(weather_files):>5} / {len(all_dates)} dates")
    print(f"  Pitcher splits:    {len(split_files):>5} / {len(all_pitcher_ids)} pitchers")
    print(f"  Advanced stats:    {len(adv):>5} pitchers")
    print(f"  Sabermetrics:      {len(sab):>5} pitchers")
    print(f"  Team batting:      {len(tb):>5} teams")
    print(f"  Team pitching:     {len(tp):>5} teams")
    print(f"  Odds (prop lines): {len(odds_files):>5} / {len(all_dates)} dates")
    print(f"  {'='*50}")
    print(f"  Ready for backfill: python -m scripts.props_backfill")
    print(f"  {'='*50}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, default=None)
    args = parser.parse_args()
    prefetch(args.season)
