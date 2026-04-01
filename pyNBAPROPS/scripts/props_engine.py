# pyNBAPROPS/scripts/props_engine.py
# NBA player prop projection engine — possession-based model.
#
# Projection pipeline:
#   1. Compute per-possession rates (season + recent blend)
#   2. Estimate tonight's possessions from matchup pace
#   3. Base projection = rate × possessions × minute share (× usage for pts)
#   4. Multiplicative matchup adjustment (opponent defense / league avg)
#   5. Kalman blend (smoothed baseline + uncertainty)
#   6. Additive context: B2B/rest, home/away, injury with/without splits
#   7. Compare to market line → Student's t cover probability → pick

import math
import unicodedata
import numpy as np
from scipy.stats import t as t_dist

from defaults import (
    PROP_T_DF, ROLLING_WINDOW, DECAY_FACTOR, MIN_GAMES, MIN_MINUTES,
    MARKET_THRESHOLDS, VAR_MULT, MIN_EDGE, MAX_EDGE, MIN_LINE,
    UNDER_ONLY_MARKETS, DISABLED_MARKETS,
    OPP_STAT_KEY, PER100_STAT_KEY,
    RATE_SEASON_WEIGHT, RATE_RECENT_WEIGHT, RECENT_WINDOW,
)
from player_kalman import get_player_projection, PLAYER_KALMAN_DEFAULTS
from sources.game_context import (
    B2B_PENALTIES, REST_BONUS, detect_b2b_from_game_logs, detect_rest_days,
    compute_home_away_split, project_minutes,
    is_player_out, compute_teammate_absence_boost,
)

# ---------------------------------------------------------------------------
# Name matching
# ---------------------------------------------------------------------------

