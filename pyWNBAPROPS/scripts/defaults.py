# pyWNBAPROPS/scripts/defaults.py
# WNBA player-prop-specific constants: markets, thresholds, calibration.
#
# Fork of pyNBAPROPS/scripts/defaults.py, re-calibrated for the WNBA.
# WNBA realities absorbed by calibration:
#   - 40-minute games (NBA is 48) -> lower per-game volumes; MIN_LINE lowered.
#   - ~44-game season, 13-15 teams -> far less data per player than NBA's 82;
#     MIN_GAMES loosened and Kalman noise widened so real players aren't
#     filtered out early.
#
# WNBA calibration evidence: constants are calibrated from a REAL 2026-season
# walk-forward backtest using ACTUAL closing lines pulled from The Odds API
# historical endpoint (sources/odds_theoddsapi_historical.py) — ~8.5k lines /
# 272 events over 49 completed game dates, graded vs nba_api box scores
# (1,487 graded picks). See the per-section comments below for the derived
# MARKET_THRESHOLDS, VAR_MULT, MIN_LINE, DISABLED_MARKETS and directional
# conclusions. NBA's numbers and "which markets" conclusions were NOT inherited
# — each was re-derived from the WNBA sweep (sweep_thresholds.py /
# calibrate_threshold.py). MIN_LINE / MIN_GAMES / Kalman noise were sized from
# the 2024+2025 WNBA volume + within-player-variance distributions.
#
# SHIPPED (real +EV): assists (UNDER-carried), points OVER-only, threes,
#   rebounds. DISABLED: pts_rebs_asts (only +0.01 u/pick UNDER-only on real
#   lines — too thin), steals/blocks/turnovers (not offered).

# ---------------------------------------------------------------------------
# Prop model constants
# ---------------------------------------------------------------------------

PROP_T_DF = 5  # Student's t degrees of freedom

# Rolling window and decay
# WNBA plays ~40 games/season (vs NBA's 82). A 10-game window is still ~25%
# of the season and reacts fast enough to role changes, so it is retained.
ROLLING_WINDOW = 10
DECAY_FACTOR = 0.92

# Minimum games to qualify for projection.
# LOOSENED vs NBA (was 5): the WNBA season is ~40 games and players miss time,
# so a 5-game gate filtered out real rotation players deep into May/June. A
# 4-game floor lets early-season starters in while still requiring a stable
# baseline. (steals/blocks/turnovers stay disabled — see DISABLED_MARKETS.)
MIN_GAMES = {
    "points":        4,
    "rebounds":      4,
    "assists":       4,
    "threes":        4,
    "pts_rebs_asts": 4,
    "steals":        8,
    "blocks":        8,
    "turnovers":     5,
}

