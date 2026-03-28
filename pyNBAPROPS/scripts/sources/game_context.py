# pyNBAPROPS/scripts/sources/game_context.py
# Game context features for player prop projections:
#   1. Rest / back-to-back detection
#   2. Injury / absence usage boost (teammates OUT → usage spike)
#   3. Home/away splits from player game logs
#   4. Minutes projection (per-minute rates × projected minutes)
#
# These are computed per-player-per-game and passed to the props engine
# as adjustment factors.

import os
import json
import time
import datetime
import requests
import re

_dir = os.path.dirname(os.path.abspath(__file__))
_B2B_CACHE_DIR = os.path.join(_dir, "..", "..", "..", "data", "b2b_cache", "nba")

# ---------------------------------------------------------------------------
# 1. Rest / Back-to-back detection
# ---------------------------------------------------------------------------

# Rest adjustments: SYMMETRIC so they don't introduce net-negative bias.
# B2B penalty AND rest bonus must average to ~0 across all games.
# ~15% of games are B2B, ~15% are 2+ days rest, ~70% are normal (1 day).
# B2B: -1.5 pts × 15% = -0.225 avg contribution
# Rest: +0.5 pts × 15% = +0.075 avg contribution
# Net: -0.15 (close to zero vs old -0.30)
B2B_PENALTIES = {
    "pts":  -1.5,     # Reduced from -2.0 (old value introduced too much bias)
    "reb":  -0.4,
    "ast":  -0.3,
    "fg3m": -0.2,
    "stl":   0.0,
    "blk":   0.0,
    "tov":   0.0,
}

# Rest bonus: player with 2+ days rest performs slightly better
REST_BONUS = {
    "pts":  +0.5,
    "reb":  +0.2,
    "ast":  +0.1,
    "fg3m": +0.1,
    "stl":   0.0,
    "blk":   0.0,
    "tov":   0.0,
}


def detect_b2b_teams(date_str=None):
    """
    Detect teams on a back-to-back by checking if they played yesterday.

    Parameters
    ----------
    date_str : str or None
        Today's date as YYYYMMDD. Auto-detected if None.

    Returns
    -------
    set
        Set of team abbreviations that played yesterday.
    """
    from zoneinfo import ZoneInfo

    if date_str is None:
        now = datetime.datetime.now(ZoneInfo("America/Chicago"))
    else:
        now = datetime.datetime.strptime(date_str[:8], "%Y%m%d")

    yesterday = now - datetime.timedelta(days=1)
    yest_str = yesterday.strftime("%Y%m%d")

    # Check cache (permanent — yesterday's games won't change)
    cache_path = os.path.join(_B2B_CACHE_DIR, f"b2b_{yest_str}.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r") as f:
                cached = json.load(f)
            return set(cached)
        except Exception:
            pass

    url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={yest_str}"

    try:
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if res.status_code != 200:
            return set()
    except Exception:
        return set()

    sb = res.json()
    teams = set()

    for ev in sb.get("events", []):
        comp = (ev.get("competitions") or [None])[0]
        if not comp:
            continue
        status = (comp.get("status") or {}).get("type", {})
        if not status.get("completed"):
            continue
        for c in comp.get("competitors", []):
            abbr = (c.get("team") or {}).get("abbreviation", "")
            if abbr:
                teams.add(abbr)

    # Cache the result (permanent — historical data doesn't change)
    if teams:
        os.makedirs(_B2B_CACHE_DIR, exist_ok=True)
        try:
            with open(cache_path, "w") as f:
                json.dump(list(teams), f)
        except Exception:
            pass

    return teams


