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
CACHE_FRESHNESS_HOURS = 0.5    # 30 min TTL for frequently changing data (lineups, probable pitchers)

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


def _load_cache(cache_path, max_age_hours=None, same_day=False):
    """Load cached JSON if it exists and isn't too old.

    Parameters
    ----------
    cache_path : Path
        Path to cached JSON file.
    max_age_hours : float or None
        Maximum cache age in hours.  None = never expire.
        Pass CACHE_FRESHNESS_HOURS explicitly for short-lived caches.
    same_day : bool
        If True, cache expires at midnight (new day = re-fetch).
        Used for game logs that need new games added daily.
    """
    if not cache_path.exists():
        return None
    try:
        mtime = cache_path.stat().st_mtime
        if same_day:
            from datetime import datetime
            cache_date = datetime.fromtimestamp(mtime).date()
            if cache_date < datetime.now().date():
                return None
        elif max_age_hours is not None:
            age_hours = (time.time() - mtime) / 3600
            if age_hours > max_age_hours:
                return None
        # max_age_hours=None and same_day=False → never expire
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
    batter_cache_path = CACHE_DIR / f"batter_game_logs_{season}.json"

    # Load existing cache (never expires — we incrementally add new games)
    existing_rows = _load_cache(cache_path) or []
    existing_batter_logs = _load_cache(batter_cache_path) or {}

    # Build set of already-cached game PKs to skip
    cached_game_ids = set()
    for r in existing_rows:
        gid = r.get("game_id")
        if gid:
            cached_game_ids.add(gid)

    # Step 1: Get all completed regular-season games from the schedule
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
        return existing_rows if existing_rows else []

    # Collect completed game PKs, skip already-cached ones
    all_game_pks = []
    for d in sched.get("dates", []):
        for g in d.get("games", []):
            status = g.get("status", {}).get("abstractGameState", "")
            if status == "Final":
                all_game_pks.append({
                    "pk": g["gamePk"],
                    "date": g.get("officialDate", ""),
                    "home_id": g.get("teams", {}).get("home", {}).get("team", {}).get("id"),
                    "away_id": g.get("teams", {}).get("away", {}).get("team", {}).get("id"),
                })

    new_game_pks = [g for g in all_game_pks if g["pk"] not in cached_game_ids]

    if not new_game_pks:
        print(f"  [mlb_stats] Using cached game logs ({len(existing_rows)} entries, 0 new games)")
        return existing_rows

    print(f"  [mlb_stats] {len(all_game_pks)} completed games, {len(new_game_pks)} new to fetch ({len(cached_game_ids)} cached)")

    # Step 2: Fetch boxscore for NEW games only, extract pitcher + batter stats
    rows = list(existing_rows)
    batting_orders_by_date = {}  # {date: {team_abbr: [player_ids]}}
    batter_logs = {int(k): v for k, v in existing_batter_logs.items()} if existing_batter_logs else {}

    for i, gm in enumerate(new_game_pks):
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

            # --- Opposing starting pitcher info (for batter logs) ---
            opp_pitchers = box.get(opp_side, {}).get("pitchers", [])
            opp_sp_id = opp_pitchers[0] if opp_pitchers else None
            opp_sp_hand = ""
            if opp_sp_id:
                opp_p_data = box.get(opp_side, {}).get("players", {}).get(f"ID{opp_sp_id}", {})
                opp_person = opp_p_data.get("person", {})
                opp_sp_hand = opp_person.get("pitchHand", {}).get("code", "")
                opp_sp_id = opp_person.get("id", opp_sp_id)

            # --- Batter lineup slot lookup ---
            slot_map = {}
            for slot_idx, bo_pid in enumerate(batting_order):
                slot_map[bo_pid] = slot_idx + 1

            # --- Extract batter hitting stats ---
            for player_key, p_data in players.items():
                person = p_data.get("person", {})
                b_pid = person.get("id")
                if not b_pid:
                    continue
                pos = p_data.get("position", {}).get("abbreviation", "")
                b_stats = p_data.get("stats", {}).get("batting", {})
                if pos == "P":
                    if not b_stats or (b_stats.get("atBats", 0) == 0 and
                                       b_stats.get("baseOnBalls", 0) == 0):
                        continue
                if not b_stats:
                    continue
                ab = b_stats.get("atBats", 0) or 0
                b_bb = b_stats.get("baseOnBalls", 0) or 0
                b_hbp = b_stats.get("hitByPitch", 0) or 0
                sac_fly = b_stats.get("sacFlies", 0) or 0
                sac_bunt = b_stats.get("sacBunts", 0) or 0
                pa = ab + b_bb + b_hbp + sac_fly + sac_bunt
                if pa == 0:
                    continue
                b_h = b_stats.get("hits", 0) or 0
                b_doubles = b_stats.get("doubles", 0) or 0
                b_triples = b_stats.get("triples", 0) or 0
                b_hr = b_stats.get("homeRuns", 0) or 0
                b_k = b_stats.get("strikeOuts", 0) or 0
                tb = b_h + b_doubles + 2 * b_triples + 3 * b_hr
                if b_pid not in batter_logs:
                    batter_logs[b_pid] = []
                batter_logs[b_pid].append({
                    "game_date": game_date,
                    "team": team_abbr,
                    "opp": opp_abbr,
                    "pa": pa, "ab": ab, "h": b_h,
                    "doubles": b_doubles, "triples": b_triples, "hr": b_hr,
                    "bb": b_bb, "k": b_k, "hbp": b_hbp, "tb": tb,
                    "lineup_slot": slot_map.get(b_pid, 0),
                    "opp_pitcher_id": opp_sp_id,
                    "opp_pitcher_hand": opp_sp_hand,
                    "is_home": side == "home",
                })

            # --- Extract starting pitcher stats ---
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
                "IP": _ip_to_float(ip_str),
                "ip_str": ip_str,
                "outs": _ip_to_outs(ip_str),
                "h": p_stats.get("hits", 0),
                "bb": p_stats.get("baseOnBalls", 0),
                "hr": p_stats.get("homeRuns", 0),
                "hbp": p_stats.get("hitByPitch", 0),
                "bf": p_stats.get("battersFaced", 0),
                "er": p_stats.get("earnedRuns", 0),
                "pitches": p_stats.get("numberOfPitches", 0),
                "ground_outs": p_stats.get("groundOuts", 0),
                "fly_outs": p_stats.get("airOuts", 0),
                "opp": opp_abbr,
                "is_home": side == "home",
                "is_start": True,
            })

        if (i + 1) % 50 == 0:
            print(f"  [mlb_stats] Processed {i+1}/{len(new_game_pks)} new games")
        time.sleep(0.15)  # light rate limiting

    new_pitcher_count = len(rows) - len(existing_rows)
    print(f"  [mlb_stats] Fetched {new_pitcher_count} new pitcher starts from {len(new_game_pks)} new games (total: {len(rows)})")

    # Cache batting orders by date (used by fetch_lineup_handedness)
    bo_cache_path = CACHE_DIR / f"batting_orders_{season}.json"
    _save_cache(bo_cache_path, batting_orders_by_date)
    print(f"  [mlb_stats] Cached batting orders for {len(batting_orders_by_date)} dates")

    # Cache batter game logs (used by fetch_batter_game_logs)
    batter_cache_path = CACHE_DIR / f"batter_game_logs_{season}.json"
    batter_total = sum(len(v) for v in batter_logs.values())
    _save_cache(batter_cache_path, batter_logs)
    print(f"  [mlb_stats] Cached batter game logs: {len(batter_logs)} batters, {batter_total} entries")

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
    today_str = date.today().strftime("%Y%m%d")
    cache_path = CACHE_DIR / f"pitcher_advanced_{season}_{today_str}.json"

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
            bf = stat.get("battersFaced", 0) or 0
            k = stat.get("strikeOuts", 0) or 0
            k_pct = round(k / bf, 4) if bf > 0 else 0.0

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
                "k": k,
                "k_pct": k_pct,
                "bf": bf,
                "bb": stat.get("baseOnBalls", 0),
                "h": stat.get("hits", 0),
                "hr": stat.get("homeRuns", 0),
                "games": stat.get("gamesPlayed", 0),
                "games_started": gs,
            }

    _save_cache(cache_path, result)
    return result


