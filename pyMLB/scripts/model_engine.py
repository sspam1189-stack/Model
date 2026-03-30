# pyMLB/scripts/model_engine.py
# Standalone MLB score projection engine using ridge regression on pitching/batting features.
import json
import math
import os
import sys

import numpy as np
from scipy.stats import norm

_RidgeCV = None


def _get_ridge_cv():
    global _RidgeCV
    if _RidgeCV is None:
        from sklearn.linear_model import RidgeCV as _RC
        _RidgeCV = _RC
    return _RidgeCV


_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
_REPO_ROOT = os.path.join(_SCRIPT_DIR, "..", "..")
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, os.path.abspath(_REPO_ROOT))

from defaults import (
    DEFAULT_W, DEFAULT_W_VAR, DEFAULT_STATS, BAYES_HYPER,
    FEATURE_COLUMNS, SDIFF_THRESHOLDS, MIN_GP, BURN_IN_DAYS,
    ENGINE_CONFIG, _WEIGHT_KEYS, _FEATURE_KEYS,
)


# ===========================================================================
# Weight Persistence
# ===========================================================================

def load_weights(path):
    """
    Load trained ridge coefficients + metadata from weights.json.

    Returns
    -------
    dict
        Keys: "coefficients", "intercept", "total_coefficients", "total_intercept",
        "alpha", "feature_columns", "n_train", "residual_var", plus metadata.
        Returns DEFAULT_W-based fallback if file doesn't exist.
    """
    if not os.path.isfile(path):
        print(f"  [model_engine] No weights file at {path} — using defaults")
        return _default_weights_dict()

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if "coefficients" not in data or "intercept" not in data:
        print(f"  [model_engine] Invalid weights file at {path} — using defaults")
        return _default_weights_dict()

    return data


