"""
defaults.py — Constants and configuration for the MLB pitcher strikeouts model.

Defines rolling-window parameters, market thresholds, variance multipliers,
edge filters, opponent adjustment weights, rest penalties, API market maps,
stat key maps, team abbreviations, and season helpers.

Market:
  strikeouts — pitcher strikeouts (K-only model)
"""

import datetime

# ---------------------------------------------------------------------------
# Student's t degrees of freedom (heavier tails than normal)
# ---------------------------------------------------------------------------
PROP_T_DF = 5

# ---------------------------------------------------------------------------
# Rolling window — pitchers start every 5 days, so 5-7 recent starts
# ---------------------------------------------------------------------------
ROLLING_WINDOW = 7
DECAY_FACTOR = 0.90

# ---------------------------------------------------------------------------
# Minimum starts to qualify
# ---------------------------------------------------------------------------
MIN_GAMES = {
    "strikeouts":   2,
}

# Minimum sample to count a start in the rolling window.
# 2026-05-20: switched from IP-based (was MIN_INNINGS=3.0) to pitch-count-based.
# Rationale: a pitcher who gets shelled and pulled after 2 IP is still REAL
# data about that pitcher's current form — excluding short outings only
# filters out his floor and biases rolling K-rate upward. The only outings
# that genuinely shouldn't count are bullpen-day/opener cameos where the
# pitcher's role was reliever, not starter. Natural threshold gap at ~25-30
# pitches: below 25 = planned opener cameo, 30+ = real start (even if brief).
# Keep MIN_INNINGS=3.0 as legacy fallback if pitches data is missing.
MIN_INNINGS = 3.0
MIN_PITCHES = 30

# ---------------------------------------------------------------------------
# Market thresholds — minimum pCover to make a pick
# The model's empirical std and rate-based projections handle calibration,
# so thresholds are set to minimum viable (just above coin flip).
# ---------------------------------------------------------------------------
MARKET_THRESHOLDS = {
    # 2026-05-12 4D sweep at BF=0.95 / VAR=1.15 / kalmanBlend=0.0 / cap=23
    # picked threshold 0.72 as optimal: 157 picks, 77.1% WR, +38.1% ROI (was
    # 0.70 → 196 picks, 74.0% WR, +32.4% ROI under same config). The new
    # config compresses the pCover distribution; 0.70 leaves quality picks
    # in the lean tier. 0.65-0.72 becomes the lean band (both sides).
    "strikeouts":   {"high": 0.72},
}

# ---------------------------------------------------------------------------
# Variance multipliers (how noisy each stat is game-to-game)
# ---------------------------------------------------------------------------
VAR_MULT = {
    # 2026-05-14 3D sweep (BF x VAR x CAP, scripts.sweep_3d_sized) at 2.5u
    # picks / 1u leans sizing with Kalman var off and engine producing
    # pCover>=0.50 (picks at >=0.72, leans 0.65-0.72): optimal cell BF=1.00
    # VAR=1.15 CAP=23 delivers +300.48u season (354 plays, 36.4% ROI) with
    # +42.43u from leans alone (66.3% WR). Beats VAR=1.20 by +5u season
    # (below 0.20u/pick bar but lean WR cleaner: 66.3% vs 65.4%).
    #
    # 2026-05-20 multi-D re-sweep at ZC_CHASE=0.7 (new ZC/chase regression
    # blend, see line ~197). New optimum: BF=0.95, VAR=1.10. Total at 2.5u
    # picks / 1.5u leans sizing: +381.4u (vs +336.6u baseline = +44.8u).
    # Pick WR rises from 80.1% to 76.6% with picks 196 -> 244 — more action
    # at slightly lower per-pick edge, but at 2.5/1.5 sizing the lean tier
    # (215 at 67.9%, +59.7u) makes up the difference.
    "strikeouts":   1.10,
}

# ---------------------------------------------------------------------------
# Edge filters
# ---------------------------------------------------------------------------
MIN_EDGE = {
    "strikeouts":   0.0,
}

EDGE_DEAD_ZONE = {}

MAX_EDGE = {
    "strikeouts":   999,
}

# ---------------------------------------------------------------------------
# Min prop line
# ---------------------------------------------------------------------------
MIN_LINE = {
    "strikeouts":   0,
}

# ---------------------------------------------------------------------------
# Disabled / direction-restricted markets
# ---------------------------------------------------------------------------
DISABLED_MARKETS = set()
UNDER_ONLY_MARKETS = set()

