#!/usr/bin/env python3
"""
One-time migration: upgrade old-format stats_cache files to enhanced format.

Old format: { "Team Name": { OFF, DEF, ... }, ... }
New format: { "season": {...}, "last10": {...}, "home": {...}, "away": {...} }

Re-fetches from NBA.com with DateTo to get point-in-time last10/home/away splits.
Overwrites the cache file in-place with the enhanced format.

Usage:
  python scripts/migrate_stats_cache.py                    # migrate all old files
  python scripts/migrate_stats_cache.py --dry-run          # just list what needs migration
"""

import json
import os
import sys
import time
from pathlib import Path

# Add pyNBA scripts to path for nba_stats module
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pyNBA" / "scripts"))
sys.path.insert(0, str(ROOT / "pyNBA" / "scripts" / "sources"))

from nba_stats import fetch_nba_stats_enhanced

CACHE_DIRS = [
    ROOT / "NBA" / "data" / "stats_cache",
    ROOT / "pyNBA" / "data" / "stats_cache",
    ROOT / "jsFull" / "data" / "stats_cache",
    ROOT / "pyFull" / "data" / "stats_cache",
]

# NBA.com rate limits — wait between requests
DELAY_BETWEEN_FETCHES = 8  # seconds


def is_old_format(filepath):
    """Check if a cache file is in the old (non-enhanced) format."""
    try:
        data = json.loads(filepath.read_text(encoding="utf-8"))
        return "season" not in data
    except Exception:
        return False


def date_from_filename(filename):
    """Convert '20260101.json' to '2026-01-01'."""
    name = filename.replace(".json", "")
    return f"{name[:4]}-{name[4:6]}-{name[6:8]}"


def migrate_file(filepath, dry_run=False):
    """Upgrade a single cache file from old to enhanced format."""
    date_str = date_from_filename(filepath.name)
    yyyymmdd = filepath.stem

    if dry_run:
        print(f"  [dry-run] Would migrate {filepath.parent.parent.parent.name}/{yyyymmdd}")
        return True

    print(f"  Fetching enhanced stats for {date_str}...")
    try:
        enhanced = fetch_nba_stats_enhanced(date_str)
        filepath.write_text(json.dumps(enhanced), encoding="utf-8")
        team_count = len(enhanced.get("season", {}))
        l10_count = len(enhanced.get("last10") or {})
        print(f"    OK: {team_count} teams, {l10_count} last10")
        return True
    except Exception as e:
        print(f"    FAILED: {e}")
        return False


def main():
    dry_run = "--dry-run" in sys.argv

    # Collect all old-format files across all cache dirs
    to_migrate = []
    for cache_dir in CACHE_DIRS:
        if not cache_dir.exists():
            continue
        module = cache_dir.parent.parent.name
        for f in sorted(cache_dir.iterdir()):
            if f.suffix == ".json" and is_old_format(f):
                to_migrate.append(f)

    if not to_migrate:
        print("All cache files are already in enhanced format. Nothing to do.")
        return

    # Deduplicate by date — we only need to fetch once per date,
    # then copy to all modules that have the same old file
    dates_to_files = {}
    for f in to_migrate:
        date = f.stem
        if date not in dates_to_files:
            dates_to_files[date] = []
        dates_to_files[date].append(f)

    unique_dates = sorted(dates_to_files.keys())
    print(f"\nFound {len(to_migrate)} old-format files across {len(unique_dates)} unique dates")
    print(f"Date range: {unique_dates[0]} — {unique_dates[-1]}\n")

    if dry_run:
        for date in unique_dates:
            files = dates_to_files[date]
            modules = [f.parent.parent.parent.name for f in files]
            print(f"  {date}: {', '.join(modules)}")
        print(f"\nRun without --dry-run to migrate.")
        return

    success = 0
    failed = 0

    for i, date in enumerate(unique_dates):
        files = dates_to_files[date]
        date_str = date_from_filename(date + ".json")

        print(f"[{i+1}/{len(unique_dates)}] Fetching enhanced stats for {date_str}...")
        try:
            enhanced = fetch_nba_stats_enhanced(date_str)
            data_json = json.dumps(enhanced)
            team_count = len(enhanced.get("season", {}))
            l10_count = len(enhanced.get("last10") or {})

            for f in files:
                f.write_text(data_json, encoding="utf-8")
                module = f.parent.parent.parent.name
                print(f"  Updated {module}/stats_cache/{date}.json ({team_count} teams, {l10_count} L10)")

            success += 1
        except Exception as e:
            print(f"  FAILED: {e}")
            failed += 1

        # Rate limit
        if i < len(unique_dates) - 1:
            time.sleep(DELAY_BETWEEN_FETCHES)

    print(f"\nDone: {success} dates migrated, {failed} failed")


if __name__ == "__main__":
    main()
