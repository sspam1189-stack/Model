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
# Market thresholds — minimum pCover to make a pick
# The model's empirical std and rate-based projections handle calibration,
# so thresholds are set to minimum viable (just above coin flip).
# ---------------------------------------------------------------------------
MARKET_THRESHOLDS = {
    "strikeouts":   {"high": 0.60},
    "outs":         {"high": 0.62, "high_under": 0.62},  # keep filter for outs
    "hits_allowed": {"high": 0.55},
    "walks":        {"high": 0.55},
    "game_hits":    {"high": 0.55},
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
# Edge filters — K has none. OUTS keeps min edge.
# ---------------------------------------------------------------------------
MIN_EDGE = {
    "strikeouts":   0.0,
    "outs":         1.0,
    "hits_allowed": 0.0,
    "walks":        0.0,
    "game_hits":    0.0,
}

EDGE_DEAD_ZONE = {}

MAX_EDGE = {
    "strikeouts":   999,
    "outs":         999,
    "hits_allowed": 999,
    "walks":        999,
    "game_hits":    999,
}

# ---------------------------------------------------------------------------
# Min prop line — OUTS keeps its floor.
# ---------------------------------------------------------------------------
MIN_LINE = {
    "strikeouts":   0,
    "outs":         16.5,
    "hits_allowed": 0,
    "walks":        0,
    "game_hits":    0,
}

# ---------------------------------------------------------------------------
# Disabled / direction-restricted markets
# ---------------------------------------------------------------------------
DISABLED_MARKETS = {"walks", "hits_allowed", "outs"}
UNDER_ONLY_MARKETS = set()

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
# Batter props — total bases
# ---------------------------------------------------------------------------
BATTER_MIN_GAMES = 10          # ~2 weeks of daily play
BATTER_MIN_PA = 30             # minimum season PA to qualify
BATTER_MARKET_THRESHOLDS = {
    "total_bases": {"high": 0.58},  # both directions
}
BATTER_MIN_EDGE = {"total_bases": 0.0}
BATTER_CONFIDENCE_FLOOR = 0.52  # don't pick if simulation too noisy
BATTER_REQUIRE_LINEUP = True
BATTER_PAIRED_FILTER = True

# PA by lineup slot (expected plate appearances per game)
LINEUP_SLOT_PA = {
    1: 4.5, 2: 4.4, 3: 4.3, 4: 4.2, 5: 4.1,
    6: 4.0, 7: 3.9, 8: 3.8, 9: 3.7,
}

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
