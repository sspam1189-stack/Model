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


# Per-100 field -> game log field
_P100_TO_LOG = {
    "PTS": "pts", "REB": "reb", "AST": "ast", "FG3M": "fg3m",
    "STL": "stl", "BLK": "blk", "TOV": "tov",
}

# Stat keys used for with/without analysis
_SPLIT_STATS = ("min", "pts", "reb", "ast", "fg3m", "stl", "blk", "tov")

# Minimum games without a teammate to trust the with/without split
_MIN_WITHOUT_GAMES = 5

# Fraction of an OUT player's per-100 production redistributed (fallback only)
_REDISTRIBUTION_FACTOR = 0.35


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


def _build_team_date_roster(team_abbrev, player_adv_stats, player_logs):
    """
    Build {game_date: set(player_id_str)} for a team from game logs.
    Only includes players who played >= 5 minutes that game.
    """
    from collections import defaultdict
    date_roster = defaultdict(set)

    for pid_str, stats in (player_adv_stats or {}).items():
        if stats.get("team") != team_abbrev:
            continue
        games = (player_logs or {}).get(int(pid_str), [])
        for g in games:
            if g.get("min", 0) >= 5:
                date_roster[g["game_date"]].add(pid_str)

    return dict(date_roster)


def _compute_with_without_split(player_id, teammate_id, player_logs, date_roster):
    """
    Compute a player's average stats in games where a teammate played
    vs games where the teammate was absent.

    Parameters
    ----------
    player_id : str
        Player being projected.
    teammate_id : str
        Teammate to check presence/absence for.
    player_logs : dict
        {player_id_int: [game_log, ...]}.
    date_roster : dict
        {game_date: set(player_id_str)} from _build_team_date_roster.

    Returns
    -------
    dict or None
        {"with": {stat: avg}, "without": {stat: avg}, "n_with": int, "n_without": int}
        or None if insufficient data.
    """
    games = player_logs.get(int(player_id), [])
    if not games:
        return None

    with_games = []
    without_games = []

    for g in games:
        if g.get("min", 0) < 15:
            continue
        gd = g.get("game_date", "")
        roster = date_roster.get(gd, set())
        if not roster:
            continue
        if teammate_id in roster:
            with_games.append(g)
        else:
            without_games.append(g)

    if len(without_games) < _MIN_WITHOUT_GAMES:
        return None  # not enough data

    def _avg(game_list, stat):
        return sum(g.get(stat, 0) for g in game_list) / len(game_list)

    result = {"with": {}, "without": {}, "n_with": len(with_games), "n_without": len(without_games)}
    for stat in _SPLIT_STATS:
        result["with"][stat] = _avg(with_games, stat) if with_games else 0.0
        result["without"][stat] = _avg(without_games, stat)

    return result


def compute_teammate_absence_boost(team_abbrev, injury_report,
                                   player_adv_stats=None,
                                   player_id=None, player_logs=None,
                                   player_per100=None):
    """
    Compute per-stat boost using actual with/without teammate splits.

    Primary method: compare this player's real stats in games where the
    OUT teammate played vs games they missed. The diff is the measured boost.

    Fallback (< 5 without-games): use OUT player's per-100-possession
    production, redistribute 35% across teammates weighted by
    (minutes-gap * usage).

    Parameters
    ----------
    team_abbrev : str
    injury_report : dict
        From load_injury_report().
    player_adv_stats : dict or None
        {player_id_str: {"USG_PCT", "MIN", "player_name", "team", ...}}
    player_id : str or int or None
        The player being projected.
    player_logs : dict or None
        {player_id_int: [game_log, ...]}.
    player_per100 : dict or None
        {player_id_str: {"PTS": float, ...}} per-100-possession stats.

    Returns
    -------
    dict
        {stat_key: float} boost to add per stat (game-log keys like "pts").
    """
    boost = {s: 0.0 for s in _SPLIT_STATS if s != "min"}
    team_injuries = injury_report.get(team_abbrev, [])
    if not team_injuries:
        return boost

    pid_str = str(player_id) if player_id is not None else None

    # Build team date roster once (shared across all OUT teammates)
    date_roster = _build_team_date_roster(
        team_abbrev, player_adv_stats, player_logs
    ) if player_logs and player_adv_stats else {}

    for inj in team_injuries:
        if inj.get("status") not in ("out", "doubtful"):
            continue
        mpg = inj.get("mpg", 0)
        if mpg < 15:
            continue

        out_pid = _find_player_id_by_name(
            inj.get("player", ""), team_abbrev, player_adv_stats
        )
        if not out_pid:
            continue

        # --- Primary: with/without split from game logs ---
        split = None
        if pid_str and date_roster:
            split = _compute_with_without_split(
                pid_str, out_pid, player_logs, date_roster
            )

        if split is not None:
            for stat in boost:
                diff = split["without"].get(stat, 0) - split["with"].get(stat, 0)
                boost[stat] += diff
            continue  # used real split, skip fallback

        # --- Fallback: per-100 redistribution ---
        per100 = (player_per100 or {}).get(out_pid, {})
        if not per100:
            continue

        # Estimate per-game from per-100: per100_stat * (team_pace / 100)
        # Use player's team pace from adv stats, default ~100
        team_pace = 100.0
        if player_adv_stats and pid_str:
            team_pace = (player_adv_stats.get(pid_str) or {}).get("PACE", 100.0) or 100.0

        # Simple share: distribute equally among rotation players (>= 15 mpg)
        n_rotation = sum(
            1 for s in (player_adv_stats or {}).values()
            if s.get("team") == team_abbrev and s.get("MIN", 0) >= 15
        ) - 1  # exclude the OUT player
        n_rotation = max(n_rotation, 5)

        for p100_key, log_key in _P100_TO_LOG.items():
            if log_key == "min":
                continue
            val = per100.get(p100_key, 0)
            if val <= 0:
                continue
            per_game = val * (team_pace / 100.0) * (mpg / 48.0)
            boost[log_key] += per_game * _REDISTRIBUTION_FACTOR / n_rotation

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