# ---------------------------------------------------------------------------
# Projection blend weights
# ---------------------------------------------------------------------------

# Pitcher handedness splits: blend weight between rolling-45d (recent form)
# and season-to-date (stable, larger sample).
# 1.0 = 100% recent  |  0.0 = 100% season
#
# 2026-05-13 deep analysis (scripts.analyze_splits_value): the recent signal
# is doing almost nothing under the current config (K_RATE_CAP_FLOOR=0.36,
# kalmanBlend=0).
#   * Mean abs change in projected K between w=0.0 and w=0.3: only 0.09 K
#   * Only 0.5% of projections shift by >0.5K; 0% shift by >1K
#   * Recent panel (5/05+): IDENTICAL picks/leans across all weights — rolling
#     45d window ≈ season-to-date by mid-May, so recent ≈ season
#   * Past 2 weeks: w=0.0 actually beats w=0.3 by +4.4u
#   * Season-wide w=0.3 wins by +9u (+0.06u/pick — below 0.20u/pick bar)
# Going with 0.0 for simplicity; recent-split machinery is mostly noise.
SPLITS_BLEND_WEIGHT = 0.0

# ---------------------------------------------------------------------------
# Lineup K% aggregation method
# ---------------------------------------------------------------------------
# How to compute the opponent lineup's K rate from the 9 confirmed batters.
# Default "simple_mean" is the baseline (proven +109.8u backfill).
#
#   "simple_mean":  unweighted mean of batter_K% vs pitcher's hand. Multiplied
#                   by pitcher's hand-weighted K%, divided by league K%.
#                   Current production behavior.
#   "pa_weighted":  league-wide slot-PA-weighted mean (SLOT_PA_WEIGHTS).
#   "pa_weighted_team": per-opponent-team slot-PA-weighted (TEAM_SLOT_PA_WEIGHTS,
#                   loaded from data/pitcher_cache/mlb/slot_pa_weights_2026.json,
#                   falls back to league if team missing). Captures per-team
#                   offensive pace differences.
#
# Pairwise-hand modes were swept 2026-05-20 and discarded:
#   pairwise_hand:        -7.9u / -2.6pp WR (204 picks vs 196 baseline)
#   pairwise_pa_weighted: -9.9u / -2.6pp WR (200 picks)
# Mechanism: re-applying platoon adjustment per-batter when pitcher_K is
# already hand-weighted via PCT_LHB widens projection variance and pushes
# borderline edges over the 0.72 confidence threshold — extra picks at lower
# edge quality, not extra accuracy.
LINEUP_K_METHOD = "simple_mean"

# Per-slot share of plate appearances WITHIN A STARTER'S OUTING. Computed
# 2026-05-20 from game_logs_2026.json (1455 starter outings season-to-date):
# total team PA per starter outing ≈ 21.6, distributed by cycling 1-9 through
# the lineup. Slot 1 gets ~13.2% of starter-outing PA; slot 9 gets ~9.0%.
# Re-derive periodically: see analyze_slot_pa.py or
#   `python -c "import json; from collections import defaultdict; ..."`.
# Used by the pa_weighted lineup K method. Normalized to sum to 1.
SLOT_PA_WEIGHTS = [
    0.13177,  # slot 1: 2.85 PA/starter outing
    0.12783,  # slot 2: 2.76
    0.12341,  # slot 3: 2.67
    0.11782,  # slot 4: 2.55
    0.11115,  # slot 5: 2.40
    0.10501,  # slot 6: 2.27
    0.09923,  # slot 7: 2.15
    0.09405,  # slot 8: 2.03
    0.08973,  # slot 9: 1.94
]


# ---------------------------------------------------------------------------
# Park K factor adjustment
# ---------------------------------------------------------------------------
# Multiplier applied to expected_k_rate based on the HOME team's park.
# Values are 3-year Statcast prior × 2026 season-to-date observed (Bayesian
# blend; prior_n=5000 BF, obs_n=actual park BF this season).
#
# Mechanisms: altitude, climate (cold dense air helps breaking balls), foul
# territory, backdrop visibility, outfield dimensions, dome/glare effects.
# Magnitude: ±4% at extremes (COL/STL low, SEA/LAA/MIA high).
#
# Flip via PARK_K_ADJUSTMENT_ENABLED for A/B sweeping. Default off until
# sweep confirms backfill signal.
PARK_K_ADJUSTMENT_ENABLED = False

