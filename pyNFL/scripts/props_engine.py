# pyNFL/scripts/props_engine.py
# NFL player prop projection engine — plays→volume→efficiency→output pipeline.
#
# Projection pipeline:
#   1. Game environment provides team-level volume (dropbacks, rush_attempts)
#   2. Player shares allocate volume (target_share, rush_share)
#   3. Player rates convert volume to output (ypa, ypc, catch_rate, etc.)
#   4. Opponent adjustment scales rates by defensive EPA
#   5. Variance: rate_std * volume * VAR_MULT
#   6. Student's t-distribution for cover probability, uniform threshold
#
# Falls back to rolling averages from player_logs when Kalman/environment
# data is unavailable, ensuring backward compatibility during testing.

import json
import math
import os

import numpy as np
from scipy.stats import t as t_dist


# ---------------------------------------------------------------------------
# Adaptive bias calibration (from calibrate_props.py Kalman filter)
# ---------------------------------------------------------------------------

_CALIB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "data", "prop_calibration.json",
)


def _load_calibration():
    """Load per-market bias/var from Kalman state file. Returns empty
    bias map if the file doesn't exist (no correction applied)."""
    try:
        with open(_CALIB_PATH) as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}, {}
    bias = {m: v.get("bias", 0.0) for m, v in state.items()}
    var = {m: v.get("var", 0.0) for m, v in state.items()}
    return bias, var


_BIAS, _BIAS_VAR = _load_calibration()


def _bias_adjust(market, proj):
    """Add Kalman-tracked bias to a raw projection. Bias convention:
    bias = mean(actual - proj), so we ADD it to recenter."""
    return proj + _BIAS.get(market, 0.0)


def _bias_var(market):
    """Posterior variance of the bias estimate (adds to projection std)."""
    return _BIAS_VAR.get(market, 0.0)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROP_T_DF = 4           # Student's t degrees of freedom (heavier tails)
DECAY_FACTOR = 0.88     # Exponential decay per game
ROLLING_WINDOW = 6      # Recent games for rolling averages

# Variance multipliers per market
VAR_MULT = {
    "pass_yds":   1.2,
    "pass_tds":   1.4,
    "rush_yds":   1.3,
    "rush_att":   1.2,
    "rec_yds":    1.4,
    "receptions": 1.2,
}

# Markets disabled from picking (still projected for display, no picks fire).
# Keeping only markets with real edge per 2023-2025 backtest:
#   receptions (+22.6% ROI), rush_att (+16.2%), rush_yds (+10.9%)
# Disabled:
#   pass_tds: structural (std=1.3 on 1-3 TD scale too coarse); 46% WR at -5u
#   rec_yds: marginal (+2.7% ROI, +1.4pp edge); 71% of volume was dragging
#            the overall edge down for almost no profit
#   pass_yds: no picks fire in backtest anyway (kept for completeness)
#
# 2026-08-27 re-audit on REAL book lines only (the backtest otherwise falls
# back to _get_simulated_line, the player's own rolling average, which is not
# a market and is trivially beatable by a projection that regresses):
#   rec_yds:    767-758  50.3%  losing at EVERY threshold. Its apparent
#               +138u UNDER at ~55% was entirely fallback picks. Stays off,
#               and lowering its gate does not help -- the curve is flat.
#   receptions: DISABLED. Only 28 of its 95 picks ever matched a real line
#               and those went 12-16 (42.9%); the 62.1% headline came from
#               67 fallback picks at 70.1%. The rush markets move the other
#               way on real lines (rush_att 82.4%, rush_yds 72.4%), which is
#               what an actual pricing edge looks like -- receptions only
#               beat the naive average, so there is no evidence to bet it.
DISABLED_MARKETS = {"pass_tds", "rec_yds", "pass_yds", "receptions"}