# ---------------------------------------------------------------------------
# 2b. Baseball Savant K%/Whiff% (CSV bulk download, free, no auth)
# ---------------------------------------------------------------------------

def fetch_savant_pitcher_rates(season=None, min_pa=10):
    """
    Fetch K%, whiff%, BB% from Baseball Savant custom leaderboard.

    Single CSV download — no per-player calls needed.
    Returns {player_id_str: {"k_pct": float, "whiff_pct": float, "bb_pct": float}}
    """
    import csv
    import io

    season = season or _current_season()
    today_str = date.today().strftime("%Y%m%d")
    cache_path = CACHE_DIR / f"savant_rates_{season}_{today_str}.json"

    cached = _load_cache(cache_path)
    if cached is not None:
        print(f"  [savant] Using cached rates ({len(cached)} pitchers)")
        return cached

    url = (
        f"https://baseballsavant.mlb.com/leaderboard/custom"
        f"?year={season}&type=pitcher&filter=&min={min_pa}"
        f"&selections=k_percent,whiff_percent,bb_percent,xba,barrel_batted_rate,hard_hit_percent"
        f"&chart=false&csv=true"
    )

    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"  [savant] Failed to fetch: {e}")
        return {}

    text = resp.text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text))

    result = {}
    for row in reader:
        pid = row.get("player_id", "").strip()
        if not pid:
            continue
        try:
            k_pct = float(row.get("k_percent", 0) or 0) / 100.0  # Savant gives as %
            whiff_pct = float(row.get("whiff_percent", 0) or 0) / 100.0
            bb_pct = float(row.get("bb_percent", 0) or 0) / 100.0
        except (ValueError, TypeError):
            continue

        try:
            xba = float(row.get("xba", 0) or 0)
            barrel = float(row.get("barrel_batted_rate", 0) or 0) / 100.0
            hard_hit = float(row.get("hard_hit_percent", 0) or 0) / 100.0
        except (ValueError, TypeError):
            xba, barrel, hard_hit = 0, 0, 0

        result[pid] = {
            "k_pct": round(k_pct, 4),
            "whiff_pct": round(whiff_pct, 4),
            "bb_pct": round(bb_pct, 4),
            "xba": round(xba, 4),
            "barrel_pct": round(barrel, 4),
            "hard_hit_pct": round(hard_hit, 4),
        }

    print(f"  [savant] Fetched rates for {len(result)} pitchers")
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
    today_str = date.today().strftime("%Y%m%d")
    cache_path = CACHE_DIR / f"pitcher_sabermetrics_{season}_{today_str}.json"

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

    cached = _load_cache(cache_path, max_age_hours=None)
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
# 4b. Batter Strikeout Rates (season + vs LHP/RHP splits)
# ---------------------------------------------------------------------------

