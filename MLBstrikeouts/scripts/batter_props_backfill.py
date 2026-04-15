#!/usr/bin/env python3
"""
MLBstrikeouts/scripts/batter_props_backfill.py
Walk-forward backfill of MLB batter total bases projections with Kalman filtering.

For each game date, projects batter TB using only prior games' data,
then compares to actual results. The batter Kalman filter is trained incrementally.

Usage:
    cd MLBstrikeouts
    python -m scripts.batter_props_backfill
    python -m scripts.batter_props_backfill --season 2026 --start-date 2026-04-10
"""

import sys, os, csv, argparse, json
import numpy as np
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from sources.mlb_stats import (
    fetch_pitcher_game_logs, fetch_batter_game_logs,
    fetch_savant_batter_rates, fetch_batter_splits,
    fetch_savant_pitcher_rates, load_pitch_hands,
    fetch_player_bat_sides, fetch_lineup_handedness,
    CACHE_DIR,
)
from sources.park_factors import compute_park_factors
from sources.weather import fetch_game_weather
from batter_props_engine import project_batter_tb
from batter_kalman import (
    new_batter_kalman_state, load_batter_kalman_state,
    save_batter_kalman_state, prune_inactive_batters,
    batch_update_from_game_logs as batter_batch_update,
    apply_drift as batter_apply_drift,
)
from defaults import current_season


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------

def grade_batter_picks(projections, batter_game_logs, game_date):
    """
    Grade batter TB projections against actual TB from game logs.

    Parameters
    ----------
    projections : list[dict]
        Batter projections for a given date with keys: player, player_id,
        team, opp, line, proj, pCover, pick, edge, odds, to_win_1u.
    batter_game_logs : dict
        {batter_id: [game_log_dicts]} — full season logs.
    game_date : str
        Date to grade (YYYY-MM-DD).

    Returns
    -------
    list[dict]
        Graded picks with actual_tb and hit fields added.
    """
    # Index actual TB by player_id for this date
    actuals_by_id = {}
    for bid, logs in batter_game_logs.items():
        for g in logs:
            if g.get("game_date") == game_date:
                actuals_by_id[bid] = g.get("tb", 0)

    graded = []
    for proj in projections:
        pick = proj.get("pick")
        if pick in ("PASS", None):
            continue

        line = proj.get("line")
        if line is None:
            continue

        player_id = proj.get("player_id")
        actual_tb = actuals_by_id.get(player_id)
        if actual_tb is None:
            # Try string key
            actual_tb = actuals_by_id.get(str(player_id))
        if actual_tb is None:
            continue

        actual_tb = float(actual_tb)

        # Push — skip
        if actual_tb == line:
            continue

        won = (pick == "OVER" and actual_tb > line) or \
              (pick == "UNDER" and actual_tb < line)

        graded.append({
            "date": game_date,
            "player": proj.get("player", ""),
            "player_id": player_id,
            "team": proj.get("team", ""),
            "opp": proj.get("opp", ""),
            "line": line,
            "proj": proj.get("proj"),
            "pCover": proj.get("pCover"),
            "pick": pick,
            "actual_tb": actual_tb,
            "hit": 1 if won else 0,
            "edge": proj.get("edge", 0),
            "odds": proj.get("odds"),
            "won": won,
        })

    return graded


def _calc_pick_units(odds, won):
    """Calculate units for a single pick using actual odds."""
    if odds is not None:
        odds = int(odds)
        if won:
            return odds / 100.0 if odds > 0 else 100.0 / abs(odds)
        else:
            return -1.0 if odds > 0 else -abs(odds) / 100.0
    else:
        return 1.0 if won else -1.1


def _calc_units(picks):
    """Calculate total units from graded picks."""
    return sum(_calc_pick_units(p.get("odds"), p["won"]) for p in picks)


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------

