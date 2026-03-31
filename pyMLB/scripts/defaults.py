# pyMLB/scripts/defaults.py
# MLB-specific constants: weights, hyperparameters, team list, thresholds.
#
# KEY DIFFERENCES FROM NBA/NFL:
#   - MLB uses pitcher-driven model with FIP/xFIP/SIERA as primary signals
#   - Lineup vs. handedness splits (wRC_vs_L, wRC_vs_R) are first-class features
#   - Home field advantage ~0.1 runs (smaller than NBA/NFL, less variance)
#   - Bullpen workload (IP last 3 days) is a live fatigue signal
#   - Park factors and weather (temperature, wind) have meaningful run impact
#   - Burn-in is measured in days (MLB plays daily, 162-game season)
#
# The ridge regression (backfill) provides initial coefficients. In-season,
# core/self_tune.py refines these via Bayesian posterior updates.


# ---------------------------------------------------------------------------
# Team stat template
# ---------------------------------------------------------------------------
# Default structure for a team's aggregated stats. Populated by mlb_stats.py
# from game-level data with exponential decay weighting.

DEFAULT_STATS = {
    "GP": 0,                    # Games played
    "wRC_plus": 100,            # Weighted runs created+ (100 = league average)
    "xwOBA": 0.320,             # Expected weighted on-base average
    "ISO": 0.150,               # Isolated power (SLG - AVG)
    "K_pct": 0.220,             # Strikeout percentage
    "BB_pct": 0.080,            # Walk percentage
    "wRC_vs_L": 100,            # wRC+ vs left-handed pitching
    "wRC_vs_R": 100,            # wRC+ vs right-handed pitching
    "team_FIP": 4.00,           # Team starter FIP (fielding independent pitching)
    "team_xFIP": 4.00,          # Team starter xFIP (expected FIP)
    "team_SIERA": 4.00,         # Team starter SIERA (skill-interactive ERA)
    "team_K_BB_pct": 0.100,     # Starter K-BB% (strikeout minus walk rate)
    "bullpen_FIP": 4.00,        # Bullpen FIP
    "bullpen_K_BB_pct": 0.100,  # Bullpen K-BB%
    "bullpen_IP_last3": 0.0,    # Bullpen innings pitched last 3 days (fatigue)
}


# ---------------------------------------------------------------------------
# Feature column order for ridge regression
# ---------------------------------------------------------------------------
# MUST match the order used in model_engine.build_feature_vector().

FEATURE_COLUMNS = [
    "starter_fip_diff",     # Team A starter FIP - Team B starter FIP
    "starter_kbb_diff",     # Team A starter K-BB% - Team B starter K-BB%
    "starter_xfip_diff",    # Team A starter xFIP - Team B starter xFIP
    "starter_siera_diff",   # Team A starter SIERA - Team B starter SIERA
    "starter_handedness",   # Encoded handedness matchup (L/R vs lineup)
    "bullpen_fip_diff",     # Bullpen FIP differential
    "bullpen_workload",     # Bullpen IP last 3 days (fatigue signal)
    "bullpen_kbb_diff",     # Bullpen K-BB% differential
    "wrc_plus_diff",        # wRC+ differential (overall lineup quality)
    "xwoba_diff",           # xwOBA differential
    "iso_diff",             # ISO differential (power)
    "kbb_bat_diff",         # Batter K-BB% differential
    "wrc_vs_hand_diff",     # wRC+ vs pitcher handedness differential
    "home_flag",            # 1 if team A is home, 0 otherwise
    "park_factor",          # Park run factor (normalized, 1.0 = average)
    "temperature_adj",      # Temperature adjustment (cold suppresses offense)
    "wind_adj",             # Wind speed/direction adjustment
    "starter_x_lineup",     # Interaction: starter quality * opposing lineup
]


# ---------------------------------------------------------------------------
# Initial model weights (means)
# ---------------------------------------------------------------------------
# Starting points for ridge coefficients. Backfill ridge regression overwrites
# these with empirically trained values. In-season, self_tune.py adjusts via
# Bayesian updates.
#
# Sign convention: positive weight -> higher feature value = more runs
# for the team being projected.

