"""
defaults.py — Constants and configuration for the MLB pitcher prop prediction model.

Defines rolling-window parameters, market thresholds, variance multipliers,
edge filters, opponent adjustment weights, rest penalties, API market maps,
stat key maps, team abbreviations, and season helpers.

Markets:
  strikeouts     — pitcher strikeouts
  outs           — pitcher outs recorded (IP x 3)
  hits_allowed   — pitcher hits allowed
  walks          — pitcher walks
  game_hits      — total game hits (both teams combined)
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
    "outs":         2,
    "hits_allowed": 2,
    "walks":        2,
    "game_hits":    5,   # needs both pitchers to have data
}

# Minimum IP per start to count in rolling window
MIN_INNINGS = 3.0

# ---------------------------------------------------------------------------
# Market thresholds (initial — will be calibrated after backtest)
# ---------------------------------------------------------------------------
MARKET_THRESHOLDS = {
    "strikeouts":   {"high": 0.75},    # 57.5% at 0.75+, +5.9u
    "outs":         {"high": 0.62, "high_under": 0.62},  # UNDER only — OVER 40% vs UNDER 53%
    "hits_allowed": {"high": 0.65, "high_under": 0.65},  # UNDER only — OVER 54% vs UNDER 60%
    "walks":        {"high": 0.80},    # disabled — no consistent edge
    "game_hits":    {"high": 0.65},
}

# ---------------------------------------------------------------------------
# Variance multipliers (how noisy each stat is game-to-game)
# ---------------------------------------------------------------------------
VAR_MULT = {
    "strikeouts":   1.2,
    "outs":         1.1,
    "hits_allowed": 1.3,
    "walks":        1.5,
    "game_hits":    1.1,
}

# ---------------------------------------------------------------------------
# Edge filters
# ---------------------------------------------------------------------------
MIN_EDGE = {
    "strikeouts":   0.5,
    "outs":         1.0,
    "hits_allowed": 0.5,
    "walks":        0.3,
    "game_hits":    0.5,
}

# Edge dead zone: skip edges in this range (middle = uncertain, small/big = signal)
EDGE_DEAD_ZONE = {
    "strikeouts": (1.5, 2.5),   # 1.5-2.5 is 50% coin flip; <1.5 and 2.5+ are 85%
}

MAX_EDGE = {
    "strikeouts":   3.5,      # tightened — huge edges are model disagreement
    "outs":         2.0,       # edge 0-1.5 is 65%, 1.5+ is 39% — big edge = wrong
    "hits_allowed": 1.8,      # edge 0-1.2 is 72.7%, 1.8+ is 45% — tight edge wins
    "walks":        2.5,
    "game_hits":    5.0,
}

# ---------------------------------------------------------------------------
# Min prop line (filter out low-volume lines)
# ---------------------------------------------------------------------------
MIN_LINE = {
    "strikeouts":   4.5,
    "outs":         16.5,      # low lines (14.5-16.5) are 10W-21L (32%)
    "hits_allowed": 4.5,
    "walks":        1.5,
    "game_hits":    14.5,
}

# ---------------------------------------------------------------------------
# Disabled / under-only markets (none initially — tune after backtest)
# ---------------------------------------------------------------------------
DISABLED_MARKETS = {"walks"}   # walks has no edge after calibration
UNDER_ONLY_MARKETS = {"outs", "hits_allowed"}   # OVER is coin flip in both, UNDER has real edge

# ---------------------------------------------------------------------------
# Opponent adjustment config
# ---------------------------------------------------------------------------

# Which team batting stat to use for each market
OPP_STAT_KEY = {
    "strikeouts":   "K_PCT",    # team K% (K/PA)
    "outs":         "OPS",      # high OPS = shorter outings
    "hits_allowed": "BA",       # team batting average
    "walks":        "BB_PCT",   # team walk rate
}

OPP_ADJ_WEIGHT = {
    "strikeouts":   0.25,
    "outs":         0.15,
    "hits_allowed": 0.20,
    "walks":        0.20,
}

# Handedness adjustment weight (pitcher splits vs LHB/RHB)
HANDEDNESS_ADJ_WEIGHT = {
    "strikeouts":   0.15,
    "hits_allowed": 0.10,
    "walks":        0.10,
    "outs":         0.0,
}

# xFIP anchor weight (regress toward true-talent)
XFIP_ANCHOR_WEIGHT = 0.20

# Season anchor (like NBA per-36 anchor — use K/9 season rate)
SEASON_ANCHOR_WEIGHT = {
    "strikeouts":   0.20,
    "outs":         0.15,
    "hits_allowed": 0.15,
    "walks":        0.25,   # walk rate is very stable
}

# ---------------------------------------------------------------------------
# Rest adjustments
# ---------------------------------------------------------------------------
REST_PENALTIES = {
    "short_rest": {          # 4 days or less between starts
        "strikeouts":   -0.3,
        "outs":         -1.5,
        "hits_allowed": +0.3,
        "walks":        +0.2,
    },
    "extra_rest": {          # 6+ days between starts
        "strikeouts":   +0.2,
        "outs":         +0.5,
        "hits_allowed": -0.2,
        "walks":        -0.1,
    },
}

# ---------------------------------------------------------------------------
# API market maps
# ---------------------------------------------------------------------------

# The Odds API market keys
PROP_MARKETS_API = [
    "pitcher_strikeouts",
    "pitcher_outs",
    "pitcher_hits_allowed",
    "pitcher_walks",
]

MARKET_MAP = {
    "pitcher_strikeouts":   "strikeouts",
    "pitcher_outs":         "outs",
    "pitcher_hits_allowed": "hits_allowed",
    "pitcher_walks":        "walks",
}
MARKET_MAP_REV = {v: k for k, v in MARKET_MAP.items()}

# FanDuel market type mapping
FD_MARKET_TYPE_MAP = {
    "PITCHER_STRIKEOUTS":        "strikeouts",
    "TOTAL_PITCHER_STRIKEOUTS":  "strikeouts",
    "PITCHER_OUTS":              "outs",
    "TOTAL_PITCHER_OUTS":        "outs",
    "PITCHER_HITS_ALLOWED":      "hits_allowed",
    "TOTAL_PITCHER_HITS_ALLOWED":"hits_allowed",
    "PITCHER_WALKS":             "walks",
    "TOTAL_PITCHER_WALKS":       "walks",
    "TOTAL_WALKS_ALLOWED":       "walks",
}

# ---------------------------------------------------------------------------
# Stat key maps
# ---------------------------------------------------------------------------
STAT_KEYS = {
    "strikeouts":   "k",
    "outs":         "outs",
    "hits_allowed": "h",
    "walks":        "bb",
}

KALMAN_STAT_KEYS = {
    "strikeouts":   "k",
    "outs":         "outs",
    "hits_allowed": "h",
    "walks":        "bb",
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