def backfill(season=None, start_date=None, end_date=None):
    """
    Walk-forward backfill of batter total bases projections.

    For each game date:
      1. Build batter Kalman state up to that date (only prior games)
      2. Load historical batter prop lines from cache
      3. Compute park factors from games up to this date
      4. Run projection engine as if it were that date
      5. Grade against actual TB from game logs
      6. Update Kalman with today's games (after projecting)

    Parameters
    ----------
    season : int or None
        MLB season year (default: current_season()).
    start_date : str or None
        Start projecting from this date (YYYY-MM-DD). Default: 10 game dates in.
    end_date : str or None
        Stop projecting after this date (YYYY-MM-DD). Default: end of season.
    """
    season = season or current_season()
    date_label = f"from {start_date}" if start_date else "after 10 game dates"
    print(f"\n{'='*60}")
    print(f"  MLB BATTER TOTAL BASES BACKTEST (Kalman)")
    print(f"  Season: {season}  |  Start {date_label}")
    print(f"{'='*60}")

    # Load batter game logs
    print(f"\n  Loading batter game logs...")
    batter_game_logs = fetch_batter_game_logs(season=season)
    if not batter_game_logs:
        print("  No batter game logs found. Run fetch_stats first.")
        return None
    print(f"  {len(batter_game_logs)} batters with game logs")

    # Load pitcher game logs (needed for probable pitcher info)
    print(f"  Loading pitcher game logs...")
    pitcher_game_logs = fetch_pitcher_game_logs(season=season)

    # Load supplementary data (season-level, fetched once)
    print(f"  Loading Savant batter rates...")
    savant_batter_rates = fetch_savant_batter_rates(season=season)
    print(f"  {len(savant_batter_rates)} batters with Savant rates")

    print(f"  Loading batter splits...")
    batter_splits_data = fetch_batter_splits(season=season)
    print(f"  {len(batter_splits_data)} batters with splits")

    print(f"  Loading Savant pitcher rates + pitch hands...")
    savant_pitcher_rates = fetch_savant_pitcher_rates(season=season)
    pitch_hands = load_pitch_hands(season=season)
    print(f"  {len(savant_pitcher_rates)} pitchers with Savant rates")

    print(f"  Loading player bat sides...")
    bat_sides = fetch_player_bat_sides(season=season)
    print(f"  {len(bat_sides)} players with bat-side data")

    # Load batting orders cache for lineup_data per date
    from sources.mlb_stats import _load_cache as _lc
    bo_cache_path = CACHE_DIR / f"batting_orders_{season}.json"
    batting_orders_all = _lc(bo_cache_path, max_age_hours=None) or {}

    # Get all unique game dates from batter logs
    all_dates = set()
    for bid, logs in batter_game_logs.items():
        for g in logs:
            d = g.get("game_date", "")
            if d:
                all_dates.add(d)
    all_dates = sorted(all_dates)
    print(f"  {len(all_dates)} game dates in season")

    # Always reset batter Kalman state from scratch
    batter_kalman_path = os.path.normpath(
        os.path.join(SCRIPT_DIR, "..", "data", "batter_kalman_state.json")
    )
    if os.path.exists(batter_kalman_path):
        os.remove(batter_kalman_path)
        print(f"  [kalman] Reset: deleted {batter_kalman_path}")
    batter_kalman = new_batter_kalman_state()

    # Collect all graded picks
    all_picks = []
    total_projected = 0

    # --- Walk-forward loop ---
    for date_idx, game_date in enumerate(all_dates):

        # Respect end_date
        if end_date and game_date > end_date:
            break

        # Determine if we should start projecting yet
        should_skip = False
        if start_date:
            should_skip = game_date < start_date
        else:
            should_skip = date_idx < 10  # need ~2 weeks of daily play

        # Build today's batter logs for Kalman update (after projecting)
        today_batter_logs = {}
        for bid, logs in batter_game_logs.items():
            today = [g for g in logs if g.get("game_date") == game_date]
            if today:
                today_batter_logs[bid] = today

        if should_skip:
            # Not projecting yet — still update Kalman with this date's games
            if today_batter_logs:
                batter_batch_update(batter_kalman, batter_game_logs,
                                    up_to_date=game_date)
            continue

        # Phase 1: Build prior-only batter logs (no lookahead)
        prior_batter_logs = {}
        for bid, logs in batter_game_logs.items():
            prior = [g for g in logs if g.get("game_date", "") < game_date]
            if prior:
                prior_batter_logs[bid] = prior

        if not prior_batter_logs:
            if today_batter_logs:
                batter_batch_update(batter_kalman, batter_game_logs,
                                    up_to_date=game_date)
            continue

        # Phase 2: Compute park factors from prior games only
        park_factors = compute_park_factors(prior_batter_logs)

        # Phase 2b: Load historical prop lines from cache
        prop_lines = None
        try:
            from sources.odds_theoddsapi import _props_cache_path, _load_cache
            date_key = game_date.replace("-", "")
            cp = _props_cache_path(date_key, market="batter_total_bases")
            cached = _load_cache(cp, max_age_hours=None)
            if cached is not None:
                prop_lines = cached
        except Exception:
            pass  # No cached props for this date

        # If no cached batter props, try FanDuel cache
        if not prop_lines:
            try:
                fd_cache = CACHE_DIR / "props_cache" / "mlb" / f"fd_batter_props_{game_date.replace('-','')}.json"
                if fd_cache.exists():
                    with open(fd_cache) as f:
                        prop_lines = json.load(f)
            except Exception:
                pass

        if not prop_lines:
            # No prop lines available — skip projection for this date
            # Still update Kalman
            if today_batter_logs:
                batter_batch_update(batter_kalman, batter_game_logs,
                                    up_to_date=game_date)
            continue

        # Phase 2c: Fetch weather + lineup data for this date
        weather_data = fetch_game_weather(game_date)
        lineup_hand = fetch_lineup_handedness(game_date, bat_sides=bat_sides, season=season)

        # Build lineup_data from batting orders
        date_lineup_data = {}
        date_bo = batting_orders_all.get(game_date, {})
        for abbr, order in date_bo.items():
            date_lineup_data[abbr] = {"player_ids": order}

        # Build probable_pitchers from pitcher game logs for this date
        date_probable = []
        if pitcher_game_logs:
            today_starters = [
                g for g in pitcher_game_logs
                if g.get("game_date", "") == game_date
                and float(g.get("IP", g.get("ip", 0))) >= 3.0
            ]
            games_by_id = defaultdict(list)
            for g in today_starters:
                gid = g.get("game_id", "")
                if gid:
                    games_by_id[gid].append(g)
            for gid, starters in games_by_id.items():
                gm = {"game_id": gid}
                for g in starters:
                    pid = g.get("pitcher_id") or g.get("player_id")
                    pname = g.get("pitcher_name") or g.get("player_name", "")
                    team = g.get("team", "")
                    opp = g.get("opp", g.get("opponent", ""))
                    is_home = g.get("is_home", False)
                    if is_home:
                        gm["home_team"] = team
                        gm["away_team"] = opp
                        gm["home_pitcher_id"] = pid
                        gm["home_pitcher_name"] = pname
                    else:
                        gm["away_team"] = team
                        gm["home_team"] = opp
                        gm["away_pitcher_id"] = pid
                        gm["away_pitcher_name"] = pname
                date_probable.append(gm)

        # Phase 3: Project batter TB
        try:
            projections = project_batter_tb(
                batter_logs=prior_batter_logs,
                lineup_data=date_lineup_data,
                prop_lines=prop_lines,
                kalman_state=batter_kalman,
                savant_batter_rates=savant_batter_rates,
                savant_pitcher_rates=savant_pitcher_rates,
                batter_splits=batter_splits_data,
                probable_pitchers=date_probable,
                park_factors=park_factors,
                weather_by_game=weather_data,
                pitch_hands=pitch_hands,
            )
        except Exception as e:
            print(f"  {game_date}: ERROR projecting — {e}")
            if today_batter_logs:
                batter_batch_update(batter_kalman, batter_game_logs,
                                    up_to_date=game_date)
            continue

        total_projected += len(projections)

        # Phase 4: Grade projections against actuals
        graded = grade_batter_picks(projections, batter_game_logs, game_date)
        all_picks.extend(graded)

        # Phase 5: Update Kalman with today's actual games (after projecting)
        if today_batter_logs:
            batter_batch_update(batter_kalman, batter_game_logs,
                                up_to_date=game_date)

        if graded:
            wins_today = sum(1 for p in graded if p["won"])
            losses_today = len(graded) - wins_today
            print(f"  {game_date}: {len(graded)} picks ({wins_today}W-{losses_today}L)")

    # --- Save fully warmed Kalman state ---
    prune_inactive_batters(batter_kalman, active_ids=[], max_inactive_days=30)
    save_batter_kalman_state(batter_kalman, batter_kalman_path)
    n_tracked = len(batter_kalman.get("players", {}))
    print(f"\n  Saved batter Kalman state ({n_tracked} batters) to {batter_kalman_path}")

    # --- Write results to CSV ---
    _write_csv(all_picks, season)

    # --- Summary ---
    _print_summary(all_picks, total_projected, season)

    return all_picks


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _write_csv(picks, season):
    """Write graded picks to CSV."""
    backfill_dir = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "backfill"))
    os.makedirs(backfill_dir, exist_ok=True)

    csv_path = os.path.join(backfill_dir, f"batter_tb_backfill_{season}.csv")
    fieldnames = [
        "date", "player", "player_id", "team", "opp",
        "line", "proj", "pCover", "pick", "actual_tb", "hit", "edge",
    ]

    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for p in picks:
            writer.writerow(p)

    print(f"  Wrote {len(picks)} graded picks to {csv_path}")