DEFAULT_W = {
    # -- Pitcher feature weights --
    "wStarterFIP": -2.5,        # Lower FIP = better starter = fewer runs allowed
    "wStarterKBB": 1.5,         # Higher K-BB% = better command = more runs suppressed
    "wStarterXFIP": -1.5,       # xFIP reinforces FIP signal
    "wStarterSIERA": -1.0,      # SIERA adds context beyond FIP
    "wStarterHand": 0.3,        # Handedness matchup edge

    # -- Bullpen feature weights --
    "wBullpenFIP": -1.5,        # Better bullpen = fewer late-game runs
    "wBullpenWorkload": -0.3,   # Fatigued bullpen = more runs allowed
    "wBullpenKBB": 0.8,         # Bullpen command differential

    # -- Lineup feature weights --
    "wWRCPlus": 2.0,            # wRC+ is the primary lineup quality signal
    "wXWOBA": 1.5,              # xwOBA adds expected contact quality
    "wISO": 0.8,                # ISO captures power/extra bases
    "wKBBBat": 0.5,             # Batter K-BB% (plate discipline)
    "wWRCvsHand": 1.0,          # Platoon advantage vs pitcher handedness

    # -- Context adjustments --
    "hfa": 0.5,                 # Home field advantage (~0.1-0.15 runs per game, ~0.5 run differential)
    "wParkFactor": 1.0,         # Park run factor effect
    "wTempAdj": 0.05,           # Cold weather run suppression per degree below 72°F
    "wWindAdj": 0.1,            # Wind effect (positive = blowing out)
    "wStarterXLineup": 0.5,     # Interaction: starter_fip * opposing wRC+

    # -- Additive constant --
    "constant": 0.0,            # Additive constant to base projection

    # -- Legacy pick thresholds (fallback / display reference) --
    "probHigh": 0.57,           # Min P(cover) for "high" pick
    "probElite": 0.63,          # Min P(cover) for "elite" confidence
    "probOUHigh": 0.58,         # Totals: min P(over/under) for "high"
    "probOUElite": 0.65,        # Totals: min P(over/under) for "elite"
    "sprHigh": 1.5,             # Min sDiff for a spread pick (runs)
    "ouHigh": 2.0,              # Min |tDiff| for a total pick (runs)
    "sprEliteBump": 1.0,        # Extra sDiff above sprHigh for "elite"
    "ouEliteBump": 1.0,         # Extra |tDiff| above ouHigh for "elite"
}


# ---------------------------------------------------------------------------
# Self-tune signal key lists
# ---------------------------------------------------------------------------
# Parallel lists mapping weight keys to feature delta keys for self_tune.py.
# Order MUST match FEATURE_COLUMNS exactly.

_WEIGHT_KEYS = [
    "wStarterFIP", "wStarterKBB", "wStarterXFIP", "wStarterSIERA", "wStarterHand",
    "wBullpenFIP", "wBullpenWorkload", "wBullpenKBB",
    "wWRCPlus", "wXWOBA", "wISO", "wKBBBat", "wWRCvsHand",
    "hfa", "wParkFactor", "wTempAdj", "wWindAdj", "wStarterXLineup",
]

_FEATURE_KEYS = [
    "dStarterFIP", "dStarterKBB", "dStarterXFIP", "dStarterSIERA", "dStarterHand",
    "dBullpenFIP", "dBullpenWorkload", "dBullpenKBB",
    "dWRCPlus", "dXWOBA", "dISO", "dKBBBat", "dWRCvsHand",
    "hca", "dParkFactor", "dTempAdj", "dWindAdj", "dStarterXLineup",
]


# ---------------------------------------------------------------------------
# Initial weight variances
# ---------------------------------------------------------------------------
# Bayesian uncertainty for each weight. Large = model is uncertain.
# Shrinks via self_tune as games are processed.

