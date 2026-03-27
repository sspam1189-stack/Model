# pyNBAPROPS/scripts/props_engine.py
# NBA player prop projection engine with Bayesian Kalman filtering + advanced stats.
#
# Projection pipeline:
#   1. Organize per-player game logs from NBA.com data
#   2. Compute rolling weighted averages (exponential decay)
#   3. Query Kalman filter for smoothed player baseline + uncertainty
#   4. Blend Kalman baseline with rolling average (configurable ratio)
#   5. Adjust for opponent defense (per-game stats allowed, not just DEF_RATING)
#   6. Adjust for pace (fast-paced games produce more stats)
#   7. Adjust for minutes/volume (advanced stats: USG%, minutes context)
#   8. Compare blended projection to market prop line
#   9. Use Student's t-distribution (with Kalman variance) for cover probability
#  10. Apply market-specific confidence thresholds and edge filters

import math
import numpy as np
from scipy.stats import t as t_dist

from defaults import (
    PROP_T_DF, ROLLING_WINDOW, DECAY_FACTOR, MIN_GAMES, MIN_MINUTES,
    MARKET_THRESHOLDS, VAR_MULT, MIN_EDGE, MAX_EDGE, MIN_LINE,
    UNDER_ONLY_MARKETS, DISABLED_MARKETS,
    OPP_STAT_KEY, OPP_ADJ_WEIGHT, PACE_ADJ_WEIGHT,
)
from player_kalman import get_player_projection, PLAYER_KALMAN_DEFAULTS
from sources.game_context import (
    B2B_PENALTIES, REST_BONUS, detect_b2b_from_game_logs, detect_rest_days,
    compute_home_away_split, compute_per_minute_rates,
    project_minutes, rate_based_projection,
)

# ---------------------------------------------------------------------------
# Name matching
# ---------------------------------------------------------------------------

def _name_key(name):
    """
    Normalize a player name for cross-source matching.

    'LeBron James'      -> ('lebron', 'james')
    'Shai Gilgeous-Alexander' -> ('shai', 'gilgeous-alexander')
    'P.J. Washington'   -> ('p.j.', 'washington')
    'Jaren Jackson Jr.' -> ('jaren', 'jackson')
    """
    name = name.strip()
    if not name:
        return ('', '')

    parts = name.split()
    suffixes = {'jr', 'jr.', 'sr', 'sr.', 'ii', 'iii', 'iv', 'v'}
    while len(parts) > 2 and parts[-1].lower().rstrip('.') in suffixes:
        parts.pop()

    if len(parts) >= 2:
        first = parts[0].lower()
        last = parts[-1].lower().rstrip('.')
        return (first, last)

    return (name[0].lower(), name.lower())


# ---------------------------------------------------------------------------
# Player game log organization
# ---------------------------------------------------------------------------

# Stat keys per market (maps market name -> game log field)
STAT_KEYS = {
    "points":        "pts",
    "rebounds":      "reb",
    "assists":       "ast",
    "threes":        "fg3m",
    "steals":        "stl",
    "blocks":        "blk",
    "turnovers":     "tov",
}

# Maps market name -> Kalman stat key (used in player_kalman)
KALMAN_STAT_KEYS = {
    "points":   "pts",
    "rebounds":  "reb",
    "assists":   "ast",
    "threes":    "fg3m",
    "steals":    "stl",
    "blocks":    "blk",
    "turnovers": "tov",
}


def organize_player_logs(raw_logs):
    """
    Organize raw game logs into per-player lists sorted by date.

    Returns
    -------
    dict
        {player_id: [game_log, ...]} sorted oldest-first.
    """
    by_player = {}
    for g in raw_logs:
        pid = g.get("player_id")
        if pid is None:
            continue
        if pid not in by_player:
            by_player[pid] = []
        by_player[pid].append(g)

    for pid in by_player:
        by_player[pid].sort(key=lambda g: g.get("game_date", ""))

    return by_player


# ---------------------------------------------------------------------------
# Rolling average helpers
# ---------------------------------------------------------------------------

