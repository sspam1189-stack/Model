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

# Minimum IP per start to count in rolling window
MIN_INNINGS = 3.0

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
    # 2026-05-12 4D sweep at kalmanBlend=0.0 / cap=23 / threshold=0.72:
    # VAR=1.15 marginally best across the BF=0.95 row (Lean U 70.0% / +24.3%
    # ROI; combined +30.5% season ROI). Wider variance tightens pCover so
    # the threshold filters more aggressively. Earlier tuning (2026-05-05)
    # picked VAR=1.0 under the old blended Kalman; with BLEND=0 the rate
    # model carries the full projection and benefits from slightly wider std.
    "strikeouts":   1.15,
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

# Projected batters-faced multiplier — calibrates the rate × min/IP-derived
# BF estimate. <1 trims for blowouts/short outings/pulled starts; >1 inflates.
# 2026-05-13 retune (props_engine BF formula now uses real `bf` field instead
# of outs+h+bb+1 reconstruction). Under clean formula + per-pitcher K cap,
# MULT=1.05 optimal: 128-34 (79.0%) +173.56u, elite +58.14u vs current +40.24u.
# Old formula under-counted ppbf (3.71 vs real 3.92), and MULT=0.95 was
# empirically compensating. With clean inputs, MULT needs to come up to 1.05
# to restore the optimal effective BF projection.
BF_MULT = 1.05

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
#     so picks-only std runs hot). Bumped K to 2.1 as a midpoint estimate;
#     runtime calibration in run_daily.py refines further once 50+ graded
#     entries land per market.
DEFAULT_EMPIRICAL_STD = {
    "strikeouts":   2.1,
    "outs":         2.4,
    "hits_allowed": 1.9,
}

# Minimum graded sample required before runtime-calibrated empirical std
# overrides DEFAULT_EMPIRICAL_STD. Below this, use the default (avoid
# noisy std estimates from sub-50 graded entries).
EMPIRICAL_STD_MIN_SAMPLE = 50

# Season anchor: weight on season-to-date K% (vs. rolling-window K%) when
# computing pitcher_k_rate.  Sweep (scripts.sweep_season_anchor) on 2026
# walk-forward backfill picked W=1.00 monotonically — recent K% is already
# captured by the 50/50 Kalman blend, so re-injecting it here just doubles
# the recent-form weight and adds noise.
SEASON_ANCHOR_WEIGHT = {
    "strikeouts":   1.00,
}

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
