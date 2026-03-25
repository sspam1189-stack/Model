#!/usr/bin/env python3
"""
pyNFL/scripts/props_backtest.py
Walk-forward backtest of player prop projections.

For each week, projects player stats using only prior weeks' data,
then compares to actual results. Reports MAE and simulated over/under
hit rates (using rolling average as proxy line).
"""

import sys
import os
import math
import argparse
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from sources.nflfastr import fetch_pbp
from sources.nfl_stats import compute_team_stats_through_week
from props_engine import (
    build_player_game_logs, project_player_props,
    ROLLING_WINDOW, MIN_GAMES_PASSER, MIN_GAMES_RECEIVER, MIN_GAMES_RUSHER,
    PROP_PROB_HIGH, PROP_PROB_ELITE, MARKET_THRESHOLDS,
)

# Try to import historical props fetch
try:
    from sources.odds_theoddsapi import fetch_historical_player_props
    HAS_HIST_PROPS = True
except ImportError:
    HAS_HIST_PROPS = False


def backtest_props(seasons, start_week=4, use_real_lines=False):
    """
    Walk-forward backtest of player prop projections.

    For each week >= start_week, build projections using only data
    from weeks 1..(week-1), then compare to actual game stats.

    If use_real_lines=True and API key is available, fetches historical
    prop lines from The Odds API (costs credits).
    """
    results = {
        "pass_yds": {"projections": [], "actuals": [], "picks": []},
        "rush_yds": {"projections": [], "actuals": [], "picks": []},
        "rec_yds": {"projections": [], "actuals": [], "picks": []},
        "receptions": {"projections": [], "actuals": [], "picks": []},
    }

    total_games_projected = 0

    for season in seasons:
        print(f"\n{'='*60}")
        print(f"  SEASON {season}")
        print(f"{'='*60}")

        pbp = fetch_pbp(season)
        if pbp is None or pbp.empty:
            print(f"  No PBP data for {season}")
            continue

        # Get all weeks in this season
        weeks = sorted(pbp["week"].dropna().unique().astype(int))
        max_week = max(weeks)

        for week in weeks:
            if week < start_week:
                continue

            # --- Build projections using only prior weeks ---
            prior_pbp = pbp[pbp["week"] < week].copy()
            if prior_pbp.empty:
                continue

            # Build player game logs from prior data only
            prior_logs = build_player_game_logs(prior_pbp)

            # Team stats through prior week for opponent adjustment
            team_stats = compute_team_stats_through_week(pbp, through_week=week - 1)
            if not team_stats:
                continue

            # This week's actual data
            week_pbp = pbp[pbp["week"] == week].copy()
            if week_pbp.empty:
                continue
            actual_logs = build_player_game_logs(week_pbp)

            # Fetch real prop lines if available
            real_lines = None
            if use_real_lines and HAS_HIST_PROPS:
                try:
                    real_lines = fetch_historical_player_props(season, week)
                except Exception as e:
                    print(f"    [props] Historical fetch failed: {e}")

            # Project props (with real lines if available)
            projections = project_player_props(prior_logs, team_stats, prop_lines=real_lines)

            # Match projections to actuals
            week_picks = 0
            for proj in projections:
                player = proj["player"]
                market = proj["market"]

                # Find this player's actual stats for this week
                actual_val = _find_actual(player, market, actual_logs)
                if actual_val is None:
                    continue

                proj_val = proj["proj"]
                std = proj["std"]

                results[market]["projections"].append(proj_val)
                results[market]["actuals"].append(actual_val)

                # Use real line from projection if available, else simulate
                pick_line = proj.get("line")
                if pick_line is None:
                    pick_line = _get_simulated_line(player, market, prior_logs)
                if pick_line is not None and std > 0:
                    sim_line = pick_line
                    diff = proj_val - sim_line
                    from scipy.stats import t as t_dist
                    z = diff / std
                    p_over = float(t_dist.cdf(z, df=4))
                    p_under = 1.0 - p_over
                    best_p = max(p_over, p_under)

                    mkt_thresh = MARKET_THRESHOLDS.get(market, {"high": PROP_PROB_HIGH, "elite": PROP_PROB_ELITE})
                    if best_p >= mkt_thresh["high"]:
                        pick = "OVER" if p_over > p_under else "UNDER"
                        won = (pick == "OVER" and actual_val > sim_line) or \
                              (pick == "UNDER" and actual_val < sim_line)
                        if actual_val == sim_line:
                            continue  # Push
                        results[market]["picks"].append({
                            "season": season,
                            "week": week,
                            "player": player,
                            "proj": proj_val,
                            "line": sim_line,
                            "actual": actual_val,
                            "pick": pick,
                            "pCover": best_p,
                            "conf": "elite" if best_p >= mkt_thresh["elite"] else "high",
                            "won": won,
                        })
                        week_picks += 1

                total_games_projected += 1

            if week_picks > 0:
                wp = [p for p in results[market]["picks"]
                      if p["season"] == season and p["week"] == week]
                wins = sum(1 for p in wp if p["won"])
                losses = len(wp) - wins
                print(f"  {season} W{week:2d}: {week_picks} picks, {wins}W-{losses}L")

    # --- Print summary ---
    print(f"\n{'='*60}")
    print(f"  PROPS BACKTEST SUMMARY")
    print(f"{'='*60}")
    print(f"  Total player-weeks projected: {total_games_projected}\n")

    grand_w = grand_l = 0
    grand_units = 0.0

    for market, data in results.items():
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

        print(f"  {market:12s}: MAE={mae:6.1f}  corr={corr:.3f}  "
              f"picks={wins}W-{losses}L ({pct:.1f}%) {'+' if units >= 0 else ''}{units:.1f}u")

    print(f"\n  TOTAL: {grand_w}W-{grand_l}L "
          f"({grand_w / max(1, grand_w + grand_l) * 100:.1f}%) "
          f"{'+' if grand_units >= 0 else ''}{grand_units:.1f}u")


