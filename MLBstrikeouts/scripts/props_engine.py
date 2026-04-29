# MLBstrikeouts/scripts/props_engine.py
# MLB pitcher strikeouts prop projection engine with Kalman filtering.

import math
import unicodedata
import numpy as np
from scipy.stats import t as t_dist

from defaults import (
    PROP_T_DF, ROLLING_WINDOW, DECAY_FACTOR, MIN_GAMES, MIN_INNINGS,
    MARKET_THRESHOLDS, VAR_MULT, MIN_EDGE, MAX_EDGE, MIN_LINE,
    UNDER_ONLY_MARKETS, DISABLED_MARKETS, EDGE_DEAD_ZONE,
    STAT_KEYS, KALMAN_STAT_KEYS,
)
from pitcher_kalman import get_pitcher_projection
from sources.game_context import (
    REST_ADJUSTMENTS, detect_rest_days, project_innings,
)


# ---------------------------------------------------------------------------
# Name matching
# ---------------------------------------------------------------------------

def _name_key(name):
    """
    Normalize a pitcher name for cross-source matching.
    """
    name = name.strip()
    if not name:
        return ('', '')
    name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')

    parts = name.split()
    suffixes = {'jr', 'jr.', 'sr', 'sr.', 'ii', 'iii', 'iv', 'v'}
    while len(parts) > 2 and parts[-1].lower().rstrip('.') in suffixes:
        parts.pop()

    if len(parts) >= 2:
        first = parts[0].lower()
        last = parts[-1].lower().rstrip('.')
        return (first, last)

    return (name[0].lower(), name.lower())


# ---------------------------------------------------------------------------
# Pitcher game log organization
# ---------------------------------------------------------------------------

def organize_pitcher_logs(raw_logs):
    """
    Organize raw game logs into per-pitcher lists sorted by date.
    Filters to starts only (IP >= 3.0 or is_start flag).
    """
    by_pitcher = {}
    for g in raw_logs:
        pid = g.get("pitcher_id")
        if pid is None:
            continue
        ip = g.get("IP", 0)
        is_start = g.get("is_start", False)
        if ip < 3.0 and not is_start:
            continue
        if pid not in by_pitcher:
            by_pitcher[pid] = []
        by_pitcher[pid].append(g)

    for pid in by_pitcher:
        by_pitcher[pid].sort(key=lambda g: g.get("game_date", ""))

    return by_pitcher


# ---------------------------------------------------------------------------
# Rolling average helpers
# ---------------------------------------------------------------------------

def _weighted_avg(values, decay=DECAY_FACTOR):
    if not values:
        return 0.0
    n = len(values)
    weights = [decay ** (n - 1 - i) for i in range(n)]
    return sum(v * w for v, w in zip(values, weights)) / sum(weights)


def _weighted_std(values, decay=DECAY_FACTOR):
    if len(values) < 2:
        return 10.0
    avg = _weighted_avg(values, decay)
    n = len(values)
    weights = [decay ** (n - 1 - i) for i in range(n)]
    w_sum = sum(weights)
    var = sum(w * (v - avg) ** 2 for v, w in zip(values, weights)) / w_sum
    return math.sqrt(max(var, 0.5))


# ---------------------------------------------------------------------------
# Injury helper
# ---------------------------------------------------------------------------

def _is_pitcher_out(name, team, injury_report):
    if not injury_report:
        return False

    nk = _name_key(name)
    out_statuses = {"out", "injured list", "il", "60-day il", "15-day il",
                    "10-day il", "bereavement", "paternity", "restricted",
                    "suspended"}

    for entry in injury_report:
        entry_nk = _name_key(entry.get("name", ""))
        entry_team = entry.get("team", "").upper()
        status = entry.get("status", "").lower().strip()

        if entry_nk == nk and entry_team == team.upper():
            if status in out_statuses:
                return True

    return False


# ---------------------------------------------------------------------------
# Main projection engine
# ---------------------------------------------------------------------------

