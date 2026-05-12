# MLBstrikeouts/scripts/sources/game_context.py
# Game context features for MLB pitcher prop projections:
#   1. Rest detection (rotation schedule: normal=5d, short<=4d, extra=6-9d, extended=10+d)
#   2. Home/away splits from pitcher game logs
#   3. Innings projection (per-inning rates x projected innings)
#   4. Pitch count analysis
#   5. Injury report loading from shared cache
#   6. IP string conversion utilities
#
# These are computed per-pitcher-per-game and passed to the props engine
# as adjustment factors.

import os
import json
import datetime

_dir = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# 1. Rest detection
# ---------------------------------------------------------------------------

# MLB pitchers typically start every 5 days.  Adjustments are keyed to
# how far off normal rotation the pitcher is.
REST_ADJUSTMENTS = {
    "short_rest": {       # 4 days or fewer between starts
        "k":    -0.3,
        "outs": -1.5,     # shorter outing expected
        "h":    +0.3,
        "bb":   +0.2,
    },
    "extra_rest": {       # 6-9 days
        "k":    +0.2,
        "outs": +0.5,
        "h":    -0.2,
        "bb":   -0.1,
    },
    "extended_rest": {    # 10+ days (IL return, etc.)
        "k":    -0.5,     # rust factor
        "outs": -2.0,
        "h":    +0.5,
        "bb":   +0.5,
    },
}


def classify_rest(rest_days):
    """
    Classify rest into a bucket and return the matching adjustment dict.

    Parameters
    ----------
    rest_days : int
        Days since last start (5 = normal rotation).

    Returns
    -------
    tuple[str, dict]
        (label, adjustments).  label is one of
        "short_rest", "normal", "extra_rest", "extended_rest".
        adjustments is a dict of stat -> float deltas (empty for normal).
    """
    if rest_days <= 4:
        return "short_rest", REST_ADJUSTMENTS["short_rest"]
    if rest_days <= 5:
        return "normal", {}
    if rest_days <= 9:
        return "extra_rest", REST_ADJUSTMENTS["extra_rest"]
    return "extended_rest", REST_ADJUSTMENTS["extended_rest"]


