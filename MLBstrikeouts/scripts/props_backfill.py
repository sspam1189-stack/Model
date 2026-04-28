#!/usr/bin/env python3
"""
MLBstrikeouts/scripts/props_backfill.py
Walk-forward backfill of MLB pitcher prop projections with Kalman filtering.

For each game date, projects pitcher stats using only prior games' data,
then compares to actual results. The Kalman filter is trained incrementally.

Usage:
    cd MLBstrikeouts
    python -m scripts.props_backfill
    python -m scripts.props_backfill --season 2026 --start-game 10
    python -m scripts.props_backfill --start-date 2026-04-01
"""

import sys, os, math, argparse, json
import numpy as np
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from sources.mlb_stats import (
    fetch_pitcher_game_logs, fetch_team_batting_stats,
    fetch_pitcher_advanced_stats, fetch_pitcher_sabermetrics,
)
from props_engine import (
    organize_pitcher_logs, project_pitcher_props, STAT_KEYS,
)
from pitcher_kalman import (
    new_pitcher_kalman_state, batch_update_from_game_logs,
    apply_drift, kalman_summary, PITCHER_KALMAN_DEFAULTS,
    save_pitcher_kalman_state, prune_inactive_pitchers,
)
from defaults import ROLLING_WINDOW, MIN_GAMES, current_season
from sources.weather import fetch_game_weather
from sources.mlb_stats import (
    fetch_pitcher_handedness_splits,
    fetch_player_bat_sides, fetch_lineup_handedness,
    fetch_batter_k_rates, load_pitch_hands,
    fetch_savant_pitcher_rates,
    fetch_savant_pitch_chunks,
    compute_pitch_level_rates_from_chunks,
    merge_pitch_level_into_savant,
    CACHE_DIR,
)


# ---------------------------------------------------------------------------
# Field normalization
# ---------------------------------------------------------------------------

def _normalize_game_log(g):
    """
    Ensure game log has both player_* and pitcher_* field names.

    MLB Stats API returns player_id / player_name, but
    organize_pitcher_logs and pitcher_kalman expect pitcher_id / pitcher_name.
    Add aliases so both consumers work.
    """
    if "pitcher_id" not in g and "player_id" in g:
        g["pitcher_id"] = g["player_id"]
    if "pitcher_name" not in g and "player_name" in g:
        g["pitcher_name"] = g["player_name"]
    # Also keep IP as float for organize_pitcher_logs filter
    if "IP" not in g and "ip" in g:
        g["IP"] = g["ip"]
    # Opponent field: props_engine uses "opp", mlb_stats uses "opponent"
    if "opp" not in g and "opponent" in g:
        g["opp"] = g["opponent"]
    return g


def _calc_pick_units(odds, won):
    """
    Calculate units for a single pick using risk-to-win-1u convention.

      + odds:  risk 1u to win (odds/100)u
      - odds:  risk (|odds|/100)u to win 1u

    If odds is None, returns 0.0 (pick contributes nothing to units). Every
    real pick has a recorded price (FanDuel / Odds API); a None price means
    the odds fetch broke. Silently pricing at -110 would misreport P/L, so
    we surface the gap instead by contributing zero.
    """
    if odds is None:
        return 0.0

    odds = int(odds)
    if odds > 0:
        return odds / 100.0 if won else -1.0
    else:
        return 1.0 if won else -abs(odds) / 100.0


def _calc_units(picks):
    """Calculate total units from backfill picks (have 'won' and 'odds')."""
    return sum(_calc_pick_units(p.get("odds"), p["won"]) for p in picks)


def _calc_units_from_dashboard(picks):
    """Calculate total units from dashboard picks (have 'result' and 'odds')."""
    return sum(
        _calc_pick_units(p.get("odds"), p["result"] == "WIN")
        for p in picks if p.get("result") in ("WIN", "LOSS")
    )


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------

