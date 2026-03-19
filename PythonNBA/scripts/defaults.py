# scripts/defaults.py
# Starting weights for the model. Self-tune adjusts ALL of these over time.
#
# KEY INSIGHT: The projection formula is a tool to find edges vs. the line.
# Profitability comes from (a) accurate projections and (b) betting at the
# right threshold. Self-tune handles both.
#
# At -110 juice you need 52.4% win rate to break even.
#
# BAYESIAN UPGRADE: Weights now have mean + variance. The variance tracks
# how confident the model is in each weight. Picks are based on P(cover)
# rather than raw edge thresholds.

DEFAULT_STATS = {}

DEFAULT_W = {
    # -- Projection weights (means) --
    "wTS": 1,           # true shooting % delta weight
    "wTO": 1,           # turnover % delta weight
    "wORR": 1,          # offensive rebound rate delta weight
    "wNET": 1,          # net rating delta weight (applied at 0.5x in projScore)
    "constant": 0,      # additive constant to base projection
    "paceAdj": 1,       # pace multiplier (1.0 = neutral)
    "hca": 1.5,         # home court advantage (points)

    # -- Legacy pick thresholds (kept as fallback / display reference) --
    "sprHigh": 4,       # min sDiff for a spread pick
    "ouHigh": 5,        # min |tDiff| for a total pick
    "sprEliteBump": 3,  # extra sDiff above sprHigh to label "elite"
    "ouEliteBump": 3,   # extra |tDiff| above ouHigh to label "elite"

    # -- Bayesian probability thresholds --
    # These replace sprHigh/ouHigh as the primary pick trigger.
    # P(cover) must exceed these values to fire a pick.
    # At -110 juice, break-even is ~0.524. We require a meaningful edge above that.
    "probHigh": 0.57,    # min P(cover) for "high" confidence pick
    "probElite": 0.63,   # min P(cover) for "elite" confidence (lock)

    "probOUHigh": 0.58,  # totals are noisier, require slightly more confidence
    "probOUElite": 0.64,

    # -- Stats blending weights --
    # recentWeight: how much to weight last-10-games vs full season.
    #   0.0 = pure season averages, 1.0 = pure last 10 games.
    #   0.35 = research-backed starting point for NBA recent form.
    "recentWeight": 0.35,

    # locationWeight: how much to adjust toward home/away splits.
    #   0.0 = ignore splits, 1.0 = full location-specific stats.
    #   0.25 = moderate adjustment. Captures home/away gaps without overfitting.
    "locationWeight": 0.25,
}

# -- Bayesian weight variances --
# Initial uncertainty for each weight. Large = we don't know yet.
# As games are processed, these shrink. Self-tune maintains these.

DEFAULT_W_VAR = {
    "wTS": 4.0,       # wide prior -- let the data decide
    "wTO": 4.0,
    "wORR": 4.0,
    "wNET": 4.0,
    "hca": 2.0,        # HCA is well-studied (~3 pts), less uncertain
    "constant": 4.0,
    # paceAdj is handled separately (non-linear, stays gradient descent)
}

# -- Bayesian hyperparameters --

BAYES_HYPER = {
    # Observation noise for margin regression (points**2).
    # NBA margins have ~12 pt std dev -> 144 variance.
    # This controls how much each game moves the weights.
    "marginNoise": 144,

    # Observation noise for total regression (points**2).
    # Totals are slightly noisier than margins.
    "totalNoise": 170,

    # Minimum variance floor for weights -- prevent overconfidence
    "minWeightVar": 0.05,

    # Maximum variance cap -- prevent reset to useless
    "maxWeightVar": 10,

    # Residual game variance (points**2) -- irreducible noise in projections
    # beyond what team uncertainty and weight uncertainty explain.
    # This is the "stuff the model can't capture" -- matchup specifics,
    # travel, rest, motivation, ref variance, etc.
    "residualVar": 100,
}

# -- XGBoost ensemble parameters --

XGB_RETRAIN_INTERVAL = 20      # games between retrains
XGB_MIN_TRAINING_GAMES = 50    # minimum graded history before XGBoost activates
XGB_ENSEMBLE_WEIGHT = 0.35     # XGBoost weight in ensemble (Bayesian gets 0.65)
XGB_LOOKBACK_WINDOW = 30       # games for adaptive weight calculation
