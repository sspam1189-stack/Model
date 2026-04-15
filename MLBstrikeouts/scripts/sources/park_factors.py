"""
park_factors.py — Self-derived rolling park factors from batter game log data.

Computes HR, XBH, single, and TB park factors as ratios vs league average.
No external API calls — all data comes from batter game logs already fetched.

Early-season handling: parks with fewer than min_games home games are blended
with prior-year baselines using Bayesian shrinkage.
"""

from collections import defaultdict

# ---------------------------------------------------------------------------
# Prior-year (2025) park factor baselines — all 30 teams
# Approximate values based on historical park factor data.
# Factors are ratios vs league average (1.0 = neutral).
# ---------------------------------------------------------------------------
PRIOR_PARK_FACTORS = {
    # --- Hitter-friendly parks ---
    "COL": {"hr": 1.25, "xbh": 1.18, "single": 1.05, "tb": 1.20},  # Coors Field
    "CIN": {"hr": 1.12, "xbh": 1.08, "single": 1.01, "tb": 1.08},  # GABP
    "TEX": {"hr": 1.08, "xbh": 1.05, "single": 1.00, "tb": 1.05},  # Globe Life
    "TOR": {"hr": 1.07, "xbh": 1.04, "single": 0.99, "tb": 1.04},  # Rogers Centre
    "PHI": {"hr": 1.06, "xbh": 1.04, "single": 1.01, "tb": 1.04},  # Citizens Bank
    "CHC": {"hr": 1.05, "xbh": 1.06, "single": 1.02, "tb": 1.05},  # Wrigley
    "NYY": {"hr": 1.08, "xbh": 1.02, "single": 0.98, "tb": 1.04},  # Yankee Stadium
    "MIL": {"hr": 1.05, "xbh": 1.03, "single": 1.00, "tb": 1.03},  # Am Fam Field
    "ATL": {"hr": 1.04, "xbh": 1.03, "single": 1.00, "tb": 1.03},  # Truist Park
    "BOS": {"hr": 0.96, "xbh": 1.14, "single": 1.04, "tb": 1.03},  # Fenway — high doubles

    # --- Neutral parks ---
    "LAD": {"hr": 1.02, "xbh": 1.01, "single": 1.00, "tb": 1.01},  # Dodger Stadium
    "ARI": {"hr": 1.03, "xbh": 1.02, "single": 1.00, "tb": 1.02},  # Chase Field
    "MIN": {"hr": 1.02, "xbh": 1.01, "single": 1.00, "tb": 1.01},  # Target Field
    "BAL": {"hr": 1.02, "xbh": 1.00, "single": 1.00, "tb": 1.01},  # Camden Yards
    "DET": {"hr": 1.00, "xbh": 1.00, "single": 1.00, "tb": 1.00},  # Comerica Park
    "CLE": {"hr": 1.00, "xbh": 1.00, "single": 1.00, "tb": 1.00},  # Progressive
    "WSH": {"hr": 1.00, "xbh": 1.00, "single": 1.00, "tb": 1.00},  # Nationals Park
    "CWS": {"hr": 1.01, "xbh": 1.00, "single": 0.99, "tb": 1.00},  # Guaranteed Rate
    "HOU": {"hr": 1.00, "xbh": 0.99, "single": 1.00, "tb": 1.00},  # Minute Maid
    "LAA": {"hr": 0.99, "xbh": 0.99, "single": 1.00, "tb": 0.99},  # Angel Stadium
    "PIT": {"hr": 0.98, "xbh": 1.00, "single": 1.00, "tb": 0.99},  # PNC Park
    "STL": {"hr": 0.99, "xbh": 0.99, "single": 1.00, "tb": 0.99},  # Busch Stadium
    "SD":  {"hr": 0.95, "xbh": 0.96, "single": 0.99, "tb": 0.96},  # Petco Park

    # --- Pitcher-friendly parks ---
    "KC":  {"hr": 0.93, "xbh": 0.96, "single": 1.01, "tb": 0.96},  # Kauffman
    "NYM": {"hr": 0.92, "xbh": 0.95, "single": 0.99, "tb": 0.95},  # Citi Field
    "TB":  {"hr": 0.92, "xbh": 0.94, "single": 0.98, "tb": 0.94},  # Tropicana
    "SEA": {"hr": 0.88, "xbh": 0.92, "single": 0.99, "tb": 0.92},  # T-Mobile
    "MIA": {"hr": 0.88, "xbh": 0.92, "single": 0.98, "tb": 0.92},  # loanDepot
    "SF":  {"hr": 0.82, "xbh": 0.90, "single": 0.98, "tb": 0.88},  # Oracle Park
    "OAK": {"hr": 0.82, "xbh": 0.90, "single": 0.98, "tb": 0.88},  # Coliseum
}

# Default neutral factors for unknown parks
_NEUTRAL = {"hr": 1.0, "xbh": 1.0, "single": 1.0, "tb": 1.0}

# Bayesian shrinkage weight toward prior (higher = more prior influence)
_PRIOR_WEIGHT = 20


# ---------------------------------------------------------------------------
# Main computation
# ---------------------------------------------------------------------------

