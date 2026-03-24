"""
nextgenstats.py — NFL Next Gen Stats supplementary data source.

Fetches tracking-based metrics (completion probability, time to throw,
separation, rush speed) from the NFL.com Next Gen Stats API. This is
optional Tier 2 enrichment — the pipeline runs without it.

The NFL NGS API has no stability guarantee. Every function is wrapped in
a try/except and returns None on failure. Warnings are printed but
exceptions are never propagated.
"""

import time
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_URL = "https://nextgenstats.nfl.com/api/statboard"
_REQUEST_TIMEOUT_SEC = 15
_REQUEST_DELAY_SEC = 1.5  # polite delay between NGS API calls

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://nextgenstats.nfl.com/",
    "Origin": "https://nextgenstats.nfl.com",
}

# Module-level timestamp for rate-limiting.
_last_request_time: float = 0.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ngs_get(stat_type: str, season: int, week: Optional[int] = None) -> Optional[list]:
    """Fetch a statboard endpoint and return the parsed JSON list.

    Parameters
    ----------
    stat_type : str
        One of ``"passing"``, ``"receiving"``, ``"rushing"``.
    season : int
        NFL season year.
    week : int or None
        Specific week, or None for full season.

    Returns
    -------
    list or None
        Parsed JSON rows from the API, or None on any failure.
    """
    global _last_request_time

    elapsed = time.time() - _last_request_time
    if elapsed < _REQUEST_DELAY_SEC:
        time.sleep(_REQUEST_DELAY_SEC - elapsed)

    params = {
        "season": str(season),
        "seasonType": "REG",
    }
    if week is not None:
        params["week"] = str(week)

    url = f"{_BASE_URL}/{stat_type}"

    resp = requests.get(url, params=params, headers=_HEADERS, timeout=_REQUEST_TIMEOUT_SEC)
    _last_request_time = time.time()

    if resp.status_code in (403, 429, 503):
        raise RuntimeError(
            f"NGS API returned HTTP {resp.status_code} for {stat_type} — "
            "API may be unavailable or rate-limited."
        )
    resp.raise_for_status()

    data = resp.json()

    # The API may wrap rows in a top-level key or return a list directly.
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # Common wrapper keys observed in the wild.
        for key in ("data", "stats", "rows", "statboard", "players"):
            if key in data and isinstance(data[key], list):
                return data[key]
        # If it's a dict with no recognizable wrapper, return as single-item list.
        return [data]

    return None


def _safe_get(row: dict, key: str, default=None):
    """Extract a value from a JSON row, returning *default* on miss."""
    val = row.get(key, default)
    if val is None or val == "":
        return default
    return val


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_ngs_passing(
    season: int, week: Optional[int] = None
) -> Optional[dict]:
    """Fetch Next Gen Stats passing data (QB tracking metrics).

    Parameters
    ----------
    season : int
        NFL season year (e.g. 2025).
    week : int or None
        Specific week, or None for full-season aggregates.

    Returns
    -------
    dict or None
        Keyed by player display name, e.g.::

            {
                "Patrick Mahomes": {
                    "team": "KC",
                    "completions": 401,
                    "attempts": 588,
                    "yards": 4839,
                    "td": 37,
                    "int": 12,
                    "avg_time_to_throw": 2.72,
                    "avg_completed_air_yards": 5.6,
                    "avg_intended_air_yards": 8.1,
                    "aggressiveness_pct": 17.2,
                    "expected_completion_pct": 65.1,
                    "completion_pct_above_expected": 3.4,
                    "max_completion_probability": 98.2,
                    "avg_air_distance": 8.5,
                },
                ...
            }

        Returns None if the API is unreachable or data is unavailable.
    """
    try:
        rows = _ngs_get("passing", season, week)
        if not rows:
            print(
                f"[WARN] nextgenstats: no passing data for "
                f"season={season}, week={week} — continuing without NGS passing data"
            )
            return None

        results: dict = {}
        for row in rows:
            name = _safe_get(row, "playerDisplayName") or _safe_get(row, "displayName") or _safe_get(row, "player_display_name", "")
            if not name:
                continue

            results[name] = {
                "team": _safe_get(row, "teamAbbr", _safe_get(row, "team", "")),
                "completions": _safe_get(row, "completions", 0),
                "attempts": _safe_get(row, "attempts", 0),
                "yards": _safe_get(row, "passingYards", _safe_get(row, "yards", 0)),
                "td": _safe_get(row, "passingTouchdowns", _safe_get(row, "touchdowns", 0)),
                "int": _safe_get(row, "interceptions", 0),
                "avg_time_to_throw": _safe_get(row, "avgTimeToThrow", _safe_get(row, "avg_time_to_throw", 0.0)),
                "avg_completed_air_yards": _safe_get(row, "avgCompletedAirYards", _safe_get(row, "avg_completed_air_yards", 0.0)),
                "avg_intended_air_yards": _safe_get(row, "avgIntendedAirYards", _safe_get(row, "avg_intended_air_yards", 0.0)),
                "aggressiveness_pct": _safe_get(row, "aggressiveness", _safe_get(row, "aggressiveness_pct", 0.0)),
                "expected_completion_pct": _safe_get(row, "expectedCompletionPercentage", _safe_get(row, "xCompletion", 0.0)),
                "completion_pct_above_expected": _safe_get(row, "completionPercentageAboveExpectation", _safe_get(row, "cpoe", 0.0)),
                "max_completion_probability": _safe_get(row, "maxCompletionProbability", 0.0),
                "avg_air_distance": _safe_get(row, "avgAirDistance", 0.0),
            }

        if not results:
            print(
                f"[WARN] nextgenstats: parsed 0 passing rows for "
                f"season={season}, week={week} — continuing without NGS passing data"
            )
            return None

        return results

    except Exception as e:
        print(f"[WARN] nextgenstats: {e} — continuing without NGS passing data")
        return None