def fetch_batter_k_rates(season=None):
    """
    Fetch per-batter K% for the entire league (bulk, 2 API calls).

    Returns dict keyed by player_id (int):
        {player_id: {
            "name": str, "team_id": int,
            "k_pct": float,         # overall K%
            "pa": int,              # plate appearances
            "k": int,               # strikeouts
            "k_pct_vs_lhp": float,  # K% vs left-handed pitchers
            "k_pct_vs_rhp": float,  # K% vs right-handed pitchers
            "pa_vs_lhp": int,
            "pa_vs_rhp": int,
        }}
    """
    season = season or _current_season()
    today_str = date.today().strftime("%Y%m%d")
    cache_path = CACHE_DIR / f"batter_k_rates_{season}_{today_str}.json"

    cached = _load_cache(cache_path)
    if cached is not None:
        return {int(k): v for k, v in cached.items()}

    result = {}

    # --- Bulk season stats (overall K%) ---
    url = (
        f"{BASE_URL}/stats?stats=season&group=hitting&season={season}"
        f"&sportId=1&playerPool=all&limit=1000"
    )
    try:
        raw = _fetch_json(url)
    except Exception as e:
        print(f"  [mlb_stats] Batter stats fetch failed: {e}")
        return {}
    time.sleep(0.5)

    for stat_group in raw.get("stats", []):
        for split in stat_group.get("splits", []):
            stat = split.get("stat", {})
            player = split.get("player", {})
            pid = player.get("id")
            if not pid:
                continue

            pa = stat.get("plateAppearances", 0) or 0
            k = stat.get("strikeOuts", 0) or 0
            pos = split.get("position", {}).get("abbreviation", "")

            # Skip pitchers
            if pos == "P":
                continue

            result[pid] = {
                "name": player.get("fullName", ""),
                "team_id": split.get("team", {}).get("id"),
                "k_pct": round(k / pa, 4) if pa > 0 else 0.0,
                "pa": pa,
                "k": k,
                "k_pct_vs_lhp": 0.0,
                "k_pct_vs_rhp": 0.0,
                "pa_vs_lhp": 0,
                "pa_vs_rhp": 0,
            }

    # --- Bulk splits vs LHP/RHP ---
    url2 = (
        f"{BASE_URL}/stats?stats=statSplits&group=hitting&season={season}"
        f"&sportId=1&sitCodes=vl,vr&playerPool=all&limit=1000"
    )
    try:
        raw2 = _fetch_json(url2)
    except Exception as e:
        print(f"  [mlb_stats] Batter splits fetch failed: {e}")
        _save_cache(cache_path, result)
        return result
    time.sleep(0.5)

    for stat_group in raw2.get("stats", []):
        for split in stat_group.get("splits", []):
            stat = split.get("stat", {})
            player = split.get("player", {})
            pid = player.get("id")
            split_code = split.get("split", {}).get("code", "")

            if not pid or pid not in result:
                continue

            pa = stat.get("plateAppearances", 0) or 0
            # plateAppearances is often 0 in splits; use battersFaced or compute
            if pa == 0:
                pa = (stat.get("atBats", 0) or 0) + (stat.get("baseOnBalls", 0) or 0) + \
                     (stat.get("hitByPitch", 0) or 0) + (stat.get("sacFlies", 0) or 0) + \
                     (stat.get("sacBunts", 0) or 0)
            k = stat.get("strikeOuts", 0) or 0

            if split_code == "vl":  # vs left-handed pitcher
                result[pid]["k_pct_vs_lhp"] = round(k / pa, 4) if pa > 0 else 0.0
                result[pid]["pa_vs_lhp"] = pa
            elif split_code == "vr":  # vs right-handed pitcher
                result[pid]["k_pct_vs_rhp"] = round(k / pa, 4) if pa > 0 else 0.0
                result[pid]["pa_vs_rhp"] = pa

    print(f"  [mlb_stats] Fetched K rates for {len(result)} batters")
    _save_cache(cache_path, result)
    return result


