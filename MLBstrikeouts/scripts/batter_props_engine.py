# MLBstrikeouts/scripts/batter_props_engine.py
# MLB batter total bases projection engine with Monte Carlo simulation.
#
# Projection pipeline:
#   1. For each batter in confirmed lineups with a TB prop line:
#   2. Project plate appearances from lineup slot + game pace
#   3. Model per-PA outcome probabilities (K, BB, HBP, HR, XBH, 1B, Out)
#      adjusted for pitcher matchup, park factors, weather
#   4. Compute expected total bases (analytical)
#   5. Blend rate-model E[TB] with Kalman-smoothed baseline (60/40)
#   6. Run Monte Carlo simulation (5000 iterations) for distribution
#   7. Compute cover probability vs market prop line
#   8. Apply threshold filters and paired teammate filter
#   9. Generate OVER/UNDER/PASS pick

import math
import unicodedata
import datetime as dt
import numpy as np

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LINEUP_SLOT_PA = {
    1: 4.5, 2: 4.4, 3: 4.3, 4: 4.2, 5: 4.1,
    6: 4.0, 7: 3.9, 8: 3.8, 9: 3.7,
}

XBH_AVG_BASES = 2.1   # ~90% doubles (2) + ~10% triples (3)
SIM_ITERATIONS = 5000
LEAGUE_AVG_RUNS = 4.5  # for pace factor

# League average fallback rates
LEAGUE_AVG_K_PCT = 0.225
LEAGUE_AVG_BB_PCT = 0.08
LEAGUE_AVG_HBP_RATE = 0.01
LEAGUE_AVG_HR_RATE = 0.035
LEAGUE_AVG_XBH_RATE = 0.05
LEAGUE_AVG_SINGLE_RATE = 0.15
LEAGUE_AVG_BARREL_PCT = 0.065
LEAGUE_AVG_HARD_HIT_PCT = 0.35
LEAGUE_AVG_ISO = 0.150

# Cover probability threshold for picks
PCOVER_THRESHOLD = 0.58

# Rolling window for batter game logs
ROLLING_WINDOW = 15
DECAY_FACTOR = 0.92

# Kalman blend ratio: rate model vs Kalman baseline
RATE_MODEL_WEIGHT = 0.60
KALMAN_WEIGHT = 0.40

# Empirical TB std (game-to-game variance in total bases)
EMPIRICAL_TB_STD = 1.8


# ---------------------------------------------------------------------------
# Name matching (mirrors props_engine._name_key)
# ---------------------------------------------------------------------------

def _name_key(name):
    """
    Normalize a player name for cross-source matching.

    Strips diacritics so names match across sources.

    'Mike Trout'       -> ('mike', 'trout')
    'Ronald Acuna Jr.' -> ('ronald', 'acuna')
    """
    name = name.strip()
    if not name:
        return ('', '')
    # Strip diacritics (e.g. accented characters -> ASCII)
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
        return EMPIRICAL_TB_STD
    avg = _weighted_avg(values, decay)
    n = len(values)
    weights = [decay ** (n - 1 - i) for i in range(n)]
    w_sum = sum(weights)
    var = sum(w * (v - avg) ** 2 for v, w in zip(values, weights)) / w_sum
    return math.sqrt(max(var, 0.5))


# ---------------------------------------------------------------------------
# PA projection
# ---------------------------------------------------------------------------

