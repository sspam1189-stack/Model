# pyMLB/scripts/sources/mlb_stats.py
# Fetch MLB team-level batting + pitching stats via pybaseball.
# Returns blended season/recent stats for each of the 30 teams.

import os
import sys
import json
import time
import logging
import datetime

_dir = os.path.dirname(os.path.abspath(__file__))
STATS_CACHE_DIR = os.path.normpath(os.path.join(_dir, "..", "..", "..", "data", "stats_cache", "mlb"))
sys.path.insert(0, os.path.join(_dir, ".."))
import defaults

logger = logging.getLogger(__name__)


def _resolve_team_name(abbr, aliases):
    """
    Map a pybaseball team abbreviation to a canonical team name.
    Checks aliases dict first (case-insensitive), then tries substring match
    against canonical team names.
    """
    key = abbr.lower().strip()
    if key in aliases:
        return aliases[key]

    # Try case-insensitive substring match against canonical names
    for canonical in defaults.MLB_TEAMS:
        if key in canonical.lower():
            return canonical

    logger.warning("mlb_stats: unmapped team abbreviation '%s'", abbr)
    return None


def _safe_float(df, row_index, col, default=None):
    """Safely extract a float from a DataFrame cell, returning default on failure."""
    try:
        val = df.at[row_index, col]
        if val is None:
            return default
        f = float(val)
        import math
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (KeyError, TypeError, ValueError):
        return default


def _extract_batting_stats(bat_df, row_idx, defaults_stats):
    """Extract batting-side stats from a pybaseball team_batting DataFrame row."""
    stats = {}

    # wRC+ -> wRC_plus
    stats["wRC_plus"] = _safe_float(bat_df, row_idx, "wRC+",
                                    defaults_stats["wRC_plus"])

    # xwOBA -> xwOBA
    stats["xwOBA"] = _safe_float(bat_df, row_idx, "xwOBA",
                                  defaults_stats["xwOBA"])

    # ISO -> ISO
    stats["ISO"] = _safe_float(bat_df, row_idx, "ISO",
                                defaults_stats["ISO"])

    # K% -> K_pct (pybaseball returns as 0-100 or 0-1 depending on version)
    k_raw = _safe_float(bat_df, row_idx, "K%", None)
    if k_raw is not None:
        # Normalize to 0-1 if stored as percentage (>1)
        stats["K_pct"] = k_raw / 100.0 if k_raw > 1.0 else k_raw
    else:
        stats["K_pct"] = defaults_stats["K_pct"]

    # BB% -> BB_pct
    bb_raw = _safe_float(bat_df, row_idx, "BB%", None)
    if bb_raw is not None:
        stats["BB_pct"] = bb_raw / 100.0 if bb_raw > 1.0 else bb_raw
    else:
        stats["BB_pct"] = defaults_stats["BB_pct"]

    # Handedness splits: wRC+ vs L / wRC+ vs R (may not be available in team_batting)
    wrc_vs_l = _safe_float(bat_df, row_idx, "wRC+ vs L", None)
    wrc_vs_r = _safe_float(bat_df, row_idx, "wRC+ vs R", None)
    stats["wRC_vs_L"] = wrc_vs_l if wrc_vs_l is not None else stats["wRC_plus"]
    stats["wRC_vs_R"] = wrc_vs_r if wrc_vs_r is not None else stats["wRC_plus"]

    # GP from the G (games) column in batting stats
    g_val = _safe_float(bat_df, row_idx, "G", None)
    stats["GP"] = int(g_val) if g_val is not None else defaults_stats["GP"]

    return stats


def _extract_pitching_stats(pit_df, row_idx, defaults_stats):
    """Extract pitching-side stats from a pybaseball team_pitching DataFrame row."""
    stats = {}

    # FIP -> team_FIP
    stats["team_FIP"] = _safe_float(pit_df, row_idx, "FIP",
                                     defaults_stats["team_FIP"])

    # xFIP -> team_xFIP
    stats["team_xFIP"] = _safe_float(pit_df, row_idx, "xFIP",
                                      defaults_stats["team_xFIP"])

    # SIERA -> team_SIERA
    stats["team_SIERA"] = _safe_float(pit_df, row_idx, "SIERA",
                                       defaults_stats["team_SIERA"])

    # K-BB% = K% - BB% -> team_K_BB_pct
    k_raw = _safe_float(pit_df, row_idx, "K%", None)
    bb_raw = _safe_float(pit_df, row_idx, "BB%", None)
    if k_raw is not None and bb_raw is not None:
        k = k_raw / 100.0 if k_raw > 1.0 else k_raw
        b = bb_raw / 100.0 if bb_raw > 1.0 else bb_raw
        stats["team_K_BB_pct"] = k - b
    else:
        stats["team_K_BB_pct"] = defaults_stats["team_K_BB_pct"]

    return stats


