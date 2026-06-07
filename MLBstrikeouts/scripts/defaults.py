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
    #
    # 2026-05-20 threshold re-sweep at full new config (BF=1.00, VAR=1.15,
    # K_CAP=0.38, ZC=0.7, pitches>=30): lowering to 0.70 gains +21.6u
    # season at IDENTICAL combined ROI (29.56%). Picks WR essentially same
    # (73.8% vs 73.9% at 0.72) — 0.70-0.72 plays just promoted from lean
    # tier to pick tier where they're sized 2.5u instead of 1.5u.
    # Volume increase: 305 picks vs 249 (+22%).
    # Lean band now 0.65-0.70 (still 5pp wide, still usable for display).
    #
    # 2026-05-25 sweep on clean backfill (n=741 graded, pCover>=0.60): WR
    # plateaus at ~72% from 0.68 to 0.73 — the model can't distinguish
    # quality inside that band, so the lean/pick split was artificial.
    # Flat 2u @ 0.68 beats current 2u-pick/1u-lean @ 0.72 by +43u season
    # (+372.80u vs +329.51u), +31.75u over last 3 weeks (+161.40u vs
    # +129.65u, 73.7% WR vs 69.5%), 19% fewer bets. Dropping the 0.65-0.68
    # band (lean-only previously, ~56% WR there) eliminates the noisiest
    # tier. New convention: flat 2u for every pick, no leans.
    #
    # 2026-05-29 (CSW era): lowered 0.68 -> 0.65. The 0.65-0.68 band that was
    # the "noisy ~56% tier" under whiff is 64.4% season / 70.4% recent under
    # CSW — the more accurate metric sharpened the margin. The added band
    # clears the >0.20 u/pick bar in both windows (+0.224 season / +0.367
    # recent). Below 0.65 the marginal value drops under the bar (0.60-0.65 =
    # +0.10 / +0.16 u/pick), so 0.65 is the floor. Watch tier is now 0.60-0.65.
    "strikeouts":   {"high": 0.65},
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
    # blend, see line ~197). First pass: BF=0.95, VAR=1.10. +381.4u at 2.5/1.5.
    #
    # 2026-05-20 4D extension (BF x VAR x BF_CAP x K_CAP) after pitches>=30
    # filter shipped: full optimum shifted to VAR=1.05 (was 1.10). At
    # BF=0.95, BF_CAP=23, K_CAP=0.38, VAR=1.05: +388.7u season at 2.5/1.5
    # (+52.1u over original baseline / +15.5%).
    #
    # 2026-05-20 5D extension (BF x VAR sweep at fixed K_CAP=0.38, BF_CAP=23,
    # ZC=0.7, pitches>=30): season ROI nearly flat across the BF=0.95 row,
    # but RECENT (5/4+) window shows BF=1.00 VAR=1.15 leads on combined
    # ROI (22.07%) and total recent WR (68.6%). Shipping this config to
    # bias toward recent-window performance (model edge compression makes
    # recent signal more predictive of forward returns than season-wide).
    # Season at this config: +363.6u (vs +388.7u VAR=1.05 = -25u).
    # Recent at this config: +82.8u (vs +83.9u VAR=1.05 = -1.1u, ROI +0.5pp).
    #
    # 2026-05-28 (post leakage-fix): 1.30 -> 1.20, paired with BF_MULT 1.00
    # (see BF_MULT note). 1.0/1.2 wins the leak-free season on ROI + units.
    "strikeouts":   1.20,
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
# Pairwise-hand modes (pairwise_hand, pairwise_pa_weighted) were swept
# 2026-05-20 — both lost (-7.9u to -9.9u). Removed from harness.
#
# 2026-05-20 head-to-head at threshold=0.70 ship config: simple_mean wins
# season units (+8.9u) but pa_weighted wins recent (+1.9u) and dominates
# lean tier (ROI 28.4% vs 22.4% season, 31.2% vs 22.1% recent). Shipping
# pa_weighted to bias toward recent-window performance and lean quality.
LINEUP_K_METHOD = "pa_weighted"

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

# Career handed-K% fallback for thin in-season samples (tier-3 in
# compute_lineup_k_pct). When a batter's in-season overall PA is below this
# cutoff, use their career vs-hand K% (prior completed seasons, leak-free)
# instead of a noisy small-sample rate — e.g. a call-up reading 5 K / 5 PA =
# 100%. 0 = OFF. Swept 2026-06-04: a PA cutoff alone (broad replacement) churns
# the play set and loses recent units; the EXTREME gate below is the lever.
CAREER_KRATE_MAX_SEASON_PA = 0
# Career fallback fires when a batter's in-season K% is an implausible outlier
# (>= this) — almost always a tiny-sample blip (e.g. a call-up reading 5 K /
# 5 PA = 100%). Replaces it with that batter's career handed K% (prior seasons,
# leak-free). SHIPPED 0.45 (2026-06-04): with no PA cutoff this fires only on
# the genuine artifacts, leaving normal thin samples alone — recent +1.3u
# (+263.7 vs +262.4 @2.5u) / season +8.5u (+472.4 vs +463.9), un-flips the
# 6/04 Rodon OVER<-UNDER artifact. 0 = OFF. Continuous shrink (sweep) lost
# recent units; broad cutoffs churned; surgical outlier-only correction won.
CAREER_KRATE_EXTREME = 0.45
# If > 0, tier-3 empirical-Bayes shrinks the thin in-season rate toward the
# career handed rate — k_est = (k_season + career*C)/(pa_season + C) — instead
# of a hard swap. C is the prior strength in pseudo-PA. 0 = hard replace.
CAREER_KRATE_SHRINK_C = 0.0
# Empirical-Bayes prior strength (pseudo-PA) for tier-3: when > 0, shrink the
# thin in-season rate toward the career handed rate instead of hard-swapping:
#   k_est = (k_season + career_rate * C) / (pa_season + C). 0 = hard replace.
CAREER_KRATE_SHRINK_C = 0.0