def project_pitcher_props(pitcher_logs, team_batting_stats=None,
                          prop_lines=None, kalman_state=None,
                          pitcher_adv_stats=None, pitcher_sabermetrics=None,
                          pitcher_splits=None, probable_pitchers=None,
                          injury_report=None, weather_by_game=None,
                          batter_k_rates=None, lineup_data=None,
                          savant_rates=None, k_skill_config=None):
    """
    Project pitcher strikeouts props for all pitchers with sufficient game logs.
    """
    projections = []
    market = "strikeouts"

    if market in DISABLED_MARKETS:
        return projections

    stat_key = STAT_KEYS[market]

    # Compute league averages for opponent batting adjustment
    league_avg = {}
    if team_batting_stats:
        for k in ("K_PCT", "BA", "OPS", "BB_PCT"):
            vals = [s.get(k, 0) for s in team_batting_stats.values() if s.get(k)]
            league_avg[k] = np.mean(vals) if vals else 0.0
    skill_baselines = _skill_baselines(savant_rates or {})

    # Index prop lines by (first_name, last_name, market)
    line_lookup = {}
    pitcher_game_ctx = {}
    if prop_lines:
        for pl in prop_lines:
            nk = _name_key(pl.get("player", ""))
            key = (nk[0], nk[1], pl.get("market", ""))
            line_lookup[key] = pl

    pitcher_id_ctx = {}
    if probable_pitchers:
        for gm in probable_pitchers:
            home_team = gm.get("home_team", "")
            away_team = gm.get("away_team", "")
            for side, pitcher_name, pitcher_id, team, opp, is_home in [
                ("home", gm.get("home_pitcher", ""), gm.get("home_pitcher_id"),
                 home_team, away_team, True),
                ("away", gm.get("away_pitcher", ""), gm.get("away_pitcher_id"),
                 away_team, home_team, False),
            ]:
                ctx = {
                    "opp": opp,
                    "is_home": is_home,
                    "team": team,
                    "game_id": gm.get("game_id", ""),
                    "pitcher_name": pitcher_name,
                }
                if pitcher_name:
                    nk = _name_key(pitcher_name)
                    pitcher_game_ctx[nk] = ctx
                if pitcher_id is not None:
                    pitcher_id_ctx[pitcher_id] = ctx

    for pid, games in pitcher_logs.items():
        if not games:
            continue

        recent = games[-ROLLING_WINDOW:]
        name = games[-1].get("pitcher_name", "Unknown")
        team = games[-1].get("team", "")

        if injury_report and _is_pitcher_out(name, team, injury_report):
            continue

        ctx = pitcher_id_ctx.get(pid, {})
        if not ctx:
            nk = _name_key(name)
            ctx = pitcher_game_ctx.get(nk, {})

        if probable_pitchers and not ctx:
            continue

        latest_opp = ctx.get("opp", games[-1].get("opp", ""))
        is_home = ctx.get("is_home", games[-1].get("is_home", True))
        game_date = games[-1].get("game_date", "")

        qualified = [g for g in recent if g.get("IP", 0) >= MIN_INNINGS]

        adv = (pitcher_adv_stats or {}).get(str(pid), {})
        saber = (pitcher_sabermetrics or {}).get(str(pid), {})
        splits = (pitcher_splits or {}).get(str(pid), {})

        rest_days = detect_rest_days(games, game_date)
        pitcher_bb9 = adv.get("BB_PER_9") or adv.get("bb_per_9")
        opp_batting = (team_batting_stats or {}).get(latest_opp, {})
        opp_ops = opp_batting.get("OPS") or opp_batting.get("ops")
        proj_ip = project_innings(
            qualified, adv_stats=adv, rest_days=rest_days,
            pitcher_bb_per_9=pitcher_bb9,
            opp_ops=opp_ops,
            league_avg_ops=league_avg.get("OPS"),
        )

        if proj_ip < 3.0:
            continue

        min_g = MIN_GAMES.get(market, 3)
        vals = [g.get(stat_key, 0) for g in qualified]

        if len(vals) < min_g:
            continue

        n_games = len(vals)
        rolling_std = _weighted_std(vals) * VAR_MULT.get(market, 1.2)

        # --- Pitcher K rate by handedness ---
        vl = splits.get("vs_left", {})
        vr = splits.get("vs_right", {})
        vl_k, vl_pa = vl.get("k", 0), vl.get("pa", 0)
        vr_k, vr_pa = vr.get("k", 0), vr.get("pa", 0)

        k_rate_vs_lhb = vl_k / vl_pa if vl_pa > 0 else 0.0
        k_rate_vs_rhb = vr_k / vr_pa if vr_pa > 0 else 0.0

        overall_k_pct = adv.get("k_pct", 0) or 0.0
        savant = (savant_rates or {}).get(str(pid), {})
        savant_k_pct = savant.get("k_pct", 0) or 0.0

        if overall_k_pct == 0 and savant_k_pct > 0:
            overall_k_pct = savant_k_pct
        elif overall_k_pct == 0:
            total_k = sum(g.get("k", 0) for g in qualified)
            est_bf = sum(
                int(g.get("IP", g.get("ip", 0)) * 3) + g.get("h", 0) + g.get("bb", 0)
                for g in qualified
            )
            overall_k_pct = total_k / est_bf if est_bf > 0 else 0.20

        if k_rate_vs_lhb == 0:
            k_rate_vs_lhb = overall_k_pct
        if k_rate_vs_rhb == 0:
            k_rate_vs_rhb = overall_k_pct

        opp_stats = (team_batting_stats or {}).get(latest_opp, {})
        pct_lhb = opp_stats.get("PCT_LHB", 0.40)

        pitcher_k_rate = k_rate_vs_lhb * pct_lhb + k_rate_vs_rhb * (1.0 - pct_lhb)

        # --- Lineup K tendency ---
        lineup_k_rate = league_avg.get("K_PCT", 0.22)
        if batter_k_rates and lineup_data:
            opp_lineup = lineup_data.get(latest_opp, {})
            opp_player_ids = opp_lineup.get("player_ids", [])
            if opp_player_ids:
                _pitch_hand = adv.get("pitch_hand", "R")
                from sources.mlb_stats import compute_lineup_k_pct
                lk = compute_lineup_k_pct(opp_player_ids, batter_k_rates, _pitch_hand)
                if lk.get("lineup_k_pct_vs_hand", 0) > 0:
                    lineup_k_rate = lk["lineup_k_pct_vs_hand"]

        lg_k_rate = league_avg.get("K_PCT", 0.22) or 0.22
        expected_k_rate = (pitcher_k_rate * lineup_k_rate) / lg_k_rate
        expected_k_rate = _apply_k_skill_adjustment(
            expected_k_rate, savant, skill_baselines, k_skill_config
        )
        expected_k_rate = max(0.05, min(0.50, expected_k_rate))

        # --- Projected batters faced ---
        game_bfs = []
        game_pcs = []
        game_ppbfs = []
        for g in qualified:
            _outs = g.get("outs", 0)
            _h = g.get("h", 0)
            _bb = g.get("bb", 0)
            _pc = g.get("pitches", 0)
            _bf = _outs + _h + _bb + 1
            if _bf > 0 and _pc > 0:
                game_bfs.append(_bf)
                game_pcs.append(_pc)
                game_ppbfs.append(_pc / _bf)

        if game_ppbfs:
            avg_ppbf = _weighted_avg(game_ppbfs)
            avg_pc = _weighted_avg(game_pcs)
            projected_bf = avg_pc / avg_ppbf if avg_ppbf > 0 else 24.0
        else:
            whip = adv.get("WHIP", 0) or 1.20
            projected_bf = proj_ip * (3.0 + whip * 0.7)

        projected_bf = min(projected_bf * 0.91, 23.0)

        # --- Weather effect on K ---
        k_weather_mult = 1.0
        if weather_by_game:
            _game_id = ctx.get("game_id")
            if _game_id:
                w_data = (weather_by_game.get(_game_id)
                          or weather_by_game.get(str(_game_id)))
                if w_data:
                    import re as _re
                    _temp_m = _re.search(r"(\d+)", str(w_data.get("temp", "")))
                    if _temp_m:
                        _temp = int(_temp_m.group(1))
                        if _temp <= 50:
                            k_weather_mult += 0.02
                        elif _temp >= 90:
                            k_weather_mult -= 0.01

        model_proj = expected_k_rate * projected_bf * k_weather_mult

        # --- Kalman blend ---
        kalman_key = KALMAN_STAT_KEYS.get(market)
        kalman_proj, _ = _blend_with_kalman(
            kalman_state, str(pid), kalman_key,
            model_proj, rolling_std,
        )
        model_proj = 0.5 * model_proj + 0.5 * kalman_proj

        _kp = get_pitcher_projection(kalman_state, str(pid),
                                     kalman_key, rolling_avg=model_proj)
        k_kalman_var = _kp.get("var", 0.0)

        # Rest adjustment
        model_proj = _apply_rest_adjustment(model_proj, market, rest_days)

        proj = model_proj

        # --- Empirical std ---
        EMPIRICAL_STD = {"strikeouts": 1.9}
        emp_std = EMPIRICAL_STD.get(market, 2.0)

        if n_games <= 3:
            std = emp_std
        elif n_games <= 7:
            blend = (n_games - 3) / 4.0
            std = emp_std * (1 - blend) + rolling_std * blend
            std = max(std, emp_std * 0.7)
        else:
            std = max(rolling_std, emp_std * 0.5)

        k_model_var = k_kalman_var * 0.5
        std = math.sqrt(std**2 + k_model_var)

        prop = _make_prop(name, team, market, proj, std, line_lookup, latest_opp,
                          proj_ip=proj_ip, proj_bf=projected_bf)
        if prop:
            projections.append(prop)

    # --- Paired teammate filter ---
    from collections import defaultdict

    paired_keep_best = [
        ("strikeouts", "UNDER"),
        ("strikeouts", "OVER"),
    ]
    for mkt, direction in paired_keep_best:
        by_team = defaultdict(list)
        for p in projections:
            if p.get("market") == mkt and p.get("pick") == direction:
                by_team[p.get("team", "")].append(p)
        for tm, picks in by_team.items():
            if len(picks) >= 2:
                picks.sort(key=lambda x: -(x.get("pCover") or 0))
                for p in picks[1:]:
                    p["pick"] = "PASS"
                    p["conf"] = "low"

    return projections


