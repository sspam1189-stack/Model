# pyNBAPROPS/scripts/defaults.py
# NBA player-prop-specific constants: markets, thresholds, team list.
#
# Adapted from pyNFL/scripts/props_engine.py constants for NBA context.
# NBA props have different variance profiles than NFL:
#   - Points are the most predictable (high-volume stat, ~20+ FGA/game)
#   - Assists are moderately predictable (playmaker usage)
#   - Rebounds are noisy (depends on missed shots, lineup)
#   - 3-pointers made are very volatile (low-count stat)
#   - Steals/blocks are ultra-noisy (rare events)

# ---------------------------------------------------------------------------
# Prop model constants
# ---------------------------------------------------------------------------

PROP_T_DF = 5  # Student's t degrees of freedom (slightly less heavy-tailed than NFL's 4)

# Rolling window and decay
ROLLING_WINDOW = 10        # NBA plays 82 games — more data than NFL's 6-game window
DECAY_FACTOR = 0.92        # Slower decay than NFL (0.88) — NBA performance is more stable game-to-game

# Minimum games to qualify for projection
MIN_GAMES = {
    "points":        5,    # Need decent sample for scoring
    "rebounds":      5,
    "assists":       5,
    "threes":        5,    # 3-pointers made
    "pts_rebs_asts": 5,    # Points + Rebounds + Assists combo
    "steals":        8,    # Rare events need bigger sample
    "blocks":        8,
    "turnovers":     5,
}

# Minimum minutes per game to include in rolling window
MIN_MINUTES = 15  # Filter out blowout/injury shortened games

# ---------------------------------------------------------------------------
# Market-specific thresholds (calibrated for NBA prop variance)
# ---------------------------------------------------------------------------
# NBA points are the most predictable player prop; rebounds/assists are moderate;
# threes/steals/blocks are very noisy.

MARKET_THRESHOLDS = {
    "points":        {"high": 0.58, "elite": 0.64},
    "rebounds":      {"high": 0.60, "elite": 0.66},
    "assists":       {"high": 0.60, "elite": 0.66},
    "threes":        {"high": 0.65, "elite": 0.72},   # Very volatile — need high bar
    "pts_rebs_asts": {"high": 0.58, "elite": 0.64},   # Combo smooths variance
    "steals":        {"high": 0.70, "elite": 0.78},   # Ultra-noisy
    "blocks":        {"high": 0.70, "elite": 0.78},
    "turnovers":     {"high": 0.62, "elite": 0.68},
}

# Variance multipliers (inflate weighted std to account for game-to-game noise)
VAR_MULT = {
    "points":        1.1,   # Scoring is relatively stable for starters
    "rebounds":      1.3,   # Depends on opponent pace, lineup, missed shots
    "assists":       1.3,   # Depends on teammate shooting
    "threes":        1.6,   # Very high variance (count stat, often 0-8 range)
    "pts_rebs_asts": 1.1,   # Combo smooths individual variance
    "steals":        1.8,   # Rare event, high game-to-game noise
    "blocks":        1.8,
    "turnovers":     1.4,
}

# ---------------------------------------------------------------------------
# Edge filters (|proj - line| size)
# ---------------------------------------------------------------------------
# Too-small edges are noise; too-large edges mean the model is likely wrong.

MIN_EDGE = {
    "points":        2.0,
    "rebounds":      1.0,
    "assists":       1.0,
    "threes":        0.5,
    "pts_rebs_asts": 3.0,
    "steals":        0.3,
    "blocks":        0.3,
    "turnovers":     0.5,
}

MAX_EDGE = {
    "points":        12.0,
    "rebounds":       5.0,
    "assists":        5.0,
    "threes":         3.0,
    "pts_rebs_asts": 15.0,
    "steals":         1.5,
    "blocks":         1.5,
    "turnovers":      3.0,
}

# Minimum line value (filter out low-volume players)
MIN_LINE = {
    "points":        10.5,   # Skip bench players with tiny lines
    "rebounds":       3.5,
    "assists":        2.5,
    "threes":         1.5,
    "pts_rebs_asts": 20.5,
    "steals":         0.5,
    "blocks":         0.5,
    "turnovers":      1.5,
}

# ---------------------------------------------------------------------------
# Directional filters
# ---------------------------------------------------------------------------
# Start with no directional bias — NBA props may not have the same UNDER bias
# as NFL. This can be refined after backtesting.
UNDER_ONLY_MARKETS = set()   # Empty = allow both OVER and UNDER
# If backtest shows UNDER is consistently better, add markets here:
# UNDER_ONLY_MARKETS = {"points", "pts_rebs_asts"}

