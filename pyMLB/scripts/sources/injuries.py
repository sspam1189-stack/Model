# pyMLB/scripts/sources/injuries.py
# Fetch MLB injury data from ESPN's public API.
# Also extracts probable pitchers from scoreboard data.

import os
import json
import requests
import datetime as _dt

_HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.normpath(os.path.join(_HERE, "..", "..", "..", "data", "injury_cache", "mlb"))

ESPN_INJURIES_URL = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/injuries"
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"

HEADERS = {"user-agent": "mlb-picks-bot/1.0"}


def fetch_injury_data(date_str=None):
    """
    Fetch injury report for all MLB teams from ESPN.
    Returns: {"report": {team_name: [{"name", "position", "status", "detail"}]}}
    Caches to disk by date.
    """
    if date_str is None:
        date_str = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d")

    # Check cache
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, date_str + ".json")
    today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d")
    if date_str != today and os.path.exists(cache_path):
        with open(cache_path, "r") as f:
            return json.load(f)

    try:
        res = requests.get(ESPN_INJURIES_URL, headers=HEADERS, timeout=15)
        if res.status_code != 200:
            print(f"  [injuries] ESPN returned {res.status_code}")
            return {"report": {}}
        data = res.json()
    except Exception as e:
        print(f"  [injuries] Fetch error: {e}")
        return {"report": {}}

    report = {}
    for team_entry in data.get("injuries", []):
        team_info = team_entry.get("team", {})
        team_name = team_info.get("displayName", "")
        if not team_name:
            continue

        injuries = []
        for category in team_entry.get("injuries", []):
            for item in category.get("injuries", []):
                athlete = item.get("athlete", {})
                injuries.append({
                    "name": athlete.get("displayName", ""),
                    "position": athlete.get("position", {}).get("abbreviation", ""),
                    "status": item.get("status", ""),
                    "detail": item.get("longComment", "") or item.get("shortComment", ""),
                    "type": item.get("type", {}).get("description", ""),
                })

        if injuries:
            report[team_name] = injuries

    result = {"report": report}

    # Cache
    try:
        with open(cache_path, "w") as f:
            json.dump(result, f, indent=2)
    except Exception:
        pass

    return result


def get_probable_pitchers(date_str=None):
    """
    Extract probable starting pitchers from ESPN scoreboard.
    Returns: {gameId: {"home_pitcher": str, "away_pitcher": str}}
    """
    url = ESPN_SCOREBOARD_URL
    if date_str:
        url += f"?dates={date_str}"

    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
        if res.status_code != 200:
            return {}
        data = res.json()
    except Exception:
        return {}

    probables = {}
    for ev in data.get("events", []):
        game_id = ev.get("id", "")
        comp = (ev.get("competitions") or [None])[0]
        if not comp:
            continue

        home_pitcher = ""
        away_pitcher = ""

        for competitor in comp.get("competitors", []):
            probables_list = competitor.get("probables", [])
            for p in probables_list:
                pitcher_name = (p.get("athlete") or {}).get("displayName", "")
                if competitor.get("homeAway") == "home":
                    home_pitcher = pitcher_name
                else:
                    away_pitcher = pitcher_name

        if home_pitcher or away_pitcher:
            team_away = ""
            team_home = ""
            for c in comp.get("competitors", []):
                dn = (c.get("team") or {}).get("displayName", "")
                if c.get("homeAway") == "home":
                    team_home = dn
                else:
                    team_away = dn

            probables[game_id] = {
                "home_pitcher": home_pitcher,
                "away_pitcher": away_pitcher,
                "home_team": team_home,
                "away_team": team_away,
            }

    return probables


def get_key_injuries(report, team_name):
    """Return key injuries (IL or Out status) for a team."""
    injuries = report.get(team_name, [])
    return [
        inj for inj in injuries
        if inj.get("status", "").lower() in (
            "injured reserve", "10-day il", "15-day il", "60-day il",
            "out", "day-to-day", "suspension",
        )
    ]


def classify_injuries(report, team_name):
    """Classify injuries by severity for a team."""
    injuries = report.get(team_name, [])
    il = [i for i in injuries if "il" in i.get("status", "").lower() or "injured" in i.get("status", "").lower()]
    dtd = [i for i in injuries if "day-to-day" in i.get("status", "").lower()]
    out = [i for i in injuries if i.get("status", "").lower() == "out"]
    return {"il": il, "dtd": dtd, "out": out}
