#!/usr/bin/env python3
"""
pyNBAPROPS/scripts/props_backfill.py
Walk-forward backfill of NBA player prop projections with Kalman filtering.

For each game date, projects player stats using only prior games' data,
then compares to actual results. The Kalman filter is trained incrementally
as games are processed (no lookahead).

Usage:
    python -m scripts.props_backfill
    python -m scripts.props_backfill --season 2025-26 --start-game 15
    python -m scripts.props_backfill --real-lines
"""

import sys
import os
import math
import argparse
import json
import numpy as np
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from sources.nba_player_stats import (
    fetch_player_game_logs, fetch_team_def_stats, fetch_player_advanced_stats,
    fetch_player_positions, fetch_team_def_by_position, fetch_player_per36_stats,
)
from props_engine import (
    organize_player_logs, project_player_props, _name_key, _weighted_avg,
    STAT_KEYS,
)
from player_kalman import (
    new_player_kalman_state, batch_update_from_game_logs,
    apply_drift, kalman_summary, PLAYER_KALMAN_DEFAULTS,
)
from defaults import (
    ROLLING_WINDOW, MIN_GAMES, MIN_MINUTES,
)


def backfill(season="2025-26", start_game=15, start_date=None, use_real_lines=True):
    """
    Walk-forward backfill with Kalman filter trained incrementally.
    Always uses real prop lines — fetches from Odds API and caches if not already cached.
    Always resets Kalman state from scratch (no prior history).

    For each game date:
      1. Project using prior data + current Kalman state (no lookahead)
      2. Compare projections to actuals
      3. Update Kalman state with today's actual results
      4. Move to next date

    Parameters
    ----------
    start_date : str or None
        If set (YYYY-MM-DD), start projecting from this date instead of
        using start_game. Games before this date still train the Kalman.
    """
    date_label = f"from {start_date}" if start_date else f"after game #{start_game}"
    print(f"\n{'='*60}")
    print(f"  NBA PLAYER PROPS BACKTEST (Bayesian Kalman)")
    print(f"  Season: {season}  |  Start {date_label}")
    print(f"{'='*60}")

    # Load player game logs (from cache if available — run fetch_stats.py first)
    print(f"\n  Loading player game logs...")
    all_logs = fetch_player_game_logs(season=season)
    if not all_logs:
        print("  No player game logs found. Run: python -m scripts.fetch_stats --season " + season)
        return None

    # Load team defensive stats (from cache if available)
    print(f"  Loading team defensive stats...")
    team_def = fetch_team_def_stats(season=season)

    # Load advanced player stats (from cache if available)
    print(f"  Loading advanced player stats...")
    adv_stats = fetch_player_advanced_stats(season=season)

    # Load player positions, positional defense, per-36
    print(f"  Loading player positions, positional defense, per-36 stats...")
    player_positions = fetch_player_positions(season=season)
    team_def_by_pos = fetch_team_def_by_position(season=season)
    player_per36 = fetch_player_per36_stats(season=season)

    # Organize by player
    player_logs = organize_player_logs(all_logs)
    print(f"  {len(player_logs)} players, {len(all_logs)} total game logs")

    # Get all unique game dates
    all_dates = sorted(set(g.get("game_date", "") for g in all_logs if g.get("game_date")))
    print(f"  {len(all_dates)} game dates in season")

    # Always reset Kalman state from scratch — delete any persisted state
    kalman_state_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "kalman_state.json")
    kalman_state_path = os.path.normpath(kalman_state_path)
    if os.path.exists(kalman_state_path):
        os.remove(kalman_state_path)
        print(f"  [kalman] Reset: deleted {kalman_state_path}")
    kalman_state = new_player_kalman_state()

    # All markets to track
    all_markets = list(STAT_KEYS.keys()) + ["pts_rebs_asts"]
    results = {m: {"projections": [], "actuals": [], "picks": []} for m in all_markets}
    total_projected = 0

    # --- Walk-forward loop ---
    for date_idx, game_date in enumerate(all_dates):

        # Phase 1: Train Kalman on all games BEFORE this date
        # (first pass: batch-update on prior dates not yet processed)
        prior_date_logs = [g for g in all_logs if g.get("game_date", "") == game_date
                           and g.get("min", 0) >= 10]

        # Determine if we should start projecting yet
        should_skip = False
        if start_date:
            # Use date-based start: skip projection until we reach start_date
            should_skip = game_date < start_date
        else:
            # Use game-index-based start
            should_skip = date_idx < start_game

        if should_skip:
            # Not projecting yet, but still update Kalman with this date's games
            if prior_date_logs:
                batch_update_from_game_logs(kalman_state, prior_date_logs)
            continue

        # Phase 2: Build prior-only player logs for projection
        prior_logs = {}
        actual_games = {}

        for pid, games in player_logs.items():
            prior = [g for g in games if g.get("game_date", "") < game_date]
            today = [g for g in games if g.get("game_date", "") == game_date]

            if prior and len(prior) >= min(MIN_GAMES.values()):
                prior_logs[pid] = prior
            if today:
                actual_games[pid] = today[0]

        if not prior_logs or not actual_games:
            # Still update Kalman with today's games
            if prior_date_logs:
                batch_update_from_game_logs(kalman_state, prior_date_logs)
            continue

        # Phase 3: Load real prop lines — fetch from Odds API + cache if missing
        real_lines = None
        try:
            from sources.odds_theoddsapi import _props_cache_path, _load_cache, fetch_historical_nba_props
            date_key = game_date.replace("-", "")
            cp = _props_cache_path(date_key)
            cached = _load_cache(cp, max_age_hours=None)
            if cached is not None:
                real_lines = cached
            else:
                print(f"  [{game_date}] No cached props — fetching from Odds API...")
                real_lines = fetch_historical_nba_props(game_date)
                if not real_lines:
                    print(f"  [{game_date}] No props from API — skipping date")
        except Exception as e:
            print(f"  [{game_date}] Props fetch error: {e}")

        # Phase 4: Project props using prior data + current Kalman state + advanced stats
        projections = project_player_props(
            prior_logs, team_def,
            prop_lines=real_lines,
            kalman_state=kalman_state,
            player_adv_stats=adv_stats,
            today_games=actual_games,
            player_positions=player_positions,
            team_def_by_pos=team_def_by_pos,
            player_per36=player_per36,
        )

        # Phase 5: Grade projections against actuals
        # Use pick/conf/pCover directly from project_player_props so all gate
        # logic (directional PRA gate, UNDER_ONLY_MARKETS, edge filters, etc.)
        # is applied consistently with the live model.
        date_picks = 0
        for proj in projections:
            player = proj["player"]
            market = proj["market"]

            actual_val = _find_actual(player, market, actual_games, player_logs)
            if actual_val is None:
                continue

            proj_val = proj["proj"]
            std = proj["std"]

            results[market]["projections"].append(proj_val)
            results[market]["actuals"].append(actual_val)

            # Skip if no active pick from the engine
            pick = proj.get("pick")
            if pick in ("PASS", None):
                total_projected += 1
                continue

            pick_line = proj.get("line")
            if pick_line is None:
                total_projected += 1
                continue

            if actual_val == pick_line:
                total_projected += 1
                continue  # Push

            won = (pick == "OVER" and actual_val > pick_line) or \
                  (pick == "UNDER" and actual_val < pick_line)

            results[market]["picks"].append({
                "date": game_date,
                "player": player,
                "team": proj.get("team", ""),
                "opp": proj.get("opp", ""),
                "proj": proj_val,
                "std": std,
                "line": pick_line,
                "actual": actual_val,
                "pick": pick,
                "pCover": proj.get("pCover"),
                "conf": proj.get("conf"),
                "won": won,
            })
            date_picks += 1
            total_projected += 1

        # Phase 6: UPDATE Kalman with today's actual games (after projecting)
        if prior_date_logs:
            batch_update_from_game_logs(kalman_state, prior_date_logs)

        if date_picks > 0 and date_idx % 10 == 0:
            print(f"  {game_date}: {date_picks} picks")

    # --- Save Kalman state so run_daily can pick up from here ---
    from player_kalman import save_player_kalman_state, prune_inactive_players
    prune_inactive_players(kalman_state)
    save_player_kalman_state(kalman_state, kalman_state_path)
    print(f"  Saved Kalman state ({len(kalman_state['players'])} players) to {kalman_state_path}")

    # --- Summary ---
    print(kalman_summary(kalman_state, top_n=10, stat_key="pts"))
    _print_summary(results, total_projected, season)

    return results