def save_weights(weights, path):
    """
    Persist trained ridge coefficients to weights.json.

    Parameters
    ----------
    weights : dict
        Must contain "coefficients", "intercept", "alpha", "feature_columns".
    path : str
        Output file path.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(weights, f, indent=2)
    print(f"  [model_engine] Saved weights to {path}"
          f" ({len(weights.get('coefficients', []))} features,"
          f" alpha={weights.get('alpha', '?')})")


def _default_weights_dict():
    """Build a weights dict from DEFAULT_W for cold start."""
    key_map = dict(zip(FEATURE_COLUMNS, _WEIGHT_KEYS))
    coefficients = [DEFAULT_W.get(key_map.get(col, ""), 0.0) for col in FEATURE_COLUMNS]
    return {
        "coefficients": coefficients,
        "intercept": DEFAULT_W.get("constant", 0.0),
        "total_coefficients": coefficients,  # Same init for total model
        "total_intercept": 9.0,  # ~9 runs/game default
        "alpha": 1.0,
        "feature_columns": FEATURE_COLUMNS,
        "n_train": 0,
        "residual_var": BAYES_HYPER["residualVar"],
        "source": "defaults",
    }


# ===========================================================================
# Feature Engineering
# ===========================================================================

def build_feature_vector(home_stats, away_stats, starter_home, starter_away,
                         bullpen_home, bullpen_away, park_factor, weather,
                         is_home=True):
    """
    Build the 18-feature vector for one matchup (from home team's perspective).

    Parameters
    ----------
    home_stats : dict
        Home team batting stats (wRC_plus, xwOBA, ISO, K_pct, BB_pct, wRC_vs_L, wRC_vs_R, etc.)
    away_stats : dict
        Away team batting stats.
    starter_home : dict
        Home starter data: {name, FIP, xFIP, SIERA, K_BB_pct, IP_per_GS, hand}
    starter_away : dict
        Away starter data.
    bullpen_home : dict
        Home bullpen data: {FIP, K_BB_pct, workload}
    bullpen_away : dict
        Away bullpen data.
    park_factor : float
        Park run factor (1.0 = neutral).
    weather : dict
        {"temperature_adj": float, "wind_adj": float}
    is_home : bool
        True if the team being projected is the home team.

    Returns
    -------
    np.ndarray
        Feature vector of shape (18,), ordered per FEATURE_COLUMNS.
    """
    h = home_stats
    a = away_stats
    sh = starter_home
    sa = starter_away
    bh = bullpen_home
    ba = bullpen_away

    # --- Starter features ---
    starter_fip_diff = sh.get("FIP", 4.5) - sa.get("FIP", 4.5)
    starter_kbb_diff = sh.get("K_BB_pct", 0.08) - sa.get("K_BB_pct", 0.08)
    starter_xfip_diff = sh.get("xFIP", sh.get("FIP", 4.5)) - sa.get("xFIP", sa.get("FIP", 4.5))
    starter_siera_diff = sh.get("SIERA", sh.get("FIP", 4.5)) - sa.get("SIERA", sa.get("FIP", 4.5))

    # Handedness: how well home lineup hits vs away starter's hand,
    # minus how well away lineup hits vs home starter's hand
    # wRC_vs_L = lineup wRC+ vs left-handed pitching
    # wRC_vs_R = lineup wRC+ vs right-handed pitching
    away_hand = sa.get("hand", "R")
    home_hand = sh.get("hand", "R")

    # Home lineup vs away starter
    if away_hand == "R":
        home_bat_vs_starter = h.get("wRC_vs_R", h.get("wRC_plus", 100))
    else:
        home_bat_vs_starter = h.get("wRC_vs_L", h.get("wRC_plus", 100))

    # Away lineup vs home starter
    if home_hand == "R":
        away_bat_vs_starter = a.get("wRC_vs_R", a.get("wRC_plus", 100))
    else:
        away_bat_vs_starter = a.get("wRC_vs_L", a.get("wRC_plus", 100))

    # Normalize by 100 (wRC+ scale) to get [-1, 1]-ish range
    starter_handedness = (home_bat_vs_starter - away_bat_vs_starter) / 100.0

    # --- Bullpen features ---
    bullpen_fip_diff = bh.get("FIP", 4.5) - ba.get("FIP", 4.5)
    bullpen_workload = bh.get("workload", 0.0) - ba.get("workload", 0.0)
    bullpen_kbb_diff = bh.get("K_BB_pct", 0.08) - ba.get("K_BB_pct", 0.08)

    # --- Lineup features ---
    wrc_plus_diff = (h.get("wRC_plus", 100) - a.get("wRC_plus", 100)) / 100.0
    xwoba_diff = h.get("xwOBA", 0.320) - a.get("xwOBA", 0.320)
    iso_diff = h.get("ISO", 0.150) - a.get("ISO", 0.150)

    # Plate discipline: (BB% - K%) differential — positive = home has better discipline
    kbb_bat_diff = (
        (h.get("BB_pct", 0.08) - h.get("K_pct", 0.22))
        - (a.get("BB_pct", 0.08) - a.get("K_pct", 0.22))
    )

    # Platoon feature: wRC+ vs specific pitcher handedness
    if away_hand == "R":
        home_hand_wrc = h.get("wRC_vs_R", h.get("wRC_plus", 100)) / 100.0
    else:
        home_hand_wrc = h.get("wRC_vs_L", h.get("wRC_plus", 100)) / 100.0

    if home_hand == "R":
        away_hand_wrc = a.get("wRC_vs_R", a.get("wRC_plus", 100)) / 100.0
    else:
        away_hand_wrc = a.get("wRC_vs_L", a.get("wRC_plus", 100)) / 100.0

    wrc_vs_hand_diff = home_hand_wrc - away_hand_wrc

    # --- Context features ---
    home_flag = 1.0 if is_home else 0.0
    park_factor_feat = park_factor - 1.0  # Deviation from neutral
    temperature_adj = weather.get("temperature_adj", 0.0)
    wind_adj = weather.get("wind_adj", 0.0)

    # Interaction: starter quality * opposing lineup quality
    starter_x_lineup = starter_fip_diff * (h.get("wRC_plus", 100) - a.get("wRC_plus", 100)) / 100.0

    # Build in FEATURE_COLUMNS order:
    # starter_fip_diff, starter_kbb_diff, starter_xfip_diff, starter_siera_diff,
    # starter_handedness, bullpen_fip_diff, bullpen_workload, bullpen_kbb_diff,
    # wrc_plus_diff, xwoba_diff, iso_diff, kbb_bat_diff, wrc_vs_hand_diff,
    # home_flag, park_factor, temperature_adj, wind_adj, starter_x_lineup
    features = np.array([
        starter_fip_diff,
        starter_kbb_diff,
        starter_xfip_diff,
        starter_siera_diff,
        starter_handedness,
        bullpen_fip_diff,
        bullpen_workload,
        bullpen_kbb_diff,
        wrc_plus_diff,
        xwoba_diff,
        iso_diff,
        kbb_bat_diff,
        wrc_vs_hand_diff,
        home_flag,
        park_factor_feat,
        temperature_adj,
        wind_adj,
        starter_x_lineup,
    ], dtype=np.float64)

    return features


def build_feature_matrix(matchups):
    """
    Build feature matrix for multiple matchups (for training).

    Parameters
    ----------
    matchups : list of dict
        Each dict must contain:
            "home_stats", "away_stats", "starter_home", "starter_away",
            "bullpen_home", "bullpen_away", "park_factor", "weather"
        Optional: "is_home" (defaults to True)

    Returns
    -------
    np.ndarray
        Feature matrix of shape (n_matchups, n_features).
    """
    rows = []
    for m in matchups:
        vec = build_feature_vector(
            m["home_stats"],
            m["away_stats"],
            m["starter_home"],
            m["starter_away"],
            m["bullpen_home"],
            m["bullpen_away"],
            m.get("park_factor", 1.0),
            m.get("weather", {"temperature_adj": 0.0, "wind_adj": 0.0}),
            m.get("is_home", True),
        )
        rows.append(vec)
    return np.array(rows, dtype=np.float64)


# ===========================================================================
# Score Projection
# ===========================================================================

def project_score(features, weights, use_total_model=False):
    """
    Dot product with ridge coefficients.

    Parameters
    ----------
    features : np.ndarray
        Feature vector from build_feature_vector().
    weights : dict
        Loaded weights dict.
    use_total_model : bool
        If True, use total_coefficients/total_intercept instead of spread model.

    Returns
    -------
    float
        Projected margin contribution (or total contribution).
    """
    coef_key = "total_coefficients" if use_total_model else "coefficients"
    int_key = "total_intercept" if use_total_model else "intercept"

    coefs = np.array(weights.get(coef_key, weights.get("coefficients", [])), dtype=np.float64)
    intercept = float(weights.get(int_key, weights.get("intercept", 0.0)))

    if len(coefs) == 0:
        return float(intercept)

    # Handle feature count mismatch gracefully
    n_feat = len(features)
    n_coef = len(coefs)
    if n_feat != n_coef:
        if n_feat < n_coef:
            padded = np.zeros(n_coef, dtype=np.float64)
            padded[:n_feat] = features
            features = padded
        else:
            features = features[:n_coef]

    return float(np.dot(coefs, features) + intercept)


def project_game_scores(home_stats, away_stats, starter_home, starter_away,
                        bullpen_home, bullpen_away, park_factor, weather,
                        weights, kalman_adj_home=None, kalman_adj_away=None):
    """
    Project full game scores for both teams.

    Returns
    -------
    dict
        {"projHome", "projAway", "projSpread", "projTotal", "marginVar"}
    """
    # Build feature vectors from each perspective
    home_features = build_feature_vector(
        home_stats, away_stats, starter_home, starter_away,
        bullpen_home, bullpen_away, park_factor, weather, is_home=True,
    )
    away_features = build_feature_vector(
        away_stats, home_stats, starter_away, starter_home,
        bullpen_away, bullpen_home, park_factor, weather, is_home=False,
    )

    # Margin model: projects run differential
    home_margin = project_score(home_features, weights, use_total_model=False)
    away_margin = project_score(away_features, weights, use_total_model=False)

    # Total model: projects combined run total
    # (used for reference; final total uses score sum for stability)
    # total_proj = project_score(home_features, weights, use_total_model=True)

    # Center around league average: ~4.5 runs per team
    league_avg = 4.5
    proj_home = league_avg + home_margin / 2
    proj_away = league_avg + away_margin / 2

    # Apply Kalman adjustments
    margin_var = weights.get("residual_var", BAYES_HYPER["residualVar"])

    if kalman_adj_home:
        proj_home += kalman_adj_home.get("mean", 0.0)
        margin_var += kalman_adj_home.get("var", 0.0)

    if kalman_adj_away:
        proj_away += kalman_adj_away.get("mean", 0.0)
        margin_var += kalman_adj_away.get("var", 0.0)

    proj_home = round(proj_home * 10) / 10
    proj_away = round(proj_away * 10) / 10
    proj_total = round((proj_home + proj_away) * 10) / 10   # Score sum (more stable)
    proj_spread = round((proj_away - proj_home) * 10) / 10  # Negative = home favored

    return {
        "projHome": proj_home,
        "projAway": proj_away,
        "projSpread": proj_spread,
        "projTotal": proj_total,
        "marginVar": round(margin_var * 10) / 10,
    }


# ===========================================================================
# Game Analysis — the core analysis function
# ===========================================================================

def analyze_game(game_data, team_stats, pitcher_adjustments, weights,
                 kalman_states=None, residual_var=None, thresholds=None):
    """
    Full analysis of a single MLB game.

    Parameters
    ----------
    game_data : dict
        Must contain: "home", "away", "line" (spread), "total" (over/under).
        May contain: "_parkFactor" (float), "_weather" (dict),
                     "homeScore", "awayScore" (for grading).
    team_stats : dict
        {team_name: stats_dict} for all teams.
    pitcher_adjustments : dict
        Output from pitcher_layer.compute_pitcher_adjustments().
        {"home": {"starter": {...}, "bullpen": {...}},
         "away": {"starter": {...}, "bullpen": {...}}}
    weights : dict
        Loaded ridge coefficients (from load_weights).
    kalman_states : dict or None
        Kalman state {"teams": {team_name: {"adj_mean", "adj_var"}}}.
    residual_var : float or None
        Dynamic residual variance (from self_tune).
    thresholds : dict or None
        sDiff thresholds for confidence tiers.

    Returns
    -------
    dict or None
        Full analysis result. Returns None if teams can't be resolved.
    """
    home_name = game_data.get("home")
    away_name = game_data.get("away")

    if not home_name or not away_name:
        return None

    # Resolve team names
    home_key = _resolve_team(team_stats, home_name)
    away_key = _resolve_team(team_stats, away_name)

    if not home_key or not away_key:
        return None

    home_st = team_stats.get(home_key)
    away_st = team_stats.get(away_key)

    if not home_st or not away_st:
        return None

    # Extract pitcher data
    home_pit = pitcher_adjustments.get("home", {})
    away_pit = pitcher_adjustments.get("away", {})

    starter_home = home_pit.get("starter", _default_starter())
    starter_away = away_pit.get("starter", _default_starter())
    bullpen_home = home_pit.get("bullpen", _default_bullpen())
    bullpen_away = away_pit.get("bullpen", _default_bullpen())

    # Park factor and weather
    park_factor = game_data.get("_parkFactor", 1.0)
    weather = game_data.get("_weather", {"temperature_adj": 0.0, "wind_adj": 0.0})

    # Kalman adjustments
    kalman_adj_home = None
    kalman_adj_away = None
    if kalman_states and kalman_states.get("teams"):
        kt = kalman_states["teams"]
        ht = kt.get(home_key)
        if ht:
            kalman_adj_home = {"mean": ht.get("adj_mean", 0.0), "var": ht.get("adj_var", 0.0)}
        at = kt.get(away_key)
        if at:
            kalman_adj_away = {"mean": at.get("adj_mean", 0.0), "var": at.get("adj_var", 0.0)}

    # Project scores
    proj = project_game_scores(
        home_st, away_st,
        starter_home, starter_away,
        bullpen_home, bullpen_away,
        park_factor, weather,
        weights,
        kalman_adj_home=kalman_adj_home,
        kalman_adj_away=kalman_adj_away,
    )

    proj_home = proj["projHome"]
    proj_away = proj["projAway"]
    proj_spread = proj["projSpread"]
    proj_total = proj["projTotal"]
    margin_var = proj["marginVar"]

    # Apply park factor to total (park factor affects combined run environment)
    proj_total = round(proj_total * park_factor * 10) / 10

    # Dynamic residual_var override
    r_var = residual_var or weights.get("residual_var", BAYES_HYPER["residualVar"])
    effective_var = max(margin_var, r_var)

    # Market data
    market_spread = game_data.get("line", 0.0)   # Negative = home favored
    market_total = game_data.get("total", 0.0)

    # Edge computation
    model_margin = proj_home - proj_away
    margin_vs_line = model_margin - (-market_spread)  # = model_margin + market_spread
    s_diff = abs(margin_vs_line)
    t_diff = round((proj_total - market_total) * 10) / 10

    # ---------------------------------------------------------------------------
    # P(cover) via Normal CDF — MLB margins are more normally distributed than NFL
    # Floor at 1.5 run std (2.25 variance) to prevent overconfidence
    # ---------------------------------------------------------------------------
    margin_std = math.sqrt(max(effective_var, 2.25))
    p_home_cover = float(norm.cdf(margin_vs_line / margin_std))
    p_away_cover = 1.0 - p_home_cover

    # P(over/under) — totals have more variance (two teams combined)
    total_std = math.sqrt(max(effective_var * 1.3, 4.0))
    p_over = float(norm.cdf(t_diff / total_std)) if market_total > 0 else 0.5
    p_under = 1.0 - p_over

    # ---------------------------------------------------------------------------
    # Pick logic
    # ---------------------------------------------------------------------------
    thresh = thresholds or SDIFF_THRESHOLDS
    abs_line = abs(market_spread)
    home_fav = market_spread < 0

    confidence_tier = _classify_confidence(s_diff, thresh)

    # Spread pick
    s_pick = "PASS"
    s_conf = "low"
    p_cover = None

    best_spread_p = max(p_home_cover, p_away_cover)
    spread_side = "home" if p_home_cover >= p_away_cover else "away"
    prob_threshold = DEFAULT_W.get("probHigh", 0.57)
    abs_line_cap = 5.0  # Don't pick into lines > 5 runs (MLB cap)

    if best_spread_p >= prob_threshold and abs_line <= abs_line_cap:
        s_pick = _format_spread_pick(home_name, away_name, spread_side, abs_line, home_fav)
        s_conf = "elite" if best_spread_p >= DEFAULT_W.get("probElite", 0.63) else "high"
        p_cover = best_spread_p
        confidence_tier = _classify_confidence(s_diff, thresh)

    # Over/Under pick
    o_pick = "PASS"
    o_conf = "low"
    p_ou = None

    best_total_p = max(p_over, p_under)
    ou_prob_high = DEFAULT_W.get("probOUHigh", 0.58)
    ou_prob_elite = DEFAULT_W.get("probOUElite", 0.65)

    if best_total_p >= ou_prob_high and market_total > 0:
        o_pick = "OVER" if p_over >= p_under else "UNDER"
        o_conf = "elite" if best_total_p >= ou_prob_elite else "high"
        p_ou = best_total_p

    # ---------------------------------------------------------------------------
    # Pitcher note
    # ---------------------------------------------------------------------------
    pitcher_note = _build_pitcher_note(pitcher_adjustments)

    # ---------------------------------------------------------------------------
    # Build margin features for self_tune compatibility
    # ---------------------------------------------------------------------------
    margin_features = _extract_margin_features(
        home_st, away_st,
        starter_home, starter_away,
        bullpen_home, bullpen_away,
        park_factor, weather,
    )

    # Raw feature vector for ridge training (backfill collects these)
    _home_fv = build_feature_vector(
        home_st, away_st,
        starter_home, starter_away,
        bullpen_home, bullpen_away,
        park_factor, weather, is_home=True,
    )
    _feature_vec = _home_fv.tolist() if hasattr(_home_fv, "tolist") else list(_home_fv)

    # ---------------------------------------------------------------------------
    # Result
    # ---------------------------------------------------------------------------
    result = {
        # Game identifiers
        "home": home_key,
        "away": away_key,
        "line": market_spread,
        "total": market_total,

        # Projections
        "hS": proj_home,
        "aS": proj_away,
        "pT": proj_total,
        "projSpread": proj_spread,

        # Edge metrics
        "margin": round(margin_vs_line * 10) / 10,
        "sDiff": round(s_diff * 10) / 10,
        "tDiff": t_diff,

        # Picks
        "sPick": s_pick,
        "sConf": s_conf,
        "oPick": o_pick,
        "oConf": o_conf,
        "confidenceTier": confidence_tier,

        # Probabilities
        "pHomeCover": round(p_home_cover * 1000) / 1000,
        "pAwayCover": round(p_away_cover * 1000) / 1000,
        "pOver": round(p_over * 1000) / 1000,
        "pUnder": round(p_under * 1000) / 1000,
        "pCover": round(p_cover * 1000) / 1000 if p_cover is not None else None,
        "pOU": round(p_ou * 1000) / 1000 if p_ou is not None else None,

        # Variance
        "marginVar": round(margin_var * 10) / 10,
        "marginStd": round(margin_std * 10) / 10,
        "residualVar": round(r_var * 10) / 10,

        # Metadata
        "pitcherNote": pitcher_note,

        # Internal features (for self_tune and diagnostics)
        "_marginFeatures": margin_features,
        # Raw feature vector for ridge regression training (backfill)
        "_features": _feature_vec,
    }

    # Propagate raw game_data fields (date, gameId, homeScore, awayScore, etc.)
    for k in ("date", "gameId", "commenceTimeIso", "homeScore", "awayScore"):
        if k in game_data:
            result[k] = game_data[k]

    return result


# ===========================================================================
# Batch Analysis
# ===========================================================================

def analyze_slate(games, team_stats, pitcher_data_map, weights,
                  kalman_states=None, residual_var=None, thresholds=None):
    """
    Analyze a full slate of MLB games.

    Parameters
    ----------
    games : list of dict
        List of game_data dicts.
    team_stats : dict
        All team stats {team_name: stats_dict}.
    pitcher_data_map : dict
        {game_key: pitcher_adjustments_dict}
        where game_key = "away@home"
    weights : dict
        Ridge coefficients.
    kalman_states : dict or None
        Kalman state.
    residual_var : float or None
        Dynamic residual variance.
    thresholds : dict or None
        sDiff thresholds.

    Returns
    -------
    list of dict
        Analysis results for each game (None entries filtered out).
    """
    results = []
    for g in games:
        game_key = f"{g.get('away', '')}@{g.get('home', '')}"
        pitcher_adj = (pitcher_data_map or {}).get(game_key, _default_pitcher_adjustments())

        result = analyze_game(
            g, team_stats, pitcher_adj, weights,
            kalman_states=kalman_states,
            residual_var=residual_var,
            thresholds=thresholds,
        )
        if result is not None:
            results.append(result)

    return results


# ===========================================================================
# Ridge Regression Training
# ===========================================================================

def train_ridge(X, y_margin, y_total=None, feature_columns=None, alphas=None):
    """
    Train ridge regression on historical matchup data.

    Parameters
    ----------
    X : np.ndarray
        Feature matrix of shape (n_games, n_features).
    y_margin : np.ndarray
        Target: run differential (home - away), shape (n_games,).
    y_total : np.ndarray or None
        Target: total runs scored (home + away). If provided, also trains total model.
    feature_columns : list of str or None
        Column names. Defaults to FEATURE_COLUMNS.
    alphas : array-like or None
        Regularization alphas for CV. Defaults to logspace(-2, 2, 30).

    Returns
    -------
    dict
        Trained weights dict.
    """
    from sklearn.preprocessing import StandardScaler

    X = np.asarray(X, dtype=np.float64)
    y_margin = np.asarray(y_margin, dtype=np.float64)
    n_samples, n_features = X.shape

    if n_samples < 20:
        print(f"  [model_engine] WARNING: Only {n_samples} training samples."
              " Ridge results may be unreliable.")

    RidgeCV = _get_ridge_cv()
    feat_cols = feature_columns or FEATURE_COLUMNS

    cv_alphas = alphas if alphas is not None else np.logspace(-2, 2, 30)
    n_folds = min(5, n_samples)

    # Standardize features
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # Margin model
    margin_model = RidgeCV(alphas=cv_alphas, cv=n_folds,
                           scoring="neg_mean_absolute_error")
    margin_model.fit(X_scaled, y_margin)
    coefs_margin = margin_model.coef_ / scaler.scale_
    intercept_margin = (margin_model.intercept_
                        - np.dot(margin_model.coef_, scaler.mean_ / scaler.scale_))

    pred_margin = X @ coefs_margin + intercept_margin
    residuals_margin = y_margin - pred_margin
    residual_var = float(np.var(residuals_margin))
    ss_total = float(np.var(y_margin)) * n_samples
    ss_res = float(np.sum(residuals_margin ** 2))
    r_squared = 1.0 - ss_res / ss_total if ss_total > 0 else 0.0
    best_alpha = max(float(margin_model.alpha_), 0.1)

    print(f"  [model_engine] Ridge (margin) trained: alpha={best_alpha:.4f},"
          f" R²={r_squared:.4f}, residualVar={residual_var:.1f}, n={n_samples}")

    # Total model
    total_coefs = coefs_margin.tolist()
    total_intercept = 9.0

    if y_total is not None:
        y_total = np.asarray(y_total, dtype=np.float64)
        total_model = RidgeCV(alphas=cv_alphas, cv=n_folds,
                              scoring="neg_mean_absolute_error")
        total_model.fit(X_scaled, y_total)
        total_coefs_arr = total_model.coef_ / scaler.scale_
        total_intercept = float(total_model.intercept_
                                - np.dot(total_model.coef_, scaler.mean_ / scaler.scale_))
        total_coefs = total_coefs_arr.tolist()
        print(f"  [model_engine] Ridge (total) trained: alpha={float(total_model.alpha_):.4f}")

    # Log top margin feature importance
    feature_importance = sorted(
        zip(feat_cols[:n_features], coefs_margin.tolist()),
        key=lambda x: abs(x[1]),
        reverse=True,
    )
    for fname, coef in feature_importance[:5]:
        print(f"    {fname}: {coef:+.4f}")

    return {
        "coefficients": coefs_margin.tolist(),
        "intercept": float(intercept_margin),
        "total_coefficients": total_coefs,
        "total_intercept": total_intercept,
        "alpha": best_alpha,
        "feature_columns": feat_cols[:n_features],
        "n_train": n_samples,
        "residual_var": residual_var,
        "r_squared": r_squared,
        "source": "ridge_cv",
    }


# ===========================================================================
# Convenience: Load everything needed to run
# ===========================================================================

def load_engine(data_dir=None):
    """
    Load weights and return a ready-to-use engine configuration.

    Parameters
    ----------
    data_dir : str or None
        Path to pyMLB/data/. If None, resolves relative to this file.

    Returns
    -------
    dict
        {"weights": dict, "thresholds": dict, "residual_var": float}
    """
    if data_dir is None:
        data_dir = os.path.join(_SCRIPT_DIR, "..", "data")

    weights_path = os.path.join(data_dir, "weights.json")
    thresholds_path = os.path.join(data_dir, "thresholds.json")

    weights = load_weights(weights_path)

    thresholds = SDIFF_THRESHOLDS
    if os.path.isfile(thresholds_path):
        with open(thresholds_path, "r", encoding="utf-8") as f:
            thresholds = json.load(f)

    return {
        "weights": weights,
        "thresholds": thresholds,
        "residual_var": weights.get("residual_var", BAYES_HYPER["residualVar"]),
    }


# ===========================================================================
# Helper: Margin Features for self_tune compatibility
# ===========================================================================

def _extract_margin_features(home_stats, away_stats, starter_home, starter_away,
                              bullpen_home, bullpen_away, park_factor, weather):
    """
    Extract margin features in a format compatible with core/self_tune.py.

    Maps MLB feature vector to _FEATURE_KEYS for Signal 1.
    """
    fv = build_feature_vector(
        home_stats, away_stats,
        starter_home, starter_away,
        bullpen_home, bullpen_away,
        park_factor, weather, is_home=True,
    )
    features = {}
    for i, key in enumerate(_FEATURE_KEYS):
        features[key] = float(fv[i]) if i < len(fv) else 0.0
    features["hca"] = 1.0   # Always 1 for home perspective
    features["_baseline"] = 0.0
    return features


# ===========================================================================
# Helper: Team Name Resolution
# ===========================================================================

def _resolve_team(team_stats, name):
    """
    Resolve a team name to its canonical key in team_stats.

    Tries exact match → lowered match → alias lookup → abbreviation → substring.
    """
    if not team_stats or not name:
        return None

    # Exact match
    if name in team_stats:
        return name

    # Lowered match
    name_lower = name.lower().strip()
    for key in team_stats:
        if key.lower().strip() == name_lower:
            return key

    # Alias lookup
    aliases = ENGINE_CONFIG.get("TEAM_NAME_ALIASES", {})
    canonical = aliases.get(name_lower)
    if canonical:
        if canonical in team_stats:
            return canonical
        # Try abbreviation from MLB_TEAMS
        try:
            from defaults import MLB_TEAMS
            abbrevs = MLB_TEAMS.get(canonical, [])
            for abbr in abbrevs:
                if abbr in team_stats:
                    return abbr
                if abbr.upper() in team_stats:
                    return abbr.upper()
        except ImportError:
            pass

    # Try abbreviation directly
    if name.upper() in team_stats:
        return name.upper()

    # Substring match (fuzzy)
    for key in team_stats:
        key_lower = key.lower()
        if name_lower in key_lower or key_lower in name_lower:
            return key

    return None


# ===========================================================================
# Helper: Confidence Classification
# ===========================================================================

def _classify_confidence(s_diff, thresholds=None):
    """Classify sDiff into a confidence tier."""
    thresh = thresholds or SDIFF_THRESHOLDS
    if s_diff >= thresh.get("elite", 2.5):
        return "elite"
    elif s_diff >= thresh.get("high", 1.5):
        return "high"
    elif s_diff >= thresh.get("medium", 0.75):
        return "medium"
    else:
        return "low"


# ===========================================================================
# Helper: Format Spread Pick
# ===========================================================================

def _format_spread_pick(home_name, away_name, spread_side, abs_line, home_fav):
    """
    Format a spread pick string like "New York Yankees -1.5".
    """
    if spread_side == "home":
        team = home_name
        sign = "-" if home_fav else "+"
    else:
        team = away_name
        sign = "+" if home_fav else "-"

    return f"{team} {sign}{abs_line}"


# ===========================================================================
# Helper: Pitcher Note Builder
# ===========================================================================

def _build_pitcher_note(pitcher_adjustments):
    """Build human-readable pitcher note from pitcher adjustments dict."""
    if not pitcher_adjustments:
        return None

    parts = []
    for side in ("home", "away"):
        pit = pitcher_adjustments.get(side, {})
        starter = pit.get("starter", {})
        if starter:
            name = starter.get("name", "TBD")
            hand = starter.get("hand", "?")
            fip = starter.get("FIP", 4.50)
            label = "Home" if side == "home" else "Away"
            parts.append(f"{label}: {name} ({hand}, FIP={fip:.1f})")

    return " | ".join(parts) if parts else None


# ===========================================================================
# Private fallback constructors
# ===========================================================================

def _default_starter():
    """Return league-average default starter stats."""
    return {
        "name": "TBD",
        "FIP": 4.50,
        "xFIP": 4.50,
        "SIERA": 4.50,
        "K_BB_pct": 0.08,
        "IP_per_GS": 5.5,
        "hand": "R",
    }


def _default_bullpen():
    """Return league-average default bullpen stats."""
    return {
        "FIP": 4.50,
        "K_BB_pct": 0.08,
        "workload": 0.0,
    }


def _default_pitcher_adjustments():
    """Return neutral pitcher adjustments for both sides."""
    return {
        "home": {"starter": _default_starter(), "bullpen": _default_bullpen()},
        "away": {"starter": _default_starter(), "bullpen": _default_bullpen()},
    }