DEFAULT_W_VAR = {
    "wStarterFIP": 4.0,         # Moderate prior — FIP is well-validated signal
    "wStarterKBB": 4.0,
    "wStarterXFIP": 4.0,
    "wStarterSIERA": 4.0,
    "wStarterHand": 3.0,        # Handedness splits have tighter prior
    "wBullpenFIP": 4.0,
    "wBullpenWorkload": 3.0,    # Workload effect is fairly stable
    "wBullpenKBB": 4.0,
    "wWRCPlus": 4.0,
    "wXWOBA": 4.0,
    "wISO": 4.0,
    "wKBBBat": 3.0,
    "wWRCvsHand": 4.0,
    "hfa": 1.5,                 # HFA well-studied, less uncertainty
    "wParkFactor": 2.0,         # Park factors are publicly available, moderate prior
    "wTempAdj": 3.0,
    "wWindAdj": 3.0,
    "wStarterXLineup": 4.0,
    "constant": 3.0,
}


# ---------------------------------------------------------------------------
# Bayesian hyperparameters
# ---------------------------------------------------------------------------

BAYES_HYPER = {
    # Observation noise for run differential regression (runs^2).
    # MLB run differentials have ~3.5 run std dev -> ~12 variance.
    "marginNoise": 12,

    # Observation noise for total runs regression (runs^2).
    # Total runs have ~3.0 run std dev -> ~9 variance.
    "totalNoise": 9,

    # Minimum variance floor — prevent overconfidence
    "minWeightVar": 0.05,

    # Maximum variance cap — prevent reset to useless
    "maxWeightVar": 8,

    # Residual game variance (runs^2) — irreducible noise.
    # MLB is high-variance even with perfect info. Updated dynamically by self_tune.
    "residualVar": 10,
}


# ---------------------------------------------------------------------------
# Kalman filter defaults
# ---------------------------------------------------------------------------
# MLB-specific: moderate process noise, daily cadence means faster adaptation
# than NFL (weekly) but more data than NBA (daily).

KALMAN_DEFAULTS = {
    "initialVar": 16,       # ~4 run std dev initially
    "gameNoise": 12,        # ~3.5 run std dev per game
    "dailyDrift": 0.15,     # Daily drift for pitcher rotation/injury changes
    "minVar": 1.0,          # Don't collapse too far (small sample per pitcher)
    "maxVar": 25,           # Wide uncertainty allowed early season
}


# ---------------------------------------------------------------------------
# HCA clamp range for self_tune
# ---------------------------------------------------------------------------

HCA_CLAMP_LO = 0.0
HCA_CLAMP_HI = 1.5
HCA_VAR_FLOOR = 0.1


# ---------------------------------------------------------------------------
# sDiff thresholds for confidence tiers
# ---------------------------------------------------------------------------

SDIFF_THRESHOLDS = {
    "elite": 2.5,           # Very high edge — strong pick (runs)
    "high": 1.5,            # Clear edge — standard pick
    "medium": 0.75,         # Marginal edge — lean
    "low": 0.0,             # Below threshold — PASS
}


# ---------------------------------------------------------------------------
# Engine configuration
# ---------------------------------------------------------------------------

BURN_IN_DAYS = 10           # Days before model starts making picks (enough for rotation cycle)
MIN_GP = 0                  # No minimum — project from Day 1 with priors

# Ridge regression default alpha (overridden by CV during backfill)
DEFAULT_RIDGE_ALPHA = 1.0

ENGINE_CONFIG = {
    "TEAM_NAME_ALIASES": {},    # Populated by _build_alias_map() below
    "MIN_GP": MIN_GP,
    "USE_SAFE_FUZZY": True,     # MLB team names vary across sources
    "USE_COLLAPSE_ABBR": True,  # Handle "N.Y. Yankees" vs "NYY" etc.
}


# ---------------------------------------------------------------------------
# Domed stadiums — weather adjustments are suppressed for these
# ---------------------------------------------------------------------------