def compute_lineup_k_pct(lineup_player_ids, batter_k_rates, pitcher_hand="R"):
    """
    Compute lineup-specific K% from the actual batting order.

    Parameters
    ----------
    lineup_player_ids : list[int]
        Player IDs of the 9 batters in the lineup.
    batter_k_rates : dict
        {player_id: {"k_pct": float, "k_pct_vs_lhp": float, "k_pct_vs_rhp": float, ...}}
    pitcher_hand : str
        "L" or "R" — the pitcher's throwing hand.

    Returns
    -------
    dict
        {"lineup_k_pct": float, "lineup_k_pct_vs_hand": float, "n_batters": int}
        lineup_k_pct: overall K% of the lineup
        lineup_k_pct_vs_hand: K% specifically vs this pitcher's handedness
    """
    k_pcts = []
    k_pcts_vs_hand = []

    hand_key = "k_pct_vs_lhp" if pitcher_hand == "L" else "k_pct_vs_rhp"

    for pid in lineup_player_ids:
        batter = batter_k_rates.get(pid) or batter_k_rates.get(str(pid))
        if not batter:
            continue

        if batter.get("k_pct", 0) > 0:
            k_pcts.append(batter["k_pct"])

        # Handedness-specific K%
        vs_hand = batter.get(hand_key, 0)
        if vs_hand > 0:
            k_pcts_vs_hand.append(vs_hand)
        elif batter.get("k_pct", 0) > 0:
            # Fallback to overall if no split data
            k_pcts_vs_hand.append(batter["k_pct"])

    return {
        "lineup_k_pct": round(sum(k_pcts) / len(k_pcts), 4) if k_pcts else 0.0,
        "lineup_k_pct_vs_hand": round(sum(k_pcts_vs_hand) / len(k_pcts_vs_hand), 4) if k_pcts_vs_hand else 0.0,
        "n_batters": len(k_pcts),
    }


