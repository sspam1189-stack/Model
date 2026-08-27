# pyNFL/scripts/defaults.py
# NFL-specific constants: weights, hyperparameters, team list, thresholds.
#
# KEY DIFFERENCES FROM NBA:
#   - NFL uses ridge regression on EPA features, not weighted stat deltas
#   - Weight keys map to EPA-based features (wPassOff, wRushOff, etc.)
#   - Home field advantage ~2.5 pts (NFL average, declining in recent years)
#   - Higher variance everywhere: 17-game season, weekly cadence, injury regime changes
#   - Burn-in is 1 week (backfill provides trained starting point)
#
# The ridge regression (backfill) provides initial coefficients. In-season,
# core/self_tune.py refines these via Bayesian posterior updates.

# ---------------------------------------------------------------------------
# Team stat template
# ---------------------------------------------------------------------------
# Default structure for a team's aggregated stats. Populated by nfl_stats.py
# from play-by-play data with exponential decay weighting.

DEFAULT_STATS = {
    "GP": 0,                # Games played
    "passOffEPA": 0.0,      # Offensive passing EPA/play
    "rushOffEPA": 0.0,      # Offensive rushing EPA/play
    "passDefEPA": 0.0,      # Defensive passing EPA/play allowed
    "rushDefEPA": 0.0,      # Defensive rushing EPA/play allowed
    "successRateOff": 0.0,  # Offensive success rate
    "successRateDef": 0.0,  # Defensive success rate
    "cpoe": 0.0,            # Completion % Over Expected
    "adot": 0.0,            # Average depth of target
    "pressureRateOff": 0.0, # Pressure rate allowed (O-line)
    "pressureRateDef": 0.0, # Pressure rate generated (defense)
    "qbEPA": 0.0,           # QB EPA per dropback
    "rzTDOff": 0.0,         # Red zone TD% (offense)
    "rzTDDef": 0.0,         # Red zone TD% allowed (defense)
    "thirdDownOff": 0.0,    # 3rd down conversion rate (offense)
    "thirdDownDef": 0.0,    # 3rd down conversion rate (defense)
    "pace": 63.0,           # Plays per game (NFL average ~63)
    "toMargin": 0.0,        # Turnover margin per game
}


# ---------------------------------------------------------------------------
# Initial model weights (means)
# ---------------------------------------------------------------------------
# Starting points for ridge coefficients. Backfill ridge regression overwrites
# these with empirically trained values. In-season, self_tune.py adjusts via
# Bayesian updates.
#
# Sign convention: positive weight -> higher feature value = more points
# for the team being projected.

DEFAULT_W = {
    # -- Ridge regression feature weights --
    "wPassOff": 3.5,        # Offensive passing EPA (highest signal in NFL)
    "wRushOff": 1.5,        # Offensive rushing EPA
    "wPassDef": -3.0,       # Defensive pass EPA allowed (negative = good D helps)
    "wRushDef": -1.2,       # Defensive rush EPA allowed
    "wPace": 0.3,           # Pace interaction (plays/game deviation from mean)
    "wRZ": 1.0,             # Red zone efficiency differential
    "hfa": 2.5,             # Home field advantage (~2.5 pts NFL average)

    # -- Supplementary feature weights --
    "wCPOE": 0.8,           # CPOE differential
    "wPressure": -0.5,      # Pressure rate differential (negative = pressure hurts)
    "wSuccessRate": 1.5,    # Success rate differential
    "wQBEPA": 2.0,          # QB EPA/dropback differential
    "wTurnover": 0.8,       # Turnover margin differential

    # -- Interaction / matchup feature weights --
    "wPassMatchup": 1.0,    # passOffEPA * passDefEPA interaction
    "wRushMatchup": 0.5,    # rushOffEPA * rushDefEPA interaction
    "wPaceXEPA": 0.3,       # pace_diff * total_epa_diff interaction
    "wThirdDown": 1.0,      # Third-down conversion differential

    # -- Additive adjustments --
    "constant": 0.0,        # Additive constant to base projection

    # -- Legacy pick thresholds (fallback / display reference) --
    "sprHigh": 3.0,         # Min sDiff for a spread pick (NFL lines tighter than NBA)
    "ouHigh": 4.0,          # Min |tDiff| for a total pick
    "sprEliteBump": 2.0,    # Extra sDiff above sprHigh for "elite"
    "ouEliteBump": 2.0,     # Extra |tDiff| above ouHigh for "elite"

    # -- Bayesian probability thresholds --
    "probHigh": 0.58,       # Min P(cover) for "high" pick (calibrated for t-dist + variance fix)
    "probElite": 0.64,      # Min P(cover) for "elite" confidence (lock)
    "probOUHigh": 0.62,     # Totals: min P(over/under) for "high" (higher bar — totals are noisier)
    "probOUElite": 0.68,    # Totals: min P(over/under) for "elite"
}


