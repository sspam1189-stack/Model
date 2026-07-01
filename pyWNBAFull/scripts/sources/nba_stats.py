# scripts/sources/nba_stats.py  (pyWNBAFull)
# Fetches WNBA team advanced stats — full season, last-N window, home/away splits.
#
# WNBA data layer swap vs pyFull/:
#   - Uses nba_api (LeagueDashTeamStats) with league_id_nullable='10' (WNBA).
#     Verified 2026-07-01: direct requests to stats.wnba.com / stats.nba.com
#     time out from runners, but nba_api's LeagueDashTeamStats returns WNBA rows
#     (13 teams in 2025, all Advanced fields present). So we go through nba_api.
#   - Season strings are a single calendar year ('2026'), NOT '2025-26'.
#   - Team count is ~13 (2025) / ~15 (2026 post-expansion), so the completeness
#     floor is 10 teams, not 20.

import time
import datetime
import math

# nba_api uses its own bundled http headers/host that succeed where a raw
# requests call to stats.wnba.com is blocked.
from nba_api.stats.endpoints import leaguedashteamstats as _ldts

WNBA_LEAGUE_ID = "10"

# Minimum teams for a "complete" WNBA slate. NBA used 20 (30-team league);
# WNBA has 13 (2025) / 15 (2026). 10 keeps us safe against 1-2 missing teams.
MIN_TEAMS = 10


def current_season():
    """WNBA season string == the current calendar year (single-year, May–Sept).

    Dropped the NBA October-crossing logic ('2025-26'); WNBA seasons are a plain
    year. During the ~May–Sept season the season year is simply the current year.
    """
    return str(datetime.datetime.now().year)


def _season_for_date(date_to):
    """Season year for a given YYYY-MM-DD / YYYYMMDD date (defaults to now)."""
    if not date_to:
        return current_season()
    s = str(date_to).replace("-", "")
    if len(s) >= 4:
        return s[:4]
    return current_season()


def to_nba_date(date_str):
    """Convert YYYY-MM-DD or YYYYMMDD -> MM/DD/YYYY for the stats API."""
    if not date_str:
        return ""
    s = str(date_str).replace("-", "")
    if len(s) == 8:
        return f"{s[4:6]}/{s[6:8]}/{s[0:4]}"
    return date_str


def _safe_float(val):
    try:
        return float(val) if val is not None else 0.0
    except (ValueError, TypeError):
        return 0.0


def _fetch_team_stats(date_to=None, date_from=None, last_n_games=0, location="", season_type="Regular Season"):
    """Core fetch via nba_api LeagueDashTeamStats with LeagueID='10' (WNBA)."""
    season = _season_for_date(date_to)

    kwargs = dict(
        league_id_nullable=WNBA_LEAGUE_ID,
        season=season,
        season_type_all_star=season_type,
        measure_type_detailed_defense="Advanced",
        per_mode_detailed="PerGame",
        pace_adjust="N",
        plus_minus="N",
        rank="N",
        month=0,
        opponent_team_id=0,
        period=0,
        last_n_games=last_n_games,
        timeout=60,
    )
    if date_to:
        kwargs["date_to_nullable"] = to_nba_date(date_to)
    if date_from:
        kwargs["date_from_nullable"] = to_nba_date(date_from)
    if location:
        kwargs["location_nullable"] = location  # "Home" or "Road"

    last_err = None
    df = None
    for attempt in range(5):
        try:
            ep = _ldts.LeagueDashTeamStats(**kwargs)
            df = ep.get_data_frames()[0]
            break
        except Exception as e:
            last_err = e
            wait = 3 * (2 ** attempt)
            print(f"  [wnba_stats] fetch attempt {attempt + 1}/5 failed ({e}); retrying in {wait}s...")
            time.sleep(wait)
    if df is None:
        raise Exception(f"WNBA team stats fetch failed after 5 attempts: {last_err}")

    cols = list(df.columns)

    def has(*names):
        return all(n in cols for n in names)

    if not has("TEAM_NAME", "GP", "OFF_RATING", "DEF_RATING", "TS_PCT", "TM_TOV_PCT", "OREB_PCT", "PACE"):
        raise Exception(f"WNBA stats: missing expected columns; got {cols}")

    stats = {}
    for _, row in df.iterrows():
        team_name = str(row["TEAM_NAME"] or "")
        stats[team_name] = {
            "OFF": _safe_float(row["OFF_RATING"]),
            "DEF": _safe_float(row["DEF_RATING"]),
            "TS": _safe_float(row["TS_PCT"]),
            "TO": _safe_float(row["TM_TOV_PCT"]),
            "ORR": _safe_float(row["OREB_PCT"]),
            "PACE": _safe_float(row["PACE"]),
            "GP": _safe_float(row["GP"]),
        }
    return stats


