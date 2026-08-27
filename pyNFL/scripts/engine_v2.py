# pyNFL/scripts/engine_v2.py
# NFL spread/total engine v2 — structural EPA-based projection.
#
# Replaces the v1 stack (18-feature ridge + Bayesian self-tuned weights +
# Kalman strength adjustments) with the structural identity EPA already
# encodes: points = plays x (league pts/play + matchup EPA edge). EPA *is*
# expected points, so no fitted feature coefficients are needed — just two
# walk-forward-calibrated scalars per market (alpha, beta) that shrink the
# noisy raw edge toward reality.
#
# Validated LOSO on 2023-2025 (705 games) vs the v1 engine:
#   margin: corr 0.342 / MAE 10.76  (v1: 0.330 / 10.80)
#   total:  corr 0.182 / MAE 10.51  (v1: 0.112 / 11.11)
# Neither beats the market line (corr 0.462 / 0.326) — no public-stat model
# does. The reference projection exists for display, monitoring, and as the
# carrier for validated situational edges (backup-QB overs) and live-only
# information (injury deltas, weather). Spread/total model picks stay
# monitor-only.
#
# Dropped from v1 on evidence: rest/travel adjustments (no residual signal
# vs closing lines in the 855-game factor battery), Kalman strength
# adjustment layer, ridge feature stack, self-tuned feature weights.

import math

import numpy as np
from scipy.stats import t as t_dist

from defaults import DEFAULT_W, SDIFF_THRESHOLDS, BACKUP_QB_OVER_RATE
from prob_calib import calibrated_prob
from power_ratings import rating_margin
from model_engine import (
    _resolve_team, _classify_confidence, _format_spread_pick,
    _build_injury_note, _extract_margin_features, build_feature_vector,
)

# Structural constants
PASS_RATE = 0.58          # league dropback share of plays
LEAGUE_PPP = 22.0 / 63.0  # baseline points per play (pre-shrinkage anchor)

# Fallback scale calibration — LOSO-stable values from 2023-2025.
# alpha ~ HCA in points; beta ~ shrinkage applied to the raw edge.
#
# The two margin paths live on different scales and are calibrated
# separately.  Joint power ratings come out natively in points, so their
# beta is ~1.0 and their alpha IS the home-field advantage; the structural
# EPA-rate form has an arbitrary scale and needs heavy shrinkage.
DEFAULT_SCALE = {
    # alpha on margin_ratings carries the home-field advantage (the raw
    # ratings margin is centered). Measured HFA 2023-2025: raw mean home
    # margin 2.41, and 2.39 from a ridge controlling for team strength --
    # two independent methods agreeing. Per-season it reads 2.92 / 2.28 /
    # 2.03, but each season carries +/-0.85 SE, so that apparent decline is
    # NOT significant (2023-vs-2025 t=0.75; linear trend t=-0.75). Treat
    # HFA as a constant ~2.4 until many more seasons say otherwise.
    "margin_ratings":    {"alpha": 2.67, "beta": 1.01},
    "margin_structural": {"alpha": 2.30, "beta": 0.38},
    "total":             {"alpha": 34.6, "beta": 0.25},
}

# Recency weighting for the scale fit: each season back counts this much.
# 1.0 = flat, which is what we use. Decay 0.5 was tested (to chase the
# apparent HFA decline) and REJECTED: the decline isn't statistically real,
# projection accuracy did not improve (corr 0.374 -> 0.371, MAE 10.66 ->
# 10.68), and it degraded ATS 52.2% -> 50.3% by firing more away picks.
# Kept as a knob so this can be revisited once there are enough seasons to
# actually detect a trend.
SEASON_DECAY = 1.0

NFL_T_DF = 5
KEY_NUMBERS = [3.0, 7.0, 10.0, 14.0]
KEY_NUMBER_DEADBAND = 1.5
KEY_NUMBER_DAMPENING = 0.92


# ---------------------------------------------------------------------------
# Scale calibration (walk-forward, like prob_calib)
# ---------------------------------------------------------------------------

