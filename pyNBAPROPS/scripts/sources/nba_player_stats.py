# pyNBAPROPS/scripts/sources/nba_player_stats.py
# Fetch NBA player game logs (basic + advanced) via nba_api package.
#
# Uses nba_api instead of raw requests to stats.nba.com because NBA.com
# aggressively blocks/rate-limits direct API calls. nba_api handles
# headers, cookies, and retries properly.
#
# Three data layers:
#   1. Basic box score: PTS, REB, AST, STL, BLK, TOV, FG3M, FGA, FTA, MIN
#      Source: LeagueGameLog (PlayerOrTeam=P)
#
#   2. Advanced per-game: USG%, TS%, AST%, REB%, OFF_RATING, DEF_RATING, PACE
#      Source: LeagueDashPlayerStats (MeasureType=Advanced)
#
#   3. Team defense: DEF_RATING, OPP_PTS, OPP_REB, OPP_AST, PACE, etc.
#      Source: LeagueDashTeamStats (Advanced + Opponent)

import os
import json
import time

_dir = os.path.dirname(os.path.abspath(__file__))
PLAYER_CACHE_DIR = os.path.join(_dir, "..", "..", "..", "data", "player_cache", "nba")

import sys
sys.path.insert(0, os.path.join(_dir, ".."))
from defaults import current_season


# ---------------------------------------------------------------------------
# 1. Basic player game logs
# ---------------------------------------------------------------------------

def fetch_player_game_logs(season=None, season_type="Regular Season", date_to=None):
    """
    Fetch all player game logs for the season via nba_api.

    Returns list of dicts with basic box score + minutes for every player-game.
    """
    from nba_api.stats.endpoints import leaguegamelog

    season = season or current_season()

    cache_key = f"basic_{season}_{season_type.replace(' ', '_')}"
    if date_to:
        cache_key += f"_{str(date_to).replace('-', '')}"
    cache_path = os.path.join(PLAYER_CACHE_DIR, f"{cache_key}.json")

    if os.path.exists(cache_path):
        age_h = (time.time() - os.path.getmtime(cache_path)) / 3600
        if age_h < 6:
            with open(cache_path, "r") as f:
                data = json.load(f)
            print(f"  [player_stats] Using cache: {os.path.basename(cache_path)} ({len(data)} logs)")
            return data

    date_to_fmt = _fmt_date_nba_api(date_to)

    try:
        lg = leaguegamelog.LeagueGameLog(
            season=season,
            season_type_all_star=season_type,
            player_or_team_abbreviation="P",
            date_to_nullable=date_to_fmt if date_to_fmt else None,
            timeout=120,
        )
        df = lg.get_data_frames()[0]
    except Exception as e:
        print(f"  [player_stats] ERROR fetching game logs: {e}")
        raise

    logs = []
    for _, row in df.iterrows():
        minutes = _parse_minutes(row.get("MIN"))
        matchup = str(row.get("MATCHUP") or "")
        opp = _parse_opponent(matchup)

        logs.append({
            "player_id":   int(row.get("PLAYER_ID", 0)),
            "player_name": str(row.get("PLAYER_NAME") or ""),
            "team":        str(row.get("TEAM_ABBREVIATION") or ""),
            "opp":         opp,
            "is_home":     " vs. " in matchup,
            "game_id":     str(row.get("GAME_ID") or ""),
            "game_date":   str(row.get("GAME_DATE") or ""),
            "min":         minutes,
            # --- Basic box score ---
            "pts":   _si(row, "PTS"),
            "reb":   _si(row, "REB"),
            "ast":   _si(row, "AST"),
            "stl":   _si(row, "STL"),
            "blk":   _si(row, "BLK"),
            "tov":   _si(row, "TOV"),
            "fg3m":  _si(row, "FG3M"),
            "fg3a":  _si(row, "FG3A"),
            "fga":   _si(row, "FGA"),
            "fgm":   _si(row, "FGM"),
            "fta":   _si(row, "FTA"),
            "ftm":   _si(row, "FTM"),
            "oreb":  _si(row, "OREB"),
            "dreb":  _si(row, "DREB"),
            "pf":    _si(row, "PF"),
            "plus_minus": _sf(row, "PLUS_MINUS"),
        })

    _write_cache(cache_path, logs)
    print(f"  [player_stats] Fetched {len(logs)} player game logs for {season}")
    return logs


