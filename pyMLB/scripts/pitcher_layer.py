# pyMLB/scripts/pitcher_layer.py
# Handles starter swap (scratches) and bullpen fatigue computation.
# Called by model_engine.analyze_game() to apply pitcher adjustments.

import os
import sys

_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _dir)
import defaults


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_pitcher_adjustments(game, pitcher_data, injury_data):
    """
    Compute pitcher adjustments for a game.

    Parameters
    ----------
    game : dict
        Keys: "home", "away", "commenceTimeIso"
    pitcher_data : dict
        {"starters": {team: {name: {...}}}, "bullpens": {team: {...}}}
    injury_data : dict
        {"report": {team: [{name, position, status, detail}]},
         "probable_pitchers": {game_key: {"home": name, "away": name}}}

    Returns
    -------
    dict with "home" and "away" keys, each containing "starter" and "bullpen"
    sub-dicts.
    """
    home_team = game.get("home", "")
    away_team = game.get("away", "")

    probable_pitchers = injury_data.get("probable_pitchers", {})
    probable = _find_probable_pitchers(home_team, away_team, probable_pitchers)

    result = {}
    for side, team in (("home", home_team), ("away", away_team)):
        probable_name = probable.get(side) if probable else None
        team_starters = pitcher_data.get("starters", {}).get(team, {})
        team_injury_list = injury_data.get("report", {}).get(team, [])
        team_bullpen = pitcher_data.get("bullpens", {}).get(team, {})

        starter = _build_starter(probable_name, team_starters, team_injury_list)
        bullpen = _build_bullpen(team_bullpen)

        result[side] = {"starter": starter, "bullpen": bullpen}

    return result


def get_starter_ip_projection(starter_stats):
    """
    Return expected innings for this starter's start.

    Parameters
    ----------
    starter_stats : dict or None

    Returns
    -------
    float : projected IP (fallback 5.5)
    """
    if not starter_stats:
        return 5.5
    ip = starter_stats.get("IP", 0.0)
    gs = starter_stats.get("GS", 0)
    if gs <= 0:
        return 5.5
    return ip / gs


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _find_probable_pitchers(home_team, away_team, probable_pitchers):
    """
    Search probable_pitchers dict for the entry matching this game.

    Tries game keys that might be:
      - An ESPN game ID (no useful structure for matching)
      - "home@away" or "away@home" string
    Falls back to iterating all entries and checking the team names.

    Returns {"home": name, "away": name} or None.
    """
    if not probable_pitchers:
        return None

    # Try "away@home" style key
    key_candidates = [
        f"{away_team}@{home_team}",
        f"{home_team}@{away_team}",
        f"{away_team.lower()}@{home_team.lower()}",
        f"{home_team.lower()}@{away_team.lower()}",
    ]
    for k in key_candidates:
        if k in probable_pitchers:
            return probable_pitchers[k]

    # Try iterating — check if value dict has "home"/"away" keys that match
    home_lower = home_team.lower()
    away_lower = away_team.lower()
    for key, entry in probable_pitchers.items():
        if not isinstance(entry, dict):
            continue
        key_lower = key.lower()
        # Key might embed team abbreviation or partial name
        if (home_lower in key_lower or away_lower in key_lower or
                key_lower in home_lower or key_lower in away_lower):
            return entry

    # Last resort: if there's only one entry, use it
    if len(probable_pitchers) == 1:
        return next(iter(probable_pitchers.values()))

    return None


def _build_starter(probable_name, team_starters, team_injury_list):
    """
    Resolve probable starter, check for scratch, find replacement if needed.

    Returns a starter stats dict (with "name", "FIP", etc.) or default stats.
    """
    # 1. Resolve probable starter from starters dict
    pitcher_name, stats = _resolve_starter(probable_name, team_starters)

    # 2. Check if scratched
    if pitcher_name and _is_scratched(pitcher_name, team_injury_list):
        pitcher_name, stats = None, None

    # 3. If we have a valid starter, build and return
    if pitcher_name and stats:
        return _format_starter(pitcher_name, stats)

    # 4. If probable name known but couldn't resolve in starters dict,
    #    check whether injury list has them (might just be unlisted in stats)
    if probable_name and not pitcher_name:
        if not _is_scratched(probable_name, team_injury_list):
            # Probable pitcher is healthy but we have no stats — use defaults
            d = _default_starter_stats()
            d["name"] = probable_name
            return d

    # 5. Try to find the best available starter from team_starters
    best = _find_best_available(team_starters, team_injury_list)
    if best:
        name, stats = best
        return _format_starter(name, stats)

    # 6. Full fallback
    return _default_starter_stats()


