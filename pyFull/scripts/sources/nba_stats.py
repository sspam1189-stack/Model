# scripts/sources/nba_stats.py
# Fetches NBA.com team stats -- full season, last N games, home/away splits.
#
# Uses direct requests to stats.nba.com (nba_api default headers are blocked).
# Mirrors the working jsNBA implementation.

import time
import datetime
import math
import requests

NBA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
}


def current_season():
    """Auto-detect current NBA season string (e.g. '2025-26')."""
    now = datetime.datetime.now()
    year = now.year
    month = now.month
    start_year = year if month >= 10 else year - 1
    return f"{start_year}-{str(start_year + 1)[2:]}"


def to_nba_date(date_str):
    """Convert YYYY-MM-DD or YYYYMMDD -> MM/DD/YYYY for NBA.com API."""
    if not date_str:
        return ""
    s = str(date_str).replace("-", "")
    if len(s) == 8:
        return f"{s[4:6]}/{s[6:8]}/{s[0:4]}"
    return date_str


def _fetch_team_stats(date_to=None, date_from=None, last_n_games=0, location="", season_type="Regular Season"):
    """Core fetch -- direct request to stats.nba.com (mirrors jsNBA)."""
    season = current_season()
    params = {
        "MeasureType": "Advanced",
        "PerMode": "PerGame",
        "PaceAdjust": "N",
        "PlusMinus": "N",
        "Rank": "N",
        "Season": season,
        "SeasonType": season_type,
        "DateFrom": to_nba_date(date_from) if date_from else "",
        "DateTo": to_nba_date(date_to) if date_to else "",
        "Outcome": "",
        "Location": location,
        "Month": "0",
        "SeasonSegment": "",
        "OpponentTeamID": "0",
        "VsConference": "",
        "VsDivision": "",
        "GameSegment": "",
        "Period": "0",
        "ShotClockRange": "",
        "LastNGames": str(last_n_games),
    }
    url = "https://stats.nba.com/stats/leaguedashteamstats"

    last_err = None
    json_data = None
    for attempt in range(5):
        try:
            res = requests.get(url, params=params, headers=NBA_HEADERS, timeout=30)
            if res.status_code != 200:
                raise Exception(f"HTTP {res.status_code}")
            text = res.text
            if not text or not text.strip():
                raise Exception("empty response body")
            import json as _json
            json_data = _json.loads(text)
            break
        except Exception as e:
            last_err = e
            wait = 3 * (2 ** attempt)
            print(f"  [nba_stats] fetch attempt {attempt + 1}/5 failed ({e}); retrying in {wait}s...")
            time.sleep(wait)
    if json_data is None:
        raise Exception(f"NBA.com team stats fetch failed after 5 attempts: {last_err}")

    result_sets = json_data.get("resultSets") or []
    if not result_sets:
        raise Exception("NBA.com: no resultSets in response")
    rs = result_sets[0]
    headers = rs.get("headers") or []
    rows = rs.get("rowSet") or []

    def col(name):
        try:
            return headers.index(name)
        except ValueError:
            return -1

    i_team = col("TEAM_NAME")
    i_gp = col("GP")
    i_off = col("OFF_RATING")
    i_def = col("DEF_RATING")
    i_ts = col("TS_PCT")
    i_to = col("TM_TOV_PCT")
    i_orr = col("OREB_PCT")
    i_pace = col("PACE")
    if -1 in (i_team, i_gp, i_off, i_def, i_ts, i_to, i_orr, i_pace):
        raise Exception("NBA.com: missing expected columns in response")

    stats = {}
    for row in rows:
        team_name = str(row[i_team] or "")
        stats[team_name] = {
            "OFF": _safe_float(row[i_off]),
            "DEF": _safe_float(row[i_def]),
            "TS": _safe_float(row[i_ts]),
            "TO": _safe_float(row[i_to]),
            "ORR": _safe_float(row[i_orr]),
            "PACE": _safe_float(row[i_pace]),
            "GP": _safe_float(row[i_gp]),
        }
    return stats


def _safe_float(val):
    """Convert value to float, defaulting to 0.0."""
    try:
        return float(val) if val is not None else 0.0
    except (ValueError, TypeError):
        return 0.0


# -- Public: season stats (backward compatible) --

def fetch_nba_stats(date_to=None, date_from=None, season_type="Regular Season"):
    if date_from:
        range_str = f"{to_nba_date(date_from)} -> {to_nba_date(date_to) if date_to else 'today'}"
    elif date_to:
        range_str = f"up to {to_nba_date(date_to)}"
    else:
        range_str = "full season"
    print(f"  [nba_stats] Fetching stats ({range_str}) [{season_type}]...")

    stats = _fetch_team_stats(date_to, date_from, season_type=season_type)
    count = len(stats)
    skipped = sum(1 for s in stats.values() if s["GP"] < 10)
    print(f"  [nba_stats] Got {count} teams ({skipped} with < 10 games)")

    # Filter out teams with too few games
    stats = {k: v for k, v in stats.items() if v["GP"] >= 10}

    if len(stats) < 20:
        raise Exception(f"NBA.com stats incomplete: only {len(stats)} teams")

    return stats


# -- Blend playoff + regular season stats per team --

STAT_KEYS = ["OFF", "DEF", "TS", "TO", "ORR", "PACE"]
PLAYOFF_RAMP_GAMES = 16  # full playoff weight after this many games