# ---------------------------------------------------------------------------
# 2. Advanced player stats (season-to-date)
# ---------------------------------------------------------------------------

def fetch_player_advanced_stats(season=None, date_to=None):
    """
    Fetch advanced per-player stats (season-to-date averages) via nba_api.

    Returns dict: {player_id_str: {"USG_PCT", "TS_PCT", "AST_PCT", ...}}
    """
    from nba_api.stats.endpoints import leaguedashplayerstats

    season = season or current_season()

    cache_key = f"adv_{season}"
    if date_to:
        cache_key += f"_{str(date_to).replace('-', '')}"
    cache_path = os.path.join(PLAYER_CACHE_DIR, f"{cache_key}.json")

    if os.path.exists(cache_path):
        age_h = (time.time() - os.path.getmtime(cache_path)) / 3600
        if age_h < 6:
            with open(cache_path, "r") as f:
                return json.load(f)

    date_to_fmt = _fmt_date_nba_api(date_to)

    try:
        stats = leaguedashplayerstats.LeagueDashPlayerStats(
            season=season,
            season_type_all_star="Regular Season",
            measure_type_detailed_defense="Advanced",
            per_mode_detailed="PerGame",
            date_to_nullable=date_to_fmt if date_to_fmt else None,
            timeout=120,
        )
        df = stats.get_data_frames()[0]
    except Exception as e:
        print(f"  [adv_stats] ERROR: {e}")
        return {}

    result = {}
    for _, row in df.iterrows():
        pid = row.get("PLAYER_ID")
        if pid is None:
            continue
        result[str(int(pid))] = {
            "player_name": str(row.get("PLAYER_NAME") or ""),
            "team":        str(row.get("TEAM_ABBREVIATION") or ""),
            "GP":          _si(row, "GP"),
            "MIN":         _sf(row, "MIN"),
            # --- Advanced metrics ---
            "USG_PCT":     _sf(row, "USG_PCT"),
            "TS_PCT":      _sf(row, "TS_PCT"),
            "AST_PCT":     _sf(row, "AST_PCT"),
            "AST_TO":      _sf(row, "AST_TO"),
            "AST_RATIO":   _sf(row, "AST_RATIO"),
            "OREB_PCT":    _sf(row, "OREB_PCT"),
            "DREB_PCT":    _sf(row, "DREB_PCT"),
            "REB_PCT":     _sf(row, "REB_PCT"),
            "EFG_PCT":     _sf(row, "EFG_PCT"),
            "OFF_RATING":  _sf(row, "OFF_RATING"),
            "DEF_RATING":  _sf(row, "DEF_RATING"),
            "NET_RATING":  _sf(row, "NET_RATING"),
            "PACE":        _sf(row, "PACE"),
            "PIE":         _sf(row, "PIE"),
        }

    _write_cache(cache_path, result)
    print(f"  [adv_stats] Fetched advanced stats for {len(result)} players")
    return result


# ---------------------------------------------------------------------------
# 3. Team defensive stats (for opponent adjustment)
# ---------------------------------------------------------------------------

