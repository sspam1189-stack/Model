# MLBstrikeouts/scripts/props_engine.py
# MLB pitcher prop projection engine with Kalman filtering + advanced stats.
#
# Projection pipeline:
#   1. Organize per-pitcher game logs from MLB data
#   2. Compute rolling weighted averages (exponential decay)
#   3. Query Kalman filter for smoothed pitcher baseline + uncertainty
#   4. Blend Kalman baseline with rolling average (configurable ratio)
#   5. Anchor strikeouts to xFIP-derived K/9 (stabilize volatile K rates)
#   6. Anchor to season per-9 rates (stabilize early-season projections)
#   7. Adjust for opponent batting (K%, BA, BB% vs league average)
#   8. Adjust for handedness splits (vs LHB/RHB composition)
#   9. Adjust for rest days (extra rest bonus / short rest penalty)
#  10. Apply home/away split
#  11. Compare blended projection to market prop line
#  12. Use Student's t-distribution (with Kalman variance) for cover probability
#  13. Apply market-specific confidence thresholds and edge filters
#  14. Project game-level total hits (sum of both starters + bullpen allowance)

import math
import unicodedata
import numpy as np
from scipy.stats import t as t_dist

from defaults import (
    PROP_T_DF, ROLLING_WINDOW, DECAY_FACTOR, MIN_GAMES, MIN_INNINGS,
    MARKET_THRESHOLDS, VAR_MULT, MIN_EDGE, MAX_EDGE, MIN_LINE,
    UNDER_ONLY_MARKETS, DISABLED_MARKETS, EDGE_DEAD_ZONE,
    OPP_STAT_KEY, OPP_ADJ_WEIGHT, HANDEDNESS_ADJ_WEIGHT,
    XFIP_ANCHOR_WEIGHT, SEASON_ANCHOR_WEIGHT,
    STAT_KEYS, KALMAN_STAT_KEYS,
)
from pitcher_kalman import get_pitcher_projection, PITCHER_KALMAN_DEFAULTS
from sources.game_context import (
    REST_ADJUSTMENTS, detect_rest_days,
    compute_home_away_split, compute_per_inning_rates,
    project_innings, rate_based_projection,
)
from sources.weather import compute_weather_multiplier


# ---------------------------------------------------------------------------
# Name matching
# ---------------------------------------------------------------------------

