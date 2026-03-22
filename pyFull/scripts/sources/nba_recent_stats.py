import requests
import math
from datetime import datetime
from urllib.parse import urlencode

NBA_BASE = "https://stats.nba.com/stats/leaguedashteamstats"


def current_season():
    now = datetime.now()
    year = now.year
    month = now.month
    start_year = year if month >= 10 else year - 1
    return f"{start_year}-{str(start_year + 1)[2:]}"


SEASON = current_season()

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


def today_mmddyyyy():
    d = datetime.now()
    return f"{d.month:02d}/{d.day:02d}/{d.year}"


def norm_key(s):
    import re
    return re.sub(r"  +", " ", re.sub(r"[^a-z0-9 ]", " ", str(s or "").lower())).strip()


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


def map_team_name(nba_name):
    k = norm_key(nba_name)
    return NBA_TO_HOLLINGER.get(k)


def fetch_nba_stats(measure_type, date_from, date_to):
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

    url = f"{NBA_BASE}?{urlencode(params)}"
    res = requests.get(url, headers=NBA_HEADERS)
    if res.status_code != 200:
        raise Exception(f"NBA.com {measure_type} failed: {res.status_code}")

    json_data = res.json()
    rs = (json_data.get("resultSets") or [None])[0]
    if not rs or not rs.get("headers") or not rs.get("rowSet"):
        raise Exception(f"NBA.com {measure_type}: unexpected response shape")

    out = {}
    for row in rs["rowSet"]:
        obj = {}
        for i, h in enumerate(rs["headers"]):
            obj[h] = row[i]
        team_name = obj.get("TEAM_NAME", "")
        key = map_team_name(team_name)
        if key:
            out[key] = obj
    return out


def fetch_blended_stats(hollinger_stats, date_from, recent_weight=0.65):
    """
    Fetch post-deadline stats and blend with full-season Hollinger stats.
    recent_weight: 0.0-1.0, how much to weight recent stats (default 0.65)
    """
    date_to = today_mmddyyyy()

    advanced_map = None
    min_games = None

    try:
        print(f"  [nba_recent] Fetching post-deadline stats ({date_from} -> {date_to})...")
        advanced_map = fetch_nba_stats("Advanced", date_from, date_to)

        game_counts = [r.get("GP", 0) for r in advanced_map.values()]
        min_games = min(game_counts) if game_counts else 0
        print(f"  [nba_recent] Got {len(advanced_map)} teams, min games: {min_games}")
    except Exception as e:
        print(f"  [nba_recent] NBA.com fetch failed ({e}) -- using Hollinger only")
        return hollinger_stats

    # If too few games (< 5), don't trust recent stats
    if min_games < 5:
        print(f"  [nba_recent] Too few games ({min_games}) -- using Hollinger only")
        return hollinger_stats

    w = recent_weight
    blended = {**hollinger_stats}

    for key, h in hollinger_stats.items():
        recent = advanced_map.get(key)
        if not recent:
            print(f"  [nba_recent] No recent data for {key} -- keeping Hollinger")
            continue

        r_off = float(recent.get("OFF_RATING", float("nan")))
        r_def = float(recent.get("DEF_RATING", float("nan")))
        r_pace = float(recent.get("PACE", float("nan")))
        r_ts = float(recent.get("TS_PCT", float("nan")))
        r_to = float(recent.get("TM_TOV_PCT", float("nan")))
        r_orr = float(recent.get("OREB_PCT", float("nan")))

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
