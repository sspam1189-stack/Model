# pyNBAPROPS/scripts/defaults.py
# NBA player-prop-specific constants: markets, thresholds, calibration.
#
# Calibrated from 2025-26 backtest (Jan 1 - Mar 26, 2026).
# Model was systematically under-projecting, which inflated UNDER win rates.
# Calibration offsets correct the bias so picks reflect real model skill.
#
# After calibration, real edge comes from:
#   - Rebounds UNDER (73% at edge > 2)
#   - Assists OVER + UNDER (61%/79%)
#   - Points UNDER (59%, marginal but profitable)
#   - Threes/turnovers/PRA dropped — no real edge after calibration

# ---------------------------------------------------------------------------
# Prop model constants
# ---------------------------------------------------------------------------

PROP_T_DF = 5  # Student's t degrees of freedom

# Rolling window and decay
ROLLING_WINDOW = 10
DECAY_FACTOR = 0.92

# Minimum games to qualify for projection
MIN_GAMES = {
    "points":        5,
    "rebounds":      5,
    "assists":       5,
    "threes":        5,
    "pts_rebs_asts": 5,
    "steals":        8,
    "blocks":        8,
    "turnovers":     5,
}

# Minimum minutes per game to include in rolling window
MIN_MINUTES = 15

# ---------------------------------------------------------------------------
# Calibration offsets — REMOVED
# ---------------------------------------------------------------------------
# Root cause of bias was fixed instead of using static offsets:
#   1. Volume penalty removed (was one-directional, only subtracted)
#   2. B2B made symmetric (penalty + rest bonus)
#   3. Rate blend changed from 50/50 to 30/70 (rolling avg is unbiased)
# Rolling average itself has zero bias (pts: -0.02, reb: -0.002, ast: -0.01)

# ---------------------------------------------------------------------------
# Market-specific thresholds (tightened after calibration)
# ---------------------------------------------------------------------------
# After removing bias, only high-confidence picks have real edge.
# Thresholds set per-market based on calibrated backtest analysis.

MARKET_THRESHOLDS = {
    "points":        {"high": 0.75, "elite": 0.85},   # 56.5% at 0.75 — optimal is floor
    "rebounds":      {"high": 0.73, "elite": 0.83},   # Raised 0.70→0.73: 59%→67% (+9.6u)
    "assists":       {"high": 0.72, "elite": 0.82},   # Raised 0.70→0.72: OVER 69%, UNDER 66%
    "threes":        {"high": 0.80, "elite": 0.90},   # 80% win rate — already optimal
    "pts_rebs_asts": {"high": 0.80, "elite": 0.87},   # 63% win rate — already optimal
    "steals":        {"high": 0.85, "elite": 0.90},
    "blocks":        {"high": 0.85, "elite": 0.90},
    "turnovers":     {"high": 0.80, "elite": 0.90},
}

# Variance multipliers
VAR_MULT = {
    "points":        1.1,
    "rebounds":      1.3,
    "assists":       1.3,
    "threes":        1.6,
    "pts_rebs_asts": 1.1,
    "steals":        1.8,
    "blocks":        1.8,
    "turnovers":     1.4,
}

# ---------------------------------------------------------------------------
# Edge filters (|proj - line| size)
# ---------------------------------------------------------------------------
# After calibration, small edges (< 2) are noise.
# Big edges (> 8) mean the model disagrees too much with the market.

MIN_EDGE = {
    "points":        4.0,   # small edges (2-3) are 38% — noise
    "rebounds":      1.0,
    "assists":       1.0,
    "threes":        0.5,
    "pts_rebs_asts": 3.0,
    "steals":        0.3,
    "blocks":        0.3,
    "turnovers":     0.5,
}

MAX_EDGE = {
    "points":        8.0,    # Tightened from 12 — big edges are usually wrong
    "rebounds":      4.0,    # Tightened from 5
    "assists":       4.0,    # Tightened from 5
    "threes":        2.5,    # Tightened from 3
    "pts_rebs_asts": 10.0,   # Tightened from 15
    "steals":        1.5,
    "blocks":        1.5,
    "turnovers":     2.5,    # Tightened from 3
}

