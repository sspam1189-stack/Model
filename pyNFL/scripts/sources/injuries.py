# pyNFL/scripts/sources/injuries.py
# Fetches NFL injury data from ESPN's public API.
# NFL injuries include practice designations: OUT, Doubtful, Questionable, Probable.
#
# Exports:
#   fetch_injury_data()       -> { "report": { team -> [{ player, status, position, description }] } }
#   classify_injuries(report) -> { player_id -> status (IN/QUESTIONABLE/OUT) }
#   get_key_injuries(report, team_name) -> [{ player, status, position, description }]

import requests
import re

ESPN_NFL_INJURIES = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"


# -- NFL positional importance for injury impact --

# Positional weight tiers for injury delta calculation
POSITION_TIERS = {
    # Tier 1: Highest impact
    "QB": "elite",
    # Tier 2: High impact
    "EDGE": "high", "DE": "high", "OLB": "high",
    "WR": "high", "CB": "high",
    # Tier 3: Medium impact
    "OT": "medium", "OG": "medium", "C": "medium",
    "TE": "medium", "RB": "medium",
    "S": "medium", "FS": "medium", "SS": "medium",
    "DT": "medium", "NT": "medium",
    "ILB": "medium", "MLB": "medium", "LB": "medium",
    # Tier 4: Lower impact (still matters)
    "K": "low", "P": "low", "LS": "low",
    "FB": "low",
}


def _normalize_position(raw):
    """Normalize position string to standard abbreviation."""
    s = str(raw or "").strip().upper()
    # Handle common ESPN position formats
    s = s.replace("OUTSIDE LINEBACKER", "OLB")
    s = s.replace("INSIDE LINEBACKER", "ILB")
    s = s.replace("MIDDLE LINEBACKER", "MLB")
    s = s.replace("DEFENSIVE END", "DE")
    s = s.replace("DEFENSIVE TACKLE", "DT")
    s = s.replace("NOSE TACKLE", "NT")
    s = s.replace("CORNERBACK", "CB")
    s = s.replace("SAFETY", "S")
    s = s.replace("FREE SAFETY", "FS")
    s = s.replace("STRONG SAFETY", "SS")
    s = s.replace("WIDE RECEIVER", "WR")
    s = s.replace("TIGHT END", "TE")
    s = s.replace("RUNNING BACK", "RB")
    s = s.replace("FULLBACK", "FB")
    s = s.replace("QUARTERBACK", "QB")
    s = s.replace("OFFENSIVE TACKLE", "OT")
    s = s.replace("OFFENSIVE GUARD", "OG")
    s = s.replace("CENTER", "C")
    s = s.replace("KICKER", "K")
    s = s.replace("PUNTER", "P")
    s = s.replace("LONG SNAPPER", "LS")
    # If already abbreviated, return as-is
    return s.split("/")[0].strip()  # Handle "DE/OLB" -> "DE"


def _normalize_status(raw):
    """
    Normalize ESPN injury status to one of: out, doubtful, questionable, probable, active.
    """
    s = str(raw or "").lower().strip()
    if "out" in s or "injured reserve" in s or "ir" == s or "pup" in s or "suspended" in s:
        return "out"
    if "doubtful" in s:
        return "doubtful"
    if "questionable" in s or "day-to-day" in s or "dtd" in s:
        return "questionable"
    if "probable" in s:
        return "probable"
    return "active"


def _classify_status(normalized_status):
    """
    Classify normalized status into model categories: OUT, QUESTIONABLE, IN.
    - OUT/Doubtful -> OUT
    - Questionable -> QUESTIONABLE
    - Probable/Active/empty -> IN
    """
    if normalized_status in ("out", "doubtful"):
        return "OUT"
    if normalized_status == "questionable":
        return "QUESTIONABLE"
    return "IN"


def _get_position_tier(position):
    """Get importance tier for a position."""
    return POSITION_TIERS.get(position, "low")