def build_scale_calibration(runs, min_games=100, season_decay=SEASON_DECAY):
    """
    Fit the (alpha, beta) shrinkage per market from graded v2 history:
    margin ~ alpha + beta * rawMargin, total ~ alpha + beta * rawTotal.
    Falls back to DEFAULT_SCALE until min_games graded v2 games exist.
    """
    pairs = {"margin_ratings": [], "margin_structural": [], "total": []}
    for r in runs or []:
        if r.get("burnIn"):
            continue
        season = r.get("season") or 0
        for g in r.get("games", []):
            hs, as_ = g.get("homeScore"), g.get("awayScore")
            if not isinstance(hs, (int, float)) or not isinstance(as_, (int, float)):
                continue
            rm, rt = g.get("_v2RawMargin"), g.get("_v2RawTotal")
            if isinstance(rm, (int, float)):
                # Never mix the two margin scales in one fit
                src = ("margin_ratings" if g.get("_v2MarginSource") == "ratings"
                       else "margin_structural")
                pairs[src].append((rm, hs - as_, season))
            if isinstance(rt, (int, float)):
                pairs["total"].append((rt, hs + as_, season))

    def _fit(obs, default, beta_cap):
        if len(obs) < min_games:
            return dict(default)
        xs = np.array([p[0] for p in obs], dtype=float)
        ys = np.array([p[1] for p in obs], dtype=float)
        ss = np.array([p[2] for p in obs], dtype=float)
        # Recency weights: each season back counts SEASON_DECAY as much.
        w = season_decay ** np.maximum(0.0, ss.max() - ss)
        sw = w.sum()
        if sw <= 0:
            return dict(default)
        mx = float((w * xs).sum() / sw)
        my = float((w * ys).sum() / sw)
        vx = float((w * (xs - mx) ** 2).sum() / sw)
        if vx < 1e-9:
            return dict(default)
        beta = float((w * (xs - mx) * (ys - my)).sum() / sw / vx)
        beta = min(beta_cap, max(0.0, beta))   # shrink, never runaway
        alpha = float(my - beta * mx)
        return {"alpha": alpha, "beta": beta, "n": len(obs),
                "effN": round(float(sw), 1)}

    return {
        # ratings are already points-scaled, so beta ~1.0 is expected there
        "margin_ratings": _fit(pairs["margin_ratings"],
                               DEFAULT_SCALE["margin_ratings"], 1.5),
        "margin_structural": _fit(pairs["margin_structural"],
                                  DEFAULT_SCALE["margin_structural"], 1.0),
        "total": _fit(pairs["total"], DEFAULT_SCALE["total"], 1.0),
    }


# ---------------------------------------------------------------------------
# Structural projection
# ---------------------------------------------------------------------------

def _blend_epa(stats, off=True):
    if off:
        return (PASS_RATE * stats.get("passOffEPA", 0.0)
                + (1 - PASS_RATE) * stats.get("rushOffEPA", 0.0))
    return (PASS_RATE * stats.get("passDefEPA", 0.0)
            + (1 - PASS_RATE) * stats.get("rushDefEPA", 0.0))


def _league_avg_off(team_stats):
    vals = [_blend_epa(st) for st in team_stats.values()]
    return float(np.mean(vals)) if vals else 0.0


def raw_structural(home_st, away_st, league_off):
    """Raw (unshrunk) structural margin and total: EPA/play x plays."""
    plays = (home_st.get("pace", 63.0) + away_st.get("pace", 63.0)) / 2.0

    def team_pts(off_st, def_st):
        edge = ((_blend_epa(off_st) - league_off)
                + (_blend_epa(def_st, off=False) - league_off))
        return plays * (LEAGUE_PPP + edge)

    hp = team_pts(home_st, away_st)
    ap = team_pts(away_st, home_st)
    return hp - ap, hp + ap


# ---------------------------------------------------------------------------
# Main entry point — signature-compatible with model_engine.analyze_game
# ---------------------------------------------------------------------------