def project_pa(batter_logs, lineup_slot, team_implied_runs=None,
               opp_pitcher_k_pct=None):
    """
    Project plate appearances for today's game.

    Parameters
    ----------
    batter_logs : list[dict]
        Recent game logs for this batter.
    lineup_slot : int or None
        1-9 batting order position, or None if unknown.
    team_implied_runs : float or None
        Vegas implied team run total (from game lines).
    opp_pitcher_k_pct : float or None
        Opposing pitcher's K% (high-K pitcher = fewer baserunners =
        slight PA reduction for later lineup slots).

    Returns
    -------
    float
        Projected plate appearances.
    """
    # Base PA from lineup slot
    if lineup_slot and lineup_slot in LINEUP_SLOT_PA:
        base_pa = LINEUP_SLOT_PA[lineup_slot]
    else:
        # Fallback: rolling 15-game average PA
        if batter_logs:
            recent = batter_logs[-ROLLING_WINDOW:]
            pa_vals = [g.get("pa", g.get("PA", 0)) for g in recent]
            pa_vals = [v for v in pa_vals if v > 0]
            if pa_vals:
                base_pa = _weighted_avg(pa_vals)
            else:
                base_pa = 3.8  # conservative fallback
        else:
            base_pa = 3.8

    # Adjust for game pace: team_implied_runs / LEAGUE_AVG_RUNS
    pace_factor = 1.0
    if team_implied_runs and team_implied_runs > 0:
        pace_factor = team_implied_runs / LEAGUE_AVG_RUNS
        pace_factor = max(0.85, min(1.15, pace_factor))

    projected = base_pa * pace_factor

    # High-K pitcher adjustment: fewer baserunners = fewer PA for later slots
    if opp_pitcher_k_pct and lineup_slot and lineup_slot >= 5:
        # If pitcher K% is well above league average, slight PA reduction
        k_excess = (opp_pitcher_k_pct - LEAGUE_AVG_K_PCT) / LEAGUE_AVG_K_PCT
        if k_excess > 0:
            # Max ~3% reduction for extreme K pitchers in late lineup spots
            pa_adj = 1.0 - min(k_excess * 0.05, 0.03)
            projected *= pa_adj

    return projected


# ---------------------------------------------------------------------------
# Outcome rate modeling
# ---------------------------------------------------------------------------

