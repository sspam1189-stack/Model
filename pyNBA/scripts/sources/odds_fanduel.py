# scripts/sources/odds_fanduel.py
# Fetch NBA game spreads + totals from FanDuel's public sportsbook API.
# No API key needed — uses the same public endpoints as the FD website.
# Used as primary live odds source (saves Odds API credits).

import os
import re
import json
import math
import requests
import datetime
from zoneinfo import ZoneInfo

_dir = os.path.dirname(os.path.abspath(__file__))
ODDS_CACHE_DIR = os.path.join(_dir, "..", "..", "..", "data", "odds_cache", "nba")

FD_BASE = "https://sbapi.mi.sportsbook.fanduel.com/api"
FD_API_KEY = "FhMFpcPWXMeyZxOx"  # Public key embedded in FD website

FD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


def _today_chicago():
    now = datetime.datetime.now(ZoneInfo("America/Chicago"))
    return now.strftime("%Y-%m-%d")


def _norm_team(name):
    return str(name or "").strip()


def _to_model_line(home, away, spread_pts, spread_team):
    """Convert sportsbook spread to model format: negative = home favored."""
    if not spread_team:
        return spread_pts
    spread_team_norm = _norm_team(spread_team).lower()
    home_norm = _norm_team(home).lower()
    away_norm = _norm_team(away).lower()
    if spread_team_norm in home_norm or home_norm in spread_team_norm:
        return spread_pts  # Home team spread (already correct sign)
    elif spread_team_norm in away_norm or away_norm in spread_team_norm:
        return -spread_pts  # Away team spread — flip for model
    return spread_pts


def fetch_fanduel_nba_odds():
    """
    Fetch today's NBA spreads + totals from FanDuel.
    Returns list of dicts matching the format from odds_theoddsapi:
        [{"away": str, "home": str, "line": float, "total": float, "_book": "FanDuel"}]
    """
    url = f"{FD_BASE}/content-managed-page?page=CUSTOM&customPageId=nba&_ak={FD_API_KEY}"
    try:
        r = requests.get(url, headers=FD_HEADERS, timeout=15)
        if r.status_code != 200:
            print(f"  [fanduel] API returned {r.status_code}")
            return []
        data = r.json()
    except Exception as e:
        print(f"  [fanduel] Fetch error: {e}")
        return []

    attachments = data.get("attachments", {})
    events = attachments.get("events", {})
    markets = attachments.get("markets", {})

    today = _today_chicago()
    games = []
    for eid, ev in events.items():
        name = ev.get("name", "")
        if "@" not in name:
            continue

        # Filter to today's games only (Chicago time)
        open_date = ev.get("openDate", "")
        if open_date:
            try:
                dt = datetime.datetime.fromisoformat(open_date.replace("Z", "+00:00"))
                game_date = dt.astimezone(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d")
                if game_date != today:
                    continue
            except (ValueError, TypeError):
                pass

        parts = name.split(" @ ", 1)
        if len(parts) != 2:
            continue
        away = _norm_team(parts[0])
        home = _norm_team(parts[1])

        # Find spread and total markets for this event
        ev_markets = [m for m in markets.values() if str(m.get("eventId")) == str(eid)]

        line = None
        total = None

        for m in ev_markets:
            mt = m.get("marketType", "")
            # Spread: MATCH_HANDICAP_(2-WAY)
            if "HANDICAP" in mt and "2-WAY" in mt:
                for runner in m.get("runners", []):
                    h = runner.get("handicap")
                    if h is not None:
                        h = float(h)
                        if h < 0:
                            spread_team = runner.get("runnerName", "")
                            line = _to_model_line(home, away, h, spread_team)
            # Total: TOTAL_POINTS_(OVER/UNDER)
            if "TOTAL_POINTS" in mt:
                for runner in m.get("runners", []):
                    if runner.get("runnerName") == "Over":
                        h = runner.get("handicap")
                        if h is not None:
                            total = float(h)

        games.append({
            "away": away,
            "home": home,
            "line": line,
            "total": total,
            "_book": "FanDuel",
        })

    # Cache today's odds
    if games:
        try:
            date_key = _today_chicago().replace("-", "")
            os.makedirs(ODDS_CACHE_DIR, exist_ok=True)
            cache_path = os.path.join(ODDS_CACHE_DIR, date_key + ".json")
            existing = {}
            if os.path.exists(cache_path):
                with open(cache_path, "r") as f:
                    existing = json.load(f)
            for g in games:
                if g["line"] is not None or g["total"] is not None:
                    key = f"{g['away']}@{g['home']}"
                    existing[key] = {"line": g["line"], "total": g["total"], "_book": "FanDuel", "_note": "fanduel live"}
            with open(cache_path, "w") as f:
                json.dump(existing, f, indent=2)
            print(f"  [fanduel] Cached {len(games)} games to {date_key}.json")
        except Exception:
            pass

    print(f"  [fanduel] Fetched {len(games)} NBA games with spreads/totals")
    return games
