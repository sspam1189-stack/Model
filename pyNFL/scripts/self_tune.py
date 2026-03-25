# pyNFL/scripts/self_tune.py
# Standalone NFL Bayesian weight tuner.
#
# This is a full reimplementation for NFL weight keys. We do NOT import
# tune_weights from core/self_tune.py because that module hard-codes NBA
# feature names (wTS, wTO, wORR, wNET / dTS, dTO, dORR, dNET).
#
# compute_residual_var is sport-agnostic, so we still import it from core.

import sys
import os
import math
import re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import defaults

# Import the sport-agnostic residual variance helper from core
import core.self_tune as _core_tune

_core_tune._configure(defaults)
_core_tune.HCA_CLAMP_LO = defaults.HCA_CLAMP_LO
_core_tune.HCA_CLAMP_HI = defaults.HCA_CLAMP_HI
_core_tune.HCA_VAR_FLOOR = defaults.HCA_VAR_FLOOR

compute_residual_var = _core_tune.compute_residual_var

# ---------------------------------------------------------------------------
# NFL-specific config pulled from defaults
# ---------------------------------------------------------------------------

_DEFAULT_W = defaults.DEFAULT_W
_DEFAULT_W_VAR = defaults.DEFAULT_W_VAR
_BAYES_HYPER = defaults.BAYES_HYPER

HCA_CLAMP_LO = defaults.HCA_CLAMP_LO
HCA_CLAMP_HI = defaults.HCA_CLAMP_HI
HCA_VAR_FLOOR = defaults.HCA_VAR_FLOOR


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    chosen_is_home = p["team"] == g.get("home")
    margin = (g["homeScore"] - g["awayScore"]) if chosen_is_home else (g["awayScore"] - g["homeScore"])
    val = margin + p["pts"] if p["sign"] == "+" else margin - p["pts"]
    if val == 0:
        return "PUSH"
    return "WIN" if val > 0 else "LOSS"


def _grade_total(g):
    if not g.get("oPick") or g["oPick"] == "PASS":
        return None
    actual = g["homeScore"] + g["awayScore"]
    if actual == g["total"]:
        return "PUSH"
    if g["oPick"] == "OVER":
        return "WIN" if actual > g["total"] else "LOSS"
    return "WIN" if actual < g["total"] else "LOSS"


def _r4(x):
    return round(x * 10000) / 10000


def _r3(x):
    return round(x * 1000) / 1000


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _is_finite(x):
    if x is None:
        return False
    try:
        return math.isfinite(x)
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# NFL weight keys for the Bayesian margin update (Signal 1)
# ---------------------------------------------------------------------------
# These map to _marginFeatures produced by model_engine._extract_margin_features.
#   weight key  ->  margin feature key
#   wPassOff    ->  dPassOff
#   wRushOff    ->  dRushOff
#   wPassDef    ->  dPassDef
#   wRushDef    ->  dRushDef
#   wPace       ->  dPace
#   wRZ         ->  dRZ
#   hfa         ->  hca
#
# Additional features that appear in _marginFeatures but are NOT weights
# (they are read-only inputs): injHome, injAway, _baseline

_WEIGHT_KEYS = ["wPassOff", "wRushOff", "wPassDef", "wRushDef", "wPace", "wRZ", "hfa"]
_FEATURE_KEYS = ["dPassOff", "dRushOff", "dPassDef", "dRushDef", "dPace", "dRZ", "hca"]

# EPA weight clamp range (all non-HFA ridge weights)
_EPA_CLAMP_LO = 0
_EPA_CLAMP_HI = 10


# ---------------------------------------------------------------------------
# Main tuner
# ---------------------------------------------------------------------------