# UNDER-ONLY. The entire prop edge is on the under side, in every market, in
# every season. Measured on the 2023-2025 walk-forward backtest:
#
#   market      UNDER                        OVER
#   rush_att    72-21   77.4%  +48.9u        113-100  53.1%   +3.0u
#   rush_yds   112-51   68.7%  +55.9u         65-76   46.1%  -18.6u
#   receptions  59-36   62.1%  +19.4u          (fires no overs)
#   ------------------------------------------------------------------
#   all        243-108  69.2% +124.2u        178-176  50.3%  -15.6u
#
# The overs are 354 picks that net -15.6u -- pure variance with a rake. The
# mechanism is that books set player lines at the MEAN of a right-skewed
# distribution (a back's yardage has a long upside tail), so the median
# outcome sits below the line and the under is the correct side more often
# than half the time. That skew is structural, not a pricing accident.
#
# Dropping overs takes the book from 421-284 (59.7%) +108.6u to
# 243-108 (69.2%) +124.2u -- more units from half the picks.
UNDER_ONLY = True

# Empirical std floor per market — derived from 2025 season backtest residuals
# (proj - actual). Model-computed std from rolling std × VAR_MULT was producing
# overconfident pCover (claimed 98% → actual 55%). These floors represent true
# game-to-game variance in each market; they're structural features of NFL
# football (multiplicative variance through volume × rate chains), not
# season-specific artifacts.
#
# Final std = sqrt(rolling_std**2 + model_std**2) floored at EMPIRICAL_STD.
EMPIRICAL_STD = {
    "pass_yds":   55.0,   # est; refine when pass_yds picks are backtested
    "pass_tds":    1.3,
    "rush_yds":   38.9,
    "rush_att":    6.2,
    "rec_yds":    32.6,
    "receptions":  1.9,
}

# Single uniform threshold for all markets
# Calibrated from 3-season backtest (2023-2025, 1920 picks) to maximize
# total units. Tuned after adding Kalman bias correction + EMPIRICAL_STD
# floor — thresholds below reflect what the calibrated engine actually
# produces, not raw overconfident pCover.
MARKET_THRESHOLDS = {
    "pass_yds":   0.80,   # no picks fire yet; keep permissive
    "pass_tds":   0.80,   # disabled via DISABLED_MARKETS
    "rush_yds":   0.82,   # +44u vs +42u at 0.80 (filtered 0.80-0.82 losers)
    "rush_att":   0.84,   # +59u vs +44u at 0.80 (clearest signal above 0.84)
    "rec_yds":    0.80,   # flat curve; tighter only sacrifices volume
    "receptions": 0.80,   # 69% WR at every threshold; keep all picks
}

# Opponent adjustment weight (uniform for all markets)
OPP_ADJ_WEIGHT = 0.20

# Minimum sample sizes
MIN_GAMES_PASSER = 3
MIN_GAMES_RUSHER = 3
MIN_GAMES_RECEIVER = 3


# ---------------------------------------------------------------------------
# Name matching (nflfastr uses "K.Murray", Odds API uses "Kyler Murray")
# ---------------------------------------------------------------------------

def _name_key(name):
    """
    Normalize a player name to (first_initial, last_name) for cross-source matching.

    'K.Murray'       -> ('k', 'murray')
    'Kyler Murray'   -> ('k', 'murray')
    'De\\'Von Achane' -> ('d', 'achane')
    'T.J. Watt'      -> ('t', 'watt')
    'A.J. Brown'     -> ('a', 'brown')
    """
    name = name.strip()
    if not name:
        return ('', '')

    # nflfastr format: "K.Murray" or "De.Smith"
    if '.' in name and ' ' not in name:
        parts = name.split('.', 1)
        return (parts[0][0].lower(), parts[1].lower())

    # Full name: "Kyler Murray", "De'Von Achane", "T.J. Watt", "Marvin Harrison Jr."
    parts = name.split()
    # Strip suffixes like Jr., Sr., II, III, IV
    suffixes = {'jr', 'jr.', 'sr', 'sr.', 'ii', 'iii', 'iv', 'v'}
    while len(parts) > 2 and parts[-1].lower().rstrip('.') in suffixes:
        parts.pop()
    if len(parts) >= 2:
        first_initial = parts[0][0].lower()
        last = parts[-1].lower().rstrip('.')
        return (first_initial, last)

    return (name[0].lower(), name.lower())


# ---------------------------------------------------------------------------
# Player game log aggregation
# ---------------------------------------------------------------------------