# Minimum minutes per game to include in rolling window.
# WNBA games are 40 min (NBA 48). Backtest volume distribution (2024+2025,
# n=7,069 player-games >=15 min): median 27 min, p75 32, p90 35. A 12-minute
# floor (vs NBA's 15) keeps genuine rotation players (WNBA benches are shorter,
# so a 12-15 min player is a real contributor) without admitting garbage-time.
MIN_MINUTES = 12

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
# WNBA calibration — REAL 2026 season backtest (walk-forward, ACTUAL closing
# lines from The Odds API historical endpoint: ~8.5k lines / 272 events across
# 49 completed game dates, May 8 - Jun 28 2026; graded vs nba_api box scores).
# 1,487 graded picks. This REPLACES the earlier synthetic-line calibration now
# that real WNBA book lines are available (paid key). Per-market threshold
# sweep (sweep_thresholds.py), WR / units / u-per-pick:
#     assists  0.575 -> 68.2% WR  +14.6u  +0.33 u/pick  (UNDER carries it)
#     points   0.600 -> 59.0% WR  +25.5u  +0.14 u/pick  (flat/-EV below 0.60)
#     threes   0.575 -> 62.0% WR  +10.1u  +0.20 u/pick
#     rebounds 0.575 -> 58.3% WR   +4.5u  +0.13 u/pick
#     pra      0.575 -> 57.3% WR  +20.5u  +0.10 u/pick  (UNDER-only +1.7u only;
#                                                        DISABLED, see below)
# Calibration table (calibrate_threshold.py): model is mildly OVER-confident
# (claim > WR by 0.03-0.06) for points/PRA/rebounds, well-calibrated for
# threes, and UNDER-confident for assists. Points is -EV in the 0.55-0.60
# bucket (claim 0.57 vs WR 0.476) and only turns +EV at 0.60 (claim 0.62 vs
# WR 0.599) -> points threshold = 0.60. Assists is under-confident so it bets
# at 0.575.
#
# POINTS BOTH-DIRECTIONS re-sweep (0.55->0.75 in 0.01 steps on the same 2026
# real-line data, 705 captured points picks; points_sweep). Per-direction:
#     thr   BOTH (n / WR / u/pick)   OVER (n / WR / u/pick)   UNDER (n / WR / u/pick)
#     0.58  201 / 57.7% / +0.112     127 / 55.9% / +0.074     74 / 60.8% / +0.177
#     0.59  161 / 60.2% / +0.165     101 / 59.4% / +0.148     60 / 61.7% / +0.195
#     0.60  124 / 59.7% / +0.153      80 / 61.3% / +0.186     44 / 56.8% / +0.093
#     0.62   65 / 55.4% / +0.063      34 / 61.8% / +0.197     31 / 48.4% / -0.084
# Key finding: OVER is -EV below 0.58 but turns solidly +EV at 0.60 (61.3% WR,
# +0.186 u/pick) and stays +EV/+0.19 up to 0.63 — it is NOT hopeless. UNDER's
# edge is concentrated 0.58-0.60 and fades above. BOTH-directions @0.60
# (59.7% WR, +0.153 u/pick, ~4x the volume) DOMINATES the old UNDER-only@0.60
# (58.7% WR, +0.133 u/pick) on units AND u/pick — so points is shipped
# BOTH DIRECTIONS at 0.60 (removed from UNDER_ONLY_MARKETS). Combined u/pick
# (0.15-0.16) sits just under the ~0.20 house bar, but each DIRECTION clears
# ~0.18-0.20 in its band, and the 0.60 gate keeps OVER firing only where it is
# +EV. Threshold 0.60 (over 0.59) trades ~6u of volume-driven units for a
# cleaner cutoff at identical u/pick.
MARKET_THRESHOLDS = {
    "points":        {"high": 0.600},   # OVER-ONLY (see OVER_ONLY_MARKETS); OVER +EV only >=0.60 -> 64.1% WR
    "rebounds":      {"high": 0.600},   # rebs sweep: 0.575 sits in a dip (56.7%/+0.09/pk); 0.60 = 60.0%/+2.4u/+0.160/pk high-ROI cut (0.55 peaked +0.168/pk but 0.60 chosen for the cleaner cutoff)
    "assists":       {"high": 0.575},
    "threes":        {"high": 0.575},
    "pts_rebs_asts": {"high": 0.575},   # DISABLED below (kept for when re-enabled)
    "steals":        {"high": 0.65},
    "blocks":        {"high": 0.65},
    "turnovers":     {"high": 0.65},
}

