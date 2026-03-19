# scripts/self_tune.py
# --------------------------------------------------------------------------
# BAYESIAN UPGRADE: Two learning signals, now principled.
#
#   SIGNAL 1: BAYESIAN WEIGHT UPDATE
#     Replaces gradient descent. Maintains a mean + variance for each weight.
#     After each game, the weight posterior is updated via a diagonal Kalman
#     filter (equivalent to diagonal-covariance Bayesian linear regression).
#     Weights with high uncertainty move more; confident weights move less.
#
#   SIGNAL 2: PROBABILITY THRESHOLD TUNING
#     Replaces sprHigh/ouHigh. Adjusts probHigh/probElite based on ATS
#     profitability. If we're winning at >55%, relax thresholds (more picks).
#     If losing at <48%, tighten thresholds (fewer, more confident picks).
#
#   SIGNAL 3: CONSTANT + PACE (gradient descent, unchanged)
#     These are non-linear in the projection formula (constant affects total
#     not margin, paceAdj is a multiplier). Gradient descent is fine for them.
#
# NCAA adaptation: HCA clamp range [2.0, 6.0], HCA variance floor 0.3.
# --------------------------------------------------------------------------

import math
import re

from defaults import DEFAULT_W, DEFAULT_W_VAR, BAYES_HYPER


# -- Helpers ---------------------------------------------------------------

def _parse_spread_pick(pick):
    if not pick or pick == "PASS":
        return None
    m = re.match(r"(.+?)\s+([+-])(\d+(?:\.\d+)?)", pick)
    if m:
        return {"team": m.group(1).strip(), "sign": m.group(2), "pts": float(m.group(3))}
    return None


def _grade_spread(g):
    p = _parse_spread_pick(g.get("sPick"))
    if not p:
        return None
    chosen_is_home = p["team"] == g["home"]
    margin = g["homeScore"] - g["awayScore"] if chosen_is_home else g["awayScore"] - g["homeScore"]
    val = margin + p["pts"] if p["sign"] == "+" else margin - p["pts"]
    if val == 0:
        return "PUSH"
    return "WIN" if val > 0 else "LOSS"


def _grade_total(g):
    o_pick = g.get("oPick")
    if not o_pick or o_pick == "PASS":
        return None
    actual = g["homeScore"] + g["awayScore"]
    if actual == g["total"]:
        return "PUSH"
    if o_pick == "OVER":
        return "WIN" if actual > g["total"] else "LOSS"
    return "WIN" if actual < g["total"] else "LOSS"


def _r4(x):
    return round(x * 10000) / 10000


def _r3(x):
    return round(x * 1000) / 1000


def _clamp(x, mn, mx):
    return max(mn, min(mx, x))


# -- Main Tuner -----------------------------------------------------------
#
# Parameters:
#   current_w:       current weight means { "wTS", "wTO", "wORR", "wNET", "hca", "constant", "paceAdj", ... }
#   current_w_var:   current weight variances { "wTS", "wTO", "wORR", "wNET", "hca", "constant" }
#   completed_rows:  array of graded game objects with _marginFeatures, homeScore, awayScore
#
# Returns: { "W": ..., "W_var": ... } — updated weight means and variances