def _weighted_avg(values, decay=DECAY_FACTOR):
    """Exponentially weighted average (most recent = highest weight)."""
    if not values:
        return 0.0
    n = len(values)
    weights = [decay ** (n - 1 - i) for i in range(n)]
    return sum(v * w for v, w in zip(values, weights)) / sum(weights)


def _weighted_std(values, decay=DECAY_FACTOR):
    """Weighted standard deviation."""
    if len(values) < 2:
        return 10.0
    avg = _weighted_avg(values, decay)
    n = len(values)
    weights = [decay ** (n - 1 - i) for i in range(n)]
    w_sum = sum(weights)
    var = sum(w * (v - avg) ** 2 for v, w in zip(values, weights)) / w_sum
    return math.sqrt(max(var, 0.5))


# ---------------------------------------------------------------------------
# Main projection engine
# ---------------------------------------------------------------------------

def project_player_props(player_logs, team_def_stats=None, prop_lines=None,
                         kalman_state=None, player_adv_stats=None,
                         today_games=None):
    """
    Project player props for all players with sufficient game logs.

    Parameters
    ----------
    player_logs : dict
        {player_id: [game_log, ...]} from organize_player_logs.
    team_def_stats : dict or None
        {team_abbr: {"DEF_RATING", "OPP_PTS", "OPP_REB", "OPP_AST", "PACE", ...}}
    prop_lines : list[dict] or None
        Market prop lines from The Odds API.
    kalman_state : dict or None
        Per-player Kalman state from player_kalman.
    player_adv_stats : dict or None
        {player_id_str: {"USG_PCT", "TS_PCT", "PACE", "MIN", ...}}
        from fetch_player_advanced_stats.
    today_games : dict or None
        {player_id: game_log_dict} for today's actual games.
        Used in backtest to get correct opponent/home/away for each player.
        When None (live mode), falls back to games[-1] (most recent game).

    Returns
    -------
    list[dict]
        Projections with picks where applicable.
    """
    projections = []

    # Compute league averages for opponent adjustment
    league_avg = {}
    if team_def_stats:
        all_opp_keys = {"OPP_PTS", "OPP_REB", "OPP_AST", "OPP_FG3M", "OPP_TOV",
                        "OPP_STL", "OPP_BLK", "PACE", "DEF_RATING"}
        for k in all_opp_keys:
            vals = [s.get(k, 0) for s in team_def_stats.values() if s.get(k)]
            league_avg[k] = np.mean(vals) if vals else 0.0

    # Index prop lines by (first_name, last_name, market)
    line_lookup = {}
    if prop_lines:
        for pl in prop_lines:
            nk = _name_key(pl.get("player", ""))
            key = (nk[0], nk[1], pl.get("market", ""))
            line_lookup[key] = pl

    for pid, games in player_logs.items():
        if not games:
            continue

        recent = games[-ROLLING_WINDOW:]
        name = games[-1].get("player_name", "Unknown")
        team = games[-1].get("team", "")

        # Use today's actual game info if available (backtest mode),
        # otherwise fall back to most recent game (live mode).
        today_game = (today_games or {}).get(pid)
        if today_game:
            latest_opp = today_game.get("opp", "")
            is_home = today_game.get("is_home", True)
            game_date = today_game.get("game_date", "")
        else:
            latest_opp = games[-1].get("opp", "")
            is_home = games[-1].get("is_home", True)
            game_date = games[-1].get("game_date", "")

        # Filter to games with meaningful minutes
        qualified = [g for g in recent if g.get("min", 0) >= MIN_MINUTES]

        # Get this player's advanced stats (if available)
        adv = (player_adv_stats or {}).get(str(pid), {})

        # --- Per-minute rates ---
        rates = compute_per_minute_rates(qualified)

        # --- Projected minutes ---
        is_b2b = detect_b2b_from_game_logs(games, game_date)
        proj_min = project_minutes(qualified, adv_stats=adv, is_b2b=is_b2b)

        if proj_min < 12:
            continue  # Skip players projected for very few minutes

        # --- Project each individual market ---
        for market, stat_key in STAT_KEYS.items():
            # Skip disabled markets (no real edge after calibration)
            if market in DISABLED_MARKETS:
                continue

            min_g = MIN_GAMES.get(market, 5)
            vals = [g.get(stat_key, 0) for g in qualified]

            if len(vals) < min_g:
                continue

            # --- Rate-based projection: per-min rate × projected minutes ---
            rate_key = f"{stat_key}_per_min"
            if rate_key in rates and rates[rate_key] > 0:
                rate_proj = rate_based_projection(rates[rate_key], proj_min)
            else:
                rate_proj = None

            # --- Rolling average (traditional) ---
            rolling_avg = _weighted_avg(vals)
            rolling_std = _weighted_std(vals) * VAR_MULT.get(market, 1.2)

            # --- Blend rate-based with rolling average (30/70) ---
            # Rolling avg is unbiased. Rate-based tends to project low because
            # projected minutes are conservative. Weight rolling more heavily.
            if rate_proj is not None:
                blended_raw = 0.3 * rate_proj + 0.7 * rolling_avg
            else:
                blended_raw = rolling_avg

            # --- Kalman blending ---
            kalman_key = KALMAN_STAT_KEYS.get(market)
            proj, std = _blend_with_kalman(
                kalman_state, str(pid), kalman_key,
                blended_raw, rolling_std,
            )

            # --- Opponent adjustment (per-game stats allowed) ---
            proj = _apply_opp_adjustment(proj, market, latest_opp,
                                         team_def_stats, league_avg)

            # --- Pace adjustment ---
            proj = _apply_pace_adjustment(proj, market, team, latest_opp,
                                          team_def_stats, league_avg)

            # --- Rest adjustment (symmetric: B2B penalty + rest bonus) ---
            if is_b2b:
                proj += B2B_PENALTIES.get(stat_key, 0.0)
            else:
                rest_days = detect_rest_days(games, game_date)
                if rest_days >= 3:
                    proj += REST_BONUS.get(stat_key, 0.0)

            # --- Home/away split ---
            split = compute_home_away_split(games, stat_key)
            if is_home and split.get("home_split_adj"):
                proj += split["home_split_adj"]
            elif not is_home and split.get("away_split_adj"):
                proj += split["away_split_adj"]

            # Volume adjustment removed — was one-directional (only penalized,
            # never boosted), introducing systematic downward bias. The minutes
            # projection via rate × projected_min already handles low-minutes players.

            prop = _make_prop(name, team, market, proj, std, line_lookup, latest_opp)
            if prop:
                projections.append(prop)

        # --- PRA combo (Points + Rebounds + Assists) ---
        if "pts_rebs_asts" in DISABLED_MARKETS:
            continue
        min_g_pra = MIN_GAMES.get("pts_rebs_asts", 5)
        if len(qualified) >= min_g_pra:
            pts_vals = [g.get("pts", 0) for g in qualified]
            reb_vals = [g.get("reb", 0) for g in qualified]
            ast_vals = [g.get("ast", 0) for g in qualified]
            pra_vals = [p + r + a for p, r, a in zip(pts_vals, reb_vals, ast_vals)]

            # Rate-based PRA (30/70 blend — rolling is unbiased)
            pra_rate = (rates.get("pts_per_min", 0) +
                        rates.get("reb_per_min", 0) +
                        rates.get("ast_per_min", 0))
            if pra_rate > 0:
                rate_pra = rate_based_projection(pra_rate, proj_min)
                rolling_avg = 0.3 * rate_pra + 0.7 * _weighted_avg(pra_vals)
            else:
                rolling_avg = _weighted_avg(pra_vals)
            rolling_std = _weighted_std(pra_vals) * VAR_MULT.get("pts_rebs_asts", 1.1)

            proj, std = _blend_pra_with_kalman(
                kalman_state, str(pid),
                rolling_avg, rolling_std,
            )

            proj = _apply_opp_adjustment(proj, "pts_rebs_asts", latest_opp,
                                         team_def_stats, league_avg)
            proj = _apply_pace_adjustment(proj, "pts_rebs_asts", team, latest_opp,
                                          team_def_stats, league_avg)

            # Rest adjustment for PRA (symmetric)
            if is_b2b:
                proj += (B2B_PENALTIES.get("pts", 0) +
                         B2B_PENALTIES.get("reb", 0) +
                         B2B_PENALTIES.get("ast", 0))
            else:
                rest_days = detect_rest_days(games, game_date)
                if rest_days >= 3:
                    proj += (REST_BONUS.get("pts", 0) +
                             REST_BONUS.get("reb", 0) +
                             REST_BONUS.get("ast", 0))

            # Home/away split for PRA
            pts_split = compute_home_away_split(games, "pts")
            reb_split = compute_home_away_split(games, "reb")
            ast_split = compute_home_away_split(games, "ast")
            adj_key = "home_split_adj" if is_home else "away_split_adj"
            pra_split_adj = (pts_split.get(adj_key, 0) +
                             reb_split.get(adj_key, 0) +
                             ast_split.get(adj_key, 0))
            proj += pra_split_adj

            # Volume adjustment removed (same reason as individual markets)

            prop = _make_prop(name, team, "pts_rebs_asts", proj, std, line_lookup, latest_opp)
            if prop:
                projections.append(prop)

    return projections