def fetch_team_def_stats(season=None, date_to=None):
    """
    Fetch team defensive stats for opponent adjustment via nba_api.

    Returns dict: {team_abbr: {"DEF_RATING", "OPP_PTS", "OPP_REB", ...}}
    """
    from nba_api.stats.endpoints import leaguedashteamstats
    from nba_api.stats.static import teams as nba_teams

    season = season or current_season()
    date_to_fmt = _fmt_date_nba_api(date_to)

    # --- Cache: team defense stats (6-hour TTL) ---
    cache_key = f"teamdef_{season}"
    if date_to:
        cache_key += f"_{str(date_to).replace('-', '')}"
    cache_path = os.path.join(PLAYER_CACHE_DIR, f"{cache_key}.json")

    if os.path.exists(cache_path):
        age_h = (time.time() - os.path.getmtime(cache_path)) / 3600
        if age_h < 6:
            with open(cache_path, "r") as f:
                cached = json.load(f)
            print(f"  [team_def] Using cache: {os.path.basename(cache_path)} ({len(cached)} teams)")
            return cached

    # Build team name -> abbreviation mapping (nba_api returns TEAM_NAME, not TEAM_ABBREVIATION)
    team_name_to_abbr = {t['full_name']: t['abbreviation'] for t in nba_teams.get_teams()}

    teams = {}

    # --- Advanced team stats (DEF_RATING, PACE) ---
    try:
        adv = leaguedashteamstats.LeagueDashTeamStats(
            season=season,
            season_type_all_star="Regular Season",
            measure_type_detailed_defense="Advanced",
            per_mode_detailed="PerGame",
            date_to_nullable=date_to_fmt if date_to_fmt else None,
            timeout=120,
        )
        df_adv = adv.get_data_frames()[0]
        for _, row in df_adv.iterrows():
            team_name = str(row.get("TEAM_NAME") or "")
            abbr = team_name_to_abbr.get(team_name, team_name[:3].upper())
            teams[abbr] = {
                "DEF_RATING":   _sf(row, "DEF_RATING"),
                "OFF_RATING":   _sf(row, "OFF_RATING"),
                "NET_RATING":   _sf(row, "NET_RATING"),
                "PACE":         _sf(row, "PACE"),
                "OPP_REB_RATE": _sf(row, "DREB_PCT"),
                "REB_PCT":      _sf(row, "REB_PCT"),
            }
    except Exception as e:
        print(f"  [team_def] ERROR fetching advanced: {e}")

    time.sleep(1)  # Rate limit between API calls

    # --- Opponent per-game stats (what teams ALLOW) ---
    try:
        opp = leaguedashteamstats.LeagueDashTeamStats(
            season=season,
            season_type_all_star="Regular Season",
            measure_type_detailed_defense="Opponent",
            per_mode_detailed="PerGame",
            date_to_nullable=date_to_fmt if date_to_fmt else None,
            timeout=120,
        )
        df_opp = opp.get_data_frames()[0]
        for _, row in df_opp.iterrows():
            team_name = str(row.get("TEAM_NAME") or "")
            abbr = team_name_to_abbr.get(team_name, team_name[:3].upper())
            if abbr not in teams:
                teams[abbr] = {}
            teams[abbr].update({
                "OPP_PTS":     _sf(row, "OPP_PTS"),
                "OPP_REB":     _sf(row, "OPP_REB"),
                "OPP_AST":     _sf(row, "OPP_AST"),
                "OPP_FG3M":    _sf(row, "OPP_FG3M"),
                "OPP_FG3_PCT": _sf(row, "OPP_FG3_PCT"),
                "OPP_FGA":     _sf(row, "OPP_FGA"),
                "OPP_FTA":     _sf(row, "OPP_FTA"),
                "OPP_TOV":     _sf(row, "OPP_TOV"),
                "OPP_STL":     _sf(row, "OPP_STL"),
                "OPP_BLK":     _sf(row, "OPP_BLK"),
            })
    except Exception as e:
        print(f"  [team_def] ERROR fetching opponent: {e}")

    # Cache the result
    if teams:
        _write_cache(cache_path, teams)

    print(f"  [team_def] Fetched defense stats for {len(teams)} teams")
    return teams


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_date_nba_api(date_str):
    """Convert YYYY-MM-DD or YYYYMMDD to MM/DD/YYYY for nba_api."""
    if not date_str:
        return ""
    d = str(date_str).replace("-", "")
    if len(d) == 8:
        return f"{d[4:6]}/{d[6:8]}/{d[0:4]}"
    return date_str


def _write_cache(path, data):
    """Write JSON cache file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f)


def _parse_minutes(raw):
    """Parse minutes from '32:15' or '32' or float."""
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw)
    if ":" in s:
        parts = s.split(":")
        try:
            return float(parts[0]) + float(parts[1]) / 60
        except (ValueError, IndexError):
            return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_opponent(matchup):
    """Extract opponent from 'LAL vs. BOS' or 'LAL @ BOS'."""
    for sep in [" vs. ", " @ "]:
        if sep in matchup:
            return matchup.split(sep)[-1].strip()
    return ""


def _si(row, key):
    """Safe int from pandas row."""
    val = row.get(key)
    try:
        return int(val) if val is not None else 0
    except (ValueError, TypeError):
        return 0


def _sf(row, key):
    """Safe float from pandas row."""
    val = row.get(key)
    try:
        return float(val) if val is not None else 0.0
    except (ValueError, TypeError):
        return 0.0