# ---------------------------------------------------------------------------
# Experimental K-skill adjustments
# ---------------------------------------------------------------------------

def _skill_baselines(savant_rates):
    fields = ("whiff_pct", "zone_contact_pct", "chase_pct", "csw_pct", "stuff_score")
    baselines = {}
    for field in fields:
        vals = [float(v.get(field, 0) or 0) for v in savant_rates.values()]
        vals = [v for v in vals if v > 0]
        if len(vals) >= 10:
            avg = float(np.mean(vals))
            sd = float(np.std(vals))
            baselines[field] = {"avg": avg, "sd": max(sd, 0.001), "n": len(vals)}
    return baselines


def _apply_k_skill_adjustment(expected_k_rate, savant, baselines, config):
    """
    Apply optional experimental process-skill modifiers.

    Config shape:
      {
        "weights": {"whiff_pct": 0.02, "zone_contact_pct": -0.02},
        "cap": 0.05
      }

    Weights are multiplier deltas per one standard deviation from league average.
    A 0.02 weight means +1 SD raises expected K rate by 2%.
    """
    if not config:
        return expected_k_rate

    weights = config.get("weights", {})
    if not weights:
        return expected_k_rate

    total = 0.0
    for field, weight in weights.items():
        val = float(savant.get(field, 0) or 0)
        base = baselines.get(field)
        if val <= 0 or not base:
            continue
        z = (val - base["avg"]) / base["sd"]
        total += float(weight) * max(-2.0, min(2.0, z))

    cap = float(config.get("cap", 0.05))
    total = max(-cap, min(cap, total))
    return expected_k_rate * (1.0 + total)


