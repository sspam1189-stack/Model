#!/usr/bin/env python3
"""
MLBstrikeouts/scripts/run_daily.py
Daily MLB pitcher prop + game hits + batter TB projection pipeline with Kalman filtering.

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
  13. Project all pitcher props (K, outs, hits, walks)
  14. Project total game hits
  15. Fetch batter data (game logs, Savant rates, splits, park factors)
  16. Batter Kalman update
  17. Project batter total bases
  18. Generate picks, write output
  19. Save updated Kalman state

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
    fetch_team_batting_stats, fetch_team_pitching_stats,
    fetch_today_probable_pitchers,
    fetch_player_bat_sides, fetch_lineup_handedness,
    fetch_batter_k_rates, load_pitch_hands,
    fetch_savant_pitcher_rates,
    fetch_batter_game_logs, fetch_savant_batter_rates, fetch_batter_splits,
)
from sources.weather import fetch_game_weather
from sources.park_factors import compute_park_factors
from sources.odds_fanduel import fetch_fanduel_mlb_props, fetch_fanduel_mlb_batter_props
from sources.odds_bovada import fetch_bovada_mlb_batter_props
from sources.odds_theoddsapi import fetch_mlb_pitcher_props, fetch_mlb_batter_props
from props_engine import organize_pitcher_logs, project_pitcher_props, format_props_for_dashboard, STAT_KEYS
from game_hits_engine import project_game_hits, format_game_hits_for_dashboard
from batter_props_engine import project_batter_tb, format_batter_props_for_dashboard
from pitcher_kalman import (
    load_pitcher_kalman_state, save_pitcher_kalman_state,
    new_pitcher_kalman_state, batch_update_from_game_logs,
    apply_drift, kalman_summary, prune_inactive_pitchers,
)
from batter_kalman import (
    load_batter_kalman_state, save_batter_kalman_state,
    new_batter_kalman_state, batch_update_from_game_logs as batter_batch_update,
    apply_drift as batter_apply_drift, prune_inactive_batters,
)
from defaults import current_season

# Path to persistent Kalman state
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KALMAN_STATE_PATH = os.path.join(SCRIPT_DIR, "..", "data", "kalman_state.json")
BATTER_KALMAN_STATE_PATH = os.path.join(SCRIPT_DIR, "..", "data", "batter_kalman_state.json")


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

    for pick in props:
        if pick.get("result") or not pick.get("date") or pick["date"] >= today:
            continue

        player = pick.get("player", "")
        market = pick.get("market", "")
        line = pick.get("line")
        direction = pick.get("pick", "")

        if line is None or direction not in ("OVER", "UNDER"):
            continue

        # Find actual stat from game log
        games = logs_by_pitcher_date.get((player, pick["date"]), [])
        if not games:
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

        if actual == line:
            pick["result"] = "PUSH"
            pushes += 1
        elif (direction == "OVER" and actual > line) or (direction == "UNDER" and actual < line):
            pick["result"] = "WIN"
            wins += 1
        else:
            pick["result"] = "LOSS"
            losses += 1
        graded += 1

    if graded == 0:
        print("  [grade] No picks could be matched to actual stats")
        return

    total = wins + losses
    pct = wins / max(1, total) * 100
    # Staking: plus odds risk 1u to win payout, negative odds risk X to win 1u
    # +120: risk 1u, win +1.2u, loss -1u
    # -150: risk 1.5u, win +1u, loss -1.5u
    units = 0.0
    for pick in props:
        r = pick.get("result")
        price = pick.get("odds")
        if r == "WIN":
            if price is not None and int(price) > 0:
                units += int(price) / 100.0  # +120 -> win 1.2u
            else:
                units += 1.0  # negative odds -> win 1u
        elif r == "LOSS":
            if price is not None and int(price) > 0:
                units -= 1.0  # plus odds -> risk 1u
            else:
                w1u = pick.get("to_win_1u")
                units -= float(w1u) if w1u is not None else 1.1  # -150 -> risk 1.5u
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
    """Run the daily MLB pitcher prop + game hits projection pipeline."""
    from zoneinfo import ZoneInfo

    if date_key is None:
        now = datetime.datetime.now(ZoneInfo("America/Chicago"))
        date_key = now.strftime("%Y%m%d")
        date_iso = now.strftime("%Y-%m-%d")
    else:
        date_iso = f"{date_key[:4]}-{date_key[4:6]}-{date_key[6:8]}"

    season = current_season()

    print(f"\n{'='*60}")
    print(f"  MLB PITCHER PROPS + GAME HITS + BATTER TB — {date_iso}")
    print(f"  Season: {season}")
    print(f"{'='*60}")

    # Stage 0: Grade previous picks
    print(f"\n  [0/19] Grading previous picks...")
    grade_previous_picks(season)

    # Stage 1: Load Kalman state
    print(f"\n  [1/19] Loading Kalman state...")
    kalman_state = load_pitcher_kalman_state(KALMAN_STATE_PATH)
    n_pitchers = len(kalman_state.get("pitchers", {}))
    print(f"  Kalman state: {n_pitchers} pitchers tracked")

    # Stage 2: Fetch pitcher game logs
    print(f"\n  [2/19] Fetching pitcher game logs...")
    pitcher_game_logs = fetch_pitcher_game_logs(season=season)
    if not pitcher_game_logs:
        print("  ERROR: No pitcher game logs fetched. Exiting.")
        return
    pitcher_logs = organize_pitcher_logs(pitcher_game_logs)
    print(f"  {len(pitcher_logs)} pitchers with game logs")

    # Stage 3: Update Kalman with new games
    print(f"\n  [3/19] Updating Kalman state with new games...")
    n_updated = batch_update_from_game_logs(kalman_state, pitcher_game_logs)
    print(f"  Processed {n_updated} pitcher-games")
    apply_drift(kalman_state, days_elapsed=1)

    # Stage 4: Fetch advanced stats
    print(f"\n  [4/19] Fetching pitcher advanced stats...")
    adv_stats = fetch_pitcher_advanced_stats(season=season)
    print(f"  {len(adv_stats)} pitchers with advanced stats")

    # Stage 5: Fetch sabermetrics
    print(f"\n  [5/19] Fetching pitcher sabermetrics (FIP, xFIP)...")
    saber_stats = fetch_pitcher_sabermetrics(season=season)
    print(f"  {len(saber_stats)} pitchers with sabermetrics")

    # Stage 6: Fetch probable pitchers
    print(f"\n  [6/19] Fetching today's probable pitchers...")
    all_probable = fetch_today_probable_pitchers(date_str=date_iso)

    # Split into started vs unstarted — only project unstarted games.
    # Started games keep their existing picks/projections untouched.
    import datetime as _dt
    _now_utc = _dt.datetime.now(_dt.timezone.utc)
    started_teams = set()
    probable = []
    for g in all_probable:
        game_time_str = g.get("game_time", "")
        try:
            game_time = _dt.datetime.fromisoformat(game_time_str.replace("Z", "+00:00"))
            if game_time <= _now_utc:
                # Game already started — mark teams, skip projection
                if g.get("home_team"): started_teams.add(g["home_team"])
                if g.get("away_team"): started_teams.add(g["away_team"])
                continue
        except (ValueError, AttributeError, TypeError):
            pass  # Can't parse time — include game to be safe
        probable.append(g)

    if started_teams:
        print(f"  {len(all_probable)} total games, {len(started_teams)//2} already started — skipping")
    print(f"  {len(probable)} games to project (not yet started)")

    # Stage 7: Fetch handedness splits for probable starters only
    print(f"\n  [7/19] Fetching handedness splits for probable starters...")
    pitcher_ids = set()
    for game in probable:
        for role in ("home_pitcher", "away_pitcher"):
            pid = game.get(role, {}).get("id")
            if pid:
                pitcher_ids.add(pid)
    splits = {}
    for pid in pitcher_ids:
        s = fetch_pitcher_handedness_splits(pid, season=season)
        if s:
            splits[str(pid)] = s
    print(f"  {len(splits)} pitchers with handedness splits")

    # Stage 8: Fetch team batting stats
    print(f"\n  [8/19] Fetching team batting stats...")
    team_batting = fetch_team_batting_stats(season=season)
    print(f"  {len(team_batting)} teams with batting stats")

    # Stage 9: Fetch lineup handedness (actual starting lineups, not roster avg)
    print(f"\n  [9/19] Fetching lineup handedness (actual batting orders)...")
    bat_sides = fetch_player_bat_sides(season=season)
    lineup_hand = fetch_lineup_handedness(date_iso, bat_sides=bat_sides, season=season)
    print(f"  {len(lineup_hand)} teams with lineup handedness data")

    # Merge PCT_LHB into team_batting so props_engine sees it
    for abbr, hand_data in lineup_hand.items():
        if abbr in team_batting:
            team_batting[abbr]["PCT_LHB"] = hand_data["PCT_LHB"]
        else:
            team_batting[abbr] = {"PCT_LHB": hand_data["PCT_LHB"]}

    # Build lineup_data with player_ids for K% lookup
    # lineup_hand has {team: {PCT_LHB, n_batters, source}} but we also need player_ids
    # Re-fetch lineup to get IDs (the schedule hydrate=lineups call is cached)
    lineup_data = {}
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
                    players = lineups.get(lineup_key, [])
                    lineup_entries = []
                    for slot_idx, p in enumerate(players):
                        lineup_entries.append({
                            "batter_id": p.get("id"),
                            "name": p.get("fullName", ""),
                            "slot": slot_idx + 1,
                        })
                    lineup_data[abbr] = {
                        "player_ids": [p.get("id") for p in players],
                        "lineup": lineup_entries,
                        "confirmed": len(lineup_entries) >= 9,
                        "implied_runs": None,  # could be filled from totals odds later
                    }
    except Exception:
        pass

    # Fetch batter K rates (bulk, 2 API calls)
    print(f"\n  [9b/19] Fetching batter K rates + Savant pitcher rates...")
    batter_k_rates = fetch_batter_k_rates(season=season)
    pitch_hands = load_pitch_hands(season=season)
    savant_rates = fetch_savant_pitcher_rates(season=season)
    print(f"  {len(batter_k_rates)} batters, {len(savant_rates)} pitchers with Savant K%/whiff%")

    # Inject pitcher hand into adv_stats for props_engine K% lookup
    for pid_str, adv in adv_stats.items():
        try:
            adv["pitch_hand"] = pitch_hands.get(int(pid_str), "R")
        except (ValueError, TypeError):
            pass

    # Stage 10: Fetch team pitching stats
    print(f"\n  [10/19] Fetching team pitching stats...")
    team_pitching = fetch_team_pitching_stats(season=season)
    print(f"  {len(team_pitching)} teams with pitching stats")

    # Stage 11: Fetch game weather
    print(f"\n  [11/19] Fetching game weather...")
    weather_data = fetch_game_weather(date_iso)
    n_weather = len(weather_data)
    print(f"  Weather data for {n_weather} games")

    # Stage 12: Fetch prop lines (FanDuel + Odds API combined)
    # FanDuel has K + outs only. Odds API has K + outs + hits allowed + walks.
    # Use FanDuel as primary for K/outs, Odds API fills in HA/walks.
    print(f"\n  [12/19] Fetching prop lines (FanDuel + Odds API)...")
    fd_result = fetch_fanduel_mlb_props(date_key=date_key)
    if isinstance(fd_result, tuple):
        fd_props, game_hit_lines = fd_result
    else:
        fd_props = fd_result
        game_hit_lines = []

    # Always fetch Odds API for hits_allowed + walks (FanDuel doesn't have these)
    odds_api_props = fetch_mlb_pitcher_props(date_key=date_key)

    # Merge: FanDuel lines take priority for K/outs, Odds API fills in HA/walks
    fd_markets = {(p.get("player",""), p.get("market","")) for p in fd_props}
    merged_props = list(fd_props)
    for p in odds_api_props:
        key = (p.get("player",""), p.get("market",""))
        if key not in fd_markets:
            merged_props.append(p)

    prop_lines = merged_props
    n_fd = len(fd_props)
    n_api = len(prop_lines) - n_fd
    print(f"  {n_fd} from FanDuel + {n_api} from Odds API = {len(prop_lines)} total prop lines, {len(game_hit_lines)} game hit lines")

    # Drop prop lines for started games — those picks stay from previous run
    if started_teams:
        before = len(prop_lines)
        prop_lines = [p for p in prop_lines
                      if p.get("team", "") not in started_teams]
        dropped = before - len(prop_lines)
        if dropped:
            print(f"  Dropped {dropped} prop lines for started games")
        print(f"  {len(prop_lines)} prop lines remaining (unstarted games)")

    # Stage 13: Project pitcher props
    print(f"\n  [13/19] Projecting pitcher props (Kalman + advanced stats)...")
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
    )
    picks = [p for p in projections if p["pick"] != "PASS"]
    print(f"  {len(projections)} projections, {len(picks)} actionable picks")

    # Stage 14: Project total game hits
    print(f"\n  [14/19] Projecting total game hits...")
    game_hit_projections = project_game_hits(
        probable, pitcher_logs, team_batting, team_pitching,
        kalman_state, adv_stats, game_hit_lines,
        weather_by_game=weather_data,
    )
    game_hit_picks = [p for p in game_hit_projections if p.get("pick") != "PASS"]
    print(f"  {len(game_hit_projections)} game projections, {len(game_hit_picks)} picks")

    # Print Kalman summary
    print(kalman_summary(kalman_state, top_n=5, stat_key="k"))

    # Stage 15: Fetch batter data
    print(f"\n  [15/19] Fetching batter data (game logs, Savant, splits, park factors)...")
    batter_game_logs = fetch_batter_game_logs(season=season)
    n_batters_logs = len(batter_game_logs) if batter_game_logs else 0
    print(f"  {n_batters_logs} batters with game logs")
    savant_batter_rates = fetch_savant_batter_rates(season=season)
    print(f"  {len(savant_batter_rates)} batters with Savant rates")
    batter_splits_data = fetch_batter_splits(season=season)
    print(f"  {len(batter_splits_data)} batters with splits")
    park_factors = compute_park_factors(batter_game_logs)
    print(f"  {len(park_factors)} parks with factors")

    # Stage 16: Batter Kalman update
    print(f"\n  [16/19] Updating batter Kalman state...")
    batter_kalman = load_batter_kalman_state(BATTER_KALMAN_STATE_PATH)
    if not batter_kalman:
        batter_kalman = new_batter_kalman_state()
    n_batter_updated = batter_batch_update(batter_kalman, batter_game_logs)
    batter_apply_drift(batter_kalman, date_iso)
    n_batter_tracked = len(batter_kalman.get("players", {}))
    print(f"  Processed {n_batter_updated} batter-games, {n_batter_tracked} batters tracked")

    # Stage 17: Project batter total bases
    # Source priority: FanDuel -> Bovada -> Odds API (FD has no TB market,
    # Bovada is free and has TB, Odds API costs credits)
    print(f"\n  [17/19] Projecting batter total bases...")
    fd_batter_lines = fetch_fanduel_mlb_batter_props(date_str=date_iso)
    print(f"  {len(fd_batter_lines)} batter prop lines from FanDuel")

    bovada_batter_lines = []
    try:
        bovada_batter_lines = fetch_bovada_mlb_batter_props(date_str=date_iso) or []
        print(f"  {len(bovada_batter_lines)} batter prop lines from Bovada")
    except Exception as e:
        print(f"  [batter] Bovada fetch failed: {e}")

    # Merge FanDuel + Bovada first
    seen = set()
    batter_prop_lines = []
    for line in fd_batter_lines:
        key = (line.get("player", "").lower(), line.get("market", ""))
        if key not in seen:
            seen.add(key)
            batter_prop_lines.append(line)
    for line in bovada_batter_lines:
        key = (line.get("player", "").lower(), line.get("market", ""))
        if key not in seen:
            seen.add(key)
            batter_prop_lines.append(line)

    # Only fall back to Odds API if we have no TB lines (saves credits)
    if not batter_prop_lines:
        try:
            odds_batter_lines = fetch_mlb_batter_props(date_str=date_iso) or []
            print(f"  {len(odds_batter_lines)} batter prop lines from Odds API (fallback)")
            for line in odds_batter_lines:
                key = (line.get("player", "").lower(), line.get("market", ""))
                if key not in seen:
                    seen.add(key)
                    batter_prop_lines.append(line)
        except Exception as e:
            print(f"  [batter] Odds API fetch failed: {e}")

    print(f"  {len(batter_prop_lines)} merged batter prop lines")

    batter_projections = project_batter_tb(
        batter_logs=batter_game_logs,
        lineup_data=lineup_data,
        prop_lines=batter_prop_lines,
        kalman_state=batter_kalman,
        savant_batter_rates=savant_batter_rates,
        savant_pitcher_rates=savant_rates,
        batter_splits=batter_splits_data,
        probable_pitchers=probable,
        park_factors=park_factors,
        weather_by_game=weather_data,
        pitch_hands=pitch_hands,
    )
    batter_dashboard = format_batter_props_for_dashboard(batter_projections, date_iso)
    batter_picks = [p for p in batter_projections if p.get("pick") not in ("PASS", None)]
    print(f"  {len(batter_projections)} batter projections, {len(batter_picks)} actionable picks")

    # Stage 18: Output
    print(f"\n  [18/19] Writing output...")
    dashboard = format_props_for_dashboard(projections, date_str=date_iso)
    game_hits_dash = format_game_hits_for_dashboard(game_hit_projections, date_str=date_iso)

    # Merge pitcher props, game hits, and batter props into single output
    combined = {
        **dashboard,
        "game_hits": game_hits_dash.get("game_hits", []),
        "game_hits_picks": game_hit_picks,
        "batterProps": batter_dashboard.get("batterProps", []),
        "batterProjections": batter_dashboard.get("batterProjections", []),
    }

    # Output paths
    output_paths = [
        os.path.join(SCRIPT_DIR, "..", "data", "mlb-props.json"),
        os.path.join(SCRIPT_DIR, "..", "..", "PythonDashboard", "data", "mlb-props.json"),
    ]

    # Same merge logic as NBA: keep historical picks, replace today's
    import numpy as np

    class _NumpyEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                return float(obj)
            return super().default(obj)

    for path in output_paths:
        path = os.path.normpath(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)

        existing = {}
        existing_props = []
        try:
            if os.path.exists(path):
                with open(path, "r") as f:
                    existing = json.load(f)
                existing_props = existing.get("props", [])
        except Exception:
            existing = {}
            existing_props = []

        # Keep picks from OTHER dates (already graded / historical)
        kept = [p for p in existing_props if p.get("date") != date_iso]

        # For TODAY: keep existing picks for started games, replace unstarted
        today_existing_started = [
            p for p in existing_props
            if p.get("date") == date_iso and p.get("team", "") in started_teams
        ]
        today_fresh = [p for p in combined.get("props", [])
                       if p.get("date") == date_iso or not p.get("date")]

        merged_props = kept + today_existing_started + today_fresh

        combined_merged = {**existing, **combined, "props": merged_props}
        combined_merged["totalPicks"] = len(merged_props)

        n_kept = len(kept)
        n_new = len(today_fresh)
        if n_kept > 0:
            print(f"  Merged picks: {n_kept} historical + {n_new} fresh")

        with open(path, "w") as f:
            json.dump(combined_merged, f, indent=2, cls=_NumpyEncoder)
        print(f"  Wrote to {path}")

    # Stage 19: Save Kalman state
    prune_inactive_pitchers(kalman_state)
    save_pitcher_kalman_state(kalman_state, KALMAN_STATE_PATH)
    print(f"  Saved pitcher Kalman state ({len(kalman_state.get('pitchers', {}))} pitchers)")

    # Save batter Kalman state
    save_batter_kalman_state(batter_kalman, BATTER_KALMAN_STATE_PATH)
    prune_inactive_batters(batter_kalman, active_ids=[], max_inactive_days=30)
    print(f"  Saved batter Kalman state ({len(batter_kalman.get('players', {}))} batters)")

    # Print picks
    _print_picks(picks, game_hit_picks, batter_picks)

    return combined


def _print_picks(pitcher_picks, game_hit_picks, batter_picks=None):
    """Print formatted picks summary."""
    if batter_picks is None:
        batter_picks = []
    total = len(pitcher_picks) + len(game_hit_picks) + len(batter_picks)
    if total == 0:
        print("\n  No actionable picks today.")
        return

    print(f"\n{'='*60}")
    print(f"  TODAY'S PICKS ({total} total)")
    print(f"{'='*60}")

    # Pitcher props by market
    if pitcher_picks:
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

    # Game hits
    if game_hit_picks:
        print(f"\n  --- GAME HITS ({len(game_hit_picks)} picks) ---")
        for p in sorted(game_hit_picks, key=lambda x: -(x.get("pCover") or 0)):
            matchup = p.get("matchup", "???")
            edge_sign = "+" if (p.get("edge") or 0) > 0 else ""
            odds_str = _format_odds(p.get("odds"))
            w1u = p.get("to_win_1u")
            w1u_str = f"  w1u={w1u:.2f}u" if w1u is not None else ""
            print(
                f"    {matchup:30s} "
                f"{p.get('pick', ''):5s} {p.get('line', 0):6.1f}  "
                f"proj={p.get('proj', 0):6.1f}  edge={edge_sign}{p.get('edge', 0):5.1f}  "
                f"p={p.get('pCover', 0):.3f}  "
                f"{odds_str}{w1u_str}"
            )

    # Batter total bases
    if batter_picks:
        print(f"\n  --- BATTER TOTAL BASES ({len(batter_picks)} picks) ---")
        for p in sorted(batter_picks, key=lambda x: -(x.get("pCover") or 0)):
            import unicodedata
            safe_name = unicodedata.normalize('NFKD', p.get('player', '')).encode('ascii', 'ignore').decode('ascii')
            edge_sign = "+" if (p.get("edge") or 0) > 0 else ""
            odds_str = _format_odds(p.get("odds"))
            w1u = p.get("to_win_1u")
            w1u_str = f"  w1u={w1u:.2f}u" if w1u is not None else ""
            print(
                f"    {safe_name:25s} {p.get('team', ''):3s} "
                f"{p.get('pick', ''):5s} {p.get('line', 0):6.1f}  "
                f"proj={p.get('proj', 0):6.2f}  edge={edge_sign}{p.get('edge', 0):5.2f}  "
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
    parser = argparse.ArgumentParser(description="Daily MLB pitcher prop + game hits + batter TB projections (Kalman)")
    parser.add_argument("--date", type=str, default=None,
                        help="Date in YYYYMMDD format (default: today)")
    args = parser.parse_args()

    run_daily(date_key=args.date)