def tune_weights(current_w, current_w_var, completed_rows):
    W = {**DEFAULT_W, **(current_w or {})}
    W_var = {**DEFAULT_W_VAR, **(current_w_var or {})}

    # Replace any None/NaN with defaults
    for k in list(W.keys()):
        if W[k] is None or (isinstance(W[k], float) and math.isnan(W[k])):
            W[k] = DEFAULT_W.get(k, W[k])
    for k in list(W_var.keys()):
        if W_var[k] is None or (isinstance(W_var[k], float) and math.isnan(W_var[k])):
            W_var[k] = DEFAULT_W_VAR.get(k, W_var[k])

    margin_noise = BAYES_HYPER["marginNoise"]
    min_var = BAYES_HYPER["minWeightVar"]
    max_var = BAYES_HYPER["maxWeightVar"]

    # ==================================================================
    # SIGNAL 1: Bayesian weight update (replaces gradient descent)
    # ==================================================================
    #
    # For each graded game with margin features, update the weight posterior.
    #
    # Model: actual_margin = baseline + w*x + noise
    #   where x = [dTS*p, -dTO*p, dORR*p, 0.5*dNET*p, hca_indicator]
    #   and baseline = ((hOFF+aDEF)/2 - (aOFF+hDEF)/2) * pace
    #
    # Diagonal Kalman update:
    #   prediction = baseline + dot(W_mean, x)
    #   error = actual_margin - prediction
    #   S = sum(x_i^2 * var_i) + marginNoise
    #   K_i = var_i * x_i / S
    #   W_mean_i += K_i * error
    #   W_var_i  *= (1 - K_i * x_i)

    WEIGHT_KEYS = ["wTS", "wTO", "wORR", "wNET", "hca"]
    n_bayes = 0

    for r in completed_rows:
        home_score = r.get("homeScore")
        away_score = r.get("awayScore")
        if not _is_finite(home_score) or not _is_finite(away_score):
            continue

        mf = r.get("_marginFeatures")
        if not mf:
            continue

        # Feature vector (same order as WEIGHT_KEYS)
        x = [mf["dTS"], mf["dTO"], mf["dORR"], mf["dNET"], mf["hca"]]

        # Prediction from current weights
        baseline = mf.get("_baseline", 0)
        prediction = baseline
        for i in range(len(WEIGHT_KEYS)):
            prediction += W[WEIGHT_KEYS[i]] * x[i]

        actual_margin = home_score - away_score
        error = actual_margin - prediction

        # Recency weighting: down-weight older games
        recency_w = r.get("_recencyWeight", 1.0)
        if not _is_finite(recency_w):
            recency_w = 1.0
        effective_noise = margin_noise / recency_w  # lower noise = more influence

        # Innovation variance
        S = effective_noise
        for i in range(len(WEIGHT_KEYS)):
            S += x[i] * x[i] * W_var[WEIGHT_KEYS[i]]

        # Update each weight
        for i in range(len(WEIGHT_KEYS)):
            k = WEIGHT_KEYS[i]
            K = W_var[k] * x[i] / S

            W[k] = _r3(W[k] + K * error)
            # HCA is well-studied — use a higher variance floor so it moves slower
            # NCAA HCA variance floor is 0.3 (vs 0.2 in NBA) since college HCA varies more
            floor = 0.3 if k == "hca" else min_var
            W_var[k] = _r4(_clamp(W_var[k] * (1 - K * x[i]), floor, max_var))

        n_bayes += 1

    # Clamp weight means to reasonable ranges
    W["wTS"] = _clamp(W["wTS"], 0, 5)
    W["wTO"] = _clamp(W["wTO"], 0, 5)
    W["wORR"] = _clamp(W["wORR"], 0, 5)
    W["wNET"] = _clamp(W["wNET"], 0, 5)
    # NCAA HCA range: [2.0, 6.0] — college HCA is higher and more variable than NBA
    W["hca"] = _clamp(W["hca"], 2.0, 6.0)

    if n_bayes > 0:
        print(f"  [self_tune] Bayesian update on {n_bayes} games ->"
              f" wTS={W['wTS']} (s2={W_var['wTS']:.3f})"
              f" wTO={W['wTO']} (s2={W_var['wTO']:.3f})"
              f" wNET={W['wNET']} (s2={W_var['wNET']:.3f})"
              f" hca={W['hca']} (s2={W_var['hca']:.3f})")

    # ==================================================================
    # SIGNAL 2: Profitability -> probability threshold tuning
    # ==================================================================
    #
    # Single tier (elite removed — calibration showed it was overconfident).
    # Adjust probHigh based on ATS profitability of all actionable picks.

    spr_w = 0
    spr_l = 0
    ou_ew = 0
    ou_el = 0

    for r in completed_rows:
        home_score = r.get("homeScore")
        away_score = r.get("awayScore")
        if not _is_finite(home_score) or not _is_finite(away_score):
            continue

        s_pick = r.get("sPick")
        if s_pick and s_pick != "PASS" and r.get("sConf") == "high":
            res = r.get("sResult") or _grade_spread(r)
            if res == "WIN":
                spr_w += 1
            elif res == "LOSS":
                spr_l += 1

        o_pick = r.get("oPick")
        if o_pick and o_pick != "PASS" and r.get("oConf") == "elite":
            res = r.get("oResult") or _grade_total(r)
            if res == "WIN":
                ou_ew += 1
            elif res == "LOSS":
                ou_el += 1

    MIN_SAMPLE = 10
    thresh_step = 0.008  # probability step (~0.8% per adjustment)

    # Spread probability threshold (single tier)
    if spr_w + spr_l >= MIN_SAMPLE:
        spr_pct = spr_w / (spr_w + spr_l)
        if spr_pct > 0.58:
            W["probHigh"] = _r3(max(0.52, W["probHigh"] - thresh_step * 0.67))
            print(f"  [self_tune] Spread ATS {spr_pct*100:.0f}% ({spr_w}-{spr_l}) > 58% -> probHigh down {W['probHigh']}")
        elif spr_pct < 0.52:
            W["probHigh"] = _r3(min(0.65, W["probHigh"] + thresh_step))
            print(f"  [self_tune] Spread ATS {spr_pct*100:.0f}% ({spr_w}-{spr_l}) < 52% -> probHigh up {W['probHigh']}")
        else:
            print(f"  [self_tune] Spread ATS {spr_pct*100:.0f}% ({spr_w}-{spr_l}) -- probHigh holds at {W['probHigh']}")
    else:
        print(f"  [self_tune] Spread: only {spr_w + spr_l} graded picks (need {MIN_SAMPLE}) -- probHigh unchanged")

    # Total: elite threshold only (high totals removed — not profitable)
    if ou_ew + ou_el >= MIN_SAMPLE:
        pct = ou_ew / (ou_ew + ou_el)
        if pct > 0.58:
            W["probOUElite"] = _r3(max(0.59, W["probOUElite"] - thresh_step * 0.67))
            print(f"  [self_tune] Total ELITE {pct*100:.0f}% ({ou_ew}-{ou_el}) > 58% -> probOUElite down {W['probOUElite']}")
        elif pct < 0.52:
            W["probOUElite"] = _r3(min(0.80, W["probOUElite"] + thresh_step))
            print(f"  [self_tune] Total ELITE {pct*100:.0f}% ({ou_ew}-{ou_el}) < 52% -> probOUElite up {W['probOUElite']}")
        else:
            print(f"  [self_tune] Total ELITE {pct*100:.0f}% ({ou_ew}-{ou_el}) -- probOUElite holds at {W['probOUElite']}")
    else:
        print(f"  [self_tune] Total ELITE: only {ou_ew + ou_el} graded picks (need {MIN_SAMPLE}) -- probOUElite unchanged")

    # Also keep legacy thresholds in sync (for backward compat / display)
    # sprHigh/ouHigh still used by backfill and recalculate
    ou_w_total = ou_ew
    ou_l_total = ou_el
    if spr_w + spr_l >= MIN_SAMPLE:
        spr_pct = spr_w / (spr_w + spr_l)
        if spr_pct > 0.58:
            W["sprHigh"] = _r3(max(2.5, W["sprHigh"] - 0.1))
        elif spr_pct < 0.52:
            W["sprHigh"] = _r3(min(8, W["sprHigh"] + 0.15))
    if ou_w_total + ou_l_total >= MIN_SAMPLE:
        ou_pct = ou_w_total / (ou_w_total + ou_l_total)
        if ou_pct > 0.58:
            W["ouHigh"] = _r3(max(3, W["ouHigh"] - 0.1))
        elif ou_pct < 0.52:
            W["ouHigh"] = _r3(min(10, W["ouHigh"] + 0.15))

    # ==================================================================
    # SIGNAL 3: Constant + paceAdj (gradient descent, unchanged)
    # ==================================================================
    #
    # These affect the total (hS + aS) not the margin, and paceAdj is a
    # multiplier, so they don't fit cleanly into the linear Bayesian update.
    # Gradient descent is fine for these two parameters.

    lr = 0.0005
    max_step = 0.08

    g_constant = 0
    g_pace_adj = 0
    n = 0

    for r in completed_rows:
        home_score = r.get("homeScore")
        away_score = r.get("awayScore")
        if not _is_finite(home_score) or not _is_finite(away_score):
            continue
        p_t = r.get("pT")
        if not _is_finite(p_t):
            continue

        w = r.get("_recencyWeight", 1.0)
        if not _is_finite(w):
            w = 1.0
        actual_total = home_score + away_score
        err_total = p_t - actual_total

        g_constant += w * err_total * 2
        g_pace_adj += w * err_total * (p_t / max(W.get("paceAdj", 1), 0.1)) * 0.01
        n += w

    if n > 0:
        g_constant /= n
        g_pace_adj /= n

        def clamp_step(x):
            return max(-max_step, min(max_step, x))

        W["constant"] = _r3(W["constant"] - clamp_step(lr * g_constant))
        W["paceAdj"] = _r3(_clamp(W["paceAdj"] - clamp_step(lr * g_pace_adj), 0.5, 1.5))

    # -- Final guard -------------------------------------------------------
    for k in list(W.keys()):
        if W[k] is None or (isinstance(W[k], float) and math.isnan(W[k])):
            W[k] = DEFAULT_W.get(k, 1)
    for k in list(W_var.keys()):
        if W_var[k] is None or (isinstance(W_var[k], float) and math.isnan(W_var[k])):
            W_var[k] = DEFAULT_W_VAR.get(k, 1)

    return {"W": W, "W_var": W_var}


