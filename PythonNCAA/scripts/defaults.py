# scripts/defaults.py
# Starting weights for NCAA model. Self-tune adjusts ALL of these over time.

DEFAULT_STATS = {}

DEFAULT_W = {
    # Projection weights (means)
    "wTS": 1,           # true shooting / eFG% delta weight
    "wTO": 1,           # turnover rate delta weight
    "wORR": 1,          # offensive rebound rate delta weight
    "wNET": 1,          # net rating delta weight
    "constant": 0,      # additive constant
    "paceAdj": 1,       # pace multiplier (1.0 = neutral)
    "hca": 4.0,         # home court advantage (points) — higher in college

    # Legacy thresholds (fallback)
    "sprHigh": 4,
    "ouHigh": 5,
    "sprEliteBump": 3,
    "ouEliteBump": 3,

    # Bayesian probability thresholds
    "probHigh": 0.65,       # P(cover) threshold for spread picks
    "probHighFav": 0.65,    # (legacy, unused — single threshold now)
    "probElite": 0.67,
    "probOUHigh": 0.58,
    "probOUElite": 0.64,

    # Stats blending weights
    "recentWeight": 0.30,     # less weight on recent form (fewer games, noisier)
    "locationWeight": 0.20,   # less weight on home/away splits (smaller sample)
}

DEFAULT_W_VAR = {
    "wTS": 4.0,
    "wTO": 4.0,
    "wORR": 4.0,
    "wNET": 4.0,
    "hca": 2.0,
    "constant": 4.0,
}

BAYES_HYPER = {
    "marginNoise": 196,    # NCAA margins ~14 pt std dev -> 196 variance
    "totalNoise": 210,     # totals noisier in college
    "minWeightVar": 0.05,
    "maxWeightVar": 10,
    "residualVar": 130,    # higher irreducible noise in college
}
