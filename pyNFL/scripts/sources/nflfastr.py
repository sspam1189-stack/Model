# pyNFL/scripts/sources/nflfastr.py
# Fetches NFL play-by-play data and rosters via nfl_data_py (Python wrapper for nflfastR).
#
# Usage:
#   from pyNFL.scripts.sources.nflfastr import fetch_pbp, fetch_roster
#   pbp = fetch_pbp(2025)                    # full 2025 season
#   pbp = fetch_pbp(2025, weeks=[1,2,3])     # weeks 1-3 only
#   roster = fetch_roster(2025)              # 2025 roster

import os
import time
import hashlib
import datetime
import pandas as pd
import nfl_data_py as nfl

# ---------------------------------------------------------------------------
# Cache directory -- parquet files avoid redundant API round-trips
# ---------------------------------------------------------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "..", "data"))
_CACHE_DIR = os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "..", "..", "data", "stats_cache", "nfl"))

# Columns we actually need from the massive PBP dataset (~370+ cols).
# Pulling only what we use keeps memory manageable.
PBP_COLUMNS = [
    # identifiers
    "game_id", "play_id", "season", "week", "game_date", "season_type",
    "home_team", "away_team", "posteam", "defteam", "posteam_type",
    # play classification
    "play_type", "down", "ydstogo", "yardline_100", "goal_to_go",
    "qtr", "game_seconds_remaining", "half_seconds_remaining",
    # core metrics
    "epa", "wpa", "cpoe", "air_yards", "yards_after_catch",
    "yards_gained", "first_down",
    # binary flags
    "pass", "rush", "success", "complete_pass", "incomplete_pass",
    "interception", "fumble_lost", "touchdown", "sack",
    "qb_hit", "qb_scramble", "penalty",
    # player IDs
    "passer_player_id", "passer_player_name",
    "receiver_player_id", "receiver_player_name",
    "rusher_player_id", "rusher_player_name",
    # situational
    "score_differential", "score_differential_post",
    "shotgun", "no_huddle", "pass_location",
    "run_location", "run_gap",
    # field position / red zone
    "fixed_drive", "fixed_drive_result",
    "drive_play_count",
]

# Columns that may not exist in older seasons -- we request them but don't
# fail if they're absent.  Pressure is derived from qb_hit + sack columns.
_OPTIONAL_COLS = {"cpoe", "air_yards", "yards_after_catch"}


def _ensure_cache_dir():
    """Create cache directory if it doesn't exist."""
    os.makedirs(_CACHE_DIR, exist_ok=True)


def _cache_key(prefix, season, weeks=None):
    """Deterministic cache filename based on query parameters."""
    parts = [prefix, str(season)]
    if weeks:
        parts.append("w" + "-".join(str(w) for w in sorted(weeks)))
    return hashlib.md5("_".join(parts).encode()).hexdigest()[:12]


def _cache_path(prefix, season, weeks=None):
    """Full path to the parquet cache file."""
    key = _cache_key(prefix, season, weeks)
    return os.path.join(_CACHE_DIR, f"{prefix}_{season}_{key}.parquet")


def _is_cache_fresh(path, max_age_hours=6):
    """
    Check if a cache file exists and is younger than max_age_hours.

    During the season we refresh frequently; in the offseason, stale caches
    are fine since data doesn't change.
    """
    if not os.path.exists(path):
        return False
    mtime = os.path.getmtime(path)
    age_hours = (time.time() - mtime) / 3600
    return age_hours < max_age_hours


# ---------------------------------------------------------------------------
# Play-by-play
# ---------------------------------------------------------------------------

class NoPBPDataError(RuntimeError):
    """nflverse has no play-by-play for this season yet (pre-Week-1 404).

    Distinct from a network/transport failure so callers can fall back
    deliberately instead of masking a real outage.
    """


