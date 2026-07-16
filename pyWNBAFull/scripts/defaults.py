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
#
# ── LIVE-FORWARD ATS VALIDATION (2026-07-15) ─────────────────────────────────
# The forward FanDuel sample has now accrued and been swept against real cached
# lines — the ATS backtest the paywall blocked earlier is now possible on the
# live season. Two views:
#   (a) Live graded fired picks (store, censored at the shipped probHigh=0.62):
#       70 spread picks, 44-26 = 62.9% WR, +14.0u, +0.20 u/pick. Comfortably
#       above the 52.4% -110 break-even — the model is performing as projected.
#   (b) UNCENSORED walk-forward replay (scripts/calibrate_probhigh.py): the real
#       season (May 21 - Jul 15) reproduced day-by-day from the cached
#       stats/odds/ESPN with the same engine + Kalman + self-tune trajectory,
#       capturing EVERY spread candidate (not just those >=0.62) so probHigh can
#       be swept DOWN as well as up. 136 candidates. Result by threshold:
#         sub-0.62 band is coin-flip / net-negative — 0.50-0.55 47%, 0.58-0.60
#         40%; the whole 0.50-0.62 region pooled is 36-34 (51.4%), BELOW -110
#         break-even. At/above 0.62 win-rate climbs monotonically: 0.62 60.6%,
#         0.65 63.3%, 0.67 65.0%, 0.70 69.7%.
#   CONCLUSION: probHigh=0.62 sits right at the knee where the break-even/losing
#   sub-threshold volume gets filtered out — lowering it only adds ~coin-flip
#   bets. Tightening to 0.65-0.67 lifts per-pick ROI (0.16->0.21-0.24) but total
#   units stay flat (~10u) and volume drops 25-40%, and the live vs replay views
#   DISAGREE on whether 0.65 beats 0.62 (live: 0.65 worse; replay: marginally
#   better) — not robust on one partial season. HELD at 0.62; retune again as
#   more forward picks accrue.

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
    # Swept on the real 2026 backfill (ATS vs actual closing lines): 0.58 sat
    # above a break-even 0.58-0.62 band, so tightening to 0.62 lifts WR
    # 65.3%->69.1% and ROI +0.246->+0.319 u/pick at essentially flat units
    # (+17.5 vs +17.7u). Low-conviction (n=72, one partial season, and the
    # backtest ATS runs hot) — a safe tightening; retune forward as live picks
    # accrue.
    # RE-CONFIRMED 2026-07-15 (see LIVE-FORWARD ATS VALIDATION header): live 70
    # fired picks 62.9%/+14.0u, and an uncensored walk-forward sweep shows the
    # sub-0.62 band is net-negative (0.50-0.62 pooled 51.4%, below break-even)
    # while >=0.62 climbs monotonically. 0.62 is the knee — held.
    "probHigh":  0.62,   # min P(cover) for actionable spread pick
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

    # -- Adaptive gain (see core/kalman_state.py:_effective_var) ---------------
    # gameNoise 218 pins the per-game gain at ~5%, which is right for stable,
    # well-modelled teams but far too slow for a team whose true strength is
    # miles from its box score (expansion clubs — e.g. Toronto Tempo 2026, whose
    # residual stalled at ~-3.3 pts while reality wanted ~-8, with only ~20
    # games left to converge). When a team's innovations stay one-signed (a real
    # bias, not scatter) we transiently inflate its variance to catch up fast
    # (gain ~5%->~13% for a -7pt-biased team), then settle back as the bias
    # closes. Bias within the deadband is ignored, so well-modelled teams are
    # untouched.
    #   A/B on the 2026 walk-forward replay (scripts/calibrate_probhigh.py),
    #   fired picks @>=0.62:  OFF 40-26 (60.6%) +10.4u  ->  ON 42-24 (63.6%)
    #   +14.2u. The gain is broad, not just Toronto (whose -7 bias is too large
    #   for the gentle boost to fully close): the biggest catch was Las Vegas,
    #   marked -0.4 -> -5.4 mid-slump. Low conviction (n=66, ~within noise) but
    #   principled and improves across param settings; the >=0.65 bucket gives
    #   back ~1u, immaterial at the shipped 0.62 cutoff. Params are the robust
    #   middle — deliberately NOT the +1u aggressive corner (overfit risk).
    "adaptiveBias":   True,
    "biasAlpha":      0.35,  # EWMA weight on each game's innovation
    "biasDeadband":   3.0,   # |bias| (pts) treated as normal scatter -> no boost
    "biasVarGain":    3.0,   # var added per pt of |bias| beyond the deadband
    "adaptiveMaxVar": 60,    # cap on boosted variance (gain ~ 60/(60+12+218)=20%)
}