def fetch_injury_data():
    """
    Fetch NFL injury data from ESPN API.

    Returns:
        { "report": { teamDisplayName -> [{ player, playerId, status, position, description, tier }] } }
    """
    print("  [injuries] Fetching NFL injury data from ESPN...")

    try:
        # ESPN's CDN 403s custom/bot User-Agents (since 2026-08-06); the
        # requests default UA is allowed, so send no custom User-Agent.
        res = requests.get(
            ESPN_NFL_INJURIES,
            headers={"Accept": "application/json"},
            timeout=30,
        )
    except Exception as e:
        print(f"  [injuries] ESPN injury fetch failed: {e}")
        return {"report": {}}

    if res.status_code != 200:
        print(f"  [injuries] ESPN injury API returned {res.status_code}")
        return {"report": {}}

    try:
        data = res.json()
    except Exception:
        print("  [injuries] Failed to parse ESPN injury JSON")
        return {"report": {}}

    report = {}

    # ESPN returns injuries grouped by team.
    # Live shape (2026): { "injuries": [ { "id", "displayName",
    #   "injuries": [...] } ] } — team fields sit directly on the entry.
    # Older shapes: { "items": [ { "team": {...}, "injuries": [...] } ] }
    # or a bare list of team entries.
    if isinstance(data, list):
        team_entries = data
    else:
        team_entries = data.get("injuries") or data.get("items") or []

    for team_entry in team_entries:
        team_info = team_entry.get("team") or {}
        team_name = (
            team_entry.get("displayName")
            or team_info.get("displayName")
            or team_info.get("name")
        )
        if not team_name:
            continue

        players = []
        injuries_list = team_entry.get("injuries") or []

        for inj in injuries_list:
            # Each injury entry may have nested athlete info
            athlete = inj.get("athlete") or {}
            player_name = athlete.get("displayName") or athlete.get("fullName")
            if not player_name:
                continue

            player_id = str(athlete.get("id", ""))
            raw_position = athlete.get("position", {})
            if isinstance(raw_position, dict):
                position = raw_position.get("abbreviation") or raw_position.get("name", "")
            else:
                position = str(raw_position or "")
            position = _normalize_position(position)

            # Status and description
            raw_status = inj.get("status") or ""
            if isinstance(raw_status, dict):
                raw_status = raw_status.get("type") or raw_status.get("name") or raw_status.get("description") or ""
            status = _normalize_status(raw_status)

            # Skip players who are active/probable (not injury-relevant)
            if status in ("active", "probable"):
                continue

            description = inj.get("shortComment") or inj.get("longComment") or inj.get("details", {}).get("detail") or ""
            if isinstance(description, dict):
                description = description.get("detail") or description.get("description") or ""

            tier = _get_position_tier(position)

            players.append({
                "player": player_name,
                "playerId": player_id,
                "status": status,
                "position": position,
                "description": str(description),
                "tier": tier,
            })

        if players:
            report[team_name] = players

    total = sum(len(v) for v in report.values())
    teams_with_injuries = len(report)
    print(f"  [injuries] {total} players injured/questionable across {teams_with_injuries} teams")

    return {"report": report}


def classify_injuries(report):
    """
    Classify all injuries into model categories.

    Args:
        report: dict from fetch_injury_data()["report"]

    Returns:
        dict mapping "teamName|playerName" -> { status: IN/QUESTIONABLE/OUT, player, team, position, description }
    """
    classified = {}

    for team_name, players in report.items():
        for p in players:
            key = f"{team_name}|{p['player']}"
            classified[key] = {
                "status": _classify_status(p["status"]),
                "player": p["player"],
                "playerId": p.get("playerId", ""),
                "team": team_name,
                "position": p["position"],
                "description": p["description"],
                "tier": p["tier"],
            }

    return classified


# -- Team name aliases for matching --

TEAM_ALIASES = {
    "LA Rams": "Los Angeles Rams",
    "Los Angeles Rams": "LA Rams",
    "LA Chargers": "Los Angeles Chargers",
    "Los Angeles Chargers": "LA Chargers",
}


def get_key_injuries(report, team_name):
    """
    Returns only Out/Doubtful players who are elite or high tier for a given team.

    Args:
        report: dict from fetch_injury_data()["report"]
        team_name: ESPN team display name

    Returns:
        List of injury dicts for key players who are OUT or Doubtful.
    """
    entries = report.get(team_name) or report.get(TEAM_ALIASES.get(team_name, "")) or []
    result = []
    for p in entries:
        if p["status"] not in ("out", "doubtful"):
            continue
        if p["tier"] not in ("elite", "high"):
            continue
        result.append(p)
    return result


def get_questionable_players(report, team_name):
    """
    Returns Questionable players for a given team (all positions).

    Args:
        report: dict from fetch_injury_data()["report"]
        team_name: ESPN team display name

    Returns:
        List of injury dicts for questionable players.
    """
    entries = report.get(team_name) or report.get(TEAM_ALIASES.get(team_name, "")) or []
    return [p for p in entries if p["status"] == "questionable"]


def game_uncertainty_score(away_injuries, home_injuries):
    """
    Returns 0 (no concern) -> 5+ (skip the game).
    Weighted by positional importance.
    """
    score = 0
    weights = {"elite": 2.0, "high": 1.0, "medium": 0.5, "low": 0.1}

    for inj in list(away_injuries) + list(home_injuries):
        tier = inj.get("tier", "low")
        w = weights.get(tier, 0.1)

        if inj.get("status") == "out":
            score += w
        elif inj.get("status") == "doubtful":
            score += w * 0.8
        elif inj.get("status") == "questionable":
            score += w * 0.3

    return min(round(score, 1), 5)
