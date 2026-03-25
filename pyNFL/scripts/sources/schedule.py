# pyNFL/scripts/sources/schedule.py
# Rest advantage, bye week, and travel impact computation for NFL games.
#
# Uses game timestamps and team locations to compute:
# - Short rest (Thursday games, <6 days since last game)
# - Bye week rest advantage
# - Cross-country travel (3+ timezone difference)
# - Monday night → Thursday night scheduling disadvantage

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Team timezone reference (for travel distance estimation)
# ---------------------------------------------------------------------------

TEAM_TZ = {
    "ARI": "America/Phoenix",
    "ATL": "America/New_York",
    "BAL": "America/New_York",
    "BUF": "America/New_York",
    "CAR": "America/New_York",
    "CHI": "America/Chicago",
    "CIN": "America/New_York",
    "CLE": "America/New_York",
    "DAL": "America/Chicago",
    "DEN": "America/Denver",
    "DET": "America/New_York",
    "GB":  "America/Chicago",
    "HOU": "America/Chicago",
    "IND": "America/New_York",
    "JAX": "America/New_York",
    "KC":  "America/Chicago",
    "LA":  "America/Los_Angeles",
    "LAC": "America/Los_Angeles",
    "LV":  "America/Los_Angeles",
    "MIA": "America/New_York",
    "MIN": "America/Chicago",
    "NE":  "America/New_York",
    "NO":  "America/Chicago",
    "NYG": "America/New_York",
    "NYJ": "America/New_York",
    "PHI": "America/New_York",
    "PIT": "America/New_York",
    "SEA": "America/Los_Angeles",
    "SF":  "America/Los_Angeles",
    "TB":  "America/New_York",
    "TEN": "America/Chicago",
    "WAS": "America/New_York",
}

# UTC offset lookup for simplified distance calc
_TZ_OFFSET = {
    "America/New_York": -5,
    "America/Chicago": -6,
    "America/Denver": -7,
    "America/Phoenix": -7,
    "America/Los_Angeles": -8,
}


def _tz_diff(away_abbr, home_abbr):
    """Return absolute timezone hour difference between away and home."""
    away_tz = TEAM_TZ.get(away_abbr, "America/New_York")
    home_tz = TEAM_TZ.get(home_abbr, "America/New_York")
    away_off = _TZ_OFFSET.get(away_tz, -5)
    home_off = _TZ_OFFSET.get(home_tz, -5)
    return abs(away_off - home_off)


def compute_rest_adjustment(game_data, prev_week_games=None):
    """
    Compute rest/schedule adjustments for a game.

    Parameters
    ----------
    game_data : dict
        Current game with keys: away, home, commenceTimeIso, week
    prev_week_games : list[dict] or None
        Previous week's completed games (with commenceTimeIso, away, home).
        Used to compute days rest.

    Returns
    -------
    dict
        {
            "home_rest_adj": float (points adjustment for home, positive = advantage),
            "away_rest_adj": float,
            "home_days_rest": int or None,
            "away_days_rest": int or None,
            "travel_adj": float (positive = home advantage from travel),
            "rest_note": str,
        }
    """
    result = {
        "home_rest_adj": 0.0,
        "away_rest_adj": 0.0,
        "home_days_rest": None,
        "away_days_rest": None,
        "travel_adj": 0.0,
        "rest_note": "",
    }

    home = game_data.get("home", "")
    away = game_data.get("away", "")
    game_time_str = game_data.get("commenceTimeIso", "")

    if not game_time_str:
        return result

    try:
        game_dt = datetime.fromisoformat(game_time_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return result

    notes = []

    # --- Compute days rest from previous week ---
    if prev_week_games:
        home_last = _find_last_game(home, prev_week_games)
        away_last = _find_last_game(away, prev_week_games)

        if home_last:
            result["home_days_rest"] = (game_dt - home_last).days
        if away_last:
            result["away_days_rest"] = (game_dt - away_last).days

        h_rest = result["home_days_rest"]
        a_rest = result["away_days_rest"]

        # Short rest adjustment (Thursday games after Sunday = 4 days)
        if h_rest is not None and h_rest <= 5:
            result["home_rest_adj"] -= 1.5
            notes.append(f"home short rest ({h_rest}d)")
        if a_rest is not None and a_rest <= 5:
            result["away_rest_adj"] -= 1.5
            notes.append(f"away short rest ({a_rest}d)")

        # Extra rest (bye week = 13-14 days, gives ~1-1.5 pt edge)
        if h_rest is not None and h_rest >= 12:
            result["home_rest_adj"] += 1.5
            notes.append(f"home off bye ({h_rest}d)")
        elif h_rest is not None and h_rest >= 9:
            result["home_rest_adj"] += 0.5
            notes.append(f"home extra rest ({h_rest}d)")

        if a_rest is not None and a_rest >= 12:
            result["away_rest_adj"] += 1.5
            notes.append(f"away off bye ({a_rest}d)")
        elif a_rest is not None and a_rest >= 9:
            result["away_rest_adj"] += 0.5
            notes.append(f"away extra rest ({a_rest}d)")

    # --- Travel disadvantage (away team only) ---
    # Home team abbreviations need resolving — use the ones from game_data
    # For now, we compute from team name → abbr via a simple lookup
    home_abbr = game_data.get("_homeAbbr", "")
    away_abbr = game_data.get("_awayAbbr", "")

    if home_abbr and away_abbr:
        tz_hours = _tz_diff(away_abbr, home_abbr)
        if tz_hours >= 3:
            result["travel_adj"] = 1.0  # ~1 pt advantage for home
            notes.append(f"cross-country travel ({tz_hours}h TZ diff)")
        elif tz_hours >= 2:
            result["travel_adj"] = 0.5
            notes.append(f"travel ({tz_hours}h TZ diff)")

    result["rest_note"] = "; ".join(notes) if notes else "standard rest"
    return result


def _find_last_game(team_name, prev_games):
    """Find the most recent game datetime for a team in previous games list."""
    for g in sorted(prev_games, key=lambda x: x.get("commenceTimeIso", ""), reverse=True):
        if team_name in (g.get("home", ""), g.get("away", "")):
            try:
                return datetime.fromisoformat(
                    g.get("commenceTimeIso", g.get("date", "")).replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                continue
    return None