def fetch_ngs_receiving(
    season: int, week: Optional[int] = None
) -> Optional[dict]:
    """Fetch Next Gen Stats receiving data (tracking metrics).

    Parameters
    ----------
    season : int
        NFL season year (e.g. 2025).
    week : int or None
        Specific week, or None for full-season aggregates.

    Returns
    -------
    dict or None
        Keyed by player display name, e.g.::

            {
                "Tyreek Hill": {
                    "team": "MIA",
                    "receptions": 119,
                    "targets": 170,
                    "yards": 1799,
                    "td": 13,
                    "avg_cushion": 6.2,
                    "avg_separation": 3.1,
                    "avg_intended_air_yards": 11.3,
                    "catch_pct": 70.0,
                    "avg_yac": 7.8,
                    "avg_expected_yac": 5.2,
                    "avg_yac_above_expectation": 2.6,
                },
                ...
            }

        Returns None if the API is unreachable or data is unavailable.
    """
    try:
        rows = _ngs_get("receiving", season, week)
        if not rows:
            print(
                f"[WARN] nextgenstats: no receiving data for "
                f"season={season}, week={week} — continuing without NGS receiving data"
            )
            return None

        results: dict = {}
        for row in rows:
            name = _safe_get(row, "playerDisplayName") or _safe_get(row, "displayName") or _safe_get(row, "player_display_name", "")
            if not name:
                continue

            results[name] = {
                "team": _safe_get(row, "teamAbbr", _safe_get(row, "team", "")),
                "receptions": _safe_get(row, "receptions", 0),
                "targets": _safe_get(row, "targets", 0),
                "yards": _safe_get(row, "receivingYards", _safe_get(row, "yards", 0)),
                "td": _safe_get(row, "receivingTouchdowns", _safe_get(row, "touchdowns", 0)),
                "avg_cushion": _safe_get(row, "avgCushion", _safe_get(row, "avg_cushion", 0.0)),
                "avg_separation": _safe_get(row, "avgSeparation", _safe_get(row, "avg_separation", 0.0)),
                "avg_intended_air_yards": _safe_get(row, "avgIntendedAirYards", _safe_get(row, "avg_intended_air_yards", 0.0)),
                "catch_pct": _safe_get(row, "catchPercentage", _safe_get(row, "catch_pct", 0.0)),
                "avg_yac": _safe_get(row, "avgYAC", _safe_get(row, "avg_yac", 0.0)),
                "avg_expected_yac": _safe_get(row, "avgExpectedYAC", _safe_get(row, "avg_expected_yac", 0.0)),
                "avg_yac_above_expectation": _safe_get(row, "avgYACAboveExpectation", _safe_get(row, "yac_above_expectation", 0.0)),
            }

        if not results:
            print(
                f"[WARN] nextgenstats: parsed 0 receiving rows for "
                f"season={season}, week={week} — continuing without NGS receiving data"
            )
            return None

        return results

    except Exception as e:
        print(f"[WARN] nextgenstats: {e} — continuing without NGS receiving data")
        return None


