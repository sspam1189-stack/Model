#!/usr/bin/env python3
"""
pyNBAPROPS/scripts/run_daily.py
Daily NBA player prop projection pipeline with Kalman filtering.

Stages:
  1. Load Kalman state (or initialize if first run)
  2. Fetch player game logs from NBA.com
  3. Update Kalman state with any new games
  4. Fetch team defensive stats for opponent adjustment
  5. Fetch today's prop lines from The Odds API
  6. Project all player props (Kalman-blended)
  7. Generate picks and write output
  8. Save updated Kalman state

Usage:
    cd pyNBAPROPS
    python -m scripts.run_daily
    python -m scripts.run_daily --date 20260325
"""

import sys
import os
import json
import argparse
import datetime

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from sources.nba_player_stats import (
    fetch_player_game_logs, fetch_team_def_stats, fetch_player_advanced_stats,
    fetch_player_positions, fetch_team_def_by_position, fetch_player_per36_stats,
)
from sources.odds_fanduel import fetch_fanduel_nba_props
from sources.odds_theoddsapi import fetch_nba_player_props
from props_engine import organize_player_logs, project_player_props, format_props_for_dashboard
from player_kalman import (
    load_player_kalman_state, save_player_kalman_state,
    new_player_kalman_state, batch_update_from_game_logs,
    apply_drift, kalman_summary, prune_inactive_players,
)
from defaults import current_season

# Path to persistent Kalman state
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KALMAN_STATE_PATH = os.path.join(SCRIPT_DIR, "..", "data", "kalman_state.json")


