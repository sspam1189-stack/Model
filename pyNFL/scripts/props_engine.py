# pyNFL/scripts/props_engine.py
# NFL player prop projection engine.
#
# Projects per-player stats (passing yards, rushing yards, receiving yards,
# receptions) using rolling averages from nflfastr play-by-play data,
# adjusted for opponent defense strength and game environment.
#
# Output: picks comparing projections vs. market prop lines.

import math
import numpy as np
from scipy.stats import t as t_dist

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROP_T_DF = 4  # Student's t for prop confidence (heavier tails than game totals)

# Minimum sample sizes
MIN_GAMES_PASSER = 3
MIN_GAMES_RUSHER = 3
MIN_GAMES_RECEIVER = 3

# Rolling window (recent games weighted more)
ROLLING_WINDOW = 6
DECAY_FACTOR = 0.88  # Exponential decay per game

# Prop confidence thresholds (per-market — passing is more predictable)
PROP_PROB_HIGH = 0.58
PROP_PROB_ELITE = 0.64

# Market-specific thresholds (rush/rec need higher bar due to higher variance)
MARKET_THRESHOLDS = {
    "pass_yds":      {"high": 0.58, "elite": 0.64},
    "pass_tds":      {"high": 0.62, "elite": 0.70},  # TD count is low-volume, noisy
    "pass_att":      {"high": 0.60, "elite": 0.68},
    "completions":   {"high": 0.60, "elite": 0.68},
    "rush_yds":      {"high": 0.72, "elite": 0.78},  # RB usage is very volatile
    "rush_att":      {"high": 0.65, "elite": 0.72},
    "rec_yds":       {"high": 0.72, "elite": 0.78},  # WR targets are volatile
    "receptions":    {"high": 0.75, "elite": 0.80},  # Catch count is very noisy
    "interceptions": {"high": 0.70, "elite": 0.78},  # Rare event — need high bar
}

# Variance multipliers (player stats are more variable than team totals)
VAR_MULT = {
    "pass_yds":      1.2,
    "pass_tds":      1.5,   # TDs are low-count, high variance
    "pass_att":      1.1,   # Attempts are stable
    "completions":   1.1,   # Completions are stable
    "rush_yds":      1.5,   # RB usage is highly variable
    "rush_att":      1.2,   # Rush attempts are moderately stable
    "rec_yds":       1.6,   # WR targets are volatile
    "receptions":    1.3,
    "interceptions": 1.8,   # Rare event, very noisy
}

# Legacy aliases
PASS_YDS_VAR_MULT = VAR_MULT["pass_yds"]
RUSH_YDS_VAR_MULT = VAR_MULT["rush_yds"]
REC_YDS_VAR_MULT = VAR_MULT["rec_yds"]
RECEPTIONS_VAR_MULT = VAR_MULT["receptions"]

# Directional filter
# UNDER-only markets: sportsbooks set lines high to attract OVER action
# Pass TDs is the exception: OVER wins 60.3% (books set TD lines low)
UNDER_ONLY_MARKETS = {"pass_yds", "rush_yds", "rec_yds", "receptions", "rush_att"}
OVER_ONLY_MARKETS = {"pass_tds"}  # OVER 60.3% vs UNDER 48.3%

# ---------------------------------------------------------------------------
# Calibration offsets — REMOVED (root cause fixed)
# ---------------------------------------------------------------------------
# The under-projection bias was caused by low minimum attempt filters:
#   - pass_att >= 10: included backup QBs with 12 garbage time attempts
#   - rush_att >= 5: included QBs with 5 scrambles (not real rushers)
#   - targets >= 2: included RBs with 2 dump-offs (not real receivers)
# These low-volume games dragged down the rolling average far below
# the player's true production level.
# Fixed by raising filters to: pass_att >= 15, rush_att >= 8, targets >= 4

# Minimum edge size per market (|proj - line|)
# Original +155u filters — DO NOT CHANGE without re-backtesting
MIN_EDGE = {"pass_yds": 20, "pass_tds": 0.3, "receptions": 0.5, "rush_att": 2}
MAX_EDGE = {"pass_yds": 50, "pass_tds": 2, "rush_att": 8}