# ---------------------------------------------------------------------------
# Initial weight variances
# ---------------------------------------------------------------------------
# Bayesian uncertainty for each weight. Large = model is uncertain.
# Shrinks via self_tune as games are processed.

DEFAULT_W_VAR = {
    "wPassOff": 6.0,        # Wide prior — fewer NFL data points than NBA
    "wRushOff": 6.0,
    "wPassDef": 6.0,
    "wRushDef": 6.0,
    "wPace": 4.0,
    "wRZ": 5.0,
    "hfa": 2.0,             # HFA is well-studied, less uncertainty
    "wCPOE": 5.0,
    "wPressure": 5.0,
    "wSuccessRate": 5.0,
    "wQBEPA": 5.0,
    "wTurnover": 5.0,
    "wPassMatchup": 5.0,
    "wRushMatchup": 5.0,
    "wPaceXEPA": 4.0,
    "wThirdDown": 5.0,
    "constant": 4.0,
}


# ---------------------------------------------------------------------------
# Bayesian hyperparameters
# ---------------------------------------------------------------------------

BAYES_HYPER = {
    # Observation noise for margin regression (points^2).
    # NFL margins have ~14 pt std dev -> ~196 variance.
    "marginNoise": 196,

    # Observation noise for total regression (points^2).
    "totalNoise": 225,

    # Minimum variance floor — prevent overconfidence
    "minWeightVar": 0.05,

    # Maximum variance cap — prevent reset to useless
    "maxWeightVar": 12,

    # Residual game variance (points^2) — irreducible noise.
    # NFL is noisier than NBA. Start below full margin variance since
    # the model explains some of it. Updated dynamically by self_tune.
    "residualVar": 180,
}


# ---------------------------------------------------------------------------
# Kalman filter defaults
# ---------------------------------------------------------------------------
# NFL-specific: higher process noise than NBA due to weekly cadence,
# injury regime changes, and smaller sample sizes.

KALMAN_DEFAULTS = {
    "initialVar": 36,       # ~6 pt std dev initially (NBA uses 16)
    "gameNoise": 225,       # ~15 pt std dev per game
    "dailyDrift": 0.5,      # Higher drift: weekly regime changes from injuries
    "minVar": 4.0,          # Don't collapse too far (small sample)
    "maxVar": 50,           # Wide uncertainty allowed early season
}


# ---------------------------------------------------------------------------
# HCA clamp range for self_tune
# ---------------------------------------------------------------------------

HCA_CLAMP_LO = 1.0
HCA_CLAMP_HI = 4.0
HCA_VAR_FLOOR = 0.3


# ---------------------------------------------------------------------------
# Situational adjustments (empirically validated vs closing lines)
# ---------------------------------------------------------------------------

# Backup-QB totals edge: 2023-2025, games with an unexpected starting
# QB went OVER the closing total by +2.8 pts on average (t=2.05, n=104,
# blind-over 60-43 (58.3%) +12.7u, positive each season) — books
# over-discount backup QBs on totals. See sources/qb_starts.py.
#
# BACKUP_QB_TOTAL_ADJ nudges projTotal for display; the pick itself is
# SITUATIONAL: flagged games take OVER directly at the empirical cover
# rate (routing the signal through the no-edge projection diluted it —
# verified in backfill).
BACKUP_QB_TOTAL_ADJ = 2.5
BACKUP_QB_OVER_RATE = 0.57   # shrunk from 58.3% observed


# ---------------------------------------------------------------------------
# Engine configuration
# ---------------------------------------------------------------------------

BURN_IN_WEEKS = 1           # Weeks before model starts making picks
MIN_GP = 0                  # No minimum — project from Week 1

# sDiff thresholds for confidence tiers (calibrated by backfill)
SDIFF_THRESHOLDS = {
    "elite": 5.0,           # Very high edge — strong pick
    "high": 3.0,            # Clear edge — standard pick
    "medium": 1.5,          # Marginal edge — lean
    "low": 0.0,             # Below threshold — PASS
}

# Ridge regression default alpha (overridden by CV during backfill)
DEFAULT_RIDGE_ALPHA = 1.0

ENGINE_CONFIG = {
    "TEAM_NAME_ALIASES": {},    # Populated by _build_alias_map() below
    "MIN_GP": MIN_GP,
    "USE_SAFE_FUZZY": True,     # NFL team names vary across sources
    "USE_COLLAPSE_ABBR": True,  # Handle "N.Y. Giants" vs "NYG" etc.
}


