#!/usr/bin/env python3
"""
pyNBAPROPS/scripts/fetch_odds.py
Fetch and cache historical NBA player prop odds from The Odds API.

Run this BEFORE the backtest to populate the props cache. The backtest
then reads from cache only — no Odds API calls during backtesting.

Each date costs API credits, so this script:
  - Checks the cache first (skips dates already fetched)
  - Adds a small delay between date fetches to avoid rate limiting
  - Supports date ranges for bulk historical fetching

Usage:
    cd pyNBAPROPS
    python -m scripts.fetch_odds --start 2026-01-01 --end 2026-03-25
    python -m scripts.fetch_odds --start 2026-01-01   # single date
    python -m scripts.fetch_odds --start 2026-01-01 --end 2026-03-25 --dry-run
"""

import sys
import os
import time
import argparse
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from sources.odds_theoddsapi import fetch_historical_wnba_props, _props_cache_path, _load_cache


def fetch_odds_range(start_date, end_date=None, dry_run=False, delay=1.0):
    """
    Fetch and cache historical NBA prop odds for a date range.

    Parameters
    ----------
    start_date : str
        Start date (YYYY-MM-DD).
    end_date : str or None
        End date (YYYY-MM-DD). Defaults to start_date (single day).
    dry_run : bool
        If True, only show what would be fetched without making API calls.
    delay : float
        Seconds between API calls per date.
    """
    if end_date is None:
        end_date = start_date

    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    if end < start:
        print("  ERROR: end date is before start date")
        return

    total_days = (end - start).days + 1

    print(f"\n{'='*60}")
    print(f"  NBA PROP ODDS FETCH & CACHE")
    print(f"  Range: {start_date} to {end_date} ({total_days} days)")
    if dry_run:
        print(f"  MODE: DRY RUN (no API calls)")
    print(f"{'='*60}")

    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key and not dry_run:
        print("\n  ERROR: ODDS_API_KEY environment variable not set.")
        print("  Set it with: export ODDS_API_KEY=your_key_here")
        return

    cached_count = 0
    fetched_count = 0
    empty_count = 0
    error_count = 0

    current = start
    while current <= end:
        date_str = current.strftime("%Y-%m-%d")
        date_key = current.strftime("%Y%m%d")

        # Check if already cached
        cp = _props_cache_path(date_key)
        cached = _load_cache(cp, max_age_hours=None)

        if cached is not None:
            print(f"  {date_str}: CACHED ({len(cached)} lines)")
            cached_count += 1
            current += timedelta(days=1)
            continue

        if dry_run:
            print(f"  {date_str}: WOULD FETCH (not cached)")
            current += timedelta(days=1)
            continue

        # Fetch from API
        try:
            props = fetch_historical_wnba_props(date_str, api_key=api_key)
            if props:
                fetched_count += 1
                print(f"  {date_str}: FETCHED {len(props)} prop lines")
            else:
                empty_count += 1
                print(f"  {date_str}: No props found (no games or no lines)")
        except Exception as e:
            error_count += 1
            print(f"  {date_str}: ERROR — {e}")

        time.sleep(delay)
        current += timedelta(days=1)

    # Summary
    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"  Already cached: {cached_count}")
    print(f"  Newly fetched:  {fetched_count}")
    print(f"  Empty dates:    {empty_count}")
    if error_count:
        print(f"  Errors:         {error_count}")
    print(f"  Cache dir: data/props_cache/nba/")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Fetch and cache historical NBA prop odds for backtesting"
    )
    parser.add_argument(
        "--start", type=str, required=True,
        help="Start date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--end", type=str, default=None,
        help="End date (YYYY-MM-DD). Defaults to start date."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be fetched without making API calls"
    )
    parser.add_argument(
        "--delay", type=float, default=1.0,
        help="Seconds between API calls (default: 1.0)"
    )
    args = parser.parse_args()

    fetch_odds_range(
        start_date=args.start,
        end_date=args.end,
        dry_run=args.dry_run,
        delay=args.delay,
    )
