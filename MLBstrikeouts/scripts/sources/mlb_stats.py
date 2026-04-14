"""
MLB Stats API fetcher.
Uses statsapi.mlb.com (free, no auth required).
"""

import requests
import json
import os
import time
from datetime import datetime, date
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "pitcher_cache" / "mlb"
CACHE_FRESHNESS_HOURS = 4

MLB_TEAM_ID_TO_ABBR = {
    108: "LAA", 109: "ARI", 110: "BAL", 111: "BOS", 112: "CHC",
    113: "CIN", 114: "CLE", 115: "COL", 116: "DET", 117: "HOU",
    118: "KC",  119: "LAD", 120: "WSH", 121: "NYM", 133: "OAK",
    134: "PIT", 135: "SD",  136: "SEA", 137: "SF",  138: "STL",
    139: "TB",  140: "TEX", 141: "TOR", 142: "MIN", 143: "PHI",
    144: "ATL", 145: "CWS", 146: "MIA", 147: "NYY", 158: "MIL",
}

BASE_URL = "https://statsapi.mlb.com/api/v1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ip_to_outs(ip_str):
    """Convert innings pitched string (e.g. '6.2') to total outs."""
    try:
        ip_str = str(ip_str)
        if "." in ip_str:
            whole, frac = ip_str.split(".")
            return int(whole) * 3 + int(frac)
        return int(ip_str) * 3
    except (ValueError, TypeError):
        return 0


def _ip_to_float(ip_str):
    """Convert innings pitched string (e.g. '6.2') to float (6.667)."""
    try:
        ip_str = str(ip_str)
        if "." in ip_str:
            whole, frac = ip_str.split(".")
            return int(whole) + int(frac) / 3.0
        return float(ip_str)
    except (ValueError, TypeError):
        return 0.0