def build_player_game_logs(pbp_df):
    """
    Aggregate play-by-play data into per-player per-game stat lines.

    Parameters
    ----------
    pbp_df : pd.DataFrame
        Play-by-play data with columns from nflfastr.PBP_COLUMNS.

    Returns
    -------
    dict
        {player_id: [{"game_id", "week", "team", "role", stats...}, ...]}
    """
    import pandas as pd

    logs = {}

    # --- Passers ---
    # CRITICAL: exclude sacks from pass_yds and pass_att.
    # nflfastR codes sacks as pass==1 with negative yards_gained.
    # The betting market "passing yards" does NOT include sack yards.
    # Including sacks was causing ~20-30 yard under-projection per game.
    sack_col = "sack" if "sack" in pbp_df.columns else None
    if sack_col:
        pass_plays = pbp_df[(pbp_df["pass"] == 1) & (pbp_df[sack_col] != 1)].copy()
    else:
        pass_plays = pbp_df[pbp_df["pass"] == 1].copy()
    if not pass_plays.empty:
        passer_games = pass_plays.groupby(
            ["passer_player_id", "passer_player_name", "game_id", "week", "posteam", "defteam"]
        ).agg(
            pass_yds=("yards_gained", "sum"),
            pass_att=("pass", "sum"),
            completions=("complete_pass", "sum"),
            pass_td=("touchdown", "sum"),
            interceptions=("interception", "sum"),
            air_yds=("air_yards", "sum"),
            epa_total=("epa", "sum"),
        ).reset_index()

        for _, row in passer_games.iterrows():
            pid = row["passer_player_id"]
            if pid is None or (isinstance(pid, float) and math.isnan(pid)):
                continue
            if pid not in logs:
                logs[pid] = []
            logs[pid].append({
                "game_id": row["game_id"],
                "week": int(row["week"]),
                "team": row["posteam"],
                "opp": row["defteam"],
                "name": row["passer_player_name"],
                "role": "passer",
                "pass_yds": float(row["pass_yds"]),
                "pass_att": int(row["pass_att"]),
                "completions": int(row["completions"]),
                "pass_td": int(row["pass_td"]),
                "interceptions": int(row["interceptions"]),
                "epa": float(row["epa_total"]),
            })

    # --- Rushers ---
    rush_plays = pbp_df[pbp_df["rush"] == 1].copy()
    if not rush_plays.empty:
        rusher_games = rush_plays.groupby(
            ["rusher_player_id", "rusher_player_name", "game_id", "week", "posteam", "defteam"]
        ).agg(
            rush_yds=("yards_gained", "sum"),
            rush_att=("rush", "sum"),
            rush_td=("touchdown", "sum"),
            epa_total=("epa", "sum"),
        ).reset_index()

        for _, row in rusher_games.iterrows():
            pid = row["rusher_player_id"]
            if pid is None or (isinstance(pid, float) and math.isnan(pid)):
                continue
            if pid not in logs:
                logs[pid] = []
            # Check if this player already has a game entry (e.g., also a passer)
            existing = [g for g in logs[pid] if g["game_id"] == row["game_id"]]
            if existing:
                existing[0]["rush_yds"] = float(row["rush_yds"])
                existing[0]["rush_att"] = int(row["rush_att"])
                existing[0]["rush_td"] = int(row["rush_td"])
            else:
                logs[pid].append({
                    "game_id": row["game_id"],
                    "week": int(row["week"]),
                    "team": row["posteam"],
                    "opp": row["defteam"],
                    "name": row["rusher_player_name"],
                    "role": "rusher",
                    "rush_yds": float(row["rush_yds"]),
                    "rush_att": int(row["rush_att"]),
                    "rush_td": int(row["rush_td"]),
                    "epa": float(row["epa_total"]),
                })

    # --- Receivers ---
    rec_plays = pbp_df[(pbp_df["pass"] == 1) & (pbp_df["receiver_player_id"].notna())].copy()
    if not rec_plays.empty:
        rec_games = rec_plays.groupby(
            ["receiver_player_id", "receiver_player_name", "game_id", "week", "posteam", "defteam"]
        ).agg(
            rec_yds=("yards_gained", "sum"),
            targets=("pass", "sum"),
            receptions=("complete_pass", "sum"),
            rec_td=("touchdown", "sum"),
            air_yds=("air_yards", "sum"),
            yac=("yards_after_catch", "sum"),
            epa_total=("epa", "sum"),
        ).reset_index()

        for _, row in rec_games.iterrows():
            pid = row["receiver_player_id"]
            if pid is None or (isinstance(pid, float) and math.isnan(pid)):
                continue
            if pid not in logs:
                logs[pid] = []
            existing = [g for g in logs[pid] if g["game_id"] == row["game_id"]]
            if existing:
                existing[0]["rec_yds"] = float(row["rec_yds"])
                existing[0]["targets"] = int(row["targets"])
                existing[0]["receptions"] = int(row["receptions"])
                existing[0]["rec_td"] = int(row["rec_td"])
            else:
                logs[pid].append({
                    "game_id": row["game_id"],
                    "week": int(row["week"]),
                    "team": row["posteam"],
                    "opp": row["defteam"],
                    "name": row["receiver_player_name"],
                    "role": "receiver",
                    "rec_yds": float(row["rec_yds"]),
                    "targets": int(row["targets"]),
                    "receptions": int(row["receptions"]),
                    "rec_td": int(row["rec_td"]),
                    "epa": float(row["epa_total"]),
                })

    return logs


