#!/usr/bin/env python3
"""
Re-fetch NFL player prop odds for all cached weeks with expanded markets.

Backs up existing cache, re-fetches with all 9 markets, and merges
(keeps existing lines + adds new market lines).

Usage:
    cd pyNFL
    ODDS_API_KEY=xxx python scripts/refetch_props_cache.py
"""

import os
import sys
import json
import time
import shutil
import requests
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))
from sources.odds_theoddsapi import PROP_MARKETS, _MARKET_MAP

BASE = "https://api.the-odds-api.com/v4"
SPORT_KEY = "americanfootball_nfl"
PROPS_DIR = Path(__file__).resolve().parents[1] / "data" / "props"
BACKUP_DIR = PROPS_DIR / "backup_4markets"

# NFL season start dates (approximate Thursday of Week 1)
SEASON_STARTS = {
    2023: "2023-09-07",
    2024: "2024-09-05",
    2025: "2025-09-04",
}


def _pick_best_bookmaker(bookmakers):
    preferred = ["FanDuel", "DraftKings", "BetMGM", "Caesars", "PointsBet", "BetRivers"]
    for p in preferred:
        b = next((x for x in bookmakers if x and x.get("title") == p), None)
        if b:
            return b
    return bookmakers[0] if bookmakers else None


def _week_to_date(season, week):
    """Convert season + week number to approximate game date (Sunday)."""
    start = datetime.strptime(SEASON_STARTS[season], "%Y-%m-%d")
    # Week 1 starts on that Thursday, Sunday is +3 days
    sunday = start + timedelta(days=3) + timedelta(weeks=week - 1)
    return sunday.strftime("%Y-%m-%d")


def _fetch_with_retry(url, max_retries=5):
    for attempt in range(1, max_retries + 1):
        try:
            res = requests.get(url, timeout=30)
            if res.status_code == 200:
                return res
            if res.status_code == 429:
                wait = min(2 ** attempt, 30)
                print(f"    429 rate limit, waiting {wait}s...")
                time.sleep(wait)
                continue
            if res.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            return res
        except Exception as e:
            if attempt == max_retries:
                raise
            time.sleep(2 ** attempt)
    return None


def fetch_week_props(season, week, api_key):
    """Fetch all prop lines for a given NFL season/week."""
    date_str = _week_to_date(season, week)
    ts_utc = f"{date_str}T17:00:00Z"

    markets_str = ",".join(PROP_MARKETS)

    # Step 1: Get historical events
    events_url = (
        f"{BASE}/historical/sports/{SPORT_KEY}/events?"
        f"apiKey={quote(api_key)}"
        f"&date={quote(ts_utc)}"
    )
    res = _fetch_with_retry(events_url)
    if res is None or res.status_code != 200:
        print(f"    Events fetch failed for {date_str}")
        return []

    events_data = res.json().get("data", [])
    if not events_data:
        return []

    all_props = []
    for ev in events_data:
        event_id = ev.get("id")
        home = ev.get("home_team", "")
        away = ev.get("away_team", "")

        url = (
            f"{BASE}/historical/sports/{SPORT_KEY}/events/{event_id}/odds?"
            f"apiKey={quote(api_key)}"
            f"&regions=us"
            f"&markets={markets_str}"
            f"&oddsFormat=american"
            f"&date={quote(ts_utc)}"
        )

        try:
            r = _fetch_with_retry(url)
            if r is None or r.status_code != 200:
                continue
            data = r.json().get("data", {})
        except Exception:
            continue

        bookmakers = data.get("bookmakers", [])
        if not bookmakers:
            continue

        book = _pick_best_bookmaker(bookmakers)
        if not book:
            continue

        for market in book.get("markets", []):
            market_key = market.get("key", "")
            internal_market = _MARKET_MAP.get(market_key)
            if not internal_market:
                continue

            outcomes = market.get("outcomes", [])
            player_lines = {}
            for o in outcomes:
                desc = o.get("description", "")
                name = o.get("name", "")
                point = o.get("point")
                price = o.get("price")
                if desc and point is not None:
                    if desc not in player_lines:
                        player_lines[desc] = {"player": desc, "line": point}
                    if name == "Over":
                        player_lines[desc]["over_price"] = price
                    elif name == "Under":
                        player_lines[desc]["under_price"] = price

            for pl in player_lines.values():
                all_props.append({
                    **pl,
                    "market": internal_market,
                    "event_home": home,
                    "event_away": away,
                })

        time.sleep(0.3)

    return all_props


def main():
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        print("ERROR: Set ODDS_API_KEY environment variable")
        return

    # Find all existing cache files
    cache_files = sorted(PROPS_DIR.glob("nfl_props_*_W*.json"))
    # Parse season/week from filenames
    import re
    weeks_to_fetch = []
    for f in cache_files:
        m = re.match(r"nfl_props_(\d+)_W(\d+)", f.name)
        if m:
            season = int(m.group(1))
            week = int(m.group(2))
            if season in SEASON_STARTS:
                weeks_to_fetch.append((season, week, f))

    weeks_to_fetch.sort()
    print(f"Re-fetching {len(weeks_to_fetch)} weeks with {len(PROP_MARKETS)} markets")
    print(f"Markets: {', '.join(PROP_MARKETS)}")

    # Backup existing cache
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    for _, _, f in weeks_to_fetch:
        backup_path = BACKUP_DIR / f.name
        if not backup_path.exists():
            shutil.copy2(f, backup_path)
    print(f"Backed up {len(weeks_to_fetch)} files to {BACKUP_DIR}")

    fetched = 0
    skipped = 0
    errors = 0

    for season, week, cache_file in weeks_to_fetch:
        # Check if already has new markets
        try:
            with open(cache_file) as fh:
                existing = json.load(fh)
            existing_markets = set(p["market"] for p in existing)
            new_markets = {"pass_tds", "pass_att", "completions", "rush_att", "interceptions"}
            if new_markets & existing_markets:
                print(f"  S{season} W{week:2d}: SKIP (already has new markets)")
                skipped += 1
                continue
        except Exception:
            existing = []

        print(f"  S{season} W{week:2d}: fetching...", end=" ", flush=True)
        try:
            props = fetch_week_props(season, week, api_key)
            if props:
                # Merge: keep existing lines, add new market lines
                existing_keys = set()
                for p in existing:
                    key = (p["player"], p["market"])
                    existing_keys.add(key)

                merged = list(existing)
                new_count = 0
                for p in props:
                    key = (p["player"], p["market"])
                    if key not in existing_keys:
                        merged.append(p)
                        new_count += 1

                with open(cache_file, "w") as fh:
                    json.dump(merged, fh, indent=2)

                new_mkts = set(p["market"] for p in props) - set(p["market"] for p in existing)
                print(f"{len(props)} lines ({new_count} new), markets: {sorted(new_mkts)}")
                fetched += 1
            else:
                print("no data")
        except Exception as e:
            print(f"ERROR: {e}")
            errors += 1

        time.sleep(1)

    print(f"\nDone: {fetched} fetched, {skipped} skipped, {errors} errors")


if __name__ == "__main__":
    main()