def detect_b2b_from_game_logs(player_games, game_date):
    """
    Detect if a player is on a B2B by checking their game log dates.
    Works in backtest mode without needing ESPN API.

    Parameters
    ----------
    player_games : list[dict]
        Player's game logs sorted by date.
    game_date : str
        Today's date (YYYY-MM-DD format).

    Returns
    -------
    bool
        True if the player played yesterday.
    """
    try:
        today = datetime.datetime.strptime(game_date[:10], "%Y-%m-%d")
        yesterday = (today - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return False

    for g in player_games:
        gd = g.get("game_date", "")[:10]
        if gd == yesterday:
            return True
    return False


def detect_rest_days(player_games, game_date):
    """
    Detect how many days of rest a player has (days since last game).

    Returns
    -------
    int
        Days since last game. 1 = played yesterday (B2B), 2+ = extra rest.
        Returns 99 if no prior games found.
    """
    try:
        today = datetime.datetime.strptime(game_date[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return 99

    for g in reversed(player_games):
        gd = g.get("game_date", "")[:10]
        if not gd:
            continue
        try:
            game_dt = datetime.datetime.strptime(gd, "%Y-%m-%d")
            if game_dt < today:
                return (today - game_dt).days
        except (ValueError, TypeError):
            continue
    return 99


# ---------------------------------------------------------------------------
# 2. Injury / absence usage boost
# ---------------------------------------------------------------------------

def compute_teammate_absence_boost(player_logs, player_id, team, adv_stats=None):
    """
    Estimate how much a player's stats increase when key teammates are absent.

    Logic: Compare the player's per-game stats in games where a high-usage
    teammate was absent vs. present. If a teammate with USG% > 20% is OUT
    tonight, boost this player's projection.

    For the backtest, we approximate this by looking at the player's variance
    when a teammate is missing from the game log on the same date.

    Parameters
    ----------
    player_logs : dict
        {player_id: [game_log, ...]} for all players.
    player_id : int or str
        The player being projected.
    team : str
        Team abbreviation.
    adv_stats : dict or None
        {player_id_str: {"USG_PCT": float, "MIN": float, ...}}

    Returns
    -------
    dict
        {"pts_boost": float, "reb_boost": float, "ast_boost": float, ...}
        All zeros if no data or no absent teammates detected.
    """
    # Default: no boost
    boost = {k: 0.0 for k in B2B_PENALTIES.keys()}

    if not adv_stats:
        return boost

    # Find high-usage teammates (USG% > 22%, >28 min/game)
    teammates = []
    for pid_str, stats in adv_stats.items():
        if pid_str == str(player_id):
            continue
        if stats.get("team") != team:
            continue
        usg = stats.get("USG_PCT", 0)
        mins = stats.get("MIN", 0)
        if usg > 0.22 and mins > 28:
            teammates.append({
                "pid": pid_str,
                "name": stats.get("player_name", ""),
                "usg": usg,
                "min": mins,
            })

    # For live mode: check if any high-usage teammate is missing from today's
    # game logs. This is a simple approximation — a full injury integration
    # would check ESPN injury reports.
    # For now, return zero boost (this gets refined when injury data is wired in)
    return boost


# ---------------------------------------------------------------------------
# 3. Home/away splits from game logs
# ---------------------------------------------------------------------------

def compute_home_away_split(player_games, stat_key, min_games=5):
    """
    Compute a player's home vs. away performance split.

    Returns the adjustment to apply: positive = player performs better in
    the upcoming game's venue, negative = worse.

    Parameters
    ----------
    player_games : list[dict]
        Player's game logs.
    stat_key : str
        Stat to compute split for (e.g., "pts", "reb").
    min_games : int
        Minimum home or away games needed.

    Returns
    -------
    dict
        {"home_avg": float, "away_avg": float, "split_adj": float,
         "is_home": bool or None}
        split_adj is the adjustment based on upcoming game venue.
    """
    home_vals = []
    away_vals = []

    for g in player_games:
        val = g.get(stat_key, 0)
        if g.get("min", 0) < 15:
            continue
        if g.get("is_home"):
            home_vals.append(val)
        else:
            away_vals.append(val)

    result = {
        "home_avg": 0.0,
        "away_avg": 0.0,
        "split_adj": 0.0,
    }

    if len(home_vals) < min_games or len(away_vals) < min_games:
        return result

    home_avg = sum(home_vals) / len(home_vals)
    away_avg = sum(away_vals) / len(away_vals)
    overall_avg = (sum(home_vals) + sum(away_vals)) / (len(home_vals) + len(away_vals))

    result["home_avg"] = round(home_avg, 1)
    result["away_avg"] = round(away_avg, 1)

    # Split adjustment: deviation from overall average
    # Cap at ±15% of overall average to prevent extreme adjustments
    home_adj = home_avg - overall_avg
    away_adj = away_avg - overall_avg
    max_adj = overall_avg * 0.15

    result["home_split_adj"] = max(-max_adj, min(max_adj, home_adj))
    result["away_split_adj"] = max(-max_adj, min(max_adj, away_adj))

    return result


# ---------------------------------------------------------------------------
# 4. Minutes projection (per-minute rates × projected minutes)
# ---------------------------------------------------------------------------

def compute_per_minute_rates(player_games, min_minutes=15):
    """
    Compute per-minute production rates from recent game logs.

    Parameters
    ----------
    player_games : list[dict]
        Player's recent game logs (already filtered to rolling window).
    min_minutes : float
        Minimum minutes to include a game.

    Returns
    -------
    dict
        {"pts_per_min": float, "reb_per_min": float, ...}
        Per-minute rates for each counting stat.
    """
    qualified = [g for g in player_games if g.get("min", 0) >= min_minutes]

    if not qualified:
        return {}

    stats = ["pts", "reb", "ast", "fg3m", "stl", "blk", "tov"]
    rates = {}

    for stat in stats:
        total_stat = sum(g.get(stat, 0) for g in qualified)
        total_min = sum(g.get("min", 0) for g in qualified)
        if total_min > 0:
            rates[f"{stat}_per_min"] = total_stat / total_min
        else:
            rates[f"{stat}_per_min"] = 0.0

    # Average minutes per game (for projecting tonight's minutes)
    rates["avg_min"] = sum(g.get("min", 0) for g in qualified) / len(qualified)

    return rates


def project_minutes(player_games, adv_stats=None, is_b2b=False):
    """
    Project how many minutes a player will play tonight.

    Uses recent average minutes, adjusted for:
    - Season-long average from advanced stats (regression toward mean)
    - Back-to-back penalty (~2 fewer minutes)

    Parameters
    ----------
    player_games : list[dict]
        Recent game logs (rolling window).
    adv_stats : dict or None
        Player's advanced stats {"MIN": float, ...}.
    is_b2b : bool
        Whether the player is on a back-to-back.

    Returns
    -------
    float
        Projected minutes for tonight.
    """
    qualified = [g for g in player_games if g.get("min", 0) >= 10]
    if not qualified:
        return 0.0

    # Recent average minutes (last N games)
    recent_min = sum(g.get("min", 0) for g in qualified) / len(qualified)

    # Blend with season average if available (70% recent, 30% season)
    if adv_stats and adv_stats.get("MIN", 0) > 0:
        season_min = adv_stats["MIN"]
        proj_min = 0.7 * recent_min + 0.3 * season_min
    else:
        proj_min = recent_min

    # B2B penalty: ~2 fewer minutes on average
    if is_b2b:
        proj_min -= 2.0

    return max(0.0, round(proj_min, 1))


def rate_based_projection(per_min_rate, projected_minutes):
    """
    Compute stat projection from per-minute rate × projected minutes.

    Parameters
    ----------
    per_min_rate : float
        Stat per minute (e.g., 0.75 pts/min).
    projected_minutes : float
        Expected minutes tonight.

    Returns
    -------
    float
        Projected raw stat value.
    """
    return per_min_rate * projected_minutes
