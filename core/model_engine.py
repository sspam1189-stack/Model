# core/model_engine.py
# Shared model engine with sport-specific configuration.

import math
import re
from types import SimpleNamespace


def _jround(x):
    """Round half-up (matches JS Math.round behavior, not Python's banker's rounding)."""
    return math.floor(x + 0.5)


def _norm_key(s: str) -> str:
    s = str(s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _expand_team_name(name, aliases):
    n = _norm_key(name)
    return aliases.get(n, name)


def _resolve_team_nba(H, name, aliases):
    if not H or not name:
        return None
    if name in H:
        return name
    keys = list(H.keys())
    wanted = _norm_key(name)

    for k in keys:
        if _norm_key(k) == wanted:
            return k
    expanded = _norm_key(_expand_team_name(name, aliases))
    for k in keys:
        if _norm_key(k) == expanded:
            return k
    for k in keys:
        nk = _norm_key(k)
        if wanted in nk or nk in wanted or expanded in nk or nk in expanded:
            return k
    return None


def _norm_state(s: str) -> str:
    """Normalize 'state' -> 'st' for NCAA team matching."""
    return re.sub(r'\bstate\b', 'st', s)


def _collapse_abbr(s: str) -> str:
    return s.replace(".", "")


def _safe_fuzzy(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    shorter = a if len(a) < len(b) else b
    longer = b if len(a) < len(b) else a
    if len(shorter) / len(longer) < 0.85:
        return False
    return shorter in longer


def _resolve_team_ncaa(H, name, aliases, use_collapse_abbr=True, use_safe_fuzzy=True):
    if not H or not name:
        return None
    if name in H:
        return name
    keys = list(H.keys())
    wanted = _norm_key(name)
    wanted_st = _norm_state(wanted)
    wanted_collapsed = _norm_key(_collapse_abbr(name)) if use_collapse_abbr else wanted

    for k in keys:
        nk = _norm_key(k)
        if nk == wanted or _norm_state(nk) == wanted_st:
            return k
    if use_collapse_abbr:
        for k in keys:
            nkc = _norm_key(_collapse_abbr(k))
            if nkc == wanted_collapsed or _norm_state(nkc) == _norm_state(wanted_collapsed):
                return k
    expanded = _norm_key(_expand_team_name(name, aliases))
    expanded_st = _norm_state(expanded)
    for k in keys:
        nk = _norm_key(k)
        if nk == expanded or _norm_state(nk) == expanded_st:
            return k
    if use_safe_fuzzy:
        for k in keys:
            nk = _norm_key(k)
            nk_st = _norm_state(nk)
            if (_safe_fuzzy(nk, wanted) or _safe_fuzzy(nk, expanded)
                    or _safe_fuzzy(nk_st, wanted_st) or _safe_fuzzy(nk_st, expanded_st)):
                return k
    # "School Mascot" prefix match: odds API sends "UMBC Retrievers", cache has "UMBC"
    best_prefix = None
    best_len = 0
    for k in keys:
        nk = _norm_key(k)
        nk_st = _norm_state(nk)
        nkc = _norm_key(_collapse_abbr(k)) if use_collapse_abbr else nk
        nkc_st = _norm_state(nkc)
        match_nk = (wanted.startswith(nk + " ") or wanted_collapsed.startswith(nk + " ")
                     or wanted_st.startswith(nk_st + " "))
        match_nkc = wanted_collapsed.startswith(nkc + " ") or _norm_state(wanted_collapsed).startswith(nkc_st + " ")
        if (match_nk or match_nkc) and len(nk) >= 3:
            length = max(len(nk), len(nkc))
            if length > best_len:
                best_len = length
                best_prefix = k
    if best_prefix:
        return best_prefix
    return None


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
        hca_map[team] = raw_hca * 0.5 + league_hca * 0.5
    return hca_map


def _is_finite(x):
    if x is None:
        return False
    try:
        return math.isfinite(x)
    except (TypeError, ValueError):
        return False


def _format_spread_pick(gg, spread_side, abs_line, home_fav):
    if spread_side == "home":
        return f"{gg['home']} -{abs_line}" if home_fav else f"{gg['home']} +{abs_line}"
    return f"{gg['away']} +{abs_line}" if home_fav else f"{gg['away']} -{abs_line}"


def _line_cap_ok(abs_line, cap, inclusive):
    if cap is None:
        return True
    return abs_line <= cap if inclusive else abs_line < cap


def create_model_engine(
    DEFAULT_STATS,
    DEFAULT_W,
    DEFAULT_W_VAR,
    BAYES_HYPER,
    engine_config=None,
    pace_scoring_factor=0.991,
    total_var_multiplier=1.1,
    is_neutral_site=None,
    enable_team_hca=False,
    enable_h2h=False,
    h2h_weight_key="h2hWeight",
    h2h_max_adj=4.0,
    h2h_recency_decay=0.85,
    bayes=None,
):
    if engine_config is None:
        engine_config = {}

    aliases = engine_config.get("TEAM_NAME_ALIASES", {})
    min_games = engine_config.get("MIN_GP", 15)
    use_safe_fuzzy = engine_config.get("USE_SAFE_FUZZY", False)
    use_collapse_abbr = engine_config.get("USE_COLLAPSE_ABBR", False)

    def resolve_team(H, name):
        if use_safe_fuzzy or use_collapse_abbr:
            return _resolve_team_ncaa(H, name, aliases, use_collapse_abbr, use_safe_fuzzy)
        return _resolve_team_nba(H, name, aliases)

    bayes = bayes or {}

    bayes_cfg = {
        "spread": {
            "mode": "probHigh",
            "prob_key": "probHigh",
            "min_prob": 0.57,
            "s_diff_cap": None,
            "abs_line_cap": 13,
            "abs_line_cap_inclusive": False,
            "require_line_nonzero": False,
            "fav_line_cap": None,
            "use_s_diff": True,
            "threshold": 0.60,
            **(bayes.get("spread") or {}),
        },
        "totals": {
            "enabled": True,
            "prob_key": "probOUElite",
            "min_prob": 0.64,
            **(bayes.get("totals") or {}),
        },
    }


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

    def proj_score(team, opp, is_home, H, a, W, kalman_adj=None, W_var=None, residual_var=None, team_hca=None):
        _r4 = lambda x: _jround(x * 10000) / 10000
        t_key = resolve_team(H, team)
        o_key = resolve_team(H, opp)

        t = H.get(t_key) if t_key else None
        o = H.get(o_key) if o_key else None
        if not t or not o:
            return None

        if (t.get("GP") is not None and t["GP"] < min_games) or \
           (o.get("GP") is not None and o["GP"] < min_games):
            return None

        t_off = t["OFF"]
        t_def = t["DEF"]

        base = (
            _r4((t_off + o["DEF"]) / 2)
            + _r4((t["TS"] - a["ts"]) * W["wTS"])
            - _r4((t["TO"] - a["to"]) * W["wTO"])
            + _r4((t["ORR"] - a["orr"]) * W["wORR"])
            + _r4(W["wDEF"] * (o["DEF"] - t_def))
            + W["constant"]
        )

        pace = _r4((((t["PACE"] + o["PACE"]) / 2) * W["paceAdj"]) / 100)
        if enable_team_hca and team_hca:
            hca = team_hca.get(t_key, W["hca"]) if is_home else 0
        else:
            hca = W["hca"] if is_home else 0

        score = _jround(base * pace * 10) / 10 + hca

        if kalman_adj:
            score += kalman_adj["mean"]

        score = _jround(score * 10) / 10

        variance = (residual_var / 2) if residual_var is not None else BAYES_HYPER["residualVar"]

        if kalman_adj:
            variance += kalman_adj["var"]

        if W_var:
            d_ts = t["TS"] - a["ts"]
            d_to = t["TO"] - a["to"]
            d_orr = t["ORR"] - a["orr"]
            d_def = o["DEF"] - t_def

            weight_var = (
                (d_ts * pace) ** 2 * (W_var.get("wTS", 0))
                + (d_to * pace) ** 2 * (W_var.get("wTO", 0))
                + (d_orr * pace) ** 2 * (W_var.get("wORR", 0))
                + (d_def * pace) ** 2 * (W_var.get("wDEF", 0))
                + (1 if is_home else 0) * (W_var.get("hca", 0))
            )

            variance += weight_var

        return {"score": score, "variance": variance}

    def proj_total(home_team, away_team, H, a, W):
        h_key = resolve_team(H, home_team)
        a_key = resolve_team(H, away_team)
        if not h_key or not a_key:
            return None

        h = H.get(h_key)
        aw = H.get(a_key)
        if not h or not aw:
            return None

        total_base = (h["OFF"] + aw["DEF"]) / 2 + (aw["OFF"] + h["DEF"]) / 2

        pace = (((h["PACE"] + aw["PACE"]) / 2) * (W.get("paceAdj", 1))) / 100 * pace_scoring_factor
        return _jround(total_base * pace * 10) / 10

    def extract_margin_features(home_stats, away_stats, avg_stats, pace_adj, neutral=False):
        _r4 = lambda x: _jround(x * 10000) / 10000
        pace = _r4(((home_stats["PACE"] + away_stats["PACE"]) / 2 * pace_adj) / 100)
        return {
            "dTS":  _r4((home_stats["TS"] - away_stats["TS"]) * pace),
            "dTO":  _r4(-(home_stats["TO"] - away_stats["TO"]) * pace),
            "dORR": _r4((home_stats["ORR"] - away_stats["ORR"]) * pace),
            "dDEF": _r4((away_stats["DEF"] - home_stats["DEF"]) * pace),
            "hca":  0.0 if neutral else 1.0,
            "_baseline": _r4(((home_stats["OFF"] + away_stats["DEF"]) / 2 - (away_stats["OFF"] + home_stats["DEF"]) / 2) * pace),
            "_pace": pace,
        }

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

    def _compute_h2h_adj(home_team, away_team, h2h_matchups, W):
        if not h2h_matchups:
            return None

        key = "::".join(sorted([home_team, away_team]))
        matchup = h2h_matchups.get(key)
        if not matchup or not matchup.get("games"):
            return None

        h2h_weight = W.get(h2h_weight_key, 0.15)

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
            recency = h2h_recency_decay ** (len(sorted_games) - 1 - i)
            weighted_margin += m * recency
            total_weight += recency

        n_games = len(sorted_games)
        w_avg = weighted_margin / total_weight
        game_conf = min(n_games / 4, 1.0)
        adj = max(-h2h_max_adj, min(h2h_max_adj, w_avg * h2h_weight * game_conf))

        avg_margin = total_margin / n_games
        return {
            "h2hAdj": _jround(adj * 10) / 10,
            "h2hGames": n_games,
            "h2hRecord": f"{home_wins}-{home_losses}",
            "h2hMargin": _jround(avg_margin * 10) / 10,
            "h2hNote": f"H2H {home_team} {home_wins}-{home_losses} (avg {'+' if avg_margin > 0 else ''}{avg_margin:.1f}, adj {'+' if adj > 0 else ''}{adj:.1f})",
        }

    def analyze_game(
        g,
        H,
        a,
        W,
        injury_adj=None,
        kalman_state=None,
        W_var=None,
        residual_var=None,
        h2h_matchups=None,
        team_hca=None,
    ):
        away_key = resolve_team(H, g["away"])
        home_key = resolve_team(H, g["home"])
        if not away_key or not home_key:
            return None

        gg = {**g, "away": away_key, "home": home_key}

        neutral = bool(is_neutral_site(g)) if is_neutral_site else False
        home_flag = not neutral

        home_kalman = None
        away_kalman = None

        if kalman_state and kalman_state.get("teams"):
            teams_map = kalman_state["teams"]
            ht = teams_map.get(home_key)
            home_kalman = {"mean": ht["adj_mean"], "var": ht["adj_var"]} if ht else None
            at = teams_map.get(away_key)
            away_kalman = {"mean": at["adj_mean"], "var": at["adj_var"]} if at else None

        a_proj = proj_score(gg["away"], gg["home"], False, H, a, W, away_kalman, W_var, residual_var, team_hca)
        h_proj = proj_score(gg["home"], gg["away"], home_flag, H, a, W, home_kalman, W_var, residual_var, team_hca)
        if not a_proj or not h_proj:
            return None

        a_s = a_proj["score"]
        h_s = h_proj["score"]
        p_t = _jround((a_s + h_s) * 10) / 10

        clean_total = proj_total(gg["home"], gg["away"], H, a, W) or p_t

        margin = h_s - a_s + gg["line"]

        h2h = None
        if enable_h2h:
            h2h = _compute_h2h_adj(home_key, away_key, h2h_matchups, W)
            if h2h and _is_finite(h2h.get("h2hAdj")):
                margin += h2h["h2hAdj"]

        s_diff = abs(margin)
        t_diff = _jround((p_t - gg["total"]) * 10) / 10
        clean_t_diff = _jround((clean_total - gg["total"]) * 10) / 10

        abs_line = abs(gg["line"])
        home_fav = gg["line"] < 0

        margin_var = (h_proj.get("variance", 0)) + (a_proj.get("variance", 0))
        margin_std = math.sqrt(max(margin_var, 1))

        p_home_cover = normal_cdf(margin / margin_std)
        p_away_cover = 1 - p_home_cover

        total_var = margin_var * total_var_multiplier
        total_std = math.sqrt(max(total_var, 1))
        p_over = normal_cdf(clean_t_diff / total_std)
        p_under = 1 - p_over

        s_pick = "PASS"
        s_conf = "low"
        o_pick = "PASS"
        o_conf = "low"
        p_cover = None
        p_ou = None

        best_spread_p = max(p_home_cover, p_away_cover)
        spread_side = "home" if p_home_cover >= p_away_cover else "away"

        if bayes_cfg["spread"]["mode"] == "fixed":
            spread_prob = bayes_cfg["spread"]["threshold"]
        else:
            spread_prob = W.get(bayes_cfg["spread"]["prob_key"], bayes_cfg["spread"]["min_prob"])

        picked_side_is_dog = (gg["line"] > 0) if spread_side == "home" else (gg["line"] < 0)
        fav_line_cap = bayes_cfg["spread"]["fav_line_cap"]
        line_ok = True if fav_line_cap is None else (True if picked_side_is_dog else abs_line <= fav_line_cap)

        s_diff_ok = True if (not bayes_cfg["spread"]["use_s_diff"] or bayes_cfg["spread"]["s_diff_cap"] is None) else (s_diff <= bayes_cfg["spread"]["s_diff_cap"])
        abs_line_ok = _line_cap_ok(abs_line, bayes_cfg["spread"]["abs_line_cap"], bayes_cfg["spread"]["abs_line_cap_inclusive"])
        non_zero_ok = True if not bayes_cfg["spread"]["require_line_nonzero"] else abs_line > 0

        if best_spread_p >= spread_prob and line_ok and s_diff_ok and abs_line_ok and non_zero_ok:
            s_pick = _format_spread_pick(gg, spread_side, abs_line, home_fav)
            s_conf = "elite"
            p_cover = best_spread_p

        if bayes_cfg["totals"]["enabled"]:
            best_total_p = max(p_over, p_under)
            total_prob = W.get(bayes_cfg["totals"]["prob_key"], bayes_cfg["totals"]["min_prob"])
            if best_total_p >= total_prob:
                o_pick = "OVER" if p_over >= p_under else "UNDER"
                o_conf = "elite"
                p_ou = best_total_p

        h_team = H[home_key]
        a_team = H[away_key]
        _features = {
            "dTS":  h_team["TS"] - a_team["TS"],
            "dTO":  h_team["TO"] - a_team["TO"],
            "dORR": h_team["ORR"] - a_team["ORR"],
            "dDEF": a_team["DEF"] - h_team["DEF"],
            "avgPace": (h_team["PACE"] + a_team["PACE"]) / 2,
        }

        _margin_features = extract_margin_features(h_team, a_team, a, W["paceAdj"], neutral)

        result = {
            **gg,
            "aS": a_s,
            "hS": h_s,
            "pT": p_t,
            "margin": _jround(margin * 10) / 10,
            "sDiff": _jround(s_diff * 10) / 10,
            "tDiff": clean_t_diff,
            "sPick": s_pick,
            "sConf": s_conf,
            "oPick": o_pick,
            "oConf": o_conf,
            "injuryNote": _build_injury_note(injury_adj) if injury_adj else None,

            "pHomeCover": _jround(p_home_cover * 1000) / 1000,
            "pAwayCover": _jround(p_away_cover * 1000) / 1000,
            "pOver": _jround(p_over * 1000) / 1000,
            "pUnder": _jround(p_under * 1000) / 1000,
            "pCover": _jround(p_cover * 1000) / 1000 if p_cover else None,
            "pOU": _jround(p_ou * 1000) / 1000 if p_ou else None,
            "marginVar": _jround(margin_var * 10) / 10,
            "marginStd": _jround(margin_std * 10) / 10,

            "_features": _features,
            "_marginFeatures": _margin_features,
        }

        if enable_h2h:
            result.update({
                "h2hAdj": h2h.get("h2hAdj", 0) if h2h else 0,
                "h2hGames": h2h.get("h2hGames", 0) if h2h else 0,
                "h2hRecord": h2h.get("h2hRecord") if h2h else None,
                "h2hNote": h2h.get("h2hNote") if h2h else None,
            })

        return result

    return SimpleNamespace(
        load_defaults=load_defaults,
        get_avgs=get_avgs,
        normal_cdf=normal_cdf,
        proj_score=proj_score,
        proj_total=proj_total,
        extract_margin_features=extract_margin_features,
        analyze_game=analyze_game,
    )