# ---------------------------------------------------------------------------
# NFL Teams (32 teams with common aliases)
# ---------------------------------------------------------------------------
# Keys are canonical names; values are lists of common aliases.

NFL_TEAMS = {
    # AFC East
    "Buffalo Bills": ["BUF", "buffalo", "bills"],
    "Miami Dolphins": ["MIA", "miami", "dolphins"],
    "New England Patriots": ["NE", "new england", "patriots", "pats"],
    "New York Jets": ["NYJ", "ny jets", "jets"],

    # AFC North
    "Baltimore Ravens": ["BAL", "baltimore", "ravens"],
    "Cincinnati Bengals": ["CIN", "cincinnati", "bengals"],
    "Cleveland Browns": ["CLE", "cleveland", "browns"],
    "Pittsburgh Steelers": ["PIT", "pittsburgh", "steelers"],

    # AFC South
    "Houston Texans": ["HOU", "houston", "texans"],
    "Indianapolis Colts": ["IND", "indianapolis", "colts"],
    "Jacksonville Jaguars": ["JAX", "jacksonville", "jaguars", "jags"],
    "Tennessee Titans": ["TEN", "tennessee", "titans"],

    # AFC West
    "Denver Broncos": ["DEN", "denver", "broncos"],
    "Kansas City Chiefs": ["KC", "kansas city", "chiefs"],
    "Las Vegas Raiders": ["LV", "las vegas", "raiders", "oakland raiders", "OAK"],
    "Los Angeles Chargers": ["LAC", "la chargers", "chargers", "san diego chargers"],

    # NFC East
    "Dallas Cowboys": ["DAL", "dallas", "cowboys"],
    "New York Giants": ["NYG", "ny giants", "giants"],
    "Philadelphia Eagles": ["PHI", "philadelphia", "eagles"],
    "Washington Commanders": ["WAS", "washington", "commanders",
                              "washington football team", "redskins"],

    # NFC North
    "Chicago Bears": ["CHI", "chicago", "bears"],
    "Detroit Lions": ["DET", "detroit", "lions"],
    "Green Bay Packers": ["GB", "green bay", "packers"],
    "Minnesota Vikings": ["MIN", "minnesota", "vikings"],

    # NFC South
    "Atlanta Falcons": ["ATL", "atlanta", "falcons"],
    "Carolina Panthers": ["CAR", "carolina", "panthers"],
    "New Orleans Saints": ["NO", "new orleans", "saints"],
    "Tampa Bay Buccaneers": ["TB", "tampa bay", "buccaneers", "bucs", "tampa"],

    # NFC West
    "Arizona Cardinals": ["ARI", "arizona", "cardinals"],
    "Los Angeles Rams": ["LAR", "la rams", "rams", "st louis rams", "STL"],
    "San Francisco 49ers": ["SF", "san francisco", "49ers", "niners"],
    "Seattle Seahawks": ["SEA", "seattle", "seahawks"],
}


def _build_alias_map():
    """Build normalized alias -> canonical name mapping for ENGINE_CONFIG."""
    aliases = {}
    for canonical, alias_list in NFL_TEAMS.items():
        aliases[canonical.lower()] = canonical
        for alias in alias_list:
            aliases[alias.lower()] = canonical
    return aliases


ENGINE_CONFIG["TEAM_NAME_ALIASES"] = _build_alias_map()


# ---------------------------------------------------------------------------
# Logistic Regression confirmation model
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Feature column order for ridge regression
# ---------------------------------------------------------------------------
# MUST match the order used in model_engine.build_feature_vector().

FEATURE_COLUMNS = [
    "passOffEPA_diff",      # Team A pass off EPA - Team B pass def EPA
    "rushOffEPA_diff",      # Team A rush off EPA - Team B rush def EPA
    "passDefEPA_diff",      # Team A pass def EPA - Team B pass off EPA
    "rushDefEPA_diff",      # Team A rush def EPA - Team B rush off EPA
    "pace_diff",            # Pace differential
    "rz_diff",              # Red zone efficiency differential
    "home_flag",            # 1 if team A is home, 0 otherwise
    "cpoe_diff",            # CPOE differential
    "pressure_diff",        # Pressure rate differential
    "success_rate_diff",    # Success rate differential
    "qb_epa_diff",          # QB EPA/dropback differential
    "turnover_diff",        # Turnover margin differential
    "injury_delta_a",       # Injury adjustment for team A
    "injury_delta_b",       # Injury adjustment for team B
    "pass_matchup",         # passOffEPA * passDefEPA interaction
    "rush_matchup",         # rushOffEPA * rushDefEPA interaction
    "pace_x_epa",           # pace_diff * total EPA diff interaction
    "third_down_diff",      # Third-down conversion differential
]