def _find_actual(player_name, market, actual_games, player_logs):
    """Find a player's actual stat value from today's games."""
    stat_key = STAT_KEYS.get(market)

    if market == "pts_rebs_asts":
        for pid, g in actual_games.items():
            if g.get("player_name") == player_name:
                return float(g.get("pts", 0) + g.get("reb", 0) + g.get("ast", 0))
        return None

    if not stat_key:
        return None

    for pid, g in actual_games.items():
        if g.get("player_name") == player_name:
            val = g.get(stat_key)
            if val is not None:
                return float(val)
    return None



def _print_summary(results, total_projected, season):
    """Print formatted backfill summary."""
    print(f"\n{'='*60}")
    print(f"  NBA PROPS BACKTEST SUMMARY (Kalman) — {season}")
    print(f"{'='*60}")
    print(f"  Total player-games projected: {total_projected}\n")

    grand_w = grand_l = 0
    grand_units = 0.0

    for market in results:
        data = results[market]
        projs = np.array(data["projections"])
        acts = np.array(data["actuals"])
        picks = data["picks"]

        if len(projs) == 0:
            continue

        mae = np.mean(np.abs(projs - acts))
        corr = np.corrcoef(projs, acts)[0, 1] if len(projs) > 1 else 0

        wins = sum(1 for p in picks if p["won"])
        losses = len(picks) - wins
        units = wins * 1.0 + losses * (-1.1)
        pct = wins / max(1, wins + losses) * 100

        grand_w += wins
        grand_l += losses
        grand_units += units

        print(f"  {market:14s}: MAE={mae:6.1f}  corr={corr:.3f}  "
              f"picks={wins}W-{losses}L ({pct:.1f}%) {'+' if units >= 0 else ''}{units:.1f}u")

    print(f"\n  TOTAL: {grand_w}W-{grand_l}L "
          f"({grand_w / max(1, grand_w + grand_l) * 100:.1f}%) "
          f"{'+' if grand_units >= 0 else ''}{grand_units:.1f}u")