# ---------------------------------------------------------------------------
# Whiff% regression-to-mean blend
# ---------------------------------------------------------------------------
# Savant whiff% is per-pitch (huge sample, stable) and correlates 0.602 with
# FUTURE K% (next 3 starts) vs 0.410 for current K%. Blending pulls pitcher_k
# toward underlying whiff-implied skill — catches hot/cold pitchers before
# their K rate regresses.
#
# Default 0.0 (off). Sweep candidates: 0.3, 0.5, 0.7.
# WHIFF_TO_K_RATIO: scales whiff% to expected K% (league avg ~0.78 K per whiff).
WHIFF_BLEND_WEIGHT = 0.0
# Empirical 2026 K/whiff median across 412 pitchers (savant_rates_2026_20260519).
# Used to scale whiff% to K%-equivalent before blending into pitcher_k_rate.
WHIFF_TO_K_RATIO = 0.886

# ---------------------------------------------------------------------------
# xBA → IP adjustment (sweep candidate, default off)
# ---------------------------------------------------------------------------
# 2026-05-20 analysis: Savant xBA correlates 0.732 with hits/BF and -0.359
# with outs/start. Currently IP projection penalizes only tough opp_ops; it
# doesn't penalize pitchers who allow soft contact poorly. Adding pitcher
# xBA as a symmetric input balances the IP calc.
#
# Mechanism: high xBA pitcher → more hits allowed → pulled earlier → less BF
# → fewer K opportunities. Affects projection volume, not K rate.
#
# Sweep candidates: 0.05, 0.10, 0.15 (% IP delta per unit xBA delta).
XBA_IP_ADJUSTMENT_WEIGHT = 0.0
XBA_LEAGUE_AVG = 0.245

# ---------------------------------------------------------------------------
# Zone-contact + chase → K adjustment (sweep candidate, default off)
# ---------------------------------------------------------------------------
# 2026-05-20: zone_contact_pct correlates -0.418 with future K%; chase_pct
# correlates +0.327. Regression-style additive K-rate adjustment (properly
# scaled this time — earlier naive blend failed due to feature scale).
#
# Per-pitcher k_adj = ZC_K_SLOPE * (zone_contact - league_zc)
#                   + CHASE_K_SLOPE * (chase - league_chase)
# Then pitcher_k_rate += k_adj * ZC_CHASE_BLEND_WEIGHT.
#
# League averages from 2026 savant cache (412 pitchers).
# 2026-05-20 sweep across 19 weights (0.0 to 1.2 at 0.05-0.10 grid): peak
# at 0.65-0.70 (+180.4u to +180.9u 1u/1u, +370u at 2.5/1.5 sizing). Ships
# at 0.7. Multi-D follow-up confirmed BF=0.95 VAR=1.10 is the new full
# optimum at 2.5/1.5 sizing: +381.4u season.
#
# CAVEAT: backfill loads season-end savant_rates (mild leakage on historical
# dates — whiff/ZC/chase are stable mid-late season, ~88% accurate). Live
# forward expectation should be ~80-90% of backfill numbers.
ZC_CHASE_BLEND_WEIGHT = 0.7
ZC_LEAGUE_AVG = 0.838     # mean zone_contact across pitchers
CHASE_LEAGUE_AVG = 0.288  # mean chase rate
ZC_K_SLOPE = -0.45        # 1pp ZC up → 0.45pp K down (from corr -0.418, scaled)
CHASE_K_SLOPE = 0.35      # 1pp chase up → 0.35pp K up (from corr +0.327, scaled)
PARK_K_FACTORS = {
    'ARI': 0.984, 'ATL': 0.979, 'BAL': 1.027, 'BOS': 0.981,
    'CHC': 0.995, 'CIN': 0.997, 'CLE': 1.003, 'COL': 0.954,
    'CWS': 0.996, 'DET': 1.009, 'HOU': 1.007, 'KC':  0.980,
    'LAA': 1.032, 'LAD': 1.028, 'MIA': 1.032, 'MIL': 1.007,
    'MIN': 0.987, 'NYM': 1.007, 'NYY': 1.010, 'OAK': 1.018,
    'PHI': 1.015, 'PIT': 1.000, 'SD':  1.025, 'SEA': 1.041,
    'SF':  1.003, 'STL': 0.955, 'TB':  1.027, 'TEX': 0.997,
    'TOR': 1.008, 'WSH': 0.970,
}