# ---------------------------------------------------------------------------
# Kalman blending
# ---------------------------------------------------------------------------

def _blend_with_kalman(kalman_state, player_id, stat_key,
                       rolling_avg, rolling_std):
    """
    Blend Kalman projection with rolling average.

    Returns (blended_proj, blended_std).
    """
    if kalman_state is None or stat_key is None:
        return rolling_avg, rolling_std

    kp = get_player_projection(kalman_state, player_id, stat_key,
                               rolling_avg=rolling_avg)

    proj = kp["proj"]
    kalman_var = kp["var"]

    # Use Kalman variance to inform std if source includes Kalman
    if kp["source"] in ("kalman_blend", "kalman_only"):
        # Combine: rolling std captures game-to-game volatility,
        # Kalman var captures baseline uncertainty.
        # Take the larger of the two as the effective std, scaled by VAR_MULT.
        kalman_std = math.sqrt(kalman_var)
        std = max(rolling_std, kalman_std)
    else:
        std = rolling_std

    return proj, std


def _blend_pra_with_kalman(kalman_state, player_id,
                           rolling_avg, rolling_std):
    """
    Blend PRA combo projection using sum of individual Kalman means.
    """
    if kalman_state is None:
        return rolling_avg, rolling_std

    # Get individual Kalman projections
    pts_kp = get_player_projection(kalman_state, player_id, "pts")
    reb_kp = get_player_projection(kalman_state, player_id, "reb")
    ast_kp = get_player_projection(kalman_state, player_id, "ast")

    # Only blend if all three have Kalman data
    if all(kp["source"] != "no_data" for kp in [pts_kp, reb_kp, ast_kp]):
        kalman_pra = pts_kp["proj"] + reb_kp["proj"] + ast_kp["proj"]
        blend = PLAYER_KALMAN_DEFAULTS["kalmanBlend"]
        proj = blend * kalman_pra + (1 - blend) * rolling_avg

        # Combined variance
        total_kalman_var = pts_kp["var"] + reb_kp["var"] + ast_kp["var"]
        kalman_std = math.sqrt(total_kalman_var)
        std = max(rolling_std, kalman_std)
        return proj, std

    return rolling_avg, rolling_std


