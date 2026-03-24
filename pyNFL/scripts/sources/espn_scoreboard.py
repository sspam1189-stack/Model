# pyNFL/scripts/sources/espn_scoreboard.py
# Fetches NFL final scores from ESPN's public JSON scoreboard endpoint.

import requests
import math


ESPN_NFL_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"


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

    res = requests.get(url, headers={"user-agent": "nfl-picks-bot/1.0"}, timeout=30)
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

    Args:
        week: NFL week number
        season: 4-digit year (defaults to current season)
        season_type: 1=preseason, 2=regular, 3=postseason

    Returns:
        List of final score dicts from extract_final_scores.
    """
    sb = fetch_nfl_scoreboard(week=week, season=season, season_type=season_type)
    return extract_final_scores(sb)


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