# ---------------------------------------------------------------------------
# Kalman blending
# ---------------------------------------------------------------------------

def _blend_with_kalman(kalman_state, pitcher_id, stat_key,
                       rolling_avg, rolling_std):
    if kalman_state is None or stat_key is None:
        return rolling_avg, rolling_std

    kp = get_pitcher_projection(kalman_state, pitcher_id, stat_key,
                                rolling_avg=rolling_avg)

    proj = kp["proj"]
    kalman_var = kp["var"]

    if kp["source"] in ("kalman_blend", "kalman_only"):
        kalman_std = math.sqrt(kalman_var)
        std = max(rolling_std, kalman_std)
    else:
        std = rolling_std

    return proj, std


# ---------------------------------------------------------------------------
# Rest adjustment
# ---------------------------------------------------------------------------

def _apply_rest_adjustment(proj, market, rest_days):
    """
    Adjust projection based on days of rest between starts.
    """
    if rest_days is None or rest_days == 99:
        return proj

    stat_key = STAT_KEYS.get(market)
    if not stat_key:
        return proj

    if rest_days <= 4:
        adj = REST_ADJUSTMENTS.get("short_rest", {}).get(stat_key, 0.0)
    elif rest_days >= 10:
        adj = REST_ADJUSTMENTS.get("extended_rest", {}).get(stat_key, 0.0)
    elif rest_days >= 6:
        adj = REST_ADJUSTMENTS.get("extra_rest", {}).get(stat_key, 0.0)
    else:
        adj = 0.0

    proj += adj
    return proj


