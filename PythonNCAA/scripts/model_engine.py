# scripts/model_engine.py
# --------------------------------------------------------------------------
# BAYESIAN UPGRADE: proj_score now returns { "score": ..., "variance": ... }.
# analyze_game computes P(cover) from the projected distribution and uses
# probability thresholds instead of raw edge thresholds for pick logic.
#
# Backward compatible: still outputs s_diff, margin, etc. for display/grading.
# The old threshold-based logic is kept as a fallback if Kalman state is null.
#
# NCAA adaptation: no team aliases (362 D1 teams — use fuzzy matching only),
# higher HCA default (4.0), MIN_GP = 5, SDIFF_CAP = 12.
# --------------------------------------------------------------------------

import math
import re

from defaults import DEFAULT_STATS, DEFAULT_W, DEFAULT_W_VAR, BAYES_HYPER


def load_defaults():
    return {
        "DEFAULT_STATS": DEFAULT_STATS,
        "DEFAULT_W": DEFAULT_W,
        "DEFAULT_W_VAR": DEFAULT_W_VAR,
        "BAYES_HYPER": BAYES_HYPER,
    }


def get_avgs(H):
    teams = list(H.values())
    n = len(teams) or 1
    return {
        "ts": sum(x["TS"] for x in teams) / n,
        "to": sum(x["TO"] for x in teams) / n,
        "orr": sum(x["ORR"] for x in teams) / n,
    }


# -- Normal CDF (Abramowitz & Stegun approximation, ~1e-5 accuracy) --------

def normal_cdf(x):
    a1 = 0.254829592
    a2 = -0.284496736
    a3 = 1.421413741
    a4 = -1.453152027
    a5 = 1.061405429
    p = 0.3275911
    sign = -1 if x < 0 else 1
    z = abs(x) / math.sqrt(2)
    t = 1.0 / (1.0 + p * z)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-z * z)
    return 0.5 * (1.0 + sign * y)


# -- Team Name Resolution -------------------------------------------------
# NCAA has 362 D1 teams — no hardcoded alias map. Fuzzy matching only.

