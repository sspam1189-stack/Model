# scripts/defaults.py  (pyWNBAFull)
# WNBA full-season (spreads) model constants — forked from pyFull/ (NBA) and
# retuned for the WNBA. Self-tune adjusts the projection weights over time; the
# variance/HCA priors below are seeded from a real WNBA backtest.
#
# ── WNBA CALIBRATION EVIDENCE (backtest 2026-07-01) ──────────────────────────
# Source: ESPN WNBA scoreboard final scores, 2024 + 2025 regular seasons,
#   All-Star / exhibition games excluded. 584 games pooled.
#     2024: 262 games, avg home margin +1.53, marginSD 13.40, totalSD 16.92,
#           avg total 163.4, avg team score 81.7
#     2025: 322 games, avg home margin +3.25, marginSD 15.72, totalSD 16.84,
#           avg total 163.0, avg team score 81.5
#     POOLED (584): HCA +2.48, marginSD 14.75 (var 218), totalSD 16.87 (var 285)
#   2026-to-date (151 games, live-forward holdout): HCA +2.87, marginSD 14.56,
#           avg total 172.7 (scoring up post-expansion — auto-absorbed via pace).
#
# Derived WNBA constants (differences vs NBA in comments):
#   hca         2.5   (NBA 1.5)   — pooled avg home margin ~2.48; WNBA HCA is
#                                    larger AND noisier year-to-year than NBA.
#   marginNoise 218   (NBA 144)   — marginSD 14.75^2. WNBA margins are wider.
#   totalNoise  285   (NBA 170)   — totalSD 16.87^2.
#   residualVar 150   (NBA 100)   — scaled by the same residual/marginNoise
#                                    ratio NBA used (~0.69 * 218 ≈ 150).
#   KALMAN gameNoise 218 (NBA 144), initialVar 20 / drift 0.25 loosened for the
#                                    short ~44-game/15-team season so real teams
#                                    aren't over-smoothed early.
#   MIN_GP 5    (NBA 15)          — short season; trust teams after 5 games.
#
# PROJECTION-ACCURACY / CALIBRATION CHECK (scripts/backtest_wnba.py, engine run
# on real 2024+2025 finals at synthetic line=0, teams with >=5 GP, n=79):
#   Margin MAE 10.53 pts, bias +1.14 (near-unbiased, mild home lean == HCA ok),
#   straight-up winner 64.6%.
#   pCover calibration (predicted pHomeCover vs realized home-win rate):
#     0.5-0.6 -> 57.9%, 0.6-0.7 -> 53.8%, 0.7-0.8 -> 90%, 0.8-0.9 -> 100%.
#   Monotonic and roughly on-diagonal; top buckets slightly OVER-realize (model
#   marginally under-confident up high — the safe direction). Confirms the WNBA
#   variance/HCA priors produce well-ordered, roughly calibrated probabilities.
#   (Sanity gate on the variance constants — NOT an ATS-ROI claim; no lines.)
#
# HISTORICAL LINES CAVEAT: The Odds API *historical* endpoint requires a paid
# plan (embedded free keys 401 on 2026-07-01), and ESPN does not retain WNBA
# closing lines. So the pCover cutoff and edge bounds below could NOT be ATS-
# backtested against real closing lines. They start from the NBA-tuned values;
# because WNBA's larger variance (above) already deflates pCover, these are
# conservative. They are validated FORWARD on 2026 live FanDuel lines once a
# graded sample accrues — retune probHigh from live results, not from priors.

DEFAULT_STATS = {}

DEFAULT_W = {
    # -- Projection weights (means) ------------------------------------------
    "wOFF": 1,          # offensive rating weight
    "wTS":  1,          # true shooting % delta weight
    "wTO":  1,          # turnover % delta weight
    "wORR": 1,          # offensive rebound rate delta weight
    "wDEF": 0.4,        # defensive rating delta weight
    "constant": 0,      # additive constant to base projection
    "paceAdj": 1,       # pace multiplier (1.0 = neutral)
    "hca": 2.5,         # home court advantage (points) — WNBA pooled ~2.48 (NBA 1.5)

    # -- Legacy pick thresholds (kept as fallback / display reference) --------
    "sprHigh": 4,       # min sDiff for a spread pick
    "ouHigh":  5,       # min |tDiff| for a total pick
    "sprEliteBump": 3,
    "ouEliteBump":  3,

    # -- Bayesian probability thresholds --------------------------------------
    # P(cover) must exceed these to fire a pick. At -110 juice break-even ~0.524.
    # Started from NBA-tuned 0.58; NOT ATS-backtested for WNBA (no historical
    # lines) — validate/retune forward on live 2026 graded picks.
    "probHigh":  0.58,   # min P(cover) for actionable (elite) spread pick
    "probElite": 0.63,

    "probOUHigh":  0.58, # totals engine is DISABLED for fullseason (see
    "probOUElite": 0.64, # ENGINE bayes config) — kept for shape parity only.

    # -- Stats blending weights -----------------------------------------------
    # recentWeight: last-N window vs season. WNBA plays ~3-4 games/wk/team so the
    # rolling window is small; keep NBA's proven high recent weight.
    "recentWeight": 0.87,

    # locationWeight: adjustment toward home/away splits.
    "locationWeight": 0.25,

    # h2hWeight: disabled (adds noise without accuracy gain — NBA finding, kept).
    "h2hWeight": 0.0,
}