def model_outcome_rates(batter_splits, savant_batter, savant_pitcher,
                        pitcher_hand, park_factors, weather_mult,
                        team_abbr):
    """
    Compute per-PA outcome probabilities: K, BB, HBP, HR, XBH, 1B, Out.

    Parameters
    ----------
    batter_splits : dict
        Batter's split data: {"vs_lhp": {"k_pct": ..., "bb_pct": ...,
        "hr_rate": ..., "xbh_rate": ..., "single_rate": ..., "hbp_rate": ...},
        "vs_rhp": {...}}
    savant_batter : dict
        Savant metrics for batter: {"barrel_pct": ..., "hard_hit_pct": ...,
        "k_pct": ..., "bb_pct": ...}
    savant_pitcher : dict
        Savant metrics for pitcher: {"barrel_pct": ..., "hard_hit_pct": ...,
        "k_pct": ..., "bb_pct": ...}
    pitcher_hand : str
        "L" or "R" — pitcher's throwing hand.
    park_factors : dict
        {"hr": float, "xbh": float} park-specific factors (1.0 = neutral).
    weather_mult : float
        Weather multiplier from compute_weather_multiplier (1.0 = neutral).
    team_abbr : str
        Batter's team abbreviation (for park context).

    Returns
    -------
    dict
        {"k": float, "bb": float, "hbp": float, "hr": float,
         "xbh": float, "single": float, "out": float}
        All values are probabilities summing to 1.0.
    """
    # Select batter splits vs pitcher hand
    split_key = "vs_lhp" if pitcher_hand == "L" else "vs_rhp"
    splits = (batter_splits or {}).get(split_key, {})

    # Start with batter split rates, fall back to league averages
    k_rate = splits.get("k_pct", 0) or LEAGUE_AVG_K_PCT
    bb_rate = splits.get("bb_pct", 0) or LEAGUE_AVG_BB_PCT
    hbp_rate = splits.get("hbp_rate", 0) or LEAGUE_AVG_HBP_RATE
    hr_rate = splits.get("hr_rate", 0) or LEAGUE_AVG_HR_RATE
    xbh_rate = splits.get("xbh_rate", 0) or LEAGUE_AVG_XBH_RATE
    single_rate = splits.get("single_rate", 0) or LEAGUE_AVG_SINGLE_RATE

    # --- Adjust K rate: log5-style blend of batter K% and pitcher K% ---
    pitcher_k = (savant_pitcher or {}).get("k_pct", 0) or LEAGUE_AVG_K_PCT
    batter_k = k_rate
    # log5: (p_b * p_p / p_lg) / (p_b * p_p / p_lg + (1-p_b)*(1-p_p)/(1-p_lg))
    numerator = batter_k * pitcher_k / LEAGUE_AVG_K_PCT
    denominator = numerator + ((1 - batter_k) * (1 - pitcher_k) / (1 - LEAGUE_AVG_K_PCT))
    if denominator > 0:
        k_rate = numerator / denominator
    # Clamp K rate to reasonable range
    k_rate = max(0.05, min(0.45, k_rate))

    # --- Adjust HR rate ---
    pitcher_barrel = (savant_pitcher or {}).get("barrel_pct", 0) or LEAGUE_AVG_BARREL_PCT
    barrel_ratio = pitcher_barrel / LEAGUE_AVG_BARREL_PCT if LEAGUE_AVG_BARREL_PCT > 0 else 1.0
    park_hr = (park_factors or {}).get("hr", 1.0) or 1.0
    # Weather affects power — use the power component of weather multiplier
    # weather_mult > 1.0 means hitter-friendly, boost HR
    weather_power = 1.0 + (weather_mult - 1.0) * 2.0  # amplify for HR
    weather_power = max(0.85, min(1.15, weather_power))

    hr_rate *= barrel_ratio * park_hr * weather_power
    hr_rate = max(0.005, min(0.12, hr_rate))

    # --- Adjust XBH rate ---
    pitcher_hard_hit = (savant_pitcher or {}).get("hard_hit_pct", 0) or LEAGUE_AVG_HARD_HIT_PCT
    hard_hit_ratio = pitcher_hard_hit / LEAGUE_AVG_HARD_HIT_PCT if LEAGUE_AVG_HARD_HIT_PCT > 0 else 1.0
    park_xbh = (park_factors or {}).get("xbh", 1.0) or 1.0

    xbh_rate *= hard_hit_ratio * park_xbh
    xbh_rate = max(0.01, min(0.12, xbh_rate))

    # --- Adjust BB rate: blend batter BB% with pitcher BB% ---
    pitcher_bb = (savant_pitcher or {}).get("bb_pct", 0) or LEAGUE_AVG_BB_PCT
    bb_blend = 0.6 * bb_rate + 0.4 * pitcher_bb
    bb_rate = max(0.02, min(0.20, bb_blend))

    # --- Adjust single rate: remaining contact after HR and XBH ---
    # Single rate is more about contact quality and BABIP
    single_rate *= weather_mult  # slight weather effect on singles
    single_rate = max(0.05, min(0.25, single_rate))

    # --- Normalize so all rates sum to 1.0 ---
    # Out rate is the remainder
    non_out = k_rate + bb_rate + hbp_rate + hr_rate + xbh_rate + single_rate
    if non_out >= 1.0:
        # Scale down proportionally, keeping relative ratios
        scale = 0.92 / non_out  # leave 8% for outs minimum
        k_rate *= scale
        bb_rate *= scale
        hbp_rate *= scale
        hr_rate *= scale
        xbh_rate *= scale
        single_rate *= scale
        non_out = k_rate + bb_rate + hbp_rate + hr_rate + xbh_rate + single_rate

    out_rate = 1.0 - non_out

    return {
        "k": k_rate,
        "bb": bb_rate,
        "hbp": hbp_rate,
        "hr": hr_rate,
        "xbh": xbh_rate,
        "single": single_rate,
        "out": out_rate,
    }


# ---------------------------------------------------------------------------
# Expected total bases (analytical)
# ---------------------------------------------------------------------------

def compute_expected_tb(projected_pa, outcome_rates):
    """
    Compute expected total bases analytically.

    TB = PA * (1*single + XBH_AVG_BASES*xbh + 4*hr)

    Parameters
    ----------
    projected_pa : float
    outcome_rates : dict
        From model_outcome_rates.

    Returns
    -------
    float
        Expected total bases.
    """
    tb_per_pa = (
        1.0 * outcome_rates["single"]
        + XBH_AVG_BASES * outcome_rates["xbh"]
        + 4.0 * outcome_rates["hr"]
    )
    return projected_pa * tb_per_pa


# ---------------------------------------------------------------------------
# Monte Carlo simulation
# ---------------------------------------------------------------------------