# Minimum line value per market (filters out low-volume players where noise dominates)
# rec_yds line<50: 64W-72L (-15u) vs line>=50: 31W-13L (+16.7u)
MIN_LINE = {"rec_yds": 50}


# ---------------------------------------------------------------------------
# Name matching (nflfastr uses "K.Murray", Odds API uses "Kyler Murray")
# ---------------------------------------------------------------------------

def _name_key(name):
    """
    Normalize a player name to (first_initial, last_name) for cross-source matching.

    'K.Murray'       -> ('k', 'murray')
    'Kyler Murray'   -> ('k', 'murray')
    'De\'Von Achane' -> ('d', 'achane')
    'T.J. Watt'      -> ('t', 'watt')
    'A.J. Brown'     -> ('a', 'brown')
    """
    name = name.strip()
    if not name:
        return ('', '')

    # nflfastr format: "K.Murray" or "De.Smith"
    if '.' in name and ' ' not in name:
        parts = name.split('.', 1)
        return (parts[0][0].lower(), parts[1].lower())

    # Full name: "Kyler Murray", "De'Von Achane", "T.J. Watt", "Marvin Harrison Jr."
    parts = name.split()
    # Strip suffixes like Jr., Sr., II, III, IV
    suffixes = {'jr', 'jr.', 'sr', 'sr.', 'ii', 'iii', 'iv', 'v'}
    while len(parts) > 2 and parts[-1].lower().rstrip('.') in suffixes:
        parts.pop()
    if len(parts) >= 2:
        first_initial = parts[0][0].lower()
        last = parts[-1].lower().rstrip('.')
        return (first_initial, last)

    return (name[0].lower(), name.lower())


# ---------------------------------------------------------------------------
# Player game log aggregation
# ---------------------------------------------------------------------------

