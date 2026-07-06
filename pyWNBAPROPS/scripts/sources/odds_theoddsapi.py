# pyWNBAPROPS/scripts/sources/odds_theoddsapi.py
# Fetch WNBA player prop lines from The Odds API.
#
# Fork of pyNBAPROPS's odds_theoddsapi.py: the only functional swaps are the
# sport key (basketball_nba -> basketball_wnba), the team-name map (WNBA
# clubs), and a self-contained WNBA props cache dir. Prop market keys
# (player_points, player_rebounds, ...) are identical across leagues.

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
SPORT_KEY = "basketball_wnba"

# Full team name -> abbreviation (normalize to nba_api WNBA abbreviations).
# Same comprehensive map as odds_fanduel.py.
_TEAM_ABBR = {
    "Atlanta Dream": "ATL", "Chicago Sky": "CHI", "Connecticut Sun": "CON",
    "Dallas Wings": "DAL", "Golden State Valkyries": "GSV", "Indiana Fever": "IND",
    "Los Angeles Sparks": "LAS", "Las Vegas Aces": "LVA", "Minnesota Lynx": "MIN",
    "New York Liberty": "NYL", "Phoenix Mercury": "PHX", "Seattle Storm": "SEA",
    "Washington Mystics": "WAS", "Portland Fire": "PDX", "Toronto Tempo": "TOR",
    # LA / Golden State / NY / LV variations
    "LA Sparks": "LAS", "L.A. Sparks": "LAS",
    "Golden State": "GSV", "Las Vegas": "LVA", "New York": "NYL",
    # City-only
    "Atlanta": "ATL", "Chicago": "CHI", "Connecticut": "CON", "Dallas": "DAL",
    "Indiana": "IND", "Los Angeles": "LAS", "Minnesota": "MIN", "Phoenix": "PHX",
    "Seattle": "SEA", "Washington": "WAS", "Portland": "PDX", "Toronto": "TOR",
    # Mascot-only
    "Dream": "ATL", "Sky": "CHI", "Sun": "CON", "Wings": "DAL",
    "Valkyries": "GSV", "Fever": "IND", "Sparks": "LAS", "Aces": "LVA",
    "Lynx": "MIN", "Liberty": "NYL", "Mercury": "PHX", "Storm": "SEA",
    "Mystics": "WAS", "Fire": "PDX", "Tempo": "TOR",
}

# Self-contained WNBA props cache inside pyWNBAPROPS/data.
_PROPS_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "props_cache" / "wnba"


def _props_cache_path(date_key):
    """Cache path for a given date (YYYYMMDD)."""
    return _PROPS_CACHE_DIR / f"wnba_props_{date_key}.json"


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
                print(f"  [wnba_props] Rate limited (429), retry {attempt}/{max_retries} in {wait:.1f}s...")
                time.sleep(wait)
                continue
            if res.status_code >= 500:
                wait = min(base_wait * (2 ** (attempt - 1)), max_wait)
                print(f"  [wnba_props] Server error {res.status_code}, retry {attempt}/{max_retries} in {wait:.1f}s...")
                time.sleep(wait)
                continue
            # Other client errors: don't retry
            return res
        except requests.exceptions.Timeout:
            wait = min(base_wait * (2 ** (attempt - 1)), max_wait)
            print(f"  [wnba_props] Timeout, retry {attempt}/{max_retries} in {wait:.1f}s...")
            time.sleep(wait)
        except Exception as e:
            if attempt == max_retries:
                raise
            wait = min(base_wait * (2 ** (attempt - 1)), max_wait)
            print(f"  [wnba_props] Error ({e}), retry {attempt}/{max_retries} in {wait:.1f}s...")
            time.sleep(wait)
    return None