def _norm_key(s):
    s = str(s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


# Collapse abbreviation dots only: "N.C. State" -> "NC State", "St." -> "St"
def _collapse_abbr(s):
    return s.replace(".", "")


TEAM_NAME_ALIASES = {}


def _expand_team_name(name):
    n = _norm_key(name)
    return TEAM_NAME_ALIASES.get(n, name)


# Safe substring check: only match if lengths are similar (within 30%)
# This prevents "arkansas" matching "kansas", "oregon" matching "oregon st", etc.
def _safe_fuzzy(a, b):
    if not a or not b:
        return False
    if a == b:
        return True
    shorter = a if len(a) < len(b) else b
    longer = b if len(a) < len(b) else a
    if len(shorter) / len(longer) < 0.85:
        return False
    return shorter in longer


def _resolve_team(H, name):
    if not H or not name:
        return None
    if name in H:
        return name
    keys = list(H.keys())
    wanted = _norm_key(name)
    wanted_collapsed = _norm_key(_collapse_abbr(name))

    # Exact normKey match
    for k in keys:
        if _norm_key(k) == wanted:
            return k
    # Collapsed abbreviation match: "N.C. State" <-> "NC State"
    for k in keys:
        if _norm_key(_collapse_abbr(k)) == wanted_collapsed:
            return k
    expanded = _norm_key(_expand_team_name(name))
    for k in keys:
        if _norm_key(k) == expanded:
            return k
    for k in keys:
        nk = _norm_key(k)
        if _safe_fuzzy(nk, wanted) or _safe_fuzzy(nk, expanded):
            return k
    # "School Mascot" prefix match: odds API sends "UMBC Retrievers", cache has "UMBC"
    # Find the LONGEST matching prefix to avoid "Oregon" beating "Oregon St."
    best_prefix = None
    best_len = 0
    for k in keys:
        nk = _norm_key(k)
        nkc = _norm_key(_collapse_abbr(k))
        match_nk = wanted.startswith(nk + " ") or wanted_collapsed.startswith(nk + " ")
        match_nkc = wanted_collapsed.startswith(nkc + " ")
        if (match_nk or match_nkc) and len(nk) >= 3:
            length = max(len(nk), len(nkc))
            if length > best_len:
                best_len = length
                best_prefix = k
    if best_prefix:
        return best_prefix
    return None


# -- Team-specific Home Court Advantage ------------------------------------
# Computes per-team HCA from home/away splits: ((homeNET) - (awayNET)) / 2
# Blended 50/50 with league average to stabilize small-sample splits.
# Returns a map: { "Team Name": hca_value, ... }

def compute_team_hca(home_splits, away_splits, league_hca=4.0):
    if not home_splits or not away_splits:
        return None
    hca_map = {}
    for team in home_splits:
        h = home_splits[team]
        a = away_splits.get(team)
        if not h or not a or not h.get("GP") or not a.get("GP") or h["GP"] < 10 or a["GP"] < 10:
            continue
        home_net = h["OFF"] - h["DEF"]
        away_net = a["OFF"] - a["DEF"]
        raw_hca = (home_net - away_net) / 2
        # Blend 50/50 with league average to prevent overfitting
        hca_map[team] = raw_hca * 0.5 + league_hca * 0.5
    return hca_map


# -- Projection ------------------------------------------------------------
# Returns { "score": ..., "variance": ... } instead of a single number.
#
# The score is the same formula as before.
# The variance is the sum of:
#   - Kalman team uncertainty (if provided)
#   - Weight uncertainty propagated through features (if W_var provided)
#   - Residual game noise
#
# Parameters:
#   kalman_adj: { "mean": ..., "var": ... } from kalman_state.get_team_adj() — optional
#   W_var:      weight variances { "wTS", "wTO", "wORR", "wNET", "hca" } — optional
#   team_hca:   per-team HCA map from compute_team_hca() — optional

def proj_score(team, opp, is_home, H, a, W, kalman_adj=None, W_var=None, residual_var=None, team_hca=None):
    t_key = _resolve_team(H, team)
    o_key = _resolve_team(H, opp)

    t = H.get(t_key) if t_key else None
    o = H.get(o_key) if o_key else None
    if not t or not o:
        return None

    MIN_GP = 5
    if (t.get("GP") is not None and t["GP"] < MIN_GP) or (o.get("GP") is not None and o["GP"] < MIN_GP):
        return None

    t_off = t["OFF"]
    t_def = t["DEF"]

    # -- Point estimate (same formula as before) ---------------------------

    base = (
        (t_off + o["DEF"]) / 2
        + (t["TS"] - a["ts"]) * W["wTS"]
        - (t["TO"] - a["to"]) * W["wTO"]
        + (t["ORR"] - a["orr"]) * W["wORR"]
        + (W["wNET"] * 0.5) * ((t_off - t_def) - (o["OFF"] - o["DEF"]))
        + W["constant"]
    )

    pace = (((t["PACE"] + o["PACE"]) / 2) * W["paceAdj"]) / 100
    hca = (team_hca.get(t_key, W["hca"]) if team_hca else W["hca"]) if is_home else 0
    score = base * pace + hca

    # Add Kalman adjustment if available
    if kalman_adj:
        score += kalman_adj["mean"]

    score = round(score * 10) / 10

    # -- Variance propagation -----------------------------------------------
    # Dynamic residual_var is total margin noise (measured from margin errors).
    # Split in half per team since analyze_game sums home + away variance.
    # Default (BAYES_HYPER) is already calibrated as per-team value.

    variance = residual_var / 2 if residual_var is not None else BAYES_HYPER["residualVar"]

    # Kalman team uncertainty
    if kalman_adj:
        variance += kalman_adj["var"]

    # Weight uncertainty propagated through features
    # Var(w*x) ~ x^2 * Var(w)  (diagonal approximation)
    if W_var:
        d_ts = t["TS"] - a["ts"]
        d_to = t["TO"] - a["to"]
        d_orr = t["ORR"] - a["orr"]
        d_net = (t_off - t_def) - (o["OFF"] - o["DEF"])

        weight_var = (
            (d_ts * pace) ** 2 * (W_var.get("wTS", 0))
            + (d_to * pace) ** 2 * (W_var.get("wTO", 0))
            + (d_orr * pace) ** 2 * (W_var.get("wORR", 0))
            + (0.5 * d_net * pace) ** 2 * (W_var.get("wNET", 0))
            + (1 if is_home else 0) * (W_var.get("hca", 0))
        )

        variance += weight_var

    return {"score": score, "variance": variance}


# -- Total projection (avoids double-counting) -----------------------------
# OFF/DEF ratings already embed TS%, TO%, ORR effects. proj_score adds those
# as separate corrections — fine for margin (cancels), bad for total (stacks).
# HCA is a margin effect (home scores more, away scores less), not a total effect.
# This function computes the total directly from the matchup without inflation.

def proj_total(home_team, away_team, H, a, W):
    h_key = _resolve_team(H, home_team)
    a_key = _resolve_team(H, away_team)
    if not h_key or not a_key:
        return None

    h = H.get(h_key)
    aw = H.get(a_key)
    if not h or not aw:
        return None

    # Clean total: (hOFF + aDEF)/2 + (aOFF + hDEF)/2 = matchup-based expected points
    # No TS/TO/ORR corrections (already in OFF/DEF), no HCA (margin effect, not total)
    total_base = (h["OFF"] + aw["DEF"]) / 2 + (aw["OFF"] + h["DEF"]) / 2

    # Pace correction: college pace stats may overstate effective scoring possessions.
    # Raw proj_total can overshoot actual totals. Slight correction to improve accuracy.
    # 0.991 on a ~140 avg total ~ -1.3 pts.
    PACE_SCORING_FACTOR = 0.991
    pace = (((h["PACE"] + aw["PACE"]) / 2) * (W.get("paceAdj", 1))) / 100 * PACE_SCORING_FACTOR

    # No Kalman here — Kalman tracks margin drift (team beating/missing spread),
    # not total scoring. A +3 Kalman team might be winning by defense, not offense.
    # Adding Kalman to totals inflates projections and generates bad OVER picks.
    return round(total_base * pace * 10) / 10


# Returns the feature vector used by self_tune for the margin regression.
# margin ~ features * weights + baseline

def extract_margin_features(home_stats, away_stats, avg_stats, pace_adj):
    pace = ((home_stats["PACE"] + away_stats["PACE"]) / 2 * pace_adj) / 100
    return {
        "dTS": (home_stats["TS"] - away_stats["TS"]) * pace,
        "dTO": -(home_stats["TO"] - away_stats["TO"]) * pace,    # negative: higher TO is bad
        "dORR": (home_stats["ORR"] - away_stats["ORR"]) * pace,
        "dNET": 0.5 * ((home_stats["OFF"] - home_stats["DEF"]) - (away_stats["OFF"] - away_stats["DEF"])) * pace,
        "hca": 1.0,  # home court present
        # Baseline (not weight-dependent): ((hOFF+aDEF)/2 - (aOFF+hDEF)/2) * pace
        "_baseline": ((home_stats["OFF"] + away_stats["DEF"]) / 2 - (away_stats["OFF"] + home_stats["DEF"]) / 2) * pace,
        "_pace": pace,
    }


# -- Injury note builder (unchanged) --------------------------------------

def _build_injury_note(injury_adj):
    if not injury_adj:
        return None
    parts = []
    away_injuries = injury_adj.get("awayInjuries", [])
    if away_injuries:
        parts.append("Away: " + ", ".join(f"{i['player']} ({i['status']}/{i['tier']})" for i in away_injuries))
    home_injuries = injury_adj.get("homeInjuries", [])
    if home_injuries:
        parts.append("Home: " + ", ".join(f"{i['player']} ({i['status']}/{i['tier']})" for i in home_injuries))
    return " | ".join(parts) if parts else None


# -- Game Analysis ---------------------------------------------------------
# Now computes P(cover) for spread and total.
#
# New parameters:
#   kalman_state: the full kalman state object (from kalman_state.py) — optional
#   W_var:        weight variances — optional
#
# If kalman_state and W_var are None, falls back to the legacy threshold logic.

def analyze_game(g, H, a, W, injury_adj=None, kalman_state=None, W_var=None, residual_var=None, team_hca=None):
    away_key = _resolve_team(H, g["away"])
    home_key = _resolve_team(H, g["home"])
    if not away_key or not home_key:
        return None

    gg = {**g, "away": away_key, "home": home_key}

    # Get Kalman adjustments if available
    home_kalman = None
    away_kalman = None

    if kalman_state and kalman_state.get("teams"):
        def get_adj(name):
            t = kalman_state["teams"].get(name)
            return {"mean": t["adj_mean"], "var": t["adj_var"]} if t else None
    else:
        def get_adj(name):
            return None

    home_kalman = get_adj(home_key)
    away_kalman = get_adj(away_key)

    # Project scores
    a_proj = proj_score(gg["away"], gg["home"], False, H, a, W, away_kalman, W_var, residual_var, team_hca)
    h_proj = proj_score(gg["home"], gg["away"], True, H, a, W, home_kalman, W_var, residual_var, team_hca)
    if not a_proj or not h_proj:
        return None

    a_s = a_proj["score"]
    h_s = h_proj["score"]
    p_t = round((a_s + h_s) * 10) / 10

    # Clean total for over/under probability (no TS/TO/ORR double-counting, no HCA)
    clean_total = proj_total(gg["home"], gg["away"], H, a, W) or p_t

    # -- Spread analysis ---------------------------------------------------

    margin = h_s - a_s - gg["line"]
    s_diff = abs(margin)
    t_diff = round((p_t - gg["total"]) * 10) / 10
    clean_t_diff = round((clean_total - gg["total"]) * 10) / 10

    abs_line = abs(gg["line"])
    home_fav = gg["line"] > 0

    # -- Probability of covering -------------------------------------------
    # margin_mean = hS - aS - line (positive = model favors home cover)
    # margin_var  = home_var + away_var (team uncertainties are independent)
    # P(home covers) = Phi(margin_mean / sqrt(margin_var))

    margin_var = (h_proj.get("variance", 0)) + (a_proj.get("variance", 0))
    margin_std = math.sqrt(max(margin_var, 1))  # floor at 1 to avoid division by zero

    p_home_cover = normal_cdf(margin / margin_std)
    p_away_cover = 1 - p_home_cover

    # Total: P(over) using clean total projection (no double-counting)
    total_var = margin_var * 1.1  # totals slightly noisier
    total_std = math.sqrt(max(total_var, 1))
    p_over = normal_cdf(clean_t_diff / total_std)
    p_under = 1 - p_over

    # -- Pick logic --------------------------------------------------------
    # Primary: probability-based (if Kalman state available)
    # Fallback: legacy threshold-based

    h_team = H[home_key]
    a_team = H[away_key]

    s_pick = "PASS"
    s_conf = "low"
    o_pick = "PASS"
    o_conf = "low"
    p_cover = None  # the P(cover) for the chosen side
    p_ou = None      # the P(over/under) for the chosen side

    use_bayesian = kalman_state is not None and W_var is not None

    if use_bayesian:
        # -- Bayesian pick logic (probability-based) -----------------------
        # Spread: single tier (elite was overconfident — worse than high).
        # Totals: keep elite tier (elite totals 58.6% vs high totals 49.4%).

        prob_oh = W.get("probOUHigh", 0.58)
        prob_oe = W.get("probOUElite", 0.64)

        # Spread — pCover is sole gatekeeper (sDiff redundant above 0.60).
        #   Fav line cap 8. Dogs: no line cap.
        P_COVER_THRESH = 0.60
        FAV_LINE_CAP = 8
        best_spread_p = max(p_home_cover, p_away_cover)
        spread_side = "home" if p_home_cover >= p_away_cover else "away"

        picked_side_is_dog = (
            (gg["line"] < 0 if spread_side == "home" else gg["line"] > 0)
        )

        line_ok = True if picked_side_is_dog else abs_line <= FAV_LINE_CAP

        if best_spread_p >= P_COVER_THRESH and line_ok and abs_line > 0:
            if spread_side == "home":
                s_pick = f"{gg['home']} -{abs_line}" if home_fav else f"{gg['home']} +{abs_line}"
            else:
                s_pick = f"{gg['away']} +{abs_line}" if home_fav else f"{gg['away']} -{abs_line}"
            s_conf = "elite"
            p_cover = best_spread_p

        # Total picks disabled — edge not holding (53% last 2 weeks).
        # Keeping spread-only for cleaner signal.

    else:
        # -- Legacy threshold-based logic (fallback) -----------------------

        if 3 <= s_diff <= 9 and abs_line <= 10 and s_diff >= W["sprHigh"]:
            if margin > 0:
                s_pick = f"{gg['home']} -{abs_line}" if home_fav else f"{gg['home']} +{abs_line}"
            else:
                s_pick = f"{gg['away']} +{abs_line}" if home_fav else f"{gg['away']} -{abs_line}"
            s_conf = "elite"

        ou_elite_adj = W["ouHigh"] + W.get("ouEliteBump", 3)
        if abs(clean_t_diff) >= W["ouHigh"]:
            if abs(clean_t_diff) >= ou_elite_adj:
                o_pick = "OVER" if clean_t_diff > 0 else "UNDER"
                o_conf = "elite"

    # -- Feature deltas for self_tune --------------------------------------

    _features = {
        "dTS": h_team["TS"] - a_team["TS"],
        "dTO": h_team["TO"] - a_team["TO"],
        "dORR": h_team["ORR"] - a_team["ORR"],
        "dNET": (h_team["OFF"] - h_team["DEF"]) - (a_team["OFF"] - a_team["DEF"]),
        "avgPace": (h_team["PACE"] + a_team["PACE"]) / 2,
    }

    # Margin features for Bayesian weight update
    _margin_features = extract_margin_features(h_team, a_team, a, W["paceAdj"])

    return {
        **gg,
        "aS": a_s,
        "hS": h_s,
        "pT": p_t,
        "margin": round(margin * 10) / 10,
        "sDiff": round(s_diff * 10) / 10,
        "tDiff": clean_t_diff,
        "sPick": s_pick,
        "sConf": s_conf,
        "oPick": o_pick,
        "oConf": o_conf,
        "injuryNote": _build_injury_note(injury_adj) if injury_adj else None,

        # Bayesian outputs
        "pHomeCover": round(p_home_cover * 1000) / 1000,
        "pAwayCover": round(p_away_cover * 1000) / 1000,
        "pOver": round(p_over * 1000) / 1000,
        "pUnder": round(p_under * 1000) / 1000,
        "pCover": round(p_cover * 1000) / 1000 if p_cover else None,
        "pOU": round(p_ou * 1000) / 1000 if p_ou else None,
        "marginVar": round(margin_var * 10) / 10,
        "marginStd": round(margin_std * 10) / 10,

        # Features for tuning
        "_features": _features,
        "_marginFeatures": _margin_features,
    }