# ---------------------------------------------------------------------------
# Rolling average helpers
# ---------------------------------------------------------------------------

def _weighted_avg(values, decay=DECAY_FACTOR):
    """Compute exponentially weighted average (most recent game = highest weight)."""
    if not values:
        return 0.0
    n = len(values)
    weights = [decay ** i for i in range(n)]  # [1.0, 0.88, 0.77, ...]
    weights.reverse()  # oldest first in values list -> newest gets highest weight
    return sum(v * w for v, w in zip(values, weights)) / sum(weights)


def _weighted_std(values, decay=DECAY_FACTOR):
    """Compute weighted standard deviation."""
    if len(values) < 2:
        return 20.0  # High default uncertainty
    avg = _weighted_avg(values, decay)
    n = len(values)
    weights = [decay ** i for i in range(n)]
    weights.reverse()
    w_sum = sum(weights)
    var = sum(w * (v - avg) ** 2 for v, w in zip(values, weights)) / w_sum
    return math.sqrt(max(var, 1.0))


def _weighted_rate_std(rates, decay=DECAY_FACTOR):
    """Weighted std of a list of rate values (e.g. ypa, ypc per game)."""
    if len(rates) < 2:
        return 0.15  # safe default
    n = len(rates)
    weights = [decay ** (n - 1 - i) for i in range(n)]
    w_sum = sum(weights)
    avg = sum(r * w for r, w in zip(rates, weights)) / w_sum
    var = sum(w * (r - avg) ** 2 for r, w in zip(rates, weights)) / w_sum
    return math.sqrt(max(var, 0.01))


# ---------------------------------------------------------------------------
# Fallback: compute rates/shares from game logs
# ---------------------------------------------------------------------------

def _compute_fallback_passer_rates(passer_games):
    """Compute rolling-average passer rates from game logs."""
    ypa_vals = []
    td_rate_vals = []
    comp_pct_vals = []
    dropback_vals = []
    for g in passer_games:
        att = g.get("pass_att", 0)
        if att < 10:
            continue
        ypa_vals.append(g["pass_yds"] / att)
        td_rate_vals.append(g.get("pass_td", 0) / att)
        comp_pct_vals.append(g.get("completions", 0) / att)
        dropback_vals.append(att)
    if not ypa_vals:
        return None
    return {
        "ypa": _weighted_avg(ypa_vals),
        "td_rate": _weighted_avg(td_rate_vals),
        "completion_pct": _weighted_avg(comp_pct_vals),
        "dropbacks": _weighted_avg(dropback_vals),
        "ypa_std": _weighted_rate_std(ypa_vals),
        "td_rate_std": _weighted_rate_std(td_rate_vals),
    }


