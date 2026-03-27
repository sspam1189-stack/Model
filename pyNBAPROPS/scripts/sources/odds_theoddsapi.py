# pyNBAPROPS/scripts/sources/odds_theoddsapi.py
# Fetch NBA player prop lines from The Odds API.
#
# Adapted from pyNFL/scripts/sources/odds_theoddsapi.py for NBA.
# Uses per-event prop odds endpoint with NBA-specific market keys.

import os
import json
import time
import datetime
import requests
from pathlib import Path
from urllib.parse import quote

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from defaults import PROP_MARKETS_API, MARKET_MAP

BASE = "https://api.the-odds-api.com/v4"
SPORT_KEY = "basketball_nba"

_PROPS_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "props_cache"


def _props_cache_path(date_key):
    """Cache path for a given date (YYYYMMDD)."""
    return _PROPS_CACHE_DIR / f"nba_props_{date_key}.json"


def _load_cache(path, max_age_hours=None):
    """Load JSON cache if it exists and isn't too old."""
    if not path.exists():
        return None
    if max_age_hours is not None:
        age_h = (time.time() - path.stat().st_mtime) / 3600
        if age_h > max_age_hours:
            return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return None


def _fetch_with_retry(url, max_retries=5, base_wait=0.7, max_wait=8.0, timeout=30):
    """
    Fetch URL with exponential backoff retry on 429 rate limit errors.
    Ported from pyNFL/scripts/sources/odds_theoddsapi.py.
    """
    for attempt in range(1, max_retries + 1):
        try:
            res = requests.get(url, timeout=timeout)
            if res.status_code == 200:
                return res
            if res.status_code == 429:
                wait = min(base_wait * (2 ** (attempt - 1)), max_wait)
                print(f"  [nba_props] Rate limited (429), retry {attempt}/{max_retries} in {wait:.1f}s...")
                time.sleep(wait)
                continue
            if res.status_code >= 500:
                wait = min(base_wait * (2 ** (attempt - 1)), max_wait)
                print(f"  [nba_props] Server error {res.status_code}, retry {attempt}/{max_retries} in {wait:.1f}s...")
                time.sleep(wait)
                continue
            # Other client errors: don't retry
            return res
        except requests.exceptions.Timeout:
            wait = min(base_wait * (2 ** (attempt - 1)), max_wait)
            print(f"  [nba_props] Timeout, retry {attempt}/{max_retries} in {wait:.1f}s...")
            time.sleep(wait)
        except Exception as e:
            if attempt == max_retries:
                raise
            wait = min(base_wait * (2 ** (attempt - 1)), max_wait)
            print(f"  [nba_props] Error ({e}), retry {attempt}/{max_retries} in {wait:.1f}s...")
            time.sleep(wait)
    return None