# ---------------------------------------------------------------------------
# Whiff% + xBA → pitcher_k_rate regression adjustment
# ---------------------------------------------------------------------------
# Per-pitcher additive K-rate shift based on how far whiff_pct and xBA
# deviate from league averages:
#
#   k_adj = CSW_K_SLOPE * (whiff_pct - CSW_LEAGUE_AVG)
#         + XBA_K_SLOPE   * (xba       - XBA_LEAGUE_AVG)
#   pitcher_k_rate += k_adj * CSW_XBA_BLEND_WEIGHT
#
# Slopes and league averages are computed dynamically at the start of each
# backfill / run_daily from the current Baseball Savant snapshot (see
# compute_whiff_xba_regression() in sources/mlb_stats.py). The values
# below are FALLBACKS used only if the snapshot has <50 qualified pitchers.
#
# Empirical 2026 season-to-5/21 (n=415): bivariate OLS R^2 = 0.65.
# Both whiff (+0.62) and xBA (-0.58) survive controlling for the other.
#
# Weight=0.5 chosen from the 2026-05-28 post-leakage-fix sweeps
# (sweep_4d_whiff over 0.6/0.8/1.0, then sweep_whiff_floor over 0.4/0.5/0.6).
# The earlier 0.8 was tuned on a LEAKY backfill that fed historical
# projections season-end savant whiff/xBA, which inflated the whiff signal.
# Once backfill replays as-of-date savant snapshots (load_savant_rates_as_of),
# ROI is monotonically WORSE as weight rises 0.6->0.8->1.0, and the floor
# sweep finds an interior optimum at 0.5 (3 of 4 BF/VAR cells; 0.4 stops
# helping). Moving 0.8->0.5 dropped ~68 net-losing picks and lifted season
# picks-only ROI ~23%->~29% / +11.6u (BF=1.0,CAP=24,VAR=1.3).
# NOTE: absolute backfill ROI (~28%) is still implausibly high — likely
# line-quality (non-closing lines); trust the relative tuning, not the level.
# Which pitch-quality metric is the regression's first regressor (paired with
# xBA). "whiff" = Savant whiff_pct from the custom leaderboard (default).
# "csw" = Called-Strikes+Whiffs%, reconstructed leak-free as-of each date from
# Statcast pitch logs (fetch_statcast_pitch_csw / load_csw_as_of). Slopes refit
# dynamically either way, so this just swaps which column feeds the fit. A/B
# this against "whiff" via compare_configs_AB before shipping (ROI, not fit).
#
# 2026-05-29: shipped "csw" after full 4-knob sweep (weight/BF_CAP/VAR/kcap).
# Optimal CSW config = weight0.3 / BF_CAP25 / VAR1.2 / kcap0.40. Picks-only:
# season +339.75u vs whiff@0.5/c24/k0.36 +316.25u (+23.5u), recent within-noise
# (-1.57u), more accurate on MAE/RMSE. Recent still narrowly whiff's and season
# edge is +0.073u/pick (under the 0.20 bar) — shipped on user call, near-tie.
K_QUALITY_METRIC = "csw"

# Second regressor (the contact-quality axis paired with the K_QUALITY_METRIC
# stuff axis). "xba" (default) = Savant xBA-against. Note xBA carries strikeouts
# in its own AB denominator, so part of its negative K-slope is mechanical
# overlap with K% rather than fresh contact-quality signal. "xwobacon" =
# expected wOBA on contact only, which strips the K component and isolates pure
# quality of contact — a cleaner, more orthogonal complement to CSW. Slopes
# refit dynamically either way (compute_whiff_xba_regression x2_key), so this
# just swaps which column feeds the second regressor. A/B via compare_configs_AB
# before shipping (ROI, not fit).
# NOTE: leak-free backfill of "xwobacon" needs daily savant snapshots that
# include the xwobacon column. fetch_savant_pitcher_rates began collecting it
# 2026-06-07, so only snapshots from that date forward carry it; earlier dates
# lack the column and degrade to no-blend for the xwobacon variant. Regression
# cache files are namespaced per second-metric so xba and xwobacon fits never
# collide.
CONTACT_QUALITY_METRIC = "xba"