def tune_weights(current_w, current_w_var, completed_rows):
    """
    NFL-specific Bayesian weight tuner.

    Three signals:
      1. Bayesian posterior update on margin prediction weights
      2. Profitability threshold tuning (probHigh, sprHigh, etc.)
      3. Gradient descent on additive constant using total prediction error

    Returns: {"W": dict, "W_var": dict}
    """
    W = {**_DEFAULT_W, **(current_w or {})}
    W_var = {**_DEFAULT_W_VAR, **(current_w_var or {})}

    # Heal NaN / None values
    for k in list(W.keys()):
        if W[k] is None or (isinstance(W[k], float) and math.isnan(W[k])):
            W[k] = _DEFAULT_W.get(k, W[k])
    for k in list(W_var.keys()):
        if W_var[k] is None or (isinstance(W_var[k], float) and math.isnan(W_var[k])):
            W_var[k] = _DEFAULT_W_VAR.get(k, W_var[k])

    margin_noise = _BAYES_HYPER["marginNoise"]
    min_var = _BAYES_HYPER["minWeightVar"]
    max_var = _BAYES_HYPER["maxWeightVar"]

    # ==================================================================
    # SIGNAL 1: Bayesian weight update on NFL margin features
    # ==================================================================

    n_bayes = 0

    for r in completed_rows:
        home_score = r.get("homeScore")
        away_score = r.get("awayScore")
        if not _is_finite(home_score) or not _is_finite(away_score):
            continue

        mf = r.get("_marginFeatures")
        if not mf:
            continue

        # Build feature vector from margin features dict
        x = [mf.get(fk, 0) for fk in _FEATURE_KEYS]

        baseline = mf.get("_baseline", 0)
        prediction = baseline
        for i in range(len(_WEIGHT_KEYS)):
            prediction += W[_WEIGHT_KEYS[i]] * x[i]

        actual_margin = home_score - away_score
        error = actual_margin - prediction

        recency_w = r.get("_recencyWeight", 1.0)
        if not _is_finite(recency_w):
            recency_w = 1.0
        effective_noise = margin_noise / recency_w

        # Innovation variance S = noise + sum(x_i^2 * var_i)
        S = effective_noise
        for i in range(len(_WEIGHT_KEYS)):
            S += x[i] * x[i] * W_var[_WEIGHT_KEYS[i]]

        # Kalman-style update for each weight
        for i in range(len(_WEIGHT_KEYS)):
            k = _WEIGHT_KEYS[i]
            K = W_var[k] * x[i] / S
            W[k] = _r3(W[k] + K * error)
            floor = HCA_VAR_FLOOR if k == "hfa" else min_var
            W_var[k] = _r4(_clamp(W_var[k] * (1 - K * x[i]), floor, max_var))

        n_bayes += 1

    # Clamp NFL weights: EPA weights 0-10, hfa within HCA bounds
    for k in _WEIGHT_KEYS:
        if k == "hfa":
            W[k] = _clamp(W[k], HCA_CLAMP_LO, HCA_CLAMP_HI)
        else:
            W[k] = _clamp(W[k], _EPA_CLAMP_LO, _EPA_CLAMP_HI)

    if n_bayes > 0:
        print(f"  [self_tune] Bayesian update on {n_bayes} games ->"
              f" wPassOff={W['wPassOff']} (v={W_var['wPassOff']:.3f})"
              f" wRushOff={W['wRushOff']} (v={W_var['wRushOff']:.3f})"
              f" wPassDef={W['wPassDef']} (v={W_var['wPassDef']:.3f})"
              f" wRushDef={W['wRushDef']} (v={W_var['wRushDef']:.3f})"
              f" hfa={W['hfa']} (v={W_var['hfa']:.3f})")

    # ==================================================================
    # SIGNAL 2: Profitability -> probability threshold tuning
    # ==================================================================

    spr_w = 0
    spr_l = 0
    ou_ew = 0
    ou_el = 0

    for r in completed_rows:
        if not _is_finite(r.get("homeScore")) or not _is_finite(r.get("awayScore")):
            continue

        if r.get("sPick") and r["sPick"] != "PASS" and r.get("sConf") == "high":
            res = r.get("sResult") or _grade_spread(r)
            if res == "WIN":
                spr_w += 1
            elif res == "LOSS":
                spr_l += 1

        if r.get("oPick") and r["oPick"] != "PASS" and r.get("oConf") == "elite":
            res = r.get("oResult") or _grade_total(r)
            if res == "WIN":
                ou_ew += 1
            elif res == "LOSS":
                ou_el += 1

    MIN_SAMPLE = 10
    thresh_step = 0.008

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

    # Legacy thresholds sync
    if spr_w + spr_l >= MIN_SAMPLE:
        spr_pct = spr_w / (spr_w + spr_l)
        if spr_pct > 0.58:
            W["sprHigh"] = _r3(max(2.5, W["sprHigh"] - 0.1))
        elif spr_pct < 0.52:
            W["sprHigh"] = _r3(min(8, W["sprHigh"] + 0.15))
    if ou_ew + ou_el >= MIN_SAMPLE:
        ou_pct = ou_ew / (ou_ew + ou_el)
        if ou_pct > 0.58:
            W["ouHigh"] = _r3(max(3, W["ouHigh"] - 0.1))
        elif ou_pct < 0.52:
            W["ouHigh"] = _r3(min(10, W["ouHigh"] + 0.15))

    # ==================================================================
    # SIGNAL 3: Constant (gradient descent on total prediction error)
    # ==================================================================

    lr = 0.0005
    max_step = 0.08

    g_constant = 0
    n = 0

    for r in completed_rows:
        if not _is_finite(r.get("homeScore")) or not _is_finite(r.get("awayScore")):
            continue
        if not _is_finite(r.get("pT")):
            continue

        w = r.get("_recencyWeight", 1.0)
        if not _is_finite(w):
            w = 1.0
        actual_total = r["homeScore"] + r["awayScore"]
        err_total = r["pT"] - actual_total

        g_constant += w * err_total * 2
        n += w

    if n > 0:
        g_constant /= n

        def clamp_step(x):
            return max(-max_step, min(max_step, x))

        W["constant"] = _r3(W["constant"] - clamp_step(lr * g_constant))

    # ==================================================================
    # SIGNAL 4: Adaptive Kalman parameters (gameNoise, dailyDrift)
    # ==================================================================
    # Adapt gameNoise based on rolling prediction error variance.
    # If actual errors are larger than assumed noise, increase it (and vice versa).

    KALMAN_ADAPT_RATE = 0.15
    MIN_GAMES_FOR_KALMAN = 10

    margin_errors = []
    for r in completed_rows:
        if (not _is_finite(r.get("homeScore")) or not _is_finite(r.get("awayScore"))
                or not _is_finite(r.get("pH")) or not _is_finite(r.get("pA"))):
            continue
        actual_margin = r["homeScore"] - r["awayScore"]
        pred_margin = r["pH"] - r["pA"]
        margin_errors.append(actual_margin - pred_margin)

    if len(margin_errors) >= MIN_GAMES_FOR_KALMAN:
        import numpy as _np
        empirical_game_noise = float(_np.var(margin_errors))
        current_noise = W.get("_kalman_gameNoise", _BAYES_HYPER.get("marginNoise", 225))
        new_noise = (1 - KALMAN_ADAPT_RATE) * current_noise + KALMAN_ADAPT_RATE * empirical_game_noise
        W["_kalman_gameNoise"] = _r3(max(100, min(400, new_noise)))

        # Adapt dailyDrift: if predictions consistently biased, increase drift
        mean_error = float(_np.mean(margin_errors))
        current_drift = W.get("_kalman_dailyDrift", 0.5)
        if abs(mean_error) > 2.0:
            # Predictions are consistently biased — teams are changing faster
            W["_kalman_dailyDrift"] = _r3(min(2.0, current_drift * 1.15))
        elif abs(mean_error) < 0.5:
            # Well-calibrated — slightly reduce drift
            W["_kalman_dailyDrift"] = _r3(max(0.2, current_drift * 0.95))

    # Final guard: heal any NaN / None that crept in
    for k in list(W.keys()):
        if W[k] is None or (isinstance(W[k], float) and math.isnan(W[k])):
            W[k] = _DEFAULT_W.get(k, 1)
    for k in list(W_var.keys()):
        if W_var[k] is None or (isinstance(W_var[k], float) and math.isnan(W_var[k])):
            W_var[k] = _DEFAULT_W_VAR.get(k, 1)

    return {"W": W, "W_var": W_var}
