# pyNFL/scripts/model_engine.py
# STANDALONE NFL score projection engine using ridge regression on EPA features.
#
# This is NOT a thin wrapper around core/model_engine.py.
# The NBA engine uses weighted stat deltas (dTS, dTO, dORR, dNET).
# The NFL engine uses ridge regression on EPA features — a fundamentally
# different projection approach.
#
# Key responsibilities:
#   1. Build feature vectors from team EPA stats + injury deltas
#   2. Project scores using ridge-trained coefficients
#   3. Detect edges (model spread vs market spread)
#   4. Compute P(cover) using Normal CDF with residual variance
#   5. Train ridge regression from historical data (backfill)
#   6. Persist and load trained weights
#
# Dependencies:
#   - numpy: feature vector construction, linear algebra
#   - scipy.stats.norm: P(cover) computation via Normal CDF
#   - sklearn.linear_model.RidgeCV: ridge regression with cross-validation

import json
import math
import os
import sys

import numpy as np
from scipy.stats import norm

# Lazy import sklearn — only needed for training, not inference
_RidgeCV = None


def _get_ridge_cv():
    """Lazy import of sklearn.linear_model.RidgeCV."""
    global _RidgeCV
    if _RidgeCV is None:
        from sklearn.linear_model import RidgeCV as _RC
        _RidgeCV = _RC
    return _RidgeCV


# ---------------------------------------------------------------------------
# Defaults import (resolve relative to this file)
# ---------------------------------------------------------------------------

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

_REPO_ROOT = os.path.join(_SCRIPT_DIR, "..", "..")
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, os.path.abspath(_REPO_ROOT))

