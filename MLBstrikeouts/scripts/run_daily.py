#!/usr/bin/env python3
"""
MLBstrikeouts/scripts/run_daily.py
Daily MLB pitcher strikeouts projection pipeline with Kalman filtering.

Stages:
  0. Grade previous picks
  1. Load Kalman state (or initialize if first run)
  2. Fetch pitcher game logs from MLB Stats API
  3. Update Kalman state with any new games
  4. Fetch pitcher advanced stats (K/9, BB/9, WHIP)
  5. Fetch pitcher sabermetrics (FIP, xFIP)
  6. Fetch probable pitchers
  7. Fetch handedness splits for probable starters
  8. Fetch team batting stats
  9. Fetch lineup handedness + batter K rates
  10. Fetch team pitching stats
  11. Fetch game weather
  12. Fetch prop lines (FanDuel primary, Odds API fallback)
  13. Project pitcher strikeouts
  14. Generate picks, write output
  15. Save updated Kalman state

Usage:
    cd MLBstrikeouts
    python -m scripts.run_daily
    python -m scripts.run_daily --date 20260413
"""

import sys
import os
import json
import argparse
import datetime

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from sources.mlb_stats import (
    fetch_pitcher_game_logs, fetch_pitcher_advanced_stats,
    fetch_pitcher_sabermetrics, fetch_pitcher_handedness_splits,
    fetch_pitcher_handedness_splits_season,
    fetch_team_batting_stats, fetch_team_pitching_stats,
    fetch_today_probable_pitchers,
    fetch_player_bat_sides, fetch_lineup_handedness,
    fetch_batter_k_rates, load_pitch_hands,
    fetch_savant_pitcher_rates,
)
from sources.weather import fetch_game_weather
from sources.odds_fanduel import fetch_fanduel_mlb_props
from sources.odds_theoddsapi import fetch_mlb_pitcher_props
from sources.rotowire_lineups import (
    fetch_default_lineup as fetch_rotowire_default_lineup,
    fetch_team_roster_name_to_id as fetch_rotowire_team_roster,
)
from props_engine import organize_pitcher_logs, project_pitcher_props, format_props_for_dashboard, STAT_KEYS
from pitcher_kalman import (
    load_pitcher_kalman_state, save_pitcher_kalman_state,
    new_pitcher_kalman_state, batch_update_from_game_logs,
    apply_drift, kalman_summary, prune_inactive_pitchers,
)
from defaults import current_season

# Path to persistent Kalman state
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KALMAN_STATE_PATH = os.path.join(SCRIPT_DIR, "..", "data", "kalman_state.json")

# Game statuses that mean the game never produced final stats — picks attached
# to these resolve as VOID (sportsbook-standard handling: bet refunded, no
# unit impact, no W/L count).
_VOID_STATUSES = {
    "Postponed", "Cancelled", "Canceled", "Suspended",
    "Postponed Inclement Weather", "Postponed Rain",
    "Suspended: Inclement Weather", "Suspended: Rain",
}


def _is_game_postponed(team_abbr, game_date):
    """Check the cached MLB schedule for a postponed/suspended/cancelled game
    involving ``team_abbr`` on ``game_date`` (YYYY-MM-DD).  Returns True iff
    the team's scheduled game on that date has a void-class status.
    """
    if not team_abbr or not game_date:
        return False
    cache_path = os.path.join(SCRIPT_DIR, "..", "..", "data",
                              "pitcher_cache", "mlb",
                              f"probable_pitchers_{game_date}.json")
    cache_path = os.path.normpath(cache_path)
    if not os.path.exists(cache_path):
        return False
    try:
        with open(cache_path, "r") as f:
            games = json.load(f)
    except Exception:
        return False
    team = team_abbr.upper()
    for g in games:
        if g.get("home_team", "").upper() != team and g.get("away_team", "").upper() != team:
            continue
        if g.get("status", "") in _VOID_STATUSES:
            return True
    return False


_EMP_STD_CACHE_DIR = os.path.normpath(
    os.path.join(SCRIPT_DIR, "..", "..", "data", "emp_std_cache", "mlb")
)