def _load_cache(cache_path):
    """Load cached JSON if it exists and is fresh enough."""
    if not cache_path.exists():
        return None
    try:
        age_hours = (time.time() - cache_path.stat().st_mtime) / 3600
        if age_hours > CACHE_FRESHNESS_HOURS:
            return None
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _save_cache(cache_path, data):
    """Save data to cache file."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _current_season():
    return datetime.now().year


def _fetch_json(url):
    """Fetch JSON from URL with basic error handling."""
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# 1. Pitcher Game Logs
# ---------------------------------------------------------------------------

def fetch_pitcher_game_logs(season=None):
    """
    Fetch pitcher game logs for the season.
    Returns list of dicts with per-game pitching stats.
    """
    season = season or _current_season()
    cache_path = CACHE_DIR / f"game_logs_{season}.json"

    cached = _load_cache(cache_path)
    if cached is not None:
        return cached

    url = (
        f"{BASE_URL}/stats?stats=gameLog&group=pitching&season={season}"
        f"&sportId=1&playerPool=all&limit=10000&gameType=R"
    )
    raw = _fetch_json(url)
    time.sleep(0.5)

    rows = []
    for stat_group in raw.get("stats", []):
        for split in stat_group.get("splits", []):
            stat = split.get("stat", {})
            player_info = split.get("player", {})
            team_info = split.get("team", {})
            game_info = split.get("game", {})
            opp_info = split.get("opponent", {})

            ip_str = stat.get("inningsPitched", "0")

            row = {
                "player_id": player_info.get("id"),
                "player_name": player_info.get("fullName", ""),
                "team": team_info.get("abbreviation", ""),
                "game_date": split.get("date", ""),
                "game_id": game_info.get("gamePk"),
                "k": stat.get("strikeOuts", 0),
                "ip": _ip_to_float(ip_str),
                "ip_str": ip_str,
                "outs": _ip_to_outs(ip_str),
                "h": stat.get("hits", 0),
                "bb": stat.get("baseOnBalls", 0),
                "er": stat.get("earnedRuns", 0),
                "pitches": stat.get("numberOfPitches", 0),
                "opponent": opp_info.get("abbreviation", ""),
                "home_away": split.get("homeOrAway", ""),
            }
            rows.append(row)

    _save_cache(cache_path, rows)
    return rows


# ---------------------------------------------------------------------------
# 2. Pitcher Advanced Stats
# ---------------------------------------------------------------------------

def fetch_pitcher_advanced_stats(season=None):
    """
    Fetch season-level pitching stats (K/9, BB/9, WHIP, etc.).
    Returns dict keyed by str(player_id).
    """
    season = season or _current_season()
    cache_path = CACHE_DIR / f"pitcher_advanced_{season}.json"

    cached = _load_cache(cache_path)
    if cached is not None:
        return cached

    url = (
        f"{BASE_URL}/stats?stats=season&group=pitching&season={season}"
        f"&sportId=1&playerPool=all&limit=500"
    )
    raw = _fetch_json(url)
    time.sleep(0.5)

    result = {}
    for stat_group in raw.get("stats", []):
        for split in stat_group.get("splits", []):
            stat = split.get("stat", {})
            player_info = split.get("player", {})
            pid = str(player_info.get("id", ""))
            if not pid:
                continue

            result[pid] = {
                "player_name": player_info.get("fullName", ""),
                "team": split.get("team", {}).get("abbreviation", ""),
                "k_per_9": stat.get("strikeoutsPer9Inn", 0),
                "bb_per_9": stat.get("walksPer9Inn", 0),
                "whip": stat.get("whip", 0),
                "era": stat.get("era", 0),
                "ip": _ip_to_float(stat.get("inningsPitched", "0")),
                "k": stat.get("strikeOuts", 0),
                "bb": stat.get("baseOnBalls", 0),
                "h": stat.get("hits", 0),
                "hr": stat.get("homeRuns", 0),
                "games": stat.get("gamesPlayed", 0),
                "games_started": stat.get("gamesStarted", 0),
            }

    _save_cache(cache_path, result)
    return result


# ---------------------------------------------------------------------------
# 3. Pitcher Sabermetrics (FIP, xFIP)
# ---------------------------------------------------------------------------

def fetch_pitcher_sabermetrics(season=None):
    """
    Fetch sabermetric pitching stats (FIP, xFIP, etc.).
    Returns dict keyed by str(player_id).
    """
    season = season or _current_season()
    cache_path = CACHE_DIR / f"pitcher_sabermetrics_{season}.json"

    cached = _load_cache(cache_path)
    if cached is not None:
        return cached

    url = (
        f"{BASE_URL}/stats?stats=sabermetrics&group=pitching&season={season}"
        f"&sportId=1&playerPool=all&limit=500"
    )
    raw = _fetch_json(url)
    time.sleep(0.5)

    result = {}
    for stat_group in raw.get("stats", []):
        for split in stat_group.get("splits", []):
            stat = split.get("stat", {})
            player_info = split.get("player", {})
            pid = str(player_info.get("id", ""))
            if not pid:
                continue

            result[pid] = {
                "player_name": player_info.get("fullName", ""),
                "fip": stat.get("fip", None),
                "xfip": stat.get("xfip", None),
                "babip": stat.get("babip", None),
                "k_pct": stat.get("strikeoutPercentage", None),
                "bb_pct": stat.get("walkPercentage", None),
            }

    _save_cache(cache_path, result)
    return result


# ---------------------------------------------------------------------------
# 4. Pitcher Handedness Splits (vs LHB / vs RHB)
# ---------------------------------------------------------------------------

def fetch_pitcher_handedness_splits(pitcher_id, season=None):
    """
    Fetch pitcher splits vs left-handed and right-handed batters.
    Returns dict with vs_left and vs_right keys.
    """
    season = season or _current_season()
    cache_path = CACHE_DIR / f"handedness_{pitcher_id}_{season}.json"

    cached = _load_cache(cache_path)
    if cached is not None:
        return cached

    url = (
        f"{BASE_URL}/people/{pitcher_id}/stats"
        f"?stats=statSplits&group=pitching&season={season}&sitCodes=vl,vr"
    )
    raw = _fetch_json(url)
    time.sleep(0.5)

    result = {"vs_left": {}, "vs_right": {}}
    for stat_group in raw.get("stats", []):
        for split in stat_group.get("splits", []):
            stat = split.get("stat", {})
            split_code = split.get("split", {}).get("code", "")

            split_data = {
                "ba": stat.get("avg", None),
                "obp": stat.get("obp", None),
                "slg": stat.get("slg", None),
                "ops": stat.get("ops", None),
                "k": stat.get("strikeOuts", 0),
                "bb": stat.get("baseOnBalls", 0),
                "h": stat.get("hits", 0),
                "ab": stat.get("atBats", 0),
                "pa": stat.get("plateAppearances", 0),
                "hr": stat.get("homeRuns", 0),
            }

            if split_code == "vl":
                result["vs_left"] = split_data
            elif split_code == "vr":
                result["vs_right"] = split_data

    _save_cache(cache_path, result)
    return result


# ---------------------------------------------------------------------------
# 5. Team Batting Stats
# ---------------------------------------------------------------------------

def fetch_team_batting_stats(season=None):
    """
    Fetch team-level batting stats.
    Returns dict keyed by team abbreviation.
    """
    season = season or _current_season()
    cache_path = CACHE_DIR / f"team_batting_{season}.json"

    cached = _load_cache(cache_path)
    if cached is not None:
        return cached

    url = (
        f"{BASE_URL}/teams/stats?stats=season&group=hitting"
        f"&season={season}&sportId=1"
    )
    raw = _fetch_json(url)
    time.sleep(0.5)

    result = {}
    for stat_group in raw.get("stats", []):
        for split in stat_group.get("splits", []):
            stat = split.get("stat", {})
            team_info = split.get("team", {})
            team_id = team_info.get("id")
            abbr = MLB_TEAM_ID_TO_ABBR.get(team_id, team_info.get("abbreviation", ""))
            if not abbr:
                continue

            pa = stat.get("plateAppearances", 0)
            k = stat.get("strikeOuts", 0)
            bb = stat.get("baseOnBalls", 0)

            result[abbr] = {
                "K_PCT": round(k / pa, 4) if pa else 0,
                "BA": stat.get("avg", None),
                "OPS": stat.get("ops", None),
                "BB_PCT": round(bb / pa, 4) if pa else 0,
                "PA": pa,
            }

    _save_cache(cache_path, result)
    return result


# ---------------------------------------------------------------------------
# 6. Team Pitching Stats
# ---------------------------------------------------------------------------

def fetch_team_pitching_stats(season=None):
    """
    Fetch team-level pitching stats.
    Returns dict keyed by team abbreviation.
    """
    season = season or _current_season()
    cache_path = CACHE_DIR / f"team_pitching_{season}.json"

    cached = _load_cache(cache_path)
    if cached is not None:
        return cached

    url = (
        f"{BASE_URL}/teams/stats?stats=season&group=pitching"
        f"&season={season}&sportId=1"
    )
    raw = _fetch_json(url)
    time.sleep(0.5)

    result = {}
    for stat_group in raw.get("stats", []):
        for split in stat_group.get("splits", []):
            stat = split.get("stat", {})
            team_info = split.get("team", {})
            team_id = team_info.get("id")
            abbr = MLB_TEAM_ID_TO_ABBR.get(team_id, team_info.get("abbreviation", ""))
            if not abbr:
                continue

            result[abbr] = {
                "ERA": stat.get("era", None),
                "WHIP": stat.get("whip", None),
                "H_PER_9": stat.get("hitsPer9Inn", None),
                "K_PER_9": stat.get("strikeoutsPer9Inn", None),
            }

    _save_cache(cache_path, result)
    return result


# ---------------------------------------------------------------------------
# 7. Probable Pitchers
# ---------------------------------------------------------------------------

def fetch_today_probable_pitchers(date_str=None):
    """
    Fetch schedule with probable pitchers for a given date.
    Returns list of game dicts with home/away team and pitcher info.
    """
    if date_str is None:
        date_str = date.today().strftime("%Y-%m-%d")

    cache_path = CACHE_DIR / f"probable_pitchers_{date_str}.json"

    cached = _load_cache(cache_path)
    if cached is not None:
        return cached

    url = (
        f"{BASE_URL}/schedule?sportId=1&date={date_str}"
        f"&hydrate=probablePitcher"
    )
    raw = _fetch_json(url)
    time.sleep(0.5)

    games = []
    for game_date in raw.get("dates", []):
        for game in game_date.get("games", []):
            home_team = game.get("teams", {}).get("home", {})
            away_team = game.get("teams", {}).get("away", {})
            home_pitcher = home_team.get("probablePitcher", {})
            away_pitcher = away_team.get("probablePitcher", {})

            home_team_id = home_team.get("team", {}).get("id")
            away_team_id = away_team.get("team", {}).get("id")

            games.append({
                "game_id": game.get("gamePk"),
                "game_date": date_str,
                "game_time": game.get("gameDate", ""),
                "status": game.get("status", {}).get("detailedState", ""),
                "home_team": MLB_TEAM_ID_TO_ABBR.get(home_team_id, ""),
                "home_team_id": home_team_id,
                "away_team": MLB_TEAM_ID_TO_ABBR.get(away_team_id, ""),
                "away_team_id": away_team_id,
                "home_pitcher_id": home_pitcher.get("id"),
                "home_pitcher_name": home_pitcher.get("fullName", ""),
                "away_pitcher_id": away_pitcher.get("id"),
                "away_pitcher_name": away_pitcher.get("fullName", ""),
            })

    _save_cache(cache_path, games)
    return games


# ---------------------------------------------------------------------------
# Main (quick test)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Fetching probable pitchers...")
    pitchers = fetch_today_probable_pitchers()
    for g in pitchers:
        away = g["away_team"]
        away_p = g["away_pitcher_name"]
        home = g["home_team"]
        home_p = g["home_pitcher_name"]
        print(f"  {away} ({away_p}) @ {home} ({home_p})")
    print(f"\n{len(pitchers)} games found.")