def _blend_playoff_stats(reg_season, playoff_stats):
    if not playoff_stats or not len(playoff_stats):
        return reg_season

    blended = {}
    blend_count = 0

    for team, rs in reg_season.items():
        po = playoff_stats.get(team)
        if not po or not po.get("GP") or po["GP"] < 1:
            blended[team] = dict(rs)
            continue

        po_weight = min(po["GP"] / PLAYOFF_RAMP_GAMES, 1.0)
        out = dict(rs)
        for k in STAT_KEYS:
            po_val = po.get(k)
            rs_val = rs.get(k)
            if isinstance(po_val, (int, float)) and math.isfinite(po_val) and isinstance(rs_val, (int, float)) and math.isfinite(rs_val):
                out[k] = rs_val * (1 - po_weight) + po_val * po_weight
        out["GP"] = rs["GP"]  # keep regular season GP for min-games checks
        blended[team] = out
        blend_count += 1

    if blend_count > 0:
        print(f"  [nba_stats] Blended playoff+regular for {blend_count} teams")
    return blended


# -- Public: enhanced stats (season + last10 + home + away) --

def _load_cached_enhanced(date_to=None):
    """Load enhanced stats from data/stats_cache/nba/<date>.json (or latest)."""
    import os
    import json as _json
    here = os.path.dirname(os.path.abspath(__file__))
    cache_dir = os.path.normpath(os.path.join(here, "..", "..", "..", "data", "stats_cache", "nba"))
    if not os.path.isdir(cache_dir):
        return None
    target = None
    if date_to:
        candidate = os.path.join(cache_dir, str(date_to).replace("-", "") + ".json")
        if os.path.isfile(candidate):
            target = candidate
    if not target:
        # Fall back to most recent cache file
        files = sorted(f for f in os.listdir(cache_dir) if f.endswith(".json"))
        if not files:
            return None
        target = os.path.join(cache_dir, files[-1])
    try:
        with open(target, "r", encoding="utf-8") as f:
            data = _json.load(f)
        season = data.get("season") or {}
        if len(season) >= 20:
            print(f"  [nba_stats] Using cached stats from {os.path.basename(target)} ({len(season)} teams)")
            return data
    except Exception as e:
        print(f"  [nba_stats] Cache load failed ({e})")
    return None


def fetch_nba_stats_enhanced(date_to=None, season_type="Regular Season"):
    """
    Returns { "season": ..., "last10": ..., "home": ..., "away": ... }
    all keyed by full team name.
    In playoffs: fetches both regular season + playoff stats and blends them.
    """
    in_playoffs = season_type == "Playoffs"

    print(f"  [nba_stats] Fetching enhanced stats (season + last10 + home + away) [{season_type}]...")

    # Check shared stats cache first -- stats.nba.com is often blocked from runners
    cached = _load_cached_enhanced(date_to)
    if cached is not None:
        season = cached.get("season") or {}
        season = {k: v for k, v in season.items() if v.get("GP", 0) >= 10}
        if len(season) >= 20:
            return {
                "season": season,
                "last10": cached.get("last10"),
                "home": cached.get("home"),
                "away": cached.get("away"),
            }

    # In playoffs: fetch regular season as base, then overlay playoff stats
    reg_season = None
    if in_playoffs:
        reg_season = _fetch_team_stats(date_to, None, season_type="Regular Season")
        print(f"  [nba_stats] Regular season base: {len(reg_season)} teams")
        time.sleep(2)

    try:
        raw_season = _fetch_team_stats(date_to, None, season_type=season_type)
    except Exception as e:
        if in_playoffs:
            print(f"  [nba_stats] Playoffs stats empty (playoffs just started?): {e}. Using regular season as fallback.")
            raw_season = reg_season
        else:
            raise
    season = _blend_playoff_stats(reg_season, raw_season) if in_playoffs else raw_season
    time.sleep(2)

    last10 = None
    home = None
    away = None

    try:
        # In playoffs, use regular season L10 (more stable)
        l10_type = "Regular Season" if in_playoffs else season_type
        # LastNGames counts from TODAY, not DateTo -- useless for historical dates.
        # Fix: when date_to is provided, use a DateFrom window (~25 days back) instead.
        if date_to:
            s = str(date_to).replace("-", "")
            dt = datetime.datetime(int(s[:4]), int(s[4:6]), int(s[6:8]))
            from_dt = dt - datetime.timedelta(days=25)
            date_from = from_dt.strftime("%Y-%m-%d")
            last10 = _fetch_team_stats(date_to, date_from, season_type=l10_type)
        else:
            last10 = _fetch_team_stats(date_to, None, last_n_games=10, season_type=l10_type)
        print(f"  [nba_stats] Last 10: {len(last10)} teams")
        time.sleep(2)
    except Exception as e:
        print(f"  [nba_stats] Last 10 fetch failed ({e}) -- skipping")
        last10 = None

    try:
        split_type = "Regular Season" if in_playoffs else season_type
        home = _fetch_team_stats(date_to, None, location="Home", season_type=split_type)
        print(f"  [nba_stats] Home splits: {len(home)} teams")
        time.sleep(2)
    except Exception as e:
        print(f"  [nba_stats] Home fetch failed ({e}) -- skipping")
        home = None

    try:
        split_type = "Regular Season" if in_playoffs else season_type
        away = _fetch_team_stats(date_to, None, location="Road", season_type=split_type)
        print(f"  [nba_stats] Away splits: {len(away)} teams")
    except Exception as e:
        print(f"  [nba_stats] Away fetch failed ({e}) -- skipping")
        away = None

    # Filter season stats for min games (backward compat)
    season = {k: v for k, v in season.items() if v["GP"] >= 10}
    count = len(season)
    print(f"  [nba_stats] Enhanced: {count} teams (season), {len(last10) if last10 else 0} (L10), {len(home) if home else 0} (home), {len(away) if away else 0} (away)")

    if count < 20:
        raise Exception(f"NBA.com stats incomplete: only {count} teams")

    return {"season": season, "last10": last10, "home": home, "away": away}