def _compute_fallback_rusher_rates(rusher_games):
    """Compute rolling-average rusher rates from game logs."""
    ypc_vals = []
    rush_att_vals = []
    for g in rusher_games:
        att = g.get("rush_att", 0)
        if att < 5:
            continue
        ypc_vals.append(g["rush_yds"] / att)
        rush_att_vals.append(att)
    if not ypc_vals:
        return None
    return {
        "ypc": _weighted_avg(ypc_vals),
        "rush_att": _weighted_avg(rush_att_vals),
        "ypc_std": _weighted_rate_std(ypc_vals),
        "rush_att_std": _weighted_rate_std(rush_att_vals),
    }


def _compute_fallback_receiver_rates(receiver_games, passer_games):
    """Compute rolling-average receiver rates from game logs."""
    catch_rate_vals = []
    ypr_vals = []
    target_vals = []
    # Estimate target share: targets / team pass attempts (from passer games)
    team_att_by_game = {}
    for g in passer_games:
        team_att_by_game[g.get("game_id")] = g.get("pass_att", 0)

    for g in receiver_games:
        tgt = g.get("targets", 0)
        rec = g.get("receptions", 0)
        if tgt < 2:
            continue
        catch_rate_vals.append(rec / tgt)
        if rec > 0:
            ypr_vals.append(g["rec_yds"] / rec)
        else:
            ypr_vals.append(0.0)
        target_vals.append(tgt)
    if not catch_rate_vals:
        return None
    return {
        "catch_rate": _weighted_avg(catch_rate_vals),
        "ypr": _weighted_avg(ypr_vals),
        "targets_per_game": _weighted_avg(target_vals),
        "catch_rate_std": _weighted_rate_std(catch_rate_vals),
        "ypr_std": _weighted_rate_std(ypr_vals),
    }


# ---------------------------------------------------------------------------
# Projection engine — plays -> volume -> efficiency -> output
# ---------------------------------------------------------------------------

