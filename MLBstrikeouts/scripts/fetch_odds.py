#!/usr/bin/env python3
"""
MLBstrikeouts/scripts/fetch_odds.py
Pre-fetch and cache historical MLB pitcher prop odds from The Odds API.

Run this BEFORE the backfill to populate the props cache. The backfill
then reads from cache only — zero API calls during backtesting.

Each date costs: 1 (events list) + N_games (1 per-event with ALL markets
batched) API credits. Already-cached dates are FREE (skipped entirely).

Usage:
    cd MLBstrikeouts
    python -m scripts.fetch_odds --start 2026-04-01 --end 2026-04-13
    python -m scripts.fetch_odds --start 2026-04-01   # single date
    python -m scripts.fetch_odds --start 2026-04-01 --end 2026-04-13 --dry-run
"""

import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from sources.odds_theoddsapi import fetch_historical_mlb_props_batch


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Pre-fetch and cache historical MLB prop odds for backtesting"
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
        help="Seconds between date fetches (default: 1.0)"
    )
    args = parser.parse_args()

    fetch_historical_mlb_props_batch(
        start_date=args.start,
        end_date=args.end,
        dry_run=args.dry_run,
        delay=args.delay,
    )