from defaults import (
    DEFAULT_W,
    DEFAULT_W_VAR,
    DEFAULT_STATS,
    BAYES_HYPER,
    FEATURE_COLUMNS,
    SDIFF_THRESHOLDS,
    MIN_GP,
    BURN_IN_WEEKS,
    ENGINE_CONFIG,
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
        Keys: "coefficients" (list of floats), "intercept" (float),
        "alpha" (float), "feature_columns" (list of str),
        "n_train" (int), "residual_var" (float), plus any extra metadata.
        Returns DEFAULT_W-based fallback if file doesn't exist.
    """
    if not os.path.isfile(path):
        print(f"  [model_engine] No weights file at {path} — using defaults")
        return _default_weights_dict()

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Validate required fields
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
    # Map DEFAULT_W keys to feature column coefficients
    key_map = {
        "passOffEPA_diff": "wPassOff",
        "rushOffEPA_diff": "wRushOff",
        "passDefEPA_diff": "wPassDef",
        "rushDefEPA_diff": "wRushDef",
        "pace_diff": "wPace",
        "rz_diff": "wRZ",
        "home_flag": "hfa",
        "cpoe_diff": "wCPOE",
        "pressure_diff": "wPressure",
        "success_rate_diff": "wSuccessRate",
        "qb_epa_diff": "wQBEPA",
        "turnover_diff": "wTurnover",
        "injury_delta_a": 1.0,       # Injury deltas pass through 1:1
        "injury_delta_b": -1.0,      # Opponent injury hurts them
    }

    coefficients = []
    for col in FEATURE_COLUMNS:
        key = key_map.get(col)
        if isinstance(key, str):
            coefficients.append(DEFAULT_W.get(key, 0.0))
        elif isinstance(key, (int, float)):
            coefficients.append(float(key))
        else:
            coefficients.append(0.0)

    return {
        "coefficients": coefficients,
        "intercept": DEFAULT_W.get("constant", 0.0),
        "alpha": 1.0,
        "feature_columns": FEATURE_COLUMNS,
        "n_train": 0,
        "residual_var": BAYES_HYPER["residualVar"],
        "source": "defaults",
    }


# ===========================================================================
# Feature Engineering
# ===========================================================================

def build_feature_vector(team_a_stats, team_b_stats, is_home,
                         injury_delta_a=0.0, injury_delta_b=0.0):
    """
    Build the feature vector for one matchup (from Team A's perspective).

    Team A is the team we're projecting the score for.
    Team B is the opponent.

    Parameters
    ----------
    team_a_stats : dict
        Team A's aggregated EPA stats (matching DEFAULT_STATS keys).
    team_b_stats : dict
        Team B's aggregated EPA stats.
    is_home : bool
        True if team A is the home team.
    injury_delta_a : float
        Injury adjustment for team A (positive = team A is healthier than baseline).
    injury_delta_b : float
        Injury adjustment for team B.

    Returns
    -------
    np.ndarray
        Feature vector of shape (n_features,), ordered per FEATURE_COLUMNS.
    """
    a = team_a_stats
    b = team_b_stats

    # Efficiency differentials: team A's strength vs team B's weakness
    pass_off_diff = a.get("passOffEPA", 0.0) - b.get("passDefEPA", 0.0)
    rush_off_diff = a.get("rushOffEPA", 0.0) - b.get("rushDefEPA", 0.0)
    pass_def_diff = a.get("passDefEPA", 0.0) - b.get("passOffEPA", 0.0)
    rush_def_diff = a.get("rushDefEPA", 0.0) - b.get("rushOffEPA", 0.0)

    # Pace: how many more/fewer plays than league average
    league_avg_pace = 63.0
    pace_diff = ((a.get("pace", league_avg_pace) + b.get("pace", league_avg_pace)) / 2
                 - league_avg_pace)

    # Red zone differential
    rz_diff = a.get("rzTDOff", 0.0) - b.get("rzTDDef", 0.0)

    # Home flag
    home_flag = 1.0 if is_home else 0.0

    # Supplementary features
    cpoe_diff = a.get("cpoe", 0.0) - b.get("cpoe", 0.0)
    pressure_diff = a.get("pressureRateOff", 0.0) - b.get("pressureRateDef", 0.0)
    success_rate_diff = a.get("successRateOff", 0.0) - b.get("successRateDef", 0.0)
    qb_epa_diff = a.get("qbEPA", 0.0) - b.get("qbEPA", 0.0)
    turnover_diff = a.get("toMargin", 0.0) - b.get("toMargin", 0.0)

    features = np.array([
        pass_off_diff,
        rush_off_diff,
        pass_def_diff,
        rush_def_diff,
        pace_diff,
        rz_diff,
        home_flag,
        cpoe_diff,
        pressure_diff,
        success_rate_diff,
        qb_epa_diff,
        turnover_diff,
        injury_delta_a,
        injury_delta_b,
    ], dtype=np.float64)

    return features


def build_feature_matrix(matchups):
    """
    Build feature matrix for multiple matchups (for training).

    Parameters
    ----------
    matchups : list of dict
        Each dict must contain:
            "team_a_stats", "team_b_stats", "is_home",
            "injury_delta_a" (optional), "injury_delta_b" (optional)

    Returns
    -------
    np.ndarray
        Feature matrix of shape (n_matchups, n_features).
    """
    rows = []
    for m in matchups:
        vec = build_feature_vector(
            m["team_a_stats"],
            m["team_b_stats"],
            m["is_home"],
            m.get("injury_delta_a", 0.0),
            m.get("injury_delta_b", 0.0),
        )
        rows.append(vec)
    return np.array(rows, dtype=np.float64)


# ===========================================================================
# Score Projection
# ===========================================================================

def project_score(features, weights):
    """
    Project the score contribution for one team using ridge coefficients.

    Parameters
    ----------
    features : np.ndarray
        Feature vector from build_feature_vector().
    weights : dict
        Loaded weights dict with "coefficients" and "intercept".

    Returns
    -------
    float
        Projected score contribution (not the full game score — this is the
        margin contribution from team A's perspective).
    """
    if "coefficients" not in weights:
        # No trained ridge model yet — use simple EPA-differential estimate.
        # features[0..3] are EPA diffs (passOff, rushOff, passDef, rushDef),
        # features[6] is HFA flag, features[12-13] are injury deltas.
        # Use naive weighting: ~7 * sum(EPA diffs) + HFA + injuries
        epa_sum = sum(float(features[i]) for i in range(min(4, len(features))))
        hfa = float(features[6]) if len(features) > 6 else 0
        inj = sum(float(features[i]) for i in range(12, min(14, len(features))))
        return epa_sum * 7.0 + hfa * 2.5 + inj

    coefs = np.array(weights["coefficients"], dtype=np.float64)

    # Handle feature count mismatch gracefully
    n_feat = len(features)
    n_coef = len(coefs)
    if n_feat != n_coef:
        # Pad or truncate to match
        if n_feat < n_coef:
            padded = np.zeros(n_coef, dtype=np.float64)
            padded[:n_feat] = features
            features = padded
        else:
            features = features[:n_coef]

    intercept = weights.get("intercept", 0.0)
    return float(np.dot(coefs, features) + intercept)


def project_game_scores(home_stats, away_stats, weights,
                        injury_delta_home=0.0, injury_delta_away=0.0,
                        kalman_adj_home=None, kalman_adj_away=None):
    """
    Project full game scores for both teams.

    Parameters
    ----------
    home_stats, away_stats : dict
        Team aggregated stats.
    weights : dict
        Ridge coefficients.
    injury_delta_home, injury_delta_away : float
        Injury adjustments.
    kalman_adj_home, kalman_adj_away : dict or None
        Kalman adjustments {"mean": float, "var": float}.

    Returns
    -------
    dict
        {"projHome": float, "projAway": float, "projSpread": float,
         "projTotal": float, "marginVar": float}
    """
    # Home team features: home team is "team A"
    home_features = build_feature_vector(
        home_stats, away_stats, is_home=True,
        injury_delta_a=injury_delta_home,
        injury_delta_b=injury_delta_away,
    )

    # Away team features: away team is "team A", home team is opponent
    away_features = build_feature_vector(
        away_stats, home_stats, is_home=False,
        injury_delta_a=injury_delta_away,
        injury_delta_b=injury_delta_home,
    )

    # Raw margin contributions
    home_margin = project_score(home_features, weights)
    away_margin = project_score(away_features, weights)

    # Base scores: start from league average ~22 pts per team
    # The ridge model predicts margin contributions, so we center around
    # a league average and adjust.
    league_avg_score = 22.0

    proj_home = league_avg_score + home_margin / 2
    proj_away = league_avg_score + away_margin / 2

    # Apply Kalman adjustments
    margin_var = weights.get("residual_var", BAYES_HYPER["residualVar"])

    if kalman_adj_home:
        proj_home += kalman_adj_home.get("mean", 0.0)
        margin_var += kalman_adj_home.get("var", 0.0)

    if kalman_adj_away:
        proj_away += kalman_adj_away.get("mean", 0.0)
        margin_var += kalman_adj_away.get("var", 0.0)

    # Compute weight uncertainty contribution to variance
    coefs = np.array(weights.get("coefficients", []), dtype=np.float64)
    w_var = weights.get("_w_var", None)
    if w_var:
        for i, col in enumerate(FEATURE_COLUMNS):
            if i < len(home_features) and col in w_var:
                feat_sq = float(home_features[i]) ** 2
                margin_var += feat_sq * w_var.get(col, 0.0)

    proj_home = round(proj_home * 10) / 10
    proj_away = round(proj_away * 10) / 10

    return {
        "projHome": proj_home,
        "projAway": proj_away,
        "projSpread": round((proj_away - proj_home) * 10) / 10,  # Negative = home favored
        "projTotal": round((proj_home + proj_away) * 10) / 10,
        "marginVar": round(margin_var * 10) / 10,
    }


# ===========================================================================
# Game Analysis — the core analysis function
# ===========================================================================

def analyze_game(game_data, team_stats, weights, kalman_states=None,
                 injury_deltas=None, residual_var=None, thresholds=None):
    """
    Full analysis of a single NFL game. This is the primary entry point
    for the pipeline.

    Parameters
    ----------
    game_data : dict
        Must contain: "home", "away", "line" (spread, positive = away favored),
        "total" (over/under line).
        May contain: "homeScore", "awayScore" (for grading).
    team_stats : dict
        {team_name: stats_dict} for all teams.
    weights : dict
        Loaded ridge coefficients (from load_weights).
    kalman_states : dict or None
        Kalman state {"teams": {team_name: {"adj_mean", "adj_var"}}}.
    injury_deltas : dict or None
        {"home": float, "away": float} injury adjustments.
        Also may contain "homeInjuries" and "awayInjuries" lists for notes.
    residual_var : float or None
        Dynamic residual variance (from self_tune). Falls back to weights or BAYES_HYPER.
    thresholds : dict or None
        sDiff thresholds for confidence tiers. Falls back to SDIFF_THRESHOLDS.

    Returns
    -------
    dict or None
        Full analysis result with projections, edges, picks, and metadata.
        Returns None if teams can't be resolved or insufficient data.
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



    # Injury deltas
    inj = injury_deltas or {}
    inj_home = inj.get("home", 0.0)
    inj_away = inj.get("away", 0.0)

    # Kalman adjustments
    kalman_adj_home = None
    kalman_adj_away = None
    if kalman_states and kalman_states.get("teams"):
        kt = kalman_states["teams"]
        ht = kt.get(home_key)
        if ht:
            kalman_adj_home = {"mean": ht["adj_mean"], "var": ht["adj_var"]}
        at = kt.get(away_key)
        if at:
            kalman_adj_away = {"mean": at["adj_mean"], "var": at["adj_var"]}

    # Project scores
    proj = project_game_scores(
        home_st, away_st, weights,
        injury_delta_home=inj_home,
        injury_delta_away=inj_away,
        kalman_adj_home=kalman_adj_home,
        kalman_adj_away=kalman_adj_away,
    )

    proj_home = proj["projHome"]
    proj_away = proj["projAway"]
    proj_spread = proj["projSpread"]
    proj_total = proj["projTotal"]
    margin_var = proj["marginVar"]

    # Use dynamic residual_var if provided
    r_var = residual_var or weights.get("residual_var", BAYES_HYPER["residualVar"])

    # Market data
    market_spread = game_data.get("line", 0.0)   # Positive = away favored
    market_total = game_data.get("total", 0.0)

    # Edge computation
    # model_margin = proj_home - proj_away (positive = home favored)
    # line = market_spread (positive = away favored, i.e. home is underdog)
    # margin_vs_line = model says home is X better than market says
    model_margin = proj_home - proj_away
    margin_vs_line = model_margin - (-market_spread)  # Convert line convention
    s_diff = abs(margin_vs_line)

    # Total edge
    t_diff = round((proj_total - market_total) * 10) / 10

    # ---------------------------------------------------------------------------
    # P(cover) via Normal CDF
    # ---------------------------------------------------------------------------
    margin_std = math.sqrt(max(margin_var + r_var, 1.0))

    # P(home covers): model says home beats the spread
    p_home_cover = norm.cdf(margin_vs_line / margin_std)
    p_away_cover = 1.0 - p_home_cover

    # P(over/under)
    total_std = math.sqrt(max((margin_var + r_var) * 1.1, 1.0))
    p_over = norm.cdf(t_diff / total_std) if market_total > 0 else 0.5
    p_under = 1.0 - p_over

    # ---------------------------------------------------------------------------
    # Pick logic
    # ---------------------------------------------------------------------------
    thresh = thresholds or SDIFF_THRESHOLDS
    abs_line = abs(market_spread)
    home_fav = market_spread > 0  # Positive line = home favored (convention: +X home fav, -X away fav)

    # Spread pick
    s_pick = "PASS"
    s_conf = "low"
    p_cover = None
    confidence_tier = _classify_confidence(s_diff, thresh)

    # Bayesian pick: use P(cover)
    best_spread_p = max(p_home_cover, p_away_cover)
    spread_side = "home" if p_home_cover >= p_away_cover else "away"
    prob_threshold = DEFAULT_W.get("probHigh", 0.57)

    # Caps: don't pick into huge spreads or tiny edges
    s_diff_cap = 9.0        # sDiff > 9 hits at 58% (barely above breakeven)
    abs_line_cap = 14.0     # Don't pick into lines > 14 pts

    if (best_spread_p >= prob_threshold
            and s_diff <= s_diff_cap
            and abs_line <= abs_line_cap):
        s_pick = _format_spread_pick(
            home_name, away_name, spread_side, abs_line, home_fav
        )
        s_conf = "elite" if best_spread_p >= DEFAULT_W.get("probElite", 0.63) else "high"
        p_cover = best_spread_p
        confidence_tier = _classify_confidence(s_diff, thresh)

    # Over/Under pick
    o_pick = "PASS"
    o_conf = "low"
    p_ou = None
    best_total_p = max(p_over, p_under)
    ou_prob_threshold = DEFAULT_W.get("probOUElite", 0.64)

    if best_total_p >= ou_prob_threshold and market_total > 0:
        o_pick = "OVER" if p_over >= p_under else "UNDER"
        o_conf = "elite"
        p_ou = best_total_p

    # ---------------------------------------------------------------------------
    # Injury note
    # ---------------------------------------------------------------------------
    injury_note = _build_injury_note(injury_deltas)

    # ---------------------------------------------------------------------------
    # Build margin features for self_tune compatibility
    # ---------------------------------------------------------------------------
    margin_features = _extract_margin_features(
        home_st, away_st, is_home=True, inj_home=inj_home, inj_away=inj_away
    )

    # Build raw feature vector for ridge training (backfill collects these)
    _home_fv = build_feature_vector(
        home_st, away_st, is_home=True,
        injury_delta_a=inj_home, injury_delta_b=inj_away,
    )
    _feature_vec = _home_fv.tolist() if hasattr(_home_fv, 'tolist') else list(_home_fv)

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
        "injuryNote": injury_note,
        "injuryDeltaHome": round(inj_home * 100) / 100,
        "injuryDeltaAway": round(inj_away * 100) / 100,

        # Internal features (for self_tune and diagnostics)
        "_marginFeatures": margin_features,
        # Raw feature vector for ridge regression training (backfill)
        "_features": _feature_vec,
    }

    # Propagate raw game_data fields (date, week, etc.)
    for k in ("date", "week", "gameId", "homeScore", "awayScore"):
        if k in game_data:
            result[k] = game_data[k]

    return result


# ===========================================================================
# Batch Analysis
# ===========================================================================

def analyze_slate(games, team_stats, weights, kalman_states=None,
                  injury_map=None, residual_var=None, thresholds=None):
    """
    Analyze a full slate of NFL games.

    Parameters
    ----------
    games : list of dict
        List of game_data dicts.
    team_stats : dict
        All team stats.
    weights : dict
        Ridge coefficients.
    kalman_states : dict or None
        Kalman state.
    injury_map : dict or None
        {game_key: {"home": float, "away": float, ...}} injury deltas per game.
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
        inj = (injury_map or {}).get(game_key, None)

        result = analyze_game(
            g, team_stats, weights,
            kalman_states=kalman_states,
            injury_deltas=inj,
            residual_var=residual_var,
            thresholds=thresholds,
        )
        if result is not None:
            results.append(result)

    return results


# ===========================================================================
# Ridge Regression Training
# ===========================================================================

def train_ridge(historical_features, historical_margins, alpha=None):
    """
    Train ridge regression on historical matchup data.

    If alpha is None, uses 5-fold cross-validation over a grid of alphas
    to select the best regularization parameter.

    Parameters
    ----------
    historical_features : np.ndarray
        Feature matrix of shape (n_games, n_features).
    historical_margins : np.ndarray
        Target vector of actual score margins (home - away), shape (n_games,).
    alpha : float or None
        Fixed regularization parameter. If None, uses RidgeCV.

    Returns
    -------
    dict
        Trained weights dict with "coefficients", "intercept", "alpha",
        "feature_columns", "n_train", "residual_var", "r_squared",
        "cv_scores" (if CV was used).
    """
    X = np.asarray(historical_features, dtype=np.float64)
    y = np.asarray(historical_margins, dtype=np.float64)

    n_samples, n_features = X.shape

    if n_samples < 20:
        print(f"  [model_engine] WARNING: Only {n_samples} training samples."
              " Ridge results may be unreliable.")

    RidgeCV = _get_ridge_cv()

    if alpha is not None:
        # Fixed alpha — still use RidgeCV with single alpha for consistent API
        model = RidgeCV(alphas=[alpha], cv=min(5, n_samples))
    else:
        # CV over a wide range of alphas
        alphas = np.logspace(-2, 4, 50)  # 0.01 to 10000
        n_folds = min(5, n_samples)
        model = RidgeCV(alphas=alphas, cv=n_folds, scoring="neg_mean_squared_error")

    model.fit(X, y)

    # Compute residuals for residual variance
    predictions = model.predict(X)
    residuals = y - predictions
    residual_var = float(np.var(residuals))
    r_squared = float(model.score(X, y))

    # Build weights dict
    coefficients = model.coef_.tolist()
    intercept = float(model.intercept_)
    best_alpha = float(model.alpha_)

    print(f"  [model_engine] Ridge trained: alpha={best_alpha:.4f},"
          f" R²={r_squared:.4f}, residualVar={residual_var:.1f},"
          f" n={n_samples}")

    # Log feature importance
    feature_importance = sorted(
        zip(FEATURE_COLUMNS[:n_features], coefficients),
        key=lambda x: abs(x[1]),
        reverse=True,
    )
    for fname, coef in feature_importance[:5]:
        print(f"    {fname}: {coef:+.4f}")

    return {
        "coefficients": coefficients,
        "intercept": intercept,
        "alpha": best_alpha,
        "feature_columns": FEATURE_COLUMNS[:n_features],
        "n_train": n_samples,
        "residual_var": residual_var,
        "r_squared": r_squared,
        "source": "ridge_cv",
    }


def retrain_incremental(existing_weights, new_features, new_margins,
                        blend_factor=0.3):
    """
    Incrementally update ridge weights by blending new training with existing.

    This is NOT used in-season (self_tune handles that). This is for
    extending backfill or adjusting after additional historical data.

    Parameters
    ----------
    existing_weights : dict
        Current weights.
    new_features : np.ndarray
        New feature matrix.
    new_margins : np.ndarray
        New target margins.
    blend_factor : float
        How much weight to give new data (0=keep old, 1=use only new).

    Returns
    -------
    dict
        Updated weights.
    """
    new_weights = train_ridge(new_features, new_margins,
                              alpha=existing_weights.get("alpha"))

    old_coefs = np.array(existing_weights["coefficients"])
    new_coefs = np.array(new_weights["coefficients"])

    # Pad if needed
    max_len = max(len(old_coefs), len(new_coefs))
    if len(old_coefs) < max_len:
        old_coefs = np.pad(old_coefs, (0, max_len - len(old_coefs)))
    if len(new_coefs) < max_len:
        new_coefs = np.pad(new_coefs, (0, max_len - len(new_coefs)))

    blended_coefs = ((1 - blend_factor) * old_coefs
                     + blend_factor * new_coefs)
    blended_intercept = ((1 - blend_factor) * existing_weights["intercept"]
                         + blend_factor * new_weights["intercept"])

    return {
        **new_weights,
        "coefficients": blended_coefs.tolist(),
        "intercept": float(blended_intercept),
        "n_train": existing_weights.get("n_train", 0) + new_weights["n_train"],
        "source": "incremental_blend",
    }


# ===========================================================================
# Helper: Margin Features for self_tune compatibility
# ===========================================================================

def _extract_margin_features(home_stats, away_stats, is_home=True,
                             inj_home=0.0, inj_away=0.0):
    """
    Extract margin features in a format compatible with core/self_tune.py.

    self_tune expects _marginFeatures with delta keys matching the weight
    keys (wPassOff, wRushOff, etc.) and an hca flag.
    """
    h = home_stats
    a = away_stats

    # Compute baseline: average of offensive + opposing defensive EPA
    h_off = h.get("passOffEPA", 0.0) + h.get("rushOffEPA", 0.0)
    a_off = a.get("passOffEPA", 0.0) + a.get("rushOffEPA", 0.0)
    h_def = h.get("passDefEPA", 0.0) + h.get("rushDefEPA", 0.0)
    a_def = a.get("passDefEPA", 0.0) + a.get("rushDefEPA", 0.0)

    baseline = ((h_off - a_def) - (a_off - h_def)) / 2

    return {
        "dPassOff": h.get("passOffEPA", 0.0) - a.get("passDefEPA", 0.0),
        "dRushOff": h.get("rushOffEPA", 0.0) - a.get("rushDefEPA", 0.0),
        "dPassDef": h.get("passDefEPA", 0.0) - a.get("passOffEPA", 0.0),
        "dRushDef": h.get("rushDefEPA", 0.0) - a.get("rushOffEPA", 0.0),
        "dPace": ((h.get("pace", 63) + a.get("pace", 63)) / 2 - 63.0),
        "dRZ": h.get("rzTDOff", 0.0) - a.get("rzTDDef", 0.0),
        "hca": 1.0 if is_home else 0.0,
        "injHome": inj_home,
        "injAway": inj_away,
        "_baseline": baseline,
    }


# ===========================================================================
# Helper: Team Name Resolution
# ===========================================================================

def _resolve_team(team_stats, name):
    """
    Resolve a team name to its canonical key in team_stats.

    Tries exact match, then lowered match, then alias lookup.
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

    # Alias lookup — resolve full name to canonical, then check all aliases
    # including abbreviations (since nfl_data_py uses abbreviations as keys)
    aliases = ENGINE_CONFIG.get("TEAM_NAME_ALIASES", {})
    canonical = aliases.get(name_lower)
    if canonical:
        # Direct canonical match
        if canonical in team_stats:
            return canonical
        # Try abbreviation: NFL_TEAMS maps canonical -> [abbrev, ...]
        try:
            from defaults import NFL_TEAMS
            abbrevs = NFL_TEAMS.get(canonical, [])
            for a in abbrevs:
                if a in team_stats:
                    return a
                if a.upper() in team_stats:
                    return a.upper()
        except ImportError:
            pass

    # Try the name directly as abbreviation (e.g., "CAR", "ATL")
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
    if s_diff >= thresh.get("elite", 5.0):
        return "elite"
    elif s_diff >= thresh.get("high", 3.0):
        return "high"
    elif s_diff >= thresh.get("medium", 1.5):
        return "medium"
    else:
        return "low"


# ===========================================================================
# Helper: Format Spread Pick
# ===========================================================================

def _format_spread_pick(home_name, away_name, spread_side, abs_line, home_fav):
    """
    Format a spread pick string like "Buffalo Bills -3.0".

    Parameters
    ----------
    home_name, away_name : str
    spread_side : str
        "home" or "away"
    abs_line : float
        Absolute value of the spread.
    home_fav : bool
        True if home team is favored.
    """
    if spread_side == "home":
        team = home_name
        sign = "-" if home_fav else "+"
    else:
        team = away_name
        sign = "+" if home_fav else "-"

    return f"{team} {sign}{abs_line}"


# ===========================================================================
# Helper: Injury Note Builder
# ===========================================================================

def _build_injury_note(injury_deltas):
    """Build human-readable injury note from injury delta dict."""
    if not injury_deltas:
        return None

    parts = []
    for side in ("away", "home"):
        injuries = injury_deltas.get(f"{side}Injuries", [])
        if injuries:
            label = "Away" if side == "away" else "Home"
            entries = ", ".join(
                f"{i.get('player', '?')} ({i.get('status', '?')}/{i.get('position', '?')})"
                for i in injuries
            )
            parts.append(f"{label}: {entries}")

    return " | ".join(parts) if parts else None


# ===========================================================================
# Helper: Normal CDF (pure Python fallback — prefer scipy.stats.norm above)
# ===========================================================================

def _normal_cdf_pure(x):
    """
    Approximation of the standard normal CDF.
    Used only if scipy is unavailable.
    """
    a1 = 0.254829592
    a2 = -0.284496736
    a3 = 1.421413741
    a4 = -1.453152027
    a5 = 1.061405429
    p = 0.3275911
    sign = -1 if x < 0 else 1
    z = abs(x) / math.sqrt(2)
    t = 1.0 / (1.0 + p * z)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-z * z)
    return 0.5 * (1.0 + sign * y)


# ===========================================================================
# Convenience: Load everything needed to run
# ===========================================================================

def load_engine(data_dir=None):
    """
    Load weights and return a ready-to-use engine configuration.

    Parameters
    ----------
    data_dir : str or None
        Path to pyNFL/data/. If None, resolves relative to this file.

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