# ---------------------------------------------------------------------------
# 5. Team Batting Stats
# ---------------------------------------------------------------------------

def fetch_team_batting_stats(season=None):
    """
    Fetch team-level batting stats.
    Returns dict keyed by team abbreviation.
    """
    season = season or _current_season()
    today_str = date.today().strftime("%Y%m%d")
    cache_path = CACHE_DIR / f"team_batting_{season}_{today_str}.json"

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
    today_str = date.today().strftime("%Y%m%d")
    cache_path = CACHE_DIR / f"team_pitching_{season}_{today_str}.json"

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

    cached = _load_cache(cache_path, max_age_hours=CACHE_FRESHNESS_HOURS)
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
    pitch_hands = {}
    for player in raw.get("people", []):
        pid = player.get("id")
        bat_code = player.get("batSide", {}).get("code", "R")
        pitch_code = player.get("pitchHand", {}).get("code", "R")
        if pid:
            result[pid] = bat_code
            pitch_hands[pid] = pitch_code

    # Also cache pitch hands separately for K% lineup lookup
    pitch_hand_path = CACHE_DIR / f"pitch_hands_{season}.json"
    _save_cache(pitch_hand_path, pitch_hands)
    print(f"  [mlb_stats] Also cached pitch hands for {len(pitch_hands)} players")

    print(f"  [mlb_stats] Fetched bat sides for {len(result)} players")
    _save_cache(cache_path, result)
    return result


def load_pitch_hands(season=None):
    """Load cached pitcher throwing hand lookup: {player_id: 'L'|'R'}."""
    season = season or _current_season()
    cache_path = CACHE_DIR / f"pitch_hands_{season}.json"
    cached = _load_cache(cache_path, max_age_hours=None)
    if cached:
        return {int(k): v for k, v in cached.items()}
    # If not cached, trigger bat_sides fetch which also caches pitch hands
    fetch_player_bat_sides(season=season)
    cached = _load_cache(cache_path, max_age_hours=None)
    return {int(k): v for k, v in cached.items()} if cached else {}


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

    from datetime import date as dt_date_check
    is_today = date_str >= dt_date_check.today().strftime("%Y-%m-%d")
    # Today's lineups change (1hr TTL), past dates never expire
    cached = _load_cache(cache_path, max_age_hours=CACHE_FRESHNESS_HOURS if is_today else None)
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
# 9. Batter Game Logs (from boxscores)
# ---------------------------------------------------------------------------

def fetch_batter_game_logs(season=None):
    """
    Fetch batter game logs for the season.

    Reads from the cache built by fetch_pitcher_game_logs (which extracts
    batter stats in the same boxscore loop). If the cache doesn't exist,
    triggers fetch_pitcher_game_logs first to build it.

    Returns dict keyed by batter_id (int):
        {batter_id: [{"game_date", "team", "opp", "pa", "ab", "h",
                       "doubles", "triples", "hr", "bb", "k", "hbp",
                       "tb", "lineup_slot", "opp_pitcher_id",
                       "opp_pitcher_hand", "is_home"}, ...]}
    """
    season = season or _current_season()
    cache_path = CACHE_DIR / f"batter_game_logs_{season}.json"

    cached = _load_cache(cache_path)
    if cached is not None:
        total = sum(len(v) for v in cached.values())
        print(f"  [mlb_stats] Using cached batter game logs ({len(cached)} batters, {total} entries)")
        return {int(k): v for k, v in cached.items()}

    # Cache doesn't exist — trigger pitcher game logs fetch which builds it
    print(f"  [mlb_stats] Batter cache not found, building via pitcher game logs fetch...")
    fetch_pitcher_game_logs(season=season)

    # Now read the cache that fetch_pitcher_game_logs just built
    cached = _load_cache(cache_path, max_age_hours=None)
    if cached is not None:
        total = sum(len(v) for v in cached.values())
        print(f"  [mlb_stats] Batter game logs ready ({len(cached)} batters, {total} entries)")
        return {int(k): v for k, v in cached.items()}

    print(f"  [mlb_stats] Warning: batter game logs cache not built after pitcher fetch")
    return {}


