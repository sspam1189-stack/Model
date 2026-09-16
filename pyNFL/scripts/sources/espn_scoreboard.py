# pyNFL/scripts/sources/espn_scoreboard.py
# Fetches NFL final scores from ESPN's public JSON scoreboard endpoint.

import os
import json
import time
import requests
import math
from pathlib import Path


ESPN_NFL_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"

# --------------- disk cache helpers ---------------

_CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "espn_cache" / "nfl"


def _cache_path(season, week, season_type=2):
    """Return the cache file path for a given season/week/season_type."""
    if season_type == 3:
        return _CACHE_DIR / f"nfl_scores_{season}_POST_W{week}.json"
    return _CACHE_DIR / f"nfl_scores_{season}_W{week}.json"


def _save_cache(data, path):
    """Write *data* as JSON to *path*, creating directories as needed."""
    try:
        os.makedirs(path.parent, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as exc:
        print(f"  [scores-cache] WARNING: could not write cache {path}: {exc}")


def _load_cache(path, max_age_hours=None):
    """
    Load JSON from *path* if it exists and is fresh enough.

    Args:
        path: Path to cache file.
        max_age_hours: Maximum age in hours.  ``None`` means never expire
                       (use for completed / final scores that won't change).

    Returns:
        Parsed JSON data, or ``None`` if cache miss / stale / unreadable.
    """
    try:
        if not path.exists():
            return None
        if max_age_hours is not None:
            age_s = time.time() - path.stat().st_mtime
            if age_s > max_age_hours * 3600:
                return None
        with open(path) as f:
            return json.load(f)
    except Exception as exc:
        print(f"  [scores-cache] WARNING: could not read cache {path}: {exc}")
        return None


def _expected_games(scoreboard_json):
    """How many games the week HAS, played or not.

    The denominator for "is this week complete". extract_all_games keeps
    scheduled and in-progress games, and applies the same away/home
    requirement extract_final_scores does, so the two are comparable.
    """
    return len(extract_all_games(scoreboard_json))


def _cache_payload(scores, expected):
    """What goes on disk: the finals, plus how many games the week had.

    The count is the whole point. Without it a cache of six finals cannot be
    told apart from a six-game week.
    """
    return {"scores": scores, "finals": len(scores), "expected": expected}


def _unpack_cache(cached):
    """(scores, week_is_complete) from either cache shape.

    THIS IS THE FIX (2026-09-16). The old test asked whether every entry in
    the cached list had scores -- but that list came from
    extract_final_scores, which drops non-final games, so it was asking "do
    the final games have scores" and could only ever answer yes. Any cache
    written mid-slate was therefore sealed as permanent, and the six games
    that happened to be over became the whole week forever. Compare the
    finals against the number of games the week actually had instead.

    A bare list is the pre-2026-09-16 format. 66 of those are committed for
    2023-25, all of them finished weeks, so they stay permanent rather than
    being refetched -- there is nothing left to learn about a season that
    ended. Only the new format can be judged incomplete.
    """
    if isinstance(cached, dict):
        scores = cached.get("scores") or []
        expected = cached.get("expected")
        if not scores or not isinstance(expected, int) or expected <= 0:
            return scores, False
        return scores, len(scores) >= expected
    if isinstance(cached, list):
        return cached, bool(cached)
    return [], False


def clear_cache():
    """Remove all cached scores files from disk."""
    try:
        if _CACHE_DIR.exists():
            for f in _CACHE_DIR.glob("nfl_scores_*.json"):
                f.unlink(missing_ok=True)
            print(f"  [scores-cache] Cleared cache directory {_CACHE_DIR}")
    except Exception as exc:
        print(f"  [scores-cache] WARNING: could not clear cache: {exc}")


def fetch_nfl_scoreboard(week=None, season=None, season_type=2):
    """
    Fetch ESPN NFL scoreboard JSON.

    Args:
        week: NFL week number (1-18 regular season)
        season: 4-digit year (e.g. 2025)
        season_type: 1=preseason, 2=regular, 3=postseason

    Returns:
        Raw ESPN scoreboard JSON dict.
    """
    params = {}
    if week is not None:
        params["week"] = str(week)
    if season is not None:
        params["dates"] = str(season)
        params["seasontype"] = str(season_type)

    url = ESPN_NFL_SCOREBOARD
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"

    # ESPN's CDN 403s bot/browser-spoof UAs (since 2026-08-06); the
    # requests default UA is allowed, so send no custom User-Agent.
    res = requests.get(url, timeout=30)
    if res.status_code != 200:
        raise Exception(f"HTTP {res.status_code} for {url}")
    return res.json()


def extract_final_scores(scoreboard_json):
    """
    Extract final scores from ESPN NFL scoreboard JSON.

    Returns list of dicts:
    [{ away, home, awayScore, homeScore, gameId, date, week }]
    """
    out = []
    for ev in scoreboard_json.get("events", []):
        comp = (ev.get("competitions") or [None])[0]
        if not comp:
            continue

        status = (comp.get("status") or {}).get("type", {}).get("name")
        is_final = status in ("STATUS_FINAL", "STATUS_FINAL_OVERTIME")
        if not is_final:
            continue

        competitors = comp.get("competitors", [])
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        if not away or not home:
            continue

        away_name = (away.get("team") or {}).get("displayName")
        home_name = (home.get("team") or {}).get("displayName")
        try:
            away_score = float(away.get("score"))
            home_score = float(home.get("score"))
        except (TypeError, ValueError):
            continue
        if not away_name or not home_name or not math.isfinite(away_score) or not math.isfinite(home_score):
            continue

        # Extract week number from the event if available
        week = None
        try:
            week = int(ev.get("week", {}).get("number", 0)) or None
        except (TypeError, ValueError, AttributeError):
            pass

        out.append({
            "away": away_name,
            "home": home_name,
            "awayScore": away_score,
            "homeScore": home_score,
            "gameId": ev.get("id"),
            "date": comp.get("date") or ev.get("date"),
            "week": week,
        })
    return out


def fetch_week_scores(week, season=None, season_type=2):
    """
    Convenience: fetch scoreboard for a specific week and return final scores.

    Uses a disk cache:
      - Once the week is complete -- finals == the number of games the week
        has -- the cache never expires, because scores won't change.
      - Otherwise cached for up to 2 hours (games still to play).

    The completeness test compares against the week's game count, NOT against
    the cached list's own contents; see _unpack_cache for why that
    distinction is the whole bug.

    Args:
        week: NFL week number
        season: 4-digit year (defaults to current season)
        season_type: 1=preseason, 2=regular, 3=postseason

    Returns:
        List of final score dicts from extract_final_scores.
    """
    # --- try cache ---
    cp = _cache_path(season, week, season_type) if (season and week) else None
    if cp:
        cached = _load_cache(cp, max_age_hours=None)  # optimistic: try permanent first
        if cached is not None:
            scores, complete = _unpack_cache(cached)
            if complete:
                print(f"  [scores] Using cached final scores for {season} W{week} st={season_type} ({cp.name})")
                return scores
            # Week is not over. Honour the 2-hour window, then go back out.
            fresh = _load_cache(cp, max_age_hours=2)
            if fresh is not None:
                scores, _ = _unpack_cache(fresh)
                print(f"  [scores] Using cached (partial) scores for {season} W{week} st={season_type} ({cp.name})")
                return scores

    sb = fetch_nfl_scoreboard(week=week, season=season, season_type=season_type)
    scores = extract_final_scores(sb)
    expected = _expected_games(sb)

    # --- save to cache ---
    if cp and scores:
        _save_cache(_cache_payload(scores, expected), cp)
        if expected and len(scores) < expected:
            print(f"  [scores] {season} W{week}: {len(scores)} of {expected} final "
                  f"-- cached for 2h only, week is not over")

    return scores


def extract_all_games(scoreboard_json):
    """
    Extract ALL games (not just finals) from ESPN scoreboard JSON.
    Useful for getting matchup info + commence times before games start.

    Returns list of dicts:
    [{ away, home, commenceTimeIso, isFinal, isInProgress, gameId, week }]
    """
    out = []
    for ev in scoreboard_json.get("events", []):
        comp = (ev.get("competitions") or [None])[0]
        if not comp:
            continue

        competitors = comp.get("competitors", [])
        away_c = next((c for c in competitors if c.get("homeAway") == "away"), None)
        home_c = next((c for c in competitors if c.get("homeAway") == "home"), None)
        if not away_c or not home_c:
            continue

        away = (away_c.get("team") or {}).get("displayName")
        home = (home_c.get("team") or {}).get("displayName")
        commence_time_iso = comp.get("date") or ev.get("date")
        status_name = ((comp.get("status") or {}).get("type") or {}).get("name", "")
        is_final = "FINAL" in status_name
        is_in_progress = status_name in ("STATUS_IN_PROGRESS", "STATUS_HALFTIME",
                                          "STATUS_END_PERIOD", "STATUS_FIRST_HALF",
                                          "STATUS_SECOND_HALF")

        if not away or not home:
            continue

        # Extract week number
        week = None
        try:
            week = int(ev.get("week", {}).get("number", 0)) or None
        except (TypeError, ValueError, AttributeError):
            pass

        out.append({
            "away": away,
            "home": home,
            "commenceTimeIso": commence_time_iso,
            "isFinal": is_final,
            "isInProgress": is_in_progress,
            "gameId": ev.get("id"),
            "week": week,
        })
    return out
