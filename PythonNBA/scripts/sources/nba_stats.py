# scripts/sources/nba_stats.py
# Fetches NBA.com team stats -- full season, last N games, home/away splits.
#
# Usage:
#   fetch_nba_stats("2026-01-30")              -> season stats as of Jan 30
#   fetch_nba_stats()                           -> season stats as of today
#   fetch_nba_stats_enhanced("20260301")        -> { season, last10, home, away }

import requests
import time
import datetime
import math

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
    """Core fetch -- builds the NBA.com API URL with optional overrides."""
    season = current_season()
    date_to_param = to_nba_date(date_to) if date_to else ""
    date_from_param = to_nba_date(date_from) if date_from else ""

    params = {
        "MeasureType": "Advanced",
        "PerMode": "PerGame",
        "PaceAdjust": "N",
        "PlusMinus": "N",
        "Rank": "N",
        "Season": season,
        "SeasonType": season_type,
        "DateFrom": date_from_param,
        "DateTo": date_to_param,
        "Outcome": "",
        "Location": location,  # "" = all, "Home" = home, "Road" = away
        "Month": "0",
        "SeasonSegment": "",
        "OpponentTeamID": "0",
        "VsConference": "",
        "VsDivision": "",
        "GameSegment": "",
        "Period": "0",
        "ShotClockRange": "",
        "LastNGames": str(last_n_games),  # 0 = all games
    }

    url = "https://stats.nba.com/stats/leaguedashteamstats"
    res = None
    for attempt in range(1, 4):
        try:
            res = requests.get(url, params=params, headers=NBA_HEADERS, timeout=30)
            if res.status_code == 200:
                break
            raise Exception(f"NBA.com stats failed: {res.status_code}")
        except Exception as err:
            if attempt == 3:
                raise
            print(f"  Warning: NBA.com fetch attempt {attempt}/3 failed ({err}), retrying in {attempt * 5}s...")
            time.sleep(attempt * 5)

    if res is None or res.status_code != 200:
        raise Exception(f"NBA.com stats failed: {res.status_code if res else 'no response'}")

    json_data = res.json()
    result_sets = json_data.get("resultSets")
    if not result_sets or not len(result_sets):
        raise Exception("NBA.com: no resultSet in response")
    result_set = result_sets[0]

    headers = result_set.get("headers", [])
    rows = result_set.get("rowSet", [])

    def idx(name):
        return headers.index(name) if name in headers else -1

    i_team_name = idx("TEAM_NAME")
    i_gp = idx("GP")
    i_off = idx("OFF_RATING")
    i_def = idx("DEF_RATING")
    i_ts = idx("TS_PCT")
    i_to = idx("TM_TOV_PCT")
    i_orr = idx("OREB_PCT")
    i_pace = idx("PACE")

    if -1 in [i_team_name, i_gp, i_off, i_def, i_ts, i_to, i_orr, i_pace]:
        raise Exception("NBA.com: missing expected columns in response")

    stats = {}
    for row in rows:
        team_name = row[i_team_name]
        gp = float(row[i_gp])
        stats[team_name] = {
            "OFF": float(row[i_off]),
            "DEF": float(row[i_def]),
            "TS": float(row[i_ts]),
            "TO": float(row[i_to]),
            "ORR": float(row[i_orr]),
            "PACE": float(row[i_pace]),
            "GP": gp,
        }
    return stats


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

def fetch_nba_stats_enhanced(date_to=None, season_type="Regular Season"):
    """
    Returns { "season": ..., "last10": ..., "home": ..., "away": ... }
    all keyed by full team name.
    In playoffs: fetches both regular season + playoff stats and blends them.
    """
    in_playoffs = season_type == "Playoffs"

    print(f"  [nba_stats] Fetching enhanced stats (season + last10 + home + away) [{season_type}]...")

    # In playoffs: fetch regular season as base, then overlay playoff stats
    reg_season = None
    if in_playoffs:
        reg_season = _fetch_team_stats(date_to, None, season_type="Regular Season")
        print(f"  [nba_stats] Regular season base: {len(reg_season)} teams")
        time.sleep(2)

    raw_season = _fetch_team_stats(date_to, None, season_type=season_type)
    season = _blend_playoff_stats(reg_season, raw_season) if in_playoffs else raw_season
    time.sleep(2)

    last10 = None
    home = None
    away = None

    try:
        if in_playoffs:
            last10 = _fetch_team_stats(date_to, None, last_n_games=10, season_type="Regular Season")
        else:
            last10 = _fetch_team_stats(date_to, None, last_n_games=10, season_type=season_type)
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
