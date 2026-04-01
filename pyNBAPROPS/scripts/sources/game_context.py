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
# 2. Injury report loading & teammate absence boost
# ---------------------------------------------------------------------------

_INJURY_CACHE_DIR = os.path.join(_dir, "..", "..", "..", "data", "injury_cache", "nba")

# Full team name → abbreviation (reverse of pyFull's ABBREV_TO_NAME)
NAME_TO_ABBREV = {
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA", "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN", "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "LA Clippers": "LAC", "Los Angeles Lakers": "LAL", "Memphis Grizzlies": "MEM",
    "Miami Heat": "MIA", "Milwaukee Bucks": "MIL", "Minnesota Timberwolves": "MIN",
    "New Orleans Pelicans": "NOP", "New York Knicks": "NYK", "Oklahoma City Thunder": "OKC",
    "Orlando Magic": "ORL", "Philadelphia 76ers": "PHI", "Phoenix Suns": "PHX",
    "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC", "San Antonio Spurs": "SAS",
    "Toronto Raptors": "TOR", "Utah Jazz": "UTA", "Washington Wizards": "WAS",
}


def load_injury_report(date_key):
    """
    Load injury report from the shared injury cache.

    Parameters
    ----------
    date_key : str
        Date as YYYYMMDD.

    Returns
    -------
    dict
        {team_abbrev: [{"player": str, "status": str, "tier": str, "mpg": float}]}
        Keyed by team abbreviation (e.g. "BOS"), or empty dict if no cache.
    """
    path = os.path.join(_INJURY_CACHE_DIR, f"{date_key}.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except Exception:
        return {}

    report = data.get("report", {})
    # Re-key from full team name to abbreviation
    result = {}
    for full_name, players in report.items():
        abbrev = NAME_TO_ABBREV.get(full_name)
        if abbrev:
            result[abbrev] = players
    return result


def _injury_name_key(name):
    """Normalize name for matching: (first, last) lowercased, stripped of suffixes."""
    import unicodedata
    name = name.strip()
    if not name:
        return ("", "")
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    parts = name.split()
    suffixes = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}
    while len(parts) > 2 and parts[-1].lower().rstrip(".") in suffixes:
        parts.pop()
    if len(parts) >= 2:
        return (parts[0].lower(), parts[-1].lower().rstrip("."))
    return (name[0].lower(), name.lower())


def is_player_out(player_name, team_abbrev, injury_report):
    """
    Check if a player is listed as OUT or DOUBTFUL in the injury report.

    Parameters
    ----------
    player_name : str
        Player's display name from game logs.
    team_abbrev : str
        Team abbreviation (e.g. "BOS").
    injury_report : dict
        From load_injury_report().

    Returns
    -------
    bool
    """
    team_injuries = injury_report.get(team_abbrev, [])
    if not team_injuries:
        return False
    pkey = _injury_name_key(player_name)
    for inj in team_injuries:
        if inj.get("status") not in ("out", "doubtful"):
            continue
        ikey = _injury_name_key(inj.get("player", ""))
        if pkey == ikey:
            return True
    return False


# Stat keys: per-36 field -> game log field
_P36_TO_LOG = {
    "PTS": "pts", "REB": "reb", "AST": "ast", "FG3M": "fg3m",
    "STL": "stl", "BLK": "blk", "TOV": "tov",
}

# Fraction of an OUT player's production that gets redistributed to teammates.
# Not 100% — some is lost to fewer possessions, different lineup dynamics.
_REDISTRIBUTION_FACTOR = 0.35

# Minutes ceiling — players near this have no room to absorb more.
_MINUTES_CAP = 36.0


def _find_player_id_by_name(player_name, team_abbrev, player_adv_stats):
    """Match an injury-report player name to a player_id in adv stats."""
    if not player_adv_stats:
        return None
    target = _injury_name_key(player_name)
    for pid_str, stats in player_adv_stats.items():
        if stats.get("team") != team_abbrev:
            continue
        cand = _injury_name_key(stats.get("player_name", ""))
        if cand == target:
            return pid_str
    return None