def fetch_pbp(season, weeks=None, force_refresh=False):
    """
    Fetch play-by-play data for a given NFL season.

    Parameters
    ----------
    season : int
        NFL season year (e.g. 2025 for the 2025-26 season).
    weeks : list[int] or None
        If provided, filter to these weeks only. None returns the full season.
    force_refresh : bool
        If True, bypass cache and re-download.

    Returns
    -------
    pd.DataFrame
        Play-by-play DataFrame with per-play EPA, CPOE, air_yards, WPA,
        passer/receiver/rusher IDs, and situational columns.
    """
    _ensure_cache_dir()
    cache = _cache_path("pbp", season, weeks)

    if not force_refresh and _is_cache_fresh(cache):
        print(f"  [nflfastr] Loading cached PBP for {season} (weeks={weeks or 'all'})")
        df = pd.read_parquet(cache)
        print(f"  [nflfastr] Cached: {len(df):,} plays")
        return df

    print(f"  [nflfastr] Fetching PBP for {season} (weeks={weeks or 'all'})...")

    # nfl_data_py.import_pbp_data accepts a list of years and optional columns.
    # The columns parameter filters at download time, reducing memory usage.
    available_cols = None
    for attempt in range(1, 4):
        try:
            # First attempt: request only the columns we need
            df = nfl.import_pbp_data(
                years=[season],
                columns=PBP_COLUMNS,
            )
            break
        except ValueError:
            # Some columns may not exist for older seasons -- fall back to
            # fetching all columns and selecting what's available.
            if attempt == 1:
                print(f"  [nflfastr] Column filter failed, fetching all columns...")
                try:
                    df = nfl.import_pbp_data(years=[season])
                    break
                except Exception as e2:
                    if attempt == 3:
                        raise
                    wait = attempt * 5
                    print(f"  [nflfastr] Attempt {attempt}/3 failed ({e2}), retrying in {wait}s...")
                    time.sleep(wait)
        except Exception as e:
            if attempt == 3:
                raise RuntimeError(
                    f"Failed to fetch PBP data for season {season} after 3 attempts: {e}"
                ) from e
            wait = attempt * 5
            print(f"  [nflfastr] Attempt {attempt}/3 failed ({e}), retrying in {wait}s...")
            time.sleep(wait)

    if df is None or df.empty:
        raise NoPBPDataError(f"No play-by-play data returned for season {season}")

    # Keep only requested columns that actually exist
    existing = [c for c in PBP_COLUMNS if c in df.columns]
    df = df[existing].copy()

    # Filter to real plays (exclude timeouts, end-of-quarter, etc.)
    if "play_type" in df.columns:
        valid_play_types = ["pass", "run", "qb_kneel", "qb_spike", "punt", "field_goal"]
        # For EPA analysis we primarily want pass and run plays, but keep all
        # valid types to allow downstream consumers to filter further.
        df = df[df["play_type"].isin(valid_play_types)].copy()

    # Filter to requested weeks
    if weeks and "week" in df.columns:
        df = df[df["week"].isin(weeks)].copy()

    # Fill missing optional columns with NaN so downstream code can rely
    # on their existence.
    for col in _OPTIONAL_COLS:
        if col not in df.columns:
            df[col] = float("nan")

    # Ensure numeric types for key columns
    numeric_cols = ["epa", "wpa", "cpoe", "air_yards", "yards_after_catch",
                    "yards_gained", "yardline_100", "down", "ydstogo",
                    "score_differential"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Ensure boolean flags are numeric (0/1)
    flag_cols = ["pass", "rush", "success", "complete_pass", "incomplete_pass",
                 "interception", "fumble_lost", "touchdown", "sack",
                 "qb_hit", "first_down", "penalty", "goal_to_go",
                 "shotgun", "no_huddle"]
    for col in flag_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    # Save to cache
    try:
        df.to_parquet(cache, index=False)
        print(f"  [nflfastr] Cached {len(df):,} plays to {os.path.basename(cache)}")
    except Exception as e:
        print(f"  [nflfastr] Warning: cache write failed ({e})")

    print(f"  [nflfastr] Fetched {len(df):,} plays for {season} (weeks={weeks or 'all'})")
    return df


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------

def fetch_roster(season, force_refresh=False):
    """
    Fetch the NFL roster for a given season.

    Parameters
    ----------
    season : int
        NFL season year.
    force_refresh : bool
        If True, bypass cache and re-download.

    Returns
    -------
    pd.DataFrame
        Roster DataFrame with columns: player_id, player_name, position,
        team, jersey_number, status, height, weight, birth_date, college,
        years_exp, etc.
    """
    _ensure_cache_dir()
    cache = _cache_path("roster", season)

    if not force_refresh and _is_cache_fresh(cache, max_age_hours=24):
        print(f"  [nflfastr] Loading cached roster for {season}")
        df = pd.read_parquet(cache)
        print(f"  [nflfastr] Cached: {len(df)} players")
        return df

    print(f"  [nflfastr] Fetching roster for {season}...")

    df = None
    for attempt in range(1, 4):
        try:
            df = nfl.import_rosters(years=[season])
            break
        except Exception as e:
            if attempt == 3:
                raise RuntimeError(
                    f"Failed to fetch roster for season {season} after 3 attempts: {e}"
                ) from e
            wait = attempt * 5
            print(f"  [nflfastr] Roster attempt {attempt}/3 failed ({e}), retrying in {wait}s...")
            time.sleep(wait)

    if df is None or df.empty:
        raise RuntimeError(f"No roster data returned for season {season}")

    # Standardize key columns
    col_map = {
        "player_id": "player_id",
        "player_name": "player_name",
        "full_name": "player_name",
        "position": "position",
        "team": "team",
        "jersey_number": "jersey_number",
        "status": "status",
    }
    rename = {}
    for src, dst in col_map.items():
        if src in df.columns and src != dst:
            rename[src] = dst
    if rename:
        df = df.rename(columns=rename)

    # Cache
    try:
        df.to_parquet(cache, index=False)
        print(f"  [nflfastr] Cached {len(df)} players to {os.path.basename(cache)}")
    except Exception as e:
        print(f"  [nflfastr] Warning: roster cache write failed ({e})")

    print(f"  [nflfastr] Fetched {len(df)} players for {season}")
    return df


# ---------------------------------------------------------------------------
# Seasonal / weekly aggregates (convenience wrappers)
# ---------------------------------------------------------------------------

def fetch_seasonal(season, force_refresh=False):
    """
    Fetch nfl_data_py seasonal aggregate data.

    Returns pre-aggregated season-level stats per player (EPA, CPOE, etc.).
    Useful as a cross-check against our own aggregations.
    """
    _ensure_cache_dir()
    cache = _cache_path("seasonal", season)

    if not force_refresh and _is_cache_fresh(cache, max_age_hours=24):
        print(f"  [nflfastr] Loading cached seasonal data for {season}")
        return pd.read_parquet(cache)

    print(f"  [nflfastr] Fetching seasonal data for {season}...")
    df = nfl.import_seasonal_data(years=[season])

    if df is not None and not df.empty:
        try:
            df.to_parquet(cache, index=False)
        except Exception:
            pass

    return df


def fetch_weekly(season, force_refresh=False):
    """
    Fetch nfl_data_py weekly aggregate data.

    Returns pre-aggregated weekly stats per player.
    """
    _ensure_cache_dir()
    cache = _cache_path("weekly", season)

    if not force_refresh and _is_cache_fresh(cache, max_age_hours=6):
        print(f"  [nflfastr] Loading cached weekly data for {season}")
        return pd.read_parquet(cache)

    print(f"  [nflfastr] Fetching weekly data for {season}...")
    df = nfl.import_weekly_data(years=[season])

    if df is not None and not df.empty:
        try:
            df.to_parquet(cache, index=False)
        except Exception:
            pass

    return df


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def clear_cache(season=None):
    """
    Remove cached files.

    Parameters
    ----------
    season : int or None
        If provided, only clear caches for this season.
        If None, clear all cached files.
    """
    if not os.path.exists(_CACHE_DIR):
        return

    removed = 0
    for f in os.listdir(_CACHE_DIR):
        if not f.endswith(".parquet"):
            continue
        if season and f"_{season}_" not in f:
            continue
        os.remove(os.path.join(_CACHE_DIR, f))
        removed += 1
    print(f"  [nflfastr] Cleared {removed} cached files")


def available_seasons():
    """Return list of seasons with PBP data available (typically 1999-present)."""
    current_year = datetime.datetime.now().year
    # nflfastR data starts at 1999
    return list(range(1999, current_year + 1))


def current_season():
    """Auto-detect the current NFL season year."""
    now = datetime.datetime.now()
    # NFL season starts in September; use previous year if we're before Sept
    if now.month < 3:
        return now.year - 1
    elif now.month >= 9:
        return now.year
    else:
        # March-August: previous season's data is complete
        return now.year - 1