def simulate_tb_distribution(projected_pa, outcome_rates, n_sims=SIM_ITERATIONS):
    """
    Monte Carlo simulation of total bases distribution.

    For each simulation:
      1. Draw PA from Poisson(projected_pa) for realistic PA variance
      2. Draw outcomes from Multinomial(pa, rates)
      3. TB = hr*4 + xbh*2 + single*1  (use 2 for xbh, not 2.1, for discrete)

    Parameters
    ----------
    projected_pa : float
    outcome_rates : dict
    n_sims : int

    Returns
    -------
    np.ndarray
        Array of simulated TB values, shape (n_sims,).
    """
    # Build probability vector in order: [k, bb, hbp, hr, xbh, single, out]
    probs = np.array([
        outcome_rates["k"],
        outcome_rates["bb"],
        outcome_rates["hbp"],
        outcome_rates["hr"],
        outcome_rates["xbh"],
        outcome_rates["single"],
        outcome_rates["out"],
    ])

    # Ensure probs sum to 1.0 (handle floating point)
    probs = probs / probs.sum()

    # Vectorized: draw all PA counts at once
    sim_pas = np.random.poisson(projected_pa, size=n_sims)

    # For each sim, draw multinomial outcomes
    # outcomes columns: [n_k, n_bb, n_hbp, n_hr, n_xbh, n_single, n_out]
    results = np.zeros(n_sims)
    for i in range(n_sims):
        if sim_pas[i] == 0:
            continue
        outcomes = np.random.multinomial(sim_pas[i], probs)
        # TB = hr*4 + xbh*2 + single*1
        results[i] = outcomes[3] * 4 + outcomes[4] * 2 + outcomes[5] * 1

    return results


# ---------------------------------------------------------------------------
# Cover probability
# ---------------------------------------------------------------------------

def compute_cover_probability(sim_results, line):
    """
    Compute over/under probabilities from simulation results.

    Pushes (exact hits on line) are split evenly between over and under.

    Parameters
    ----------
    sim_results : np.ndarray
    line : float

    Returns
    -------
    tuple[float, float]
        (p_over, p_under)
    """
    p_over = np.mean(sim_results > line)
    p_under = np.mean(sim_results < line)
    p_push = np.mean(sim_results == line)

    # Split push evenly
    p_over += p_push / 2
    p_under += p_push / 2

    return float(p_over), float(p_under)


# ---------------------------------------------------------------------------
# Unit calculation helper
# ---------------------------------------------------------------------------

def _to_win_1u(price):
    """Convert American odds to payout on 1 unit risked."""
    if price is None:
        return None
    try:
        price = int(price)
    except (ValueError, TypeError):
        return None
    if price > 0:
        return round(price / 100, 2)
    elif price < 0:
        return round(100 / abs(price), 2)
    return None


# ---------------------------------------------------------------------------
# Main projection engine
# ---------------------------------------------------------------------------