def _format_starter(name, stats):
    """Convert raw pitcher stats dict to the standard starter output format."""
    ip = stats.get("IP", 0.0)
    gs = stats.get("GS", 0)
    ip_per_gs = ip / max(gs, 1) if gs > 0 else 5.5

    return {
        "name": name,
        "FIP": stats.get("FIP", 4.50),
        "xFIP": stats.get("xFIP", 4.50),
        "SIERA": stats.get("SIERA", 4.50),
        "K_BB_pct": stats.get("K_BB_pct", 0.08),
        "IP_per_GS": ip_per_gs,
        "hand": stats.get("hand", "R"),
    }


def _find_best_available(team_starters, team_injury_list):
    """
    Find the best (lowest FIP) healthy starter from team_starters.

    Returns (name, stats) or None.
    """
    best_name = None
    best_stats = None
    best_fip = float("inf")

    for name, stats in team_starters.items():
        if _is_scratched(name, team_injury_list):
            continue
        fip = stats.get("FIP", 4.50)
        if fip < best_fip:
            best_fip = fip
            best_name = name
            best_stats = stats

    if best_name:
        return (best_name, best_stats)
    return None


def _build_bullpen(team_bullpen):
    """Build bullpen stats dict from raw bullpen data."""
    if not team_bullpen:
        return _default_bullpen_stats()

    return {
        "FIP": team_bullpen.get("FIP", 4.50),
        "K_BB_pct": team_bullpen.get("K_BB_pct", 0.08),
        "workload": team_bullpen.get("IP_total", 0.0),
    }


def _resolve_starter(probable_name, team_starters):
    """
    Fuzzy match probable_name against keys in team_starters.

    Returns (pitcher_name, stats_dict) or (None, None).
    """
    if not probable_name or not team_starters:
        return (None, None)

    # 1. Exact match
    if probable_name in team_starters:
        return (probable_name, team_starters[probable_name])

    # 2. Case-insensitive match
    probable_lower = probable_name.lower()
    for name, stats in team_starters.items():
        if name.lower() == probable_lower:
            return (name, stats)

    # 3. Last-name match
    probable_last = probable_name.strip().split()[-1].lower()
    for name, stats in team_starters.items():
        parts = name.strip().split()
        if parts and parts[-1].lower() == probable_last:
            return (name, stats)

    return (None, None)


def _is_scratched(pitcher_name, team_injury_list):
    """
    Return True if pitcher_name appears in team_injury_list with a serious status.

    Statuses that mean scratched:
        "Out", "IL10", "IL15", "IL60", "Injured Reserve", "Suspension"
    """
    if not pitcher_name or not team_injury_list:
        return False

    SCRATCHED_STATUSES = {"out", "il10", "il15", "il60", "injured reserve", "suspension"}
    name_lower = pitcher_name.strip().lower()
    # Also check last-name only for robustness
    name_last = name_lower.split()[-1] if name_lower else ""

    for entry in team_injury_list:
        entry_name = entry.get("name", "").strip().lower()
        status = entry.get("status", "").strip().lower()

        if status not in SCRATCHED_STATUSES:
            continue

        if entry_name == name_lower:
            return True
        # Last-name fallback
        entry_last = entry_name.split()[-1] if entry_name else ""
        if name_last and entry_last == name_last:
            return True

    return False


def _default_starter_stats():
    """Return league-average default starter stats."""
    return {
        "name": "TBD",
        "FIP": 4.50,
        "xFIP": 4.50,
        "SIERA": 4.50,
        "K_BB_pct": 0.08,
        "IP_per_GS": 5.5,
        "hand": "R",
    }


def _default_bullpen_stats():
    """Return league-average default bullpen stats."""
    return {
        "FIP": 4.50,
        "K_BB_pct": 0.08,
        "workload": 0.0,
    }
