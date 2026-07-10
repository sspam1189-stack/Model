"""
MLB Stats API fetcher.
Uses statsapi.mlb.com (free, no auth required).
"""

import requests
import json
import os
import time
from datetime import datetime, date, timedelta
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "pitcher_cache" / "mlb"
CACHE_FRESHNESS_HOURS = 0      # always fetch fresh (lineups, probable pitchers)

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
# Walk-forward leak-fix config (used when through_date is passed)
# ---------------------------------------------------------------------------
# Pitcher vs batter window asymmetry — deliberate, do not "unify":
#   * Pitcher-side inputs effectively use SEASON-to-date. Hand-split blend is
#     SPLITS_BLEND_WEIGHT=0.0 (defaults.py) so the rolling fetch here is dead
#     weight; hand-weighted K% in props_engine is season; Kalman uses every
#     start. Pitcher rates are stable — season wins on sample size.
#   * Batter/team-side inputs USE the rolling-45d window below
#     (fetch_team_batting_stats, fetch_batter_k_rates), with season fallback
#     when sample too small. Team K% drifts (roster churn, hot/cold), so 45d
#     captures current identity instead of being anchored by April data.
# Empirical: switching team_batting to season-to-date cost -27u / -8pp WR on
# backfill (196@80.1%/+109.8u → 222@72.1%/+82.5u). Reverted at db7a4216.
RECENT_WINDOW_DAYS = 45
RECENT_MIN_BF_PER_SPLIT = 40    # for pitcher splits fallback
RECENT_MIN_TEAM_PA = 150        # for team batting fallback
RECENT_MIN_BATTER_PA = 50       # for batter k_rates fallback


