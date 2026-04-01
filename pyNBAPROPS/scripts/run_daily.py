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
    fetch_player_positions, fetch_team_def_by_position,
    fetch_player_per100_stats,
)
from sources.odds_fanduel import fetch_fanduel_nba_props
from sources.odds_theoddsapi import fetch_nba_player_props
from props_engine import organize_player_logs, project_player_props, format_props_for_dashboard, STAT_KEYS
from sources.game_context import load_injury_report
from player_kalman import (
    load_player_kalman_state, save_player_kalman_state,
    new_player_kalman_state, batch_update_from_game_logs,
    apply_drift, kalman_summary, prune_inactive_players,
)
from defaults import current_season

# Path to persistent Kalman state
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KALMAN_STATE_PATH = os.path.join(SCRIPT_DIR, "..", "data", "kalman_state.json")


def grade_previous_picks(season=None):
    """Grade ungraded picks from previous dates using actual game logs."""
    from collections import defaultdict

    output_paths = [
        os.path.join(SCRIPT_DIR, "..", "data", "nba-props.json"),
        os.path.join(SCRIPT_DIR, "..", "..", "PythonDashboard", "data", "nba-props.json"),
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

    # Fetch player game logs for the season
    if season is None:
        from defaults import current_season
        season = current_season()
    all_logs = fetch_player_game_logs(season=season)
    if not all_logs:
        print("  [grade] Could not fetch game logs — skipping grading")
        return

    # Index logs by (player_name, game_date) for fast lookup
    logs_by_player_date = defaultdict(list)
    for g in all_logs:
        key = (g.get("player_name", ""), g.get("game_date", ""))
        logs_by_player_date[key].append(g)

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

        # Find actual stat
        games = logs_by_player_date.get((player, pick["date"]), [])
        if not games:
            continue
        game = games[0]

        if market == "pts_rebs_asts":
            actual = float(game.get("pts", 0)) + float(game.get("reb", 0)) + float(game.get("ast", 0))
        else:
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
    # To-win-1u: win = +1u, loss = -to_win_1u (real odds), fallback -1.1u (-110)
    units = 0.0
    for pick in props:
        r = pick.get("result")
        if r == "WIN":
            units += 1.0
        elif r == "LOSS":
            w1u = pick.get("to_win_1u")
            units -= float(w1u) if w1u is not None else 1.1
    print(f"  [grade] Graded {graded} picks: {wins}W-{losses}L ({pct:.1f}%) {'+' if units >= 0 else ''}{units:.1f}u"
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


def _get_todays_teams(date_key):
    """
    Return set of team abbreviations that actually play today per ESPN schedule.
    Filters out tomorrow's games that FanDuel may have cached early.
    """
    espn_path = os.path.join(SCRIPT_DIR, "..", "..", "data", "espn_cache", "nba", f"{date_key}.json")
    espn_path = os.path.normpath(espn_path)
    if not os.path.exists(espn_path):
        return None  # None = don't filter (no ESPN data)

    try:
        with open(espn_path, "r") as f:
            espn = json.load(f)
    except Exception:
        return None

    teams = set()
    for ev in espn.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        for team in comp.get("competitors", []):
            abbr = (team.get("team") or {}).get("abbreviation", "")
            if abbr:
                teams.add(abbr)

    return teams


def _get_started_teams(date_key):
    """
    Return set of team abbreviations whose games have already tipped off.
    Uses ESPN scoreboard cache to check commence times vs. now.
    """
    espn_path = os.path.join(SCRIPT_DIR, "..", "..", "data", "espn_cache", "nba", f"{date_key}.json")
    espn_path = os.path.normpath(espn_path)
    if not os.path.exists(espn_path):
        return set()

    try:
        with open(espn_path, "r") as f:
            espn = json.load(f)
    except Exception:
        return set()

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    started = set()

    for ev in espn.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        tip_str = comp.get("date", "")
        if not tip_str:
            continue
        try:
            tip = datetime.datetime.fromisoformat(tip_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue

        if tip <= now_utc:
            for team in comp.get("competitors", []):
                abbr = (team.get("team") or {}).get("abbreviation", "")
                if abbr:
                    started.add(abbr)

    return started


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

    # --- Grade previous picks ---
    print(f"\n  [0/8] Grading previous picks...")
    grade_previous_picks(season)

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

    # --- Stage 5b: Fetch player positions, positional defense, per-100 ---
    print(f"\n  [5b/8] Fetching player positions, positional defense, per-100 stats...")
    player_positions = fetch_player_positions(season=season)
    team_def_by_pos = fetch_team_def_by_position(season=season)
    player_per100 = fetch_player_per100_stats(season=season)
    print(f"  {len(player_positions)} positions, {len(team_def_by_pos)} teams pos-def, {len(player_per100)} per-100")

    # --- Stage 6: Fetch prop lines (FanDuel primary, Odds API fallback) ---
    print(f"\n  [6/8] Fetching prop lines (FanDuel primary)...")
    prop_lines = fetch_fanduel_nba_props(date_key=date_key)
    if not prop_lines:
        print(f"  FanDuel returned 0 lines, falling back to The Odds API...")
        prop_lines = fetch_nba_player_props(date_key=date_key)
    print(f"  {len(prop_lines)} prop lines fetched")

    # --- Stage 6b: Filter to today's games only ---
    # FanDuel sometimes caches tomorrow's lines early. Cross-reference ESPN
    # schedule to only keep prop lines for games actually on today's date.
    todays_teams = _get_todays_teams(date_key)
    if todays_teams is not None:
        before = len(prop_lines)
        prop_lines = [
            p for p in prop_lines
            if p.get("event_home") in todays_teams
               or p.get("event_away") in todays_teams
        ]
        dropped = before - len(prop_lines)
        if dropped:
            print(f"  Dropped {dropped} prop lines for non-today games (FanDuel early cache)")
        print(f"  {len(prop_lines)} prop lines for today's games")

    # Filter out started games
    started_teams = _get_started_teams(date_key)
    if started_teams:
        before = len(prop_lines)
        prop_lines = [
            p for p in prop_lines
            if p.get("event_home") not in started_teams
               and p.get("event_away") not in started_teams
        ]
        dropped = before - len(prop_lines)
        if dropped:
            print(f"  Dropped {dropped} prop lines for {len(started_teams)} started-game teams")
        print(f"  {len(prop_lines)} prop lines remaining (non-started games)")

    # --- Stage 6c: Strip today's game logs from rolling window ---
    # NBA.com game log cache may already include today's in-progress stats.
    # Remove them so projections use only completed games.
    for pid, games in player_logs.items():
        player_logs[pid] = [g for g in games if g.get("game_date", "") != date_iso]

    # --- Stage 6d: Load injury report ---
    print(f"\n  [6d/8] Loading injury report...")
    injury_report = load_injury_report(date_key)
    if injury_report:
        n_out = sum(1 for team_inj in injury_report.values()
                    for p in team_inj if p.get("status") in ("out", "doubtful"))
        n_teams = len(injury_report)
        print(f"  {n_out} players out/doubtful across {n_teams} teams")
    else:
        print(f"  No injury cache for {date_key} — projecting without injuries")

    # --- Stage 7: Project props ---
    print(f"\n  [7/8] Projecting player props (Kalman + positional defense + injuries)...")
    projections = project_player_props(
        player_logs,
        team_def_stats=team_def,
        prop_lines=prop_lines,
        kalman_state=kalman_state,
        player_adv_stats=adv_stats,
        player_positions=player_positions,
        team_def_by_pos=team_def_by_pos,
        injury_report=injury_report,
        player_per100=player_per100,
    )

    picks = [p for p in projections if p["pick"] != "PASS"]
    # Also count started-game picks that were preserved from earlier runs
    print(f"  {len(projections)} total projections, {len(picks)} new actionable picks")

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
        kept = [p for p in existing_props if p.get("date") != date_iso]

        # For TODAY's picks: preserve existing picks for started games
        # (they were made pre-game and are still valid), only replace
        # picks for non-started games with fresh projections.
        today_existing = [p for p in existing_props if p.get("date") == date_iso]
        today_started = [p for p in today_existing
                         if p.get("team") in started_teams]
        today_fresh = [p for p in dashboard.get("props", [])
                       if p.get("date") == date_iso or not p.get("date")]

        merged_props = kept + today_started + today_fresh

        # Preserve todayProjections for started games too — they were
        # generated pre-game and are valid. Only replace projections
        # for non-started games with fresh ones.
        existing_today_projs = existing.get("todayProjections", [])
        locked_projs = [p for p in existing_today_projs
                        if p.get("team") in started_teams]
        fresh_projs = dashboard.get("todayProjections", [])
        merged_today_projs = locked_projs + fresh_projs

        # Preserve existing metadata (season, mode, model from backtest)
        # but update generated timestamp and totals
        dashboard_merged = {**existing, **dashboard, "props": merged_props,
                            "todayProjections": merged_today_projs}
        if existing.get("season"):
            dashboard_merged["season"] = existing["season"]
        if existing.get("mode"):
            dashboard_merged["mode"] = existing["mode"]
        if existing.get("model"):
            dashboard_merged["model"] = existing["model"]
        dashboard_merged["totalPicks"] = len(merged_props)

        n_kept = len(kept)
        n_started = len(today_started)
        n_new = len(today_fresh)
        n_locked_projs = len(locked_projs)
        n_fresh_projs = len(fresh_projs)
        if n_kept > 0 or n_started > 0:
            print(f"  Merged picks: {n_kept} historical + {n_started} started-game + {n_new} fresh")
            print(f"  Merged projections: {n_locked_projs} locked + {n_fresh_projs} fresh")

        with open(path, "w") as f:
            json.dump(dashboard_merged, f, indent=2, cls=_NumpyEncoder)
        print(f"  Wrote to {path}")

    # Save updated Kalman state
    prune_inactive_players(kalman_state)
    save_player_kalman_state(kalman_state, KALMAN_STATE_PATH)
    print(f"  Saved Kalman state ({len(kalman_state['players'])} players)")

    # Print all today's picks: fresh + preserved started-game picks
    # Load preserved picks from the file we just wrote
    all_today_picks = list(picks)  # fresh non-started picks
    try:
        with open(os.path.normpath(output_paths[0]), "r") as f:
            written = json.load(f)
        for p in written.get("props", []):
            if (p.get("date") == date_iso
                    and p.get("pick") not in ("PASS", None)
                    and p.get("team") in started_teams):
                p["_started"] = True
                all_today_picks.append(p)
    except Exception:
        pass

    _print_picks(all_today_picks)

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
            conf_marker = ""
            edge_sign = "+" if (p.get("edge") or 0) > 0 else ""
            odds_str = _format_odds(p.get("odds"))
            w1u = p.get("to_win_1u")
            w1u_str = f"  w1u={w1u:.2f}u" if w1u is not None else ""
            lock = " [LOCKED]" if p.get("_started") else ""
            print(
                f"    {p['player']:25s} {p['team']:3s} "
                f"{p['pick']:5s} {p['line']:6.1f}  "
                f"proj={p['proj']:6.1f}  edge={edge_sign}{p.get('edge', 0):5.1f}  "
                f"p={p.get('pCover', 0):.3f}{conf_marker}  "
                f"{odds_str}{w1u_str}{lock}"
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
    parser = argparse.ArgumentParser(description="Daily NBA player prop projections (Kalman)")
    parser.add_argument("--date", type=str, default=None,
                        help="Date in YYYYMMDD format (default: today)")
    args = parser.parse_args()

    run_daily(date_key=args.date)