def backfill(season=None, start_game=10, start_date=None):
    """
    Walk-forward backfill with Kalman filter trained incrementally.
    Always resets Kalman state from scratch (no prior history).

    For each game date:
      1. Project using prior data + current Kalman state (no lookahead)
      2. Compare projections to actuals
      3. Update Kalman state with today's actual results
      4. Move to next date

    Parameters
    ----------
    season : int or None
        MLB season year (default: current_season()).
    start_game : int
        Minimum game dates before projecting (default: 10).
    start_date : str or None
        If set (YYYY-MM-DD), start projecting from this date instead of
        using start_game. Games before this date still train the Kalman.
    """
    season = season or current_season()
    date_label = f"from {start_date}" if start_date else f"after game #{start_game}"
    print(f"\n{'='*60}")
    print(f"  MLB PITCHER PROPS BACKTEST (Bayesian Kalman)")
    print(f"  Season: {season}  |  Start {date_label}")
    print(f"{'='*60}")

    # Load pitcher game logs (from cache if available)
    print(f"\n  Loading pitcher game logs...")
    all_logs = fetch_pitcher_game_logs(season=season)
    if not all_logs:
        print("  No pitcher game logs found. Run fetch_stats first.")
        return None

    # Normalize field names (player_id -> pitcher_id, etc.)
    for g in all_logs:
        _normalize_game_log(g)

    # Team batting is now fetched per-date inside the walk-forward loop
    # (see Phase 2b) to avoid season-to-today leakage.
    team_batting_by_date = {}

    # Load advanced pitcher stats
    print(f"  Loading pitcher advanced stats...")
    adv_stats = fetch_pitcher_advanced_stats(season=season)

    # Load sabermetrics (FIP, xFIP)
    print(f"  Loading pitcher sabermetrics...")
    saber_stats = fetch_pitcher_sabermetrics(season=season)

    # Load player bat-side lookup (for per-game lineup handedness)
    print(f"  Loading player bat sides...")
    bat_sides = fetch_player_bat_sides(season=season)
    print(f"  {len(bat_sides)} players with bat-side data")

    # Batter K rates are now fetched per-date inside the walk-forward loop
    # to avoid season-to-today leakage.
    print(f"  Batter K rates will be fetched per-date (walk-forward mode)...")
    batter_k_rates_by_date = {}
    pitch_hands = load_pitch_hands(season=season)
    savant_rates_base = fetch_savant_pitcher_rates(season=season)
    print(f"  {len(savant_rates_base)} pitchers with Savant K%/whiff%")

    # Walk-forward stuff_score: fetch chunks once, compose per game_date.
    season_start = f"{season}-03-20"
    latest_log_date = max((g.get("game_date", "") for g in all_logs), default=None)
    pitch_chunks = fetch_savant_pitch_chunks(
        season=season,
        start_date=season_start,
        end_date=latest_log_date,
    )
    _asof_savant_cache = {}

    def _savant_rates_asof(asof_date):
        if asof_date in _asof_savant_cache:
            return _asof_savant_cache[asof_date]
        if asof_date <= season_start:
            _asof_savant_cache[asof_date] = savant_rates_base
            return savant_rates_base
        # 30-day rolling stuff window protects against velocity drift / fatigue
        from datetime import datetime as _dt, timedelta as _td
        min_date = (
            _dt.strptime(asof_date, "%Y-%m-%d").date() - _td(days=30)
        ).strftime("%Y-%m-%d")
        date_pitch_level = compute_pitch_level_rates_from_chunks(
            pitch_chunks, asof_date=asof_date, min_date=min_date
        )
        merged = merge_pitch_level_into_savant(savant_rates_base, date_pitch_level)
        _asof_savant_cache[asof_date] = merged
        return merged

    # Inject pitcher hand into adv_stats
    for pid_str, adv in adv_stats.items():
        try:
            adv["pitch_hand"] = pitch_hands.get(int(pid_str), "R")
        except (ValueError, TypeError):
            pass

    # Load batting orders for lineup K% lookup
    from sources.mlb_stats import _load_cache as _lc
    bo_cache_path = CACHE_DIR / f"batting_orders_{season}.json"
    batting_orders_all = _lc(bo_cache_path, max_age_hours=None) or {}

    # Organize by pitcher
    pitcher_logs = organize_pitcher_logs(all_logs)
    print(f"  {len(pitcher_logs)} pitchers, {len(all_logs)} total game logs")

    # Get all unique game dates
    all_dates = sorted(set(
        g.get("game_date", "") for g in all_logs if g.get("game_date")
    ))
    print(f"  {len(all_dates)} game dates in season")

    # Always reset Kalman state from scratch
    kalman_state_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "data", "kalman_state.json"
    )
    kalman_state_path = os.path.normpath(kalman_state_path)
    if os.path.exists(kalman_state_path):
        os.remove(kalman_state_path)
        print(f"  [kalman] Reset: deleted {kalman_state_path}")
    kalman_state = new_pitcher_kalman_state()

    results = {"strikeouts": {"projections": [], "actuals": [], "picks": []}}
    total_projected = 0

    # --- Walk-forward loop ---
    for date_idx, game_date in enumerate(all_dates):

        # Collect today's logs (for Kalman update after projecting)
        today_date_logs = [
            g for g in all_logs
            if g.get("game_date", "") == game_date and g.get("IP", 0) >= 3.0
        ]

        # Determine if we should start projecting yet
        should_skip = False
        if start_date:
            should_skip = game_date < start_date
        else:
            should_skip = date_idx < start_game

        if should_skip:
            # Not projecting yet, but still update Kalman with this date's games
            if today_date_logs:
                batch_update_from_game_logs(kalman_state, today_date_logs)
            continue

        # Phase 1: Build prior-only pitcher logs for projection
        prior_logs = {}
        actual_games = {}

        for pid, games in pitcher_logs.items():
            prior = [g for g in games if g.get("game_date", "") < game_date]
            today = [g for g in games if g.get("game_date", "") == game_date]

            if prior:  # let the engine's own MIN_GAMES handle per-market filtering
                prior_logs[pid] = prior
            if today:
                actual_games[pid] = today[0]

        if not prior_logs or not actual_games:
            # Still update Kalman with today's games
            if today_date_logs:
                batch_update_from_game_logs(kalman_state, today_date_logs)
            continue

        # Phase 2: Load real prop lines from cache (pre-fetched by fetch_odds.py)
        real_lines = None
        try:
            from sources.odds_theoddsapi import _props_cache_path, _load_cache
            date_key = game_date.replace("-", "")
            cp = _props_cache_path(date_key)
            cached = _load_cache(cp, max_age_hours=None)
            if cached is not None:
                real_lines = cached
        except Exception as e:
            pass  # No props available for this date — project without lines

        # Phase 2b: Fetch lineup handedness + weather for this date
        lineup_hand = fetch_lineup_handedness(
            game_date, bat_sides=bat_sides, season=season
        )

        # Fetch per-date team batting (walk-forward: 45-day window ending at
        # game_date, with season-to-game_date fallback for small samples).
        if game_date not in team_batting_by_date:
            team_batting_by_date[game_date] = fetch_team_batting_stats(
                season=season, through_date=game_date
            )
        team_batting = team_batting_by_date[game_date]

        # Fetch per-date batter K rates the same way.
        if game_date not in batter_k_rates_by_date:
            batter_k_rates_by_date[game_date] = fetch_batter_k_rates(
                season=season, through_date=game_date
            )
        batter_k_rates = batter_k_rates_by_date[game_date]

        # Merge PCT_LHB into team_batting for this date's lineups
        date_team_batting = dict(team_batting)  # shallow copy
        for abbr, hand_data in lineup_hand.items():
            if abbr in date_team_batting:
                date_team_batting[abbr] = {
                    **date_team_batting[abbr],
                    "PCT_LHB": hand_data["PCT_LHB"],
                }
            else:
                date_team_batting[abbr] = {"PCT_LHB": hand_data["PCT_LHB"]}

        weather_data = fetch_game_weather(game_date)

        # Build lineup_data for this date.
        # Priority: live cache (lineups_YYYYMMDD.json — what run_daily actually saw
        # at projection time) → fall back to batting_orders_{season}.json
        # (post-game actual lineups). This makes backfill replay live's experience
        # rather than getting hindsight on late scratches/lineup card changes.
        date_lineup_data = {}
        live_cache_path = CACHE_DIR / f"lineups_{game_date.replace('-', '')}.json"
        live_lineup = _lc(live_cache_path, max_age_hours=None) or {}
        date_bo = batting_orders_all.get(game_date, {})
        for abbr in set(live_lineup) | set(date_bo):
            live = live_lineup.get(abbr) or {}
            live_ids = live.get("player_ids") if isinstance(live, dict) else None
            if live_ids:
                date_lineup_data[abbr] = {"player_ids": live_ids}
            elif date_bo.get(abbr):
                date_lineup_data[abbr] = {"player_ids": date_bo[abbr]}

        # Build probable_pitchers from today's actual game logs
        # (who actually started — mirrors what run_daily gets from schedule)
        date_probable = []
        today_starters = [
            g for g in all_logs
            if g.get("game_date", "") == game_date and g.get("IP", g.get("ip", 0)) >= 3.0
        ]
        # Group by game_id to pair home/away
        from collections import defaultdict
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

        # Build pitcher_splits for today's starters
        date_splits = {}
        for g in today_starters:
            pid = g.get("pitcher_id") or g.get("player_id")
            if pid and str(pid) not in date_splits:
                s = fetch_pitcher_handedness_splits(
                    pid, season=season, through_date=game_date
                )
                if s:
                    date_splits[str(pid)] = s

        # Phase 3: Project props using prior data + current Kalman state
        projections = project_pitcher_props(
            prior_logs,
            team_batting_stats=date_team_batting,
            prop_lines=real_lines,
            kalman_state=kalman_state,
            pitcher_adv_stats=adv_stats,
            pitcher_sabermetrics=saber_stats,
            pitcher_splits=date_splits,
            probable_pitchers=date_probable,
            weather_by_game=weather_data,
            batter_k_rates=batter_k_rates,
            lineup_data=date_lineup_data,
            savant_rates=_savant_rates_asof(game_date),
            k_skill_config={"weights": {"stuff_score": 0.060}, "cap": 0.12},
        )

        # Save ALL projections for this date (for Games Explorer)
        latest_all_projections = [
            p for p in projections
            if p.get("line") is not None and p.get("proj") is not None
        ]
        for p in latest_all_projections:
            p["date"] = game_date

        # Phase 4: Grade projections against actuals
        date_picks = 0
        for proj in projections:
            player = proj["player"]
            market = proj["market"]

            if market != "strikeouts":
                continue

            actual_val = _find_actual(player, market, actual_games, pitcher_logs)
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
                "odds": proj.get("odds"),
                "won": won,
            })
            date_picks += 1
            total_projected += 1

        # Phase 5: UPDATE Kalman with today's actual games (after projecting)
        if today_date_logs:
            batch_update_from_game_logs(kalman_state, today_date_logs)

        if date_picks > 0:
            wins_today = sum(1 for m in results for p in results[m]["picks"] if p.get("date") == game_date and p.get("won"))
            losses_today = sum(1 for m in results for p in results[m]["picks"] if p.get("date") == game_date and not p.get("won"))
            print(f"  {game_date}: {date_picks} picks ({wins_today}W-{losses_today}L)")

    # --- Save Kalman state so run_daily can pick up from here ---
    prune_inactive_pitchers(kalman_state)
    save_pitcher_kalman_state(kalman_state, kalman_state_path)
    print(f"  Saved Kalman state ({len(kalman_state['pitchers'])} pitchers) "
          f"to {kalman_state_path}")


    # --- Summary ---
    print(kalman_summary(kalman_state, top_n=10, stat_key="k"))
    _print_summary(results, total_projected, season)

    return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_actual(pitcher_name, market, actual_games, pitcher_logs):
    """
    Find a pitcher's actual stat value from today's games.

    Searches actual_games by matching pitcher_name (from projection) to
    the pitcher_name field in the game log entry.

    Parameters
    ----------
    pitcher_name : str
        Pitcher display name from the projection.
    market : str
        Market name (strikeouts).
    actual_games : dict
        {pitcher_id: game_log_dict} for today's games.
    pitcher_logs : dict
        Full pitcher logs (unused, kept for API compatibility).

    Returns
    -------
    float or None
        Actual stat value, or None if not found.
    """
    stat_key = STAT_KEYS.get(market)
    if not stat_key:
        return None

    from props_engine import _name_key
    target_nk = _name_key(pitcher_name)

    for pid, g in actual_games.items():
        name = g.get("pitcher_name") or g.get("player_name", "")
        if _name_key(name) == target_nk:
            val = g.get(stat_key)
            if val is not None:
                return float(val)
    return None