def fetch_ngs_rushing(
    season: int, week: Optional[int] = None
) -> Optional[dict]:
    """Fetch Next Gen Stats rushing data (tracking metrics).

    Parameters
    ----------
    season : int
        NFL season year (e.g. 2025).
    week : int or None
        Specific week, or None for full-season aggregates.

    Returns
    -------
    dict or None
        Keyed by player display name, e.g.::

            {
                "Derrick Henry": {
                    "team": "BAL",
                    "attempts": 325,
                    "yards": 1921,
                    "td": 16,
                    "avg_rush_yards": 5.9,
                    "avg_time_to_los": 2.58,
                    "rush_attempts_gte_8_defenders": 40,
                    "rush_yards_gte_8_defenders": 180,
                    "efficiency": 56.2,
                    "expected_rush_yards": 1550.0,
                    "rush_yards_over_expected": 371.0,
                    "rush_yards_over_expected_per_att": 1.14,
                    "rush_pct_over_expected": 23.9,
                },
                ...
            }

        Returns None if the API is unreachable or data is unavailable.
    """
    try:
        rows = _ngs_get("rushing", season, week)
        if not rows:
            print(
                f"[WARN] nextgenstats: no rushing data for "
                f"season={season}, week={week} — continuing without NGS rushing data"
            )
            return None

        results: dict = {}
        for row in rows:
            name = _safe_get(row, "playerDisplayName") or _safe_get(row, "displayName") or _safe_get(row, "player_display_name", "")
            if not name:
                continue

            results[name] = {
                "team": _safe_get(row, "teamAbbr", _safe_get(row, "team", "")),
                "attempts": _safe_get(row, "rushAttempts", _safe_get(row, "attempts", 0)),
                "yards": _safe_get(row, "rushYards", _safe_get(row, "yards", 0)),
                "td": _safe_get(row, "rushTouchdowns", _safe_get(row, "touchdowns", 0)),
                "avg_rush_yards": _safe_get(row, "avgRushYards", _safe_get(row, "avg_rush_yards", 0.0)),
                "avg_time_to_los": _safe_get(row, "avgTimeBehindLOS", _safe_get(row, "avg_time_to_los", 0.0)),
                "rush_attempts_gte_8_defenders": _safe_get(row, "rushAttemptsGte8Defenders", 0),
                "rush_yards_gte_8_defenders": _safe_get(row, "rushYardsGte8Defenders", 0),
                "efficiency": _safe_get(row, "efficiency", _safe_get(row, "rush_efficiency", 0.0)),
                "expected_rush_yards": _safe_get(row, "expectedRushYards", _safe_get(row, "expected_rush_yards", 0.0)),
                "rush_yards_over_expected": _safe_get(row, "rushYardsOverExpected", _safe_get(row, "ryoe", 0.0)),
                "rush_yards_over_expected_per_att": _safe_get(row, "rushYardsOverExpectedPerAtt", _safe_get(row, "ryoe_per_att", 0.0)),
                "rush_pct_over_expected": _safe_get(row, "rushPctOverExpected", 0.0),
            }

        if not results:
            print(
                f"[WARN] nextgenstats: parsed 0 rushing rows for "
                f"season={season}, week={week} — continuing without NGS rushing data"
            )
            return None

        return results

    except Exception as e:
        print(f"[WARN] nextgenstats: {e} — continuing without NGS rushing data")
        return None


def fetch_ngs_data(season, week=None):
    """Convenience wrapper fetching all NGS data in one call.

    Returns dict with 'passing', 'receiving', 'rushing' keys (any may be None).
    """
    return {
        "passing": fetch_ngs_passing(season, week=week),
        "receiving": fetch_ngs_receiving(season, week=week),
        "rushing": fetch_ngs_rushing(season, week=week),
    }