# ---------------------------------------------------------------------------
# 10. Baseball Savant Batter Rates (CSV bulk download)
# ---------------------------------------------------------------------------

def fetch_savant_batter_rates(season=None, min_pa=50):
    """
    Fetch batter Statcast rates from Baseball Savant custom leaderboard.

    Single CSV download — no per-player calls needed.
    Returns {player_id_str: {"barrel_pct", "hard_hit_pct", "xslg", "xwoba",
                              "iso", "avg_ev", "fb_pct", "gb_pct"}}
    """
    import csv
    import io

    season = season or _current_season()
    today_str = date.today().strftime("%Y%m%d")
    cache_path = CACHE_DIR / f"savant_batter_rates_{season}_{today_str}.json"

    cached = _load_cache(cache_path)
    if cached is not None:
        print(f"  [savant] Using cached batter rates ({len(cached)} batters)")
        return cached

    url = (
        f"https://baseballsavant.mlb.com/leaderboard/custom"
        f"?year={season}&type=batter&filter=&min={min_pa}"
        f"&selections=barrel_batted_rate,hard_hit_percent,xslg,xwoba,iso,"
        f"avg_hit_speed,flyballs_percent,groundballs_percent"
        f"&chart=false&csv=true"
    )

    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"  [savant] Failed to fetch batter rates: {e}")
        return {}

    text = resp.text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text))

    result = {}
    for row in reader:
        pid = row.get("player_id", "").strip()
        if not pid:
            continue

        try:
            barrel = float(row.get("barrel_batted_rate", 0) or 0) / 100.0
            hard_hit = float(row.get("hard_hit_percent", 0) or 0) / 100.0
        except (ValueError, TypeError):
            barrel, hard_hit = 0, 0

        try:
            xslg = float(row.get("xslg", 0) or 0)
            xwoba = float(row.get("xwoba", 0) or 0)
            iso = float(row.get("iso", 0) or 0)
        except (ValueError, TypeError):
            xslg, xwoba, iso = 0, 0, 0

        try:
            avg_ev = float(row.get("avg_hit_speed", 0) or 0)
        except (ValueError, TypeError):
            avg_ev = 0

        try:
            fb_pct = float(row.get("flyballs_percent", 0) or 0) / 100.0
            gb_pct = float(row.get("groundballs_percent", 0) or 0) / 100.0
        except (ValueError, TypeError):
            fb_pct, gb_pct = 0, 0

        result[pid] = {
            "barrel_pct": round(barrel, 4),
            "hard_hit_pct": round(hard_hit, 4),
            "xslg": round(xslg, 4),
            "xwoba": round(xwoba, 4),
            "iso": round(iso, 4),
            "avg_ev": round(avg_ev, 1),
            "fb_pct": round(fb_pct, 4),
            "gb_pct": round(gb_pct, 4),
        }

    print(f"  [savant] Fetched batter rates for {len(result)} batters")
    _save_cache(cache_path, result)
    return result


# ---------------------------------------------------------------------------
# 11. Batter Hitting Splits (vs LHP / vs RHP)
# ---------------------------------------------------------------------------