def project_batter_tb(batter_logs, lineup_data, prop_lines,
                      kalman_state, savant_batter_rates,
                      savant_pitcher_rates, batter_splits,
                      probable_pitchers, park_factors,
                      weather_by_game, pitch_hands):
    """
    Project batter total bases for all batters in today's confirmed lineups
    who have a TB prop line.

    Parameters
    ----------
    batter_logs : dict
        {batter_name_key or batter_id: [game_log, ...]} with fields like
        game_date, pa/PA, tb/TB, h, hr, 2b, 3b, team, etc.
    lineup_data : dict
        {team_abbr: {"confirmed": bool, "lineup": [{"name": str,
        "slot": int, "batter_id": int}, ...], "implied_runs": float}}
    prop_lines : list[dict]
        [{player, market, line, over_price, under_price, ...}]
        where market == "batter_total_bases".
    kalman_state : dict or None
        Per-batter Kalman state: {batter_key: {"tb": {"mean": float,
        "var": float}}}
    savant_batter_rates : dict
        {batter_key: {"barrel_pct": ..., "hard_hit_pct": ...,
        "k_pct": ..., "bb_pct": ...}}
    savant_pitcher_rates : dict
        {pitcher_key: {"barrel_pct": ..., "hard_hit_pct": ...,
        "k_pct": ..., "bb_pct": ...}}
    batter_splits : dict
        {batter_key: {"vs_lhp": {...}, "vs_rhp": {...}}}
    probable_pitchers : list[dict]
        [{"game_id": str, "home_team": str, "away_team": str,
        "home_pitcher": str, "away_pitcher": str, ...}]
    park_factors : dict
        {team_abbr: {"hr": float, "xbh": float}}
    weather_by_game : dict
        {game_id: {"temp": str, "wind": str, ...}}
    pitch_hands : dict
        {pitcher_name_key or pitcher_id: "L" or "R"}

    Returns
    -------
    list[dict]
        Projections with picks. Each dict contains:
        player, team, opp, market, proj, line, pick, pCover, edge,
        conf, odds, to_win_1u, lineup_slot, projected_pa,
        outcome_rates, sim_mean, sim_std, date.
    """
    from sources.weather import compute_weather_multiplier

    projections = []

    # --- Index prop lines by name key ---
    line_lookup = {}
    if prop_lines:
        for pl in prop_lines:
            mkt = pl.get("market", "")
            if mkt not in ("batter_total_bases", "total_bases"):
                continue
            nk = _name_key(pl.get("player", ""))
            line_lookup[nk] = pl

    # --- Build team -> opponent pitcher mapping from probable_pitchers ---
    team_opp_pitcher = {}   # {team_abbr: {"pitcher_name": str, "opp": str, "game_id": str}}
    if probable_pitchers:
        for gm in probable_pitchers:
            home_team = gm.get("home_team", "")
            away_team = gm.get("away_team", "")
            game_id = gm.get("game_id", "")
            # Support both naming conventions
            home_pitcher = gm.get("home_pitcher_name") or gm.get("home_pitcher", "")
            away_pitcher = gm.get("away_pitcher_name") or gm.get("away_pitcher", "")

            # Home team faces away pitcher
            if away_pitcher:
                team_opp_pitcher[home_team] = {
                    "pitcher_name": away_pitcher,
                    "opp": away_team,
                    "game_id": game_id,
                    "is_home": True,
                }
            # Away team faces home pitcher
            if home_pitcher:
                team_opp_pitcher[away_team] = {
                    "pitcher_name": home_pitcher,
                    "opp": home_team,
                    "game_id": game_id,
                    "is_home": False,
                }

    # --- Iterate through lineups ---
    batters_projected = 0
    picks_count = 0

    for team_abbr, team_info in (lineup_data or {}).items():
        if not team_info.get("confirmed", False):
            continue

        lineup = team_info.get("lineup", [])
        implied_runs = team_info.get("implied_runs")

        opp_ctx = team_opp_pitcher.get(team_abbr)
        if not opp_ctx:
            continue  # No probable pitcher for opponent

        opp_pitcher_name = opp_ctx["pitcher_name"]
        opp_team = opp_ctx["opp"]
        game_id = opp_ctx["game_id"]
        is_home = opp_ctx["is_home"]

        # Get opponent pitcher's Savant data
        opp_pitcher_nk = _name_key(opp_pitcher_name)
        savant_pitcher = (savant_pitcher_rates or {}).get(opp_pitcher_nk, {})
        # Also try string key
        if not savant_pitcher:
            savant_pitcher = (savant_pitcher_rates or {}).get(
                f"{opp_pitcher_nk[0]}_{opp_pitcher_nk[1]}", {}
            )

        # Get pitcher hand
        pitcher_hand = (pitch_hands or {}).get(opp_pitcher_nk, "R")
        if not pitcher_hand:
            pitcher_hand = (pitch_hands or {}).get(
                f"{opp_pitcher_nk[0]}_{opp_pitcher_nk[1]}", "R"
            )

        # Get pitcher K% for PA adjustment
        opp_pitcher_k_pct = savant_pitcher.get("k_pct", None)

        # Park factors for this game
        home_team = team_abbr if is_home else opp_team
        pf = (park_factors or {}).get(home_team, {})

        # Weather multiplier
        weather_mult = 1.0
        if weather_by_game:
            w_data = (weather_by_game.get(game_id)
                      or weather_by_game.get(str(game_id)))
            if w_data:
                weather_mult = compute_weather_multiplier(w_data, home_team)

        for batter_info in lineup:
            batter_name = batter_info.get("name", "")
            lineup_slot = batter_info.get("slot")
            batter_id = batter_info.get("batter_id")

            if not batter_name:
                continue

            batter_nk = _name_key(batter_name)

            # Check if this batter has a prop line
            line_data = line_lookup.get(batter_nk)
            if not line_data:
                continue  # No prop line -> skip

            line = line_data.get("line")
            over_price = line_data.get("over_price")
            under_price = line_data.get("under_price")

            # Get batter game logs
            logs = None
            if batter_logs:
                logs = (batter_logs.get(batter_nk)
                        or batter_logs.get(batter_id)
                        or batter_logs.get(f"{batter_nk[0]}_{batter_nk[1]}"))

            # --- 1. Project PA ---
            proj_pa = project_pa(
                logs or [],
                lineup_slot,
                team_implied_runs=implied_runs,
                opp_pitcher_k_pct=opp_pitcher_k_pct,
            )

            # --- 2. Model outcome rates ---
            b_splits = (batter_splits or {}).get(batter_nk, {})
            if not b_splits:
                b_splits = (batter_splits or {}).get(
                    f"{batter_nk[0]}_{batter_nk[1]}", {}
                )
            savant_batter = (savant_batter_rates or {}).get(batter_nk, {})
            if not savant_batter:
                savant_batter = (savant_batter_rates or {}).get(
                    f"{batter_nk[0]}_{batter_nk[1]}", {}
                )

            rates = model_outcome_rates(
                b_splits, savant_batter, savant_pitcher,
                pitcher_hand, pf, weather_mult, team_abbr,
            )

            # --- 3. Compute E[TB] from rate model ---
            etb = compute_expected_tb(proj_pa, rates)

            # --- 4. Kalman-smoothed baseline ---
            kalman_tb = None
            if kalman_state:
                k_data = (kalman_state.get(batter_nk)
                          or kalman_state.get(str(batter_id))
                          or kalman_state.get(f"{batter_nk[0]}_{batter_nk[1]}"))
                if k_data:
                    tb_state = k_data.get("tb", {})
                    kalman_tb = tb_state.get("mean")

            # --- 5. Blend rate model with Kalman ---
            if kalman_tb is not None and kalman_tb > 0:
                blended_etb = RATE_MODEL_WEIGHT * etb + KALMAN_WEIGHT * kalman_tb
            else:
                blended_etb = etb

            # --- 6. Scale outcome rates to match blended E[TB] ---
            # The rate model gives one E[TB]; Kalman may shift it.
            # Scale the TB-producing rates proportionally so simulation
            # matches the blended expectation.
            original_etb = etb
            if original_etb > 0 and abs(blended_etb - original_etb) > 0.01:
                scale = blended_etb / original_etb
                sim_rates = dict(rates)
                # Scale HR, XBH, single rates
                sim_rates["hr"] *= scale
                sim_rates["xbh"] *= scale
                sim_rates["single"] *= scale
                # Re-normalize
                non_out = (sim_rates["k"] + sim_rates["bb"] + sim_rates["hbp"]
                           + sim_rates["hr"] + sim_rates["xbh"] + sim_rates["single"])
                if non_out >= 1.0:
                    s = 0.92 / non_out
                    for key in ("k", "bb", "hbp", "hr", "xbh", "single"):
                        sim_rates[key] *= s
                    non_out = sum(sim_rates[key] for key in ("k", "bb", "hbp", "hr", "xbh", "single"))
                sim_rates["out"] = 1.0 - non_out
            else:
                sim_rates = dict(rates)

            # --- 7. Run Monte Carlo simulation ---
            sim_results = simulate_tb_distribution(proj_pa, sim_rates)
            sim_mean = float(np.mean(sim_results))
            sim_std = float(np.std(sim_results))

            # --- 8. Compute cover probability ---
            pick = "PASS"
            p_cover = None
            edge = None
            conf = "low"
            pick_odds = None
            to_win = None

            if line is not None:
                p_over, p_under = compute_cover_probability(sim_results, line)

                best_p = max(p_over, p_under)
                direction = "OVER" if p_over > p_under else "UNDER"
                edge_val = round(blended_etb - line, 2)

                p_cover = round(best_p, 3)
                edge = round(edge_val, 1)

                if best_p >= PCOVER_THRESHOLD:
                    # Total bases: UNDER only per memory instructions
                    # (points market equivalent)
                    if direction == "UNDER" or best_p >= 0.62:
                        # Allow OVER only at higher threshold
                        if direction == "OVER" and best_p < 0.62:
                            pass  # keep PASS
                        else:
                            pick = direction
                            conf = "high"
                            pick_odds = over_price if direction == "OVER" else under_price
                            to_win = _to_win_1u(pick_odds)

            # --- Build projection dict ---
            prop = {
                "player": batter_name,
                "team": team_abbr,
                "opp": opp_team,
                "market": "batter_total_bases",
                "proj": round(blended_etb, 1),
                "std": round(sim_std, 1) if sim_std > 0 else EMPIRICAL_TB_STD,
                "line": line,
                "over_price": over_price,
                "under_price": under_price,
                "pick": pick,
                "edge": edge,
                "pCover": p_cover,
                "conf": conf,
                "odds": pick_odds,
                "to_win_1u": to_win,
                "lineup_slot": lineup_slot,
                "projected_pa": round(proj_pa, 1),
                "outcome_rates": {k: round(v, 4) for k, v in rates.items()},
                "sim_mean": round(sim_mean, 1),
                "sim_std": round(sim_std, 2),
                "pitcher_faced": opp_pitcher_name,
                "pitcher_hand": pitcher_hand,
                "weather_mult": round(weather_mult, 3),
                "is_home": is_home,
                "date": None,  # filled in by format function
            }
            projections.append(prop)
            batters_projected += 1
            if pick != "PASS":
                picks_count += 1

    # --- Paired teammate filter ---
    # When 2+ batters from the same team both pick the same direction,
    # keep only the best pCover to avoid correlated plays.
    from collections import defaultdict

    team_picks = defaultdict(list)
    for p in projections:
        if p["pick"] != "PASS":
            team_picks[p["team"]].append(p)

    for team, picks in team_picks.items():
        if len(picks) <= 1:
            continue
        # Sort by pCover descending, keep best only
        picks.sort(key=lambda x: x.get("pCover", 0) or 0, reverse=True)
        for p in picks[1:]:
            p["pick"] = "PASS"
            p["conf"] = "low"
            p["odds"] = None
            p["to_win_1u"] = None

    # Recount after filter
    final_picks = sum(1 for p in projections if p["pick"] != "PASS")
    print(f"  [batter_tb] Projected {batters_projected} batters, {final_picks} picks")

    return projections


