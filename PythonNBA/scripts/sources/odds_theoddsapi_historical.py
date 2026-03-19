# scripts/sources/odds_theoddsapi_historical.py
# Historical odds (closing-ish snapshot) from The Odds API v4.
# Requires env var: ODDS_API_KEY

import os
import re
import math
import time
import requests
from urllib.parse import quote
from datetime import datetime, timedelta

BASE = "https://api.the-odds-api.com/v4"


def _norm_team(name):
    return re.sub(r"\s+", " ", str(name or "")).strip()


def _norm_key(s):
    s = str(s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _to_historical_iso(iso_like, offset_minutes=0):
    """Convert ISO string to historical API format, with optional minute offset."""
    try:
        # Parse ISO datetime
        dt = datetime.fromisoformat(iso_like.replace("Z", "+00:00"))
        dt = dt + timedelta(minutes=offset_minutes)
        # Format as ISO without milliseconds
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, AttributeError):
        return None


def _to_model_line(home_team, away_team, spread_points, team_for_spread):
    """
    Convert The Odds API spread into model convention:
    +X means HOME favored by X, -X means AWAY favored by X
    """
    if not isinstance(spread_points, (int, float)) or not math.isfinite(spread_points):
        return None

    abs_val = abs(spread_points)
    is_home = team_for_spread == home_team
    is_away = team_for_spread == away_team

    if not is_home and not is_away:
        return None

    if is_home:
        return abs_val if spread_points < 0 else -abs_val
    return -abs_val if spread_points < 0 else abs_val


def _pick_bookmaker(bookmakers):
    if not isinstance(bookmakers, list) or len(bookmakers) == 0:
        return None

    preferred = ["DraftKings", "FanDuel", "BetMGM", "Caesars", "PointsBet", "BetRivers"]
    for title in preferred:
        found = next((b for b in bookmakers if b and b.get("title") == title), None)
        if found:
            return found
    return bookmakers[0]


def _find_market(bookmaker, key):
    if not bookmaker or not bookmaker.get("markets"):
        return None
    return next((m for m in bookmaker["markets"] if m and m.get("key") == key), None)


def _fetch_with_retry(url, tries=5):
    wait = 0.7
    for i in range(tries):
        res = requests.get(url, timeout=30)
        if res.status_code != 429:
            return res
        time.sleep(wait)
        wait = min(wait * 2, 8.0)
    return requests.get(url, timeout=30)


# Maps Hollinger short names -> possible odds API full names
TEAM_ALIASES = {
    "la lakers": ["los angeles lakers", "la lakers"],
    "la clippers": ["los angeles clippers", "la clippers"],
    "golden state": ["golden state warriors", "golden state"],
    "oklahoma city": ["oklahoma city thunder", "oklahoma city"],
    "new orleans": ["new orleans pelicans", "new orleans"],
    "new york": ["new york knicks", "new york"],
    "san antonio": ["san antonio spurs", "san antonio"],
    "portland": ["portland trail blazers", "portland"],
    "philadelphia": ["philadelphia 76ers", "philadelphia"],
}


def _expand_aliases(name):
    k = _norm_key(name)
    return TEAM_ALIASES.get(k, [k])


def _extract_odds(data, home, away):
    home_aliases = _expand_aliases(home)
    away_aliases = _expand_aliases(away)

    # Exact match against any alias
    ev = next(
        (e for e in data
         if _norm_key(e.get("home_team")) in home_aliases
         and _norm_key(e.get("away_team")) in away_aliases),
        None,
    )

    # Partial includes fallback
    if not ev:
        ev = next(
            (e for e in data
             if any(ha in _norm_key(e.get("home_team")) or _norm_key(e.get("home_team")) in ha for ha in home_aliases)
             and any(aa in _norm_key(e.get("away_team")) or _norm_key(e.get("away_team")) in aa for aa in away_aliases)),
            None,
        )

    if not ev:
        return None

    home_team = _norm_team(ev.get("home_team"))
    away_team = _norm_team(ev.get("away_team"))
    book = _pick_bookmaker(ev.get("bookmakers"))

    line = None
    total = None

    spreads = _find_market(book, "spreads") if book else None
    totals = _find_market(book, "totals") if book else None

    if spreads and spreads.get("outcomes"):
        out = next((o for o in spreads["outcomes"] if isinstance(o.get("point"), (int, float)) and math.isfinite(float(o["point"]))), None)
        if out:
            line = _to_model_line(home_team, away_team, float(out["point"]), _norm_team(out.get("name")))

    if totals and totals.get("outcomes"):
        out = next((o for o in totals["outcomes"] if isinstance(o.get("point"), (int, float)) and math.isfinite(float(o["point"]))), None)
        if out:
            total = float(out["point"])

    return {"line": line, "total": total, "_book": book.get("title") if book else None}


def _fetch_snapshot(api_key, ts):
    url = (
        f"{BASE}/historical/sports/basketball_nba/odds?"
        f"apiKey={quote(api_key)}"
        f"&regions=us"
        f"&markets=spreads,totals"
        f"&oddsFormat=american"
        f"&date={quote(ts)}"
    )

    res = _fetch_with_retry(url)
    if res.status_code != 200:
        txt = res.text
        raise Exception(f"Historical odds fetch failed: {res.status_code} {res.reason} {txt}")

    json_data = res.json()
    return json_data.get("data", []) if isinstance(json_data.get("data"), list) else []


def fetch_closing_odds_for_game(home, away, commence_time_iso):
    """
    Fetch historical odds snapshot near tipoff and extract spread/total.
    Pass commence_time_iso from ESPN (UTC ISO string).
    """
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        raise Exception("Missing ODDS_API_KEY env var.")

    # Try snapshots at -90min, -30min, and -10min before tipoff.
    offsets = [-90, -30, -10]

    for offset in offsets:
        ts = _to_historical_iso(commence_time_iso, offset)
        if not ts:
            return {"line": None, "total": None, "_book": None, "_note": "Bad commenceTimeIso"}

        data = _fetch_snapshot(api_key, ts)
        result = _extract_odds(data, home, away)

        if result and (isinstance(result.get("line"), (int, float)) or isinstance(result.get("total"), (int, float))):
            return {**result, "_note": f"snapshot at {offset}min before tipoff"}

    return {"line": None, "total": None, "_book": None, "_note": "No odds found in pre-tipoff snapshots"}