def get_team_slot_weights(team_abbr=None):
    """
    Return per-team slot PA weights from the cached JSON. Falls back to the
    league-wide SLOT_PA_WEIGHTS when:
      * team_abbr is None
      * team is not in the cache (e.g. relocation/abbr mismatch)
      * cache file is missing or unreadable
    Used by the "pa_weighted_team" LINEUP_K_METHOD.
    """
    import os, json
    if team_abbr is None:
        return SLOT_PA_WEIGHTS
    _path = os.path.join(
        os.path.dirname(__file__), "..", "..", "data",
        "pitcher_cache", "mlb", "slot_pa_weights_2026.json",
    )
    try:
        with open(_path, "r") as _f:
            _cache = json.load(_f)
        entry = _cache.get(team_abbr)
        if entry and entry.get("weights"):
            return entry["weights"]
    except Exception:
        pass
    return SLOT_PA_WEIGHTS

# Projected batters-faced multiplier — calibrates the rate × min/IP-derived
# BF estimate. <1 trims for blowouts/short outings/pulled starts; >1 inflates.
# 2026-05-13 retune (props_engine BF formula now uses real `bf` field instead
# of outs+h+bb+1 reconstruction). Under clean formula + per-pitcher K cap,
# MULT=1.05 optimal: 128-34 (79.0%) +173.56u, elite +58.14u vs current +40.24u.
# Old formula under-counted ppbf (3.71 vs real 3.92), and MULT=0.95 was
# empirically compensating. With clean inputs, MULT needs to come up to 1.05
# to restore the optimal effective BF projection.
#
# 2026-05-14 6D sweep at 2u picks / 1u leans sizing post-Kalman-drop:
# BF=1.00 narrowly beats 1.05 (+10.7u season / +3.4pp season ROI) in the
# new VAR=1.20 / BL=0.90 regime. Trimmed back to 1.00 since the cleaner-
# inputs argument is captured by the BL/VAR/EMP knobs now.
#
# 2026-05-20 multi-D re-sweep at ZC_CHASE=0.7: BF=0.95 narrowly beats 1.00
# at user's 2.5u/1.5u sizing (+381.4u vs +370.1u, +11.3u edge). BF=0.95
# tightens the BF projection slightly → higher pick WR (76.6% vs 73.0%
# at BF=1.00) which compounds favorably at higher pick sizing.
BF_MULT = 0.95

# Hard ceiling on projected batters faced after BF_MULT. Acts as a league-wide
# safety net on top of the per-pitcher pitch-count ceiling (see props_engine.py
# avg_pc = min(avg_pc, max(recent_pcs))). Set high (e.g. 100.0) to effectively
# disable.
BF_CAP = 23.0

# Per-pitcher K-rate cap floor — applied as max(K_RATE_CAP_FLOOR, season K%)
# in props_engine.py. The matchup-driven expected_k_rate
# (pitcher_k × lineup_k / lg_k) is then clamped to that per-pitcher cap, so:
#   * Mid-tier pitchers (season K% < floor) can project up to the floor
#     but no further, even with elite matchups.
#   * Elite K pitchers (season K% > floor) get their proven ceiling.
# 2026-05-13 2D sweep (floor × headroom): floor=0.36 + headroom=1.00 optimal —
# 161 picks, 79.5% WR, +41.5% ROI, +175.56u (vs flat 0.36 cap which was
# 162 picks 79.0% WR +40.8% ROI).
K_RATE_CAP_FLOOR = 0.36

# Hard floor on expected_k_rate after the per-pitcher cap. Prevents
# nonsensical near-zero K-rate projections from degenerate inputs (e.g.
# missing recent starts blended into a 0% rate).
K_RATE_FLOOR = 0.05

# Weather K-rate multiplier — applied as `k_weather_mult = 1.0 + bonus/penalty`
# when game temperature crosses the cold/hot thresholds. Original 4/15 ship
# (commit 20a55c6f) used +0.02 cold / -0.01 hot on the "bat speed vs grip"
# thesis, but never had an isolated sweep. Magnitude (max ±0.12 K on a 6 K
# proj) is ~13× smaller than the model's 1.6 K MAE noise floor, so the
# effect can't be detected in WR. Set to 0 by 2026-05-14 to neutralize a
# probably-noise adjustment. Knobs remain so a future sweep can re-enable.
WEATHER_K_COLD_TEMP_F = 50      # °F threshold — at or below = "cold"
WEATHER_K_HOT_TEMP_F = 90       # °F threshold — at or above = "hot"
WEATHER_K_COLD_BONUS = 0.0      # was +0.02
WEATHER_K_HOT_PENALTY = 0.0     # was -0.01