def compute_empirical_std_from_graded(date_iso=None):
    """
    Compute league-level residual std (actual - proj) per market from
    graded entries in the most recent mlb-props.json.

    When date_iso is provided, only counts graded picks whose `date` is
    strictly before date_iso — preserves walk-forward integrity for
    backfill runs (no future data leak into past projections).

    Returns dict like {"strikeouts": 2.23, "n_strikeouts": 440} or {} when
    no market meets the EMPIRICAL_STD_MIN_SAMPLE threshold.
    """
    import math
    from defaults import EMPIRICAL_STD_MIN_SAMPLE

    candidate_paths = [
        os.path.join(SCRIPT_DIR, "..", "data", "mlb-props.json"),
        os.path.join(SCRIPT_DIR, "..", "..", "PythonDashboard", "data", "mlb-props.json"),
    ]
    data = None
    for path in candidate_paths:
        path = os.path.normpath(path)
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r") as f:
                data = json.load(f)
            break
        except Exception:
            continue
    if not data:
        return {}

    sources = list(data.get("props") or []) + list(data.get("todayProjections") or [])
    by_market = {}
    for p in sources:
        mkt = p.get("market")
        actual = p.get("actual")
        proj = p.get("proj")
        if mkt is None or actual is None or proj is None:
            continue
        # Walk-forward filter: only graded picks strictly before date_iso
        # count toward today's calibration. Backfill safety.
        if date_iso:
            pdate = p.get("date") or ""
            if not pdate or pdate >= date_iso:
                continue
        try:
            resid = float(actual) - float(proj)
        except (TypeError, ValueError):
            continue
        by_market.setdefault(mkt, []).append(resid)

    out = {}
    for mkt, residuals in by_market.items():
        if len(residuals) < EMPIRICAL_STD_MIN_SAMPLE:
            continue
        mean = sum(residuals) / len(residuals)
        var = sum((r - mean) ** 2 for r in residuals) / len(residuals)
        out[mkt] = math.sqrt(var)
        out[f"n_{mkt}"] = len(residuals)
    return out


