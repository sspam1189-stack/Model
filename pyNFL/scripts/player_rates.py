"""
Player efficiency rates from NFL play-by-play game logs.

Computes per-opportunity rates (ypa, completion_pct, ypc, catch_rate, etc.)
from the game-log dicts produced by build_player_game_logs() in props_engine.py.
These rates feed the "how productive per opportunity" side of projections.
"""

import math

# ---------------------------------------------------------------------------
# Thresholds — minimum opportunities to count a game for that role
# ---------------------------------------------------------------------------
MIN_PASS_ATT = 10   # QB games
MIN_RUSH_ATT = 5    # RB games
MIN_TARGETS = 3     # WR/TE games

RATE_VARIANCE_FLOOR = 0.001  # small floor for rate-based variance (not 0.5)


# ---------------------------------------------------------------------------
# 1.  Per-game rate computation
# ---------------------------------------------------------------------------

def compute_player_rates(player_game_logs):
    """
    Compute per-game efficiency rates from raw stat lines.

    Parameters
    ----------
    player_game_logs : dict
        {player_id: [game_log_dict, ...]}  — output of build_player_game_logs().
        Each game_log_dict has keys like pass_yds, pass_att, completions,
        pass_td, rush_yds, rush_att, rec_yds, targets, receptions, etc.

    Returns
    -------
    dict
        {player_id: [{"game_id", "week", "rates": {rate_key: float}}, ...]}
        Only games meeting the minimum-opportunity thresholds are included.
    """
    out = {}

    for pid, games in player_game_logs.items():
        rate_games = []
        for g in games:
            rates = {}

            # --- QB rates ---
            pass_att = g.get("pass_att", 0) or 0
            if pass_att >= MIN_PASS_ATT:
                rates["ypa"] = g.get("pass_yds", 0) / pass_att
                rates["completion_pct"] = g.get("completions", 0) / pass_att
                rates["td_rate"] = g.get("pass_td", 0) / pass_att

            # --- RB rates ---
            rush_att = g.get("rush_att", 0) or 0
            if rush_att >= MIN_RUSH_ATT:
                rates["ypc"] = g.get("rush_yds", 0) / rush_att

            # --- WR / TE rates ---
            targets = g.get("targets", 0) or 0
            if targets >= MIN_TARGETS:
                receptions = g.get("receptions", 0) or 0
                rates["catch_rate"] = receptions / targets
                if receptions > 0:
                    rates["ypr"] = g.get("rec_yds", 0) / receptions
                # ypr undefined when 0 receptions — skip rather than inf/nan

            if rates:
                rate_games.append({
                    "game_id": g.get("game_id"),
                    "week": g.get("week"),
                    "rates": rates,
                })

        if rate_games:
            out[pid] = rate_games

    return out


# ---------------------------------------------------------------------------
# 2.  Exponentially-weighted average rates
# ---------------------------------------------------------------------------

def compute_weighted_rates(player_rate_logs, decay=0.88):
    """
    Compute exponentially-weighted average of per-game rates.

    Parameters
    ----------
    player_rate_logs : list[dict]
        One player's list of rate-game dicts (as returned by compute_player_rates).
        Each element has {"game_id", "week", "rates": {key: float}}.
    decay : float
        Weight decay factor.  Most-recent game gets weight 1.0, previous
        game gets *decay*, the one before that *decay**2*, etc.

    Returns
    -------
    dict
        {rate_key: weighted_avg}  — only keys present in the data appear.
    """
    if not player_rate_logs:
        return {}

    # Collect all rate keys that appear across games
    all_keys = set()
    for entry in player_rate_logs:
        all_keys.update(entry["rates"].keys())

    result = {}
    for key in all_keys:
        vals = [(entry["rates"][key], i)
                for i, entry in enumerate(player_rate_logs)
                if key in entry["rates"]]
        if not vals:
            continue

        n_total = len(player_rate_logs)
        weighted_sum = 0.0
        weight_sum = 0.0
        for val, idx in vals:
            w = decay ** (n_total - 1 - idx)
            weighted_sum += val * w
            weight_sum += w

        result[key] = weighted_sum / weight_sum

    return result


# ---------------------------------------------------------------------------
# 3.  Weighted standard deviation for a single rate key
# ---------------------------------------------------------------------------

def compute_rate_std(player_rate_logs, rate_key, decay=0.88):
    """
    Weighted standard deviation for a specific rate across games.

    Uses a variance floor of RATE_VARIANCE_FLOOR (0.001) — much smaller
    than the 0.5 floor used for raw counting stats, because rates live
    on a tighter scale (e.g. completion_pct ~ 0.60-0.70).

    Parameters
    ----------
    player_rate_logs : list[dict]
        One player's rate-game list (same format as compute_weighted_rates).
    rate_key : str
        Which rate to compute std for (e.g. "ypa", "catch_rate").
    decay : float
        Exponential decay factor.

    Returns
    -------
    float
        Weighted standard deviation (floored at sqrt(RATE_VARIANCE_FLOOR)).
    """
    vals = [entry["rates"][rate_key]
            for entry in player_rate_logs
            if rate_key in entry["rates"]]

    if len(vals) < 2:
        # Not enough data — return a sensible default per rate type
        return _default_std(rate_key)

    n = len(vals)
    weights = [decay ** (n - 1 - i) for i in range(n)]
    w_sum = sum(weights)

    avg = sum(v * w for v, w in zip(vals, weights)) / w_sum
    var = sum(w * (v - avg) ** 2 for v, w in zip(vals, weights)) / w_sum

    return math.sqrt(max(var, RATE_VARIANCE_FLOOR))


def _default_std(rate_key):
    """Fallback std when fewer than 2 games of data exist."""
    defaults = {
        "ypa": 1.5,
        "completion_pct": 0.06,
        "td_rate": 0.02,
        "ypc": 1.0,
        "catch_rate": 0.10,
        "ypr": 3.0,
    }
    return defaults.get(rate_key, 1.0)