# Empirical residual std per market — league-level "before I know this
# pitcher" anchor used when a pitcher has <= 3 starts (rolling_std unstable).
# These defaults are cold-start fallbacks; runtime calibration in
# run_daily.py overrides them when the graded sample is large enough.
#
# History:
#   * 2026-04-15 (71dde197): shipped K=1.9 from first ~14 K picks of 2026.
#   * 2026-05-14: 440 graded K picks → selection-biased residual std 2.225.
#     True population std estimate ~1.95-2.10 (picks are conviction-skewed
#     so picks-only std runs hot). Bumped K to 2.1 as a midpoint estimate.
#   * 2026-05-14 (later): 6D sweep at 2u/1u sizing picked K=1.9 over 2.1 by
#     +8u season units (recent unchanged). Reverting to 1.9 — matches the
#     original 71dde197 ship value and the residual std under the
#     no-Kalman-var / VAR=1.20 regime.
DEFAULT_EMPIRICAL_STD = {
    "strikeouts":   1.9,
    "outs":         2.4,
    "hits_allowed": 1.9,
}

# Minimum graded sample required before runtime-calibrated empirical std
# overrides DEFAULT_EMPIRICAL_STD. Below this, use the default (avoid
# noisy std estimates from sub-50 graded entries).
EMPIRICAL_STD_MIN_SAMPLE = 50

# Pitch-count slope projection — weighted linear regression on recent pitch
# counts, blended with the existing weighted-avg PC. Catches "regime change"
# patterns (e.g., a starter on a downward leash trend) that the symmetric
# weighted-avg + last_pc-bump rule misses.
#
#     wavg_pc  = existing weighted avg of game_pcs
#     pcs      = game_pcs[-PC_SLOPE_N:]  (or all starts if PC_SLOPE_N==0)
#     ws[i]    = PC_SLOPE_DECAY ** (n - 1 - i)            (older = lighter)
#     slope, intercept = weighted-LSQR fit on (xs, pcs)
#     trend_pc = slope * n + intercept                    (project 1 step fwd)
#     avg_pc   = (1 - PC_SLOPE_WEIGHT) * wavg_pc + PC_SLOPE_WEIGHT * trend_pc
#
# 2026-05-20 4×4 (N × DECAY) + WEIGHT sweep on 2026 walk-forward backfill:
#   Baseline (WEIGHT=0):       158-41 (79.4%) +107.1u  (199 picks)
#   Best (N=7, DECAY=0.7, W=0.5): 157-37 (80.9%) +112.2u  (194 picks)
#     → +5.1u season, +1.5pp WR, -1 win / -4 losses (filtering loss-prone picks).
# WEIGHT U-shape confirmed: 0.25 underperforms baseline; 0.5 peaks; 0.75/1.0
# back off slightly. N≥7 ties with N=all under DECAY=0.7 (older starts get
# weight <0.1, contribute nothing). DECAY=0.7 beats 0.5/0.9/1.0.
PC_SLOPE_WEIGHT = 0.5
PC_SLOPE_N = 7
PC_SLOPE_DECAY = 0.7

# ---------------------------------------------------------------------------
# API market maps
# ---------------------------------------------------------------------------

# The Odds API market keys
PROP_MARKETS_API = [
    "pitcher_strikeouts",
]

MARKET_MAP = {
    "pitcher_strikeouts":   "strikeouts",
}
MARKET_MAP_REV = {v: k for k, v in MARKET_MAP.items()}

# FanDuel market type mapping
FD_MARKET_TYPE_MAP = {
    "PITCHER_STRIKEOUTS":        "strikeouts",
    "TOTAL_PITCHER_STRIKEOUTS":  "strikeouts",
}

# ---------------------------------------------------------------------------
# Stat key maps
# ---------------------------------------------------------------------------
STAT_KEYS = {
    "strikeouts":   "k",
}

KALMAN_STAT_KEYS = {
    "strikeouts":   "k",
}

# ---------------------------------------------------------------------------
# MLB Stats API headers
# ---------------------------------------------------------------------------
MLB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.mlb.com/",
    "Origin": "https://www.mlb.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