def _print_summary(picks, total_projected, season):
    """Print formatted backfill summary."""
    print(f"\n{'='*60}")
    print(f"  MLB BATTER TOTAL BASES BACKTEST SUMMARY — {season}")
    print(f"{'='*60}")
    print(f"  Total batter-games projected: {total_projected}")
    print(f"  Graded picks: {len(picks)}")

    if not picks:
        print("  No graded picks to summarize.")
        return

    wins = sum(1 for p in picks if p["won"])
    losses = len(picks) - wins
    units = _calc_units(picks)
    pct = wins / max(1, wins + losses) * 100

    print(f"\n  TOTAL: {wins}W-{losses}L ({pct:.1f}%) "
          f"{'+'if units >= 0 else ''}{units:.1f}u")

    # Breakdown by direction
    for direction in ("OVER", "UNDER"):
        dir_picks = [p for p in picks if p["pick"] == direction]
        if not dir_picks:
            continue
        d_wins = sum(1 for p in dir_picks if p["won"])
        d_losses = len(dir_picks) - d_wins
        d_units = _calc_units(dir_picks)
        d_pct = d_wins / max(1, d_wins + d_losses) * 100
        print(f"  {direction:5s}: {d_wins}W-{d_losses}L ({d_pct:.1f}%) "
              f"{'+'if d_units >= 0 else ''}{d_units:.1f}u")

    # Calibration by pCover bucket
    print(f"\n  Calibration (pCover bucket → actual hit rate):")
    buckets = [(0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 1.0)]
    for lo, hi in buckets:
        bucket_picks = [p for p in picks if lo <= (p.get("pCover") or 0) < hi]
        if not bucket_picks:
            continue
        b_wins = sum(1 for p in bucket_picks if p["won"])
        b_pct = b_wins / len(bucket_picks) * 100
        mid = (lo + hi) / 2 * 100
        print(f"    pCover {lo:.2f}-{hi:.2f} (expected ~{mid:.0f}%): "
              f"{b_wins}/{len(bucket_picks)} = {b_pct:.1f}%")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Backtest MLB batter total bases projections (Kalman)"
    )
    parser.add_argument("--season", type=int, default=None,
                        help="MLB season year (e.g. 2026)")
    parser.add_argument("--start-date", type=str, default=None,
                        help="Start projecting from this date (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=str, default=None,
                        help="Stop projecting after this date (YYYY-MM-DD)")
    args = parser.parse_args()

    backfill(args.season, start_date=args.start_date, end_date=args.end_date)


if __name__ == "__main__":
    main()