def fetch_batter_splits(season=None):
    """
    Fetch batter hitting splits vs LHP and RHP (bulk, 2 API calls).

    Returns dict keyed by batter_id (int):
        {batter_id: {
            "vs_lhp": {"ba", "slg", "iso", "hr_rate", "k_pct", "pa"},
            "vs_rhp": {"ba", "slg", "iso", "hr_rate", "k_pct", "pa"},
        }}
    """
    season = season or _current_season()
    today_str = date.today().strftime("%Y%m%d")
    cache_path = CACHE_DIR / f"batter_splits_{season}_{today_str}.json"

    cached = _load_cache(cache_path)
    if cached is not None:
        print(f"  [mlb_stats] Using cached batter splits ({len(cached)} batters)")
        return {int(k): v for k, v in cached.items()}

    result = {}

    # --- Bulk season stats (overall, to identify position players) ---
    url = (
        f"{BASE_URL}/stats?stats=season&group=hitting&season={season}"
        f"&sportId=1&playerPool=all&limit=1000"
    )
    try:
        raw = _fetch_json(url)
    except Exception as e:
        print(f"  [mlb_stats] Batter season stats fetch failed: {e}")
        return {}
    time.sleep(0.5)

    # Build set of position player IDs (skip pitchers)
    position_players = set()
    for stat_group in raw.get("stats", []):
        for split in stat_group.get("splits", []):
            player = split.get("player", {})
            pid = player.get("id")
            pos = split.get("position", {}).get("abbreviation", "")
            if pid and pos != "P":
                position_players.add(pid)

    # --- Bulk splits vs LHP/RHP ---
    url2 = (
        f"{BASE_URL}/stats?stats=statSplits&group=hitting&season={season}"
        f"&sportId=1&sitCodes=vl,vr&playerPool=all&limit=1000"
    )
    try:
        raw2 = _fetch_json(url2)
    except Exception as e:
        print(f"  [mlb_stats] Batter splits fetch failed: {e}")
        return {}
    time.sleep(0.5)

    for stat_group in raw2.get("stats", []):
        for split in stat_group.get("splits", []):
            stat = split.get("stat", {})
            player = split.get("player", {})
            pid = player.get("id")
            split_code = split.get("split", {}).get("code", "")

            if not pid or pid not in position_players:
                continue

            # Compute PA (plateAppearances is often 0 in splits)
            pa = stat.get("plateAppearances", 0) or 0
            if pa == 0:
                pa = ((stat.get("atBats", 0) or 0) +
                      (stat.get("baseOnBalls", 0) or 0) +
                      (stat.get("hitByPitch", 0) or 0) +
                      (stat.get("sacFlies", 0) or 0) +
                      (stat.get("sacBunts", 0) or 0))

            ab = stat.get("atBats", 0) or 0
            h = stat.get("hits", 0) or 0
            hr = stat.get("homeRuns", 0) or 0
            k = stat.get("strikeOuts", 0) or 0
            doubles = stat.get("doubles", 0) or 0
            triples = stat.get("triples", 0) or 0

            ba = round(h / ab, 4) if ab > 0 else 0.0
            # SLG = (1B + 2*2B + 3*3B + 4*HR) / AB
            singles = h - doubles - triples - hr
            slg = round((singles + 2 * doubles + 3 * triples + 4 * hr) / ab, 4) if ab > 0 else 0.0
            iso = round(slg - ba, 4)
            hr_rate = round(hr / pa, 4) if pa > 0 else 0.0
            k_pct = round(k / pa, 4) if pa > 0 else 0.0

            split_data = {
                "ba": ba,
                "slg": slg,
                "iso": iso,
                "hr_rate": hr_rate,
                "k_pct": k_pct,
                "pa": pa,
            }

            if pid not in result:
                result[pid] = {"vs_lhp": {}, "vs_rhp": {}}

            if split_code == "vl":  # vs left-handed pitcher
                result[pid]["vs_lhp"] = split_data
            elif split_code == "vr":  # vs right-handed pitcher
                result[pid]["vs_rhp"] = split_data

    print(f"  [mlb_stats] Fetched hitting splits for {len(result)} batters")
    _save_cache(cache_path, result)
    return result


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