# ---------------------------------------------------------------------------
# Season helpers
# ---------------------------------------------------------------------------

def current_season():
    """Auto-detect MLB season year (e.g. 2026)."""
    return datetime.datetime.now().year


def season_dates(season=None):
    """Return (start, end) dates for an MLB season."""
    year = season or current_season()
    return (f"{year}-03-20", f"{year}-10-31")


# ---------------------------------------------------------------------------
# MLB team abbreviations (full name -> standard abbreviation)
# Includes common variations: city only, mascot only, etc.
# ---------------------------------------------------------------------------
MLB_TEAM_ABBR = {
    # AL East
    "Baltimore Orioles":       "BAL",
    "Baltimore":               "BAL",
    "Orioles":                 "BAL",
    "Boston Red Sox":          "BOS",
    "Boston":                  "BOS",
    "Red Sox":                 "BOS",
    "New York Yankees":        "NYY",
    "NY Yankees":              "NYY",
    "Yankees":                 "NYY",
    "Tampa Bay Rays":          "TB",
    "Tampa Bay":               "TB",
    "Rays":                    "TB",
    "Toronto Blue Jays":       "TOR",
    "Toronto":                 "TOR",
    "Blue Jays":               "TOR",
    # AL Central
    "Chicago White Sox":       "CWS",
    "White Sox":               "CWS",
    "Cleveland Guardians":     "CLE",
    "Cleveland":               "CLE",
    "Guardians":               "CLE",
    "Detroit Tigers":          "DET",
    "Detroit":                 "DET",
    "Tigers":                  "DET",
    "Kansas City Royals":      "KC",
    "Kansas City":             "KC",
    "Royals":                  "KC",
    "Minnesota Twins":         "MIN",
    "Minnesota":               "MIN",
    "Twins":                   "MIN",
    # AL West
    "Houston Astros":          "HOU",
    "Houston":                 "HOU",
    "Astros":                  "HOU",
    "Los Angeles Angels":      "LAA",
    "LA Angels":               "LAA",
    "Angels":                  "LAA",
    "Oakland Athletics":       "OAK",
    "Oakland":                 "OAK",
    "Athletics":               "OAK",
    "A's":                     "OAK",
    "Seattle Mariners":        "SEA",
    "Seattle":                 "SEA",
    "Mariners":                "SEA",
    "Texas Rangers":           "TEX",
    "Texas":                   "TEX",
    "Rangers":                 "TEX",
    # NL East
    "Atlanta Braves":          "ATL",
    "Atlanta":                 "ATL",
    "Braves":                  "ATL",
    "Miami Marlins":           "MIA",
    "Miami":                   "MIA",
    "Marlins":                 "MIA",
    "New York Mets":           "NYM",
    "NY Mets":                 "NYM",
    "Mets":                    "NYM",
    "Philadelphia Phillies":   "PHI",
    "Philadelphia":            "PHI",
    "Phillies":                "PHI",
    "Washington Nationals":    "WSH",
    "Washington":              "WSH",
    "Nationals":               "WSH",
    # NL Central
    "Chicago Cubs":            "CHC",
    "Cubs":                    "CHC",
    "Cincinnati Reds":         "CIN",
    "Cincinnati":              "CIN",
    "Reds":                    "CIN",
    "Milwaukee Brewers":       "MIL",
    "Milwaukee":               "MIL",
    "Brewers":                 "MIL",
    "Pittsburgh Pirates":      "PIT",
    "Pittsburgh":              "PIT",
    "Pirates":                 "PIT",
    "St. Louis Cardinals":     "STL",
    "St Louis Cardinals":      "STL",
    "St. Louis":               "STL",
    "Cardinals":               "STL",
    # NL West
    "Arizona Diamondbacks":    "ARI",
    "Arizona":                 "ARI",
    "Diamondbacks":            "ARI",
    "D-backs":                 "ARI",
    "Colorado Rockies":        "COL",
    "Colorado":                "COL",
    "Rockies":                 "COL",
    "Los Angeles Dodgers":     "LAD",
    "LA Dodgers":              "LAD",
    "Dodgers":                 "LAD",
    "San Diego Padres":        "SD",
    "San Diego":               "SD",
    "Padres":                  "SD",
    "San Francisco Giants":    "SF",
    "San Francisco":           "SF",
    "Giants":                  "SF",
}
