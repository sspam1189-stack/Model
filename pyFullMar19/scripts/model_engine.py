# scripts/model_engine.py
# --------------------------------------------------------------------------
# BAYESIAN UPGRADE: projScore now returns { score, variance }.
# analyzeGame computes P(cover) from the projected distribution and uses
# probability thresholds instead of raw edge thresholds for pick logic.
#
# Backward compatible: still outputs sDiff, margin, etc. for display/grading.
# The old threshold-based logic is kept as a fallback if Kalman state is null.
# --------------------------------------------------------------------------

import math
import re
from scipy.stats import norm

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
        "ts":  sum(x["TS"] for x in teams) / n,
        "to":  sum(x["TO"] for x in teams) / n,
        "orr": sum(x["ORR"] for x in teams) / n,
    }


# -- Normal CDF (Abramowitz & Stegun approximation, ~1e-5 accuracy) ----------

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


# -- Team Name Resolution (unchanged) ----------------------------------------

def _norm_key(s):
    s = str(s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


TEAM_NAME_ALIASES = {
    "la lakers":             "Los Angeles Lakers",
    "lakers":                "Los Angeles Lakers",
    "la clippers":           "LA Clippers",
    "los angeles clippers":  "LA Clippers",
    "clippers":              "Los Angeles Clippers",
    "golden state":          "Golden State Warriors",
    "warriors":              "Golden State Warriors",
    "oklahoma city":         "Oklahoma City Thunder",
    "thunder":               "Oklahoma City Thunder",
    "new orleans":           "New Orleans Pelicans",
    "pelicans":              "New Orleans Pelicans",
    "new york":              "New York Knicks",
    "knicks":                "New York Knicks",
    "san antonio":           "San Antonio Spurs",
    "spurs":                 "San Antonio Spurs",
    "portland":              "Portland Trail Blazers",
    "trail blazers":         "Portland Trail Blazers",
    "philadelphia":          "Philadelphia 76ers",
    "76ers":                 "Philadelphia 76ers",
    "sixers":                "Philadelphia 76ers",
    "minnesota":             "Minnesota Timberwolves",
    "timberwolves":          "Minnesota Timberwolves",
    "wolves":                "Minnesota Timberwolves",
    "memphis":               "Memphis Grizzlies",
    "grizzlies":             "Memphis Grizzlies",
    "charlotte":             "Charlotte Hornets",
    "hornets":               "Charlotte Hornets",
    "indiana":               "Indiana Pacers",
    "pacers":                "Indiana Pacers",
    "washington":            "Washington Wizards",
    "wizards":               "Washington Wizards",
    "orlando":               "Orlando Magic",
    "magic":                 "Orlando Magic",
    "miami":                 "Miami Heat",
    "heat":                  "Miami Heat",
    "atlanta":               "Atlanta Hawks",
    "hawks":                 "Atlanta Hawks",
    "chicago":               "Chicago Bulls",
    "bulls":                 "Chicago Bulls",
    "detroit":               "Detroit Pistons",
    "pistons":               "Detroit Pistons",
    "cleveland":             "Cleveland Cavaliers",
    "cavaliers":             "Cleveland Cavaliers",
    "cavs":                  "Cleveland Cavaliers",
    "toronto":               "Toronto Raptors",
    "raptors":               "Toronto Raptors",
    "brooklyn":              "Brooklyn Nets",
    "nets":                  "Brooklyn Nets",
    "boston":                 "Boston Celtics",
    "celtics":               "Boston Celtics",
    "milwaukee":             "Milwaukee Bucks",
    "bucks":                 "Milwaukee Bucks",
    "denver":                "Denver Nuggets",
    "nuggets":               "Denver Nuggets",
    "utah":                  "Utah Jazz",
    "jazz":                  "Utah Jazz",
    "phoenix":               "Phoenix Suns",
    "suns":                  "Phoenix Suns",
    "sacramento":            "Sacramento Kings",
    "kings":                 "Sacramento Kings",
    "dallas":                "Dallas Mavericks",
    "mavericks":             "Dallas Mavericks",
    "mavs":                  "Dallas Mavericks",
    "houston":               "Houston Rockets",
    "rockets":               "Houston Rockets",
}


def _expand_team_name(name):
    n = _norm_key(name)
    return TEAM_NAME_ALIASES.get(n, name)


def _resolve_team(H, name):
    if not H or not name:
        return None
    if name in H:
        return name
    keys = list(H.keys())
    wanted = _norm_key(name)

    for k in keys:
        if _norm_key(k) == wanted:
            return k
    expanded = _norm_key(_expand_team_name(name))
    for k in keys:
        if _norm_key(k) == expanded:
            return k
    for k in keys:
        nk = _norm_key(k)
        if wanted in nk or nk in wanted or expanded in nk or nk in expanded:
            return k
    return None


# -- Projection ---------------------------------------------------------------
# Returns { "score": ..., "variance": ... } instead of a single number.

def proj_score(team, opp, is_home, H, a, W, kalman_adj=None, W_var=None, residual_var=None):
    t_key = _resolve_team(H, team)
    o_key = _resolve_team(H, opp)

    t = H.get(t_key) if t_key else None
    o = H.get(o_key) if o_key else None
    if not t or not o:
        return None

    MIN_GP = 15
    if (t.get("GP") is not None and t["GP"] < MIN_GP) or \
       (o.get("GP") is not None and o["GP"] < MIN_GP):
        return None

    t_off = t["OFF"]
    t_def = t["DEF"]

    # -- Point estimate (same formula as before) -------------------------------

    base = (
        (t_off + o["DEF"]) / 2
        + (t["TS"] - a["ts"]) * W["wTS"]
        - (t["TO"] - a["to"]) * W["wTO"]
        + (t["ORR"] - a["orr"]) * W["wORR"]
        + (W["wNET"] * 0.5) * ((t_off - t_def) - (o["OFF"] - o["DEF"]))
        + W["constant"]
    )

    pace = (((t["PACE"] + o["PACE"]) / 2) * W["paceAdj"]) / 100
    score = base * pace + (W["hca"] if is_home else 0)

    # Add Kalman adjustment if available
    if kalman_adj:
        score += kalman_adj["mean"]

    score = round(score * 10) / 10

    # -- Variance propagation --------------------------------------------------
    # Dynamic residualVar is total margin noise (measured from margin errors).
    # Split in half per team since analyzeGame sums home + away variance.
    # Default (BAYES_HYPER) is already calibrated as per-team value.

    variance = (residual_var / 2) if residual_var is not None else BAYES_HYPER["residualVar"]

    # Kalman team uncertainty
    if kalman_adj:
        variance += kalman_adj["var"]

    # Weight uncertainty propagated through features
    # Var(w*x) ~ x^2 * Var(w)  (diagonal approximation)
    if W_var:
        d_ts  = t["TS"] - a["ts"]
        d_to  = t["TO"] - a["to"]
        d_orr = t["ORR"] - a["orr"]
        d_net = (t_off - t_def) - (o["OFF"] - o["DEF"])

        weight_var = (
            (d_ts * pace) ** 2  * (W_var.get("wTS", 0))
            + (d_to * pace) ** 2  * (W_var.get("wTO", 0))
            + (d_orr * pace) ** 2 * (W_var.get("wORR", 0))
            + (0.5 * d_net * pace) ** 2 * (W_var.get("wNET", 0))
            + (1 if is_home else 0) * (W_var.get("hca", 0))
        )

        variance += weight_var

    return {"score": score, "variance": variance}


# -- Total projection (avoids double-counting) --------------------------------
# OFF/DEF ratings already embed TS%, TO%, ORR effects. projScore adds those
# as separate corrections -- fine for margin (cancels), bad for total (stacks).
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

    # Pace correction: NBA.com PACE overstates effective scoring possessions
    PACE_SCORING_FACTOR = 0.991
    pace = (((h["PACE"] + aw["PACE"]) / 2) * (W.get("paceAdj", 1))) / 100 * PACE_SCORING_FACTOR

    return round(total_base * pace * 10) / 10


# Returns the feature vector used by self_tune for the margin regression.
# margin ~ features * weights + baseline

def extract_margin_features(home_stats, away_stats, avg_stats, pace_adj):
    pace = ((home_stats["PACE"] + away_stats["PACE"]) / 2 * pace_adj) / 100
    return {
        "dTS":  (home_stats["TS"] - away_stats["TS"]) * pace,
        "dTO":  -(home_stats["TO"] - away_stats["TO"]) * pace,    # negative: higher TO is bad
        "dORR": (home_stats["ORR"] - away_stats["ORR"]) * pace,
        "dNET": 0.5 * ((home_stats["OFF"] - home_stats["DEF"]) - (away_stats["OFF"] - away_stats["DEF"])) * pace,
        "hca":  1.0,  # home court present
        # Baseline (not weight-dependent): ((hOFF+aDEF)/2 - (aOFF+hDEF)/2) * pace
        "_baseline": ((home_stats["OFF"] + away_stats["DEF"]) / 2 - (away_stats["OFF"] + home_stats["DEF"]) / 2) * pace,
        "_pace": pace,
    }


# -- Injury note builder (unchanged) -----------------------------------------

def _build_injury_note(injury_adj):
    if not injury_adj:
        return None
    parts = []
    away_injuries = injury_adj.get("awayInjuries") or []
    home_injuries = injury_adj.get("homeInjuries") or []
    if away_injuries:
        parts.append("Away: " + ", ".join(f"{i['player']} ({i['status']}/{i['tier']})" for i in away_injuries))
    if home_injuries:
        parts.append("Home: " + ", ".join(f"{i['player']} ({i['status']}/{i['tier']})" for i in home_injuries))
    return " | ".join(parts) if parts else None


# -- H2H Matchup Adjustment ---------------------------------------------------
# Computes a margin shift based on how two specific teams perform against
# each other historically. Baked directly into the margin like Kalman.

def _compute_h2h_adj(home_team, away_team, h2h_matchups, W):
    if not h2h_matchups:
        return None

    key = "::".join(sorted([home_team, away_team]))
    matchup = h2h_matchups.get(key)
    if not matchup or not matchup.get("games") or len(matchup["games"]) == 0:
        return None

    h2h_weight = W.get("h2hWeight", 0.15)
    max_adj = 4.0
    recency_decay = 0.85

    sorted_games = sorted(matchup["games"], key=lambda a: a.get("date", ""))

    home_wins = 0
    home_losses = 0
    weighted_margin = 0
    total_weight = 0
    total_margin = 0

    for i, g in enumerate(sorted_games):
        is_actual_home = g["home"]["team"] == home_team
        our_pts = g["home"]["pts"] if is_actual_home else g["away"]["pts"]
        their_pts = g["away"]["pts"] if is_actual_home else g["home"]["pts"]
        m = our_pts - their_pts

        if m > 0:
            home_wins += 1
        elif m < 0:
            home_losses += 1

        total_margin += m
        recency = recency_decay ** (len(sorted_games) - 1 - i)
        weighted_margin += m * recency
        total_weight += recency

    n_games = len(sorted_games)
    w_avg = weighted_margin / total_weight
    game_conf = min(n_games / 4, 1.0)
    adj = max(-max_adj, min(max_adj, w_avg * h2h_weight * game_conf))

    avg_margin = total_margin / n_games
    return {
        "h2hAdj": round(adj * 10) / 10,
        "h2hGames": n_games,
        "h2hRecord": f"{home_wins}-{home_losses}",
        "h2hMargin": round(avg_margin * 10) / 10,
        "h2hNote": f"H2H {home_team} {home_wins}-{home_losses} (avg {'+'if avg_margin > 0 else ''}{avg_margin:.1f}, adj {'+'if adj > 0 else ''}{adj:.1f})",
    }


# -- Game Analysis -------------------------------------------------------------
# Now computes P(cover) for spread and total.

def analyze_game(g, H, a, W, injury_adj=None, kalman_state=None, W_var=None, residual_var=None, h2h_matchups=None):
    away_key = _resolve_team(H, g["away"])
    home_key = _resolve_team(H, g["home"])
    if not away_key or not home_key:
        return None

    gg = {**g, "away": away_key, "home": home_key}

    # Get Kalman adjustments if available
    home_kalman = None
    away_kalman = None

    if kalman_state and kalman_state.get("teams"):
        teams_map = kalman_state["teams"]
        ht = teams_map.get(home_key)
        home_kalman = {"mean": ht["adj_mean"], "var": ht["adj_var"]} if ht else None
        at = teams_map.get(away_key)
        away_kalman = {"mean": at["adj_mean"], "var": at["adj_var"]} if at else None

    # Project scores
    a_proj = proj_score(gg["away"], gg["home"], False, H, a, W, away_kalman, W_var, residual_var)
    h_proj = proj_score(gg["home"], gg["away"], True,  H, a, W, home_kalman, W_var, residual_var)
    if not a_proj or not h_proj:
        return None

    a_s = a_proj["score"]
    h_s = h_proj["score"]
    p_t = round((a_s + h_s) * 10) / 10

    # Clean total for over/under probability (no TS/TO/ORR double-counting, no HCA)
    clean_total = proj_total(gg["home"], gg["away"], H, a, W) or p_t

    # -- Spread analysis -------------------------------------------------------

    margin = h_s - a_s - gg["line"]

    # -- H2H matchup adjustment ------------------------------------------------
    h2h = _compute_h2h_adj(home_key, away_key, h2h_matchups, W)
    if h2h and _is_finite(h2h.get("h2hAdj")):
        margin += h2h["h2hAdj"]

    s_diff = abs(margin)
    t_diff = round((p_t - gg["total"]) * 10) / 10
    clean_t_diff = round((clean_total - gg["total"]) * 10) / 10

    abs_line = abs(gg["line"])
    home_fav = gg["line"] > 0

    # -- Probability of covering -----------------------------------------------
    margin_var = (h_proj.get("variance", 0)) + (a_proj.get("variance", 0))
    margin_std = math.sqrt(max(margin_var, 1))  # floor at 1 to avoid division by zero

    p_home_cover = normal_cdf(margin / margin_std)
    p_away_cover = 1 - p_home_cover

    # Total: P(over) using clean total projection (no double-counting)
    total_var = margin_var * 1.1  # totals slightly noisier
    total_std = math.sqrt(max(total_var, 1))
    p_over  = normal_cdf(clean_t_diff / total_std)
    p_under = 1 - p_over

    # -- Pick logic ------------------------------------------------------------
    s_pick = "PASS"
    s_conf = "low"
    o_pick = "PASS"
    o_conf = "low"
    p_cover = None  # the P(cover) for the chosen side
    p_ou = None     # the P(over/under) for the chosen side

    use_bayesian = kalman_state is not None and W_var is not None

    if use_bayesian:
        # -- Bayesian pick logic (probability-based) ---------------------------
        prob_h  = W.get("probHigh", 0.57)
        prob_oh = W.get("probOUHigh", 0.58)
        prob_oe = W.get("probOUElite", 0.64)

        # Spread -- single tier
        best_spread_p = max(p_home_cover, p_away_cover)
        spread_side   = "home" if p_home_cover >= p_away_cover else "away"

        if best_spread_p >= prob_h and s_diff <= 10 and abs_line < 12:
            if spread_side == "home":
                s_pick = f"{gg['home']} -{abs_line}" if home_fav else f"{gg['home']} +{abs_line}"
            else:
                s_pick = f"{gg['away']} +{abs_line}" if home_fav else f"{gg['away']} -{abs_line}"
            s_conf = "elite"
            p_cover = best_spread_p

        # Total -- elite only (high totals 49.4%, not profitable)
        best_total_p = max(p_over, p_under)
        if best_total_p >= prob_oe:
            o_pick = "OVER" if p_over >= p_under else "UNDER"
            o_conf = "elite"
            p_ou = best_total_p

    else:
        # -- Legacy threshold-based logic (fallback) ---------------------------

        if s_diff >= W["sprHigh"] and s_diff <= 10 and abs_line < 12:
            if margin > 0:
                s_pick = f"{gg['home']} -{abs_line}" if home_fav else f"{gg['home']} +{abs_line}"
            else:
                s_pick = f"{gg['away']} +{abs_line}" if home_fav else f"{gg['away']} -{abs_line}"
            s_conf = "elite"

        if abs(clean_t_diff) >= W["ouHigh"]:
            ou_elite_adj = W["ouHigh"] + W.get("ouEliteBump", 3)
            if abs(clean_t_diff) >= ou_elite_adj:
                o_pick = "OVER" if clean_t_diff > 0 else "UNDER"
                o_conf = "elite"

    # -- Feature deltas for self_tune ------------------------------------------

    h_team = H[home_key]
    a_team = H[away_key]
    _features = {
        "dTS":  h_team["TS"]  - a_team["TS"],
        "dTO":  h_team["TO"]  - a_team["TO"],
        "dORR": h_team["ORR"] - a_team["ORR"],
        "dNET": (h_team["OFF"] - h_team["DEF"]) - (a_team["OFF"] - a_team["DEF"]),
        "avgPace": (h_team["PACE"] + a_team["PACE"]) / 2,
    }

    # Margin features for Bayesian weight update
    _margin_features = extract_margin_features(h_team, a_team, a, W["paceAdj"])

    result = {**gg}
    result.update({
        "aS":     a_s,
        "hS":     h_s,
        "pT":     p_t,
        "margin": round(margin * 10) / 10,
        "sDiff":  round(s_diff * 10) / 10,
        "tDiff":  clean_t_diff,
        "sPick":  s_pick,
        "sConf":  s_conf,
        "oPick":  o_pick,
        "oConf":  o_conf,
        "injuryNote": _build_injury_note(injury_adj) if injury_adj else None,

        # H2H matchup data
        "h2hAdj":    h2h.get("h2hAdj", 0) if h2h else 0,
        "h2hGames":  h2h.get("h2hGames", 0) if h2h else 0,
        "h2hRecord": h2h.get("h2hRecord") if h2h else None,
        "h2hNote":   h2h.get("h2hNote") if h2h else None,

        # Bayesian outputs
        "pHomeCover": round(p_home_cover * 1000) / 1000,
        "pAwayCover": round(p_away_cover * 1000) / 1000,
        "pOver":      round(p_over * 1000) / 1000,
        "pUnder":     round(p_under * 1000) / 1000,
        "pCover":     round(p_cover * 1000) / 1000 if p_cover else None,
        "pOU":        round(p_ou * 1000) / 1000 if p_ou else None,
        "marginVar":  round(margin_var * 10) / 10,
        "marginStd":  round(margin_std * 10) / 10,

        # Features for tuning
        "_features":       _features,
        "_marginFeatures": _margin_features,
    })

    return result


# -- Helper --------------------------------------------------------------------

def _is_finite(x):
    """Check if x is a finite number (equivalent to Number.isFinite in JS)."""
    if x is None:
        return False
    try:
        return math.isfinite(x)
    except (TypeError, ValueError):
        return False
