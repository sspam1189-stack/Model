# scripts/sources/odds_fanduel.py
# Fetch NCAA basketball game spreads + totals from FanDuel's public sportsbook API.
# No API key needed — uses the same public endpoints as the FD website.

import os
import json
import requests
import datetime
from zoneinfo import ZoneInfo

_dir = os.path.dirname(os.path.abspath(__file__))
ODDS_CACHE_DIR = os.path.join(_dir, "..", "..", "..", "data", "odds_cache", "ncaab")

FD_BASE = "https://sbapi.mi.sportsbook.fanduel.com/api"
FD_API_KEY = "FhMFpcPWXMeyZxOx"

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
    if not spread_team:
        return spread_pts
    st = _norm_team(spread_team).lower()
    h = _norm_team(home).lower()
    a = _norm_team(away).lower()
    if st in h or h in st:
        return spread_pts
    elif st in a or a in st:
        return -spread_pts
    return spread_pts


def fetch_fanduel_ncaab_odds():
    """
    Fetch today's NCAA men's basketball spreads + totals from FanDuel.
    Filters out women's games (marked with "(W)" in FanDuel).
    Returns list matching odds_theoddsapi format.
    """
    url = f"{FD_BASE}/content-managed-page?page=CUSTOM&customPageId=ncaab&_ak={FD_API_KEY}"
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

    games = []
    for eid, ev in events.items():
        name = ev.get("name", "")
        if "@" not in name:
            continue
        # Skip women's games
        if "(W)" in name:
            continue

        parts = name.split(" @ ", 1)
        if len(parts) != 2:
            continue
        away = _norm_team(parts[0])
        home = _norm_team(parts[1])

        ev_markets = [m for m in markets.values() if str(m.get("eventId")) == str(eid)]

        line = None
        total = None

        for m in ev_markets:
            mt = m.get("marketType", "")
            if "HANDICAP" in mt and "2-WAY" in mt:
                for runner in m.get("runners", []):
                    h = runner.get("handicap")
                    if h is not None:
                        h = float(h)
                        if h < 0:
                            spread_team = runner.get("runnerName", "")
                            line = _to_model_line(home, away, h, spread_team)
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

    # Cache
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

    print(f"  [fanduel] Fetched {len(games)} NCAAB men's games with spreads/totals")
    return games