def write_dashboard_json(results, season):
    """Write graded backfill picks to dashboard JSON files."""
    all_picks = []
    for market, data in results.items():
        for p in data["picks"]:
            all_picks.append({
                "player": p["player"],
                "team": p.get("team", ""),
                "opp": p.get("opp", ""),
                "market": market,
                "proj": p["proj"],
                "std": p.get("std", 0),
                "line": p["line"],
                "pick": p["pick"],
                "edge": round(p["proj"] - p["line"], 1),
                "pCover": p["pCover"],
                "conf": p["conf"],
                "actual": p["actual"],
                "result": "WIN" if p["won"] else "LOSS",
                "date": p.get("date", ""),
            })

    all_picks.sort(key=lambda x: (-(x.get("pCover") or 0), x.get("date", "")))

    total_w = sum(1 for p in all_picks if p["result"] == "WIN")
    total_l = sum(1 for p in all_picks if p["result"] == "LOSS")
    units = total_w * 1.0 + total_l * (-1.1)

    dashboard = {
        "sport": "nba",
        "type": "player_props",
        "season": season,
        "mode": "backfill",
        "model": "kalman_blend",
        "generated": datetime.now().isoformat(),
        "totalProjections": sum(len(d["projections"]) for d in results.values()),
        "totalPicks": len(all_picks),
        "props": all_picks,
        "summary": (
            f"{len(all_picks)} graded picks for {season}: "
            f"{total_w}W-{total_l}L ({total_w / max(1, total_w + total_l) * 100:.1f}%) "
            f"{'+' if units >= 0 else ''}{units:.1f}u"
        ),
    }

    class _NumpyEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                return float(obj)
            return super().default(obj)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    paths = [
        os.path.join(script_dir, "..", "data", "nba-props.json"),
        os.path.join(script_dir, "..", "..", "PythonDashboard", "data", "nba-props.json"),
    ]

    # Collect all backfill dates so we know which are "historical"
    backfill_dates = set(p.get("date", "") for p in all_picks)

    for path in paths:
        path = os.path.normpath(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)

        # Merge: preserve any live/today's picks that aren't in backfill date range
        existing_live_picks = []
        try:
            if os.path.exists(path):
                with open(path, "r") as f:
                    existing = json.load(f)
                existing_live_picks = [
                    p for p in existing.get("props", [])
                    if p.get("date") not in backfill_dates
                ]
        except Exception:
            existing_live_picks = []

        merged_props = all_picks + existing_live_picks
        dashboard["props"] = merged_props
        dashboard["totalPicks"] = len(merged_props)

        n_bt = len(all_picks)
        n_live = len(existing_live_picks)
        if n_live > 0:
            print(f"  Merged: {n_bt} backfill picks + {n_live} live picks preserved")

        with open(path, "w") as f:
            json.dump(dashboard, f, indent=2, cls=_NumpyEncoder)
        print(f"  Wrote {len(merged_props)} picks to {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest NBA player prop projections (Kalman)")
    parser.add_argument("--season", type=str, default="2025-26",
                        help="NBA season string (e.g. '2025-26')")
    parser.add_argument("--start-game", type=int, default=15,
                        help="Min game dates before projecting (default: 15)")
    parser.add_argument("--start-date", type=str, default=None,
                        help="Start projecting from this date (YYYY-MM-DD, e.g. '2026-01-01')")
    args = parser.parse_args()

    results = backfill(args.season, args.start_game, start_date=args.start_date)
    if results:
        write_dashboard_json(results, args.season)