def run_daily(date_key=None):
    """Run the daily NBA player prop projection pipeline."""
    from zoneinfo import ZoneInfo

    if date_key is None:
        now = datetime.datetime.now(ZoneInfo("America/Chicago"))
        date_key = now.strftime("%Y%m%d")
        date_iso = now.strftime("%Y-%m-%d")
    else:
        date_iso = f"{date_key[:4]}-{date_key[4:6]}-{date_key[6:8]}"

    season = current_season()

    print(f"\n{'='*60}")
    print(f"  NBA PLAYER PROPS (Kalman) — {date_iso}")
    print(f"  Season: {season}")
    print(f"{'='*60}")

    # --- Stage 1: Load Kalman state ---
    print(f"\n  [1/7] Loading Kalman state...")
    kalman_state = load_player_kalman_state(KALMAN_STATE_PATH)
    n_players = len(kalman_state.get("players", {}))
    print(f"  Kalman state: {n_players} players tracked")

    # --- Stage 2: Fetch player game logs ---
    print(f"\n  [2/7] Fetching player game logs...")
    player_game_logs = fetch_player_game_logs(season=season)
    if not player_game_logs:
        print("  ERROR: No player game logs fetched. Exiting.")
        return

    player_logs = organize_player_logs(player_game_logs)
    print(f"  {len(player_logs)} players with game logs")

    # --- Stage 3: Update Kalman with new games ---
    print(f"\n  [3/7] Updating Kalman state with new games...")
    n_updated = batch_update_from_game_logs(kalman_state, player_game_logs)
    print(f"  Processed {n_updated} player-games")

    # Apply drift (uncertainty grows for players who haven't played recently)
    apply_drift(kalman_state, games_elapsed=1)

    # --- Stage 4: Fetch team defensive stats ---
    print(f"\n  [4/7] Fetching team defensive stats...")
    team_def = fetch_team_def_stats(season=season)
    print(f"  {len(team_def)} teams with defensive stats")

    # --- Stage 5: Fetch advanced player stats ---
    print(f"\n  [5/8] Fetching advanced player stats (USG%, TS%, PACE)...")
    adv_stats = fetch_player_advanced_stats(season=season)
    print(f"  {len(adv_stats)} players with advanced stats")

    # --- Stage 5b: Fetch player positions, positional defense, per-36 ---
    print(f"\n  [5b/8] Fetching player positions, positional defense, per-36 stats...")
    player_positions = fetch_player_positions(season=season)
    team_def_by_pos = fetch_team_def_by_position(season=season)
    player_per36 = fetch_player_per36_stats(season=season)
    print(f"  {len(player_positions)} positions, {len(team_def_by_pos)} teams pos-def, {len(player_per36)} per-36")

    # --- Stage 6: Fetch prop lines (FanDuel primary, Odds API fallback) ---
    print(f"\n  [6/8] Fetching prop lines (FanDuel primary)...")
    prop_lines = fetch_fanduel_nba_props(date_key=date_key)
    if not prop_lines:
        print(f"  FanDuel returned 0 lines, falling back to The Odds API...")
        prop_lines = fetch_nba_player_props(date_key=date_key)
    print(f"  {len(prop_lines)} prop lines fetched")

    # --- Stage 7: Project props ---
    print(f"\n  [7/8] Projecting player props (Kalman + positional defense)...")
    projections = project_player_props(
        player_logs,
        team_def_stats=team_def,
        prop_lines=prop_lines,
        kalman_state=kalman_state,
        player_adv_stats=adv_stats,
        player_positions=player_positions,
        team_def_by_pos=team_def_by_pos,
        player_per36=player_per36,
    )

    picks = [p for p in projections if p["pick"] != "PASS"]
    print(f"  {len(projections)} total projections, {len(picks)} actionable picks")

    # Print Kalman summary for top scorers
    print(kalman_summary(kalman_state, top_n=5, stat_key="pts"))

    # --- Stage 8: Output (merge with existing — preserve started/finished games) ---
    dashboard = format_props_for_dashboard(projections, date_str=date_iso)

    import numpy as np

    class _NumpyEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                return float(obj)
            return super().default(obj)

    output_paths = [
        os.path.join(SCRIPT_DIR, "..", "data", "nba-props.json"),
        os.path.join(SCRIPT_DIR, "..", "..", "PythonDashboard", "data", "nba-props.json"),
    ]

    # Merge: keep picks from previous runs (earlier dates / graded picks),
    # only replace picks for today's date
    for path in output_paths:
        path = os.path.normpath(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)

        # Load existing dashboard data if it exists
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
        # Replace picks from TODAY's date with fresh projections
        kept = [p for p in existing_props if p.get("date") != date_iso]
        today_picks = [p for p in dashboard.get("props", []) if p.get("date") == date_iso or not p.get("date")]

        merged_props = kept + today_picks

        # Preserve existing metadata (season, mode, model from backtest)
        # but update generated timestamp and totals
        dashboard_merged = {**existing, **dashboard, "props": merged_props}
        if existing.get("season"):
            dashboard_merged["season"] = existing["season"]
        if existing.get("mode"):
            dashboard_merged["mode"] = existing["mode"]
        if existing.get("model"):
            dashboard_merged["model"] = existing["model"]
        dashboard_merged["totalPicks"] = len(merged_props)

        n_kept = len(kept)
        n_new = len(today_picks)
        if n_kept > 0:
            print(f"  Merged: kept {n_kept} historical picks + {n_new} today's picks")

        with open(path, "w") as f:
            json.dump(dashboard_merged, f, indent=2, cls=_NumpyEncoder)
        print(f"  Wrote to {path}")

    # Save updated Kalman state
    prune_inactive_players(kalman_state)
    save_player_kalman_state(kalman_state, KALMAN_STATE_PATH)
    print(f"  Saved Kalman state ({len(kalman_state['players'])} players)")

    # Print picks
    _print_picks(picks)

    return dashboard


def _print_picks(picks):
    """Print formatted picks summary."""
    if not picks:
        print("\n  No actionable picks today.")
        return

    print(f"\n{'='*60}")
    print(f"  TODAY'S PICKS ({len(picks)} total)")
    print(f"{'='*60}")

    by_market = {}
    for p in picks:
        m = p["market"]
        if m not in by_market:
            by_market[m] = []
        by_market[m].append(p)

    for market, market_picks in sorted(by_market.items()):
        print(f"\n  --- {market.upper()} ({len(market_picks)} picks) ---")
        for p in sorted(market_picks, key=lambda x: -(x.get("pCover") or 0)):
            conf_marker = "*" if p["conf"] == "elite" else ""
            edge_sign = "+" if (p.get("edge") or 0) > 0 else ""
            print(
                f"    {p['player']:25s} {p['team']:3s} "
                f"{p['pick']:5s} {p['line']:6.1f}  "
                f"proj={p['proj']:6.1f}  edge={edge_sign}{p.get('edge', 0):5.1f}  "
                f"p={p.get('pCover', 0):.3f}{conf_marker}"
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Daily NBA player prop projections (Kalman)")
    parser.add_argument("--date", type=str, default=None,
                        help="Date in YYYYMMDD format (default: today)")
    args = parser.parse_args()

    run_daily(date_key=args.date)