# ---------------------------------------------------------------------------
# Pick generation
# ---------------------------------------------------------------------------

def _american_to_decimal(price):
    if price is None:
        return None
    price = int(price)
    if price > 0:
        return 1.0 + price / 100.0
    elif price < 0:
        return 1.0 + 100.0 / abs(price)
    return None


def _to_win_1u(price):
    if price is None:
        return None
    price = int(price)
    if price > 0:
        return round(100.0 / price, 2)
    elif price < 0:
        return round(abs(price) / 100.0, 2)
    return None


def _make_prop(name, team, market, proj, std, line_lookup, opp,
               proj_ip=None, proj_bf=None):
    nk = _name_key(name)
    line_key = (nk[0], nk[1], market)
    line_data = line_lookup.get(line_key)

    line = None
    over_price = None
    under_price = None
    if line_data:
        line = line_data.get("line")
        over_price = line_data.get("over_price")
        under_price = line_data.get("under_price")

    result = {
        "player": name,
        "team": team,
        "opp": opp,
        "market": market,
        "proj": round(proj, 1),
        "std": round(std, 1),
        "line": line,
        "over_price": over_price,
        "under_price": under_price,
        "pick": "PASS",
        "edge": None,
        "pCover": None,
        "conf": "low",
        "proj_ip": round(proj_ip, 1) if proj_ip is not None else None,
        "proj_bf": round(proj_bf, 1) if proj_bf is not None else None,
    }

    if line is not None and std > 0:
        diff = proj - line
        z = diff / std
        p_over = float(t_dist.cdf(z, df=PROP_T_DF))
        p_under = 1.0 - p_over

        best_p = max(p_over, p_under)
        result["edge"] = round(diff, 1)
        result["pCover"] = round(best_p, 3)

        mkt_thresh = MARKET_THRESHOLDS.get(market, {"high": 0.58})
        direction = "OVER" if p_over > p_under else "UNDER"

        # Always populate odds for the would-be direction (matters for watchlist
        # PASS picks at 0.60-0.70 so units can be computed). Actual pick=
        # OVER/UNDER assignment still gated by the pCover threshold below.
        pick_price = over_price if direction == "OVER" else under_price
        if pick_price is not None:
            result["odds"] = pick_price
            result["to_win_1u"] = _to_win_1u(pick_price)

        thresh = mkt_thresh.get("high_under" if direction == "UNDER" else "high",
                                mkt_thresh["high"])
        if best_p >= thresh:

            if market in UNDER_ONLY_MARKETS and direction == "OVER":
                return result

            abs_edge = abs(diff)
            min_e = MIN_EDGE.get(market, 0)
            max_e = MAX_EDGE.get(market, 999)

            if abs_edge < min_e or abs_edge > max_e:
                return result

            dead = EDGE_DEAD_ZONE.get(market)
            if dead and dead[0] <= abs_edge < dead[1]:
                return result

            min_l = MIN_LINE.get(market, 0)
            if line < min_l:
                return result

            result["pick"] = direction
            result["conf"] = "high"

    return result