def _save_cache(data, path):
    """Save data as JSON cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# Ordered list of Odds API keys. Each call tries them in order: if a key
# is exhausted (HTTP 401/429 or returns 0 events when games are expected),
# we fall through to the next. Override with env var ODDS_API_KEYS (comma
# separated) or single ODDS_API_KEY.
_DEFAULT_ODDS_API_KEYS = [
    "00a735b809911c5a994857dd5af3d0f2",
    "6c5699682d30fc8664737160274f8d12",
    "02a0a1d695d50185aac07fd84b965f9d",
]


def _get_odds_api_keys():
    multi = os.environ.get("ODDS_API_KEYS", "").strip()
    if multi:
        keys = [k.strip() for k in multi.split(",") if k.strip()]
        if keys:
            return keys
    single = os.environ.get("ODDS_API_KEY", "").strip()
    if single:
        return [single]
    return list(_DEFAULT_ODDS_API_KEYS)


def _fetch_events_with_key_rotation(events_url_fmt, tag="wnba_props"):
    """Try each API key until one returns a non-empty events list.

    events_url_fmt: a format string with a single {key} placeholder.
    Returns (events_list, working_api_key) or ([], None) if all keys
    are exhausted or fail.
    """
    keys = _get_odds_api_keys()
    for idx, key in enumerate(keys):
        url = events_url_fmt.format(key=quote(key))
        try:
            res = _fetch_with_retry(url)
        except Exception as e:
            print(f"  [{tag}] Events fetch error with key {idx+1}/{len(keys)}: {e}")
            continue
        if res is None:
            print(f"  [{tag}] Events fetch failed (no response) with key {idx+1}/{len(keys)}")
            continue
        if res.status_code in (401, 429):
            print(f"  [{tag}] Key {idx+1}/{len(keys)} returned HTTP {res.status_code} — rotating to next key")
            continue
        if res.status_code != 200:
            print(f"  [{tag}] Events fetch HTTP {res.status_code} with key {idx+1}/{len(keys)} — rotating")
            continue
        try:
            events = res.json()
            # Historical endpoint wraps events in {"data": [...]}; live is bare list
            if isinstance(events, dict):
                events = events.get("data", [])
        except Exception:
            continue
        if not events:
            print(f"  [{tag}] Key {idx+1}/{len(keys)} returned 0 events — rotating to next key")
            continue
        return events, key
    return [], None


# 2026-07-06: FanDuel first (it is the primary odds source for this model),
# and selection is now PER MARKET, not per event. The old _pick_best_bookmaker
# kept one book's entire board and discarded every other book — so a
# DraftKings threes-only morning board silently threw away FanDuel's posted
# points/rebounds/assists lines.
_BOOK_PREFERENCE = ["FanDuel", "DraftKings", "BetMGM", "Caesars", "PointsBet", "BetRivers"]


def _iter_preferred_markets(bookmakers):
    """Yield one market dict per market key, each taken from the most
    preferred bookmaker that offers it."""
    def rank(b):
        title = b.get("title", "")
        return _BOOK_PREFERENCE.index(title) if title in _BOOK_PREFERENCE else len(_BOOK_PREFERENCE)

    seen = set()
    for b in sorted((x for x in bookmakers if x), key=rank):
        for m in b.get("markets", []):
            k = m.get("key", "")
            if k and k not in seen:
                seen.add(k)
                yield m


def fetch_wnba_player_props(date_key=None, save_cache=True):
    """
    Fetch WNBA player prop lines for today's games.

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

    # Load existing cache for started-game preservation
    cp = _props_cache_path(date_key)
    existing_props = _load_cache(cp, max_age_hours=None) or []

    # Step 1: Get WNBA event IDs — rotates through keys if first is exhausted
    events_url_fmt = f"{BASE}/sports/{SPORT_KEY}/events?apiKey={{key}}"
    events, api_key = _fetch_events_with_key_rotation(events_url_fmt, tag="wnba_props")
    if not events or api_key is None:
        print("  [wnba_props] No WNBA events from any key — skipping prop odds fetch")
        return []

    print(f"  [wnba_props] Found {len(events)} WNBA events")

    # Step 2: For each event, fetch prop odds
    # Always fetch fresh for pre-game, skip started/finished games
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    new_props = []
    started_games = set()
    markets_str = ",".join(PROP_MARKETS_API)

    for ev in events:
        event_id = ev.get("id")
        home = _TEAM_ABBR.get(ev.get("home_team", ""), ev.get("home_team", ""))
        away = _TEAM_ABBR.get(ev.get("away_team", ""), ev.get("away_team", ""))

        # Check if game has started
        commence_str = ev.get("commence_time", "")
        if commence_str:
            try:
                commence = datetime.datetime.fromisoformat(commence_str.replace("Z", "+00:00"))
                if commence <= now_utc:
                    started_games.add(f"{away} @ {home}")
                    continue
            except (ValueError, AttributeError):
                pass

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

        for market in _iter_preferred_markets(bookmakers):
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
                new_props.append({
                    **pl,
                    "market": internal_market,
                    "event_home": home,
                    "event_away": away,
                    "source": "odds_api",
                })

        time.sleep(0.3)  # Rate limit

    # Merge: keep cached props ONLY for started/finished games, fresh for everything else
    kept_props = [p for p in existing_props
                  if f"{p.get('event_away', '')} @ {p.get('event_home', '')}" in started_games]
    all_props = kept_props + new_props

    if started_games:
        print(f"  [wnba_props] Preserved {len(kept_props)} cached lines for {len(started_games)} started games")
    print(f"  [wnba_props] Fetched {len(new_props)} new lines, {len(all_props)} total")

    # Save (skipped when the caller uses this fetch as a gap-filler —
    # FanDuel is the primary source and owns the per-date cache; the
    # caller re-saves the merged FanDuel-first board itself)
    if all_props and save_cache:
        _save_cache(all_props, cp)

    return all_props