def _fetch_with_retry(fetch_fn, max_retries=3, sleep_between=2):
    """
    Call fetch_fn() with exponential backoff on exceptions.
    Sleeps sleep_between seconds before the first call, then 5/10/15 on retries.
    """
    time.sleep(sleep_between)
    backoff_schedule = [5, 10, 15]
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return fetch_fn()
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                wait = backoff_schedule[min(attempt, len(backoff_schedule) - 1)]
                logger.warning(
                    "mlb_stats: fetch attempt %d failed (%s), retrying in %ds",
                    attempt + 1, exc, wait
                )
                time.sleep(wait)
            else:
                logger.error("mlb_stats: all retries exhausted: %s", exc)
    raise last_exc


def fetch_team_stats(season=None, date_str=None):
    """
    Fetch MLB team-level batting + pitching stats via pybaseball.

    Parameters
    ----------
    season   : int or None — season year (defaults to current year)
    date_str : str or None — YYYYMMDD cache key (defaults to today)

    Returns
    -------
    dict : {canonical_team_name: stats_dict} where stats_dict matches DEFAULT_STATS keys.
           Returns empty dict on total failure.
    """
    today = datetime.date.today()
    if season is None:
        season = today.year
    if date_str is None:
        date_str = today.strftime("%Y%m%d")

    today_str = today.strftime("%Y%m%d")
    is_today = (date_str == today_str)

    os.makedirs(STATS_CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(STATS_CACHE_DIR, f"{date_str}.json")

    # Check disk cache — use it for past dates; re-fetch for today
    if os.path.exists(cache_path) and not is_today:
        try:
            with open(cache_path, "r") as f:
                cached = json.load(f)
            return cached.get("teams", {})
        except Exception as exc:
            logger.warning("mlb_stats: cache read failed (%s), re-fetching", exc)

    # --- Fetch from pybaseball ---
    try:
        import pybaseball  # noqa: F401 — verify it's installed
        from pybaseball import team_batting, team_pitching
    except ImportError:
        logger.error(
            "mlb_stats: pybaseball is not installed. "
            "Run: pip install pybaseball"
        )
        # Return cached data if available, else empty dict
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r") as f:
                    return json.load(f).get("teams", {})
            except Exception:
                pass
        return {}

    # Fetch batting stats
    bat_df = None
    try:
        bat_df = _fetch_with_retry(lambda: team_batting(season, ind=0))
    except Exception as exc:
        logger.error("mlb_stats: team_batting fetch failed: %s", exc)

    # Fetch pitching stats (always sleep 2s between calls)
    pit_df = None
    try:
        pit_df = _fetch_with_retry(lambda: team_pitching(season, ind=0))
    except Exception as exc:
        logger.error("mlb_stats: team_pitching fetch failed: %s", exc)

    if bat_df is None and pit_df is None:
        # Both failed — fall back to cache if available
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r") as f:
                    logger.warning("mlb_stats: using stale cache for %s", date_str)
                    return json.load(f).get("teams", {})
            except Exception:
                pass
        return {}

    aliases = defaults.ENGINE_CONFIG["TEAM_NAME_ALIASES"]
    default_stats = dict(defaults.DEFAULT_STATS)

    # Build a lookup from canonical name -> pitching row index
    pit_by_team = {}
    if pit_df is not None and "Team" in pit_df.columns:
        for idx in pit_df.index:
            abbr = str(pit_df.at[idx, "Team"]).strip()
            canonical = _resolve_team_name(abbr, aliases)
            if canonical:
                pit_by_team[canonical] = idx

    result = {}

    # TODO: implement rolling 30-day blend when pybaseball supports it
    # For now, use full season stats only.

    if bat_df is not None and "Team" in bat_df.columns:
        for idx in bat_df.index:
            abbr = str(bat_df.at[idx, "Team"]).strip()
            canonical = _resolve_team_name(abbr, aliases)
            if canonical is None:
                continue

            team_stats = dict(default_stats)

            # Merge batting stats
            bat_stats = _extract_batting_stats(bat_df, idx, default_stats)
            team_stats.update(bat_stats)

            # Merge pitching stats if available
            if canonical in pit_by_team:
                pit_stats = _extract_pitching_stats(pit_df, pit_by_team[canonical], default_stats)
                team_stats.update(pit_stats)

            # Bullpen stats are NOT set here — handled by pitcher_stats.py
            # Initialize to defaults (already present from default_stats copy)

            result[canonical] = team_stats

    # If no batting data but pitching exists, still populate pitching-only teams
    if bat_df is None and pit_df is not None and "Team" in pit_df.columns:
        for idx in pit_df.index:
            abbr = str(pit_df.at[idx, "Team"]).strip()
            canonical = _resolve_team_name(abbr, aliases)
            if canonical is None or canonical in result:
                continue
            team_stats = dict(default_stats)
            pit_stats = _extract_pitching_stats(pit_df, idx, default_stats)
            team_stats.update(pit_stats)
            result[canonical] = team_stats

    # Write cache
    try:
        payload = {
            "fetched_at": date_str,
            "season": season,
            "teams": result,
        }
        with open(cache_path, "w") as f:
            json.dump(payload, f, indent=2)
    except Exception as exc:
        logger.warning("mlb_stats: cache write failed: %s", exc)

    return result