# ---------------------------------------------------------------------------
# Adjustments: opponent, pace, volume
# ---------------------------------------------------------------------------

def _apply_opp_adjustment(proj, market, opp_team, team_def_stats, league_avg):
    """
    Adjust projection based on opponent per-game stats allowed.

    Uses actual stats allowed (OPP_PTS, OPP_REB, etc.) instead of generic
    DEF_RATING. This is more precise: a team can have a good DEF_RATING
    but still allow a lot of rebounds due to pace.
    """
    if not team_def_stats or not opp_team:
        return proj

    opp_def = team_def_stats.get(opp_team, {})
    opp_key = OPP_STAT_KEY.get(market)
    weight = OPP_ADJ_WEIGHT.get(market, 0.0)

    if opp_key and weight != 0.0:
        opp_val = opp_def.get(opp_key, 0.0)
        avg_val = league_avg.get(opp_key, 0.0)
        if opp_val > 0 and avg_val > 0:
            # Positive diff = opponent allows more than average = boost
            proj += (opp_val - avg_val) * weight

    return proj


def _apply_pace_adjustment(proj, market, player_team, opp_team,
                           team_def_stats, league_avg):
    """
    Adjust projection based on expected game pace.

    Fast-paced games produce more possessions = more stats across the board.
    Uses average of both teams' pace vs. league average.
    """
    if not team_def_stats or not player_team or not opp_team:
        return proj

    weight = PACE_ADJ_WEIGHT.get(market, 0.0)
    if weight == 0.0:
        return proj

    team_pace = team_def_stats.get(player_team, {}).get("PACE", 0)
    opp_pace = team_def_stats.get(opp_team, {}).get("PACE", 0)
    avg_pace = league_avg.get("PACE", 0)

    if team_pace > 0 and opp_pace > 0 and avg_pace > 0:
        expected_pace = (team_pace + opp_pace) / 2
        pace_diff = expected_pace - avg_pace
        proj += pace_diff * weight * proj  # Multiplicative: scale by projection size

    return proj