def _find_actual(player_name, market, actual_logs):
    """Find a player's actual stat value from this week's game logs."""
    stat_key = {
        "pass_yds": "pass_yds",
        "rush_yds": "rush_yds",
        "rec_yds": "rec_yds",
        "receptions": "receptions",
    }.get(market)

    if not stat_key:
        return None

    for pid, games in actual_logs.items():
        for g in games:
            if g.get("name") == player_name:
                val = g.get(stat_key)
                if val is not None:
                    return float(val)
    return None


def _get_simulated_line(player_name, market, prior_logs):
    """
    Simulate a market line using the player's simple rolling average.
    Markets roughly set lines near recent performance averages.
    """
    stat_key = {
        "pass_yds": "pass_yds",
        "rush_yds": "rush_yds",
        "rec_yds": "rec_yds",
        "receptions": "receptions",
    }.get(market)
    min_games = {
        "pass_yds": MIN_GAMES_PASSER,
        "rush_yds": MIN_GAMES_RUSHER,
        "rec_yds": MIN_GAMES_RECEIVER,
        "receptions": MIN_GAMES_RECEIVER,
    }.get(market, 3)

    if not stat_key:
        return None

    for pid, games in prior_logs.items():
        for g in games:
            if g.get("name") == player_name:
                # Found the player — get their recent games
                all_games = sorted(prior_logs[pid], key=lambda x: x.get("week", 0))
                recent = all_games[-ROLLING_WINDOW:]
                vals = [gg.get(stat_key, 0) for gg in recent if gg.get(stat_key) is not None]
                if len(vals) >= min_games:
                    return round(sum(vals) / len(vals), 1)
                return None
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest player prop projections")
    parser.add_argument("--seasons", nargs="+", type=int, default=[2024, 2025])
    parser.add_argument("--start-week", type=int, default=4,
                        help="First week to start projecting (need prior data)")
    parser.add_argument("--real-lines", action="store_true",
                        help="Fetch historical prop lines from The Odds API (costs credits)")
    args = parser.parse_args()

    backtest_props(args.seasons, args.start_week, use_real_lines=args.real_lines)