def fetch_historical_wnba_props(date_str, api_key=None):
    """
    Fetch historical WNBA player prop lines for a specific date.

    Parameters
    ----------
    date_str : str
        Date in YYYY-MM-DD format.
    api_key : str or None
        The Odds API key. Uses env var if None.

    Returns
    -------
    list[dict]
        Same format as fetch_wnba_player_props.
    """
    date_key = date_str.replace("-", "")

    # Check permanent cache
    cp = _props_cache_path(date_key)
    cached = _load_cache(cp, max_age_hours=None)
    if cached is not None:
        print(f"  [wnba_props] Using cached historical props: {cp.name}")
        return cached

    # Historical endpoint requires ISO timestamp
    ts_utc = f"{date_str}T17:00:00Z"  # ~12pm ET, before most NBA games

    # Step 1: Historical events. Explicit api_key arg short-circuits rotation;
    # otherwise rotate through configured keys so exhausted keys fall through.
    if api_key:
        events_url = (
            f"{BASE}/historical/sports/{SPORT_KEY}/events?"
            f"apiKey={quote(api_key)}"
            f"&date={quote(ts_utc)}"
        )
        try:
            res = _fetch_with_retry(events_url)
            if res is None or res.status_code != 200:
                print(f"  [wnba_props] Historical events failed")
                return []
            events_data = res.json().get("data", [])
        except Exception as e:
            print(f"  [wnba_props] Historical events error: {e}")
            return []
    else:
        events_url_fmt = (
            f"{BASE}/historical/sports/{SPORT_KEY}/events?"
            f"apiKey={{key}}"
            f"&date={quote(ts_utc)}"
        )
        events_data, api_key = _fetch_events_with_key_rotation(events_url_fmt, tag="wnba_props")
        if not events_data or api_key is None:
            print(f"  [wnba_props] Historical events failed for {date_str} (all keys)")
            return []

    if not events_data:
        return []

    # Step 2: Per-event prop odds
    markets_str = ",".join(PROP_MARKETS_API)
    all_props = []

    for ev in events_data:
        event_id = ev.get("id")
        home = _TEAM_ABBR.get(ev.get("home_team", ""), ev.get("home_team", ""))
        away = _TEAM_ABBR.get(ev.get("away_team", ""), ev.get("away_team", ""))

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

        for market in _iter_preferred_markets(bookmakers):
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

    print(f"  [wnba_props] Fetched {len(all_props)} historical prop lines for {date_str}")

    if all_props:
        _save_cache(all_props, cp)

    return all_props