def _name_key(name):
    """
    Normalize a player name for cross-source matching.

    Strips diacritics so 'Luka Dončić' matches 'Luka Doncic'.

    'LeBron James'      -> ('lebron', 'james')
    'Shai Gilgeous-Alexander' -> ('shai', 'gilgeous-alexander')
    'P.J. Washington'   -> ('p.j.', 'washington')
    'Jaren Jackson Jr.' -> ('jaren', 'jackson')
    """
    name = name.strip()
    if not name:
        return ('', '')
    # Strip diacritics (e.g. Dončić -> Doncic, Nurkić -> Nurkic)
    name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')

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
                         today_games=None, player_positions=None,
                         team_def_by_pos=None,
                         injury_report=None, player_per100=None):
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

    # Compute position-specific league averages for opponent adjustment
    league_avg_by_pos = {}
    if team_def_by_pos:
        for pos in ("G", "F", "C"):
            pos_vals = [t.get(pos, {}) for t in team_def_by_pos.values() if pos in t]
            if pos_vals:
                pos_avg = {}
                for k in ("OPP_PTS", "OPP_REB", "OPP_AST", "OPP_FG3M", "OPP_TOV", "OPP_STL", "OPP_BLK"):
                    kvals = [d.get(k, 0) for d in pos_vals if d.get(k)]
                    pos_avg[k] = np.mean(kvals) if kvals else 0.0
                league_avg_by_pos[pos] = pos_avg

    # Index prop lines by (first_name, last_name, market)
    line_lookup = {}
    # Build team→opponent lookup from prop lines (event_home/event_away)
    team_opp_lookup = {}
    team_home_lookup = {}
    if prop_lines:
        for pl in prop_lines:
            nk = _name_key(pl.get("player", ""))
            key = (nk[0], nk[1], pl.get("market", ""))
            line_lookup[key] = pl
            home = pl.get("event_home", "")
            away = pl.get("event_away", "")
            if home and away:
                team_opp_lookup[home] = away
                team_opp_lookup[away] = home
                team_home_lookup[home] = True
                team_home_lookup[away] = False

    for pid, games in player_logs.items():
        if not games:
            continue

        recent = games[-ROLLING_WINDOW:]
        name = games[-1].get("player_name", "Unknown")
        team = games[-1].get("team", "")

        # Skip players listed as OUT/DOUBTFUL in injury report
        if injury_report and is_player_out(name, team, injury_report):
            continue

        # Use today's actual game info if available (backtest mode),
        # otherwise derive opponent from prop lines, then fall back to last game.
        today_game = (today_games or {}).get(pid)
        if today_game:
            latest_opp = today_game.get("opp", "")
            is_home = today_game.get("is_home", True)
            game_date = today_game.get("game_date", "")
        elif team in team_opp_lookup:
            latest_opp = team_opp_lookup[team]
            is_home = team_home_lookup.get(team, True)
            game_date = ""
        else:
            latest_opp = games[-1].get("opp", "")
            is_home = games[-1].get("is_home", True)
            game_date = games[-1].get("game_date", "")

        # Filter to games with meaningful minutes
        qualified = [g for g in recent if g.get("min", 0) >= MIN_MINUTES]

        # Get this player's advanced stats
        adv = (player_adv_stats or {}).get(str(pid), {})
        p100 = (player_per100 or {}).get(str(pid), {})

        # Get player position for position-specific defense adjustment
        player_pos = (player_positions or {}).get(int(pid)) if player_positions else None

        # --- Projected minutes ---
        is_b2b = detect_b2b_from_game_logs(games, game_date)
        proj_min = project_minutes(qualified, adv_stats=adv, is_b2b=is_b2b)

        if proj_min < 12:
            continue

        # --- Possession context ---
        team_pace = (team_def_stats or {}).get(team, {}).get("PACE", 100.0) or 100.0
        opp_pace = (team_def_stats or {}).get(latest_opp, {}).get("PACE", 100.0) or 100.0
        game_pace = (team_pace + opp_pace) / 2.0
        minute_share = proj_min / 48.0
        on_court_poss = game_pace * minute_share

        # --- Compute recent per-100 rates from game logs ---
        recent_rates = _compute_recent_per100_rates(qualified, adv)

        # --- Precompute injury boost (shared across markets) ---
        inj_boost = {}
        if injury_report:
            inj_boost = compute_teammate_absence_boost(
                team, injury_report,
                player_adv_stats=player_adv_stats,
                player_id=pid,
                player_logs=player_logs,
                player_per100=player_per100,
            )

        # --- Project each individual market ---
        for market, stat_key in STAT_KEYS.items():
            if market in DISABLED_MARKETS:
                continue

            min_g = MIN_GAMES.get(market, 5)
            vals = [g.get(stat_key, 0) for g in qualified]
            if len(vals) < min_g:
                continue

            # --- 1. Per-possession rate (season + recent blend) ---
            p100_key = PER100_STAT_KEY.get(market)
            season_rate = p100.get(p100_key, 0) / 100.0 if p100_key and p100.get(p100_key) else None
            recent_rate = recent_rates.get(stat_key)

            if season_rate is not None and recent_rate is not None:
                rate = RATE_SEASON_WEIGHT * season_rate + RATE_RECENT_WEIGHT * recent_rate
            elif season_rate is not None:
                rate = season_rate
            elif recent_rate is not None:
                rate = recent_rate
            else:
                continue  # no rate data at all

            # --- 2. Base projection from possessions ---
            # Per-100 rates already incorporate usage (a high-usage player has
            # higher per-100 stats). So all stats use on-court possessions.
            base_proj = on_court_poss * rate

            # --- 3. Matchup adjustment (multiplicative) ---
            matchup_factor = _matchup_factor(
                market, latest_opp, team_def_stats, league_avg,
                player_pos=player_pos,
                team_def_by_pos=team_def_by_pos,
                league_avg_by_pos=league_avg_by_pos,
            )
            proj = base_proj * matchup_factor

            # --- 4. Kalman blend ---
            rolling_std = _weighted_std(vals) * VAR_MULT.get(market, 1.2)
            kalman_key = KALMAN_STAT_KEYS.get(market)
            proj, std = _blend_with_kalman(
                kalman_state, str(pid), kalman_key,
                proj, rolling_std,
            )

            # --- 5. Additive context adjustments ---
            # B2B / rest
            if is_b2b:
                proj += B2B_PENALTIES.get(stat_key, 0.0)
            else:
                rest_days = detect_rest_days(games, game_date)
                if rest_days >= 3:
                    proj += REST_BONUS.get(stat_key, 0.0)

            # Home/away split
            split = compute_home_away_split(games, stat_key)
            if is_home and split.get("home_split_adj"):
                proj += split["home_split_adj"]
            elif not is_home and split.get("away_split_adj"):
                proj += split["away_split_adj"]

            # Injury with/without diff
            if inj_boost:
                proj += inj_boost.get(stat_key, 0.0)

            prop = _make_prop(name, team, market, proj, std, line_lookup, latest_opp)
            if prop:
                projections.append(prop)

        # --- PRA combo (Points + Rebounds + Assists) ---
        if "pts_rebs_asts" in DISABLED_MARKETS:
            continue
        min_g_pra = MIN_GAMES.get("pts_rebs_asts", 5)
        if len(qualified) >= min_g_pra:
            def _get_proj(market):
                for p in reversed(projections):
                    if p.get("player") == name and p.get("market") == market:
                        return p.get("proj")
                return None

            proj_pts = _get_proj("points")
            proj_reb = _get_proj("rebounds")
            proj_ast = _get_proj("assists")

            if proj_pts is None or proj_reb is None or proj_ast is None:
                continue

            proj = proj_pts + proj_reb + proj_ast
            pra_vals = [g.get("pts", 0) + g.get("reb", 0) + g.get("ast", 0)
                        for g in qualified]
            std = _weighted_std(pra_vals) * VAR_MULT.get("pts_rebs_asts", 1.1)

            prop = _make_prop(name, team, "pts_rebs_asts", proj, std, line_lookup, latest_opp)
            if prop:
                pra_direction = prop.get("pick")
                if pra_direction not in ("PASS", None):
                    same_direction = any(
                        p for p in projections
                        if p.get("player") == name
                        and p.get("market") in ("points", "rebounds", "assists")
                        and p.get("pick") == pra_direction
                    )
                    if not same_direction:
                        prop["pick"] = "PASS"
                        prop["conf"] = "low"
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
# Possession-based helpers
# ---------------------------------------------------------------------------