def project_player_props(player_logs, team_stats, prop_lines=None,
                         kalman_state=None, game_environment=None,
                         player_shares=None, player_rates=None):
    """
    Project player props using the rate-based pipeline.

    Parameters
    ----------
    player_logs : dict
        {player_id: [game_log, ...]} from build_player_game_logs.
    team_stats : dict
        {team_abbr: stats_dict} for opponent strength adjustment.
    prop_lines : list[dict] or None
        Market prop lines: [{"player", "market", "line", "over_price", "under_price"}, ...]
    kalman_state : dict or None
        Per-player Kalman state for shares/rates (if available).
    game_environment : dict or None
        {team_abbr: {"dropbacks": float, "rush_attempts": float}}
        Projected team-level volume. Falls back to rolling averages if None.
    player_shares : dict or None
        {player_id: {"target_share": float, "rush_share": float}}
        Kalman-filtered if available. Falls back to game log computation.
    player_rates : dict or None
        {player_id: {"ypa": float, "completion_pct": float, "td_rate": float,
                      "ypc": float, "catch_rate": float, "ypr": float,
                      "ypa_std": float, ...}}
        Kalman-filtered if available. Falls back to game log computation.

    Returns
    -------
    list[dict]
        Projections with picks where applicable.
    """
    projections = []

    # Build opponent defense EPA rankings for adjustment
    def_pass_ranks = {}
    def_rush_ranks = {}
    for team, st in team_stats.items():
        def_pass_ranks[team] = st.get("passDefEPA", 0.0)
        def_rush_ranks[team] = st.get("rushDefEPA", 0.0)
    avg_pass_def = np.mean(list(def_pass_ranks.values())) if def_pass_ranks else 0.0
    avg_rush_def = np.mean(list(def_rush_ranks.values())) if def_rush_ranks else 0.0

    # Index prop lines by (first_initial, last_name, market)
    line_lookup = {}
    if prop_lines:
        for pl in prop_lines:
            nk = _name_key(pl.get("player", ""))
            key = (nk[0], nk[1], pl.get("market", ""))
            line_lookup[key] = pl

    # Collect all passer games per team (for receiver fallback target_share)
    team_passer_games = {}
    for pid, games in player_logs.items():
        for g in games:
            if g.get("pass_att", 0) >= 10:
                t = g.get("team", "")
                if t not in team_passer_games:
                    team_passer_games[t] = []
                team_passer_games[t].append(g)

    for pid, games in player_logs.items():
        if not games:
            continue

        # Sort by week (most recent last)
        games = sorted(games, key=lambda g: g.get("week", 0))
        recent = games[-ROLLING_WINDOW:]
        name = games[-1].get("name", "Unknown")
        team = games[-1].get("team", "")
        latest_opp = games[-1].get("opp", "")

        # Qualified game subsets
        passer_games = [g for g in recent if g.get("pass_att", 0) >= 10]
        rusher_games = [g for g in recent if g.get("rush_att", 0) >= 5]
        receiver_games = [g for g in recent if g.get("targets", 0) >= 2]

        # Opponent adjustment factors
        opp_pass_def = def_pass_ranks.get(latest_opp, avg_pass_def)
        opp_rush_def = def_rush_ranks.get(latest_opp, avg_rush_def)
        pass_opp_mult = 1.0 + (opp_pass_def - avg_pass_def) * OPP_ADJ_WEIGHT
        rush_opp_mult = 1.0 + (opp_rush_def - avg_rush_def) * OPP_ADJ_WEIGHT

        # --- Look up external data (or fall back to game logs) ---
        env = (game_environment or {}).get(team, {})
        shares = (player_shares or {}).get(pid, {})
        rates = (player_rates or {}).get(pid, {})

        # =================================================================
        # PASSING MARKETS (pass_yds, pass_tds)
        # =================================================================
        if len(passer_games) >= MIN_GAMES_PASSER:
            # Volume: team dropbacks
            dropbacks = env.get("dropbacks")
            if dropbacks is None:
                # Fallback: rolling avg of this passer's attempts
                dropbacks = _weighted_avg([g["pass_att"] for g in passer_games])

            # Rates from external source or fallback
            ypa = rates.get("ypa")
            td_rate = rates.get("td_rate")
            ypa_std = rates.get("ypa_std")
            td_rate_std = rates.get("td_rate_std")

            if ypa is None or td_rate is None:
                fb = _compute_fallback_passer_rates(passer_games)
                if fb:
                    ypa = ypa or fb["ypa"]
                    td_rate = td_rate or fb["td_rate"]
                    ypa_std = ypa_std or fb["ypa_std"]
                    td_rate_std = td_rate_std or fb["td_rate_std"]

            if ypa is not None and ypa > 0 and "pass_yds" not in DISABLED_MARKETS:
                # --- Pass yards: dropbacks * ypa * opp_adj ---
                adj_ypa = ypa * pass_opp_mult
                proj_pass_yds = dropbacks * adj_ypa
                proj_pass_yds = _bias_adjust("pass_yds", proj_pass_yds)
                std_pass_yds = (ypa_std or 0.15) * dropbacks * VAR_MULT["pass_yds"]
                std_pass_yds = max(std_pass_yds, EMPIRICAL_STD["pass_yds"])
                std_pass_yds = math.sqrt(std_pass_yds**2 + _bias_var("pass_yds"))
                prop = _make_prop(name, team, "pass_yds", proj_pass_yds,
                                  std_pass_yds, line_lookup, latest_opp)
                if prop:
                    projections.append(prop)

            if td_rate is not None and td_rate > 0 and "pass_tds" not in DISABLED_MARKETS:
                # --- Pass TDs: dropbacks * td_rate * opp_adj ---
                adj_td_rate = td_rate * pass_opp_mult
                proj_pass_tds = dropbacks * adj_td_rate
                proj_pass_tds = _bias_adjust("pass_tds", proj_pass_tds)
                std_pass_tds = (td_rate_std or 0.02) * dropbacks * VAR_MULT["pass_tds"]
                std_pass_tds = max(std_pass_tds, EMPIRICAL_STD["pass_tds"])
                std_pass_tds = math.sqrt(std_pass_tds**2 + _bias_var("pass_tds"))
                prop = _make_prop(name, team, "pass_tds", proj_pass_tds,
                                  std_pass_tds, line_lookup, latest_opp)
                if prop:
                    projections.append(prop)

        # =================================================================
        # RUSHING MARKETS (rush_att, rush_yds)
        # =================================================================
        if len(rusher_games) >= MIN_GAMES_RUSHER:
            # Volume: team rush attempts
            team_rush_att = env.get("rush_attempts")
            if team_rush_att is None:
                # Fallback: this rusher's own attempts (no team total available)
                team_rush_att = None  # signal to use direct projection

            # Shares and rates
            rush_share = shares.get("rush_share")
            ypc = rates.get("ypc")
            ypc_std = rates.get("ypc_std")

            fb_rush = _compute_fallback_rusher_rates(rusher_games)

            if rush_share is not None and team_rush_att is not None:
                # Rate-based: rush_att = team_rush_att * rush_share
                proj_rush_att = team_rush_att * rush_share
            elif fb_rush:
                # Fallback: rolling average of this player's rush attempts
                proj_rush_att = fb_rush["rush_att"]
            else:
                proj_rush_att = None

            if ypc is None and fb_rush:
                ypc = fb_rush["ypc"]
                ypc_std = fb_rush.get("ypc_std")

            if proj_rush_att is not None and proj_rush_att > 0:
                # --- Rush attempts ---
                rush_att_std = (fb_rush["rush_att_std"] if fb_rush else 2.0) * VAR_MULT["rush_att"]
                # For rush_att, variance is on the count itself, not rate*volume
                std_rush_att = rush_att_std * proj_rush_att / max(
                    fb_rush["rush_att"] if fb_rush else proj_rush_att, 1.0)
                # Simpler: just use weighted std of raw rush_att values
                raw_att_vals = [g.get("rush_att", 0) for g in rusher_games]
                std_rush_att = _weighted_std(raw_att_vals) * VAR_MULT["rush_att"]
                std_rush_att = max(std_rush_att, EMPIRICAL_STD["rush_att"])
                # Apply bias correction after std calc so std doesn't shift
                proj_rush_att_adj = _bias_adjust("rush_att", proj_rush_att)
                std_rush_att = math.sqrt(std_rush_att**2 + _bias_var("rush_att"))

                prop = _make_prop(name, team, "rush_att", proj_rush_att_adj,
                                  std_rush_att, line_lookup, latest_opp)
                if prop:
                    projections.append(prop)

                if ypc is not None and ypc > 0:
                    # --- Rush yards: rush_att * ypc * opp_adj ---
                    # Use unadjusted proj_rush_att as volume input so rush_yds
                    # has its own independent bias correction (avoid double-count)
                    adj_ypc = ypc * rush_opp_mult
                    proj_rush_yds = proj_rush_att * adj_ypc
                    proj_rush_yds = _bias_adjust("rush_yds", proj_rush_yds)
                    std_rush_yds = (ypc_std or 0.5) * proj_rush_att * VAR_MULT["rush_yds"]
                    std_rush_yds = max(std_rush_yds, EMPIRICAL_STD["rush_yds"])
                    std_rush_yds = math.sqrt(std_rush_yds**2 + _bias_var("rush_yds"))
                    prop = _make_prop(name, team, "rush_yds", proj_rush_yds,
                                      std_rush_yds, line_lookup, latest_opp)
                    if prop:
                        projections.append(prop)

        # =================================================================
        # RECEIVING MARKETS (receptions, rec_yds)
        # =================================================================
        if len(receiver_games) >= MIN_GAMES_RECEIVER:
            # Volume: team dropbacks (same as passing)
            dropbacks = env.get("dropbacks")
            if dropbacks is None:
                # Fallback: estimate from team passer games
                team_pass_games = team_passer_games.get(team, [])
                if team_pass_games:
                    dropbacks = _weighted_avg(
                        [g["pass_att"] for g in sorted(team_pass_games, key=lambda x: x.get("week", 0))[-ROLLING_WINDOW:]]
                    )
                else:
                    dropbacks = 35.0  # league average fallback

            # Shares and rates
            target_share = shares.get("target_share")
            catch_rate = rates.get("catch_rate")
            ypr = rates.get("ypr")
            catch_rate_std = rates.get("catch_rate_std")
            ypr_std = rates.get("ypr_std")

            fb_rec = _compute_fallback_receiver_rates(receiver_games, passer_games)

            if target_share is None and fb_rec:
                # Fallback: targets / team dropbacks
                target_share = fb_rec["targets_per_game"] / dropbacks if dropbacks > 0 else 0.0
            if catch_rate is None and fb_rec:
                catch_rate = fb_rec["catch_rate"]
                catch_rate_std = fb_rec.get("catch_rate_std")
            if ypr is None and fb_rec:
                ypr = fb_rec["ypr"]
                ypr_std = fb_rec.get("ypr_std")

            if target_share is not None and target_share > 0 and catch_rate is not None:
                # --- Receptions: dropbacks * target_share * catch_rate * opp_adj ---
                adj_catch_rate = catch_rate * pass_opp_mult
                proj_receptions = dropbacks * target_share * adj_catch_rate
                std_receptions = (catch_rate_std or 0.05) * dropbacks * target_share * VAR_MULT["receptions"]
                std_receptions = max(std_receptions, EMPIRICAL_STD["receptions"])
                proj_receptions_adj = _bias_adjust("receptions", proj_receptions)
                std_receptions = math.sqrt(std_receptions**2 + _bias_var("receptions"))
                prop = _make_prop(name, team, "receptions", proj_receptions_adj,
                                  std_receptions, line_lookup, latest_opp)
                if prop:
                    projections.append(prop)

                if ypr is not None and ypr > 0 and "rec_yds" not in DISABLED_MARKETS:
                    # --- Receiving yards: receptions * ypr * opp_adj ---
                    # (opp_adj already in receptions via catch_rate; apply again
                    #  to ypr would double-count, so ypr stays unadjusted)
                    # Use unadjusted proj_receptions as volume input; rec_yds
                    # has its own bias entry so it's corrected independently.
                    proj_rec_yds = proj_receptions * ypr
                    proj_rec_yds = _bias_adjust("rec_yds", proj_rec_yds)
                    std_rec_yds = (ypr_std or 1.0) * proj_receptions * VAR_MULT["rec_yds"]
                    std_rec_yds = max(std_rec_yds, EMPIRICAL_STD["rec_yds"])
                    std_rec_yds = math.sqrt(std_rec_yds**2 + _bias_var("rec_yds"))
                    prop = _make_prop(name, team, "rec_yds", proj_rec_yds,
                                      std_rec_yds, line_lookup, latest_opp)
                    if prop:
                        projections.append(prop)

    return projections