def build_player_game_logs(pbp_df):
    """
    Aggregate play-by-play data into per-player per-game stat lines.

    Parameters
    ----------
    pbp_df : pd.DataFrame
        Play-by-play data with columns from nflfastr.PBP_COLUMNS.

    Returns
    -------
    dict
        {player_id: [{"game_id", "week", "team", "role", stats...}, ...]}
    """
    import pandas as pd

    logs = {}

    # --- Passers ---
    pass_plays = pbp_df[pbp_df["pass"] == 1].copy()
    if not pass_plays.empty:
        passer_games = pass_plays.groupby(
            ["passer_player_id", "passer_player_name", "game_id", "week", "posteam", "defteam"]
        ).agg(
            pass_yds=("yards_gained", "sum"),
            pass_att=("pass", "sum"),
            completions=("complete_pass", "sum"),
            pass_td=("touchdown", "sum"),
            interceptions=("interception", "sum"),
            air_yds=("air_yards", "sum"),
            epa_total=("epa", "sum"),
        ).reset_index()

        for _, row in passer_games.iterrows():
            pid = row["passer_player_id"]
            if pid is None or (isinstance(pid, float) and math.isnan(pid)):
                continue
            if pid not in logs:
                logs[pid] = []
            logs[pid].append({
                "game_id": row["game_id"],
                "week": int(row["week"]),
                "team": row["posteam"],
                "opp": row["defteam"],
                "name": row["passer_player_name"],
                "role": "passer",
                "pass_yds": float(row["pass_yds"]),
                "pass_att": int(row["pass_att"]),
                "completions": int(row["completions"]),
                "pass_td": int(row["pass_td"]),
                "interceptions": int(row["interceptions"]),
                "epa": float(row["epa_total"]),
            })

    # --- Rushers ---
    rush_plays = pbp_df[pbp_df["rush"] == 1].copy()
    if not rush_plays.empty:
        rusher_games = rush_plays.groupby(
            ["rusher_player_id", "rusher_player_name", "game_id", "week", "posteam", "defteam"]
        ).agg(
            rush_yds=("yards_gained", "sum"),
            rush_att=("rush", "sum"),
            rush_td=("touchdown", "sum"),
            epa_total=("epa", "sum"),
        ).reset_index()

        for _, row in rusher_games.iterrows():
            pid = row["rusher_player_id"]
            if pid is None or (isinstance(pid, float) and math.isnan(pid)):
                continue
            if pid not in logs:
                logs[pid] = []
            # Check if this player already has a game entry (e.g., also a passer)
            existing = [g for g in logs[pid] if g["game_id"] == row["game_id"]]
            if existing:
                existing[0]["rush_yds"] = float(row["rush_yds"])
                existing[0]["rush_att"] = int(row["rush_att"])
                existing[0]["rush_td"] = int(row["rush_td"])
            else:
                logs[pid].append({
                    "game_id": row["game_id"],
                    "week": int(row["week"]),
                    "team": row["posteam"],
                    "opp": row["defteam"],
                    "name": row["rusher_player_name"],
                    "role": "rusher",
                    "rush_yds": float(row["rush_yds"]),
                    "rush_att": int(row["rush_att"]),
                    "rush_td": int(row["rush_td"]),
                    "epa": float(row["epa_total"]),
                })

    # --- Receivers ---
    rec_plays = pbp_df[(pbp_df["pass"] == 1) & (pbp_df["receiver_player_id"].notna())].copy()
    if not rec_plays.empty:
        rec_games = rec_plays.groupby(
            ["receiver_player_id", "receiver_player_name", "game_id", "week", "posteam", "defteam"]
        ).agg(
            rec_yds=("yards_gained", "sum"),
            targets=("pass", "sum"),
            receptions=("complete_pass", "sum"),
            rec_td=("touchdown", "sum"),
            air_yds=("air_yards", "sum"),
            yac=("yards_after_catch", "sum"),
            epa_total=("epa", "sum"),
        ).reset_index()

        for _, row in rec_games.iterrows():
            pid = row["receiver_player_id"]
            if pid is None or (isinstance(pid, float) and math.isnan(pid)):
                continue
            if pid not in logs:
                logs[pid] = []
            existing = [g for g in logs[pid] if g["game_id"] == row["game_id"]]
            if existing:
                existing[0]["rec_yds"] = float(row["rec_yds"])
                existing[0]["targets"] = int(row["targets"])
                existing[0]["receptions"] = int(row["receptions"])
                existing[0]["rec_td"] = int(row["rec_td"])
            else:
                logs[pid].append({
                    "game_id": row["game_id"],
                    "week": int(row["week"]),
                    "team": row["posteam"],
                    "opp": row["defteam"],
                    "name": row["receiver_player_name"],
                    "role": "receiver",
                    "rec_yds": float(row["rec_yds"]),
                    "targets": int(row["targets"]),
                    "receptions": int(row["receptions"]),
                    "rec_td": int(row["rec_td"]),
                    "epa": float(row["epa_total"]),
                })

    return logs


# ---------------------------------------------------------------------------
# Projection engine
# ---------------------------------------------------------------------------

def _weighted_avg(values, decay=DECAY_FACTOR):
    """Compute exponentially weighted average (most recent game = highest weight)."""
    if not values:
        return 0.0
    n = len(values)
    weights = [decay ** i for i in range(n)]  # [1.0, 0.88, 0.77, ...]
    weights.reverse()  # oldest first in values list → newest gets highest weight
    return sum(v * w for v, w in zip(values, weights)) / sum(weights)


def _weighted_std(values, decay=DECAY_FACTOR):
    """Compute weighted standard deviation."""
    if len(values) < 2:
        return 20.0  # High default uncertainty
    avg = _weighted_avg(values, decay)
    n = len(values)
    weights = [decay ** i for i in range(n)]
    weights.reverse()
    w_sum = sum(weights)
    var = sum(w * (v - avg) ** 2 for v, w in zip(values, weights)) / w_sum
    return math.sqrt(max(var, 1.0))