def _apply_volume_adjustment(proj, adv_stats):
    """
    Adjust projection based on player's minutes context.

    Players averaging fewer minutes than the volume threshold get a
    slight downward adjustment (they might get pulled in blowouts).
    """
    if not adv_stats:
        return proj

    avg_min = adv_stats.get("MIN", 0)
    if avg_min <= 0:
        return proj

    if avg_min < MINUTES_VOLUME_THRESHOLD:
        # Scale down proportionally (e.g., 24 min avg → 24/28 = 0.857 factor)
        volume_factor = avg_min / MINUTES_VOLUME_THRESHOLD
        # Don't scale below 0.75 (25% penalty max)
        volume_factor = max(0.75, volume_factor)
        proj *= volume_factor

    return proj


# ---------------------------------------------------------------------------
# Pick generation
# ---------------------------------------------------------------------------

def _make_prop(name, team, market, proj, std, line_lookup, opp):
    """Build a single prop projection + pick."""
    nk = _name_key(name)
    line_key = (nk[0], nk[1], market)
    line_data = line_lookup.get(line_key)

    line = None
    if line_data:
        line = line_data.get("line")

    result = {
        "player": name,
        "team": team,
        "opp": opp,
        "market": market,
        "proj": round(proj, 1),
        "std": round(std, 1),
        "line": line,
        "pick": "PASS",
        "edge": None,
        "pCover": None,
        "conf": "low",
    }

    if line is not None and std > 0:
        diff = proj - line
        z = diff / std
        p_over = float(t_dist.cdf(z, df=PROP_T_DF))
        p_under = 1.0 - p_over

        best_p = max(p_over, p_under)
        result["edge"] = round(diff, 1)
        result["pCover"] = round(best_p, 3)

        mkt_thresh = MARKET_THRESHOLDS.get(market, {"high": 0.58, "elite": 0.64})
        if best_p >= mkt_thresh["high"]:
            direction = "OVER" if p_over > p_under else "UNDER"

            if market in UNDER_ONLY_MARKETS and direction == "OVER":
                return result

            abs_edge = abs(diff)
            min_e = MIN_EDGE.get(market, 0)
            max_e = MAX_EDGE.get(market, 999)
            if abs_edge < min_e or abs_edge > max_e:
                return result

            min_l = MIN_LINE.get(market, 0)
            if line < min_l:
                return result

            result["pick"] = direction
            result["conf"] = "elite" if best_p >= mkt_thresh["elite"] else "high"

    return result


# ---------------------------------------------------------------------------
# Dashboard output
# ---------------------------------------------------------------------------

def format_props_for_dashboard(projections, date_str="today"):
    """Format prop projections into dashboard-compatible JSON."""
    import datetime as dt

    picks = [p for p in projections if p["pick"] != "PASS"]
    picks.sort(key=lambda p: p.get("pCover", 0) or 0, reverse=True)

    return {
        "sport": "nba",
        "date": date_str,
        "generated": dt.datetime.now().isoformat(),
        "totalProjections": len(projections),
        "totalPicks": len(picks),
        "props": picks,
        "summary": f"{len(picks)} picks from {len(projections)} projections",
    }