def detect_rest_days(pitcher_games, game_date):
    """
    Compute days since pitcher's last start.

    Walks backwards through *pitcher_games* (assumed sorted chronologically)
    looking for the most recent game before *game_date*.

    Parameters
    ----------
    pitcher_games : list[dict]
        Pitcher's game logs.  Each entry must contain ``"game_date"``
        (``YYYY-MM-DD`` string).
    game_date : str
        Today's date (``YYYY-MM-DD`` or ``YYYYMMDD`` format).

    Returns
    -------
    int
        Days since last start.  5 = normal rotation.
        Returns 99 if no prior starts found.
    """
    try:
        today = datetime.datetime.strptime(game_date[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        try:
            today = datetime.datetime.strptime(game_date[:8], "%Y%m%d")
        except (ValueError, TypeError):
            return 99

    for g in reversed(pitcher_games):
        gd = g.get("game_date", "")[:10]
        if not gd:
            continue
        try:
            game_dt = datetime.datetime.strptime(gd, "%Y-%m-%d")
            if game_dt < today:
                return (today - game_dt).days
        except (ValueError, TypeError):
            continue

    return 99


# ---------------------------------------------------------------------------
# 2. Home / away splits
# ---------------------------------------------------------------------------

def compute_home_away_split(pitcher_games, stat_key, min_games=3):
    """
    Compute pitcher's home vs away performance split.

    Parameters
    ----------
    pitcher_games : list[dict]
        Pitcher's game logs.  Each entry should contain the requested
        *stat_key* and an ``"is_home"`` boolean.
    stat_key : str
        Stat to compute split for (e.g. ``"k"``, ``"ip"``, ``"h"``).
    min_games : int
        Minimum home **or** away games needed to produce a split.

    Returns
    -------
    dict
        ``{"home_avg": float, "away_avg": float,
          "home_split_adj": float, "away_split_adj": float}``
        Adjustments are capped at +/-15 % of the overall average.
    """
    home_vals = []
    away_vals = []

    for g in pitcher_games:
        val = g.get(stat_key, 0)
        # Skip very short outings (< 3 IP) so they don't skew splits
        ip = g.get("ip", 0)
        if isinstance(ip, str):
            ip = ip_to_float(ip)
        if ip < 3.0:
            continue
        if g.get("is_home"):
            home_vals.append(val)
        else:
            away_vals.append(val)

    result = {
        "home_avg": 0.0,
        "away_avg": 0.0,
        "home_split_adj": 0.0,
        "away_split_adj": 0.0,
    }

    if len(home_vals) < min_games or len(away_vals) < min_games:
        return result

    home_avg = sum(home_vals) / len(home_vals)
    away_avg = sum(away_vals) / len(away_vals)
    overall_avg = (sum(home_vals) + sum(away_vals)) / (len(home_vals) + len(away_vals))

    result["home_avg"] = round(home_avg, 2)
    result["away_avg"] = round(away_avg, 2)

    # Cap adjustments at +/-15% of overall average
    max_adj = overall_avg * 0.15 if overall_avg > 0 else 0.0
    home_adj = home_avg - overall_avg
    away_adj = away_avg - overall_avg

    result["home_split_adj"] = round(max(-max_adj, min(max_adj, home_adj)), 2)
    result["away_split_adj"] = round(max(-max_adj, min(max_adj, away_adj)), 2)

    return result


# ---------------------------------------------------------------------------
# 3. Innings projection
# ---------------------------------------------------------------------------

def project_outs(pitcher_games, adv_stats=None, rest_days=5,
                 pitcher_bb_per_9=None, opp_ops=None, league_avg_ops=None):
    """
    Project total outs the pitcher will record tonight.

    Outs is the atomic workload unit (each PA either is or isn't an out),
    which avoids the IP-notation ambiguity entirely. All BF / pitch-count
    math downstream is cleaner in outs.

    Uses:
    - Recent average outs (from qualified starts with >= 9 outs)
    - Season average outs from advanced stats (blended 70/30)
    - Rest day adjustment
    - Pitch count trend (high recent counts -> fewer outs)
    - Pitcher BB/9 (r=-0.129 with IP: wild pitchers get shorter leashes)
    - Opponent OPS (r=-0.103 with IP: tough lineups shorten outings)

    Returns
    -------
    float
        Expected outs (e.g. 17.1). Round to int at the display boundary.
    """
    qualified_outs = []
    for g in pitcher_games:
        ip = g.get("ip", 0)
        if isinstance(ip, str):
            ip = ip_to_float(ip)
        outs = ip * 3.0
        if outs >= 9.0:
            qualified_outs.append(outs)

    if not qualified_outs:
        return 0.0

    recent_outs = sum(qualified_outs) / len(qualified_outs)

    if adv_stats and adv_stats.get("avg_ip", 0) > 0:
        season_outs = adv_stats["avg_ip"] * 3.0
        proj_outs = 0.7 * recent_outs + 0.3 * season_outs
    else:
        proj_outs = recent_outs

    # Rest adjustment (outs = IP * 3)
    if rest_days <= 4:
        proj_outs -= 1.5
    elif rest_days >= 10:
        proj_outs -= 2.1
    elif rest_days >= 6:
        proj_outs += 0.6

    recent_pc = _avg_pitch_count(pitcher_games, window=5)
    if recent_pc > 100:
        proj_outs -= 0.9 * ((recent_pc - 100) / 10)

    LEAGUE_AVG_BB9 = 3.3
    if pitcher_bb_per_9 is not None and pitcher_bb_per_9 > 0:
        bb9_diff = pitcher_bb_per_9 - LEAGUE_AVG_BB9
        proj_outs -= bb9_diff * 0.45

    if opp_ops is not None and opp_ops > 0:
        avg_ops = league_avg_ops or 0.710
        if avg_ops > 0:
            ops_diff = (opp_ops - avg_ops) / avg_ops
            proj_outs -= ops_diff * proj_outs * 0.10

    return max(0.0, round(proj_outs, 1))


# ---------------------------------------------------------------------------
# 4. Per-inning rate calculation
# ---------------------------------------------------------------------------

def compute_per_inning_rates(pitcher_games, min_ip=3.0):
    """
    Compute per-inning production rates from recent game logs.

    Only includes games where the pitcher recorded at least *min_ip*
    innings to avoid relievers / early hooks skewing rates.

    Parameters
    ----------
    pitcher_games : list[dict]
        Pitcher's recent game logs.
    min_ip : float
        Minimum innings pitched to include a game.

    Returns
    -------
    dict
        ``{"k_per_ip": float, "h_per_ip": float, "bb_per_ip": float,
          "avg_ip": float}``
    """
    total_k = 0
    total_h = 0
    total_bb = 0
    total_ip = 0.0
    count = 0

    for g in pitcher_games:
        ip = g.get("ip", 0)
        if isinstance(ip, str):
            ip = ip_to_float(ip)
        if ip < min_ip:
            continue

        total_k += g.get("k", g.get("so", 0))
        total_h += g.get("h", 0)
        total_bb += g.get("bb", 0)
        total_ip += ip
        count += 1

    if total_ip == 0 or count == 0:
        return {"k_per_ip": 0.0, "h_per_ip": 0.0, "bb_per_ip": 0.0, "avg_ip": 0.0}

    return {
        "k_per_ip":  round(total_k / total_ip, 3),
        "h_per_ip":  round(total_h / total_ip, 3),
        "bb_per_ip": round(total_bb / total_ip, 3),
        "avg_ip":    round(total_ip / count, 2),
    }


# ---------------------------------------------------------------------------
# 5. Rate-based projection
# ---------------------------------------------------------------------------

def rate_based_projection(per_ip_rate, projected_innings):
    """
    Compute stat projection from rate x projected innings.

    Parameters
    ----------
    per_ip_rate : float
        Stat per inning (e.g. 1.2 K/IP).
    projected_innings : float
        Expected innings tonight (e.g. ``project_outs(...) / 3``).

    Returns
    -------
    float
        Projected raw stat value.
    """
    return per_ip_rate * projected_innings


# ---------------------------------------------------------------------------
# 6. Pitch count analysis
# ---------------------------------------------------------------------------

def _avg_pitch_count(pitcher_games, window=5):
    """
    Internal helper: average pitch count over last *window* starts.

    Returns 0 if no pitch count data available.
    """
    counts = []
    for g in pitcher_games[-window:]:
        pc = g.get("pitch_count", g.get("pitches", 0))
        if pc and pc > 0:
            counts.append(pc)
    return sum(counts) / len(counts) if counts else 0


def compute_pitch_count_trend(pitcher_games, window=5):
    """
    Analyze recent pitch count trends.

    High pitch counts in recent starts -> manager may pull pitcher earlier.
    Low pitch counts -> pitcher may be stretched further or is getting
    knocked out early.

    Parameters
    ----------
    pitcher_games : list[dict]
        Pitcher's game logs.  Expects ``"pitch_count"`` or ``"pitches"``
        key.
    window : int
        Number of recent starts to analyze.

    Returns
    -------
    dict
        ``{"avg_pitch_count": float,
          "trend": str,            # "increasing", "decreasing", or "stable"
          "recent_counts": list}``
    """
    counts = []
    for g in pitcher_games[-window:]:
        pc = g.get("pitch_count", g.get("pitches", 0))
        if pc and pc > 0:
            counts.append(pc)

    if not counts:
        return {"avg_pitch_count": 0, "trend": "unknown", "recent_counts": []}

    avg_pc = sum(counts) / len(counts)

    # Trend: compare first half to second half
    trend = "stable"
    if len(counts) >= 4:
        mid = len(counts) // 2
        first_half = sum(counts[:mid]) / mid
        second_half = sum(counts[mid:]) / (len(counts) - mid)
        diff = second_half - first_half
        if diff > 5:
            trend = "increasing"
        elif diff < -5:
            trend = "decreasing"

    return {
        "avg_pitch_count": round(avg_pc, 1),
        "trend": trend,
        "recent_counts": counts,
    }


# ---------------------------------------------------------------------------
# 7. Injury report loading
# ---------------------------------------------------------------------------

_INJURY_CACHE_DIR = os.path.join(_dir, "..", "..", "..", "data", "injury_cache", "mlb")

MLB_NAME_TO_ABBREV = {
    "Arizona Diamondbacks": "ARI", "Atlanta Braves": "ATL",
    "Baltimore Orioles": "BAL", "Boston Red Sox": "BOS",
    "Chicago Cubs": "CHC", "Chicago White Sox": "CHW",
    "Cincinnati Reds": "CIN", "Cleveland Guardians": "CLE",
    "Colorado Rockies": "COL", "Detroit Tigers": "DET",
    "Houston Astros": "HOU", "Kansas City Royals": "KC",
    "Los Angeles Angels": "LAA", "Los Angeles Dodgers": "LAD",
    "Miami Marlins": "MIA", "Milwaukee Brewers": "MIL",
    "Minnesota Twins": "MIN", "New York Mets": "NYM",
    "New York Yankees": "NYY", "Oakland Athletics": "OAK",
    "Philadelphia Phillies": "PHI", "Pittsburgh Pirates": "PIT",
    "San Diego Padres": "SD", "San Francisco Giants": "SF",
    "Seattle Mariners": "SEA", "St. Louis Cardinals": "STL",
    "Tampa Bay Rays": "TB", "Texas Rangers": "TEX",
    "Toronto Blue Jays": "TOR", "Washington Nationals": "WSH",
}


def load_injury_report(date_key):
    """
    Load MLB injury report from shared cache.

    Parameters
    ----------
    date_key : str
        Date string used as filename (e.g. ``"20260413"`` or
        ``"2026-04-13"``).

    Returns
    -------
    dict
        ``{team_abbrev: [{"player": str, "status": str, ...}, ...]}``
        Empty dict if no report found.
    """
    clean_key = date_key.replace("-", "")
    path = os.path.join(_INJURY_CACHE_DIR, f"{clean_key}.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except Exception:
        return {}

    report = data.get("report", {})
    result = {}
    for full_name, players in report.items():
        abbrev = MLB_NAME_TO_ABBREV.get(full_name)
        if abbrev:
            result[abbrev] = players
    return result


def _injury_name_key(name):
    """
    Normalize name for matching: ``(first, last)`` lowercased,
    stripped of suffixes like Jr., Sr., II, III.
    """
    import unicodedata
    name = name.strip()
    if not name:
        return ("", "")
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    parts = name.split()
    suffixes = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}
    while len(parts) > 2 and parts[-1].lower().rstrip(".") in suffixes:
        parts.pop()
    if len(parts) >= 2:
        return (parts[0].lower(), parts[-1].lower().rstrip("."))
    return (name[0].lower(), name.lower())


def is_pitcher_injured(pitcher_name, team_abbrev, injury_report):
    """
    Check if a pitcher is listed as OUT (IL, 60-day IL) in the injury report.

    Parameters
    ----------
    pitcher_name : str
        Pitcher's full name.
    team_abbrev : str
        Team abbreviation (e.g. ``"NYY"``).
    injury_report : dict
        From :func:`load_injury_report`.

    Returns
    -------
    bool
        True if the pitcher is listed as out/IL.
    """
    team_injuries = injury_report.get(team_abbrev, [])
    if not team_injuries:
        return False
    pkey = _injury_name_key(pitcher_name)
    for inj in team_injuries:
        status = (inj.get("status") or "").lower()
        if status not in ("out", "il", "60-day il", "injured list"):
            continue
        ikey = _injury_name_key(inj.get("player", ""))
        if pkey == ikey:
            return True
    return False


# ---------------------------------------------------------------------------
# 8. IP string conversion utilities
# ---------------------------------------------------------------------------

def ip_to_float(ip_str):
    """
    Convert MLB IP string to float innings.

    In baseball, ``"6.2"`` means 6 and 2/3 innings (20 outs), not 6.2.
    The fractional part represents *thirds* of an inning (0, 1, or 2).

    Parameters
    ----------
    ip_str : str or float
        Innings pitched in baseball notation (e.g. ``"6.2"``).

    Returns
    -------
    float
        True innings value (e.g. ``6.2`` -> ``6.667``).

    Examples
    --------
    >>> ip_to_float("6.0")
    6.0
    >>> ip_to_float("6.1")
    6.333
    >>> ip_to_float("6.2")
    6.667
    """
    ip = float(str(ip_str))
    full = int(ip)
    partial = round((ip - full) * 10)
    return round(full + partial / 3.0, 3)


def ip_to_outs(ip_str):
    """
    Convert MLB IP string to total outs.

    Parameters
    ----------
    ip_str : str or float
        Innings pitched in baseball notation (e.g. ``"6.2"``).

    Returns
    -------
    int
        Total outs recorded (e.g. ``"6.2"`` -> ``20``).

    Examples
    --------
    >>> ip_to_outs("6.0")
    18
    >>> ip_to_outs("6.2")
    20
    >>> ip_to_outs("7.1")
    22
    """
    ip = float(str(ip_str))
    full = int(ip)
    partial = round((ip - full) * 10)
    return full * 3 + partial


def outs_to_ip(outs):
    """
    Convert total outs back to MLB IP notation float.

    Parameters
    ----------
    outs : int
        Total outs.

    Returns
    -------
    float
        IP in baseball notation (e.g. ``20`` -> ``6.2``).
    """
    full = outs // 3
    remainder = outs % 3
    return full + remainder / 10.0


# ---------------------------------------------------------------------------
# 9. Aggregate context for a single pitcher start
# ---------------------------------------------------------------------------

def build_game_context(pitcher_games, game_date, adv_stats=None, is_home=None):
    """
    Build a complete game context dict for one pitcher's upcoming start.

    This is the main entry point called by the props engine.  It
    computes rest, innings projection, per-IP rates, pitch count trend,
    and home/away split adjustments.

    Parameters
    ----------
    pitcher_games : list[dict]
        Pitcher's game logs (sorted chronologically, most recent last).
    game_date : str
        Date of the upcoming game.
    adv_stats : dict or None
        Season-level advanced stats for the pitcher.
    is_home : bool or None
        Whether the pitcher is at home tonight.

    Returns
    -------
    dict
        Full context dictionary with keys:
        ``rest_days``, ``rest_label``, ``rest_adj``,
        ``proj_outs``, ``per_ip_rates``, ``pitch_count_trend``,
        ``home_away_k_split``.
    """
    rest_days = detect_rest_days(pitcher_games, game_date)
    rest_label, rest_adj = classify_rest(rest_days)

    proj_outs_ = project_outs(pitcher_games, adv_stats=adv_stats, rest_days=rest_days)
    per_ip = compute_per_inning_rates(pitcher_games)
    pc_trend = compute_pitch_count_trend(pitcher_games)

    # Home/away K split
    k_split = compute_home_away_split(pitcher_games, "k")

    ctx = {
        "rest_days":         rest_days,
        "rest_label":        rest_label,
        "rest_adj":          rest_adj,
        "proj_outs":         proj_outs_,
        "per_ip_rates":      per_ip,
        "pitch_count_trend": pc_trend,
        "home_away_k_split": k_split,
    }

    # Apply home/away split to K projection if venue is known
    if is_home is not None:
        adj_key = "home_split_adj" if is_home else "away_split_adj"
        ctx["k_venue_adj"] = k_split.get(adj_key, 0.0)
    else:
        ctx["k_venue_adj"] = 0.0

    return ctx