# ---------------------------------------------------------------------------
# Dashboard output
# ---------------------------------------------------------------------------

def format_props_for_dashboard(projections, date_str="today"):
    """Format prop projections into dashboard-compatible JSON.

    `props` contains:
      - actionable picks (pick=OVER/UNDER, pCover >= MARKET_THRESHOLDS[market]['high'])
      - watchlist entries (pick=PASS, pCover >= 0.60) tagged with would_be_pick
        so the dashboard hides them but they're persisted for backend analysis.
    """
    import datetime as dt

    actionable = [p for p in projections if p["pick"] != "PASS"]
    actionable.sort(key=lambda p: p.get("pCover", 0) or 0, reverse=True)

    # Watchlist: PASS projections at pCover >= 0.60 with a real line.
    # Add `would_be_pick` (direction inferred from edge) so future grading
    # can score them. Dashboard filters out pick=PASS so they don't display.
    watchlist = []
    for p in projections:
        if p.get("pick") != "PASS":
            continue
        if p.get("line") is None:
            continue
        if (p.get("pCover") or 0) < 0.60:
            continue
        proj_v = p.get("proj")
        line_v = p.get("line")
        if proj_v is None or line_v is None:
            continue
        p_copy = dict(p)
        wbp = "OVER" if proj_v > line_v else "UNDER"
        p_copy["would_be_pick"] = wbp
        p_copy["conf"] = "watch"
        # Backfill odds + to_win_1u from over/under price if missing — leans
        # need real prices to compute units / display correctly.
        if p_copy.get("odds") is None:
            fallback_price = p_copy.get("over_price") if wbp == "OVER" else p_copy.get("under_price")
            if fallback_price is not None:
                p_copy["odds"] = fallback_price
                p_copy["to_win_1u"] = _to_win_1u(fallback_price)
        watchlist.append(p_copy)

    combined = actionable + watchlist

    all_with_lines = [p for p in projections if p.get("line") is not None]

    for p in projections:
        if not p.get("date"):
            p["date"] = date_str
    for p in combined:
        if not p.get("date"):
            p["date"] = date_str

    all_with_lines.sort(key=lambda p: (
        p.get("team") or "",
        p.get("market") or "",
        -(p.get("pCover") or 0),
    ))

    return {
        "sport": "mlb",
        "type": "pitcher_props",
        "season": "2026",
        "mode": "live",
        "model": "kalman_blend",
        "date": date_str,
        "generated": dt.datetime.now().isoformat(),
        "totalProjections": len(projections),
        "totalPicks": len(actionable),
        "totalWatchlist": len(watchlist),
        "props": combined,
        "todayProjections": all_with_lines,
        "summary": f"{len(actionable)} picks (+{len(watchlist)} watch) from {len(projections)} projections",
    }