CSW_XBA_BLEND_WEIGHT = 0.3   # 2026-05-29: 0.5->0.3, CSW's tuned optimum (was whiff's)
# Fallbacks used only when the dynamic refit has <50 qualified pitchers.
# 2026-05-29: re-derived from the CSW regression (x1_key="csw") on the
# leak-free as-of-2026-05-28 snapshot (n=433, R^2=0.648). The prior values
# were whiff-scale (slope 0.6279 / mean 0.2557) — wrong for CSW, whose %
# runs higher (mean ~0.284) and steeper (slope ~1.15). xBA partial slope
# also shifts slightly when paired with CSW instead of whiff.
CSW_LEAGUE_AVG = 0.2844   # fallback (mean CSW%)
XBA_LEAGUE_AVG   = 0.2394   # fallback (mean xBA against)
CSW_K_SLOPE    = 1.1540   # fallback (bivariate CSW partial slope)
XBA_K_SLOPE      = -0.6208  # fallback (bivariate xBA partial slope, paired w/ CSW)


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
#
# 2026-05-20 BF x VAR sweep (pitches>=30 + K_CAP=0.38): season ROI flat
# across BF=0.95-1.00, but RECENT (5/4+) combined WR best at BF=1.00
# (68.6% with VAR=1.15) vs BF=0.95 (68.5%). Bumping to 1.00 to bias
# toward recent-window performance amid market edge compression.
#
# 2026-05-28 4D sweep (BF x VAR x CAP x whiff) + BF-projection calibration:
# raw BF projection is unbiased (actual/proj=1.004 season), so 1.10 is NOT a
# 2026-05-28 (post leakage-fix): reverted 1.10 -> 1.00 and VAR 1.30 -> 1.20.
# On the LEAK-FREE backfill, BF=1.00 is the calibrated value (projection bias
# +0.02, MAE 1.68) whereas 1.10 over-projects (+0.32 bias, worse MAE) and its
# gains are OVER-skewed soft-line harvesting, not real edge. Head-to-head at
# CAP=24/WHIFF=0.5: BF=1.0/VAR=1.2 wins the SEASON on both ROI and units
# (28.6% / +136.1u vs 1.1/1.3's 27.0% / +133.4u); recent is ~tied (32.6% vs
# 34.3%). Choosing the season-calibrated config over the recent-regime bet.
# (The earlier 1.10 "recent-regime bet" was set pre-leakage-fix.)
BF_MULT = 1.00

# Hard ceiling on projected batters faced after BF_MULT. League-wide safety
# net on top of the avg_pc = median(last 5 PCs) projection (see props_engine).
# 2026-05-27 sweep: 24 beats 23 on season ROI (+39.5% vs +36.8%), units
# (+175.8u vs +161.7u), win rate (72.6% vs 71.5%), and TAKE/PASS gap
# (+15.4pp vs +5.7pp). Filters out marginal UNDERs that tend to lose.
# 2026-05-29: 24 -> 25 with K_QUALITY_METRIC=csw. CSW's projection distribution
# wants a higher cap than whiff (whiff peaks at 24, CSW season peaks at 26 but
# 25 is the recent-units + accuracy optimum). See K_QUALITY_METRIC note.
BF_CAP = 25.0

# Per-pitcher BF ceiling percentile. When > 0, each pitcher's projected BF
# is capped at the Nth percentile of their own recent game BFs (from the
# qualified rolling window) instead of the global BF_CAP. BF_CAP remains
# as the hard safety net above the per-pitcher ceiling.
# Set to 0.0 to disable (use global BF_CAP only).
BF_CAP_PCTILE = 0.0

# Per-pitcher K-rate cap floor — applied as max(K_RATE_CAP_FLOOR, season K%)
# in props_engine.py. The matchup-driven expected_k_rate
# (pitcher_k × lineup_k / lg_k) is then clamped to that per-pitcher cap, so:
#   * Mid-tier pitchers (season K% < floor) can project up to the floor
#     but no further, even with elite matchups.
#   * Elite K pitchers (season K% > floor) get their proven ceiling.
# 2026-05-13 2D sweep (floor × headroom): floor=0.36 + headroom=1.00 optimal —
# 161 picks, 79.5% WR, +41.5% ROI, +175.56u (vs flat 0.36 cap which was
# 162 picks 79.0% WR +40.8% ROI).
# 2026-05-20 K_CAP sweep at ZC=0.7 BF=0.95 VAR=1.10: peak shifted from 0.36
# to 0.38 (+5.5u at 2.5/1.5). ZC/chase blend boosts elite-pitch-quality
# pitchers' K rate, and 0.36 was clipping too aggressively.
# 2026-05-29: 0.36 -> 0.40 with K_QUALITY_METRIC=csw. kcap is the lever that
# closes CSW's recent-window gap: recent picks-only -6.58u at k0.36 -> -1.57u
# at k0.40 (peak; saturates k0.42+). Season also peaks here (+339.75u).
K_RATE_CAP_FLOOR = 0.40

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