def load_or_compute_empirical_std(date_key, date_iso):
    """
    Cached, run-stable empirical std for `date_key` (YYYYMMDD).

    First call of the day computes from graded picks dated strictly before
    date_iso, writes the result to
        data/emp_std_cache/mlb/emp_std_<date_key>.json
    All subsequent runs that day read from the cache, so multiple runs in
    one day project under the SAME emp_std (reproducible picks, no drift
    from picks newly graded mid-day).

    Returns the loaded/computed dict (may be empty) plus a `source` field:
    "cache" or "computed".
    """
    cache_path = os.path.join(_EMP_STD_CACHE_DIR, f"emp_std_{date_key}.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r") as f:
                cached = json.load(f)
            cached["source"] = "cache"
            return cached
        except Exception as e:
            print(f"  [empirical_std] cache read failed ({e}), recomputing")

    computed = compute_empirical_std_from_graded(date_iso=date_iso)
    computed["source"] = "computed"
    computed["computed_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    computed["date_iso"] = date_iso
    try:
        os.makedirs(_EMP_STD_CACHE_DIR, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(computed, f, indent=2)
    except Exception as e:
        print(f"  [empirical_std] cache write failed: {e}")
    return computed


def grade_previous_picks(season=None):
    """Grade ungraded picks from previous dates using actual game logs."""
    from collections import defaultdict

    output_paths = [
        os.path.join(SCRIPT_DIR, "..", "data", "mlb-props.json"),
        os.path.join(SCRIPT_DIR, "..", "..", "PythonDashboard", "data", "mlb-props.json"),
    ]

    # Load existing picks from first available path
    existing = {}
    source_path = None
    for path in output_paths:
        path = os.path.normpath(path)
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    existing = json.load(f)
                source_path = path
                break
            except Exception:
                continue

    if not existing:
        print("  [grade] No existing props file found — skipping grading")
        return

    props = existing.get("props", [])
    from zoneinfo import ZoneInfo
    today = datetime.datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d")

    # Find ungraded picks from previous dates
    ungraded = [p for p in props if p.get("date") and p["date"] < today and not p.get("result")]
    if not ungraded:
        print("  [grade] No ungraded picks from previous dates")
        return

    # Get unique dates that need grading
    dates_to_grade = sorted(set(p["date"] for p in ungraded))
    print(f"  [grade] Found {len(ungraded)} ungraded picks from {', '.join(dates_to_grade)}")

    # Fetch pitcher game logs for the season
    if season is None:
        from defaults import current_season
        season = current_season()
    all_logs = fetch_pitcher_game_logs(season=season)
    if not all_logs:
        print("  [grade] Could not fetch game logs — skipping grading")
        return

    # Index logs by (pitcher_name, game_date) for fast lookup
    logs_by_pitcher_date = defaultdict(list)
    for g in all_logs:
        key = (g.get("pitcher_name", ""), g.get("game_date", ""))
        logs_by_pitcher_date[key].append(g)

    # Grade each pick
    graded = 0
    wins = 0
    losses = 0
    pushes = 0
    watch_graded = 0  # watchlist (lean) picks resolved this pass

    for pick in props:
        if pick.get("result") or not pick.get("date") or pick["date"] >= today:
            continue

        player = pick.get("player", "")
        market = pick.get("market", "")
        line = pick.get("line")
        direction = pick.get("pick", "")

        # Watchlist (pick=PASS) entries are graded against would_be_pick
        # so 0.60-0.70 bucket performance can be tracked.
        is_watch = direction == "PASS"
        if is_watch:
            direction = pick.get("would_be_pick", "")

        if line is None or direction not in ("OVER", "UNDER"):
            continue

        # Find actual stat from game log
        games = logs_by_pitcher_date.get((player, pick["date"]), [])
        # Drop degenerate rows (bf=0, outs=0, pitches=0) — these are stale
        # captures of postponed/makeup games where stats hadn't propagated.
        # Treat as missing so we either VOID (postponement) or leave ungraded
        # (data not yet propagated) rather than crediting a fake k=0.
        games = [g for g in games if not (
            int(g.get("bf", 0) or 0) == 0
            and int(g.get("outs", 0) or 0) == 0
            and int(g.get("pitches", 0) or 0) == 0
        )]
        if not games:
            # No game log for this pitcher/date.  Could be:
            #   (a) game postponed / suspended / cancelled — mark VOID
            #   (b) data hasn't propagated yet (recent date) — leave ungraded
            # Check the schedule cache for the pitcher's team on that date.
            if _is_game_postponed(pick.get("team", ""), pick["date"]):
                pick["result"] = "VOID"
                pick["actual"] = None
                if not is_watch:
                    graded += 1  # count as resolved (so we stop reprocessing)
                else:
                    watch_graded += 1
            continue
        game = games[0]

        stat_key = STAT_KEYS.get(market)
        if not stat_key:
            continue
        val = game.get(stat_key)
        if val is None:
            continue
        actual = float(val)

        pick["actual"] = round(actual, 1)
        # Capture actual outs/BF/pitches alongside K for historical analysis
        if game.get("outs") is not None:
            pick["actual_outs"] = game.get("outs")
        if game.get("bf") is not None:
            pick["actual_bf"] = game.get("bf")
        if game.get("pitches") is not None:
            pick["actual_pitches"] = game.get("pitches")

        if actual == line:
            pick["result"] = "PUSH"
            if not is_watch:
                pushes += 1
        elif (direction == "OVER" and actual > line) or (direction == "UNDER" and actual < line):
            pick["result"] = "WIN"
            if not is_watch:
                wins += 1
        else:
            pick["result"] = "LOSS"
            if not is_watch:
                losses += 1
        if not is_watch:
            graded += 1
        else:
            watch_graded += 1

    if graded == 0 and watch_graded == 0:
        print("  [grade] No picks could be matched to actual stats")
        return

    total = wins + losses
    pct = wins / max(1, total) * 100
    units = 0.0
    for pick in props:
        r = pick.get("result")
        price = pick.get("odds")
        if r == "WIN":
            if price is not None and int(price) > 0:
                units += int(price) / 100.0
            else:
                units += 1.0
        elif r == "LOSS":
            if price is not None and int(price) > 0:
                units -= 1.0
            else:
                w1u = pick.get("to_win_1u")
                units -= float(w1u) if w1u is not None else 1.1
    print(f"  [grade] Graded {graded} picks: {wins}W-{losses}L ({pct:.1f}%) {'+'if units >= 0 else ''}{units:.1f}u"
          + (f" + {pushes} pushes" if pushes else ""))

    # Write back to all output paths
    existing["props"] = props
    for path in output_paths:
        path = os.path.normpath(path)
        if os.path.exists(os.path.dirname(path)):
            try:
                with open(path, "w") as f:
                    json.dump(existing, f, indent=2)
                print(f"  [grade] Updated {path}")
            except Exception as e:
                print(f"  [grade] Failed to write {path}: {e}")


def run_daily(date_key=None):
    """Run the daily MLB pitcher strikeouts projection pipeline."""
    from zoneinfo import ZoneInfo

    if date_key is None:
        now = datetime.datetime.now(ZoneInfo("America/Chicago"))
        date_key = now.strftime("%Y%m%d")
        date_iso = now.strftime("%Y-%m-%d")
    else:
        date_iso = f"{date_key[:4]}-{date_key[4:6]}-{date_key[6:8]}"

    season = current_season()

    print(f"\n{'='*60}")
    print(f"  MLB PITCHER STRIKEOUTS — {date_iso}")
    print(f"  Season: {season}")
    print(f"{'='*60}")

    # Stage 0: Grade previous picks
    print(f"\n  [0/15] Grading previous picks...")
    grade_previous_picks(season)

    # Stage 1: Load Kalman state
    print(f"\n  [1/15] Loading Kalman state...")
    kalman_state = load_pitcher_kalman_state(KALMAN_STATE_PATH)
    n_pitchers = len(kalman_state.get("pitchers", {}))
    print(f"  Kalman state: {n_pitchers} pitchers tracked")

    # Stage 2: Fetch pitcher game logs
    print(f"\n  [2/15] Fetching pitcher game logs...")
    pitcher_game_logs = fetch_pitcher_game_logs(season=season)
    if not pitcher_game_logs:
        print("  ERROR: No pitcher game logs fetched. Exiting.")
        return
    pitcher_logs = organize_pitcher_logs(pitcher_game_logs)
    print(f"  {len(pitcher_logs)} pitchers with game logs")

    # Stage 3: Update Kalman with new games
    print(f"\n  [3/15] Updating Kalman state with new games...")
    n_updated = batch_update_from_game_logs(kalman_state, pitcher_game_logs)
    print(f"  Processed {n_updated} pitcher-games")
    apply_drift(kalman_state)

    # Stage 4: Fetch advanced stats
    print(f"\n  [4/15] Fetching pitcher advanced stats...")
    adv_stats = fetch_pitcher_advanced_stats(season=season)
    print(f"  {len(adv_stats)} pitchers with advanced stats")

    # Stage 5: Fetch sabermetrics
    print(f"\n  [5/15] Fetching pitcher sabermetrics (FIP, xFIP)...")
    saber_stats = fetch_pitcher_sabermetrics(season=season)
    print(f"  {len(saber_stats)} pitchers with sabermetrics")

    # Stage 6: Fetch probable pitchers
    print(f"\n  [6/15] Fetching today's probable pitchers...")
    all_probable = fetch_today_probable_pitchers(date_str=date_iso)

    import datetime as _dt
    _now_utc = _dt.datetime.now(_dt.timezone.utc)
    started_teams = set()
    for g in all_probable:
        game_time_str = g.get("game_time", "")
        try:
            game_time = _dt.datetime.fromisoformat(game_time_str.replace("Z", "+00:00"))
            if game_time <= _now_utc:
                if g.get("home_team"): started_teams.add(g["home_team"])
                if g.get("away_team"): started_teams.add(g["away_team"])
        except (ValueError, AttributeError, TypeError):
            pass

    probable = list(all_probable)
    if started_teams:
        print(f"  {len(all_probable)} total games, {len(started_teams)//2} already started (picks preserved, projecting all)")
    print(f"  {len(probable)} games to project")

    opposing_pitcher_by_team = {}
    for _g in all_probable:
        if _g.get("home_team") and _g.get("away_pitcher_id"):
            opposing_pitcher_by_team[_g["home_team"]] = _g["away_pitcher_id"]
        if _g.get("away_team") and _g.get("home_pitcher_id"):
            opposing_pitcher_by_team[_g["away_team"]] = _g["home_pitcher_id"]

    _pitch_hands_early = load_pitch_hands(season=season)

    # Stage 7: Fetch handedness splits for probable starters only
    print(f"\n  [7/15] Fetching handedness splits for probable starters...")
    pitcher_ids = set()
    for game in probable:
        for key in ("home_pitcher_id", "away_pitcher_id"):
            pid = game.get(key)
            if pid:
                pitcher_ids.add(pid)
    splits = {}
    for pid in pitcher_ids:
        s = fetch_pitcher_handedness_splits(pid, season=season, through_date=date_iso)
        s_season = fetch_pitcher_handedness_splits_season(pid, season=season, through_date=date_iso)
        if s and s_season:
            s["vs_left_season"] = s_season.get("vs_left", {})
            s["vs_right_season"] = s_season.get("vs_right", {})
        if s:
            splits[str(pid)] = s
    print(f"  {len(splits)} pitchers with handedness splits (recent + season)")

    # Stage 8: Fetch team batting stats
    print(f"\n  [8/15] Fetching team batting stats...")
    team_batting = fetch_team_batting_stats(season=season, through_date=date_iso)
    print(f"  {len(team_batting)} teams with batting stats")

    # Stage 9: Fetch lineup handedness (actual starting lineups, not roster avg)
    print(f"\n  [9/15] Fetching lineup handedness (actual batting orders)...")
    bat_sides = fetch_player_bat_sides(season=season)
    lineup_hand = fetch_lineup_handedness(date_iso, bat_sides=bat_sides, season=season)
    print(f"  {len(lineup_hand)} teams with lineup handedness data")

    for abbr, hand_data in lineup_hand.items():
        if abbr in team_batting:
            team_batting[abbr]["PCT_LHB"] = hand_data["PCT_LHB"]
        else:
            team_batting[abbr] = {"PCT_LHB": hand_data["PCT_LHB"]}

    from sources.mlb_stats import CACHE_DIR as _MLB_CACHE_DIR
    _lineup_cache_path = _MLB_CACHE_DIR / f"lineups_{date_iso.replace('-','')}.json"
    lineup_data = {}
    try:
        if _lineup_cache_path.exists():
            with open(_lineup_cache_path, "r") as _f:
                lineup_data = json.load(_f) or {}
    except Exception:
        lineup_data = {}

    _all_confirmed = (lineup_data
                      and all(v.get("confirmed") for v in lineup_data.values()))

    if not _all_confirmed:
        try:
            import requests
            url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date_iso}&hydrate=lineups"
            resp = requests.get(url, timeout=15)
            sched = resp.json()
            from sources.mlb_stats import MLB_TEAM_ID_TO_ABBR
            for date_entry in sched.get("dates", []):
                for game in date_entry.get("games", []):
                    lineups = game.get("lineups", {})
                    teams = game.get("teams", {})
                    for side, lineup_key in [("home", "homePlayers"), ("away", "awayPlayers")]:
                        team_id = teams.get(side, {}).get("team", {}).get("id")
                        abbr = MLB_TEAM_ID_TO_ABBR.get(team_id, "")
                        if not abbr:
                            continue
                        if lineup_data.get(abbr, {}).get("confirmed"):
                            continue
                        players = lineups.get(lineup_key, [])
                        lineup_entries = []
                        for slot_idx, p in enumerate(players):
                            lineup_entries.append({
                                "batter_id": p.get("id"),
                                "name": p.get("fullName", ""),
                                "slot": slot_idx + 1,
                            })
                        confirmed = len(lineup_entries) >= 9
                        pids = [p.get("id") for p in players]
                        _lineup_source = None
                        if not pids:
                            opp_pid = opposing_pitcher_by_team.get(abbr)
                            vs_hand = _pitch_hands_early.get(opp_pid) if opp_pid else None
                            if vs_hand in ("R", "L"):
                                rw = fetch_rotowire_default_lineup(
                                    abbr, vs_hand, date_iso
                                ) or []
                                if rw:
                                    from sources.rotowire_lineups import _norm_name
                                    roster = fetch_rotowire_team_roster(team_id)
                                    resolved = []
                                    for e in rw:
                                        pid = roster.get(_norm_name(e["name"]))
                                        if pid:
                                            resolved.append((pid, e["name"]))
                                    if len(resolved) >= 5:
                                        pids = [pid for pid, _ in resolved]
                                        lineup_entries = [
                                            {"batter_id": pid, "name": nm, "slot": i + 1}
                                            for i, (pid, nm) in enumerate(resolved)
                                        ]
                                        _lineup_source = f"rotowire_default_vs_{vs_hand}HP"
                        if not pids:
                            from sources.mlb_stats import get_recent_batting_order
                            recent = get_recent_batting_order(abbr, season=season, before_date=date_iso)
                            if recent:
                                pids = recent
                                lineup_entries = [
                                    {"batter_id": pid, "name": "", "slot": i + 1}
                                    for i, pid in enumerate(recent)
                                ]
                                _lineup_source = "recent_lineup_fallback"
                        lineup_data[abbr] = {
                            "player_ids": pids,
                            "lineup": lineup_entries,
                            "confirmed": confirmed,
                            "source": ("lineup" if confirmed else
                                       (_lineup_source or "none")),
                            "implied_runs": None,
                        }
        except Exception:
            pass

        try:
            os.makedirs(os.path.dirname(str(_lineup_cache_path)), exist_ok=True)
            with open(_lineup_cache_path, "w") as _f:
                json.dump(lineup_data, _f)
        except Exception:
            pass

    _n_conf = sum(1 for v in lineup_data.values() if v.get("confirmed"))
    print(f"  {len(lineup_data)} teams in lineup_data ({_n_conf} confirmed, frozen)")

    # Fetch batter K rates (bulk, 2 API calls)
    print(f"\n  [9b/15] Fetching batter K rates + Savant pitcher rates...")
    batter_k_rates = fetch_batter_k_rates(season=season, through_date=date_iso)
    pitch_hands = load_pitch_hands(season=season)
    savant_rates = fetch_savant_pitcher_rates(season=season)
    print(f"  {len(batter_k_rates)} batters, {len(savant_rates)} pitchers with Savant K%/whiff%")

    for pid_str, adv in adv_stats.items():
        try:
            adv["pitch_hand"] = pitch_hands.get(int(pid_str), "R")
        except (ValueError, TypeError):
            pass

    # Stage 10: Fetch team pitching stats
    print(f"\n  [10/15] Fetching team pitching stats...")
    team_pitching = fetch_team_pitching_stats(season=season)
    print(f"  {len(team_pitching)} teams with pitching stats")

    # Stage 11: Fetch game weather
    print(f"\n  [11/15] Fetching game weather...")
    weather_data = fetch_game_weather(date_iso)
    n_weather = len(weather_data)
    print(f"  Weather data for {n_weather} games")

    # Stage 12: Fetch prop lines (FanDuel + Odds API combined)
    print(f"\n  [12/15] Fetching prop lines (FanDuel + Odds API)...")
    # Freeze the FanDuel cache only for games whose pick was ALREADY locked in
    # a prior run (lockState ∈ {lineup_confirmed, game_started, final}). On the
    # very first run that sees a confirmation, we still fetch fresh odds so the
    # lock price reflects the live market at the moment of confirmation, not a
    # stale price cached before the lineup was posted.
    fd_frozen_keys = set()
    try:
        prior_path = os.path.join(SCRIPT_DIR, "..", "data", "mlb-props.json")
        if os.path.exists(prior_path):
            with open(prior_path, "r") as _f:
                _prior = json.load(_f)
            _LOCKED = {"lineup_confirmed", "game_started", "final"}
            for _p in (_prior.get("props", []) or []):
                if _p.get("date") == date_iso and _p.get("lockState") in _LOCKED:
                    if _p.get("team"):
                        fd_frozen_keys.add(_p["team"])
                    if _p.get("opp"):
                        fd_frozen_keys.add(_p["opp"])
    except Exception:
        fd_frozen_keys = set()
    fd_result = fetch_fanduel_mlb_props(
        date_key=date_key, confirmed_team_keys=fd_frozen_keys
    )
    if isinstance(fd_result, tuple):
        fd_props, _ = fd_result
    else:
        fd_props = fd_result

    odds_api_props = fetch_mlb_pitcher_props(date_key=date_key)

    fd_markets = {(p.get("player",""), p.get("market","")) for p in fd_props}
    merged_props = list(fd_props)
    for p in odds_api_props:
        key = (p.get("player",""), p.get("market",""))
        if key not in fd_markets:
            merged_props.append(p)

    prop_lines = merged_props
    n_fd = len(fd_props)
    n_api = len(prop_lines) - n_fd
    print(f"  {n_fd} from FanDuel + {n_api} from Odds API = {len(prop_lines)} total prop lines")

    # Stage 13: Project pitcher props
    print(f"\n  [13/15] Projecting pitcher strikeouts (Kalman + advanced stats)...")
    # Runtime-calibrated empirical std — cached per date_key so multiple
    # runs in one day project under the SAME emp_std. Walk-forward safe:
    # only graded picks dated strictly before date_iso count toward today's
    # calibration. Falls back to DEFAULT_EMPIRICAL_STD inside the engine
    # when a market has <EMPIRICAL_STD_MIN_SAMPLE graded entries.
    emp_std_cache = load_or_compute_empirical_std(date_key, date_iso)
    runtime_emp_std = {
        k: v for k, v in emp_std_cache.items()
        if not k.startswith(("n_", "source", "computed_at", "date_iso"))
        and isinstance(v, (int, float))
    }
    if runtime_emp_std:
        from defaults import DEFAULT_EMPIRICAL_STD
        src = emp_std_cache.get("source", "?")
        for mkt, val in runtime_emp_std.items():
            n = emp_std_cache.get(f"n_{mkt}", "?")
            default_val = DEFAULT_EMPIRICAL_STD.get(mkt, "n/a")
            print(f"  [empirical_std] {mkt}: {val:.3f} ({src}, n={n}, "
                  f"default {default_val})")
    else:
        print(f"  [empirical_std] insufficient graded sample — using defaults")
    projections = project_pitcher_props(
        pitcher_logs,
        team_batting_stats=team_batting,
        prop_lines=prop_lines,
        kalman_state=kalman_state,
        pitcher_adv_stats=adv_stats,
        pitcher_sabermetrics=saber_stats,
        pitcher_splits=splits,
        probable_pitchers=probable,
        injury_report=None,
        weather_by_game=weather_data,
        batter_k_rates=batter_k_rates,
        lineup_data=lineup_data,
        savant_rates=savant_rates,
        empirical_std=runtime_emp_std or None,
    )
    picks = [p for p in projections if p["pick"] != "PASS"]
    print(f"  {len(projections)} projections, {len(picks)} actionable picks")

    print(kalman_summary(kalman_state, top_n=5, stat_key="k"))

    # Stage 14: Output
    print(f"\n  [14/15] Writing output...")
    dashboard = format_props_for_dashboard(projections, date_str=date_iso)

    game_times = {}
    game_statuses = {}
    for g in all_probable:
        gt = g.get("game_time", "")
        status = g.get("status", "") or ""
        for t_key in ("home_team", "away_team"):
            team = g.get(t_key)
            if not team:
                continue
            if gt:
                game_times[team] = gt
            if status:
                game_statuses[team] = status

    combined = {
        **dashboard,
        "gameTimes": game_times,
        "gameStatuses": game_statuses,
    }

    output_paths = [
        os.path.join(SCRIPT_DIR, "..", "data", "mlb-props.json"),
        os.path.join(SCRIPT_DIR, "..", "..", "PythonDashboard", "data", "mlb-props.json"),
    ]

    import numpy as np

    class _NumpyEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                return float(obj)
            return super().default(obj)

    confirmed_teams = {
        abbr for abbr, v in (lineup_data or {}).items()
        if v.get("confirmed")
    }
    locked_teams = set(started_teams) | confirmed_teams

    _LOCK_RANK = {"pending": 0, "lineup_confirmed": 1, "game_started": 2, "final": 3}
    _now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"

    def _lock_key(pick):
        return pick.get("opp", "") or pick.get("team", "")

    def _current_lock_state(pick):
        key = _lock_key(pick)
        if key in started_teams:
            return "game_started"
        if key in confirmed_teams:
            return "lineup_confirmed"
        return "pending"

    def _is_locked(pick):
        return _current_lock_state(pick) != "pending"

    def _stamp(pick, existing_pick=None):
        new_state = _current_lock_state(pick)
        prev_state = (existing_pick or {}).get("lockState", "pending")
        if existing_pick and _LOCK_RANK.get(prev_state, 0) >= _LOCK_RANK.get(new_state, 0):
            pick["lockState"] = prev_state
            pick["lockedAt"] = existing_pick.get("lockedAt")
        else:
            pick["lockState"] = new_state
            pick["lockedAt"] = (pick.get("lockedAt")
                                or (_now_iso if new_state != "pending" else None))
        return pick

    for path in output_paths:
        path = os.path.normpath(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)

        existing = {}
        existing_props = []
        existing_today_proj = []
        try:
            if os.path.exists(path):
                with open(path, "r") as f:
                    existing = json.load(f)
                existing_props = existing.get("props", [])
                existing_today_proj = [p for p in existing.get("todayProjections", [])
                                       if p.get("date") == date_iso]
        except Exception:
            existing = {}
            existing_props = []
            existing_today_proj = []

        kept = [p for p in existing_props if p.get("date") != date_iso]

        def _pkey(p):
            return (p.get("team", ""), p.get("player", ""), p.get("market", ""))
        existing_today_by_key = {
            _pkey(p): p for p in existing_props if p.get("date") == date_iso
        }
        existing_proj_by_key = {_pkey(p): p for p in existing_today_proj}
        proj_locked_keys = {
            k for k, p in existing_proj_by_key.items()
            if p.get("lockState") in ("lineup_confirmed", "game_started", "final")
        }

        _LOCK_STATES = ("lineup_confirmed", "game_started", "final")
        already_locked_entries = [
            p for p in existing_props
            if p.get("date") == date_iso
            and _is_locked(p)
            and p.get("lockState") in _LOCK_STATES
        ]
        transitioning_keys = {
            _pkey(p) for p in existing_props
            if p.get("date") == date_iso
            and _is_locked(p)
            and p.get("lockState") not in _LOCK_STATES
        }
        locked_keys = {_pkey(p) for p in already_locked_entries}

        today_fresh = []
        existing_today_keys = set(existing_today_by_key.keys())
        for p in combined.get("props", []):
            if not (p.get("date") == date_iso or not p.get("date")):
                continue
            k = _pkey(p)
            if k in locked_keys:
                continue
            if k in proj_locked_keys and k not in transitioning_keys:
                continue
            if _is_locked(p) and k in existing_today_keys and k not in transitioning_keys:
                continue
            if (_current_lock_state(p) == "game_started"
                and k not in existing_today_keys
                and k not in existing_proj_by_key):
                continue
            today_fresh.append(_stamp(p, existing_today_by_key.get(k)))

        today_existing_locked = [
            _stamp(dict(p), p) for p in already_locked_entries
        ]

        merged_props = kept + today_existing_locked + today_fresh

        today_fresh_keys = {_pkey(p) for p in today_fresh}
        stale_pending, stale_at_lock = [], []
        for p in existing_props:
            if p.get("date") != date_iso:
                continue
            k = _pkey(p)
            if k in today_fresh_keys:
                continue
            if k in {_pkey(x) for x in already_locked_entries}:
                continue
            if _is_locked(p):
                stale_at_lock.append(p)
            else:
                stale_pending.append(p)
        if stale_pending:
            names = ", ".join(f"{p.get('player','?')}({p.get('team','?')})"
                              for p in stale_pending[:5])
            extra = "..." if len(stale_pending) > 5 else ""
            print(f"  [merge] Dropped {len(stale_pending)} stale pending picks "
                  f"(projection now PASS): {names}{extra}")
        if stale_at_lock:
            names = ", ".join(f"{p.get('player','?')}({p.get('team','?')})"
                              for p in stale_at_lock[:5])
            extra = "..." if len(stale_at_lock) > 5 else ""
            print(f"  [merge] Dropped {len(stale_at_lock)} picks at lock "
                  f"(lineup confirmed, fresh projection no longer a pick): "
                  f"{names}{extra}")

        prev_locked_keys = {
            _pkey(p) for p in existing_props
            if p.get("date") == date_iso
            and p.get("lockState") in ("lineup_confirmed", "game_started", "final")
        }
        new_today_keys = {_pkey(p) for p in merged_props if p.get("date") == date_iso}
        dropped = prev_locked_keys - new_today_keys
        if dropped:
            raise RuntimeError(
                f"Refusing to erase {len(dropped)} locked picks on {date_iso}: "
                f"{sorted(dropped)[:5]}{'...' if len(dropped) > 5 else ''}"
            )

        def _merge_projections(existing_list, fresh_list):
            _LS = ("lineup_confirmed", "game_started", "final")
            existing_today = [p for p in existing_list if p.get("date") == date_iso]
            existing_today_keys = {
                (p.get("team",""), p.get("player",""), p.get("market",""))
                for p in existing_today
            }
            already_locked_proj = {
                (p.get("team",""), p.get("player",""), p.get("market","")): p
                for p in existing_today
                if _is_locked(p)
                and p.get("lockState") in _LS
            }
            merged = []
            seen = set()
            for k, p in already_locked_proj.items():
                merged.append(_stamp(dict(p), p))
                seen.add(k)
            for p in fresh_list:
                k = (p.get("team",""), p.get("player",""), p.get("market",""))
                if k in seen:
                    continue
                if _current_lock_state(p) == "game_started" and k not in existing_today_keys:
                    continue
                merged.append(_stamp(p))
                seen.add(k)
            return merged

        merged_today_proj = _merge_projections(
            existing.get("todayProjections", []) or [],
            combined.get("todayProjections", []) or [],
        )

        combined_merged = {
            **existing, **combined,
            "props": merged_props,
            "todayProjections": merged_today_proj,
        }
        combined_merged["totalPicks"] = len(merged_props)

        n_kept_hist = len(kept)
        n_locked = len(today_existing_locked)
        n_new = len(today_fresh)
        n_started = len([p for p in today_existing_locked
                         if p.get("lockState") == "game_started"])
        n_confirmed = n_locked - n_started
        print(f"  Merged picks: {n_kept_hist} historical + {n_locked} locked "
              f"({n_started} started, {n_confirmed} lineup-confirmed) + {n_new} fresh")

        with open(path, "w") as f:
            json.dump(combined_merged, f, indent=2, cls=_NumpyEncoder)
        print(f"  Wrote to {path}")

    # Stage 15: Save Kalman state
    prune_inactive_pitchers(kalman_state)
    save_pitcher_kalman_state(kalman_state, KALMAN_STATE_PATH)
    print(f"  Saved pitcher Kalman state ({len(kalman_state.get('pitchers', {}))} pitchers)")

    _print_picks(picks)

    return combined


def _print_picks(pitcher_picks):
    """Print formatted picks summary."""
    total = len(pitcher_picks)
    if total == 0:
        print("\n  No actionable picks today.")
        return

    print(f"\n{'='*60}")
    print(f"  TODAY'S PICKS ({total} total)")
    print(f"{'='*60}")

    by_market = {}
    for p in pitcher_picks:
        m = p["market"]
        if m not in by_market:
            by_market[m] = []
        by_market[m].append(p)

    for market, market_picks in sorted(by_market.items()):
        print(f"\n  --- {market.upper()} ({len(market_picks)} picks) ---")
        for p in sorted(market_picks, key=lambda x: -(x.get("pCover") or 0)):
            edge_sign = "+" if (p.get("edge") or 0) > 0 else ""
            odds_str = _format_odds(p.get("odds"))
            w1u = p.get("to_win_1u")
            w1u_str = f"  w1u={w1u:.2f}u" if w1u is not None else ""
            import unicodedata
            safe_name = unicodedata.normalize('NFKD', p['player']).encode('ascii', 'ignore').decode('ascii')
            print(
                f"    {safe_name:25s} {p.get('team', ''):3s} "
                f"{p['pick']:5s} {p['line']:6.1f}  "
                f"proj={p['proj']:6.1f}  edge={edge_sign}{p.get('edge', 0):5.1f}  "
                f"p={p.get('pCover', 0):.3f}  "
                f"{odds_str}{w1u_str}"
            )


def _format_odds(price):
    """Format American odds with +/- prefix."""
    if price is None:
        return "odds=N/A"
    price = int(price)
    if price > 0:
        return f"odds=+{price}"
    return f"odds={price}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Daily MLB pitcher strikeouts projections (Kalman)")
    parser.add_argument("--date", type=str, default=None,
                        help="Date in YYYYMMDD format (default: today)")
    args = parser.parse_args()

    run_daily(date_key=args.date)
