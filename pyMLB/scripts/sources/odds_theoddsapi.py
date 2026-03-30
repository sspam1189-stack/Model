# pyMLB/scripts/sources/odds_theoddsapi.py
# Fetch MLB spreads + totals from The Odds API (fallback when FanDuel unavailable).
# Requires env var: ODDS_API_KEY

import os
import re
import math
import json
import requests
import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

_dir = os.path.dirname(os.path.abspath(__file__))
ODDS_CACHE_DIR = os.path.join(_dir, "..", "..", "..", "data", "odds_cache", "mlb")

BASE = "https://api.the-odds-api.com/v4"


def _norm_team(name):
    return re.sub(r"\s+", " ", str(name or "")).strip()


def _today_iso_chicago():
    now = datetime.datetime.now(ZoneInfo("America/Chicago"))
    return now.strftime("%Y-%m-%d")


def _to_model_line(home_team, away_team, spread_points, team_for_spread):
    """Convert spread to model format: negative = home favored."""
    if not isinstance(spread_points, (int, float)) or not math.isfinite(spread_points):
        return None
    is_home = team_for_spread == home_team
    is_away = team_for_spread == away_team
    if not is_home and not is_away:
        return None
    if is_home:
        return spread_points
    return -spread_points


def _pick_best_bookmaker(bookmakers):
    if not isinstance(bookmakers, list) or len(bookmakers) == 0:
        return None
    preferred = ["DraftKings", "FanDuel", "BetMGM", "Caesars", "PointsBet", "BetRivers"]
    for p in preferred:
        b = next((x for x in bookmakers if x and x.get("title") == p), None)
        if b:
            return b
    return bookmakers[0]


def _find_market(bookmaker, key):
    if not bookmaker or not bookmaker.get("markets"):
        return None
    return next((m for m in bookmaker["markets"] if m and m.get("key") == key), None)


def fetch_mlb_odds():
    """Fetch today's MLB odds from The Odds API."""
    api_key = os.environ.get("ODDS_API_KEY", "")
    if not api_key:
        print("  [odds] WARNING: ODDS_API_KEY not set")
        return []

    url = (
        f"{BASE}/sports/baseball_mlb/odds?"
        f"apiKey={quote(api_key)}"
        f"&regions=us"
        f"&markets=spreads,totals"
        f"&oddsFormat=american"
    )

    res = requests.get(url, timeout=30)
    if res.status_code != 200:
        raise Exception(f"TheOddsAPI failed: {res.status_code} {res.reason} {res.text[:200]}")

    data = res.json()
    today = _today_iso_chicago()
    games = []
    now = datetime.datetime.now(datetime.timezone.utc)

    for ev in data:
        home = _norm_team(ev.get("home_team"))
        away = _norm_team(ev.get("away_team"))
        if not home or not away:
            continue

        commence_str = ev.get("commence_time")
        if commence_str:
            try:
                commence = datetime.datetime.fromisoformat(commence_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                commence = None
            if commence:
                d_chicago = commence.astimezone(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d")
                if d_chicago != today:
                    continue
                if commence <= now:
                    continue

        book = _pick_best_bookmaker(ev.get("bookmakers"))
        line = None
        total = None

        spreads = _find_market(book, "spreads") if book else None
        totals = _find_market(book, "totals") if book else None

        if spreads and spreads.get("outcomes"):
            out = next(
                (o for o in spreads["outcomes"]
                 if isinstance(o.get("point"), (int, float)) and math.isfinite(float(o["point"]))),
                None,
            )
            if out:
                team_for_spread = _norm_team(out.get("name"))
                pts = float(out["point"])
                line = _to_model_line(home, away, pts, team_for_spread)

        if totals and totals.get("outcomes"):
            out = next(
                (o for o in totals["outcomes"]
                 if isinstance(o.get("point"), (int, float)) and math.isfinite(float(o["point"]))),
                None,
            )
            if out:
                total = float(out["point"])

        games.append({
            "away": away,
            "home": home,
            "line": line,
            "total": total,
            "commenceTimeIso": commence_str,
            "_book": book.get("title") if book else None,
        })

    # Cache
    date_key = today.replace("-", "")
    cache_path = os.path.join(ODDS_CACHE_DIR, date_key + ".json")
    existing = {}
    try:
        if os.path.exists(cache_path):
            with open(cache_path, "r") as f:
                existing = json.load(f)
    except Exception:
        pass

    if games:
        try:
            os.makedirs(ODDS_CACHE_DIR, exist_ok=True)
            for g in games:
                key = f"{g['away']}@{g['home']}"
                if key not in existing:  # Don't overwrite FanDuel data
                    existing[key] = {"line": g["line"], "total": g["total"], "_book": g["_book"], "_note": "odds_api"}
            with open(cache_path, "w") as f:
                json.dump(existing, f, indent=2)
        except Exception:
            pass

    # Backfill started games from cache
    fresh_keys = {f"{g['away']}@{g['home']}" for g in games}
    for key, val in existing.items():
        if key not in fresh_keys and val.get("line") is not None:
            parts = key.split("@", 1)
            if len(parts) == 2:
                games.append({"away": parts[0], "home": parts[1], "line": val["line"],
                              "total": val.get("total"), "_book": val.get("_book")})

    return games


def fetch_historical_odds(date_str):
    """
    Load odds from disk cache for a historical date.
    Returns list of game dicts from the cached JSON.
    """
    cache_path = os.path.join(ODDS_CACHE_DIR, date_str + ".json")
    if not os.path.exists(cache_path):
        return []
    with open(cache_path, "r") as f:
        data = json.load(f)
    games = []
    for key, val in data.items():
        parts = key.split("@", 1)
        if len(parts) == 2:
            games.append({
                "away": parts[0], "home": parts[1],
                "line": val.get("line"), "total": val.get("total"),
                "_book": val.get("_book"),
            })
    return games