def _print_summary(results, total_projected, season):
    """Print formatted backfill summary per market + grand total."""
    print(f"\n{'='*60}")
    print(f"  MLB PITCHER PROPS BACKTEST SUMMARY (Kalman) -- {season}")
    print(f"{'='*60}")
    print(f"  Total pitcher-games projected: {total_projected}\n")

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
        units = _calc_units(picks)
        pct = wins / max(1, wins + losses) * 100

        grand_w += wins
        grand_l += losses
        grand_units += units

        print(f"  {market:14s}: MAE={mae:6.1f}  corr={corr:.3f}  "
              f"picks={wins}W-{losses}L ({pct:.1f}%) "
              f"{'+'if units >= 0 else ''}{units:.1f}u")

    print(f"\n  TOTAL: {grand_w}W-{grand_l}L "
          f"({grand_w / max(1, grand_w + grand_l) * 100:.1f}%) "
          f"{'+'if grand_units >= 0 else ''}{grand_units:.1f}u")


# ---------------------------------------------------------------------------
# Dashboard JSON
# ---------------------------------------------------------------------------

class _NumpyEncoder(json.JSONEncoder):
    """JSON encoder that handles numpy types."""
    def default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        return super().default(obj)


def write_dashboard_json(results, season):
    """
    Write graded backfill picks to dashboard JSON files.

    Merges with existing live picks (preserves picks from dates not
    in the backfill range).

    Output paths:
      - MLBstrikeouts/data/mlb-props.json
      - PythonDashboard/data/mlb-props.json
    """
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
                "odds": p.get("odds"),
                "to_win_1u": p.get("to_win_1u"),
                "actual": p["actual"],
                "result": "WIN" if p["won"] else "LOSS",
                "date": p.get("date", ""),
            })

    all_picks.sort(key=lambda x: (-(x.get("pCover") or 0), x.get("date", "")))

    total_w = sum(1 for p in all_picks if p["result"] == "WIN")
    total_l = sum(1 for p in all_picks if p["result"] == "LOSS")
    units = _calc_units_from_dashboard(all_picks)

    dashboard = {
        "sport": "mlb",
        "type": "pitcher_props",
        "season": str(season),
        "mode": "backfill",
        "model": "kalman_blend",
        "generated": datetime.now().isoformat(),
        "totalProjections": sum(len(d["projections"]) for d in results.values()),
        "totalPicks": len(all_picks),
        "props": all_picks,
        "summary": (
            f"{len(all_picks)} graded picks for {season}: "
            f"{total_w}W-{total_l}L "
            f"({total_w / max(1, total_w + total_l) * 100:.1f}%) "
            f"{'+'if units >= 0 else ''}{units:.1f}u"
        ),
    }

    script_dir = os.path.dirname(os.path.abspath(__file__))
    paths = [
        os.path.join(script_dir, "..", "data", "mlb-props.json"),
        os.path.join(script_dir, "..", "..", "PythonDashboard", "data", "mlb-props.json"),
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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backtest MLB pitcher prop projections (Kalman)"
    )
    parser.add_argument("--season", type=int, default=None,
                        help="MLB season year (e.g. 2026)")
    parser.add_argument("--start-game", type=int, default=10,
                        help="Min game dates before projecting (default: 10)")
    parser.add_argument("--start-date", type=str, default=None,
                        help="Start projecting from this date (YYYY-MM-DD)")
    args = parser.parse_args()

    results = backfill(args.season, args.start_game, start_date=args.start_date)
    if results:
        write_dashboard_json(results, args.season or current_season())
