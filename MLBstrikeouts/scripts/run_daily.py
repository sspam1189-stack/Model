#!/usr/bin/env python3
"""
MLBstrikeouts/scripts/run_daily.py
Daily MLB pitcher prop + game hits projection pipeline with Kalman filtering.

Stages:
  0. Grade previous picks
  1. Load Kalman state (or initialize if first run)
  2. Fetch pitcher game logs from MLB Stats API
  3. Update Kalman state with any new games
  4. Fetch pitcher advanced stats (K/9, BB/9, WHIP)
  5. Fetch pitcher sabermetrics (FIP, xFIP)
  6. Fetch pitcher handedness splits (vs LHB/RHB)
  7. Fetch team batting stats (K%, BA, OPS, BB%)
  8. Fetch team pitching stats (for game hits model)
  9. Fetch today's probable pitchers + schedule
  10. Fetch prop lines (FanDuel primary, Odds API fallback)
  11. Project all pitcher props (K, outs, hits, walks)
  12. Project total game hits
  13. Generate picks, write output
  14. Save updated Kalman state

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
)
from sources.odds_fanduel import fetch_fanduel_mlb_props
from sources.odds_theoddsapi import fetch_mlb_pitcher_props
from props_engine import organize_pitcher_logs, project_pitcher_props, format_props_for_dashboard, STAT_KEYS
from game_hits_engine import project_game_hits, format_game_hits_for_dashboard
from pitcher_kalman import (
    load_pitcher_kalman_state, save_pitcher_kalman_state,
    new_pitcher_kalman_state, batch_update_from_game_logs,
    apply_drift, kalman_summary, prune_inactive_pitchers,
)
from defaults import current_season

# Path to persistent Kalman state
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KALMAN_STATE_PATH = os.path.join(SCRIPT_DIR, "..", "data", "kalman_state.json")


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
    print(f"  MLB PITCHER PROPS + GAME HITS — {date_iso}")
    print(f"  Season: {season}")
    print(f"{'='*60}")

    # Stage 0: Grade previous picks
    print(f"\n  [0/14] Grading previous picks...")
    grade_previous_picks(season)

    # Stage 1: Load Kalman state
    print(f"\n  [1/14] Loading Kalman state...")
    kalman_state = load_pitcher_kalman_state(KALMAN_STATE_PATH)
    n_pitchers = len(kalman_state.get("players", {}))
    print(f"  Kalman state: {n_pitchers} pitchers tracked")

    # Stage 2: Fetch pitcher game logs
    print(f"\n  [2/14] Fetching pitcher game logs...")
    pitcher_game_logs = fetch_pitcher_game_logs(season=season)
    if not pitcher_game_logs:
        print("  ERROR: No pitcher game logs fetched. Exiting.")
        return
    pitcher_logs = organize_pitcher_logs(pitcher_game_logs)
    print(f"  {len(pitcher_logs)} pitchers with game logs")

    # Stage 3: Update Kalman with new games
    print(f"\n  [3/14] Updating Kalman state with new games...")
    n_updated = batch_update_from_game_logs(kalman_state, pitcher_game_logs)
    print(f"  Processed {n_updated} pitcher-games")
    apply_drift(kalman_state, days_elapsed=1)

    # Stage 4: Fetch advanced stats
    print(f"\n  [4/14] Fetching pitcher advanced stats...")
    adv_stats = fetch_pitcher_advanced_stats(season=season)
    print(f"  {len(adv_stats)} pitchers with advanced stats")

    # Stage 5: Fetch sabermetrics
    print(f"\n  [5/14] Fetching pitcher sabermetrics (FIP, xFIP)...")
    saber_stats = fetch_pitcher_sabermetrics(season=season)
    print(f"  {len(saber_stats)} pitchers with sabermetrics")

    # Stage 6: Fetch probable pitchers
    print(f"\n  [6/14] Fetching today's probable pitchers...")
    probable = fetch_today_probable_pitchers(date_str=date_iso)
    print(f"  {len(probable)} games with probable pitchers")

    # Stage 7: Fetch handedness splits for probable starters only
    print(f"\n  [7/14] Fetching handedness splits for probable starters...")
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
    print(f"\n  [8/14] Fetching team batting stats...")
    team_batting = fetch_team_batting_stats(season=season)
    print(f"  {len(team_batting)} teams with batting stats")

    # Stage 9: Fetch team pitching stats
    print(f"\n  [9/14] Fetching team pitching stats...")
    team_pitching = fetch_team_pitching_stats(season=season)
    print(f"  {len(team_pitching)} teams with pitching stats")

    # Stage 10: Fetch prop lines (FanDuel + Odds API combined)
    # FanDuel has K + outs only. Odds API has K + outs + hits allowed + walks.
    # Use FanDuel as primary for K/outs, Odds API fills in HA/walks.
    print(f"\n  [10/14] Fetching prop lines (FanDuel + Odds API)...")
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

    # Stage 11: Project pitcher props
    print(f"\n  [11/14] Projecting pitcher props (Kalman + advanced stats)...")
    projections = project_pitcher_props(
        pitcher_logs,
        team_batting_stats=team_batting,
        prop_lines=prop_lines,
        kalman_state=kalman_state,
        pitcher_adv_stats=adv_stats,
        pitcher_sabermetrics=saber_stats,
        pitcher_splits=splits,
        probable_pitchers=probable,
        injury_report=None,  # TODO: wire in MLB injury report
    )
    picks = [p for p in projections if p["pick"] != "PASS"]
    print(f"  {len(projections)} projections, {len(picks)} actionable picks")

    # Stage 12: Project total game hits
    print(f"\n  [12/14] Projecting total game hits...")
    game_hit_projections = project_game_hits(
        probable, pitcher_logs, team_batting, team_pitching,
        kalman_state, adv_stats, game_hit_lines,
    )
    game_hit_picks = [p for p in game_hit_projections if p.get("pick") != "PASS"]
    print(f"  {len(game_hit_projections)} game projections, {len(game_hit_picks)} picks")

    # Print Kalman summary
    print(kalman_summary(kalman_state, top_n=5, stat_key="k"))

    # Stage 13: Output
    print(f"\n  [13/14] Writing output...")
    dashboard = format_props_for_dashboard(projections, date_str=date_iso)
    game_hits_dash = format_game_hits_for_dashboard(game_hit_projections, date_str=date_iso)

    # Merge pitcher props and game hits into single output
    combined = {
        **dashboard,
        "game_hits": game_hits_dash.get("game_hits", []),
        "game_hits_picks": game_hit_picks,
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

        # For TODAY's picks: replace with fresh projections
        today_fresh = [p for p in combined.get("props", [])
                       if p.get("date") == date_iso or not p.get("date")]

        merged_props = kept + today_fresh

        combined_merged = {**existing, **combined, "props": merged_props}
        combined_merged["totalPicks"] = len(merged_props)

        n_kept = len(kept)
        n_new = len(today_fresh)
        if n_kept > 0:
            print(f"  Merged picks: {n_kept} historical + {n_new} fresh")

        with open(path, "w") as f:
            json.dump(combined_merged, f, indent=2, cls=_NumpyEncoder)
        print(f"  Wrote to {path}")

    # Stage 14: Save Kalman state
    prune_inactive_pitchers(kalman_state)
    save_pitcher_kalman_state(kalman_state, KALMAN_STATE_PATH)
    print(f"  Saved Kalman state ({len(kalman_state.get('pitchers', {}))} pitchers)")

    # Print picks
    _print_picks(picks, game_hit_picks)

    return combined


def _print_picks(pitcher_picks, game_hit_picks):
    """Print formatted picks summary."""
    total = len(pitcher_picks) + len(game_hit_picks)
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


def _format_odds(price):
    """Format American odds with +/- prefix."""
    if price is None:
        return "odds=N/A"
    price = int(price)
    if price > 0:
        return f"odds=+{price}"
    return f"odds={price}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Daily MLB pitcher prop + game hits projections (Kalman)")
    parser.add_argument("--date", type=str, default=None,
                        help="Date in YYYYMMDD format (default: today)")
    args = parser.parse_args()

    run_daily(date_key=args.date)