def _estimate_poss_from_game(g, player_pace=100.0):
    """Estimate a player's on-court possessions from a single game log."""
    mins = g.get("min", 0)
    if mins <= 0:
        return 0.0
    # Best estimate: pace × minute_share
    return player_pace * (mins / 48.0)


def _compute_recent_per100_rates(qualified_games, adv_stats=None):
    """
    Compute per-100-possession rates from recent game logs.

    Returns {stat_key: rate_per_possession} (divide by 100 to get per-poss).
    """
    if not qualified_games:
        return {}

    pace = (adv_stats or {}).get("PACE", 100.0) or 100.0
    stats = ("pts", "reb", "ast", "fg3m", "stl", "blk", "tov")
    totals = {s: 0.0 for s in stats}
    total_poss = 0.0

    for g in qualified_games:
        poss = _estimate_poss_from_game(g, pace)
        if poss <= 0:
            continue
        total_poss += poss
        for s in stats:
            totals[s] += g.get(s, 0)

    if total_poss <= 0:
        return {}

    # Return as per-possession (not per-100)
    return {s: totals[s] / total_poss for s in stats}


def _matchup_factor(market, opp_team, team_def_stats, league_avg,
                    player_pos=None, team_def_by_pos=None, league_avg_by_pos=None):
    """
    Multiplicative matchup factor: opp_allowed / league_avg.

    Returns >1 for soft defense (boost), <1 for tough defense (suppress).
    Uses position-specific stats when available, falls back to team-total.
    """
    if not team_def_stats or not opp_team:
        return 1.0

    opp_key = OPP_STAT_KEY.get(market)
    if not opp_key:
        return 1.0

    # Try position-specific first
    if player_pos and team_def_by_pos and league_avg_by_pos:
        opp_pos_def = (team_def_by_pos.get(opp_team) or {}).get(player_pos, {})
        pos_avg = (league_avg_by_pos.get(player_pos) or {})
        pos_opp_val = opp_pos_def.get(opp_key, 0)
        pos_avg_val = pos_avg.get(opp_key, 0)
        if pos_opp_val > 0 and pos_avg_val > 0:
            return pos_opp_val / pos_avg_val

    # Fallback: team-total
    opp_val = (team_def_stats.get(opp_team) or {}).get(opp_key, 0)
    avg_val = league_avg.get(opp_key, 0)
    if opp_val > 0 and avg_val > 0:
        return opp_val / avg_val

    return 1.0