def project_player_props(player_logs, team_stats, prop_lines=None):
    """
    Project player props for all players with sufficient game logs.

    Parameters
    ----------
    player_logs : dict
        {player_id: [game_log, ...]} from build_player_game_logs.
    team_stats : dict
        {team_abbr: stats_dict} for opponent strength adjustment.
    prop_lines : list[dict] or None
        Market prop lines: [{"player", "market", "line", "over_price", "under_price"}, ...]

    Returns
    -------
    list[dict]
        Projections with picks where applicable.
    """
    projections = []

    # Build opponent defense rankings for adjustment
    def_pass_ranks = {}
    def_rush_ranks = {}
    for team, st in team_stats.items():
        def_pass_ranks[team] = st.get("passDefEPA", 0.0)
        def_rush_ranks[team] = st.get("rushDefEPA", 0.0)
    # League averages
    avg_pass_def = np.mean(list(def_pass_ranks.values())) if def_pass_ranks else 0.0
    avg_rush_def = np.mean(list(def_rush_ranks.values())) if def_rush_ranks else 0.0

    # Index prop lines by (first_initial, last_name, market) for cross-source matching
    line_lookup = {}
    if prop_lines:
        for pl in prop_lines:
            nk = _name_key(pl.get("player", ""))
            key = (nk[0], nk[1], pl.get("market", ""))
            line_lookup[key] = pl

    for pid, games in player_logs.items():
        if not games:
            continue

        # Sort by week (most recent last)
        games = sorted(games, key=lambda g: g.get("week", 0))
        recent = games[-ROLLING_WINDOW:]
        name = games[-1].get("name", "Unknown")
        team = games[-1].get("team", "")
        latest_opp = games[-1].get("opp", "")

        # --- Qualified game sets (reused across markets) ---
        # Original filters from +155u backtest — lower filters capture the
        # under-projection bias which IS the edge (model projects low → UNDER wins)
        passer_games = [g for g in recent if g.get("pass_att", 0) >= 10]
        rusher_games = [g for g in recent if g.get("rush_att", 0) >= 5]
        receiver_games = [g for g in recent if g.get("targets", 0) >= 2]

        opp_pass_def = def_pass_ranks.get(latest_opp, avg_pass_def)
        opp_rush_def = def_rush_ranks.get(latest_opp, avg_rush_def)
        pass_opp_adj = (opp_pass_def - avg_pass_def)
        rush_opp_adj = (opp_rush_def - avg_rush_def)

        # --- Passing yards ---
        if len(passer_games) >= MIN_GAMES_PASSER:
            vals = [g["pass_yds"] for g in passer_games]
            proj = _weighted_avg(vals) + pass_opp_adj * 25
            std = _weighted_std(vals) * VAR_MULT["pass_yds"]
            prop = _make_prop(name, team, "pass_yds", proj, std, line_lookup, latest_opp)
            if prop: projections.append(prop)

        # --- Pass TDs ---
        if len(passer_games) >= MIN_GAMES_PASSER:
            vals = [g.get("pass_td", 0) for g in passer_games]
            proj = _weighted_avg(vals) + pass_opp_adj * 0.5
            std = _weighted_std(vals) * VAR_MULT["pass_tds"]
            prop = _make_prop(name, team, "pass_tds", proj, std, line_lookup, latest_opp)
            if prop: projections.append(prop)

        # --- Pass attempts --- DISABLED: 42.0% win rate, -15u over 3 seasons
        # --- Completions --- DISABLED: 47.1% win rate, -17u over 3 seasons
        # --- Interceptions --- DISABLED: corr=0.01, no predictive power + invalid Odds API market

        # --- Rushing yards ---
        if len(rusher_games) >= MIN_GAMES_RUSHER:
            vals = [g["rush_yds"] for g in rusher_games]
            proj = _weighted_avg(vals) + rush_opp_adj * 15
            std = _weighted_std(vals) * VAR_MULT["rush_yds"]
            prop = _make_prop(name, team, "rush_yds", proj, std, line_lookup, latest_opp)
            if prop: projections.append(prop)

        # --- Rush attempts ---
        if len(rusher_games) >= MIN_GAMES_RUSHER:
            vals = [g.get("rush_att", 0) for g in rusher_games]
            proj = _weighted_avg(vals)
            std = _weighted_std(vals) * VAR_MULT["rush_att"]
            prop = _make_prop(name, team, "rush_att", proj, std, line_lookup, latest_opp)
            if prop: projections.append(prop)

        # --- Receiving yards ---
        if len(receiver_games) >= MIN_GAMES_RECEIVER:
            vals = [g["rec_yds"] for g in receiver_games]
            proj = _weighted_avg(vals) + pass_opp_adj * 15
            std = _weighted_std(vals) * VAR_MULT["rec_yds"]
            prop = _make_prop(name, team, "rec_yds", proj, std, line_lookup, latest_opp)
            if prop: projections.append(prop)

        # --- Receptions ---
        if len(receiver_games) >= MIN_GAMES_RECEIVER:
            vals = [g.get("receptions", 0) for g in receiver_games]
            proj = _weighted_avg(vals)
            std = _weighted_std(vals) * VAR_MULT["receptions"]
            prop = _make_prop(name, team, "receptions", proj, std, line_lookup, latest_opp)
            if prop: projections.append(prop)

    return projections