DOMED_STADIUMS = {
    "Toronto Blue Jays",        # Rogers Centre (retractable)
    "Tampa Bay Rays",           # Tropicana Field
    "Houston Astros",           # Minute Maid Park (retractable)
    "Arizona Diamondbacks",     # Chase Field (retractable)
    "Seattle Mariners",         # T-Mobile Park (retractable)
    "Milwaukee Brewers",        # American Family Field (retractable)
    "Miami Marlins",            # loanDepot park (retractable)
    "Minnesota Twins",          # Target Field (open-air, but listed for reference)
}


# ---------------------------------------------------------------------------
# MLB Teams (30 teams with common aliases)
# ---------------------------------------------------------------------------
# Keys are canonical names; values are lists of common aliases.

MLB_TEAMS = {
    # AL East
    "Baltimore Orioles": ["BAL", "baltimore", "orioles", "o's"],
    "Boston Red Sox": ["BOS", "boston", "red sox", "sox"],
    "New York Yankees": ["NYY", "ny yankees", "yankees", "new york yankees"],
    "Tampa Bay Rays": ["TB", "TBR", "tbr", "tampa bay", "rays", "tampa"],
    "Toronto Blue Jays": ["TOR", "toronto", "blue jays", "jays"],

    # AL Central
    "Chicago White Sox": ["CWS", "chw", "chicago white sox", "white sox"],
    "Cleveland Guardians": ["CLE", "cleveland", "guardians", "cleveland indians"],
    "Detroit Tigers": ["DET", "detroit", "tigers"],
    "Kansas City Royals": ["KC", "KCR", "kcr", "kc", "kansas city", "royals"],
    "Minnesota Twins": ["MIN", "minnesota", "twins"],

    # AL West
    "Houston Astros": ["HOU", "houston", "astros"],
    "Los Angeles Angels": ["LAA", "la angels", "angels", "anaheim angels", "california angels"],
    "Oakland Athletics": ["OAK", "ATH", "ath", "oak", "oakland", "athletics", "a's", "las vegas athletics", "sacramento athletics"],
    "Seattle Mariners": ["SEA", "seattle", "mariners", "m's"],
    "Texas Rangers": ["TEX", "texas", "rangers"],

    # NL East
    "Atlanta Braves": ["ATL", "atlanta", "braves"],
    "Miami Marlins": ["MIA", "miami", "marlins", "florida marlins"],
    "New York Mets": ["NYM", "ny mets", "mets", "new york mets"],
    "Philadelphia Phillies": ["PHI", "philadelphia", "phillies"],
    "Washington Nationals": ["WSH", "WSN", "wsn", "was", "washington", "nationals", "nats"],

    # NL Central
    "Chicago Cubs": ["CHC", "chc", "chicago cubs", "cubs"],
    "Cincinnati Reds": ["CIN", "cincinnati", "reds"],
    "Milwaukee Brewers": ["MIL", "milwaukee", "brewers"],
    "Pittsburgh Pirates": ["PIT", "pittsburgh", "pirates", "bucs"],
    "St. Louis Cardinals": ["STL", "st louis", "st. louis", "cardinals", "cards"],

    # NL West
    "Arizona Diamondbacks": ["ARI", "arizona", "diamondbacks", "dbacks", "d-backs"],
    "Colorado Rockies": ["COL", "colorado", "rockies"],
    "Los Angeles Dodgers": ["LAD", "la dodgers", "dodgers", "los angeles dodgers"],
    "San Diego Padres": ["SD", "sdp", "san diego", "padres", "friars"],
    "San Francisco Giants": ["SF", "sfg", "san francisco", "giants"],
}


def _build_alias_map():
    """Build normalized alias -> canonical name mapping for ENGINE_CONFIG."""
    aliases = {}
    for canonical, alias_list in MLB_TEAMS.items():
        aliases[canonical.lower()] = canonical
        for alias in alias_list:
            aliases[alias.lower()] = canonical
    return aliases


ENGINE_CONFIG["TEAM_NAME_ALIASES"] = _build_alias_map()