# -- Public: season stats (backward compatible) --

def fetch_nba_stats(date_to=None, date_from=None, season_type="Regular Season"):
    if date_from:
        range_str = f"{to_nba_date(date_from)} -> {to_nba_date(date_to) if date_to else 'today'}"
    elif date_to:
        range_str = f"up to {to_nba_date(date_to)}"
    else:
        range_str = "full season"
    print(f"  [wnba_stats] Fetching stats ({range_str}) [{season_type}]...")

    stats = _fetch_team_stats(date_to, date_from, season_type=season_type)
    count = len(stats)
    skipped = sum(1 for s in stats.values() if s["GP"] < 5)
    print(f"  [wnba_stats] Got {count} teams ({skipped} with < 5 games)")

    # WNBA plays a short season; require only 5 games (NBA used 10).
    stats = {k: v for k, v in stats.items() if v["GP"] >= 5}

    if len(stats) < MIN_TEAMS:
        raise Exception(f"WNBA stats incomplete: only {len(stats)} teams")

    return stats


# -- Enhanced stats (season + last10 + home + away) --

def fetch_nba_stats_enhanced(date_to=None, season_type="Regular Season"):
    """
    Returns { "season": ..., "last10": ..., "home": ..., "away": ... }
    all keyed by full team name.

    WNBA has no separate playoff-blend path (short playoffs, thin samples);
    we always fetch the requested season_type. Regular Season is the default
    and covers the vast majority of the schedule.
    """
    print(f"  [wnba_stats] Fetching enhanced stats (season + last10 + home + away) [{season_type}]...")

    season = _fetch_team_stats(date_to, None, season_type=season_type)
    time.sleep(1.5)

    last10 = None
    home = None
    away = None

    try:
        # LastNGames counts from today, not DateTo — useless for historical dates.
        # When date_to is given, use a ~20-day DateFrom window instead (WNBA plays
        # ~3-4 games/wk/team, so ~20 days ≈ last ~8-10 games).
        if date_to:
            s = str(date_to).replace("-", "")
            dt = datetime.datetime(int(s[:4]), int(s[4:6]), int(s[6:8]))
            from_dt = dt - datetime.timedelta(days=20)
            date_from = from_dt.strftime("%Y-%m-%d")
            last10 = _fetch_team_stats(date_to, date_from, season_type=season_type)
        else:
            last10 = _fetch_team_stats(date_to, None, last_n_games=10, season_type=season_type)
        print(f"  [wnba_stats] Last 10 (rolling window): {len(last10)} teams")
        time.sleep(1.5)
    except Exception as e:
        print(f"  [wnba_stats] Last 10 fetch failed ({e}) -- skipping")
        last10 = None

    try:
        home = _fetch_team_stats(date_to, None, location="Home", season_type=season_type)
        print(f"  [wnba_stats] Home splits: {len(home)} teams")
        time.sleep(1.5)
    except Exception as e:
        print(f"  [wnba_stats] Home fetch failed ({e}) -- skipping")
        home = None

    try:
        away = _fetch_team_stats(date_to, None, location="Road", season_type=season_type)
        print(f"  [wnba_stats] Away splits: {len(away)} teams")
    except Exception as e:
        print(f"  [wnba_stats] Away fetch failed ({e}) -- skipping")
        away = None

    season = {k: v for k, v in season.items() if v.get("GP", 0) >= 5}
    count = len(season)
    print(f"  [wnba_stats] Enhanced: {count} teams (season), {len(last10) if last10 else 0} (L10), {len(home) if home else 0} (home), {len(away) if away else 0} (away)")

    if count < MIN_TEAMS:
        raise Exception(f"WNBA stats incomplete: only {count} teams")

    return {"season": season, "last10": last10, "home": home, "away": away}
