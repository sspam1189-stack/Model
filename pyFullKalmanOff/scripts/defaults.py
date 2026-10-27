# NBA Full KalmanOff -- a second NBA full-season model, identical to pyFull
# except three settings (2026-10-22), chosen on the clean 2025-26 backfill
# through run_daily (replay_season.py):
#   KALMAN_SCALE 0.0      (pyFull 1.0)   Kalman offsets not applied
#   recentWeight 0.35     (pyFull 0.87)  less weight on last-10 form
#   HCA_CLAMP_LO 0.0      (pyFull 1.5)   home court free to tune below 1.5
#                    margin err  home bias  reg season       full season
#   pyFull             12.17      +0.80    176-164 -4.4u    188-176 -5.6u
#   KalmanOff          11.62      +0.10     95-82 +4.8u     107-91 +6.9u
#   Vegas line         11.19      +0.38
# Own data (pyFullKalmanOff/data), recorded inputs (data/run_inputs/
# nba-kalmanoff) and dashboard feed (PythonDashboard/data/
# fullseason-kalmanoff.json). Shares the stats/odds/injury caches.
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
    # -- Projection weights (means) ------------------------------------------
    "wOFF": 1,          # offensive rating weight
    "wTS":  1,          # true shooting % delta weight
    "wTO":  1,          # turnover % delta weight
    "wORR": 1,          # offensive rebound rate delta weight
    # Since 2026-10-27 (MARGIN_FORMULA "dnet") this weights the whole
    # net-rating difference (wNET); was 0.4 on opponent DEF vs league.
    "wDEF": 1.0,
    "constant": 0,      # additive constant to base projection
    "paceAdj": 1,       # pace multiplier (1.0 = neutral)
    "hca": 1.5,         # home court advantage (points)

    # -- Legacy pick thresholds (kept as fallback / display reference) --------
    "sprHigh": 4,       # min sDiff for a spread pick
    "ouHigh":  5,       # min |tDiff| for a total pick
    "sprEliteBump": 3,  # extra sDiff above sprHigh to label "elite"
    "ouEliteBump":  3,  # extra |tDiff| above ouHigh to label "elite"

    # -- Bayesian probability thresholds --------------------------------------
    # These replace sprHigh/ouHigh as the primary pick trigger.
    # P(cover) must exceed these values to fire a pick.
    # At -110 juice, break-even is ~0.524. We require a meaningful edge above that.
    "probHigh":  0.58,   # min P(cover) for "high" confidence pick
    "probElite": 0.63,   # min P(cover) for "elite" confidence (lock)

    "probOUHigh":  0.58, # totals are noisier, require slightly more confidence
    "probOUElite": 0.64,

    # -- Stats blending weights -----------------------------------------------
    # recentWeight: how much to weight last-10-games vs full season.
    #   0.0 = pure season averages, 1.0 = pure last 10 games.
    #   0.35 = research-backed starting point for NBA recent form.
    "recentWeight": 0.35,

    # locationWeight: how much to adjust toward home/away splits.
    #   0.0 = ignore splits, 1.0 = full location-specific stats.
    #   0.25 = moderate adjustment. Captures home/away gaps without overfitting.
    "locationWeight": 0.25,

    # h2hWeight: disabled — H2H adds noise without improving accuracy.
    # Backtest: pyNBA (no H2H) 62.1% vs pyFull (with H2H) 60.6%.
    "h2hWeight": 0.0,
}

# -- Stake tier ---------------------------------------------------------------
# Tiered-staking backtest (scripts/stake_tiers.py) on the 412-227 season: the
# pCover >= 0.65 band went 257-119 (68.4%) vs 58.9% below, and the marginal
# upsized stake earns ~33% ROI vs the book's 25%. So a fired pick with
# pCover >= ELITE_STAKE_CUT is a 2u play, otherwise 1u. 0.65 (not 0.63) is the
# line because the 0.63-0.65 band is a calibration dip (41-31, 56.9% — the
# weakest slice in the book) that should NOT be doubled. Display/staking overlay
# only — does not change which picks fire, and the headline ATS record stays
# flat 1u. (Note: playoffs already floor probHigh at 0.65, so every playoff pick
# clears this cut and is 2u — intended: postseason picks are all high-conviction.)
ELITE_STAKE_CUT = 0.65
BASE_STAKE_UNITS = 1
ELITE_STAKE_UNITS = 2


