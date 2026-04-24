#!/usr/bin/env python3
"""
pyNFL/scripts/props_backtest.py
Walk-forward backtest of player prop projections.

Uses the plays -> volume -> efficiency -> output pipeline:
  1. Team environment: Vegas lines + prior PBP -> dropbacks, rush_att per team
  2. Player volume shares: target_share, rush_share from prior PBP
  3. Player efficiency rates: ypa, completion_pct, ypc, catch_rate, ypr, td_rate
  4. Kalman filter: smooth rate/share estimates across weeks
  5. Output = projected volume * projected rate

For each week, projects player stats using only prior weeks' data,
then compares to actual results. Reports W-L-units per market.
"""

import sys
import os
import json
import math
import argparse
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from sources.nflfastr import fetch_pbp
from team_environment import (
    compute_team_pace, compute_team_pass_rate,
    project_game_environment,
)
from player_volume import compute_shares_from_pbp, compute_weighted_shares
from player_kalman_nfl import (
    new_player_kalman_state, batch_update_from_game_logs,
    get_player_projection, apply_drift,
)
from props_engine import (
    build_player_game_logs, _name_key, _weighted_avg, _weighted_std,
    ROLLING_WINDOW, DECAY_FACTOR, PROP_T_DF,
    MIN_GAMES_PASSER, MIN_GAMES_RECEIVER, MIN_GAMES_RUSHER,
    MARKET_THRESHOLDS, VAR_MULT,
)
from scipy.stats import t as t_dist

# ---------------------------------------------------------------------------
# Data directories
# ---------------------------------------------------------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "data"))
_ODDS_DIR = os.path.join(_DATA_DIR, "odds")
_PROPS_DIR = os.path.join(_DATA_DIR, "props")

# ---------------------------------------------------------------------------
# NFL team name resolution: full name <-> nflfastr abbreviation
# ---------------------------------------------------------------------------
_FULL_TO_ABBR = {}
_ABBR_TO_FULL = {}

def _build_team_maps():
    """Build full-name <-> abbreviation mappings from defaults.NFL_TEAMS."""
    global _FULL_TO_ABBR, _ABBR_TO_FULL
    if _FULL_TO_ABBR:
        return
    try:
        from defaults import NFL_TEAMS
        for full_name, aliases in NFL_TEAMS.items():
            abbr = aliases[0]  # First alias is always the abbreviation
            _FULL_TO_ABBR[full_name.lower()] = abbr
            _ABBR_TO_FULL[abbr] = full_name
            for a in aliases:
                _FULL_TO_ABBR[a.lower()] = abbr
    except ImportError:
        pass