# ---------------------------------------------------------------------------
# Dashboard output
# ---------------------------------------------------------------------------

def format_batter_props_for_dashboard(projections, date_str="today"):
    """
    Format batter TB projections into dashboard-compatible JSON.

    Parameters
    ----------
    projections : list[dict]
        From project_batter_tb.
    date_str : str
        Date string for the projections.

    Returns
    -------
    dict
        {"batterProps": [...], "batterProjections": [...], ...}
    """
    # Ensure every projection has a date field
    for p in projections:
        if not p.get("date"):
            p["date"] = date_str

    picks = [p for p in projections if p["pick"] != "PASS"]
    picks.sort(key=lambda p: p.get("pCover", 0) or 0, reverse=True)

    # All projections with a prop line
    all_with_lines = [p for p in projections if p.get("line") is not None]
    all_with_lines.sort(key=lambda p: (
        p.get("team") or "",
        -(p.get("pCover") or 0),
    ))

    return {
        "sport": "mlb",
        "type": "batter_props",
        "season": "2026",
        "mode": "live",
        "model": "monte_carlo_blend",
        "date": date_str,
        "generated": dt.datetime.now().isoformat(),
        "totalProjections": len(projections),
        "totalPicks": len(picks),
        "batterProps": picks,
        "batterProjections": all_with_lines,
        "summary": f"{len(picks)} picks from {len(projections)} projections",
    }