# -- Dynamic Residual Variance --------------------------------------------
# Computes the actual prediction error variance from historical results.
# This replaces the hardcoded BAYES_HYPER["residualVar"] with reality.
#
# residual_var = Var(actual_margin - projected_margin)
#
# Uses all graded games from store.runs (excluding burn-in).
# Falls back to BAYES_HYPER["residualVar"] if insufficient data.

def compute_residual_var(runs):
    MIN_GAMES = 30  # need enough data for stable estimate
    errors = []

    for r in runs:
        if r.get("burnIn"):
            continue
        for g in r.get("games", []):
            home_score = g.get("homeScore")
            away_score = g.get("awayScore")
            if not _is_finite(home_score) or not _is_finite(away_score):
                continue
            margin = g.get("margin")
            line = g.get("line")
            if not _is_finite(margin) or not _is_finite(line):
                continue

            actual_edge = (home_score - away_score) - line
            proj_edge = margin  # hS - aS - line
            errors.append(actual_edge - proj_edge)

    if len(errors) < MIN_GAMES:
        print(f"  [self_tune] residualVar: only {len(errors)} games (need {MIN_GAMES}) -- using default {BAYES_HYPER['residualVar']}")
        return BAYES_HYPER["residualVar"]

    mean = sum(errors) / len(errors)
    variance = sum((x - mean) ** 2 for x in errors) / len(errors)

    rounded = round(variance * 10) / 10

    print(f"  [self_tune] residualVar: {rounded} (std={math.sqrt(variance):.1f}) from {len(errors)} games")
    return rounded


# -- Utility ---------------------------------------------------------------

def _is_finite(x):
    """Check if a value is a finite number (not None, not NaN, not inf)."""
    if x is None:
        return False
    try:
        return math.isfinite(x)
    except (TypeError, ValueError):
        return False