def _resolve_abbr(name):
    """Resolve a team name (full or partial) to nflfastr abbreviation."""
    _build_team_maps()
    if not name:
        return name
    # Already an abbreviation?
    if name in _ABBR_TO_FULL:
        return name
    key = name.strip().lower()
    return _FULL_TO_ABBR.get(key, name)


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def _load_odds(season, week):
    """Load Vegas odds from data/odds/nfl_odds_SEASON_WWEEK.json."""
    path = os.path.join(_ODDS_DIR, f"nfl_odds_{season}_W{week}.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return []


def _load_prop_lines(season, week):
    """Load prop lines from data/props/nfl_props_SEASON_WWEEK.json."""
    path = os.path.join(_PROPS_DIR, f"nfl_props_{season}_W{week}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return None


def _build_odds_lookup(odds_list):
    """
    Build lookup: (home_abbr, away_abbr) -> {"total": float, "line": float}.
    Also indexes by individual team -> game info for easy per-player lookup.
    """
    games = {}
    team_game = {}  # abbr -> (home_abbr, away_abbr)
    for g in odds_list:
        home_abbr = _resolve_abbr(g.get("home", ""))
        away_abbr = _resolve_abbr(g.get("away", ""))
        total = g.get("total")
        line = g.get("line")  # home spread
        if not home_abbr or not away_abbr:
            continue
        entry = {"total": total, "line": line, "home": home_abbr, "away": away_abbr}
        games[(home_abbr, away_abbr)] = entry
        team_game[home_abbr] = entry
        team_game[away_abbr] = entry
    return games, team_game


# ---------------------------------------------------------------------------
# Player rates computation (inline — player_rates.py doesn't exist yet)
# ---------------------------------------------------------------------------

def compute_player_rates_from_logs(player_logs):
    """
    Compute per-player efficiency rates from game logs.

    Parameters
    ----------
    player_logs : dict
        {player_id: [game_log, ...]} from build_player_game_logs.

    Returns
    -------
    dict
        {player_id: {"ypa", "completion_pct", "td_rate", "ypc",
                      "catch_rate", "ypr", "games": [rate_dict_per_game, ...]}}
    """
    rates = {}
    for pid, games in player_logs.items():
        player_rates = []
        for g in sorted(games, key=lambda x: x.get("week", 0)):
            r = {
                "game_id": g.get("game_id"),
                "week": g.get("week", 0),
                "player_id": pid,
                "player_name": g.get("name", ""),
            }
            # Passer rates
            pa = g.get("pass_att", 0)
            if pa > 0:
                r["ypa"] = g.get("pass_yds", 0) / pa
                r["completion_pct"] = g.get("completions", 0) / pa
                r["td_rate"] = g.get("pass_td", 0) / pa

            # Rusher rates
            ra = g.get("rush_att", 0)
            if ra > 0:
                r["ypc"] = g.get("rush_yds", 0) / ra

            # Receiver rates
            tgt = g.get("targets", 0)
            rec = g.get("receptions", 0)
            if tgt > 0:
                r["catch_rate"] = rec / tgt
            if rec > 0:
                r["ypr"] = g.get("rec_yds", 0) / rec

            player_rates.append(r)

        if player_rates:
            rates[pid] = player_rates
    return rates


def compute_weighted_rates(rate_games, stat_keys, decay=DECAY_FACTOR):
    """
    Compute weighted average rates for a player.

    Parameters
    ----------
    rate_games : list[dict]
        Per-game rate dicts (sorted by week ascending).
    stat_keys : list[str]
        Which rate keys to average.
    decay : float
        Exponential decay factor.

    Returns
    -------
    dict
        {stat_key: weighted_avg}
    """
    result = {}
    for key in stat_keys:
        vals = [g[key] for g in rate_games if key in g and g[key] is not None]
        if vals:
            result[key] = _weighted_avg(vals, decay)
    return result


# ---------------------------------------------------------------------------
# Build Kalman game log records for batch_update_from_game_logs
# ---------------------------------------------------------------------------

def _build_kalman_logs(player_logs, player_shares):
    """
    Build game log records suitable for batch_update_from_game_logs.

    Merges per-game rates and shares into a flat list of dicts with
    player_id, player_name, game_id, and all Kalman-tracked stats.
    """
    records = []
    rates = compute_player_rates_from_logs(player_logs)

    for pid, rate_games in rates.items():
        share_games = player_shares.get(pid, [])
        # Index share games by game_id for merge
        share_idx = {s["game_id"]: s for s in share_games}

        for rg in rate_games:
            rec = {
                "player_id": pid,
                "player_name": rg.get("player_name", ""),
                "game_id": rg.get("game_id"),
            }
            # Copy rate stats
            for key in ("ypa", "completion_pct", "td_rate", "ypc", "catch_rate", "ypr"):
                if key in rg:
                    rec[key] = rg[key]
            # Also feed raw pass_yds for Kalman tracking (used by pass_yds market)
            game_id = rg.get("game_id")
            if game_id:
                for pg in player_logs.get(pid, []):
                    if pg.get("game_id") == game_id and pg.get("pass_yds") is not None:
                        rec["pass_yds"] = pg["pass_yds"]
                        break
            # Merge share stats
            sg = share_idx.get(rg.get("game_id"))
            if sg:
                rec["target_share"] = sg.get("target_share", 0.0)
                rec["rush_share"] = sg.get("rush_share", 0.0)
            records.append(rec)

    return records


# ---------------------------------------------------------------------------
# Volume-based projection: the new engine
# ---------------------------------------------------------------------------

def _project_volume_output(
    pid, player_logs, player_shares, player_rates_dict,
    team_env, kalman_state, opp_team,
):
    """
    Project a player's stats using volume * efficiency.

    Parameters
    ----------
    pid : str
        Player ID.
    player_logs : list[dict]
        This player's game logs (sorted by week).
    player_shares : list[dict]
        This player's share logs (sorted by week).
    player_rates_dict : list[dict]
        This player's per-game rate dicts.
    team_env : dict
        Team game environment from project_game_environment().
    kalman_state : dict
        Current Kalman state.
    opp_team : str
        Opponent abbreviation.

    Returns
    -------
    dict or None
        Projection dict with all markets, or None if insufficient data.
    """
    if not player_logs or not team_env:
        return None

    recent_logs = player_logs[-ROLLING_WINDOW:]
    name = recent_logs[-1].get("name", "Unknown")
    team = recent_logs[-1].get("team", "")

    # Weighted shares
    recent_shares = player_shares[-ROLLING_WINDOW:] if player_shares else []
    w_shares = compute_weighted_shares(recent_shares)

    # Weighted rates (rolling average as baseline)
    recent_rates = player_rates_dict[-ROLLING_WINDOW:] if player_rates_dict else []

    # Projected team volume from game environment
    proj_dropbacks = team_env.get("dropbacks", 30.0)
    proj_rush_att = team_env.get("rush_attempts", 25.0)

    projections = {}

    # --- Passing markets ---
    passer_games = [g for g in recent_logs if g.get("pass_att", 0) >= 10]
    if len(passer_games) >= MIN_GAMES_PASSER:
        proj_pass_att = team_env.get("dropbacks", 33.0)

        # --- Pass yards: hybrid model ---
        # Neither pure decomposition (attempts×YPA, corr=0.16) nor pure
        # raw Kalman captures pass yards well alone. Combine:
        #   0.40 × Kalman(raw pass_yds)     — captures coaching intent, QB aggression
        #   0.30 × Kalman(attempts) × Kalman(YPA) — causal structure
        #   0.15 × opponent adjustment       — matchup context
        #   0.15 × game environment scaling  — Vegas-implied volume

        # Component 1: Kalman-smoothed raw pass yards
        pass_yds_vals = [g["pass_yds"] for g in passer_games]
        rolling_pass_yds = _weighted_avg(pass_yds_vals)
        k_pass_yds = get_player_projection(kalman_state, pid, "pass_yds",
                                            rolling_avg=rolling_pass_yds)
        raw_component = k_pass_yds["proj"]

        # Component 2: Kalman(attempts) × Kalman(YPA)
        rate_keys = ["ypa", "td_rate"]
        rolling_rates = compute_weighted_rates(recent_rates, rate_keys)
        k_ypa = get_player_projection(kalman_state, pid, "ypa",
                                       rolling_avg=rolling_rates.get("ypa"))
        decomp_component = proj_pass_att * k_ypa["proj"]

        # Component 3: opponent adjustment (additive delta from league avg)
        opp_adj = 0.0
        # Will be applied as a delta below

        # Component 4: game environment scaling
        env_scale = team_env.get("implied_total", 22.0) / 22.0

        # Hybrid blend: raw persistence + structural decomposition, scaled by environment
        base_proj = 0.50 * raw_component + 0.50 * decomp_component
        # Environment scaling: if implied total is above/below average, adjust
        proj_pass_yds = base_proj * env_scale

        # --- Pass TDs: volume × rate ---
        k_td = get_player_projection(kalman_state, pid, "td_rate",
                                      rolling_avg=rolling_rates.get("td_rate"))
        proj_pass_tds = proj_pass_att * k_td["proj"]

        # Std
        pass_td_vals = [g.get("pass_td", 0) for g in passer_games]

        projections["pass_yds"] = {
            "proj": proj_pass_yds,
            "std": _weighted_std(pass_yds_vals) * VAR_MULT["pass_yds"],
        }
        projections["pass_tds"] = {
            "proj": proj_pass_tds,
            "std": _weighted_std(pass_td_vals) * VAR_MULT["pass_tds"],
        }

    # --- Rushing markets ---
    rusher_games = [g for g in recent_logs if g.get("rush_att", 0) >= 5]
    if len(rusher_games) >= MIN_GAMES_RUSHER:
        # Volume: player's rush share * team projected rush attempts
        k_rush_share = get_player_projection(
            kalman_state, pid, "rush_share",
            rolling_avg=w_shares.get("rush_share"),
        )
        proj_player_rush_att = proj_rush_att * k_rush_share["proj"]

        # Rate
        rolling_ypc = compute_weighted_rates(recent_rates, ["ypc"])
        k_ypc = get_player_projection(kalman_state, pid, "ypc",
                                       rolling_avg=rolling_ypc.get("ypc"))
        proj_rush_yds = proj_player_rush_att * k_ypc["proj"]

        ypc_vals = [g["rush_yds"] / max(g["rush_att"], 1) for g in rusher_games]
        share_vals = [g.get("rush_att", 0) for g in rusher_games]
        ypc_std = _weighted_std(ypc_vals) if len(ypc_vals) >= 2 else 1.5

        projections["rush_yds"] = {
            "proj": proj_rush_yds,
            "std": ypc_std * proj_player_rush_att * VAR_MULT["rush_yds"],
        }
        projections["rush_att"] = {
            "proj": proj_player_rush_att,
            "std": _weighted_std(share_vals) * VAR_MULT["rush_att"],
        }

    # --- Receiving markets ---
    receiver_games = [g for g in recent_logs if g.get("targets", 0) >= 2]
    if len(receiver_games) >= MIN_GAMES_RECEIVER:
        # Volume: player's target share * team projected dropbacks
        k_tgt_share = get_player_projection(
            kalman_state, pid, "target_share",
            rolling_avg=w_shares.get("target_share"),
        )
        proj_targets = proj_dropbacks * k_tgt_share["proj"]

        # Rates
        rolling_rec_rates = compute_weighted_rates(
            recent_rates, ["catch_rate", "ypr"]
        )
        k_catch = get_player_projection(
            kalman_state, pid, "catch_rate",
            rolling_avg=rolling_rec_rates.get("catch_rate"),
        )
        k_ypr = get_player_projection(
            kalman_state, pid, "ypr",
            rolling_avg=rolling_rec_rates.get("ypr"),
        )

        proj_receptions = proj_targets * k_catch["proj"]
        proj_rec_yds = proj_receptions * k_ypr["proj"]

        ypr_vals = [g["rec_yds"] / max(g.get("receptions", 1), 1) for g in receiver_games if g.get("receptions", 0) > 0]
        catch_vals = [g.get("receptions", 0) / max(g.get("targets", 1), 1) for g in receiver_games if g.get("targets", 0) > 0]
        ypr_std = _weighted_std(ypr_vals) if len(ypr_vals) >= 2 else 4.0
        catch_std = _weighted_std(catch_vals) if len(catch_vals) >= 2 else 0.15

        projections["rec_yds"] = {
            "proj": proj_rec_yds,
            "std": ypr_std * proj_receptions * VAR_MULT["rec_yds"],
        }
        projections["receptions"] = {
            "proj": proj_receptions,
            "std": catch_std * proj_targets * VAR_MULT["receptions"],
        }

    if not projections:
        return None

    return {
        "pid": pid,
        "name": name,
        "team": team,
        "opp": opp_team,
        "markets": projections,
    }


# ---------------------------------------------------------------------------
# Main backtest loop
# ---------------------------------------------------------------------------

MARKETS = ["pass_tds", "rush_yds", "rush_att", "rec_yds", "receptions"]
# pass_yds disabled: 0.23 correlation, 44-50% win rate across all variants — no edge


def backtest_props(seasons, start_week=4):
    """
    Walk-forward backtest using the volume-based projection pipeline.

    For each week >= start_week:
      1. Load Vegas odds for this week
      2. Compute team game environment from prior PBP + Vegas
      3. Compute player shares and rates from prior PBP
      4. Project via volume * Kalman-smoothed rates
      5. Grade against actual stats + real prop lines
    """
    results = {m: {"projections": [], "actuals": [], "picks": []} for m in MARKETS}

    total_projected = 0

    for season in seasons:
        print(f"\n{'='*60}")
        print(f"  SEASON {season}")
        print(f"{'='*60}")

        pbp = fetch_pbp(season)
        if pbp is None or pbp.empty:
            print(f"  No PBP data for {season}")
            continue

        weeks = sorted(pbp["week"].dropna().unique().astype(int))

        # Fresh Kalman state per season
        kalman_state = new_player_kalman_state()

        for week in weeks:
            if week < start_week:
                # Still need to process early weeks for Kalman warm-up
                if week >= 1:
                    early_pbp = pbp[pbp["week"] == week].copy()
                    if not early_pbp.empty:
                        early_logs = build_player_game_logs(early_pbp)
                        early_shares = compute_shares_from_pbp(early_pbp)
                        kalman_records = _build_kalman_logs(early_logs, early_shares)
                        batch_update_from_game_logs(kalman_state, kalman_records)
                        apply_drift(kalman_state, games_elapsed=1)
                continue

            # --- 1. Load Vegas odds for this week ---
            odds_list = _load_odds(season, week)
            _, team_odds = _build_odds_lookup(odds_list)

            # --- 2. Prior PBP data ---
            prior_pbp = pbp[pbp["week"] < week].copy()
            if prior_pbp.empty:
                continue

            # --- 3. Team game environment ---
            team_pace = compute_team_pace(prior_pbp)
            team_pass_rate = compute_team_pass_rate(prior_pbp)

            # Build per-team game environments for this week's games
            team_envs = {}  # team_abbr -> env dict
            for entry in odds_list:
                home_abbr = _resolve_abbr(entry.get("home", ""))
                away_abbr = _resolve_abbr(entry.get("away", ""))
                total = entry.get("total")
                line = entry.get("line")  # home spread
                if not home_abbr or not away_abbr or total is None or line is None:
                    continue

                home_env = project_game_environment(
                    home_abbr, away_abbr, team_pace, team_pass_rate,
                    vegas_total=total, vegas_spread=line, is_home=True,
                )
                away_env = project_game_environment(
                    away_abbr, home_abbr, team_pace, team_pass_rate,
                    vegas_total=total, vegas_spread=line, is_home=False,
                )
                team_envs[home_abbr] = home_env
                team_envs[away_abbr] = away_env

            # --- 4. Player shares and rates from prior PBP ---
            prior_logs = build_player_game_logs(prior_pbp)
            prior_shares = compute_shares_from_pbp(prior_pbp)
            prior_rates = compute_player_rates_from_logs(prior_logs)

            # --- 5. Actual data for grading ---
            week_pbp = pbp[pbp["week"] == week].copy()
            if week_pbp.empty:
                continue
            actual_logs = build_player_game_logs(week_pbp)

            # --- 6. Load prop lines for this week ---
            prop_lines = _load_prop_lines(season, week)
            line_lookup = {}
            if prop_lines:
                for pl in prop_lines:
                    nk = _name_key(pl.get("player", ""))
                    key = (nk[0], nk[1], pl.get("market", ""))
                    line_lookup[key] = pl

            # --- 7. Project each player ---
            week_picks = 0
            for pid, games in prior_logs.items():
                if not games:
                    continue

                latest = games[-1]
                team = latest.get("team", "")
                opp = latest.get("opp", "")

                # Get team environment (fall back to league average if no odds)
                env = team_envs.get(team)
                if env is None:
                    # No odds for this team — use simple league averages
                    env = {
                        "dropbacks": 33.0,
                        "rush_attempts": 27.0,
                        "implied_total": 22.0,
                        "pass_pct": 0.58,
                    }

                proj = _project_volume_output(
                    pid, games,
                    prior_shares.get(pid, []),
                    prior_rates.get(pid, []),
                    env, kalman_state, opp,
                )
                if proj is None:
                    continue

                name = proj["name"]

                # Grade each market
                for market, mdata in proj["markets"].items():
                    proj_val = mdata["proj"]
                    std = mdata["std"]

                    if market not in MARKETS:
                        continue

                    actual_val = _find_actual(name, market, actual_logs)
                    if actual_val is None:
                        continue

                    results[market]["projections"].append(proj_val)
                    results[market]["actuals"].append(actual_val)
                    total_projected += 1

                    # Respect DISABLED_MARKETS — skip pick generation for
                    # markets that are tracked for calibration but don't fire.
                    from props_engine import DISABLED_MARKETS as _DISABLED
                    if market in _DISABLED:
                        continue

                    # Look up real prop line
                    nk = _name_key(name)
                    line_key = (nk[0], nk[1], market)
                    line_data = line_lookup.get(line_key)
                    sim_line = line_data.get("line") if line_data else None

                    # Fall back to simulated line if no real line
                    if sim_line is None:
                        sim_line = _get_simulated_line(name, market, prior_logs)

                    if sim_line is not None and std > 0:
                        diff = proj_val - sim_line
                        z = diff / std
                        p_over = float(t_dist.cdf(z, df=PROP_T_DF))
                        p_under = 1.0 - p_over
                        best_p = max(p_over, p_under)

                        thresh = MARKET_THRESHOLDS.get(market, 0.80) if isinstance(MARKET_THRESHOLDS.get(market), (int, float)) else 0.80
                        if best_p >= thresh:
                            pick = "OVER" if p_over > p_under else "UNDER"
                            # No filters — let the model prove itself
                            won = ((pick == "OVER" and actual_val > sim_line) or
                                   (pick == "UNDER" and actual_val < sim_line))
                            if actual_val == sim_line:
                                continue  # Push
                            results[market]["picks"].append({
                                "season": season,
                                "week": week,
                                "player": name,
                                "proj": round(proj_val, 1),
                                "line": sim_line,
                                "actual": actual_val,
                                "pick": pick,
                                "pCover": round(best_p, 3),
                                "conf": "high",
                                "won": won,
                            })
                            week_picks += 1

            # --- 8. Update Kalman with this week's actual data ---
            week_logs = build_player_game_logs(week_pbp)
            week_shares = compute_shares_from_pbp(week_pbp)
            kalman_records = _build_kalman_logs(week_logs, week_shares)
            n_updated = batch_update_from_game_logs(kalman_state, kalman_records)
            apply_drift(kalman_state, games_elapsed=1)

            if week_picks > 0:
                # Show this week's picks across all markets
                week_all = []
                for m in MARKETS:
                    week_all.extend(
                        p for p in results[m]["picks"]
                        if p["season"] == season and p["week"] == week
                    )
                wins = sum(1 for p in week_all if p["won"])
                losses = len(week_all) - wins
                print(f"  {season} W{week:2d}: {week_picks} picks, {wins}W-{losses}L "
                      f"(kalman: {n_updated} players updated)")

    # --- Print summary ---
    print(f"\n{'='*60}")
    print(f"  PROPS BACKTEST SUMMARY (volume-based pipeline)")
    print(f"{'='*60}")
    print(f"  Total player-week-markets projected: {total_projected}\n")

    grand_w = grand_l = 0
    grand_units = 0.0

    for market in MARKETS:
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

        print(f"  {market:12s}: MAE={mae:6.1f}  corr={corr:.3f}  "
              f"picks={wins}W-{losses}L ({pct:.1f}%) {'+' if units >= 0 else ''}{units:.1f}u")

    print(f"\n  TOTAL: {grand_w}W-{grand_l}L "
          f"({grand_w / max(1, grand_w + grand_l) * 100:.1f}%) "
          f"{'+' if grand_units >= 0 else ''}{grand_units:.1f}u")

    return results


# ---------------------------------------------------------------------------
# Grading helpers (same as original)
# ---------------------------------------------------------------------------

def _find_actual(player_name, market, actual_logs):
    """Find a player's actual stat value from this week's game logs."""
    stat_key = {
        "pass_yds": "pass_yds",
        "pass_tds": "pass_td",
        "pass_att": "pass_att",
        "completions": "completions",
        "rush_yds": "rush_yds",
        "rush_att": "rush_att",
        "rec_yds": "rec_yds",
        "receptions": "receptions",
        "interceptions": "interceptions",
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
        "pass_tds": "pass_td",
        "pass_att": "pass_att",
        "completions": "completions",
        "rush_yds": "rush_yds",
        "rush_att": "rush_att",
        "rec_yds": "rec_yds",
        "receptions": "receptions",
        "interceptions": "interceptions",
    }.get(market)
    min_games = {
        "pass_yds": MIN_GAMES_PASSER,
        "pass_tds": MIN_GAMES_PASSER,
        "rush_yds": MIN_GAMES_RUSHER,
        "rush_att": MIN_GAMES_RUSHER,
        "rec_yds": MIN_GAMES_RECEIVER,
        "receptions": MIN_GAMES_RECEIVER,
    }.get(market, 3)
    if not stat_key:
        return None
    for pid, games in prior_logs.items():
        for g in games:
            if g.get("name") == player_name:
                all_games = sorted(prior_logs[pid], key=lambda x: x.get("week", 0))
                recent = all_games[-ROLLING_WINDOW:]
                vals = [gg.get(stat_key, 0) for gg in recent if gg.get(stat_key) is not None]
                if len(vals) >= min_games:
                    return round(sum(vals) / len(vals), 1)
                return None
    return None


# ---------------------------------------------------------------------------
# Dashboard JSON writer (same as original)
# ---------------------------------------------------------------------------

def write_dashboard_json(results, seasons):
    """Write graded backtest picks to dashboard JSON files."""
    from datetime import datetime

    all_picks = []
    for market, data in results.items():
        for p in data["picks"]:
            all_picks.append({
                "player": p["player"],
                "team": "",
                "opp": "",
                "market": market,
                "proj": p["proj"],
                "std": 0,
                "line": p["line"],
                "pick": p["pick"],
                "edge": round(p["proj"] - p["line"], 1),
                "pCover": p["pCover"],
                "conf": p["conf"],
                "actual": p["actual"],
                "result": "WIN" if p["won"] else "LOSS",
                "season": p["season"],
                "week": p["week"],
            })

    all_picks.sort(key=lambda x: (-x["season"], -x["week"], -(x["pCover"] or 0)))

    total_w = sum(1 for p in all_picks if p["result"] == "WIN")
    total_l = sum(1 for p in all_picks if p["result"] == "LOSS")
    units = total_w * 1.0 + total_l * (-1.1)

    dashboard = {
        "season": seasons[-1] if seasons else 0,
        "week": "backtest",
        "generated": datetime.now().isoformat(),
        "totalProjections": sum(len(d["projections"]) for d in results.values()),
        "totalPicks": len(all_picks),
        "props": all_picks,
        "summary": (
            f"{len(all_picks)} graded picks across {len(seasons)} seasons: "
            f"{total_w}W-{total_l}L ({total_w / max(1, total_w + total_l) * 100:.1f}%) "
            f"{'+' if units >= 0 else ''}{units:.1f}u"
        ),
    }

    paths = [
        os.path.join(_SCRIPT_DIR, "..", "data", "nfl-props.json"),
        os.path.join(_SCRIPT_DIR, "..", "..", "PythonDashboard", "data", "nfl-props.json"),
    ]

    class _NumpyEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                return float(obj)
            return super().default(obj)

    for path in paths:
        path = os.path.normpath(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)

        existing_props = []
        try:
            if os.path.exists(path):
                with open(path, "r") as f:
                    existing = json.load(f)
                existing_props = existing.get("props", [])
        except Exception:
            existing_props = []

        new_weeks = set()
        for p in all_picks:
            new_weeks.add((p.get("season"), p.get("week")))

        kept = [p for p in existing_props
                if (p.get("season"), p.get("week")) not in new_weeks]

        merged = kept + all_picks
        dashboard_merged = {**dashboard, "props": merged, "totalPicks": len(merged)}

        if kept:
            print(f"  Merged: kept {len(kept)} picks from other weeks + {len(all_picks)} new")

        with open(path, "w") as f:
            json.dump(dashboard_merged, f, indent=2, cls=_NumpyEncoder)
        print(f"  Wrote {len(merged)} picks to {path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backtest player props (volume-based pipeline)"
    )
    parser.add_argument("--seasons", nargs="+", type=int, default=[2023, 2024, 2025])
    parser.add_argument("--start-week", type=int, default=4,
                        help="First week to start projecting (need prior data)")
    parser.add_argument("--skip-calibrate", action="store_true",
                        help="Skip auto-calibration update (debug only)")
    args = parser.parse_args()

    results = backtest_props(args.seasons, args.start_week)
    write_dashboard_json(results, args.seasons)

    # Auto-update prop calibration from newly-graded picks.
    # Detects new season in data and widens variance 3x before folding
    # residuals in. Safe to run every time — no-op on 2nd call same data.
    if not args.skip_calibrate:
        try:
            from calibrate_props import update_from_graded
            here = os.path.dirname(os.path.abspath(__file__))
            props_path = os.path.join(here, "..", "data", "nfl-props.json")
            calib_path = os.path.join(here, "..", "data", "prop_calibration.json")
            print("\n[calibrate] Updating prop_calibration.json from graded picks...")
            update_from_graded(props_path, calib_path)
        except Exception as e:
            print(f"  [calibrate] WARNING: calibration update failed: {e}")
