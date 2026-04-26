# scripts/sources/nba_stats.py
# Fetches NBA.com team stats -- full season, last N games, home/away splits.
#
# Uses nba_api package instead of raw requests to stats.nba.com.
# nba_api handles headers, cookies, and retries automatically.
#
# Usage:
#   fetch_nba_stats("2026-01-30")              -> season stats as of Jan 30
#   fetch_nba_stats()                           -> season stats as of today
#   fetch_nba_stats_enhanced("20260301")        -> { season, last10, home, away }

import time
import datetime
import math


def current_season():
    """Auto-detect current NBA season string (e.g. '2025-26')."""
    now = datetime.datetime.now()
    year = now.year
    month = now.month
    start_year = year if month >= 10 else year - 1
    return f"{start_year}-{str(start_year + 1)[2:]}"


def to_nba_date(date_str):
    """Convert YYYY-MM-DD or YYYYMMDD -> MM/DD/YYYY for NBA.com API."""
    if not date_str:
        return ""
    s = str(date_str).replace("-", "")
    if len(s) == 8:
        return f"{s[4:6]}/{s[6:8]}/{s[0:4]}"
    return date_str


def _fetch_team_stats(date_to=None, date_from=None, last_n_games=0, location="", season_type="Regular Season"):
    """Core fetch -- uses nba_api LeagueDashTeamStats endpoint."""
    from nba_api.stats.endpoints import leaguedashteamstats

    season = current_season()
    date_to_param = to_nba_date(date_to) if date_to else None
    date_from_param = to_nba_date(date_from) if date_from else None

    # Map location param: "" -> None, "Home" -> "Home", "Road" -> "Road"
    location_param = location if location else None

    last_err = None
    df = None
    for attempt in range(5):
        try:
            endpoint = leaguedashteamstats.LeagueDashTeamStats(
                season=season,
                season_type_all_star=season_type,
                measure_type_detailed_defense="Advanced",
                per_mode_detailed="PerGame",
                date_to_nullable=date_to_param,
                date_from_nullable=date_from_param,
                last_n_games=last_n_games,
                location_nullable=location_param,
                timeout=120,
            )
            df = endpoint.get_data_frames()[0]
            break
        except Exception as e:
            last_err = e
            wait = 3 * (2 ** attempt)
            print(f"  [nba_stats] fetch attempt {attempt + 1}/5 failed ({e}); retrying in {wait}s...")
            time.sleep(wait)
    if df is None:
        raise Exception(f"nba_api team stats fetch failed after 5 attempts: {last_err}")

    if df.empty:
        raise Exception("nba_api: no data returned")

    stats = {}
    for _, row in df.iterrows():
        team_name = str(row.get("TEAM_NAME") or "")
        gp = float(row.get("GP", 0))
        stats[team_name] = {
            "OFF": _safe_float(row.get("OFF_RATING")),
            "DEF": _safe_float(row.get("DEF_RATING")),
            "TS": _safe_float(row.get("TS_PCT")),
            "TO": _safe_float(row.get("TM_TOV_PCT")),
            "ORR": _safe_float(row.get("OREB_PCT")),
            "PACE": _safe_float(row.get("PACE")),
            "GP": gp,
        }
    return stats


def _safe_float(val):
    """Convert value to float, defaulting to 0.0."""
    try:
        return float(val) if val is not None else 0.0
    except (ValueError, TypeError):
        return 0.0


# -- Public: season stats (backward compatible) --

def fetch_nba_stats(date_to=None, date_from=None, season_type="Regular Season"):
    if date_from:
        range_str = f"{to_nba_date(date_from)} -> {to_nba_date(date_to) if date_to else 'today'}"
    elif date_to:
        range_str = f"up to {to_nba_date(date_to)}"
    else:
        range_str = "full season"
    print(f"  [nba_stats] Fetching stats ({range_str}) [{season_type}]...")

    stats = _fetch_team_stats(date_to, date_from, season_type=season_type)
    count = len(stats)
    skipped = sum(1 for s in stats.values() if s["GP"] < 10)
    print(f"  [nba_stats] Got {count} teams ({skipped} with < 10 games)")

    # Filter out teams with too few games
    stats = {k: v for k, v in stats.items() if v["GP"] >= 10}

    if len(stats) < 20:
        raise Exception(f"NBA.com stats incomplete: only {len(stats)} teams")

    return stats


# -- Blend playoff + regular season stats per team --

STAT_KEYS = ["OFF", "DEF", "TS", "TO", "ORR", "PACE"]
PLAYOFF_RAMP_GAMES = 16  # full playoff weight after this many games


