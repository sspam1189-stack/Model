"""
MLBstrikeouts/scripts/game_hits_engine.py
Total game hits projection engine.

Game hits = pitcher_A_hits_allowed + pitcher_B_hits_allowed + bullpen_contribution

Pipeline:
  1. For each game, get both probable starters
  2. Project each pitcher's hits allowed (from their game logs + Kalman)
  3. Estimate bullpen hits based on remaining innings
  4. Sum for total game hits projection
  5. Compare to market line via Student's t
"""

import math
import numpy as np
from scipy.stats import t as t_dist

from defaults import (
    PROP_T_DF, ROLLING_WINDOW, DECAY_FACTOR,
    MARKET_THRESHOLDS, VAR_MULT, MIN_EDGE, MAX_EDGE, MIN_LINE,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# League-average bullpen hits per 9 IP (historical ~8.8-9.0)
DEFAULT_BULLPEN_H_PER_9 = 8.8

# Default starter innings when no data available
DEFAULT_STARTER_IP = 5.5

# Minimum starts required before we trust a pitcher projection
MIN_PITCHER_STARTS = 3

# Minimum projected innings for a starter to be included
MIN_PROJ_IP = 3.0

# Bullpen variance multiplier: bullpen innings are inherently noisy
BULLPEN_VAR_FACTOR = 0.5


# ---------------------------------------------------------------------------
# League average helpers
# ---------------------------------------------------------------------------

def _compute_league_avg(stats_dict, key):
    """
    Compute league average of a stat across all teams.

    Parameters
    ----------
    stats_dict : dict
        {team_abbr: {stat_key: value, ...}}
    key : str
        Stat key to average (e.g. "H_PER_9", "BA").

    Returns
    -------
    float
        League average, or 0.0 if no data.
    """
    if not stats_dict:
        return 0.0
    vals = [s.get(key, 0) for s in stats_dict.values() if s.get(key)]
    return float(np.mean(vals)) if vals else 0.0


# ---------------------------------------------------------------------------
# Rolling average helpers (mirrors props_engine pattern)
# ---------------------------------------------------------------------------

def _weighted_avg(values, decay=None):
    """Exponentially weighted average (most recent = highest weight)."""
    if decay is None:
        decay = DECAY_FACTOR
    if not values:
        return 0.0
    n = len(values)
    weights = [decay ** (n - 1 - i) for i in range(n)]
    return sum(v * w for v, w in zip(values, weights)) / sum(weights)


def _weighted_std(values, decay=None):
    """Weighted standard deviation."""
    if decay is None:
        decay = DECAY_FACTOR
    if len(values) < 2:
        return 2.5  # wide default for game-level uncertainty
    avg = _weighted_avg(values, decay)
    n = len(values)
    weights = [decay ** (n - 1 - i) for i in range(n)]
    w_sum = sum(weights)
    var = sum(w * (v - avg) ** 2 for v, w in zip(values, weights)) / w_sum
    return math.sqrt(max(var, 0.5))


# ---------------------------------------------------------------------------
# Pitcher hits-allowed projection
# ---------------------------------------------------------------------------

def _project_pitcher_hits(pitcher_id, pitcher_logs, kalman_state,
                          adv_stats, opp_team, team_batting_stats,
                          league_avg_ba):
    """
    Project hits allowed by a single pitcher.

    Uses recent game logs with exponential decay, blended with Kalman
    state when available. Adjusts for opponent batting average.

    Parameters
    ----------
    pitcher_id : int or str
        Pitcher ID.
    pitcher_logs : dict
        {pitcher_id: [game_log, ...]}
    kalman_state : dict
        Pitcher Kalman state.
    adv_stats : dict
        {pitcher_id_str: {"h_per_9": float, ...}}
    opp_team : str
        Opponent team abbreviation.
    team_batting_stats : dict
        {team_abbr: {"BA": float, ...}}
    league_avg_ba : float
        League average batting average.

    Returns
    -------
    dict or None
        {"proj_h": float, "proj_ip": float, "var": float}
        None if insufficient data.
    """
    logs = pitcher_logs.get(pitcher_id, [])
    if not logs:
        return None

    recent = logs[-ROLLING_WINDOW:]
    qualified = [g for g in recent if g.get("IP", 0) >= MIN_PROJ_IP]

    if len(qualified) < MIN_PITCHER_STARTS:
        return None

    # --- Hits allowed rolling average ---
    h_vals = [g.get("hits_allowed", g.get("H", 0)) for g in qualified]
    ip_vals = [g.get("IP", 0) for g in qualified]

    rolling_h = _weighted_avg(h_vals)
    rolling_ip = _weighted_avg(ip_vals)
    rolling_std = _weighted_std(h_vals)

    if rolling_ip < MIN_PROJ_IP:
        rolling_ip = DEFAULT_STARTER_IP

    # --- Kalman blending ---
    proj_h = rolling_h
    proj_var = rolling_std ** 2

    if kalman_state and str(pitcher_id) in kalman_state:
        ks = kalman_state[str(pitcher_id)]
        # Look for hits_allowed or h_per_9 Kalman key
        for kk in ("hits_allowed", "h_allowed", "h_per_9"):
            if kk in ks:
                k_mean = ks[kk].get("mean", rolling_h)
                k_var = ks[kk].get("var", proj_var)
                # Inverse-variance weighted blend
                if k_var > 0 and proj_var > 0:
                    w_k = 1.0 / k_var
                    w_r = 1.0 / proj_var
                    proj_h = (w_k * k_mean + w_r * rolling_h) / (w_k + w_r)
                    proj_var = 1.0 / (w_k + w_r)
                break

    # --- Advanced stats anchor (H/9 rate) ---
    pid_str = str(pitcher_id)
    p_adv = (adv_stats or {}).get(pid_str, {})
    h_per_9 = p_adv.get("h_per_9", p_adv.get("H_PER_9", 0))

    if h_per_9 > 0 and rolling_ip > 0:
        rate_proj = h_per_9 * rolling_ip / 9.0
        # Blend: 75% rolling/Kalman, 25% rate anchor
        proj_h = 0.75 * proj_h + 0.25 * rate_proj

    # --- Opponent batting average adjustment ---
    if team_batting_stats and opp_team and league_avg_ba > 0:
        opp_ba = team_batting_stats.get(opp_team, {}).get("BA", 0)
        if opp_ba > 0:
            ba_ratio = (opp_ba - league_avg_ba) / league_avg_ba
            # Scale adjustment by ~15% weight
            proj_h *= (1.0 + 0.15 * ba_ratio)

    # Clamp to reasonable range
    proj_h = max(0.0, proj_h)

    return {
        "proj_h": proj_h,
        "proj_ip": rolling_ip,
        "var": proj_var,
    }


# ---------------------------------------------------------------------------
# Bullpen adjustment
# ---------------------------------------------------------------------------

def _adjust_bullpen_rate(base_rate, team, team_pitching_stats, league_avg_h9):
    """
    Adjust bullpen H/9 based on team pitching quality.

    Good bullpens allow fewer hits; bad bullpens allow more.

    Parameters
    ----------
    base_rate : float
        League-average bullpen H/9.
    team : str
        Team abbreviation.
    team_pitching_stats : dict
        {team_abbr: {"ERA": float, "WHIP": float, "H_PER_9": float, ...}}
    league_avg_h9 : float
        League average H/9.

    Returns
    -------
    float
        Adjusted bullpen H/9 rate.
    """
    if not team_pitching_stats or not team or league_avg_h9 <= 0:
        return base_rate

    team_stats = team_pitching_stats.get(team, {})
    team_h9 = team_stats.get("H_PER_9", team_stats.get("h_per_9", 0))

    if team_h9 <= 0:
        return base_rate

    # Scale bullpen rate by how team compares to league average
    ratio = team_h9 / league_avg_h9
    adjusted = base_rate * ratio

    # Clamp to reasonable range (6.0 - 12.0 H/9)
    return max(6.0, min(12.0, adjusted))


# ---------------------------------------------------------------------------
# Pick generation
# ---------------------------------------------------------------------------

def _american_to_decimal(price):
    """Convert American odds to decimal odds."""
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


def _make_game_hit_prop(home, away, proj, std, line_data,
                        home_pitcher, away_pitcher):
    """
    Build a game hits projection + pick using Student's t.

    Parameters
    ----------
    home : str
        Home team abbreviation.
    away : str
        Away team abbreviation.
    proj : float
        Projected total game hits.
    std : float
        Projection standard deviation.
    line_data : dict or None
        {"line": float, "over_price": int, "under_price": int}
    home_pitcher : dict
        Home pitcher info (id, name).
    away_pitcher : dict
        Away pitcher info (id, name).

    Returns
    -------
    dict or None
        Game hits prop with pick, or None if no line available.
    """
    game_label = f"{away} @ {home}"

    line = None
    over_price = None
    under_price = None

    if line_data:
        line = line_data.get("line")
        over_price = line_data.get("over_price")
        under_price = line_data.get("under_price")

    result = {
        "player": game_label,
        "home_team": home,
        "away_team": away,
        "team": f"{away}@{home}",
        "market": "game_hits",
        "proj": round(proj, 1),
        "std": round(std, 2),
        "line": line,
        "over_price": over_price,
        "under_price": under_price,
        "home_pitcher": home_pitcher.get("name", "TBD") if isinstance(home_pitcher, dict) else str(home_pitcher),
        "away_pitcher": away_pitcher.get("name", "TBD") if isinstance(away_pitcher, dict) else str(away_pitcher),
        "pick": "PASS",
        "edge": None,
        "pCover": None,
        "conf": "low",
    }

    if line is None or std <= 0:
        return result

    diff = proj - line
    z = diff / std
    p_over = float(t_dist.cdf(z, df=PROP_T_DF))
    p_under = 1.0 - p_over

    best_p = max(p_over, p_under)
    direction = "OVER" if p_over > p_under else "UNDER"

    result["edge"] = round(diff, 1)
    result["pCover"] = round(best_p, 3)

    mkt_thresh = MARKET_THRESHOLDS.get("game_hits", {"high": 0.55})
    thresh = mkt_thresh.get("high", 0.55)

    if best_p >= thresh:
        abs_edge = abs(diff)
        min_e = MIN_EDGE.get("game_hits", 0)
        max_e = MAX_EDGE.get("game_hits", 999)
        min_l = MIN_LINE.get("game_hits", 0)

        if abs_edge < min_e or abs_edge > max_e:
            return result

        if line < min_l:
            return result

        pick_price = over_price if direction == "OVER" else under_price

        result["pick"] = direction
        result["conf"] = "high"
        result["odds"] = pick_price
        result["to_win_1u"] = _to_win_1u(pick_price)

    return result


# ---------------------------------------------------------------------------
# Main projection entry point
# ---------------------------------------------------------------------------

def project_game_hits(games_today, pitcher_logs, team_batting_stats,
                      team_pitching_stats, kalman_state, pitcher_adv_stats,
                      game_hit_lines):
    """
    Project total game hits for each game today.

    Parameters
    ----------
    games_today : list[dict]
        [{"home_team": "NYY", "away_team": "BOS",
          "home_pitcher": {"id": 543037, "name": "Gerrit Cole"},
          "away_pitcher": {"id": 519242, "name": "Chris Sale"}}, ...]
    pitcher_logs : dict
        {pitcher_id: [game_log, ...]}
    team_batting_stats : dict
        {team_abbr: {"BA": 0.256, "K_PCT": 0.23, ...}}
    team_pitching_stats : dict
        {team_abbr: {"ERA": 3.85, "WHIP": 1.22, "H_PER_9": 8.2, ...}}
    kalman_state : dict
        Pitcher Kalman state.
    pitcher_adv_stats : dict
        {pitcher_id_str: {"h_per_9": float, ...}}
    game_hit_lines : list[dict]
        [{"event_home": "NYY", "event_away": "BOS", "line": 16.5,
          "over_price": -110, "under_price": -110}, ...]

    Returns
    -------
    list[dict]
        Game hits projections with picks.
    """
    projections = []

    # League averages for normalization
    league_avg_h9 = _compute_league_avg(team_pitching_stats, "H_PER_9")
    league_avg_ba = _compute_league_avg(team_batting_stats, "BA")

    # Fallbacks when team-level stats are sparse
    if league_avg_h9 <= 0:
        league_avg_h9 = 8.5  # historical MLB average ~8.5 H/9
    if league_avg_ba <= 0:
        league_avg_ba = 0.250  # historical MLB average BA

    # Index lines by (home, away)
    line_lookup = {}
    for gl in (game_hit_lines or []):
        key = (gl.get("event_home", ""), gl.get("event_away", ""))
        line_lookup[key] = gl

    for game in games_today:
        home = game.get("home_team", "")
        away = game.get("away_team", "")
        home_pitcher = game.get("home_pitcher") or {}
        away_pitcher = game.get("away_pitcher") or {}

        home_pid = home_pitcher.get("id") if isinstance(home_pitcher, dict) else None
        away_pid = away_pitcher.get("id") if isinstance(away_pitcher, dict) else None

        if home_pid is None or away_pid is None:
            continue

        # Project hits allowed by each starter
        home_p_hits = _project_pitcher_hits(
            home_pid, pitcher_logs, kalman_state,
            pitcher_adv_stats, opp_team=away,
            team_batting_stats=team_batting_stats,
            league_avg_ba=league_avg_ba,
        )
        away_p_hits = _project_pitcher_hits(
            away_pid, pitcher_logs, kalman_state,
            pitcher_adv_stats, opp_team=home,
            team_batting_stats=team_batting_stats,
            league_avg_ba=league_avg_ba,
        )

        if home_p_hits is None or away_p_hits is None:
            continue

        # --- Bullpen contribution ---
        home_starter_ip = home_p_hits["proj_ip"]
        away_starter_ip = away_p_hits["proj_ip"]

        home_bp_ip = max(0, 9.0 - home_starter_ip)
        away_bp_ip = max(0, 9.0 - away_starter_ip)

        # Adjust bullpen H/9 by team pitching quality
        home_bp_h9 = _adjust_bullpen_rate(
            DEFAULT_BULLPEN_H_PER_9, home, team_pitching_stats, league_avg_h9,
        )
        away_bp_h9 = _adjust_bullpen_rate(
            DEFAULT_BULLPEN_H_PER_9, away, team_pitching_stats, league_avg_h9,
        )

        home_bp_hits = home_bp_h9 * home_bp_ip / 9.0
        away_bp_hits = away_bp_h9 * away_bp_ip / 9.0

        # --- Total game hits ---
        total_hits = (home_p_hits["proj_h"] + away_p_hits["proj_h"]
                      + home_bp_hits + away_bp_hits)

        # --- Combined variance ---
        # Starter variance + bullpen variance (bullpen is noisier per IP)
        total_var = (home_p_hits["var"] + away_p_hits["var"]
                     + (home_bp_hits * BULLPEN_VAR_FACTOR) ** 2
                     + (away_bp_hits * BULLPEN_VAR_FACTOR) ** 2)
        total_std = math.sqrt(max(total_var, 1.0)) * VAR_MULT.get("game_hits", 1.1)

        # --- Make pick ---
        line_data = line_lookup.get((home, away))
        prop = _make_game_hit_prop(
            home, away, total_hits, total_std, line_data,
            home_pitcher, away_pitcher,
        )
        if prop:
            projections.append(prop)

    return projections


# ---------------------------------------------------------------------------
# Dashboard output
# ---------------------------------------------------------------------------

def format_game_hits_for_dashboard(projections, date_str="today"):
    """
    Format game hits projections into dashboard-compatible JSON.

    Parameters
    ----------
    projections : list[dict]
        Output from project_game_hits.
    date_str : str
        Date label (e.g. "2026-04-13").

    Returns
    -------
    dict
        Dashboard-ready payload.
    """
    import datetime as dt

    picks = [p for p in projections if p.get("pick") != "PASS"]
    picks.sort(key=lambda p: -(p.get("pCover") or 0))

    for p in projections:
        if not p.get("date"):
            p["date"] = date_str

    return {
        "sport": "mlb",
        "type": "game_hits",
        "season": "2026",
        "mode": "live",
        "model": "kalman_blend",
        "date": date_str,
        "generated": dt.datetime.now().isoformat(),
        "totalProjections": len(projections),
        "totalPicks": len(picks),
        "picks": picks,
        "allProjections": projections,
        "summary": f"{len(picks)} game hit picks from {len(projections)} games",
    }
