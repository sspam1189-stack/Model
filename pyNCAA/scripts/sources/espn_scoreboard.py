# scripts/sources/espn_scoreboard.py
# ESPN scoreboard for NCAA men's basketball

import re
import requests
import math


def fetch_scoreboard(date_yyyymmdd=None):
    base = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
    params = f"?dates={date_yyyymmdd}&groups=50&limit=365" if date_yyyymmdd else "?groups=50&limit=365"
    url = f"{base}{params}"
    res = requests.get(url, headers={"user-agent": "ncaa-picks-bot/1.0"})
    if not res.ok:
        raise Exception(f"HTTP {res.status_code} for {url}")
    return res.json()


def _norm_for_match(s):
    """Normalize a team name for fuzzy comparison."""
    s = str(s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\bstate\b", "st", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def fetch_tournament_teams(date_yyyymmdd, stats_keys=None):
    """Fetch teams playing in the NCAA tournament (groups=100) and NIT (groups=98)
    from ESPN's scoreboard for the given date.

    Returns a dict mapping normalized name variants to the best short name.
    If stats_keys (e.g. Barttorvik team names) are provided, ESPN names are
    resolved to matching stats keys for consistency.
    """
    base = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
    teams = {}  # norm_name -> best short name

    # Build a lookup from stats_keys for resolving ESPN names -> model names
    stats_norm = {}
    if stats_keys:
        for k in stats_keys:
            stats_norm[_norm_for_match(k)] = k

    def _best_name(team_obj):
        """Resolve an ESPN team object to the best model-compatible name."""
        location = (team_obj.get("location") or "").strip()   # e.g. "Ohio State"
        display = (team_obj.get("displayName") or "").strip()  # e.g. "Ohio State Buckeyes"
        abbr = (team_obj.get("abbreviation") or "").strip()    # e.g. "OSU"

        # Try matching location against stats keys first (most reliable)
        if stats_norm:
            for candidate in [location, display]:
                nk = _norm_for_match(candidate)
                if nk in stats_norm:
                    return stats_norm[nk]
                # Try prefix match: "ohio st" matches "ohio st" in stats
                for sk, sv in stats_norm.items():
                    if sk and nk and (nk.startswith(sk) or sk.startswith(nk)):
                        if abs(len(nk) - len(sk)) <= 4:
                            return sv

        # Fallback: use location (shorter than displayName)
        return location or display

    for groups in (100, 98):
        try:
            url = f"{base}?dates={date_yyyymmdd}&groups={groups}&seasontype=3&limit=200"
            res = requests.get(url, headers={"user-agent": "ncaa-picks-bot/1.0"}, timeout=10)
            if not res.ok:
                continue
            data = res.json()
            for ev in (data.get("events") or []):
                for comp in (ev.get("competitions") or []):
                    for c in (comp.get("competitors") or []):
                        team_obj = c.get("team") or {}
                        display = team_obj.get("displayName")
                        location = team_obj.get("location")
                        if not display:
                            continue
                        best = _best_name(team_obj)
                        # Register multiple normalized variants
                        for variant in [display, location, best]:
                            if variant:
                                teams[_norm_for_match(variant)] = best
        except Exception:
            continue

    return teams


def extract_final_scores(scoreboard_json):
    out = []
    for ev in (scoreboard_json.get("events") or []):
        comp = (ev.get("competitions") or [None])[0]
        if not comp:
            continue

        status = (comp.get("status") or {}).get("type", {}).get("name")
        is_final = status == "STATUS_FINAL" or status == "STATUS_FINAL_OVERTIME"
        if not is_final:
            continue

        competitors = comp.get("competitors") or []
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        if not away or not home:
            continue

        away_name = (away.get("team") or {}).get("displayName")
        home_name = (home.get("team") or {}).get("displayName")
        try:
            away_score = float(away.get("score"))
            home_score = float(home.get("score"))
        except (TypeError, ValueError):
            continue
        if not away_name or not home_name or not math.isfinite(away_score) or not math.isfinite(home_score):
            continue

        out.append({
            "away": away_name,
            "home": home_name,
            "awayScore": away_score,
            "homeScore": home_score,
            "date": (scoreboard_json.get("day") or {}).get("date")
        })
    return out