def _date_window(season, through_date, window_days=RECENT_WINDOW_DAYS):
    """
    Return (startDate, endDate) ISO strings for a rolling window ending at
    through_date, clamped so startDate is no earlier than {season}-03-20.

    Parameters
    ----------
    season : int
    through_date : str (YYYY-MM-DD)
    window_days : int
    """
    from datetime import datetime as _dt, timedelta as _td
    end = _dt.strptime(through_date, "%Y-%m-%d").date()
    start = end - _td(days=window_days)
    season_start = _dt.strptime(f"{season}-03-20", "%Y-%m-%d").date()
    if start < season_start:
        start = season_start
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


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
    """Save data to cache file.

    Atomic write (temp file + os.replace) with a short retry. The repo often
    lives under a synced folder (OneDrive/Desktop) that intermittently locks
    files mid-sync, surfacing as transient OSError [Errno 22]/[Errno 13] on
    write. A single such hiccup used to abort long multi-cell sweeps; retrying
    a few times rides over the lock, and the temp+replace avoids leaving a
    half-written cache behind.
    """
    import time as _time
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    last_err = None
    for attempt in range(5):
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, cache_path)
            return
        except OSError as e:
            last_err = e
            _time.sleep(0.3 * (attempt + 1))
    # Final fallback: best-effort direct write; swallow if it still fails so a
    # cache-write blip never crashes a backfill/sweep mid-run.
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        print(f"  [cache] WARN: could not write {cache_path} ({last_err})")
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass


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

    # Identify gamePks whose cached rows are all degenerate (bf=0, outs=0,
    # pitches=0) — typically stale captures of makeup/postponed games where
    # the API briefly flagged Final with empty stats. Re-fetch these so the
    # cache picks up the real line.
    def _is_degenerate(r):
        return (int(r.get("bf", 0) or 0) == 0
                and int(r.get("outs", 0) or 0) == 0
                and int(r.get("pitches", 0) or 0) == 0)

    from collections import defaultdict as _dd
    _by_pk = _dd(list)
    for r in existing_rows:
        gid = r.get("game_id")
        if gid:
            _by_pk[gid].append(r)
    degenerate_game_pks = {pk for pk, group in _by_pk.items()
                           if group and all(_is_degenerate(r) for r in group)}
    if degenerate_game_pks:
        print(f"  [mlb_stats] Re-fetching {len(degenerate_game_pks)} game(s) with degenerate cached rows")
        # Drop degenerate rows so the re-fetch replaces them rather than duplicating
        existing_rows = [r for r in existing_rows
                         if r.get("game_id") not in degenerate_game_pks]

    # Build set of already-cached game PKs to skip (excluding degenerate ones)
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
    # Load existing batting orders and merge new games into them (incremental)
    bo_cache_path = CACHE_DIR / f"batting_orders_{season}.json"
    existing_bo = _load_cache(bo_cache_path) or {}
    batting_orders_by_date = {d: dict(teams) for d, teams in existing_bo.items()}
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

            # --- Extract pitcher stats (EVERY pitcher, not just the starter) ---
            # pitchers[] is in order of appearance: pitchers[0] is the game's
            # first pitcher (is_start=True — includes openers); everyone after
            # is a relief row (is_start=False). Relief rows matter because a
            # rotation arm can live behind an opener for weeks (e.g. Fedde,
            # June 2026: four bulk outings of 2.2-5.0 IP, none as pitchers[0])
            # — with starter-only logs he looks absent, which corrupts
            # rest-gate gaps and drops 3+ IP bulk outings organize_pitcher_logs
            # is designed to keep.
            for p_idx, sp_id in enumerate(pitchers):
                p_data = players.get(f"ID{sp_id}", {})
                person = p_data.get("person", {})
                p_stats = p_data.get("stats", {}).get("pitching", {})

                if not p_stats:
                    continue

                # Guard against half-populated boxscores (bf=0, outs=0,
                # pitches=0) — the API sometimes flips a makeup game to Final
                # before stats propagate, returning a stats dict full of
                # zeros. Skip so we try again next run instead of locking in
                # a bad row.
                _bf = int(p_stats.get("battersFaced", 0) or 0)
                _pc = int(p_stats.get("numberOfPitches", 0) or 0)
                ip_str = p_stats.get("inningsPitched", "0")
                if _bf == 0 and _pc == 0 and _ip_to_outs(ip_str) == 0:
                    continue

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
                    "is_start": p_idx == 0,
                })

        if (i + 1) % 50 == 0:
            print(f"  [mlb_stats] Processed {i+1}/{len(new_game_pks)} new games")
        time.sleep(0.15)  # light rate limiting

    new_pitcher_count = len(rows) - len(existing_rows)
    print(f"  [mlb_stats] Fetched {new_pitcher_count} new pitcher starts from {len(new_game_pks)} new games (total: {len(rows)})")

    # Cache batting orders by date (used by fetch_lineup_handedness)
    # bo_cache_path defined above; merged dict includes existing + new
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
    Fetch season-level pitching stats restricted to STARTER outings only.

    Uses the MLB Stats API split endpoint with `sitCodes=sp` (Starter) so
    every aggregate value (K/9, K%, WHIP, ERA, IP, BF, GS, etc.) reflects
    starts only. The plain `stats=season` aggregate mixed relief and
    starter appearances together, which biased K-rate inputs high for
    dual-role pitchers (Tyler Alexander, Martín Pérez post-demotion,
    openers, swingmen). The split endpoint is the same single bulk call
    — no per-player fanout — so cost is identical.

    Returns dict keyed by str(player_id). Pitchers who haven't started a
    game this season are absent from the response.
    """
    season = season or _current_season()
    today_str = date.today().strftime("%Y%m%d")
    cache_path = CACHE_DIR / f"pitcher_advanced_{season}_{today_str}.json"

    cached = _load_cache(cache_path)
    if cached is not None:
        return cached

    url = (
        f"{BASE_URL}/stats?stats=statSplits&group=pitching&season={season}"
        f"&sportId=1&playerPool=all&sitCodes=sp&limit=500"
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


def fetch_pitcher_advanced_stats_through(season, end_date):
    """
    Walk-forward through-date version of `fetch_pitcher_advanced_stats`.

    Returns starter-only aggregate pitching stats spanning the season
    from spring training through `end_date` (YYYY-MM-DD). Used by the
    walk-forward backfill so each game-date sees only the stats that
    would have been available before that date. Same fields, same
    dict shape as `fetch_pitcher_advanced_stats`.

    Caches per end_date — historical days never need to refetch, only
    the current/forward edge gets a fresh API call.
    """
    season = season or _current_season()
    end_key = end_date.replace("-", "")
    cache_path = CACHE_DIR / f"pitcher_advanced_starter_{season}_thru_{end_key}.json"

    cached = _load_cache(cache_path)
    if cached is not None:
        return cached

    url = (
        f"{BASE_URL}/stats?stats=byDateRange&group=pitching&season={season}"
        f"&sportId=1&playerPool=all&sitCodes=sp"
        f"&startDate={season}-03-01&endDate={end_date}&limit=500"
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
    Fetch K%, whiff%, xBA from Baseball Savant custom leaderboard.

    Single CSV download — no per-player calls needed. Returns
    {player_id_str: {"k_pct": float, "whiff_pct": float, "xba": float}}.
    whiff_pct and xba feed the K-rate regression in props_engine
    (gated by CSW_XBA_BLEND_WEIGHT > 0).
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
        f"&selections=k_percent,whiff_percent,xba"
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
            xba = float(row.get("xba", 0) or 0)
        except (ValueError, TypeError):
            continue

        result[pid] = {
            "k_pct":     round(k_pct, 4),
            "whiff_pct": round(whiff_pct, 4),
            "xba":       round(xba, 4),
        }

    print(f"  [savant] Fetched rates for {len(result)} pitchers")
    _save_cache(cache_path, result)
    return result


def load_savant_rates_as_of(through_date, season=None):
    """
    Load the most recent daily savant snapshot captured on or before
    `through_date` (YYYY-MM-DD), for walk-forward backfill.

    Baseball Savant's custom leaderboard does NOT honor date-range params
    (verified against the API), so its whiff%/xBA can only be pulled as a
    season-to-date snapshot. To stay leak-free in backfill we replay the
    dated snapshots run_daily cached each day it ran
    (savant_rates_{season}_{YYYYMMDD}.json) — i.e. exactly the season-to-date
    figures that were known on/before the projection date.

    Returns {} when no snapshot on/before `through_date` exists (e.g. dates
    before daily caching began). An empty dict makes props_engine skip the
    whiff/xBA adjustment entirely (the `if whiff>0 or xba>0` guard), so early
    dates degrade to the no-blend baseline rather than leaking future data.
    """
    import glob
    season = season or _current_season()
    thru_key = through_date.replace("-", "")
    prefix = f"savant_rates_{season}_"
    best_key = None
    best_path = None
    for p in glob.glob(str(CACHE_DIR / f"{prefix}*.json")):
        stamp = os.path.basename(p)[len(prefix):-len(".json")]
        if len(stamp) != 8 or not stamp.isdigit():
            continue  # skips the undated savant_rates_{season}.json
        if stamp <= thru_key and (best_key is None or stamp > best_key):
            best_key, best_path = stamp, p
    if best_path is None:
        return {}
    try:
        with open(best_path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def compute_csw_xba_regression(savant_data, min_pitchers=50, x1_key="whiff_pct"):
    """
    Fit bivariate OLS of K% on (x1_key, xba) from the current savant
    snapshot. Returns slopes + league means used by props_engine to
    regress pitcher_k_rate.

    x1_key selects the first regressor: "csw" (Called-Strikes+Whiffs%,
    merged in from load_csw_as_of — the shipped K-quality metric) or
    "whiff_pct" (the deprecated fallback). The return keys are
    csw_slope/csw_mean regardless of x1_key — they denote "the metric-1
    slope/mean" and feed CSW_K_SLOPE/CSW_LEAGUE_AVG in props_engine, which
    multiplies them by whichever metric K_QUALITY_METRIC selects.

    Returns {'csw_slope', 'xba_slope', 'csw_mean', 'xba_mean', 'n', 'r2'}
    or None if fewer than min_pitchers qualify (caller should fall back
    to defaults.py hardcoded values).
    """
    pairs = []
    for pid, r in (savant_data or {}).items():
        w = r.get(x1_key); x = r.get("xba"); k = r.get("k_pct")
        if w and x and k and w > 0 and x > 0 and k > 0:
            pairs.append((w, x, k))
    n = len(pairs)
    if n < min_pitchers:
        return None

    ws = [p[0] for p in pairs]
    xs = [p[1] for p in pairs]
    ks = [p[2] for p in pairs]
    mw = sum(ws) / n
    mx = sum(xs) / n
    mk = sum(ks) / n
    vww = sum((a - mw) ** 2 for a in ws)
    vxx = sum((a - mx) ** 2 for a in xs)
    vwx = sum((a - mw) * (b - mx) for a, b in zip(ws, xs))
    cwk = sum((a - mw) * (k - mk) for a, k in zip(ws, ks))
    cxk = sum((b - mx) * (k - mk) for b, k in zip(xs, ks))
    det = vww * vxx - vwx ** 2
    if det == 0:
        return None
    bw = (cwk * vxx - cxk * vwx) / det
    bx = (cxk * vww - cwk * vwx) / det
    ssr = sum((bw * (w - mw) + bx * (x - mx) - (k - mk)) ** 2
              for w, x, k in pairs)
    sst = sum((k - mk) ** 2 for k in ks)
    r2 = 1 - ssr / sst if sst else 0

    return {
        "csw_slope":   bw,
        "xba_slope":   bx,
        "csw_mean":    mw,
        "xba_mean":    mx,
        "n":           n,
        "r2":          r2,
    }


# ---------------------------------------------------------------------------
# 2c. Called-Strikes+Whiffs% (CSW) from Statcast pitch-level data
# ---------------------------------------------------------------------------
# CSW is NOT exposed by the custom leaderboard (those columns export empty),
# so we reconstruct it from the pitch-level Statcast search, which — unlike
# the leaderboard — honors date ranges. We download one game-date at a time
# and cache an IMMUTABLE daily tally (past days never change), then sum those
# tallies up to prior_date to get a leak-free season-to-date CSW for backfill.
# This mirrors load_savant_rates_as_of's walk-forward contract but rebuilds
# the metric instead of replaying snapshots that were never captured for CSW.

# CSW numerator = strikes the batter did not put in play. foul_tip and
# swinging_strike_blocked are whiffs (caught/uncaught); called_strike is the
# command/zone half. Matches the standard Pitcher List CSW convention.
_CSW_NUMERATOR_DESCRIPTIONS = frozenset({
    "called_strike",
    "swinging_strike",
    "swinging_strike_blocked",
    "foul_tip",
})


def _fetch_statcast_day(day_iso):
    """Download one game-date of pitch-level Statcast rows. Returns the list
    of {pitcher, description} dicts, or None on persistent failure."""
    import csv as _csv
    import io as _io
    url = (
        "https://baseballsavant.mlb.com/statcast_search/csv?all=true"
        f"&player_type=pitcher&game_date_gt={day_iso}&game_date_lt={day_iso}"
        "&type=details"
    )
    for attempt in range(4):
        try:
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"},
                                timeout=90)
            resp.raise_for_status()
            reader = _csv.DictReader(_io.StringIO(resp.text.lstrip("﻿")))
            return [{"pitcher": r.get("pitcher", "").strip(),
                     "description": (r.get("description") or "").strip()}
                    for r in reader]
        except Exception as e:
            if attempt == 3:
                print(f"  [csw] {day_iso} fetch failed after retries: {e}")
                return None
            time.sleep(3)
    return None


def fetch_statcast_pitch_csw(season=None, start_date=None, end_date=None):
    """
    Build per-game-date CSW tallies per pitcher from Statcast pitch data.

    Iterates calendar days from start_date..end_date and, for each day with no
    immutable cache yet, downloads that day's pitches and writes
    csw_daily_{season}_{YYYYMMDD}.json = {pid: {"csw": int, "pitches": int}}.
    Only days strictly before today are cached (today's games are incomplete
    and, being same-day, would leak into a same-day live projection anyway).

    Returns the count of daily files now present in range. load_csw_as_of()
    consumes these; this fn just ensures they exist (idempotent / cheap on
    re-run since past days are skipped once cached).
    """
    season = season or _current_season()
    today = date.today()
    if start_date is None:
        # Cover late-March openers; missing early off-days just cache empty.
        start = date(int(season), 3, 15)
    else:
        start = date.fromisoformat(start_date)
    if end_date is None:
        end = today - timedelta(days=1)   # never cache today (incomplete)
    else:
        end = min(date.fromisoformat(end_date), today - timedelta(days=1))

    present = 0
    fetched = 0
    d = start
    while d <= end:
        day_iso = d.isoformat()
        cache_path = CACHE_DIR / f"csw_daily_{season}_{d.strftime('%Y%m%d')}.json"
        if cache_path.exists():
            present += 1
            d += timedelta(days=1)
            continue
        rows = _fetch_statcast_day(day_iso)
        if rows is None:
            # Transient failure: don't cache, leave gap for a later run to fill.
            d += timedelta(days=1)
            continue
        tally = {}
        for r in rows:
            pid = r["pitcher"]
            if not pid:
                continue
            t = tally.setdefault(pid, {"csw": 0, "pitches": 0})
            t["pitches"] += 1
            if r["description"] in _CSW_NUMERATOR_DESCRIPTIONS:
                t["csw"] += 1
        _save_cache(cache_path, tally)
        present += 1
        fetched += 1
        d += timedelta(days=1)
    if fetched:
        print(f"  [csw] cached {fetched} new daily tally files "
              f"({present} total in {start}..{end})")
    else:
        print(f"  [csw] {present} daily tally files present ({start}..{end})")
    return present


def load_csw_as_of(through_date, season=None, min_pitches=100):
    """
    Walk-forward CSW loader: sum the immutable daily tallies on/before
    through_date (YYYY-MM-DD) into a per-pitcher season-to-date CSW%.

    Returns {pid_str: {"csw": float}} for pitchers with >= min_pitches seen
    by through_date. Pitchers below the pitch floor are dropped (noisy rate),
    which makes props_engine skip the adjustment for them — same degradation
    as a missing savant entry. Returns {} when no daily files cover the range
    (pre-caching dates) so early backfill dates fall back to the no-blend
    baseline, exactly like the whiff path.
    """
    import glob, re
    season = season or _current_season()
    target = through_date.replace("-", "")
    pattern = str(CACHE_DIR / f"csw_daily_{season}_*.json")
    agg = {}
    for f in sorted(glob.glob(pattern)):
        m = re.search(rf"csw_daily_{season}_(\d{{8}})\.json$", f)
        if not m or m.group(1) > target:
            continue
        try:
            with open(f, "r", encoding="utf-8") as fh:
                day = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        for pid, t in day.items():
            a = agg.setdefault(pid, {"csw": 0, "pitches": 0})
            a["csw"] += t.get("csw", 0)
            a["pitches"] += t.get("pitches", 0)
    out = {}
    for pid, a in agg.items():
        if a["pitches"] >= min_pitches:
            out[pid] = {"csw": round(a["csw"] / a["pitches"], 4)}
    return out


def _regression_cache_prefix(metric):
    """Cache-file prefix per K-quality metric. CSW fits are stored separately
    from whiff fits so a whiff-era cached file is never mistaken for a CSW fit
    (and vice versa) after a K_QUALITY_METRIC switch."""
    return "csw_xba_regression" if metric == "csw" else "whiff_xba_regression"


def _normalize_reg_keys(d):
    """Back-compat: pre-rename cached regression files (and run_daily files
    written before the whiff->csw rename) stored the generic metric-1 slope
    as whiff_slope/whiff_mean. Map them to csw_slope/csw_mean on load so old
    caches keep working without a refetch."""
    if not d:
        return d
    if "csw_slope" not in d and "whiff_slope" in d:
        d["csw_slope"] = d["whiff_slope"]
    if "csw_mean" not in d and "whiff_mean" in d:
        d["csw_mean"] = d["whiff_mean"]
    return d


def save_csw_xba_regression(reg, season=None, date_iso=None, metric="whiff"):
    """
    Persist a regression result to data/pitcher_cache/mlb/
    <metric>_xba_regression_<YYYYMMDD>.json so slope drift can be audited
    historically. Idempotent — overwrites if the same date is re-run.
    The `metric` arg selects the cache file ("whiff" default, or "csw") so
    the two metrics' daily fits never collide. The dict keys are
    csw_slope/csw_mean (generic "metric-1") regardless of metric.
    """
    if not reg:
        return None
    season = season or _current_season()
    if date_iso:
        date_str = date_iso.replace("-", "")
    else:
        date_str = date.today().strftime("%Y%m%d")
    out = {
        "date_iso":    date_iso or date.today().isoformat(),
        "season":      season,
        "metric":      metric,
        "n":           reg["n"],
        "csw_slope":   reg["csw_slope"],
        "xba_slope":   reg["xba_slope"],
        "csw_mean":    reg["csw_mean"],
        "xba_mean":    reg["xba_mean"],
        "r2":          reg["r2"],
        "saved_at":    datetime.now().isoformat(timespec="seconds"),
    }
    cache_path = CACHE_DIR / f"{_regression_cache_prefix(metric)}_{season}_{date_str}.json"
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(out, f, indent=2)
    except Exception as e:
        print(f"  [CSW/xBA cache] write failed: {e}")
        return None
    return cache_path


def load_csw_xba_regression_as_of(date_iso, season=None, metric="whiff"):
    """
    Walk-forward slope loader.

    Returns the most recent cached regression on or before date_iso, or
    None if no cache covers that range. Used by props_backfill to set
    each game-date's slopes from a snapshot fit only on prior data.
    `metric` selects the cache family ("whiff" default, or "csw").
    """
    import glob, re
    season = season or _current_season()
    target = date_iso.replace("-", "")
    prefix = _regression_cache_prefix(metric)
    pattern = str(CACHE_DIR / f"{prefix}_{season}_*.json")
    files = sorted(glob.glob(pattern))
    candidates = []
    for f in files:
        m = re.search(rf"{prefix}_{season}_(\d{{8}})\.json$", f)
        if m and m.group(1) <= target:
            candidates.append((m.group(1), f))
    if not candidates:
        return None
    candidates.sort()
    _, path = candidates[-1]
    try:
        with open(path) as f:
            return _normalize_reg_keys(json.load(f))
    except Exception:
        return None


def load_csw_xba_regression_for_date(date_iso, season=None, metric="whiff"):
    """
    Load the regression cache for an EXACT date (not "as of").

    Used by run_daily so slopes get locked once per day — re-runs later
    in the same day reuse the morning's slope instead of refitting on
    updated mid-day savant data (which would leak today's completed
    games into projections of today's later games).
    `metric` selects the cache family ("whiff" default, or "csw").

    Returns the dict or None if no file exists for that exact date.
    """
    season = season or _current_season()
    date_str = date_iso.replace("-", "")
    cache_path = CACHE_DIR / f"{_regression_cache_prefix(metric)}_{season}_{date_str}.json"
    if not cache_path.exists():
        return None
    try:
        with open(cache_path) as f:
            return _normalize_reg_keys(json.load(f))
    except Exception:
        return None


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

def _parse_pitcher_splits_response(raw):
    """Parse /people/{id}/stats?stats=statSplits response into vs_left/vs_right dict."""
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

            if ip_float > 0:
                split_data["k_per_9"] = round(split_data["k"] / ip_float * 9, 2)
                split_data["h_per_9"] = round(split_data["h"] / ip_float * 9, 2)
                split_data["bb_per_9"] = round(split_data["bb"] / ip_float * 9, 2)

            if split_code == "vl":
                result["vs_left"] = split_data
            elif split_code == "vr":
                result["vs_right"] = split_data
    return result


def fetch_pitcher_handedness_splits(pitcher_id, season=None, through_date=None):
    """
    Fetch pitcher splits vs left-handed and right-handed batters.
    Returns dict with vs_left and vs_right keys.

    If `through_date` is provided, fetches only games within a rolling
    RECENT_WINDOW_DAYS window ending at through_date (walk-forward mode).
    Falls back to season-to-through_date when either split has fewer than
    RECENT_MIN_BF_PER_SPLIT batters-faced.
    """
    season = season or _current_season()

    if through_date is None:
        # --- Live/default path: unchanged season-to-today behavior ---
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
        result = _parse_pitcher_splits_response(raw)
        _save_cache(cache_path, result)
        return result

    # --- Walk-forward path: date-keyed cache + rolling window + fallback ---
    thru_key = through_date.replace("-", "")
    cache_path = CACHE_DIR / f"handedness_{pitcher_id}_{season}_thru_{thru_key}.json"
    cached = _load_cache(cache_path, max_age_hours=None)
    if cached is not None:
        return cached

    start_date, end_date = _date_window(season, through_date)
    url = (
        f"{BASE_URL}/people/{pitcher_id}/stats"
        f"?stats=statSplits&group=pitching&sitCodes=vl,vr"
        f"&startDate={start_date}&endDate={end_date}"
    )
    try:
        raw = _fetch_json(url)
    except Exception:
        raw = {}
    time.sleep(0.3)
    result = _parse_pitcher_splits_response(raw)

    # Fallback: if either split is too small, refetch season-to-through_date
    vl_pa = (result.get("vs_left") or {}).get("pa", 0) or 0
    vr_pa = (result.get("vs_right") or {}).get("pa", 0) or 0
    if vl_pa < RECENT_MIN_BF_PER_SPLIT or vr_pa < RECENT_MIN_BF_PER_SPLIT:
        season_start = f"{season}-03-20"
        url2 = (
            f"{BASE_URL}/people/{pitcher_id}/stats"
            f"?stats=statSplits&group=pitching&sitCodes=vl,vr"
            f"&startDate={season_start}&endDate={end_date}"
        )
        try:
            raw2 = _fetch_json(url2)
            time.sleep(0.3)
            fallback = _parse_pitcher_splits_response(raw2)
            # Use fallback for any split that was undersized
            if vl_pa < RECENT_MIN_BF_PER_SPLIT and fallback.get("vs_left"):
                result["vs_left"] = fallback["vs_left"]
            if vr_pa < RECENT_MIN_BF_PER_SPLIT and fallback.get("vs_right"):
                result["vs_right"] = fallback["vs_right"]
        except Exception:
            pass

    _save_cache(cache_path, result)
    return result


def fetch_pitcher_handedness_splits_season(pitcher_id, season=None, through_date=None):
    """
    Season-to-through_date version (no rolling window) of pitcher
    handedness splits. Used alongside the rolling-45d version to enable
    blended recent+stable estimates in props_engine.

    Returns dict with vs_left and vs_right keys (same shape as the
    rolling fetcher).
    """
    season = season or _current_season()
    if through_date is None:
        # Live mode — same as the unrestricted fetch
        return fetch_pitcher_handedness_splits(pitcher_id, season=season)

    thru_key = through_date.replace("-", "")
    cache_path = CACHE_DIR / f"handedness_{pitcher_id}_{season}_season_thru_{thru_key}.json"
    cached = _load_cache(cache_path, max_age_hours=None)
    if cached is not None:
        return cached

    season_start = f"{season}-03-20"
    url = (
        f"{BASE_URL}/people/{pitcher_id}/stats"
        f"?stats=statSplits&group=pitching&sitCodes=vl,vr"
        f"&startDate={season_start}&endDate={through_date}"
    )
    try:
        raw = _fetch_json(url)
    except Exception:
        raw = {}
    time.sleep(0.3)
    result = _parse_pitcher_splits_response(raw)
    _save_cache(cache_path, result)
    return result


# ---------------------------------------------------------------------------
# 4b. Batter Strikeout Rates (season + vs LHP/RHP splits)
# ---------------------------------------------------------------------------

def _fetch_batter_k_rates_window(season, start_date, end_date):
    """Fetch per-batter K rates for a date range. Returns {pid: dict}."""
    result = {}

    # Overall hitting stats for the window
    url = (
        f"{BASE_URL}/stats?stats=byDateRange&group=hitting&sportId=1"
        f"&playerPool=all&limit=1000"
        f"&startDate={start_date}&endDate={end_date}"
    )
    try:
        raw = _fetch_json(url)
    except Exception as e:
        print(f"  [mlb_stats] Batter window fetch failed ({start_date}..{end_date}): {e}")
        return {}
    time.sleep(0.3)

    for stat_group in raw.get("stats", []):
        for split in stat_group.get("splits", []):
            stat = split.get("stat", {})
            player = split.get("player", {})
            pid = player.get("id")
            if not pid:
                continue

            pa = stat.get("plateAppearances", 0) or 0
            if pa == 0:
                pa = ((stat.get("atBats", 0) or 0)
                      + (stat.get("baseOnBalls", 0) or 0)
                      + (stat.get("hitByPitch", 0) or 0)
                      + (stat.get("sacFlies", 0) or 0)
                      + (stat.get("sacBunts", 0) or 0))
            k = stat.get("strikeOuts", 0) or 0
            pos = split.get("position", {}).get("abbreviation", "")
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

    # vs LHP/RHP splits via stats=statSplits. The league-wide statSplits
    # endpoint DOES honor startDate/endDate (verified against the API), so we
    # bound the splits to season-start..end_date (season-to-as-of-date) to
    # match the walk-forward window's end. Using `season=` instead pulled
    # FULL-season splits including games after end_date, which leaked future
    # batter performance into historical projections.
    _split_season_start = f"{season}-03-20"
    url2 = (
        f"{BASE_URL}/stats?stats=statSplits&group=hitting"
        f"&sportId=1&sitCodes=vl,vr&playerPool=all&limit=1000"
        f"&startDate={_split_season_start}&endDate={end_date}"
    )
    try:
        raw2 = _fetch_json(url2)
        time.sleep(0.3)
    except Exception:
        return result

    for stat_group in raw2.get("stats", []):
        for split in stat_group.get("splits", []):
            stat = split.get("stat", {})
            player = split.get("player", {})
            pid = player.get("id")
            split_code = split.get("split", {}).get("code", "")
            if not pid or pid not in result:
                continue
            pa = stat.get("plateAppearances", 0) or 0
            if pa == 0:
                pa = ((stat.get("atBats", 0) or 0)
                      + (stat.get("baseOnBalls", 0) or 0)
                      + (stat.get("hitByPitch", 0) or 0)
                      + (stat.get("sacFlies", 0) or 0)
                      + (stat.get("sacBunts", 0) or 0))
            k = stat.get("strikeOuts", 0) or 0
            if split_code == "vl":
                result[pid]["k_pct_vs_lhp"] = round(k / pa, 4) if pa > 0 else 0.0
                result[pid]["pa_vs_lhp"] = pa
            elif split_code == "vr":
                result[pid]["k_pct_vs_rhp"] = round(k / pa, 4) if pa > 0 else 0.0
                result[pid]["pa_vs_rhp"] = pa

    return result


def fetch_batter_k_rates(season=None, through_date=None):
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

    # --- Walk-forward path ---
    if through_date is not None:
        thru_key = through_date.replace("-", "")
        cache_path = CACHE_DIR / f"batter_k_rates_{season}_thru_{thru_key}.json"
        cached = _load_cache(cache_path, max_age_hours=None)
        if cached is not None:
            return {int(k): v for k, v in cached.items()}

        start_date, end_date = _date_window(season, through_date)
        window = _fetch_batter_k_rates_window(season, start_date, end_date)

        # Per-batter fallback: refetch season-to-through_date, replace
        # entries where window PA is too small.
        season_start = f"{season}-03-20"
        fallback = _fetch_batter_k_rates_window(season, season_start, end_date)

        result = {}
        # Union of batters seen in either window or fallback
        all_pids = set(window.keys()) | set(fallback.keys())
        for pid in all_pids:
            w = window.get(pid)
            f_entry = fallback.get(pid)
            if w and w.get("pa", 0) >= RECENT_MIN_BATTER_PA:
                result[pid] = w
            elif f_entry:
                # Use season-to-through_date for under-sampled batters
                result[pid] = f_entry
            elif w:
                result[pid] = w

        print(f"  [mlb_stats] Fetched K rates (walk-forward thru {through_date}) "
              f"for {len(result)} batters")
        _save_cache(cache_path, result)
        return result

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


def fetch_batter_career_k_rates(season=None, n_prior_seasons=2):
    """
    Per-batter CAREER handed K% built from the `n_prior_seasons` COMPLETED
    seasons before `season` (default: 2025+2024 for a 2026 run).

    Leak-free by construction: only prior, finished seasons are aggregated, so
    the same dict is valid for every date in the current-season backtest and is
    never contaminated by in-season games. Used as the tier-3 fallback in
    compute_lineup_k_pct — when a batter's in-season sample is too thin to
    trust, fall back to their career handed rate rather than a noisy 5-PA blip.

    Returns {player_id (int): {
        "name", "k_pct_vs_lhp", "pa_vs_lhp", "k_pct_vs_rhp", "pa_vs_rhp"}}.
    Batters with no prior-season MLB data (true rookies) are simply absent.
    """
    season = season or _current_season()
    years = [season - 1 - i for i in range(max(1, n_prior_seasons))]
    cache_path = CACHE_DIR / f"batter_career_k_rates_thru_{years[0]}.json"
    cached = _load_cache(cache_path, max_age_hours=None)
    if cached is not None:
        return {int(k): v for k, v in cached.items()}

    agg = {}  # pid -> {name, k_l, pa_l, k_r, pa_r}
    for yr in years:
        url = (
            f"{BASE_URL}/stats?stats=statSplits&group=hitting&sportId=1"
            f"&season={yr}&sitCodes=vl,vr&playerPool=all&limit=2000"
        )
        try:
            raw = _fetch_json(url)
            time.sleep(0.3)
        except Exception as e:
            print(f"  [mlb_stats] career splits {yr} fetch failed: {e}")
            continue
        for stat_group in raw.get("stats", []):
            for split in stat_group.get("splits", []):
                stat = split.get("stat", {})
                player = split.get("player", {})
                pid = player.get("id")
                code = split.get("split", {}).get("code", "")
                if not pid or code not in ("vl", "vr"):
                    continue
                pa = stat.get("plateAppearances", 0) or 0
                if pa == 0:
                    pa = ((stat.get("atBats", 0) or 0)
                          + (stat.get("baseOnBalls", 0) or 0)
                          + (stat.get("hitByPitch", 0) or 0)
                          + (stat.get("sacFlies", 0) or 0)
                          + (stat.get("sacBunts", 0) or 0))
                k = stat.get("strikeOuts", 0) or 0
                e = agg.setdefault(pid, {
                    "name": player.get("fullName", ""),
                    "k_l": 0, "pa_l": 0, "k_r": 0, "pa_r": 0})
                if code == "vl":
                    e["k_l"] += k
                    e["pa_l"] += pa
                else:
                    e["k_r"] += k
                    e["pa_r"] += pa

    result = {}
    for pid, e in agg.items():
        result[pid] = {
            "name": e["name"],
            "k_pct_vs_lhp": round(e["k_l"] / e["pa_l"], 4) if e["pa_l"] else 0.0,
            "pa_vs_lhp": e["pa_l"],
            "k_pct_vs_rhp": round(e["k_r"] / e["pa_r"], 4) if e["pa_r"] else 0.0,
            "pa_vs_rhp": e["pa_r"],
        }

    print(f"  [mlb_stats] Fetched career K rates ({'+'.join(map(str, years))}) "
          f"for {len(result)} batters")
    _save_cache(cache_path, result)
    return result


LINEUP_HAND_PA_GATE = 75
# Min career PA vs a hand to trust the career tier-3 fallback.
CAREER_HAND_PA_MIN = 50


def compute_lineup_k_pct(lineup_player_ids, batter_k_rates, pitcher_hand="R",
                          slot_weights=None, career_k_rates=None,
                          career_min_season_pa=0, career_extreme_kpct=0.0,
                          career_shrink_c=0.0):
    """
    Compute lineup-specific K% from the actual batting order.

    Returns simple-mean and PA-weighted variants. (Pairwise-hand modes were
    swept 2026-05-20 and discarded; removed from harness.)

    Per-batter K% fallback chain (best -> worst):
      1. in-season vs-hand K%   — if season PA vs that hand >= LINEUP_HAND_PA_GATE
      2. in-season overall K%   — if season overall PA >= career_min_season_pa
      3. career vs-hand K%       — when in-season sample is too thin (tier-2 PA
                                   below career_min_season_pa) AND career has a
                                   trustworthy handed sample (>= CAREER_HAND_PA_MIN)
      4. in-season overall K%   — anything left (e.g. true rookies w/ no career)

    career_extreme_kpct: if > 0, tier-3 fires ONLY when the thin in-season rate
    is also an implausible outlier (overall >= this, e.g. 0.45). This targets
    pathological small-sample blips (5 K / 5 PA = 100%) without churning normal
    thin samples — those stay on tier 2.

    career_shrink_c: if > 0, tier-3 does empirical-Bayes shrinkage of the thin
    in-season rate TOWARD the career handed rate instead of a hard swap:
        k_est = (k_season + career_rate * C) / (pa_season + C)
    C is the prior strength in pseudo-PA. 0 = hard replace with career rate.

    Tier 3 is OFF when career_min_season_pa <= 0 or career_k_rates is None, so
    the legacy 2-tier behavior is preserved exactly.
    """
    hand_key = "k_pct_vs_lhp" if pitcher_hand == "L" else "k_pct_vs_rhp"
    pa_key = "pa_vs_lhp" if pitcher_hand == "L" else "pa_vs_rhp"
    use_career = bool(career_k_rates) and (career_min_season_pa > 0
                                           or career_extreme_kpct > 0)
    # Ungated continuous empirical-Bayes mode: when C>0 with no PA/extreme gate,
    # shrink EVERY batter toward career, weighted by in-season sample size.
    continuous = (career_shrink_c > 0 and bool(career_k_rates)
                  and career_min_season_pa <= 0 and career_extreme_kpct <= 0)

    per_slot_overall = []
    per_slot_vs_hand = []

    for pid in lineup_player_ids:
        batter = batter_k_rates.get(pid) or batter_k_rates.get(str(pid))
        if not batter:
            per_slot_overall.append(None)
            per_slot_vs_hand.append(None)
            continue

        overall = batter.get("k_pct", 0) or 0
        overall_pa = batter.get("pa", 0) or 0
        per_slot_overall.append(overall if overall > 0 else None)

        vs_hand = batter.get(hand_key, 0) or 0
        pa_vs = batter.get(pa_key, 0) or 0

        if continuous:
            # Best in-season signal + its PA (vs-hand if trustworthy, else overall)
            if pa_vs >= LINEUP_HAND_PA_GATE and vs_hand > 0:
                rate_in, pa_in = vs_hand, pa_vs
            else:
                rate_in, pa_in = overall, overall_pa
            cb = career_k_rates.get(pid) or career_k_rates.get(str(pid))
            c_rate = (cb.get(hand_key, 0) or 0) if cb else 0
            c_pa = (cb.get(pa_key, 0) or 0) if cb else 0
            if cb and c_pa >= CAREER_HAND_PA_MIN and c_rate > 0:
                # k_est = (k_season + career*C) / (pa_season + C)
                batter_vs_ph = ((rate_in * pa_in + c_rate * career_shrink_c)
                                / (pa_in + career_shrink_c))
            else:
                batter_vs_ph = rate_in                  # no usable career prior
            per_slot_vs_hand.append(batter_vs_ph if batter_vs_ph > 0 else None)
            continue

        # --- legacy gated tier logic (C handling inside tier 3) ---
        # tier-3 fires when the in-season sample is thin (PA cutoff, if set) AND
        # the rate is an implausible outlier (extreme gate, if set). With no PA
        # cutoff, the extreme gate alone decides; with no extreme gate, the PA
        # cutoff alone decides. Both default-off => legacy 2-tier behavior.
        pa_ok = (career_min_season_pa <= 0) or (overall_pa < career_min_season_pa)
        extreme_ok = (career_extreme_kpct <= 0) or (overall >= career_extreme_kpct)
        fire = use_career and pa_ok and extreme_ok
        if pa_vs >= LINEUP_HAND_PA_GATE and vs_hand > 0:
            batter_vs_ph = vs_hand                      # tier 1
        elif not fire:
            batter_vs_ph = overall                      # tier 2
        else:
            # tier 3: in-season sample too thin (and extreme) — career handed
            cb = career_k_rates.get(pid) or career_k_rates.get(str(pid))
            c_rate = (cb.get(hand_key, 0) or 0) if cb else 0
            c_pa = (cb.get(pa_key, 0) or 0) if cb else 0
            if cb and c_pa >= CAREER_HAND_PA_MIN and c_rate > 0:
                if career_shrink_c > 0:
                    # empirical Bayes: blend thin in-season toward career prior
                    k_season = overall * overall_pa
                    batter_vs_ph = ((k_season + c_rate * career_shrink_c)
                                    / (overall_pa + career_shrink_c))
                else:
                    batter_vs_ph = c_rate               # hard replace
            else:
                batter_vs_ph = overall                  # tier 4 (rookies, etc.)
        per_slot_vs_hand.append(batter_vs_ph if batter_vs_ph > 0 else None)

    def _mean(vals):
        clean = [v for v in vals if v is not None and v > 0]
        return (sum(clean) / len(clean)) if clean else 0.0

    def _weighted_mean(vals, weights):
        if not weights or len(weights) != len(vals):
            return _mean(vals)
        num, denom = 0.0, 0.0
        for v, w in zip(vals, weights):
            if v is None or v <= 0:
                continue
            num += v * w
            denom += w
        return (num / denom) if denom > 0 else 0.0

    n_batters = sum(1 for v in per_slot_overall if v is not None and v > 0)

    return {
        "lineup_k_pct": round(_mean(per_slot_overall), 4),
        "lineup_k_pct_vs_hand": round(_mean(per_slot_vs_hand), 4),
        "lineup_k_pct_vs_hand_pa_weighted":
            round(_weighted_mean(per_slot_vs_hand, slot_weights), 4),
        "n_batters": n_batters,
    }


# ---------------------------------------------------------------------------
# 5. Team Batting Stats
# ---------------------------------------------------------------------------

def _parse_team_batting_response(raw):
    """Parse team batting stats response into {abbr: {...}} dict."""
    result = {}
    for stat_group in raw.get("stats", []):
        for split in stat_group.get("splits", []):
            stat = split.get("stat", {})
            team_info = split.get("team", {})
            team_id = team_info.get("id")
            abbr = MLB_TEAM_ID_TO_ABBR.get(team_id, team_info.get("abbreviation", ""))
            if not abbr:
                continue

            pa = stat.get("plateAppearances", 0) or 0
            k = stat.get("strikeOuts", 0) or 0
            bb = stat.get("baseOnBalls", 0) or 0

            result[abbr] = {
                "K_PCT": round(k / pa, 4) if pa else 0,
                "BA": _safe_float(stat.get("avg", 0) or 0),
                "OPS": _safe_float(stat.get("ops", 0) or 0),
                "BB_PCT": round(bb / pa, 4) if pa else 0,
                "PA": pa,
            }
    return result


def fetch_team_batting_stats(season=None, through_date=None):
    """
    Fetch team-level batting stats.
    Returns dict keyed by team abbreviation.

    If `through_date` is provided, fetches only games within a rolling
    RECENT_WINDOW_DAYS window ending at through_date (walk-forward mode).
    Falls back to season-to-through_date when avg team PA < RECENT_MIN_TEAM_PA.
    """
    season = season or _current_season()

    if through_date is None:
        # --- Live/default path ---
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
        result = _parse_team_batting_response(raw)
        _save_cache(cache_path, result)
        return result

    # --- Walk-forward path ---
    thru_key = through_date.replace("-", "")
    cache_path = CACHE_DIR / f"team_batting_{season}_thru_{thru_key}.json"
    cached = _load_cache(cache_path, max_age_hours=None)
    if cached is not None:
        return cached

    start_date, end_date = _date_window(season, through_date)
    url = (
        f"{BASE_URL}/teams/stats?stats=byDateRange&group=hitting&sportId=1"
        f"&startDate={start_date}&endDate={end_date}"
    )
    try:
        raw = _fetch_json(url)
    except Exception:
        raw = {}
    time.sleep(0.3)
    result = _parse_team_batting_response(raw)

    # Fallback: if avg team PA is too small, use season-to-through_date
    if result:
        avg_pa = sum(v.get("PA", 0) for v in result.values()) / max(1, len(result))
    else:
        avg_pa = 0
    if avg_pa < RECENT_MIN_TEAM_PA:
        season_start = f"{season}-03-20"
        url2 = (
            f"{BASE_URL}/teams/stats?stats=byDateRange&group=hitting&sportId=1"
            f"&startDate={season_start}&endDate={end_date}"
        )
        try:
            raw2 = _fetch_json(url2)
            time.sleep(0.3)
            fallback = _parse_team_batting_response(raw2)
            if fallback:
                result = fallback
        except Exception:
            pass

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

    from datetime import date as dt_date_check
    is_today = date_str >= dt_date_check.today().strftime("%Y-%m-%d")
    # Always refetch for today (starter can change); past dates never expire
    cached = _load_cache(cache_path, max_age_hours=0 if is_today else None)
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


def build_team_opp_hand_by_date(pitcher_logs, pitch_hands):
    """
    Build a {date_str: {team_abbr: 'L'|'R'}} lookup of which-handed starter
    each team FACED on each date this season. Used by
    get_recent_batting_order to prefer same-hand lineups when projecting
    tonight's batting order against a same-hand starter.

    Parameters
    ----------
    pitcher_logs : dict
        Output of organize_pitcher_logs — {pitcher_id: [game_log, ...]}.
        Each game log needs `game_date`, `opp`, `is_start`.
    pitch_hands : dict
        {pitcher_id: 'L'|'R'} from load_pitch_hands.

    Returns
    -------
    dict
        {date_str: {team_abbr: hand}}  — the team's opposing starter's hand.
    """
    result = {}
    for pid, games in (pitcher_logs or {}).items():
        try:
            hand = pitch_hands.get(int(pid))
        except (TypeError, ValueError):
            hand = pitch_hands.get(pid)
        if hand not in ("L", "R"):
            continue
        for g in games:
            if not g.get("is_start"):
                continue
            d = g.get("game_date", "")
            opp = (g.get("opp", "") or "").upper()
            if d and opp:
                result.setdefault(d, {})[opp] = hand
    return result


def get_recent_batting_order(team_abbr, season=None, before_date=None,
                             max_lookback_days=14,
                             vs_hand=None, opp_starter_hand_by_date=None):
    """
    Return the most recent batting order (list of player IDs) for a team prior
    to `before_date`. Used as a fallback when today's lineup hasn't been posted.

    When ``vs_hand`` and ``opp_starter_hand_by_date`` are both provided, makes
    a first pass preferring batting orders from games where the team faced a
    same-handed starter as tonight's pitcher (teams platoon, so vs-LHP and
    vs-RHP lineups can differ by 2-3 spots). Falls back to any-hand match if
    no same-hand lineup exists within the lookback window — preserves the
    original behavior as a safety net.

    Parameters
    ----------
    team_abbr : str
        Team abbreviation (e.g., "NYM").
    season : int or None
    before_date : str or None
        ISO date "YYYY-MM-DD". Looks for batting orders before this date.
    max_lookback_days : int
        Max days back to search.
    vs_hand : str or None
        Tonight's starter's throwing hand ("L" or "R"). When set with
        ``opp_starter_hand_by_date``, enables same-hand preferential matching.
    opp_starter_hand_by_date : dict or None
        Output of ``build_team_opp_hand_by_date``.

    Returns
    -------
    list of int or None
        Most recent batting order, or None if not found.
    """
    season = season or _current_season()
    bo_cache_path = CACHE_DIR / f"batting_orders_{season}.json"
    bo_data = _load_cache(bo_cache_path, max_age_hours=None)
    if not bo_data:
        return None
    from datetime import date as dt_date, timedelta, datetime as dt_dt
    if before_date:
        ref = dt_dt.strptime(before_date, "%Y-%m-%d").date()
    else:
        ref = dt_date.today()

    can_filter = vs_hand in ("L", "R") and bool(opp_starter_hand_by_date)

    # Pass 1: prefer same-hand match
    if can_filter:
        for i in range(1, max_lookback_days + 1):
            d = (ref - timedelta(days=i)).strftime("%Y-%m-%d")
            order = bo_data.get(d, {}).get(team_abbr)
            if not order:
                continue
            faced_hand = (opp_starter_hand_by_date.get(d, {}) or {}).get(team_abbr)
            if faced_hand == vs_hand:
                return order

    # Pass 2: any-hand fallback (original behavior)
    for i in range(1, max_lookback_days + 1):
        d = (ref - timedelta(days=i)).strftime("%Y-%m-%d")
        order = bo_data.get(d, {}).get(team_abbr)
        if order:
            return order
    return None


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
    # Past dates: never expire (return cached)
    # Today: always refetch, BUT merge with existing cache so partial fetches
    # (e.g., early-morning runs before all teams post lineups) accumulate
    # rather than overwriting fuller data from a later run.
    if not is_today:
        cached = _load_cache(cache_path, max_age_hours=None)
        if cached is not None:
            return cached
        existing = {}
    else:
        existing = _load_cache(cache_path, max_age_hours=None) or {}

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
                        # Fallback: use team's most recent batting order
                        recent = get_recent_batting_order(abbr, season=season, before_date=date_str)
                        if recent:
                            pct = _compute_pct_lhb(recent, bat_sides)
                            result[abbr] = {
                                "PCT_LHB": pct,
                                "n_batters": len(recent),
                                "source": "recent_lineup_fallback",
                            }
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

    # Merge with existing cache: prefer fresh results, keep cached teams
    # not refetched this run (so partial fetches accumulate across the day).
    # For past dates `existing` is {} so this is a no-op replace.
    # For today: once a team is "confirmed" (source=lineup with >=9 batters),
    # freeze it — don't let a later refetch overwrite with altered data.
    merged = dict(existing)
    for abbr, fresh in result.items():
        prev = merged.get(abbr, {})
        prev_confirmed = (prev.get("source") == "lineup"
                          and prev.get("n_batters", 0) >= 9)
        if is_today and prev_confirmed:
            continue  # frozen
        merged[abbr] = fresh
    if merged:
        _save_cache(cache_path, merged)
    return merged


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
