# MLBstrikeouts/scripts/sources/mlb_schedule.py
# Fetch + cache the MLB schedule (with results) from the MLB Stats API for
# the fade-list moneyline model.
#
# Provides, per game: commence time (UTC), home/away abbreviations, final
# status, and the winner. Used to (a) time the historical closing-line
# snapshot and (b) grade moneyline bets.
#
# Caching mirrors the K model's per-date scheme: past dates whose games are
# all final are permanently cached; today/future are always refetched.

import os
import json
import datetime
import requests
from pathlib import Path

_SCHEDULE_URL = (
    "https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}"
    "&hydrate=linescore,probablePitcher"
)

_CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "odds_cache" / "mlb_ml"

# Void-class statuses (game did not produce a settleable result on this date).
_VOID_STATUSES = {
    "Postponed", "Cancelled", "Canceled", "Suspended",
    "Postponed Inclement Weather", "Postponed Rain",
    "Suspended: Inclement Weather", "Suspended: Rain",
}

# Full MLB team name -> abbreviation, matching the convention used in
# mlb-props.json (KC / SD / SF / TB / WSH / CWS, and OAK for the Athletics).
NAME_TO_ABBR = {
    "Arizona Diamondbacks": "ARI", "Atlanta Braves": "ATL",
    "Baltimore Orioles": "BAL", "Boston Red Sox": "BOS",
    "Chicago Cubs": "CHC", "Chicago White Sox": "CWS",
    "Cincinnati Reds": "CIN", "Cleveland Guardians": "CLE",
    "Colorado Rockies": "COL", "Detroit Tigers": "DET",
    "Houston Astros": "HOU", "Kansas City Royals": "KC",
    "Los Angeles Angels": "LAA", "Los Angeles Dodgers": "LAD",
    "Miami Marlins": "MIA", "Milwaukee Brewers": "MIL",
    "Minnesota Twins": "MIN", "New York Mets": "NYM",
    "New York Yankees": "NYY",
    "Athletics": "OAK", "Oakland Athletics": "OAK",
    "Sacramento Athletics": "OAK",
    "Philadelphia Phillies": "PHI", "Pittsburgh Pirates": "PIT",
    "San Diego Padres": "SD", "San Francisco Giants": "SF",
    "Seattle Mariners": "SEA", "St. Louis Cardinals": "STL",
    "Tampa Bay Rays": "TB", "Texas Rangers": "TEX",
    "Toronto Blue Jays": "TOR", "Washington Nationals": "WSH",
    # Alternates seen from odds feeds.
    "Cleveland Indians": "CLE",
}


def team_abbr(full_name):
    """Full team name -> props-convention abbreviation, or None if unknown."""
    if not full_name:
        return None
    return NAME_TO_ABBR.get(full_name.strip())


def _cache_path(date_key):
    return _CACHE_DIR / f"schedule_{date_key}.json"


def _parse_games(payload):
    games = []
    for d in payload.get("dates", []):
        for g in d.get("games", []):
            status = g.get("status", {})
            abstract = status.get("abstractGameState", "")
            detailed = status.get("detailedState", "")
            teams = g.get("teams", {})
            home = teams.get("home", {})
            away = teams.get("away", {})
            home_abbr = team_abbr((home.get("team") or {}).get("name"))
            away_abbr = team_abbr((away.get("team") or {}).get("name"))
            is_final = abstract == "Final"
            void = detailed in _VOID_STATUSES
            home_win = None
            if is_final and not void:
                # isWinner is present on the winning side only.
                if home.get("isWinner") is True:
                    home_win = True
                elif away.get("isWinner") is True:
                    home_win = False
            games.append({
                "gamePk": g.get("gamePk"),
                "commence": g.get("gameDate"),  # UTC ISO
                "home": home_abbr,
                "away": away_abbr,
                "home_name": (home.get("team") or {}).get("name"),
                "away_name": (away.get("team") or {}).get("name"),
                "home_score": home.get("score"),
                "away_score": away.get("score"),
                "home_pitcher": (home.get("probablePitcher") or {}).get("fullName"),
                "away_pitcher": (away.get("probablePitcher") or {}).get("fullName"),
                "status": detailed,
                "final": is_final,
                "void": void,
                "home_win": home_win,
            })
    return games


def fetch_schedule(date_key, use_cache=True):
    """Return the list of games for ``date_key`` (YYYYMMDD).

    Past dates whose games are all final/void are cached permanently. Today
    or an in-progress date is always refetched (results still settling).
    """
    date_iso = f"{date_key[:4]}-{date_key[4:6]}-{date_key[6:8]}"
    cp = _cache_path(date_key)
    if use_cache and cp.exists():
        try:
            with open(cp, "r") as f:
                cached = json.load(f)
            # Trust the cache only if every game reached a terminal state.
            if cached and all(g.get("final") or g.get("void") for g in cached):
                return cached
        except Exception:
            pass

    resp = requests.get(_SCHEDULE_URL.format(date=date_iso), timeout=30)
    resp.raise_for_status()
    games = _parse_games(resp.json())

    # Persist only when everything is terminal (avoids freezing live scores).
    if games and all(g.get("final") or g.get("void") for g in games):
        cp.parent.mkdir(parents=True, exist_ok=True)
        with open(cp, "w") as f:
            json.dump(games, f, indent=2)
    return games