# -- Bayesian weight variances ------------------------------------------------
DEFAULT_W_VAR = {
    "wTS":      4.0,
    "wTO":      4.0,
    "wORR":     4.0,
    "wDEF":     4.0,
    "hca":      2.0,
    "constant": 4.0,
}

# -- Bayesian hyperparameters -------------------------------------------------
BAYES_HYPER = {
    # Observation noise for margin regression (points^2).
    # WNBA margins have ~14.75 pt std dev -> ~218 variance (NBA was 144).
    "marginNoise": 218,

    # Observation noise for total regression (points^2).
    # WNBA total std ~16.87 -> ~285 (NBA was 170).
    "totalNoise": 285,

    "minWeightVar": 0.05,
    "maxWeightVar": 10,

    # Residual game variance (points^2) — irreducible projection noise.
    # Scaled from NBA (100 at marginNoise 144, ratio ~0.69) -> ~150 for WNBA.
    "residualVar": 150,
}

# --- Core engine config (used by core/model_engine.py) -------------------------
# WNBA team-name aliases: map short/odds-feed forms to nba_api full team names.
# 15 teams (2025: 13; 2026 adds Golden State Valkyries already present + Portland
# Fire, Toronto Tempo expansion).
ENGINE_CONFIG = {
    "TEAM_NAME_ALIASES": {
        "atlanta":              "Atlanta Dream",
        "dream":                "Atlanta Dream",
        "chicago":              "Chicago Sky",
        "sky":                  "Chicago Sky",
        "connecticut":          "Connecticut Sun",
        "sun":                  "Connecticut Sun",
        "dallas":               "Dallas Wings",
        "wings":                "Dallas Wings",
        "golden state":         "Golden State Valkyries",
        "valkyries":            "Golden State Valkyries",
        "indiana":              "Indiana Fever",
        "fever":                "Indiana Fever",
        "las vegas":            "Las Vegas Aces",
        "vegas":                "Las Vegas Aces",
        "aces":                 "Las Vegas Aces",
        "los angeles":          "Los Angeles Sparks",
        "la sparks":            "Los Angeles Sparks",
        "sparks":               "Los Angeles Sparks",
        "minnesota":            "Minnesota Lynx",
        "lynx":                 "Minnesota Lynx",
        "new york":             "New York Liberty",
        "liberty":              "New York Liberty",
        "phoenix":              "Phoenix Mercury",
        "mercury":              "Phoenix Mercury",
        "portland":             "Portland Fire",
        "fire":                 "Portland Fire",
        "seattle":              "Seattle Storm",
        "storm":                "Seattle Storm",
        "toronto":              "Toronto Tempo",
        "tempo":                "Toronto Tempo",
        "washington":           "Washington Mystics",
        "mystics":              "Washington Mystics",
    },
    # Short WNBA season — trust a team's stats after 5 games (NBA used 15).
    "MIN_GP": 5,
    "USE_SAFE_FUZZY": False,
    "USE_COLLAPSE_ABBR": False,
}

# --- Self-tune HCA clamps (used by core/self_tune.py) -------------------------
# WNBA HCA is larger and noisier than NBA (2024 +1.53, 2025 +3.25). Clamp the
# self-tuned HCA to a band centered on the pooled ~2.5 rather than NBA's 1.5-3.5.
HCA_CLAMP_LO  = 1.0
HCA_CLAMP_HI  = 4.0
HCA_VAR_FLOOR = 0.2

# --- Kalman filter defaults (used by core/kalman_state.py) ---------------------
# Short season -> looser priors so real team strength emerges faster and isn't
# over-smoothed. gameNoise matches the WNBA margin variance (218).
KALMAN_DEFAULTS = {
    "initialVar":  20,     # NBA 16 — start less certain (fewer games to learn from)
    "gameNoise":   218,    # NBA 144 — WNBA margin variance
    "dailyDrift":  0.25,   # NBA 0.15 — teams change faster over a compressed season
    "minVar":      2.0,
    "maxVar":      30,
}