# Fire floor (2026-10-08, by choice): only the 2u band fires. The 1u band
# (0.58-0.65) went 155-108 (58.9%), +36.20u, +12.5% ROI on 289.30u risked over
# the 2025-26 season -- positive, but 1u of every 2.4 risked for under half the
# 2u band's +30.5% ROI, so we pass on those games. This costs expected profit:
# +0.138u/pick forgone, below the 0.20u/pick bar a threshold change normally
# has to clear. Applied per run in run_daily (a copy, like the playoff floor), so
# the self-tuned probHigh in history.json is untouched and backfills keep
# replaying the full 0.58 book. Set to None to restore the tuned threshold.
MIN_FIRE_PCOVER = ELITE_STAKE_CUT


def stake_units_for(p_cover):
    """Recommended stake (units) for a fired spread pick given its P(cover)."""
    if p_cover is not None and p_cover >= ELITE_STAKE_CUT:
        return ELITE_STAKE_UNITS
    return BASE_STAKE_UNITS

# -- Bayesian weight variances ------------------------------------------------
# Initial uncertainty for each weight. Large = we don't know yet.
# As games are processed, these shrink. Self-tune maintains these.

DEFAULT_W_VAR = {
    "wTS":      4.0,     # wide prior -- let the data decide
    "wTO":      4.0,
    "wORR":     4.0,
    "wDEF":     4.0,
    "hca":      2.0,     # HCA is well-studied (~3 pts), less uncertain
    "constant": 4.0,
    # paceAdj is handled separately (non-linear, stays gradient descent)
}

# -- Bayesian hyperparameters -------------------------------------------------

# -- Bayesian hyperparameters -------------------------------------------------

BAYES_HYPER = {
    # Observation noise for margin regression (points^2).
    # NBA margins have ~12 pt std dev -> 144 variance.
    # This controls how much each game moves the weights.
    "marginNoise": 144,

    # Observation noise for total regression (points^2).
    # Totals are slightly noisier than margins.
    "totalNoise": 170,

    # Minimum variance floor for weights -- prevent overconfidence
    "minWeightVar": 0.05,

    # Maximum variance cap -- prevent reset to useless
    "maxWeightVar": 10,

    # Residual game variance (points^2) -- irreducible noise in projections
    # beyond what team uncertainty and weight uncertainty explain.
    # This is the "stuff the model can't capture" -- matchup specifics,
    # travel, rest, motivation, ref variance, etc.
    "residualVar": 100,
}

# --- Core engine config (used by core/model_engine.py) -------------------------
# Weights self-tune fits: only the ones the projection uses (TS/TO/ORR were
# dropped from the projection on 2026-04-03 but were still being fitted).
TUNE_KEYS = ["wDEF", "hca"]