def analyze_game(game_data, team_stats, weights, kalman_states=None,
                 injury_deltas=None, residual_var=None, thresholds=None,
                 prob_calib=None, situational=None, scale_calib=None,
                 power_ratings=None):
    """
    Analyze one game with the structural v2 engine.

    kalman_states is accepted for signature compatibility and ignored —
    team stats are already exponentially decayed and the v1 Kalman
    adjustment layer showed no incremental value.
    """
    home_name = game_data.get("home")
    away_name = game_data.get("away")
    if not home_name or not away_name:
        return None

    home_key = _resolve_team(team_stats, home_name)
    away_key = _resolve_team(team_stats, away_name)
    if not home_key or not away_key:
        return None
    home_st, away_st = team_stats.get(home_key), team_stats.get(away_key)
    if not home_st or not away_st:
        return None

    weights = weights or {}
    inj = injury_deltas or {}
    inj_home = inj.get("home", 0.0) or 0.0
    inj_away = inj.get("away", 0.0) or 0.0

    # --- Raw projection ---
    # Margin prefers joint power ratings (opponent adjustment solved
    # simultaneously, small samples shrunk by the ridge penalty); it falls
    # back to the structural EPA-rate form early in the season before the
    # ratings have enough games. Totals always use the structural form —
    # ratings showed no gain there.
    league_off = _league_avg_off(team_stats)
    raw_margin, raw_total = raw_structural(home_st, away_st, league_off)
    margin_source = "structural"
    plays = (home_st.get("pace", 63.0) + away_st.get("pace", 63.0)) / 2.0
    if power_ratings:
        _rm = rating_margin(power_ratings, home_key, away_key, plays)
        if _rm is not None:
            raw_margin, margin_source = _rm, "ratings"

    # Backup-QB margin penalty: the team ratings don't know who is playing
    # quarterback, so a team starting someone other than the QB its rating
    # is built on is over-rated by ~3 pts. Uses the LOOSE qb_change flag
    # (falling back to the strict backup flag) because the rating stays
    # stale for the whole absence, not just the transition week. Applied to
    # the RAW margin (pre-scale) to match how it was validated.
    situational = situational or {}
    backup_qb_home = bool(situational.get("home_backup_qb"))
    backup_qb_away = bool(situational.get("away_backup_qb"))
    qb_change_home = bool(situational.get("home_qb_change", backup_qb_home))
    qb_change_away = bool(situational.get("away_qb_change", backup_qb_away))
    if qb_change_home != qb_change_away:   # both sides changed -> cancels
        from defaults import BACKUP_QB_MARGIN_PENALTY
        raw_margin += (-BACKUP_QB_MARGIN_PENALTY if qb_change_home
                       else BACKUP_QB_MARGIN_PENALTY)

    scale = scale_calib or DEFAULT_SCALE
    _mkey = "margin_ratings" if margin_source == "ratings" else "margin_structural"
    sm = scale.get(_mkey, DEFAULT_SCALE[_mkey])
    stt = scale.get("total", DEFAULT_SCALE["total"])

    proj_margin = sm["alpha"] + sm["beta"] * raw_margin + inj_home - inj_away
    proj_total = stt["alpha"] + stt["beta"] * raw_total

    # Weather (dome/outdoor adjustment, mainly totals)
    weather_adj = game_data.get("_weatherAdj", {}) or {}
    proj_total += weather_adj.get("total_adj", 0.0)

    # Backup-QB structural nudge on the total (the pick itself is
    # situational, below)
    if backup_qb_home or backup_qb_away:
        from defaults import BACKUP_QB_TOTAL_ADJ
        proj_total += BACKUP_QB_TOTAL_ADJ

    proj_margin = round(proj_margin * 10) / 10
    proj_total = round(proj_total * 10) / 10
    proj_home = round((proj_total + proj_margin) / 2 * 10) / 10
    proj_away = round((proj_total - proj_margin) / 2 * 10) / 10

    # --- Market comparison ---
    market_spread = game_data.get("line", 0.0)    # negative = home favored
    market_total = game_data.get("total", 0.0)
    margin_vs_line = proj_margin + market_spread
    s_diff = abs(margin_vs_line)
    t_diff = round((proj_total - market_total) * 10) / 10

    # --- Probabilities: raw parametric score for gating ---
    r_var = residual_var or weights.get("residual_var", 190.0)
    margin_std = math.sqrt(max(r_var, 4.0))
    p_home_cover = float(t_dist.cdf(margin_vs_line / margin_std, df=NFL_T_DF))
    for kn in KEY_NUMBERS:
        if abs(abs(margin_vs_line) - kn) < KEY_NUMBER_DEADBAND:
            p_home_cover = 0.5 + (p_home_cover - 0.5) * KEY_NUMBER_DAMPENING
            break
    p_away_cover = 1.0 - p_home_cover
    total_std = math.sqrt(max(r_var * 1.4, 4.0))
    p_over = float(t_dist.cdf(t_diff / total_std, df=NFL_T_DF)) if market_total > 0 else 0.5
    p_under = 1.0 - p_over

    # --- Empirical calibration for displayed probabilities ---
    p_home_cover_raw, p_away_cover_raw = p_home_cover, p_away_cover
    p_over_raw, p_under_raw = p_over, p_under
    prob_calibrated = False
    if prob_calib:
        _cp = calibrated_prob(prob_calib.get("spread"), margin_vs_line)
        if _cp is not None:
            p_home_cover, p_away_cover = _cp, 1.0 - _cp
            prob_calibrated = True
        if market_total > 0:
            _ct = calibrated_prob(prob_calib.get("total"), t_diff)
            if _ct is not None:
                p_over, p_under = _ct, 1.0 - _ct

    # --- Picks (raw-score gates; monitor-only product) ---
    thresh = thresholds or SDIFF_THRESHOLDS
    abs_line = abs(market_spread)
    home_fav = market_spread < 0

    # ONE threshold per market — no high/elite split. A game either clears
    # the bar and is a pick, or it doesn't. Tiering implied that "elite"
    # picks were better, which three seasons of grading never supported
    # (elite 53.1% vs high 52.5%), and it invited variable bet sizing on a
    # difference that was noise.
    s_pick, s_conf, p_cover = "PASS", "low", None
    best_spread_p = max(p_home_cover_raw, p_away_cover_raw)
    spread_side = "home" if p_home_cover_raw >= p_away_cover_raw else "away"
    prob_threshold = weights.get("probHigh", DEFAULT_W.get("probHigh", 0.57))
    if best_spread_p >= prob_threshold:
        s_pick = _format_spread_pick(home_name, away_name, spread_side, abs_line, home_fav)
        s_conf = "pick"
        p_cover = p_home_cover if spread_side == "home" else p_away_cover
    confidence_tier = s_conf

    o_pick, o_conf, p_ou = "PASS", "low", None
    best_total_p = max(p_over_raw, p_under_raw)
    ou_prob_high = weights.get("probOUHigh", DEFAULT_W.get("probOUHigh", 0.59))
    if best_total_p >= ou_prob_high and market_total > 0:
        o_pick = "OVER" if p_over_raw >= p_under_raw else "UNDER"
        o_conf = "pick"
        p_ou = p_over if o_pick == "OVER" else p_under

    # Situational totals pick: validated backup-QB OVER takes precedence
    situational_pick = None
    if (backup_qb_home or backup_qb_away) and market_total > 0:
        o_pick, o_conf = "OVER", "pick"
        p_ou = BACKUP_QB_OVER_RATE
        situational_pick = "backup_qb_over"

    injury_note = _build_injury_note(injury_deltas)

    # Compatibility artifacts for self_tune / backfill ridge collection
    margin_features = _extract_margin_features(
        home_st, away_st, is_home=True, inj_home=inj_home, inj_away=inj_away)
    _fv = build_feature_vector(home_st, away_st, is_home=True,
                               injury_delta_a=inj_home, injury_delta_b=inj_away)

    result = {
        "home": home_key,
        "away": away_key,
        "line": market_spread,
        "total": market_total,
        "hS": proj_home,
        "aS": proj_away,
        "pT": proj_total,
        "projSpread": -proj_margin,   # market convention: negative = home fav
        "margin": round(margin_vs_line * 10) / 10,
        "sDiff": round(s_diff * 10) / 10,
        "tDiff": t_diff,
        "sPick": s_pick,
        "sConf": s_conf,
        "oPick": o_pick,
        "oConf": o_conf,
        "confidenceTier": confidence_tier,
        "pHomeCover": round(p_home_cover * 1000) / 1000,
        "pAwayCover": round(p_away_cover * 1000) / 1000,
        "pOver": round(p_over * 1000) / 1000,
        "pUnder": round(p_under * 1000) / 1000,
        "pCover": round(p_cover * 1000) / 1000 if p_cover is not None else None,
        "pOU": round(p_ou * 1000) / 1000 if p_ou is not None else None,
        "pHomeCoverRaw": round(p_home_cover_raw * 1000) / 1000,
        "pOverRaw": round(p_over_raw * 1000) / 1000,
        "probCalibrated": prob_calibrated,
        "marginVar": round(r_var * 10) / 10,
        "marginStd": round(margin_std * 10) / 10,
        "residualVar": round(r_var * 10) / 10,
        "injuryNote": injury_note,
        "injuryDeltaHome": round(inj_home * 100) / 100,
        "injuryDeltaAway": round(inj_away * 100) / 100,
        "backupQBHome": backup_qb_home,
        "backupQBAway": backup_qb_away,
        "situationalPick": situational_pick,
        "engine": "v2",
        "_v2RawMargin": round(raw_margin * 100) / 100,
        "_v2RawTotal": round(raw_total * 100) / 100,
        "_v2MarginSource": margin_source,
        "_marginFeatures": margin_features,
        "_features": _fv.tolist() if hasattr(_fv, "tolist") else list(_fv),
    }
    for k in ("date", "week", "gameId", "homeScore", "awayScore"):
        if k in game_data:
            result[k] = game_data[k]
    return result