# ---------------------------------------------------------------------------
# Opponent adjustment (new: uses actual per-game opponent stats allowed)
# ---------------------------------------------------------------------------
# For each prop market, we adjust the projection based on how many of that
# stat the opponent allows per game vs. league average.
#
# Formula: opp_adj = (opp_allowed - league_avg) * OPP_ADJ_WEIGHT
#
# Example: league avg PTS allowed = 112. Opponent allows 118.
#          opp_adj = (118 - 112) * 0.30 = +1.8 points boost.
#
# This is much more precise than the old DEF_RATING-based approach because:
#   - OPP_PTS directly measures points allowed, not overall defense quality
#   - OPP_REB directly measures rebounds allowed (pace-dependent)
#   - OPP_FG3M directly measures 3-pointers allowed

# Maps market -> opponent stat key (from fetch_team_def_stats)
OPP_STAT_KEY = {
    "points":        "OPP_PTS",       # Points allowed per game
    "rebounds":      "OPP_REB",       # Rebounds allowed per game
    "assists":       "OPP_AST",       # Assists allowed per game
    "threes":        "OPP_FG3M",      # 3PM allowed per game
    "pts_rebs_asts": "OPP_PTS",       # Use points as proxy for PRA (dominant component)
    "steals":        "OPP_TOV",       # Turnovers forced correlates with steals given up
    "blocks":        "OPP_BLK",       # Blocks against per game
    "turnovers":     "OPP_TOV",       # Turnovers forced
}

# Weight applied to the (opp_allowed - league_avg) difference
# Higher weight = stronger opponent adjustment
OPP_ADJ_WEIGHT = {
    "points":        0.30,    # 30% of the per-game difference
    "rebounds":      0.25,    # Rebounds are pace-dependent
    "assists":       0.20,    # Assists weakly tied to opponent
    "threes":        0.25,    # 3PM depends on opponent perimeter D
    "pts_rebs_asts": 0.25,    # Composite
    "steals":        0.0,     # Too noisy for per-game adjustment
    "blocks":        0.0,     # Too noisy
    "turnovers":     0.0,     # Don't adjust — turnovers are player-driven
}

# ---------------------------------------------------------------------------
# Advanced stats: Usage-based minutes/volume projection
# ---------------------------------------------------------------------------
# When a player's USG% is available (from advanced stats), we can scale
# the projection by their expected volume in a given game.
#
# If a co-star is out, USG% will be higher in recent games → the model
# naturally captures this through the rolling average. But we can also
# use pace differential to adjust for game environment.

# Pace adjustment: how much to scale projection based on game pace
# Higher pace = more possessions = more stats across the board
PACE_ADJ_WEIGHT = {
    "points":        0.008,    # ~0.8 pts per 1 pace above average
    "rebounds":      0.005,    # ~0.5 reb per 1 pace above average
    "assists":       0.004,    # ~0.4 ast per 1 pace above average
    "threes":        0.003,    # ~0.3 3pm per 1 pace above average
    "pts_rebs_asts": 0.015,    # Sum
    "steals":        0.0,
    "blocks":        0.0,
    "turnovers":     0.002,
}

# Minutes threshold: if player's season avg minutes are below this,
# their prop projection gets a volume penalty
MINUTES_VOLUME_THRESHOLD = 28.0  # Above this = full projection; below = scaled down

# Legacy keys for backward compat with older code paths
OPP_ADJ_SCALE = OPP_ADJ_WEIGHT
OPP_DEF_STAT = OPP_STAT_KEY

# ---------------------------------------------------------------------------
# The Odds API: NBA prop market keys
# ---------------------------------------------------------------------------

PROP_MARKETS_API = [
    "player_points",
    "player_rebounds",
    "player_assists",
    "player_threes",
    "player_points_rebounds_assists",
    # Uncomment when ready to project these:
    # "player_steals",
    # "player_blocks",
    # "player_turnovers",
]

# Map API market key -> internal market name
MARKET_MAP = {
    "player_points":                     "points",
    "player_rebounds":                   "rebounds",
    "player_assists":                    "assists",
    "player_threes":                     "threes",
    "player_points_rebounds_assists":     "pts_rebs_asts",
    "player_steals":                     "steals",
    "player_blocks":                     "blocks",
    "player_turnovers":                  "turnovers",
}

# Reverse map: internal name -> API market key
MARKET_MAP_REV = {v: k for k, v in MARKET_MAP.items()}

# ---------------------------------------------------------------------------
# NBA Stats API headers (same as pyNBA)
# ---------------------------------------------------------------------------

NBA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
}

# ---------------------------------------------------------------------------
# Season helpers
# ---------------------------------------------------------------------------

def current_season():
    """Auto-detect NBA season string (e.g. '2025-26')."""
    import datetime
    now = datetime.datetime.now()
    start_year = now.year if now.month >= 10 else now.year - 1
    return f"{start_year}-{str(start_year + 1)[2:]}"


def season_start_year(season_str=None):
    """Extract start year from '2025-26' -> 2025."""
    s = season_str or current_season()
    return int(s.split("-")[0])