ENGINE_CONFIG = {
    "TEAM_NAME_ALIASES": {
        "la lakers":             "Los Angeles Lakers",
        "lakers":                "Los Angeles Lakers",
        "la clippers":           "LA Clippers",
        "los angeles clippers":  "LA Clippers",
        "clippers":              "Los Angeles Clippers",
        "golden state":          "Golden State Warriors",
        "warriors":              "Golden State Warriors",
        "oklahoma city":         "Oklahoma City Thunder",
        "thunder":               "Oklahoma City Thunder",
        "new orleans":           "New Orleans Pelicans",
        "pelicans":              "New Orleans Pelicans",
        "new york":              "New York Knicks",
        "knicks":                "New York Knicks",
        "san antonio":           "San Antonio Spurs",
        "spurs":                 "San Antonio Spurs",
        "portland":              "Portland Trail Blazers",
        "trail blazers":         "Portland Trail Blazers",
        "philadelphia":          "Philadelphia 76ers",
        "76ers":                 "Philadelphia 76ers",
        "sixers":                "Philadelphia 76ers",
        "minnesota":             "Minnesota Timberwolves",
        "timberwolves":          "Minnesota Timberwolves",
        "wolves":                "Minnesota Timberwolves",
        "memphis":               "Memphis Grizzlies",
        "grizzlies":             "Memphis Grizzlies",
        "charlotte":             "Charlotte Hornets",
        "hornets":               "Charlotte Hornets",
        "indiana":               "Indiana Pacers",
        "pacers":                "Indiana Pacers",
        "washington":            "Washington Wizards",
        "wizards":               "Washington Wizards",
        "orlando":               "Orlando Magic",
        "magic":                 "Orlando Magic",
        "miami":                 "Miami Heat",
        "heat":                  "Miami Heat",
        "atlanta":               "Atlanta Hawks",
        "hawks":                 "Atlanta Hawks",
        "chicago":               "Chicago Bulls",
        "bulls":                 "Chicago Bulls",
        "detroit":               "Detroit Pistons",
        "pistons":               "Detroit Pistons",
        "cleveland":             "Cleveland Cavaliers",
        "cavaliers":             "Cleveland Cavaliers",
        "cavs":                  "Cleveland Cavaliers",
        "toronto":               "Toronto Raptors",
        "raptors":               "Toronto Raptors",
        "brooklyn":              "Brooklyn Nets",
        "nets":                  "Brooklyn Nets",
        "boston":                 "Boston Celtics",
        "celtics":               "Boston Celtics",
        "milwaukee":             "Milwaukee Bucks",
        "bucks":                 "Milwaukee Bucks",
        "denver":                "Denver Nuggets",
        "nuggets":               "Denver Nuggets",
        "utah":                  "Utah Jazz",
        "jazz":                  "Utah Jazz",
        "phoenix":               "Phoenix Suns",
        "suns":                  "Phoenix Suns",
        "sacramento":            "Sacramento Kings",
        "kings":                 "Sacramento Kings",
        "dallas":                "Dallas Mavericks",
        "mavericks":             "Dallas Mavericks",
        "mavs":                  "Dallas Mavericks",
        "houston":               "Houston Rockets",
        "rockets":               "Houston Rockets",
    },
    # Two-stage burn-in (2026-10-27). Projections start once 20+ teams have
    # MIN_GP games, so Kalman and self-tune learn from then; OFFICIAL picks wait
    # until 20+ teams have PICKS_MIN_GP games. In between every game is
    # projected and graded but marked burnIn, with no picks.
    "MIN_GP": 5,
    "PICKS_MIN_GP": 10,
    # Projection margin = wNET x (home net rating - away net rating) x pace + HCA
    # (one tuned weight; offence and defence count equally). Clean 2025-26
    # backfill: same accuracy as the Apr 3 formula, better record in both
    # models (Full Season -6.7u -> +15.9u, KalmanOff -1.1u -> +13.6u).
    "MARGIN_FORMULA": "dnet",
    # NBA Full KalmanOff: the Kalman filter still tracks every team (kalmanAdj
    # is saved on each game) but projections don't use it.
    "KALMAN_SCALE": 0.0,
    "USE_SAFE_FUZZY": False,
    "USE_COLLAPSE_ABBR": False,
}

# --- Self-tune HCA clamps (used by core/self_tune.py) -------------------------
HCA_CLAMP_LO  = 0.0
HCA_CLAMP_HI  = 3.5
HCA_VAR_FLOOR = 0.2

# --- Kalman filter defaults (used by core/kalman_state.py) ---------------------
KALMAN_DEFAULTS = {
    "initialVar":  16,
    "gameNoise":   144,
    "dailyDrift":  0.15,
    "minVar":      2.0,
    "maxVar":      30,
}
