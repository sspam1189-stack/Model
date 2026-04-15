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
CACHE_FRESHNESS_HOURS = 1

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


def _load_cache(cache_path, max_age_hours=None):
    """Load cached JSON if it exists and isn't too old.

    Parameters
    ----------
    cache_path : Path
        Path to cached JSON file.
    max_age_hours : float or None
        Maximum cache age in hours.  None = never expire (matches NBA pattern).
        Default uses CACHE_FRESHNESS_HOURS (1hr) for backward compatibility.
    """
    if not cache_path.exists():
        return None
    if max_age_hours is None:
        max_age_hours = CACHE_FRESHNESS_HOURS
    try:
        age_hours = (time.time() - cache_path.stat().st_mtime) / 3600
        if age_hours > max_age_hours:
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


def _safe_float(val, default=0.0):
    """Convert a value to float, returning default for non-numeric strings like '-.--'."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


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

    The MLB Stats API bulk gameLog endpoint (playerPool=all) doesn't return
    data for the current in-progress season. Instead, we build game logs
    from the schedule + boxscore feed:
      1. Get all completed regular-season games from the schedule
      2. For each game, fetch the boxscore
      3. Extract starting pitcher stats from each side

    Results are cached. On subsequent calls within CACHE_FRESHNESS_HOURS,
    the cache is returned directly. This means the first call of the day
    may take 2-3 minutes (fetching ~15 boxscores per game day) but
    subsequent calls are instant.

    Returns list of dicts with per-game pitching stats.
    """
    season = season or _current_season()
    cache_path = CACHE_DIR / f"game_logs_{season}.json"

    cached = _load_cache(cache_path)
    if cached is not None:
        print(f"  [mlb_stats] Using cached game logs ({len(cached)} entries)")
        return cached

    # Step 1: Get all completed regular-season games from the schedule
    # Use season date range (spring training excluded by gameType filter in boxscore)
    start_date = f"{season}-03-20"
    end_date = date.today().strftime("%Y-%m-%d")

    print(f"  [mlb_stats] Fetching schedule {start_date} to {end_date}...")
    schedule_url = (
        f"{BASE_URL}/schedule?sportId=1"
        f"&startDate={start_date}&endDate={end_date}"
        f"&gameType=R"  # Regular season only
    )
    try:
        sched = _fetch_json(schedule_url)
    except Exception as e:
        print(f"  [mlb_stats] Schedule fetch failed: {e}")
        return []

    # Collect completed game PKs
    game_pks = []
    for d in sched.get("dates", []):
        for g in d.get("games", []):
            status = g.get("status", {}).get("abstractGameState", "")
            if status == "Final":
                game_pks.append({
                    "pk": g["gamePk"],
                    "date": g.get("officialDate", ""),
                    "home_id": g.get("teams", {}).get("home", {}).get("team", {}).get("id"),
                    "away_id": g.get("teams", {}).get("away", {}).get("team", {}).get("id"),
                })

    print(f"  [mlb_stats] {len(game_pks)} completed games, fetching boxscores...")

    # Step 2: Fetch boxscore for each game, extract starting pitcher stats
    # Also extract batting orders per team per date for lineup handedness cache
    rows = []
    batting_orders_by_date = {}  # {date: {team_abbr: [player_ids]}}

    for i, gm in enumerate(game_pks):
        try:
            box_url = f"{BASE_URL}.1/game/{gm['pk']}/feed/live"
            live = _fetch_json(box_url)
        except Exception:
            continue

        box = live.get("liveData", {}).get("boxscore", {}).get("teams", {})
        game_date = gm["date"]

        for side in ["away", "home"]:
            side_data = box.get(side, {})
            pitchers = side_data.get("pitchers", [])
            players = side_data.get("players", {})
            team_info = side_data.get("team", {})
            team_id = team_info.get("id")
            team_abbr = MLB_TEAM_ID_TO_ABBR.get(team_id, "")

            # Opponent is the other side
            opp_side = "home" if side == "away" else "away"
            opp_id = box.get(opp_side, {}).get("team", {}).get("id")
            opp_abbr = MLB_TEAM_ID_TO_ABBR.get(opp_id, "")

            # Extract batting order for lineup handedness cache
            batting_order = side_data.get("battingOrder", [])
            if batting_order and team_abbr:
                if game_date not in batting_orders_by_date:
                    batting_orders_by_date[game_date] = {}
                batting_orders_by_date[game_date][team_abbr] = batting_order

            if not pitchers:
                continue

            # Starting pitcher = first pitcher in the list
            sp_id = pitchers[0]
            p_data = players.get(f"ID{sp_id}", {})
            person = p_data.get("person", {})
            p_stats = p_data.get("stats", {}).get("pitching", {})

            if not p_stats:
                continue

            ip_str = p_stats.get("inningsPitched", "0")

            rows.append({
                "pitcher_id": person.get("id", sp_id),
                "pitcher_name": person.get("fullName", ""),
                "team": team_abbr,
                "game_date": game_date,
                "game_id": gm["pk"],
                "k": p_stats.get("strikeOuts", 0),
                "ip": _ip_to_float(ip_str),
                "IP": _ip_to_float(ip_str),  # alias used by game_context
                "ip_str": ip_str,
                "outs": _ip_to_outs(ip_str),
                "h": p_stats.get("hits", 0),
                "bb": p_stats.get("baseOnBalls", 0),
                "er": p_stats.get("earnedRuns", 0),
                "pitches": p_stats.get("numberOfPitches", 0),
                "opp": opp_abbr,
                "is_home": side == "home",
                "is_start": True,
            })

        if (i + 1) % 50 == 0:
            print(f"  [mlb_stats] Processed {i+1}/{len(game_pks)} games ({len(rows)} pitcher starts)")
        time.sleep(0.15)  # light rate limiting

    print(f"  [mlb_stats] Fetched {len(rows)} pitcher starts from {len(game_pks)} games")

    # Cache batting orders by date (used by fetch_lineup_handedness)
    bo_cache_path = CACHE_DIR / f"batting_orders_{season}.json"
    _save_cache(bo_cache_path, batting_orders_by_date)
    print(f"  [mlb_stats] Cached batting orders for {len(batting_orders_by_date)} dates")

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

            ip = _ip_to_float(stat.get("inningsPitched", "0"))
            gs = stat.get("gamesStarted", 0)
            result[pid] = {
                "player_name": player_info.get("fullName", ""),
                "team": split.get("team", {}).get("abbreviation", ""),
                "K_PER_9": _safe_float(stat.get("strikeoutsPer9Inn", 0) or 0),
                "BB_PER_9": _safe_float(stat.get("walksPer9Inn", 0) or 0),
                "H_PER_9": _safe_float(stat.get("hitsPer9Inn", 0) or 0),
                "WHIP": _safe_float(stat.get("whip", 0) or 0),
                "ERA": _safe_float(stat.get("era", 0) or 0),
                "ip": ip,
                "avg_ip": ip / gs if gs > 0 else 0,
                "k": stat.get("strikeOuts", 0),
                "bb": stat.get("baseOnBalls", 0),
                "h": stat.get("hits", 0),
                "hr": stat.get("homeRuns", 0),
                "games": stat.get("gamesPlayed", 0),
                "games_started": gs,
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
                "fip": _safe_float(stat.get("fip", 0) or 0),
                "xfip": _safe_float(stat.get("xfip", 0) or 0),
                "babip": _safe_float(stat.get("babip", 0) or 0),
                "k_pct": _safe_float(stat.get("strikeoutPercentage", 0) or 0),
                "bb_pct": _safe_float(stat.get("walkPercentage", 0) or 0),
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

            # Extract IP as float (e.g. "18.1" → 18.333)
            ip_str = stat.get("inningsPitched", "0")
            try:
                ip_parts = str(ip_str).split(".")
                ip_float = int(ip_parts[0]) + (int(ip_parts[1]) / 3.0 if len(ip_parts) > 1 else 0.0)
            except (ValueError, IndexError):
                ip_float = 0.0

            bf = stat.get("battersFaced", 0) or 0

            split_data = {
                "ba": stat.get("avg", None),
                "obp": stat.get("obp", None),
                "slg": stat.get("slg", None),
                "ops": stat.get("ops", None),
                "k": stat.get("strikeOuts", 0),
                "bb": stat.get("baseOnBalls", 0),
                "h": stat.get("hits", 0),
                "ab": stat.get("atBats", 0),
                "pa": bf,  # battersFaced is the real PA (API's plateAppearances is 0)
                "hr": stat.get("homeRuns", 0),
                "ip": round(ip_float, 2),
                "whip": stat.get("whip", None),
                "ground_outs": stat.get("groundOuts", 0),
                "air_outs": stat.get("airOuts", 0),
            }

            # Compute per-9 rates (the keys the handedness adjustment needs)
            if ip_float > 0:
                split_data["k_per_9"] = round(split_data["k"] / ip_float * 9, 2)
                split_data["h_per_9"] = round(split_data["h"] / ip_float * 9, 2)
                split_data["bb_per_9"] = round(split_data["bb"] / ip_float * 9, 2)

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
                "BA": _safe_float(stat.get("avg", 0) or 0),
                "OPS": _safe_float(stat.get("ops", 0) or 0),
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
                "ERA": _safe_float(stat.get("era")),
                "WHIP": _safe_float(stat.get("whip")),
                "H_PER_9": _safe_float(stat.get("hitsPer9Inn")),
                "K_PER_9": _safe_float(stat.get("strikeoutsPer9Inn")),
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
# 8. Player bat-side lookup + game-lineup handedness
# ---------------------------------------------------------------------------

def fetch_player_bat_sides(season=None):
    """
    Build a player_id → bat_side_code lookup for the entire league.

    Uses the bulk /sports/1/players endpoint (single API call, ~900 players).
    Returns: {player_id (int): "L" | "R" | "S"}
    Cached per season (never changes mid-season).
    """
    season = season or _current_season()
    cache_path = CACHE_DIR / f"player_bat_sides_{season}.json"

    cached = _load_cache(cache_path, max_age_hours=None)  # never expire
    if cached is not None:
        # Keys come back as strings from JSON; convert to int
        return {int(k): v for k, v in cached.items()}

    url = f"{BASE_URL}/sports/1/players?season={season}"
    try:
        raw = _fetch_json(url)
    except Exception as e:
        print(f"  [mlb_stats] Player bat-side fetch failed: {e}")
        return {}
    time.sleep(0.5)

    result = {}
    for player in raw.get("people", []):
        pid = player.get("id")
        bat_code = player.get("batSide", {}).get("code", "R")
        if pid:
            result[pid] = bat_code

    print(f"  [mlb_stats] Fetched bat sides for {len(result)} players")
    _save_cache(cache_path, result)
    return result


def fetch_lineup_handedness(date_str, bat_sides=None, season=None):
    """
    Fetch actual starting lineups for a given date and compute PCT_LHB
    per team based on the real 9-man batting order.

    For today/future dates: uses schedule hydrate=lineups (pre-game lineups).
    For past dates: uses boxscore battingOrder (actual lineups).

    Parameters
    ----------
    date_str : str
        ISO date, e.g. "2026-04-14".
    bat_sides : dict or None
        {player_id: "L"/"R"/"S"} from fetch_player_bat_sides.
        If None, will be fetched automatically.
    season : int or None
        Season year (for bat_sides fetch if needed).

    Returns
    -------
    dict
        {team_abbr: {"PCT_LHB": float, "n_batters": int, "source": str}}
    """
    cache_path = CACHE_DIR / f"lineup_handedness_{date_str}.json"

    cached = _load_cache(cache_path, max_age_hours=None)  # lineups don't change
    if cached is not None:
        return cached

    # Build bat-side lookup if not provided
    if bat_sides is None:
        bat_sides = fetch_player_bat_sides(season=season)

    from datetime import date as dt_date
    today = dt_date.today().strftime("%Y-%m-%d")
    result = {}

    if date_str >= today:
        # --- Today/future: use schedule hydrate=lineups ---
        url = (
            f"{BASE_URL}/schedule?sportId=1&date={date_str}"
            f"&hydrate=lineups"
        )
        try:
            raw = _fetch_json(url)
        except Exception as e:
            print(f"  [mlb_stats] Lineup fetch failed: {e}")
            return {}
        time.sleep(0.5)

        for date_entry in raw.get("dates", []):
            for game in date_entry.get("games", []):
                lineups = game.get("lineups", {})
                teams = game.get("teams", {})

                for side, lineup_key in [("home", "homePlayers"),
                                          ("away", "awayPlayers")]:
                    team_id = teams.get(side, {}).get("team", {}).get("id")
                    abbr = MLB_TEAM_ID_TO_ABBR.get(team_id, "")
                    if not abbr:
                        continue

                    players = lineups.get(lineup_key, [])
                    if not players:
                        continue

                    pct = _compute_pct_lhb(
                        [p.get("id") for p in players], bat_sides
                    )
                    result[abbr] = {
                        "PCT_LHB": pct,
                        "n_batters": len(players),
                        "source": "lineup",
                    }

    else:
        # --- Past dates: use cached batting orders from game logs ---
        # fetch_pitcher_game_logs extracts battingOrder during boxscore pass,
        # so we don't need to re-fetch every boxscore.
        season = season or _current_season()
        bo_cache_path = CACHE_DIR / f"batting_orders_{season}.json"
        bo_data = _load_cache(bo_cache_path, max_age_hours=None)

        if bo_data and date_str in bo_data:
            for abbr, order in bo_data[date_str].items():
                if order:
                    pct = _compute_pct_lhb(order, bat_sides)
                    result[abbr] = {
                        "PCT_LHB": pct,
                        "n_batters": len(order),
                        "source": "boxscore",
                    }
        else:
            # Fallback: fetch boxscores individually (slow, but works)
            sched_url = (
                f"{BASE_URL}/schedule?sportId=1&date={date_str}&gameType=R"
            )
            try:
                sched = _fetch_json(sched_url)
            except Exception:
                return {}
            time.sleep(0.3)

            game_pks = []
            for d in sched.get("dates", []):
                for g in d.get("games", []):
                    if g.get("status", {}).get("abstractGameState") == "Final":
                        game_pks.append(g["gamePk"])

            for gpk in game_pks:
                try:
                    box_url = f"{BASE_URL}.1/game/{gpk}/feed/live"
                    live = _fetch_json(box_url)
                except Exception:
                    continue
                time.sleep(0.15)

                box = live.get("liveData", {}).get("boxscore", {}).get("teams", {})
                for side in ["home", "away"]:
                    side_data = box.get(side, {})
                    team_id = side_data.get("team", {}).get("id")
                    abbr = MLB_TEAM_ID_TO_ABBR.get(team_id, "")
                    if not abbr:
                        continue

                    batting_order = side_data.get("battingOrder", [])
                    if not batting_order:
                        continue

                    pct = _compute_pct_lhb(batting_order, bat_sides)
                    result[abbr] = {
                        "PCT_LHB": pct,
                        "n_batters": len(batting_order),
                        "source": "boxscore",
                    }

    if result:
        _save_cache(cache_path, result)
    return result


def _compute_pct_lhb(player_ids, bat_sides):
    """Compute PCT_LHB from a list of player IDs and bat-side lookup."""
    left = 0.0
    total = 0.0
    for pid in player_ids:
        code = bat_sides.get(pid, bat_sides.get(str(pid), "R"))
        if code == "L":
            left += 1.0
            total += 1.0
        elif code == "S":
            left += 0.5
            total += 1.0
        else:
            total += 1.0
    if total == 0:
        return 0.40  # fallback
    return round(left / total, 4)


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