def compute_park_factors(batter_game_logs, min_games=20):
    """Compute park factors from batter game log data.

    For each ballpark (identified by the home team), aggregate batter stats
    from home games vs league average to produce HR, XBH, single, and TB
    park factors.  Parks with fewer than ``min_games`` home games are blended
    with prior-year baselines using Bayesian shrinkage.

    Parameters
    ----------
    batter_game_logs : dict
        ``{batter_id: [game_log_dicts]}`` where each game log dict has at
        least: ``team``, ``is_home``, ``hr``, ``doubles``, ``triples``,
        ``h``, ``ab``, ``pa``, ``tb``.
    min_games : int, optional
        Minimum home games at a park before using current-season data without
        shrinkage.  Default 20.

    Returns
    -------
    dict
        ``{team_abbr: {"hr": float, "xbh": float, "single": float, "tb": float}}``
        All factors are ratios vs league average (1.0 = neutral).
    """
    # --- 1. Aggregate stats by park (home games) and league totals ----------
    park_stats = defaultdict(lambda: {
        "hr": 0, "xbh": 0, "single": 0, "tb": 0, "pa": 0, "games": 0,
    })
    league_totals = {"hr": 0, "xbh": 0, "single": 0, "tb": 0, "pa": 0}

    game_ids_by_park = defaultdict(set)  # track unique games per park

    for _batter_id, logs in batter_game_logs.items():
        for g in logs:
            pa = _safe_int(g.get("pa", 0))
            if pa <= 0:
                continue

            hr = _safe_int(g.get("hr", 0))
            doubles = _safe_int(g.get("doubles", 0))
            triples = _safe_int(g.get("triples", 0))
            h = _safe_int(g.get("h", 0))
            tb = _safe_int(g.get("tb", 0))

            xbh = doubles + triples
            singles = max(h - doubles - triples - hr, 0)

            # Accumulate league totals (all games, home and away)
            league_totals["hr"] += hr
            league_totals["xbh"] += xbh
            league_totals["single"] += singles
            league_totals["tb"] += tb
            league_totals["pa"] += pa

            # Accumulate park stats (home games only)
            if g.get("is_home"):
                team = g.get("team", "")
                if not team:
                    continue
                park_stats[team]["hr"] += hr
                park_stats[team]["xbh"] += xbh
                park_stats[team]["single"] += singles
                park_stats[team]["tb"] += tb
                park_stats[team]["pa"] += pa

                # Count unique games (use game_date as proxy)
                game_key = f"{team}_{g.get('game_date', '')}"
                if game_key not in game_ids_by_park[team]:
                    game_ids_by_park[team].add(game_key)
                    park_stats[team]["games"] += 1

    # --- 2. Compute league average per-PA rates --------------------------------
    league_pa = league_totals["pa"]
    if league_pa == 0:
        # No data at all — return all priors
        return {team: dict(PRIOR_PARK_FACTORS.get(team, _NEUTRAL))
                for team in PRIOR_PARK_FACTORS}

    league_rates = {
        "hr":     league_totals["hr"]     / league_pa,
        "xbh":    league_totals["xbh"]    / league_pa,
        "single": league_totals["single"] / league_pa,
        "tb":     league_totals["tb"]     / league_pa,
    }

    # --- 3. Compute per-park factors -------------------------------------------
    factors = {}
    for team, stats in park_stats.items():
        park_pa = stats["pa"]
        n_games = stats["games"]

        if park_pa == 0:
            factors[team] = dict(PRIOR_PARK_FACTORS.get(team, _NEUTRAL))
            continue

        # Current-season park rates
        current = {}
        for component in ("hr", "xbh", "single", "tb"):
            park_rate = stats[component] / park_pa
            lg_rate = league_rates[component]
            if lg_rate > 0:
                current[component] = park_rate / lg_rate
            else:
                current[component] = 1.0

        # Bayesian shrinkage: blend with prior when sample is small
        prior = PRIOR_PARK_FACTORS.get(team, _NEUTRAL)
        if n_games < min_games:
            blended = {}
            for component in ("hr", "xbh", "single", "tb"):
                blended[component] = (
                    (n_games * current[component] + _PRIOR_WEIGHT * prior[component])
                    / (n_games + _PRIOR_WEIGHT)
                )
            factors[team] = blended
        else:
            factors[team] = current

    # --- 4. Fill in teams with no home game data yet ---------------------------
    for team in PRIOR_PARK_FACTORS:
        if team not in factors:
            factors[team] = dict(PRIOR_PARK_FACTORS[team])

    return factors


# ---------------------------------------------------------------------------
# Lookup helper
# ---------------------------------------------------------------------------

def get_park_factor(park_factors, team, component="tb"):
    """Look up a single park factor component with a safe default.

    Parameters
    ----------
    park_factors : dict
        Output of :func:`compute_park_factors`.
    team : str
        Team abbreviation (e.g. ``"COL"``).
    component : str, optional
        One of ``"hr"``, ``"xbh"``, ``"single"``, ``"tb"``.  Default ``"tb"``.

    Returns
    -------
    float
        Park factor ratio (1.0 = neutral).  Returns 1.0 if the team or
        component is not found.
    """
    if not park_factors or not team:
        return 1.0
    team_factors = park_factors.get(team, {})
    return team_factors.get(component, 1.0)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_int(val):
    """Convert a value to int, returning 0 on failure."""
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0