def _save_cache(data, path):
    """Save data as JSON cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _pick_best_bookmaker(bookmakers):
    """Select preferred bookmaker from list."""
    preferred = ["DraftKings", "FanDuel", "BetMGM", "Caesars", "PointsBet", "BetRivers"]
    for p in preferred:
        b = next((x for x in bookmakers if x and x.get("title") == p), None)
        if b:
            return b
    return bookmakers[0] if bookmakers else None


def fetch_nba_player_props(date_key=None):
    """
    Fetch NBA player prop lines for today's games.

    Parameters
    ----------
    date_key : str or None
        Date in YYYYMMDD format for caching. Auto-detected if None.

    Returns
    -------
    list[dict]
        [{"player": str, "market": str, "line": float,
          "over_price": int, "under_price": int,
          "event_home": str, "event_away": str}, ...]
    """
    from zoneinfo import ZoneInfo

    if date_key is None:
        now = datetime.datetime.now(ZoneInfo("America/Chicago"))
        date_key = now.strftime("%Y%m%d")

    # Check cache (2-hour freshness for live props)
    cp = _props_cache_path(date_key)
    cached = _load_cache(cp, max_age_hours=2)
    if cached is not None:
        print(f"  [nba_props] Using cache: {cp.name} ({len(cached)} lines)")
        return cached

    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        print("  [nba_props] Missing ODDS_API_KEY — skipping prop odds fetch")
        return []

    # Step 1: Get NBA event IDs
    events_url = f"{BASE}/sports/{SPORT_KEY}/events?apiKey={quote(api_key)}"
    try:
        res = _fetch_with_retry(events_url)
        if res is None or res.status_code != 200:
            print(f"  [nba_props] Events fetch failed")
            return []
        events = res.json()
    except Exception as e:
        print(f"  [nba_props] Events fetch error: {e}")
        return []

    print(f"  [nba_props] Found {len(events)} NBA events")

    # Step 2: For each event, fetch prop odds
    all_props = []
    markets_str = ",".join(PROP_MARKETS_API)

    for ev in events:
        event_id = ev.get("id")
        home = ev.get("home_team", "")
        away = ev.get("away_team", "")

        url = (
            f"{BASE}/sports/{SPORT_KEY}/events/{event_id}/odds?"
            f"apiKey={quote(api_key)}"
            f"&regions=us"
            f"&markets={markets_str}"
            f"&oddsFormat=american"
        )

        try:
            r = _fetch_with_retry(url)
            if r is None or r.status_code != 200:
                continue
            data = r.json()
        except Exception:
            continue

        bookmakers = data.get("bookmakers", [])
        if not bookmakers:
            continue

        book = _pick_best_bookmaker(bookmakers)
        if not book:
            continue

        for market in book.get("markets", []):
            market_key = market.get("key", "")
            internal_market = MARKET_MAP.get(market_key)
            if not internal_market:
                continue

            outcomes = market.get("outcomes", [])
            # Group by player (over/under pairs)
            player_lines = {}
            for o in outcomes:
                desc = o.get("description", "")
                name = o.get("name", "")  # "Over" or "Under"
                point = o.get("point")
                price = o.get("price")
                if desc and point is not None:
                    if desc not in player_lines:
                        player_lines[desc] = {"player": desc, "line": point}
                    if name == "Over":
                        player_lines[desc]["over_price"] = price
                    elif name == "Under":
                        player_lines[desc]["under_price"] = price

            for pl in player_lines.values():
                all_props.append({
                    **pl,
                    "market": internal_market,
                    "event_home": home,
                    "event_away": away,
                })

        time.sleep(0.3)  # Rate limit

    print(f"  [nba_props] Fetched {len(all_props)} player prop lines")

    # Cache results
    if all_props:
        _save_cache(all_props, cp)

    return all_props


def fetch_historical_nba_props(date_str, api_key=None):
    """
    Fetch historical NBA player prop lines for a specific date.

    Parameters
    ----------
    date_str : str
        Date in YYYY-MM-DD format.
    api_key : str or None
        The Odds API key. Uses env var if None.

    Returns
    -------
    list[dict]
        Same format as fetch_nba_player_props.
    """
    date_key = date_str.replace("-", "")

    # Check permanent cache
    cp = _props_cache_path(date_key)
    cached = _load_cache(cp, max_age_hours=None)
    if cached is not None:
        print(f"  [nba_props] Using cached historical props: {cp.name}")
        return cached

    api_key = api_key or os.environ.get("ODDS_API_KEY")
    if not api_key:
        return []

    # Historical endpoint requires ISO timestamp
    ts_utc = f"{date_str}T17:00:00Z"  # ~12pm ET, before most NBA games

    # Step 1: Historical events
    events_url = (
        f"{BASE}/historical/sports/{SPORT_KEY}/events?"
        f"apiKey={quote(api_key)}"
        f"&date={quote(ts_utc)}"
    )
    try:
        res = _fetch_with_retry(events_url)
        if res is None or res.status_code != 200:
            print(f"  [nba_props] Historical events failed")
            return []
        events_data = res.json().get("data", [])
    except Exception as e:
        print(f"  [nba_props] Historical events error: {e}")
        return []

    if not events_data:
        return []

    # Step 2: Per-event prop odds
    markets_str = ",".join(PROP_MARKETS_API)
    all_props = []

    for ev in events_data:
        event_id = ev.get("id")
        home = ev.get("home_team", "")
        away = ev.get("away_team", "")

        url = (
            f"{BASE}/historical/sports/{SPORT_KEY}/events/{event_id}/odds?"
            f"apiKey={quote(api_key)}"
            f"&regions=us"
            f"&markets={markets_str}"
            f"&oddsFormat=american"
            f"&date={quote(ts_utc)}"
        )

        try:
            r = _fetch_with_retry(url)
            if r is None or r.status_code != 200:
                continue
            data = r.json().get("data", {})
        except Exception:
            continue

        bookmakers = data.get("bookmakers", [])
        if not bookmakers:
            continue

        book = _pick_best_bookmaker(bookmakers)
        if not book:
            continue

        for market in book.get("markets", []):
            market_key = market.get("key", "")
            internal_market = MARKET_MAP.get(market_key)
            if not internal_market:
                continue

            outcomes = market.get("outcomes", [])
            player_lines = {}
            for o in outcomes:
                desc = o.get("description", "")
                name = o.get("name", "")
                point = o.get("point")
                price = o.get("price")
                if desc and point is not None:
                    if desc not in player_lines:
                        player_lines[desc] = {"player": desc, "line": point}
                    if name == "Over":
                        player_lines[desc]["over_price"] = price
                    elif name == "Under":
                        player_lines[desc]["under_price"] = price

            for pl in player_lines.values():
                all_props.append({
                    **pl,
                    "market": internal_market,
                    "event_home": home,
                    "event_away": away,
                })

        time.sleep(0.3)

    print(f"  [nba_props] Fetched {len(all_props)} historical prop lines for {date_str}")

    if all_props:
        _save_cache(all_props, cp)

    return all_props