# ---------------------------------------------------------------------------
# Pick generation — simplified
# ---------------------------------------------------------------------------

def _make_prop(name, team, market, proj, std, line_lookup, opp):
    """
    Build a single prop projection + pick.

    Simplified: uniform threshold, no directional filters, no edge windows,
    no MIN_LINE. If p(cover) >= threshold, pick the direction.
    """
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
        # P(over) using Student's t
        p_over = float(t_dist.cdf(z, df=PROP_T_DF))
        p_under = 1.0 - p_over

        best_p = max(p_over, p_under)
        result["edge"] = round(diff, 1)
        result["pCover"] = round(best_p, 3)

        # Uniform threshold check
        thresh = MARKET_THRESHOLDS.get(market, 0.80)
        if best_p >= thresh:
            direction = "OVER" if p_over > p_under else "UNDER"
            if UNDER_ONLY and direction == "OVER":
                return result          # see UNDER_ONLY for why overs are dead
            pick_price = over_price if direction == "OVER" else under_price

            result["pick"] = direction
            result["conf"] = "high"
            result["odds"] = pick_price

    return result


# ---------------------------------------------------------------------------
# Format for dashboard JSON
# ---------------------------------------------------------------------------

def format_props_for_dashboard(projections, season, week):
    """
    Format prop projections into dashboard-compatible JSON structure.

    Returns
    -------
    dict
        {"season", "week", "props": [...], "summary": str}
    """
    picks = [p for p in projections if p["pick"] != "PASS"]
    picks.sort(key=lambda p: p.get("pCover", 0) or 0, reverse=True)

    return {
        "season": season,
        "week": week,
        "generated": __import__("datetime").datetime.now().isoformat(),
        "totalProjections": len(projections),
        "totalPicks": len(picks),
        "props": picks,
        "summary": f"{len(picks)} picks from {len(projections)} projections",
    }
