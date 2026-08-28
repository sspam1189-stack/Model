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
# Confidence threshold
# ---------------------------------------------------------------------------
# Calibrated thresholds: VAR_MULT and pCover thresholds were jointly optimized
# so that the model's predicted pCover ≈ actual win rate (~65-70%).
# Previous VAR_MULT=1.2 was massively overconfident (predicted 84%, actual 62%).

MARKET_THRESHOLDS = {
    "points":        {"high": 0.65},
    "rebounds":      {"high": 0.600},
    "assists":       {"high": 0.575},
    "threes":        {"high": 0.600},
    "pts_rebs_asts": {"high": 0.65},
    "steals":        {"high": 0.65},
    "blocks":        {"high": 0.65},
    "turnovers":     {"high": 0.65},
}

# Per-market variance multipliers — applied to total rate+minutes variance.
# 2026-04-28: after props_engine adopted proper Var(rate × min) formula,
# residual miscalibration was per-market (assists -10pp, rebounds -14pp,
# threes -14pp gap from claim). Per-market VAR_MULTs tuned from
# calibrate_threshold output to bring claimed pCover into line with
# actual realized win rate per market.
#
# 2026-08-27 STALE — RE-SWEEP BEFORE THE SEASON OPENS. These were fitted while
# project_minutes/_weighted_minutes_std both conditioned on min >= 15, so the
# minutes-variance term was measured on a truncated sample and came out far too
# small; VAR_MULT was absorbing the gap. Both now use the unfiltered window
# (real E[min] and its real spread), so base_var is materially larger and these
# multipliers over-inflate std -> pCover compresses toward 0.5 -> pick volume
# drops. Re-run scripts/calibrate_threshold.py against a fresh backfill and
# reset these; do not read pick counts as a signal until that is done.
VAR_MULT = {
    "points":        2.0,
    "rebounds":      4.03,  # 2026-04-29 final — recal #3, balanced WR + volume
    "assists":       3.55,  # 2026-05-10 resweep — scale 0.82 from 4.32 (n=237, +2pp gap)
    "threes":        3.16,
    "pts_rebs_asts": 2.0,
    "steals":        2.0,
    "blocks":        2.0,
    "turnovers":     2.0,
}

# ---------------------------------------------------------------------------
# Edge bounds (sanity checks only — NOT the primary filter)
# ---------------------------------------------------------------------------
# With rate-based projections + honest variance, the confidence threshold
# does the real filtering. These just catch model failures (insane outliers).

MIN_EDGE = {
    "points":        0.5,
    "rebounds":      1.5,
    "assists":       0.5,
    "threes":        1.0,
    "pts_rebs_asts": 1.0,
    "steals":        0.2,
    "blocks":        0.2,
    "turnovers":     0.3,
}

MAX_EDGE = {
    "points":        10.0,
    "rebounds":       6.0,
    "assists":        6.0,
    "threes":         4.0,
    "pts_rebs_asts": 15.0,
    "steals":         3.0,
    "blocks":         3.0,
    "turnovers":      4.0,
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

# 2026-08-26 audit of the full 2025-26 season (631 graded picks, +112.9u,
# +17.9% ROI) re-confirmed the directional split the notes above describe,
# on a real sample this time:
#
#     threes   OVER   74-67  n=141   -0.6% ROI   <- no edge, 141 bets of noise
#     threes   UNDER  69-28  n= 97  +35.0% ROI
#     assists  OVER   73-41  n=114  +21.7% ROI   (both directions work)
#     assists  UNDER  99-56  n=155  +21.6% ROI
#     rebounds UNDER  52-27  n= 79  +24.5% ROI
#     rebounds OVER   22-18  n= 40   +3.8% ROI   <- barely above vig, cut too
#
# Threes is the widest cell in the portfolio: three-point volume is the
# highest-variance counting stat in basketball and books shade those lines up
# because overs sell, so the model's overs pay for the unders' edge. Cutting
# them lifts the market from +14.0% blended to +35.0%.
#
# Rebounds OVER is cut with it (user, 2026-08-26): +3.8% over 40 bets is
# inside the noise band and matches the older note above ("52% -> loser"),
# so the same shading logic applies.
# Assists stays two-directional -- it is the one market where overs earn.
#
# 2026-08-27 re-scored at the REAL cached FanDuel prices (the table above was
# computed at a flat -110, which the 631-pick record was also graded at). Both
# cuts get stronger, not weaker:
#
#     cell             W-L      WR    mean impl   real ROI   @-110 ROI
#     threes   OVER    74-67   52.5%     59.9%      -11.7%      +0.2%
#     threes   UNDER   69-28   71.1%     62.5%      +13.6%     +35.8%
#     rebounds OVER    22-18   55.0%     55.0%       +1.1%      +5.0%
#     rebounds UNDER   52-27   65.8%     57.2%      +15.7%     +25.7%
#     assists  OVER    73-41   64.0%     57.8%      +10.4%     +22.2%
#     assists  UNDER   99-56   63.9%     58.6%       +8.4%     +21.9%
#     ---------------------------------------------------------------
#     whole book      392-239  62.1%                 +5.5%     +18.6%
#     minus both OVER 293-152  65.8%                +11.4%
#
# threes OVER is -11.7% at real prices, not the -0.6% the flat-priced table
# showed: books shade three-point overs (mean implied 59.9% on that cell) and
# the model fires them anyway. rebounds OVER is +1.1%, below vig, not the
# +5.0% the flat price implied. RETIRED, both of them -- do not restore
# either OVER side without re-running the price-aware numbers.
UNDER_ONLY_MARKETS = {"threes", "rebounds"}

DISABLED_MARKETS = {"steals", "blocks", "turnovers", "points", "pts_rebs_asts"}

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

# Opponent adjustment: additive (opp_stat - league_avg) × weight.
# Uniform 0.20 — moderate, lets projection do the work.
OPP_ADJ_WEIGHT = {
    "points":        0.20,
    "rebounds":      0.20,
    "assists":       0.20,
    "threes":        0.20,
    "pts_rebs_asts": 0.20,
    "steals":        0.0,
    "blocks":        0.0,
    "turnovers":     0.0,
}

# Pace adjustment
# Pace adjustment: multiplicative scaling by expected game pace.
# Uniform 0.005 — pace affects all counting stats similarly.
PACE_ADJ_WEIGHT = {
    "points":        0.005,
    "rebounds":      0.005,
    "assists":       0.005,
    "threes":        0.003,
    "pts_rebs_asts": 0.005,
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

# Season anchor blends per-minute rate with per-36 season rate.
# Applied at rate level (not raw stat level), so it regresses
# the rate toward the season baseline before multiplying by minutes.
# Uniform weight — all markets benefit from mean regression.
SEASON_ANCHOR_WEIGHT = {
    "points":        0.15,
    "rebounds":      0.15,
    "assists":       0.15,
    "threes":        0.10,   # smaller sample, trust recent more
    "pts_rebs_asts": 0.15,
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