def _get_team_minutes_weights(team_abbrev, player_adv_stats, injury_report):
    """
    Compute minutes-gap weights for all healthy teammates.

    Weight = max(0, CAP - current_mpg). Players already near 36 mpg get
    almost zero weight; bench guys with room to grow get the most.

    Returns
    -------
    dict
        {player_id_str: weight} and total_weight (for normalization).
    float
        Sum of all weights.
    """
    # Build set of OUT player names for this team
    out_names = set()
    for inj in injury_report.get(team_abbrev, []):
        if inj.get("status") in ("out", "doubtful"):
            out_names.add(_injury_name_key(inj.get("player", "")))

    weights = {}
    for pid_str, stats in (player_adv_stats or {}).items():
        if stats.get("team") != team_abbrev:
            continue
        mpg = stats.get("MIN", 0)
        if mpg < 15:
            continue  # not in rotation
        # Skip OUT players themselves
        nk = _injury_name_key(stats.get("player_name", ""))
        if nk in out_names:
            continue
        gap = max(0.0, _MINUTES_CAP - mpg)
        weights[pid_str] = gap

    total = sum(weights.values())
    return weights, total


def compute_teammate_absence_boost(team_abbrev, injury_report,
                                   player_per36=None, player_adv_stats=None,
                                   player_id=None):
    """
    Compute per-stat boost for a player based on OUT teammates' actual production.

    Uses minutes-gap weighting: players with more room to grow in minutes
    absorb a bigger share. A 15-mpg bench guy stepping into the lineup gets
    far more than a star already at 35 mpg.

    For each OUT teammate:
      1. Look up their per-36 stats and mpg
      2. Compute per-game production: per36_stat * (mpg / 36)
      3. Redistribute a fraction (35%) to healthy teammates
      4. Weight by minutes gap: max(0, 36 - player_mpg)

    Parameters
    ----------
    team_abbrev : str
        Team abbreviation (e.g. "BOS").
    injury_report : dict
        From load_injury_report().
    player_per36 : dict or None
        {player_id_str: {"PTS": float, "REB": float, ...}}
    player_adv_stats : dict or None
        {player_id_str: {"USG_PCT": float, "MIN": float, "player_name": str, ...}}
    player_id : str or None
        The player being projected.

    Returns
    -------
    dict
        {stat_key: float} boost to add to projection per stat (game-log keys).
    """
    boost = {v: 0.0 for v in _P36_TO_LOG.values()}
    team_injuries = injury_report.get(team_abbrev, [])
    if not team_injuries:
        return boost

    # Compute minutes-gap weights for all healthy teammates
    weights, total_weight = _get_team_minutes_weights(
        team_abbrev, player_adv_stats, injury_report
    )
    if total_weight <= 0 or not weights:
        return boost

    player_weight = weights.get(str(player_id), 0.0) if player_id else 0.0
    if player_weight <= 0:
        return boost  # this player has no room to absorb (or not found)

    # This player's share of the redistributed production
    share = player_weight / total_weight

    for inj in team_injuries:
        if inj.get("status") not in ("out", "doubtful"):
            continue
        mpg = inj.get("mpg", 0)
        if mpg < 15:
            continue  # bench player — minimal redistribution

        # Find the OUT player's per-36 stats
        out_pid = _find_player_id_by_name(
            inj.get("player", ""), team_abbrev, player_adv_stats
        )
        out_p36 = (player_per36 or {}).get(out_pid, {}) if out_pid else {}

        if not out_p36:
            continue  # can't size the boost without per-36 data

        # Compute per-game production and redistribute by minutes gap
        for p36_key, log_key in _P36_TO_LOG.items():
            per36_val = out_p36.get(p36_key, 0)
            if per36_val <= 0:
                continue
            per_game = per36_val * (mpg / 36.0)
            boost[log_key] += per_game * _REDISTRIBUTION_FACTOR * share

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
