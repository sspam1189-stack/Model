# MLBstrikeouts/scripts/props_engine.py
# MLB pitcher strikeouts prop projection engine with Kalman filtering.

import math
import os
import unicodedata
import numpy as np
from scipy.stats import t as t_dist

_BLEND_FLOOR_MULT = float(os.environ.get("BLEND_FLOOR_MULT", "0.9"))

from defaults import (
    PROP_T_DF, ROLLING_WINDOW, DECAY_FACTOR, MIN_GAMES, MIN_INNINGS, MIN_PITCHES,
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

    Keeps only outings flagged as starts (or 3+ IP relief outings — a
    bullpen-day arm who covered multiple innings). Trusts the upstream
    `is_start` flag so this matches `sitCodes=sp` (Starter) used by
    `fetch_pitcher_advanced_stats`. Kalman state, adv-stats aggregates,
    and per-game projections all draw from the same "what is a start"
    answer.
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
        # Drop scheduled-but-unplayed stubs (ip=0, no recorded outs/K/H/BB).
        # The fetcher pre-populates a placeholder row for tonight's game; if
        # left in, it shifts games[-1]/games[-ROLLING_WINDOW:] and quietly
        # corrupts proj_ip, rolling_std, and rest_days.
        if ip == 0 and g.get("outs", 0) == 0 and g.get("k", 0) == 0 \
                and g.get("h", 0) == 0 and g.get("bb", 0) == 0:
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
                          savant_rates=None, empirical_std=None):
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

        # Qualified-game filter — pitches-based (was IP >= MIN_INNINGS=3.0).
        # Includes short shellings (2 IP with 30+ pitches) as real data about
        # pitcher's current form. Excludes bullpen-day opener cameos (<30
        # pitches). Falls back to IP filter when pitches data is unavailable.
        def _qualifies(g):
            pc = g.get("pitches", 0) or 0
            if pc > 0:
                return pc >= MIN_PITCHES
            return g.get("IP", 0) >= MIN_INNINGS
        qualified = [g for g in recent if _qualifies(g)]

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
        # Blend rolling-45d (recent form) with full season-to-date (stable).
        # SPLITS_BLEND_WEIGHT controls weight on recent: 1.0 = pure recent
        # (legacy behavior), 0.0 = pure season, 0.5 = even blend.
        from defaults import SPLITS_BLEND_WEIGHT
        vl = splits.get("vs_left", {})
        vr = splits.get("vs_right", {})
        vl_season = splits.get("vs_left_season", {})
        vr_season = splits.get("vs_right_season", {})

        def _blended_rate(recent, season_dict, w):
            r_k, r_pa = recent.get("k", 0), recent.get("pa", 0)
            s_k, s_pa = season_dict.get("k", 0), season_dict.get("pa", 0)
            r_rate = r_k / r_pa if r_pa > 0 else None
            s_rate = s_k / s_pa if s_pa > 0 else None
            if r_rate is not None and s_rate is not None:
                return w * r_rate + (1.0 - w) * s_rate
            if s_rate is not None:
                return s_rate
            if r_rate is not None:
                return r_rate
            return 0.0

        k_rate_vs_lhb = _blended_rate(vl, vl_season, SPLITS_BLEND_WEIGHT)
        k_rate_vs_rhb = _blended_rate(vr, vr_season, SPLITS_BLEND_WEIGHT)
        # Combined PA across windows for the "no data" check below
        vl_pa = (vl.get("pa", 0) or 0) + (vl_season.get("pa", 0) or 0)
        vr_pa = (vr.get("pa", 0) or 0) + (vr_season.get("pa", 0) or 0)

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

        # Hand-weighted season K% is the pitcher rate. A rolling-window
        # recent-K% blend used to live here, but every sweep
        # (sweep_season_anchor across multiple regimes, most recently
        # post-Kalman-drop) picked W=1.00 monotonically — the recent signal
        # is noise relative to season-to-date once Kalman is off the mean.
        # Fallback to overall_k_pct (starter-split season K%) only when
        # both hand splits are missing and the weighted rate collapses to 0.
        season_k_rate = k_rate_vs_lhb * pct_lhb + k_rate_vs_rhb * (1.0 - pct_lhb)
        pitcher_k_rate = season_k_rate or overall_k_pct

        # Zone-contact + chase regression K adjustment (sweep candidate).
        from defaults import (ZC_CHASE_BLEND_WEIGHT, ZC_LEAGUE_AVG,
                               CHASE_LEAGUE_AVG, ZC_K_SLOPE, CHASE_K_SLOPE)
        # Properly scaled this time: additive shift in pitcher_k_rate based
        # on how far this pitcher's ZC/chase deviates from league avg.
        if ZC_CHASE_BLEND_WEIGHT > 0:
            zc = savant.get("zone_contact_pct", 0)
            chase = savant.get("chase_pct", 0)
            if zc > 0 or chase > 0:
                k_adj = (
                    ZC_K_SLOPE * (zc - ZC_LEAGUE_AVG)
                    + CHASE_K_SLOPE * (chase - CHASE_LEAGUE_AVG)
                )
                pitcher_k_rate += k_adj * ZC_CHASE_BLEND_WEIGHT

        # --- Lineup K tendency ---
        # Three-tier fallback (best → worst):
        #   1. Hand-aware lineup K% from the posted lineup's 9 hitters
        #   2. Opponent team season K% (hand-agnostic, but team-specific)
        #   3. League-average K%
        # Prior behavior collapsed straight from tier 1 to tier 3, treating
        # all 30 teams as 21.7% K when lineups hadn't posted yet. Team K%
        # spread is ~7pp (TOR 18.0% to LAA 25.2%), so the league-avg
        # fallback systematically over-projected K's against low-K offenses
        # like TOR/TB/CLE and under-projected vs LAA/BAL/COL.
        opp_team_k_pct = opp_stats.get("K_PCT") or 0.0
        if opp_team_k_pct > 0:
            lineup_k_rate = opp_team_k_pct
        else:
            lineup_k_rate = league_avg.get("K_PCT", 0.22)
        lg_k_rate = league_avg.get("K_PCT", 0.22) or 0.22
        if batter_k_rates and lineup_data:
            opp_lineup = lineup_data.get(latest_opp, {})
            opp_player_ids = opp_lineup.get("player_ids", [])
            if opp_player_ids:
                _pitch_hand = adv.get("pitch_hand", "R")
                from sources.mlb_stats import compute_lineup_k_pct
                from defaults import (LINEUP_K_METHOD, SLOT_PA_WEIGHTS,
                                       get_team_slot_weights)
                if LINEUP_K_METHOD == "pa_weighted":
                    _slot_w = SLOT_PA_WEIGHTS
                elif LINEUP_K_METHOD == "pa_weighted_team":
                    _slot_w = get_team_slot_weights(latest_opp)
                else:
                    _slot_w = None
                lk = compute_lineup_k_pct(
                    opp_player_ids, batter_k_rates, _pitch_hand,
                    slot_weights=_slot_w,
                )
                _key = ("lineup_k_pct_vs_hand_pa_weighted"
                        if LINEUP_K_METHOD in ("pa_weighted", "pa_weighted_team")
                        else "lineup_k_pct_vs_hand")
                _val = lk.get(_key, 0) or 0
                if _val > 0:
                    lineup_k_rate = _val

        expected_k_rate = (pitcher_k_rate * lineup_k_rate) / lg_k_rate

        from defaults import K_RATE_CAP_FLOOR, K_RATE_FLOOR

        # Per-pitcher K-rate cap — see defaults.K_RATE_CAP_FLOOR for sweep
        # rationale. cap = max(floor, season K%): mid-tier pitchers can't
        # exceed the floor on matchup boosts; elite K arms get their own
        # proven ceiling.
        _k_cap_used = max(K_RATE_CAP_FLOOR, overall_k_pct)
        expected_k_rate = max(K_RATE_FLOOR, min(_k_cap_used, expected_k_rate))

        # --- Projected batters faced ---
        # Use the real `bf` field from game logs when available (true MLB
        # battersFaced count). Fall back to reconstruction (outs + h + bb + 1)
        # only when bf is missing. The old reconstruction over-counted by
        # ~1.2 BF per game (the +1 always fires, HBP/SF/SH/ROE missed), which
        # pulled avg_ppbf down to 3.71 vs the real 3.92 — and BF_MULT=0.95
        # was empirically compensating for that bias.
        game_bfs = []
        game_pcs = []
        game_ppbfs = []
        for g in qualified:
            _outs = g.get("outs", 0)
            _h = g.get("h", 0)
            _bb = g.get("bb", 0)
            _pc = g.get("pitches", 0)
            _bf = g.get("bf", 0) or (_outs + _h + _bb + 1)
            if _bf > 0 and _pc > 0:
                game_bfs.append(_bf)
                game_pcs.append(_pc)
                game_ppbfs.append(_pc / _bf)

        if game_ppbfs:
            avg_ppbf = _weighted_avg(game_ppbfs)
            # Pitch count: bias up toward the most recent start when it's
            # higher than the rolling avg, to capture the upward pitch-count
            # trend as pitchers stretch out. Skip the bump if the last start
            # was abnormally short (pulled early).
            wavg_pc = _weighted_avg(game_pcs)
            last_pc = game_pcs[-1] if game_pcs else wavg_pc
            if last_pc >= 70:
                avg_pc = max(wavg_pc, last_pc)
            else:
                avg_pc = wavg_pc
            # --- Slope projection (exploratory) ---
            from defaults import PC_SLOPE_WEIGHT, PC_SLOPE_N, PC_SLOPE_DECAY
            if PC_SLOPE_WEIGHT > 0 and len(game_pcs) >= 3:
                pcs = game_pcs[-PC_SLOPE_N:] if PC_SLOPE_N else game_pcs
                n_pc = len(pcs)
                ws = [PC_SLOPE_DECAY ** (n_pc - 1 - i) for i in range(n_pc)]
                xs = list(range(n_pc))
                sw = sum(ws)
                mean_x = sum(w*x for w, x in zip(ws, xs)) / sw
                mean_y = sum(w*y for w, y in zip(ws, pcs)) / sw
                num = sum(w*(x-mean_x)*(y-mean_y) for w, x, y in zip(ws, xs, pcs))
                den = sum(w*(x-mean_x)**2 for w, x in zip(ws, xs))
                if den > 0:
                    slope = num / den
                    intercept = mean_y - slope * mean_x
                    trend_pc = slope * n_pc + intercept
                    avg_pc = (1 - PC_SLOPE_WEIGHT) * avg_pc + PC_SLOPE_WEIGHT * trend_pc
            # --- Pitch-count ceiling ---
            # Cap projected pitches at the recent ceiling (max of last 5
            # starts). Pitchers have hard pitch-count caps from coaching
            # staff that the rolling-avg pitch count ignores. Without this,
            # picks that look like "100-pitch outing → 25 BF" project K's
            # the pitcher cannot physically achieve.
            recent_pcs = game_pcs[-5:] if game_pcs else []
            if recent_pcs:
                avg_pc = min(avg_pc, max(recent_pcs))
            projected_bf = avg_pc / avg_ppbf if avg_ppbf > 0 else 24.0
        else:
            avg_ppbf = 3.9  # MLB league avg pitches/BF — fallback only
            avg_pc = None   # no recent data
            whip = adv.get("WHIP", 0) or 1.20
            projected_bf = proj_ip * (3.0 + whip * 0.7)

        from defaults import BF_MULT as _BF_MULT, BF_CAP as _BF_CAP
        # SHOW REAL PROJECTED BF, BUT CAP IT FOR THE K MATH.
        # We display each pitcher's natural BF projection (after BF_MULT only)
        # so the dashboard shows real expected depth — Cease 25.6, Ohtani 27.4,
        # Skenes 25.8, etc. — instead of clamping every elite starter to 23.
        # The K projection itself still uses the capped value because BF_CAP=23
        # is the empirical optimum: every sweep (2026-05-13, sweep_combined_caps)
        # confirmed that loosening it adds borderline picks that drag WR down
        # and break the lean band. So:
        #     projected_bf_display = real expected BF (what users see)
        #     projected_bf         = min(real, 23) used in K = rate × BF
        projected_bf_display = projected_bf * _BF_MULT
        projected_bf = min(projected_bf_display, _BF_CAP)
        # Projected pitch count: prefer recent avg if we have it; otherwise
        # derive from final projected_bf × league-avg pitches/BF.
        proj_pc = avg_pc if avg_pc is not None else projected_bf * avg_ppbf

        # --- Weather effect on K ---
        # Temperature only. Wind is intentionally NOT used for K projections:
        # the published research (Statcast Weather Applied Metrics, MLB.com
        # 2024) ties wind effects to batted-ball outcomes (HR/FB, BABIP) at
        # ~7 parks, not to whiff rate. Routed through opp_ops the path-2 BF
        # effect tops out at ~0.04 K even at Wrigley with 20mph wind out —
        # below the model's 1.6 K MAE noise floor. Wind data is still
        # captured in the weather cache for future hits/HR markets; see
        # weather.py:WIND_PARK_FACTOR for the per-park multipliers.
        from defaults import (
            WEATHER_K_COLD_TEMP_F, WEATHER_K_HOT_TEMP_F,
            WEATHER_K_COLD_BONUS, WEATHER_K_HOT_PENALTY,
        )
        k_weather_mult = 1.0
        if weather_by_game and (WEATHER_K_COLD_BONUS or WEATHER_K_HOT_PENALTY):
            _game_id = ctx.get("game_id")
            if _game_id:
                w_data = (weather_by_game.get(_game_id)
                          or weather_by_game.get(str(_game_id)))
                if w_data:
                    import re as _re
                    _temp_m = _re.search(r"(\d+)", str(w_data.get("temp", "")))
                    if _temp_m:
                        _temp = int(_temp_m.group(1))
                        if _temp <= WEATHER_K_COLD_TEMP_F:
                            k_weather_mult += WEATHER_K_COLD_BONUS
                        elif _temp >= WEATHER_K_HOT_TEMP_F:
                            k_weather_mult -= WEATHER_K_HOT_PENALTY

        model_proj = expected_k_rate * projected_bf * k_weather_mult

        # --- Kalman blend ---
        # One blend, one call. get_pitcher_projection returns the blended proj
        # and the pitcher's Kalman variance in a single shot. The single knob
        # is PITCHER_KALMAN_DEFAULTS["kalmanBlend"].
        kalman_key = KALMAN_STAT_KEYS.get(market)
        if kalman_state is not None and kalman_key is not None:
            _kp = get_pitcher_projection(kalman_state, str(pid), kalman_key,
                                         rolling_avg=model_proj)
            model_proj = _kp["proj"]
            k_kalman_var = _kp.get("var", 0.0)
        else:
            k_kalman_var = 0.0

        # Rest adjustment
        model_proj = _apply_rest_adjustment(model_proj, market, rest_days)

        proj = model_proj

        # --- Empirical std ---
        # Prefer runtime-calibrated empirical_std (computed from graded
        # population in run_daily.py); fall back to DEFAULT_EMPIRICAL_STD
        # when the caller didn't supply one or the market isn't in it.
        from defaults import DEFAULT_EMPIRICAL_STD
        _emp_src = empirical_std if (empirical_std and market in empirical_std) else DEFAULT_EMPIRICAL_STD
        emp_std = _emp_src.get(market, 2.0)

        if n_games <= 3:
            std = emp_std
        elif n_games <= 7:
            blend = (n_games - 3) / 4.0
            std = emp_std * (1 - blend) + rolling_std * blend
            std = max(std, emp_std * _BLEND_FLOOR_MULT)
        else:
            std = max(rolling_std, emp_std * 0.5)

        # Kalman variance contribution dropped 2026-05-14 (VAR sweep showed
        # VAR_MULT=1.10 with Kalman var off beats VAR_MULT=1.15 with Kalman
        # var on by +30u season at 2u picks / 1u leans sizing, tied recent).
        # kalmanBlend is also 0.0, so the Kalman state no longer affects
        # strikeouts projections.

        prop = _make_prop(name, team, market, proj, std, line_lookup, latest_opp,
                          proj_ip=proj_ip, proj_bf=projected_bf_display, proj_pc=proj_pc,
                          proj_bf_capped=projected_bf)
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
               proj_ip=None, proj_bf=None, proj_pc=None, proj_bf_capped=None):
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
        # proj_bf shows the REAL natural BF projection on the dashboard, but
        # the K math behind the projection uses proj_bf_capped (min with
        # BF_CAP=23). Cap=23 was the ideal config from the unit/ROI sweeps;
        # display gets the uncapped number so elite-K starters' true depth
        # (Cease 25-26, Ohtani 27+, Skenes 26) is visible to users.
        "proj_bf": round(proj_bf, 1) if proj_bf is not None else None,
        "proj_bf_capped": round(proj_bf_capped, 1) if proj_bf_capped is not None else None,
        "proj_pc": round(proj_pc, 0) if proj_pc is not None else None,
        "opp_team_k_pct": round(opp_team_k_pct * 100, 1) if opp_team_k_pct else None,
        "lineup_k_pct": round(lineup_k_rate * 100, 1) if lineup_k_rate else None,
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