def _make_prop(name, team, market, proj, std, line_lookup, opp):
    """Build a single prop projection + pick."""
    # Look up market line using normalized name key
    nk = _name_key(name)
    line_key = (nk[0], nk[1], market)
    line_data = line_lookup.get(line_key)

    line = None
    if line_data:
        line = line_data.get("line")

    result = {
        "player": name,
        "team": team,
        "opp": opp,
        "market": market,
        "proj": round(proj, 1),
        "std": round(std, 1),
        "line": line,
        "pick": "PASS",
        "edge": None,
        "pCover": None,
        "conf": "low",
    }

    if line is not None and std > 0:
        diff = proj - line
        z = diff / std
        # P(over) using Student's t
        p_over = float(t_dist.cdf(z, df=PROP_T_DF))
        p_under = 1.0 - p_over

        best_p = max(p_over, p_under)
        result["edge"] = round(diff, 1)
        result["pCover"] = round(best_p, 3)

        # Market-specific thresholds
        mkt_thresh = MARKET_THRESHOLDS.get(market, {"high": PROP_PROB_HIGH, "elite": PROP_PROB_ELITE})
        if best_p >= mkt_thresh["high"]:
            direction = "OVER" if p_over > p_under else "UNDER"
            # Directional filters
            if market in UNDER_ONLY_MARKETS and direction == "OVER":
                return result  # keep as PASS
            if market in OVER_ONLY_MARKETS and direction == "UNDER":
                return result  # keep as PASS
            # Edge size filter: skip tiny edges (noise) and extreme edges (model wrong)
            abs_edge = abs(diff)
            min_e = MIN_EDGE.get(market, 0)
            max_e = MAX_EDGE.get(market, 999)
            if abs_edge < min_e or abs_edge > max_e:
                return result  # keep as PASS
            # Minimum line filter: skip low-volume players where noise dominates
            min_l = MIN_LINE.get(market, 0)
            if line < min_l:
                return result  # keep as PASS
            result["pick"] = direction
            result["conf"] = "elite" if best_p >= mkt_thresh["elite"] else "high"

    return result


# ---------------------------------------------------------------------------
# Format for dashboard JSON
# ---------------------------------------------------------------------------

def format_props_for_dashboard(projections, season, week):
    """
    Format prop projections into dashboard-compatible JSON structure.

    Returns
    -------
    dict
        {"season", "week", "props": [...], "summary": str}
    """
    picks = [p for p in projections if p["pick"] != "PASS"]
    picks.sort(key=lambda p: p.get("pCover", 0) or 0, reverse=True)

    return {
        "season": season,
        "week": week,
        "generated": __import__("datetime").datetime.now().isoformat(),
        "totalProjections": len(projections),
        "totalPicks": len(picks),
        "props": picks,
        "summary": f"{len(picks)} picks from {len(projections)} projections",
    }