def _name_key(name):
    """
    Normalize a pitcher name for cross-source matching.

    Strips diacritics so 'Shohei Ohtani' matches across sources.

    'Gerrit Cole'       -> ('gerrit', 'cole')
    'Yoshinobu Yamamoto' -> ('yoshinobu', 'yamamoto')
    'Chris Sale Jr.'    -> ('chris', 'sale')
    """
    name = name.strip()
    if not name:
        return ('', '')
    # Strip diacritics (e.g. accented characters -> ASCII)
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

    Filters to starts only (IP >= 3.0 or is_start flag) to exclude
    relief appearances.

    Parameters
    ----------
    raw_logs : list[dict]
        Raw pitcher game logs with fields like pitcher_id, game_date, IP,
        strikeouts, hits_allowed, walks, etc.

    Returns
    -------
    dict
        {pitcher_id: [game_log, ...]} sorted oldest-first, starts only.
    """
    by_pitcher = {}
    for g in raw_logs:
        pid = g.get("pitcher_id")
        if pid is None:
            continue
        # Filter to starts only: IP >= 3.0 or explicit is_start flag
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
    """Exponentially weighted average (most recent = highest weight)."""
    if not values:
        return 0.0
    n = len(values)
    weights = [decay ** (n - 1 - i) for i in range(n)]
    return sum(v * w for v, w in zip(values, weights)) / sum(weights)


def _weighted_std(values, decay=DECAY_FACTOR):
    """Weighted standard deviation."""
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
    """
    Check if a pitcher is listed as OUT or on the IL in the injury report.

    Parameters
    ----------
    name : str
        Pitcher's display name.
    team : str
        Pitcher's team abbreviation.
    injury_report : list[dict]
        [{name, team, status, ...}] from injury data source.

    Returns
    -------
    bool
        True if pitcher should be excluded from projections.
    """
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
                          injury_report=None, weather_by_game=None):
    """
    Project pitcher props for all pitchers with sufficient game logs.

    Parameters
    ----------
    pitcher_logs : dict
        {pitcher_id: [game_log, ...]} from organize_pitcher_logs.
    team_batting_stats : dict or None
        {team_abbr: {"K_PCT": float, "BA": float, "OPS": float, "BB_PCT": float,
                     "PCT_LHB": float (optional)}}
    prop_lines : list[dict] or None
        Market prop lines from FanDuel / Odds API.
    kalman_state : dict or None
        Per-pitcher Kalman state from pitcher_kalman.
    pitcher_adv_stats : dict or None
        {pitcher_id_str: {"K_PER_9": float, "BB_PER_9": float, "H_PER_9": float,
                          "WHIP": float, ...}}
    pitcher_sabermetrics : dict or None
        {pitcher_id_str: {"fip": float, "xfip": float}}
    pitcher_splits : dict or None
        {pitcher_id_str: {"vs_right": {"k_per_9": float, "ba": float, ...},
                          "vs_left": {"k_per_9": float, "ba": float, ...}}}
    probable_pitchers : list[dict] or None
        [{"game_id": str, "home_team": str, "away_team": str,
          "home_pitcher": str, "away_pitcher": str,
          "home_pitcher_id": int, "away_pitcher_id": int}]
    injury_report : list[dict] or None
        [{name, team, status, ...}] from injury data source.

    Returns
    -------
    list[dict]
        Projections with picks where applicable.
    """
    projections = []

    # Compute league averages for opponent batting adjustment
    league_avg = {}
    if team_batting_stats:
        for k in ("K_PCT", "BA", "OPS", "BB_PCT"):
            vals = [s.get(k, 0) for s in team_batting_stats.values() if s.get(k)]
            league_avg[k] = np.mean(vals) if vals else 0.0

    # Index prop lines by (first_name, last_name, market)
    line_lookup = {}
    # Build pitcher -> game context from prop lines or probable pitchers
    pitcher_game_ctx = {}  # pitcher_name_key -> {opp, is_home, team}
    if prop_lines:
        for pl in prop_lines:
            nk = _name_key(pl.get("player", ""))
            key = (nk[0], nk[1], pl.get("market", ""))
            line_lookup[key] = pl

    # Build pitcher game context from probable pitchers
    # Also build pitcher_id -> game context for ID-based lookup
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

        # Skip injured pitchers
        if injury_report and _is_pitcher_out(name, team, injury_report):
            continue

        # Resolve game context: check if this pitcher is a probable starter today
        # Try by pitcher_id first, then by name key
        ctx = pitcher_id_ctx.get(pid, {})
        if not ctx:
            nk = _name_key(name)
            ctx = pitcher_game_ctx.get(nk, {})

        # If we have probable pitchers data but this pitcher isn't in it,
        # they're not starting today — skip
        if probable_pitchers and not ctx:
            continue

        latest_opp = ctx.get("opp", games[-1].get("opp", ""))
        is_home = ctx.get("is_home", games[-1].get("is_home", True))
        game_date = games[-1].get("game_date", "")

        # Filter to starts with enough innings
        qualified = [g for g in recent if g.get("IP", 0) >= MIN_INNINGS]

        # Get this pitcher's advanced stats and sabermetrics
        adv = (pitcher_adv_stats or {}).get(str(pid), {})
        saber = (pitcher_sabermetrics or {}).get(str(pid), {})
        splits = (pitcher_splits or {}).get(str(pid), {})

        # --- Per-inning rates ---
        rates = compute_per_inning_rates(qualified)

        # --- Projected innings ---
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
            continue  # Skip pitchers projected for very few innings

        # --- Project each individual market ---
        for market, stat_key in STAT_KEYS.items():
            # Skip disabled markets
            if market in DISABLED_MARKETS:
                continue

            min_g = MIN_GAMES.get(market, 3)
            vals = [g.get(stat_key, 0) for g in qualified]

            if len(vals) < min_g:
                continue

            # --- 1. Rolling average (traditional) ---
            rolling_avg = _weighted_avg(vals)
            rolling_std = _weighted_std(vals) * VAR_MULT.get(market, 1.2)

            # Small-sample variance inflation: with few games, we can't
            # estimate true variance — inflate so pCover is harder to clear.
            # This replaces hard MIN_GAMES filters with principled uncertainty.
            n_games = len(vals)
            if n_games <= 3:
                # 2 games → ×1.8, 3 games → ×1.35, 4+ → ×1.0
                small_sample_mult = 1.0 + 1.6 / (n_games ** 1.5)
                rolling_std *= small_sample_mult

            # --- 2. Rate-based projection (rate x projected IP) ---
            rate_key = f"{stat_key}_per_ip"
            if rate_key in rates and rates[rate_key] > 0:
                rate_proj = rate_based_projection(rates[rate_key], proj_ip)
                blended_raw = 0.4 * rate_proj + 0.6 * rolling_avg
            else:
                blended_raw = rolling_avg

            # --- 3. xFIP anchor (strikeouts only) ---
            if market == "strikeouts" and saber.get("xfip"):
                k_per_9 = adv.get("K_PER_9", 0) or adv.get("k_per_9", 0)
                xfip_k9 = _xfip_to_k9(saber["xfip"], k_per_9)
                if xfip_k9 > 0:
                    xfip_proj = xfip_k9 * proj_ip / 9.0
                    blended_raw = ((1 - XFIP_ANCHOR_WEIGHT) * blended_raw
                                   + XFIP_ANCHOR_WEIGHT * xfip_proj)

            # --- 4. Season anchor (per-9 baseline) ---
            anchor_w = SEASON_ANCHOR_WEIGHT.get(market, 0.0)
            if anchor_w > 0 and adv:
                season_rate = _get_season_rate(adv, market)
                if season_rate and season_rate > 0:
                    season_proj = season_rate * proj_ip / 9.0
                    blended_raw = ((1 - anchor_w) * blended_raw
                                   + anchor_w * season_proj)

            # --- 5. Kalman blending ---
            kalman_key = KALMAN_STAT_KEYS.get(market)
            proj, std = _blend_with_kalman(
                kalman_state, str(pid), kalman_key,
                blended_raw, rolling_std,
            )

            # --- 6. Opponent batting adjustment ---
            proj = _apply_opp_adjustment(proj, market, latest_opp,
                                         team_batting_stats, league_avg)

            # --- 7. Handedness adjustment ---
            proj = _apply_handedness_adjustment(proj, market, splits,
                                                latest_opp, team_batting_stats)

            # --- 8. Rest adjustment ---
            proj = _apply_rest_adjustment(proj, market, rest_days)

            # --- 9. Home/away split ---
            split = compute_home_away_split(games, stat_key)
            if is_home and split.get("home_split_adj"):
                proj += split["home_split_adj"]
            elif not is_home and split.get("away_split_adj"):
                proj += split["away_split_adj"]

            # --- 9b. Weather adjustment (hits markets only) ---
            if market == "hits_allowed" and weather_by_game:
                _game_id = ctx.get("game_id")
                if _game_id:
                    w_data = (weather_by_game.get(_game_id)
                              or weather_by_game.get(str(_game_id)))
                    if w_data:
                        _home_team = ctx.get("team") if is_home else ctx.get("opp", "")
                        w_mult = compute_weather_multiplier(w_data, _home_team)
                        proj *= w_mult

            # --- 10. Make pick ---
            prop = _make_prop(name, team, market, proj, std, line_lookup, latest_opp)
            if prop:
                projections.append(prop)

    # --- Game hits (game-level market) ---
    if "game_hits" not in DISABLED_MARKETS and probable_pitchers:
        game_hits_props = _project_game_hits(
            probable_pitchers, projections, team_batting_stats, line_lookup,
        )
        projections.extend(game_hits_props)

    # --- Paired teammate filter ---
    # When 2+ pitchers from the same game both pick the same direction,
    # correlation between them drags down win rate. Keep best pCover only.
    from collections import defaultdict

    paired_keep_best = [
        ("strikeouts", "UNDER"),
        ("strikeouts", "OVER"),
    ]
    for market, direction in paired_keep_best:
        by_team = defaultdict(list)
        for p in projections:
            if p.get("market") == market and p.get("pick") == direction:
                by_team[p.get("team", "")].append(p)
        for team, picks in by_team.items():
            if len(picks) >= 2:
                picks.sort(key=lambda x: -(x.get("pCover") or 0))
                for p in picks[1:]:
                    p["pick"] = "PASS"
                    p["conf"] = "low"

    return projections


# ---------------------------------------------------------------------------
# Kalman blending
# ---------------------------------------------------------------------------

def _blend_with_kalman(kalman_state, pitcher_id, stat_key,
                       rolling_avg, rolling_std):
    """
    Blend Kalman projection with rolling average.

    Returns (blended_proj, blended_std).
    """
    if kalman_state is None or stat_key is None:
        return rolling_avg, rolling_std

    kp = get_pitcher_projection(kalman_state, pitcher_id, stat_key,
                                rolling_avg=rolling_avg)

    proj = kp["proj"]
    kalman_var = kp["var"]

    # Use Kalman variance to inform std if source includes Kalman
    if kp["source"] in ("kalman_blend", "kalman_only"):
        kalman_std = math.sqrt(kalman_var)
        std = max(rolling_std, kalman_std)
    else:
        std = rolling_std

    return proj, std


# ---------------------------------------------------------------------------
# Adjustments: xFIP anchor, season anchor, opponent, handedness, rest
# ---------------------------------------------------------------------------

def _xfip_to_k9(xfip, actual_k9):
    """
    Convert xFIP to an implied K/9 rate.

    xFIP normalizes HR/FB rate to league average. The relationship between
    xFIP and K/9 is approximately:
        implied_K9 ~ actual_K9 * (league_avg_FIP / xFIP)

    When xFIP < actual FIP, the pitcher is "unlucky" on HR and their
    true talent K/9 is likely higher than observed.

    If actual_k9 is available, blend xFIP signal with it.
    If not, estimate from xFIP using league-average relationship.

    Parameters
    ----------
    xfip : float
        Expected Fielding Independent Pitching.
    actual_k9 : float
        Pitcher's actual K/9 rate this season.

    Returns
    -------
    float
        Implied K/9 rate anchored to xFIP.
    """
    if not xfip or xfip <= 0:
        return actual_k9 if actual_k9 > 0 else 0.0

    # League-average constants (approximate)
    LEAGUE_AVG_FIP = 4.00
    LEAGUE_AVG_K9 = 8.5

    if actual_k9 > 0:
        # xFIP-implied adjustment: if xFIP is lower than league avg FIP,
        # the pitcher's true talent K rate is likely higher
        # Blend: 70% actual K/9 + 30% xFIP-derived estimate
        xfip_ratio = LEAGUE_AVG_FIP / xfip if xfip > 0 else 1.0
        xfip_implied_k9 = LEAGUE_AVG_K9 * xfip_ratio
        return 0.7 * actual_k9 + 0.3 * xfip_implied_k9
    else:
        # No actual K/9 — estimate entirely from xFIP
        xfip_ratio = LEAGUE_AVG_FIP / xfip if xfip > 0 else 1.0
        return LEAGUE_AVG_K9 * xfip_ratio


def _get_season_rate(adv_stats, market):
    """
    Get the season per-9 rate for a given market from advanced stats.

    Used for season anchoring to stabilize projections early in the season
    when rolling windows are noisy.

    Parameters
    ----------
    adv_stats : dict
        Pitcher advanced stats: {"K_PER_9", "BB_PER_9", "H_PER_9", "HR_PER_9", ...}
    market : str
        Market name.

    Returns
    -------
    float or None
        Per-9 rate for the market, or None if not available.
    """
    _RATE_MAP = {
        "strikeouts": ("K_PER_9", "k_per_9"),
        "walks": ("BB_PER_9", "bb_per_9"),
        "hits_allowed": ("H_PER_9", "h_per_9"),
        "outs": None,  # derived, no direct per-9 rate
    }

    keys = _RATE_MAP.get(market)
    if keys is None:
        return None

    for k in keys if isinstance(keys, tuple) else [keys]:
        val = adv_stats.get(k, 0)
        if val and val > 0:
            return val

    return None


def _apply_opp_adjustment(proj, market, opp_team, team_batting_stats, league_avg):
    """
    Adjust projection based on opponent batting stats.

    For strikeouts: teams with high K% face more strikeouts.
    For hits_allowed: teams with high BA produce more hits.
    For walks: teams with high BB% draw more walks.

    Uses multiplicative adjustment: scales projection by how far the opponent
    deviates from league average in the relevant stat.
    """
    if not team_batting_stats or not opp_team:
        return proj

    opp_key = OPP_STAT_KEY.get(market)
    weight = OPP_ADJ_WEIGHT.get(market, 0.0)
    if not opp_key or weight == 0.0:
        return proj

    opp_stats = team_batting_stats.get(opp_team, {})
    opp_val = opp_stats.get(opp_key, 0.0)
    avg_val = league_avg.get(opp_key, 0.0)

    if opp_val > 0 and avg_val > 0:
        # Multiplicative: scale by projection size (relative to league avg)
        diff = (opp_val - avg_val) / avg_val

        # Invert for outs: high OPS = shorter outings = FEWER outs
        # (all other markets: higher opponent stat = more of that stat)
        if market == "outs":
            diff = -diff

        proj += diff * weight * proj

    return proj


def _apply_handedness_adjustment(proj, market, pitcher_splits,
                                 opp_team, team_batting_stats):
    """
    Adjust based on pitcher's splits vs LHB/RHB and opposing lineup composition.

    Uses the pitcher's per-9 rates against left-handed and right-handed batters,
    weighted by the opposing team's lineup handedness composition.

    Parameters
    ----------
    proj : float
        Current projection.
    market : str
        Market name (strikeouts, hits_allowed, walks, outs).
    pitcher_splits : dict
        {"vs_right": {"k_per_9": float, "ba": float, ...},
         "vs_left": {"k_per_9": float, "ba": float, ...}}
    opp_team : str
        Opposing team abbreviation.
    team_batting_stats : dict or None
        {team_abbr: {..., "PCT_LHB": float (optional)}}
    """
    weight = HANDEDNESS_ADJ_WEIGHT.get(market, 0.0)
    if weight == 0.0 or not pitcher_splits:
        return proj

    vs_left = pitcher_splits.get("vs_left", {})
    vs_right = pitcher_splits.get("vs_right", {})

    # Get opposing team's lineup composition (approximate: use team roster handedness)
    # Default to 60% RHB, 40% LHB if unknown
    opp_stats = (team_batting_stats or {}).get(opp_team, {})
    pct_lhb = opp_stats.get("PCT_LHB", 0.40)
    pct_rhb = 1.0 - pct_lhb

    # Get pitcher's rate vs each handedness
    # Use rate stats that actually exist in the splits data:
    #   - "ba" (batting avg against) for hits_allowed
    #   - "k_rate" or fall back to k/pa for strikeouts
    #   - "bb_rate" or fall back to bb/pa for walks
    stat_rate_key = {
        "strikeouts": "k_per_9",
        "hits_allowed": "ba",       # BA against is in the splits data
        "walks": "bb_per_9",
    }.get(market)

    if not stat_rate_key:
        return proj

    try:
        rate_vs_l = float(vs_left.get(stat_rate_key, 0) or 0)
        rate_vs_r = float(vs_right.get(stat_rate_key, 0) or 0)
    except (ValueError, TypeError):
        rate_vs_l, rate_vs_r = 0.0, 0.0

    # Fallback: compute rate from counting stats if per-9 not available
    if rate_vs_l == 0 and rate_vs_r == 0:
        count_key, denom_key = {
            "strikeouts": ("k", "pa"),
            "hits_allowed": ("h", "ab"),
            "walks": ("bb", "pa"),
        }.get(market, (None, None))
        if count_key and denom_key:
            l_count = vs_left.get(count_key, 0)
            l_denom = vs_left.get(denom_key, 0)
            r_count = vs_right.get(count_key, 0)
            r_denom = vs_right.get(denom_key, 0)
            if l_denom > 0:
                rate_vs_l = l_count / l_denom
            if r_denom > 0:
                rate_vs_r = r_count / r_denom

    if rate_vs_l > 0 and rate_vs_r > 0:
        weighted_rate = rate_vs_l * pct_lhb + rate_vs_r * pct_rhb
        overall_rate = (rate_vs_l + rate_vs_r) / 2  # approximate
        if overall_rate > 0:
            adj = (weighted_rate - overall_rate) / overall_rate * proj * weight
            proj += adj

    return proj


def _apply_rest_adjustment(proj, market, rest_days):
    """
    Adjust projection based on days of rest between starts.

    Short rest (4 days or fewer) = penalty.
    Extra rest (6-9 days) = bonus.
    Extended rest (10+ days) = rust penalty.
    Normal rest (5 days) = no adjustment.

    REST_ADJUSTMENTS from game_context is keyed by rest category
    ("short_rest", "extra_rest", "extended_rest"), with values keyed
    by stat key ("k", "outs", "h", "bb"). We map market -> stat_key
    to look up the correct adjustment.
    """
    if rest_days is None or rest_days == 99:
        return proj

    # Map market name to the stat key used in REST_ADJUSTMENTS
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
        adj = 0.0  # normal rest (5 days)

    proj += adj
    return proj


# ---------------------------------------------------------------------------
# Game hits projection
# ---------------------------------------------------------------------------

def _project_game_hits(games, pitcher_projections, team_batting_stats, line_lookup):
    """
    Project total game hits by summing both pitchers' projected hits allowed
    plus a bullpen hit allowance.

    Parameters
    ----------
    games : list[dict]
        Probable pitcher matchups: [{game_id, home_team, away_team,
        home_pitcher, away_pitcher}]
    pitcher_projections : list[dict]
        Already-computed pitcher projections (from project_pitcher_props).
    team_batting_stats : dict or None
        {team_abbr: {K_PCT, BA, OPS, BB_PCT}}
    line_lookup : dict
        Prop line lookup by (first, last, market).

    Returns
    -------
    list[dict]
        Game-level hit projections.
    """
    # League average bullpen hits per 9 innings (~9.0 H/9 historical average)
    BULLPEN_H_PER_9 = 9.0
    AVG_STARTER_IP = 5.5  # league avg innings per start

    results = []

    # Index pitcher projections by name key for lookup
    pitcher_ha_by_nk = {}
    pitcher_ip_by_nk = {}
    for p in pitcher_projections:
        if p.get("market") == "hits_allowed":
            nk = _name_key(p.get("player", ""))
            pitcher_ha_by_nk[nk] = p.get("proj", 0)
        # Estimate projected IP from outs market if available
        if p.get("market") == "outs":
            nk = _name_key(p.get("player", ""))
            pitcher_ip_by_nk[nk] = p.get("proj", 0) / 3.0  # outs -> IP

    for gm in games:
        home_team = gm.get("home_team", "")
        away_team = gm.get("away_team", "")
        home_pitcher = gm.get("home_pitcher", "")
        away_pitcher = gm.get("away_pitcher", "")

        if not home_pitcher or not away_pitcher:
            continue

        home_nk = _name_key(home_pitcher)
        away_nk = _name_key(away_pitcher)

        # Get hits allowed projections for each starter
        home_ha = pitcher_ha_by_nk.get(home_nk)
        away_ha = pitcher_ha_by_nk.get(away_nk)

        if home_ha is None or away_ha is None:
            continue

        # Get projected IP for each starter (default to league avg)
        home_ip = pitcher_ip_by_nk.get(home_nk, AVG_STARTER_IP)
        away_ip = pitcher_ip_by_nk.get(away_nk, AVG_STARTER_IP)

        # Bullpen allowance: (9 - starter_IP) x league_avg_bullpen_H/9 / 9
        home_bullpen_ip = max(0, 9.0 - home_ip)
        away_bullpen_ip = max(0, 9.0 - away_ip)
        bullpen_hits = (home_bullpen_ip + away_bullpen_ip) * BULLPEN_H_PER_9 / 9.0

        # Total game hits: both starters' hits allowed + bullpen allowance
        total_hits = home_ha + away_ha + bullpen_hits

        # Std: rough estimate based on game-level volatility
        # Game hits typically vary with std ~2.5
        std = 2.5

        # Build game hits prop
        game_label = f"{away_team} @ {home_team}"
        prop = _make_prop(
            name=game_label,
            team=f"{away_team}@{home_team}",
            market="game_hits",
            proj=total_hits,
            std=std,
            line_lookup=line_lookup,
            opp="",
        )
        if prop:
            prop["home_team"] = home_team
            prop["away_team"] = away_team
            prop["home_pitcher"] = home_pitcher
            prop["away_pitcher"] = away_pitcher
            results.append(prop)

    return results


# ---------------------------------------------------------------------------
# Pick generation
# ---------------------------------------------------------------------------

def _american_to_decimal(price):
    """Convert American odds to decimal odds (e.g. -110 -> 1.909, +150 -> 2.5)."""
    if price is None:
        return None
    price = int(price)
    if price > 0:
        return 1.0 + price / 100.0
    elif price < 0:
        return 1.0 + 100.0 / abs(price)
    return None


def _to_win_1u(price):
    """Calculate wager needed to win 1 unit at given American odds."""
    if price is None:
        return None
    price = int(price)
    if price > 0:
        return round(100.0 / price, 2)
    elif price < 0:
        return round(abs(price) / 100.0, 2)
    return None


def _make_prop(name, team, market, proj, std, line_lookup, opp):
    """Build a single prop projection + pick."""
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

        # Directional threshold: use high_under for UNDER if available
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

            # Edge dead zone: skip edges in the uncertain middle range
            dead = EDGE_DEAD_ZONE.get(market)
            if dead and dead[0] <= abs_edge < dead[1]:
                return result

            min_l = MIN_LINE.get(market, 0)
            if line < min_l:
                return result

            # Attach the relevant odds for the picked direction
            pick_price = over_price if direction == "OVER" else under_price

            result["pick"] = direction
            result["conf"] = "high"
            result["odds"] = pick_price
            result["to_win_1u"] = _to_win_1u(pick_price)

    return result


# ---------------------------------------------------------------------------
# Dashboard output
# ---------------------------------------------------------------------------

def format_props_for_dashboard(projections, date_str="today"):
    """Format prop projections into dashboard-compatible JSON."""
    import datetime as dt

    picks = [p for p in projections if p["pick"] != "PASS"]
    picks.sort(key=lambda p: p.get("pCover", 0) or 0, reverse=True)

    # Ensure every pick has a date field for dashboard filtering
    for p in picks:
        if not p.get("date"):
            p["date"] = date_str

    # All projections for today (including PASS) -- used by Games Explorer
    # Only include pitchers who have a prop line (actually pitching today)
    today_all = [p for p in projections if p.get("line") is not None]
    for p in today_all:
        if not p.get("date"):
            p["date"] = date_str
    today_all.sort(key=lambda p: (
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
        "totalPicks": len(picks),
        "props": picks,
        "todayProjections": today_all,
        "summary": f"{len(picks)} picks from {len(projections)} projections",
    }