# Minimum line value (filter out low-volume players)
MIN_LINE = {
    "points":        12.5,   # Raised from 10.5 — skip bench players
    "rebounds":       3.5,
    "assists":        2.5,
    "threes":         1.5,
    "pts_rebs_asts": 22.5,   # Raised from 20.5
    "steals":         0.5,
    "blocks":         0.5,
    "turnovers":      1.5,
}

# ---------------------------------------------------------------------------
# Directional filters (based on calibrated backtest)
# ---------------------------------------------------------------------------
# After calibration:
#   - Points OVER: 48% → loser. UNDER only.
#   - Rebounds OVER: 52% → loser. UNDER only.
#   - Assists: both directions profitable (61% OVER, 79% UNDER). Allow both.
#   - Threes OVER: 54% → marginal. UNDER only (tiny sample but 89%).
#   - PRA OVER: 52% → loser. UNDER only.
#   - Turnovers: not enough edge after calibration. Disabled.

UNDER_ONLY_MARKETS = {"points", "rebounds"}  # OVER still unprofitable even with season anchor
# assists allows both OVER and UNDER (real model skill in both directions)

# Markets to disable entirely (no real edge after calibration)
DISABLED_MARKETS = {"steals", "blocks", "turnovers", "pts_rebs_asts"}

# ---------------------------------------------------------------------------
# Opponent adjustment
# ---------------------------------------------------------------------------

OPP_STAT_KEY = {
    "points":        "OPP_PTS",
    "rebounds":      "OPP_REB",
    "assists":       "OPP_AST",
    "threes":        "OPP_FG3M",
    "pts_rebs_asts": "OPP_PTS",
    "steals":        "OPP_TOV",
    "blocks":        "OPP_BLK",
    "turnovers":     "OPP_TOV",
}

OPP_ADJ_WEIGHT = {
    "points":        0.30,  # matches best backtest config
    "rebounds":      0.25,
    "assists":       0.20,
    "threes":        0.25,
    "pts_rebs_asts": 0.25,
    "steals":        0.0,
    "blocks":        0.0,
    "turnovers":     0.0,
}

# Pace adjustment
PACE_ADJ_WEIGHT = {
    "points":        0.008,
    "rebounds":      0.005,
    "assists":       0.004,
    "threes":        0.003,
    "pts_rebs_asts": 0.015,
    "steals":        0.0,
    "blocks":        0.0,
    "turnovers":     0.002,
}

# Minutes threshold — volume penalty removed (was one-directional bias source)
# Kept for backward compat but no longer used in projection pipeline
MINUTES_VOLUME_THRESHOLD = 28.0

# ---------------------------------------------------------------------------
# Season anchor (per36 regression toward season mean)
# ---------------------------------------------------------------------------
# Rolling avg chases recent variance; points/rebounds are streaky.
# Blend the rolling-based projection with a per-36 season baseline to
# prevent recency-driven deflation that inflates phantom UNDER edges.
# Weight 0.0 = pure rolling (old behavior), 1.0 = pure per-36 season.

SEASON_ANCHOR_WEIGHT = {
    "points":        0.25,  # with rate blend
    "rebounds":      0.20,  # no rate blend — 68.1% +39.2u
    "assists":       0.0,   # already unbiased
    "threes":        0.0,   # small sample
    "pts_rebs_asts": 0.30,  # no rate blend — best bias fix
}

# Per-36 stat keys (maps market -> key in per36 dict from nba_api)
PER36_STAT_KEY = {
    "points":   "PTS",
    "rebounds":  "REB",
    "assists":   "AST",
    "threes":    "FG3M",
}

# Legacy keys
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
]

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

MARKET_MAP_REV = {v: k for k, v in MARKET_MAP.items()}

# ---------------------------------------------------------------------------
# NBA Stats API headers
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