# ---------------------------------------------------------------------------
# Pick generation
# ---------------------------------------------------------------------------

def _american_to_decimal(price):
    """Convert American odds to decimal odds (e.g. -110 -> 1.909, +150 -> 2.5)."""
    if price is None:
        return None
    price = int(price)
    if price > 0:
        return 1.0 + price / 100.0
    elif price < 0:
        return 1.0 + 100.0 / abs(price)
    return None


def _to_win_1u(price):
    """Calculate wager needed to win 1 unit at given American odds."""
    if price is None:
        return None
    price = int(price)
    if price > 0:
        return round(100.0 / price, 2)
    elif price < 0:
        return round(abs(price) / 100.0, 2)
    return None


def _make_prop(name, team, market, proj, std, line_lookup, opp):
    """Build a single prop projection + pick."""
    nk = _name_key(name)
    line_key = (nk[0], nk[1], market)
    line_data = line_lookup.get(line_key)

    line = None
    over_price = None
    under_price = None
    if line_data:
        line = line_data.get("line")
        over_price = line_data.get("over_price")
        under_price = line_data.get("under_price")

    result = {
        "player": name,
        "team": team,
        "opp": opp,
        "market": market,
        "proj": round(proj, 1),
        "std": round(std, 1),
        "line": line,
        "over_price": over_price,
        "under_price": under_price,
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

        mkt_thresh = MARKET_THRESHOLDS.get(market, {"high": 0.58})
        direction = "OVER" if p_over > p_under else "UNDER"

        # Directional threshold: use high_under for UNDER if available
        thresh = mkt_thresh.get("high_under" if direction == "UNDER" else "high",
                                mkt_thresh["high"])
        if best_p >= thresh:

            if market in UNDER_ONLY_MARKETS and direction == "OVER":
                return result

            abs_edge = abs(diff)
            min_e = MIN_EDGE.get(market, 0)
            max_e = MAX_EDGE.get(market, 999)

            # Directional edge overrides for points
            if market == "points":
                if direction == "OVER":
                    min_e, max_e = 4.0, 5.6  # inclusive 5.5 (<=5.5)
                else:
                    min_e, max_e = 4.5, 6.5

            if abs_edge < min_e or abs_edge > max_e:
                return result

            min_l = MIN_LINE.get(market, 0)
            if line < min_l:
                return result

            result["pick"] = direction
            result["conf"] = "high"

            # Attach the relevant odds for the picked direction
            pick_price = over_price if direction == "OVER" else under_price
            result["odds"] = pick_price
            result["to_win_1u"] = _to_win_1u(pick_price)

    return result


# ---------------------------------------------------------------------------
# Dashboard output
# ---------------------------------------------------------------------------

def format_props_for_dashboard(projections, date_str="today"):
    """Format prop projections into dashboard-compatible JSON."""
    import datetime as dt

    picks = [p for p in projections if p["pick"] != "PASS"]
    picks.sort(key=lambda p: p.get("pCover", 0) or 0, reverse=True)

    # Ensure every pick has a date field for dashboard filtering
    for p in picks:
        if not p.get("date"):
            p["date"] = date_str

    # All projections for today (including PASS) — used by Games Explorer
    # Only include players who have a prop line (actually playing today)
    today_all = [p for p in projections if p.get("market") != "pts_rebs_asts" and p.get("line") is not None]
    for p in today_all:
        if not p.get("date"):
            p["date"] = date_str
    today_all.sort(key=lambda p: (p.get("team") or "", p.get("market") or "", -(p.get("pCover") or 0)))

    return {
        "sport": "nba",
        "type": "player_props",
        "season": "2025-26",
        "mode": "live",
        "model": "kalman_blend",
        "date": date_str,
        "generated": dt.datetime.now().isoformat(),
        "totalProjections": len(projections),
        "totalPicks": len(picks),
        "props": picks,
        "todayProjections": today_all,
        "summary": f"{len(picks)} picks from {len(projections)} projections",
    }
