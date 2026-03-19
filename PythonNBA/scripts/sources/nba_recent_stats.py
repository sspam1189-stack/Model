# scripts/sources/nba_recent_stats.py
# Fetches team stats from NBA.com scoped to a date range (e.g. post-trade-deadline).
# Blends with full-season Hollinger stats to prevent small-sample volatility.

import requests
import datetime
import math
import re

NBA_BASE = "https://stats.nba.com/stats/leaguedashteamstats"


def _current_season():
    now = datetime.datetime.now()
    year = now.year
    month = now.month
    start_year = year if month >= 10 else year - 1
    return f"{start_year}-{str(start_year + 1)[2:]}"


SEASON = _current_season()

NBA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
    "Connection": "keep-alive",
}


def _today_mmddyyyy():
    d = datetime.datetime.now()
    return f"{d.month:02d}/{d.day:02d}/{d.year}"


def _norm_key(s):
    s = str(s or "").lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"  +", " ", s)
    return s.strip()


# Map NBA.com full team names -> Hollinger city keys
NBA_TO_HOLLINGER = {
    "atlanta hawks": "Atlanta",
    "boston celtics": "Boston",
    "brooklyn nets": "Brooklyn",
    "charlotte hornets": "Charlotte",
    "chicago bulls": "Chicago",
    "cleveland cavaliers": "Cleveland",
    "dallas mavericks": "Dallas",
    "denver nuggets": "Denver",
    "detroit pistons": "Detroit",
    "golden state warriors": "Golden State",
    "houston rockets": "Houston",
    "indiana pacers": "Indiana",
    "la clippers": "LA Clippers",
    "los angeles clippers": "LA Clippers",
    "la lakers": "LA Lakers",
    "los angeles lakers": "LA Lakers",
    "memphis grizzlies": "Memphis",
    "miami heat": "Miami",
    "milwaukee bucks": "Milwaukee",
    "minnesota timberwolves": "Minnesota",
    "new orleans pelicans": "New Orleans",
    "new york knicks": "New York",
    "oklahoma city thunder": "Oklahoma City",
    "orlando magic": "Orlando",
    "philadelphia 76ers": "Philadelphia",
    "phoenix suns": "Phoenix",
    "portland trail blazers": "Portland",
    "sacramento kings": "Sacramento",
    "san antonio spurs": "San Antonio",
    "toronto raptors": "Toronto",
    "utah jazz": "Utah",
    "washington wizards": "Washington",
}


def _map_team_name(nba_name):
    k = _norm_key(nba_name)
    return NBA_TO_HOLLINGER.get(k)


def _fetch_nba_stats(measure_type, date_from, date_to):
    params = {
        "MeasureType": measure_type,
        "PerMode": "PerGame",
        "PaceAdjust": "N",
        "PlusMinus": "N",
        "Rank": "N",
        "Season": SEASON,
        "SeasonType": "Regular Season",
        "DateFrom": date_from,
        "DateTo": date_to,
        "Outcome": "",
        "Location": "",
        "Month": "0",
        "SeasonSegment": "",
        "OpponentTeamID": "0",
        "VsConference": "",
        "VsDivision": "",
        "GameSegment": "",
        "Period": "0",
        "ShotClockRange": "",
        "LastNGames": "0",
    }

    res = requests.get(NBA_BASE, params=params, headers=NBA_HEADERS, timeout=30)
    if res.status_code != 200:
        raise Exception(f"NBA.com {measure_type} failed: {res.status_code}")

    json_data = res.json()
    rs = (json_data.get("resultSets") or [None])[0]
    if not rs or not rs.get("headers") or not rs.get("rowSet"):
        raise Exception(f"NBA.com {measure_type}: unexpected response shape")

    # Convert to map: hollinger_key -> { col_name: value }
    out = {}
    for row in rs["rowSet"]:
        obj = {}
        for i, h in enumerate(rs["headers"]):
            obj[h] = row[i]
        team_name = obj.get("TEAM_NAME", "")
        key = _map_team_name(team_name)
        if key:
            out[key] = obj
    return out


def fetch_blended_stats(hollinger_stats, date_from, recent_weight=0.65):
    """
    Fetch post-deadline stats and blend with full-season Hollinger stats.
    recent_weight: 0.0-1.0, how much to weight recent stats (default 0.65)
    """
    date_to = _today_mmddyyyy()

    advanced_map = None
    min_games = None

    try:
        print(f"  [nba_recent] Fetching post-deadline stats ({date_from} -> {date_to})...")
        advanced_map = _fetch_nba_stats("Advanced", date_from, date_to)

        game_counts = [r.get("GP", 0) for r in advanced_map.values()]
        min_games = min(game_counts) if game_counts else 0
        print(f"  [nba_recent] Got {len(advanced_map)} teams, min games: {min_games}")
    except Exception as e:
        print(f"  [nba_recent] NBA.com fetch failed ({e}) -- using Hollinger only")
        return hollinger_stats

    # If too few games (< 5), don't trust recent stats -- fall back to Hollinger
    if min_games < 5:
        print(f"  [nba_recent] Too few games ({min_games}) -- using Hollinger only")
        return hollinger_stats

    w = recent_weight
    blended = dict(hollinger_stats)

    for key, h in hollinger_stats.items():
        recent = advanced_map.get(key)
        if not recent:
            print(f"  [nba_recent] No recent data for {key} -- keeping Hollinger")
            continue

        r_off = float(recent.get("OFF_RATING", 0))
        r_def = float(recent.get("DEF_RATING", 0))
        r_pace = float(recent.get("PACE", 0))
        r_ts = float(recent.get("TS_PCT", 0))
        r_to = float(recent.get("TM_TOV_PCT", 0))
        r_orr = float(recent.get("OREB_PCT", 0))

        blended[key] = {
            **h,
            "OFF": (w * r_off + (1 - w) * h["OFF"]) if math.isfinite(r_off) else h["OFF"],
            "DEF": (w * r_def + (1 - w) * h["DEF"]) if math.isfinite(r_def) else h["DEF"],
            "PACE": (w * r_pace + (1 - w) * h["PACE"]) if math.isfinite(r_pace) else h["PACE"],
            "TS": (w * r_ts + (1 - w) * h["TS"]) if math.isfinite(r_ts) else h["TS"],
            "TO": (w * r_to + (1 - w) * h["TO"]) if math.isfinite(r_to) else h["TO"],
            "ORR": (w * r_orr + (1 - w) * h["ORR"]) if math.isfinite(r_orr) else h["ORR"],
        }

    return blended