def _blend_playoff_stats(reg_season, playoff_stats):
    if not playoff_stats or not len(playoff_stats):
        return reg_season

    blended = {}
    blend_count = 0

    for team, rs in reg_season.items():
        po = playoff_stats.get(team)
        if not po or not po.get("GP") or po["GP"] < 1:
            blended[team] = dict(rs)
            continue

        po_weight = min(po["GP"] / PLAYOFF_RAMP_GAMES, 1.0)
        out = dict(rs)
        for k in STAT_KEYS:
            po_val = po.get(k)
            rs_val = rs.get(k)
            if isinstance(po_val, (int, float)) and math.isfinite(po_val) and isinstance(rs_val, (int, float)) and math.isfinite(rs_val):
                out[k] = rs_val * (1 - po_weight) + po_val * po_weight
        out["GP"] = rs["GP"]  # keep regular season GP for min-games checks
        blended[team] = out
        blend_count += 1

    if blend_count > 0:
        print(f"  [nba_stats] Blended playoff+regular for {blend_count} teams")
    return blended


# -- Public: enhanced stats (season + last10 + home + away) --

def fetch_nba_stats_enhanced(date_to=None, season_type="Regular Season"):
    """
    Returns { "season": ..., "last10": ..., "home": ..., "away": ... }
    all keyed by full team name.
    In playoffs: fetches both regular season + playoff stats and blends them.
    """
    in_playoffs = season_type == "Playoffs"

    print(f"  [nba_stats] Fetching enhanced stats (season + last10 + home + away) [{season_type}]...")

    # In playoffs: fetch regular season as base, then overlay playoff stats
    reg_season = None
    if in_playoffs:
        reg_season = _fetch_team_stats(date_to, None, season_type="Regular Season")
        print(f"  [nba_stats] Regular season base: {len(reg_season)} teams")
        time.sleep(2)

    try:
        raw_season = _fetch_team_stats(date_to, None, season_type=season_type)
    except Exception as e:
        if in_playoffs:
            print(f"  [nba_stats] Playoffs stats empty (playoffs just started?): {e}. Using regular season as fallback.")
            raw_season = reg_season
        else:
            raise
    season = _blend_playoff_stats(reg_season, raw_season) if in_playoffs else raw_season
    time.sleep(2)

    last10 = None
    home = None
    away = None

    try:
        # In playoffs, use regular season L10 (more stable)
        l10_type = "Regular Season" if in_playoffs else season_type
        # LastNGames counts from TODAY, not DateTo -- useless for historical dates.
        # Fix: when date_to is provided, use a DateFrom window (~25 days back) instead.
        if date_to:
            s = str(date_to).replace("-", "")
            dt = datetime.datetime(int(s[:4]), int(s[4:6]), int(s[6:8]))
            from_dt = dt - datetime.timedelta(days=25)
            date_from = from_dt.strftime("%Y-%m-%d")
            last10 = _fetch_team_stats(date_to, date_from, season_type=l10_type)
        else:
            last10 = _fetch_team_stats(date_to, None, last_n_games=10, season_type=l10_type)
        print(f"  [nba_stats] Last 10: {len(last10)} teams")
        time.sleep(2)
    except Exception as e:
        print(f"  [nba_stats] Last 10 fetch failed ({e}) -- skipping")
        last10 = None

    try:
        split_type = "Regular Season" if in_playoffs else season_type
        home = _fetch_team_stats(date_to, None, location="Home", season_type=split_type)
        print(f"  [nba_stats] Home splits: {len(home)} teams")
        time.sleep(2)
    except Exception as e:
        print(f"  [nba_stats] Home fetch failed ({e}) -- skipping")
        home = None

    try:
        split_type = "Regular Season" if in_playoffs else season_type
        away = _fetch_team_stats(date_to, None, location="Road", season_type=split_type)
        print(f"  [nba_stats] Away splits: {len(away)} teams")
    except Exception as e:
        print(f"  [nba_stats] Away fetch failed ({e}) -- skipping")
        away = None

    # Filter season stats for min games (backward compat)
    season = {k: v for k, v in season.items() if v["GP"] >= 10}
    count = len(season)
    print(f"  [nba_stats] Enhanced: {count} teams (season), {len(last10) if last10 else 0} (L10), {len(home) if home else 0} (home), {len(away) if away else 0} (away)")

    if count < 20:
        raise Exception(f"NBA.com stats incomplete: only {count} teams")

    return {"season": season, "last10": last10, "home": home, "away": away}