# Per-market variance multipliers — applied to total rate+minutes variance.
# calibrate_threshold.py VAR_MULT-scaling on the real 2026 picks: assists is
# under-confident (scale 0.54 -> the inherited 3.55 is if anything a touch
# high, but lowering it would over-fire; KEPT); rebounds/threes are ~well
# calibrated (scale 1.3-1.5 -> small bumps below); points/PRA show large scale
# factors (4-5x) driven almost entirely by the OVER side losing — the right fix
# there is DIRECTIONAL (points is UNDER-only) not a giant variance inflation
# that would zero out volume, so points/PRA VAR_MULT are left modest and the
# directional filter + 0.60 threshold do the work. Revisit as more live picks
# accrue.
VAR_MULT = {
    "points":        2.3,   # slight bump; both dirs + 0.60 thresh does the filtering
    "rebounds":      4.30,  # +calibration nudge (was 4.03; scale ~1.49 on real picks)
    "assists":       3.55,  # under-confident on real data — KEPT (lowering over-fires)
    "threes":        3.30,  # +small nudge (was 3.16; scale ~1.26)
    "pts_rebs_asts": 2.3,
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

# Minimum line value (filter out low-volume players).
# RE-DERIVED for WNBA from the 2024+2025 volume distribution (n=7,069
# player-games >=15 min): points mean 11.3 / p75 16, rebounds 4.6 / p75 6,
# assists 2.8 / p75 4, threes 1.1 / p75 2, PRA 18.7 / p75 25. WNBA volumes run
# well below the NBA's (40-min games), so the NBA floors (points 12.5, PRA
# 22.5) would have cut most of the rotation. Floors set near each stat's
# median so we skip deep-bench noise but keep starters/6th-women.
MIN_LINE = {
    "points":         8.5,
    "rebounds":       3.5,
    "assists":        2.5,
    "threes":         1.5,
    "pts_rebs_asts": 14.5,
    "steals":         0.5,
    "blocks":         0.5,
    "turnovers":      1.5,
}

# ---------------------------------------------------------------------------
# Directional filters (REAL 2026 backtest, actual closing lines — data-driven)
# ---------------------------------------------------------------------------
# Per-market x direction on the 1,487 real graded picks (flat -110, n / WR /
# units):
#   - assists : UNDER 70.0% (n=100, +37.0u) | OVER 51.9% (n=104, -1.0u)
#               -> UNDER carries the edge; OVER ~breakeven. Both allowed (OVER
#                  isn't a loser), but the money is on UNDER.
#   - points  : UNDER 57.0% (n=272, +26.3u) | OVER 48.8% (n=441, -33.6u) at the
#               0.50 sweep floor. OVER's losses are concentrated in LOW-
#               confidence picks; gated at pCover>=0.60 OVER flips +EV
#               (+0.186 u/pick, see MARKET_THRESHOLDS note above). SHIPPED BOTH
#               DIRECTIONS @0.60 per the points sweep — the 0.60 gate is what
#               makes OVER safe, not a hard UNDER-only lock.
#   - rebounds: OVER 56.2% (n=48, +3.9u) | UNDER 53.6% (n=28, +0.7u)  -> both.
#   - threes  : OVER 58.0% (n=50, +5.9u) | UNDER 63.6% (n=11, +2.6u)  -> both.
#   - pts_rebs_asts: OVER 50.6% (n=257, -9.7u) | UNDER 52.8% (n=176, +1.7u).
#               -> UNDER only marginally +EV (+0.01 u/pick); OVER a loser.
#                  Net edge too thin to ship -> DISABLED (re-evaluated per the
#                  coordinator's request: against REAL lines PRA still shows no
#                  meaningful edge). Re-check as more live picks accrue.
#   - steals / blocks / turnovers: not offered on WNBA books -> disabled.

UNDER_ONLY_MARKETS = set()

# points is OVER-ONLY. Full 2026 sweep (0.53->0.64) shows the two directions
# want OPPOSITE gates: OVER is only +EV at >=0.60 (64.1% WR, +14.3u,
# +0.223 u/pick, n=64) and climbs to 65-69% above it, while UNDER's edge sits
# at 0.58-0.59 (~59-61%) and DIES above 0.60 (50%/-EV at 0.62). Forcing both
# through one 0.60 gate capped combined WR at ~61%; dropping the lower-WR UNDER
# side lifts points to 64.1% WR and +0.223 u/pick (best ROI of any points
# config) while keeping solid volume. Chosen over both-directions when win rate
# / ROI is the objective (both-dirs keeps ~5u more volume at ~61% WR).
OVER_ONLY_MARKETS = {"points"}

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
# WNBA Stats API headers (WNBA stats are served off stats.wnba.com)
# ---------------------------------------------------------------------------

NBA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.wnba.com/",
    "Origin": "https://www.wnba.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
}

# ---------------------------------------------------------------------------
# Season helpers
# ---------------------------------------------------------------------------

def current_season():
    """Auto-detect WNBA season string (a plain calendar year, e.g. '2026').

    Unlike the NBA (Oct-Jun, spanning two years -> '2025-26'), the WNBA plays
    a single-year May-September season, so the season string is simply the
    current calendar year. During the off-season (Oct-Apr) we still return the
    current year -- the most-recent completed season's caches remain valid until
    the new season tips off in May.
    """
    import datetime
    now = datetime.datetime.now()
    return str(now.year)


def season_start_year(season_str=None):
    """Extract the (single) year from a WNBA season string '2026' -> 2026."""
    s = season_str or current_season()
    # Tolerate an accidental NBA-style '2025-26' by taking the first token.
    return int(str(s).split("-")[0])
