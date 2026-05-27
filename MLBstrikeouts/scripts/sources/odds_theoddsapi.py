# MLBstrikeouts/scripts/sources/odds_theoddsapi.py
# Fetch MLB pitcher prop lines from The Odds API.
#
# Adapted from pyNBAPROPS/scripts/sources/odds_theoddsapi.py for MLB.
# Uses per-event prop odds endpoint with MLB-specific market keys.

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
SPORT_KEY = "baseball_mlb"

# Full team name -> abbreviation (normalize to match game logs)
_TEAM_ABBR = {
    "Arizona Diamondbacks": "ARI", "Atlanta Braves": "ATL",
    "Baltimore Orioles": "BAL", "Boston Red Sox": "BOS",
    "Chicago Cubs": "CHC", "Chicago White Sox": "CWS",
    "Cincinnati Reds": "CIN", "Cleveland Guardians": "CLE",
    "Colorado Rockies": "COL", "Detroit Tigers": "DET",
    "Houston Astros": "HOU", "Kansas City Royals": "KCR",
    "Los Angeles Angels": "LAA", "Los Angeles Dodgers": "LAD",
    "Miami Marlins": "MIA", "Milwaukee Brewers": "MIL",
    "Minnesota Twins": "MIN", "New York Mets": "NYM",
    "New York Yankees": "NYY", "Oakland Athletics": "OAK",
    "Philadelphia Phillies": "PHI", "Pittsburgh Pirates": "PIT",
    "San Diego Padres": "SDP", "San Francisco Giants": "SFG",
    "Seattle Mariners": "SEA", "St. Louis Cardinals": "STL",
    "Tampa Bay Rays": "TBR", "Texas Rangers": "TEX",
    "Toronto Blue Jays": "TOR", "Washington Nationals": "WSN",
    # Alternate names
    "Cleveland Indians": "CLE",
    "LA Angels": "LAA", "L.A. Angels": "LAA", "Anaheim Angels": "LAA",
    "LA Dodgers": "LAD", "L.A. Dodgers": "LAD",
    "NY Mets": "NYM", "NY Yankees": "NYY",
    "Chi Cubs": "CHC", "Chi White Sox": "CWS",
    "TB Rays": "TBR", "Tampa Bay": "TBR",
    "SF Giants": "SFG", "San Francisco": "SFG",
    "SD Padres": "SDP", "San Diego": "SDP",
    "KC Royals": "KCR", "Kansas City": "KCR",
    "St Louis Cardinals": "STL", "Saint Louis Cardinals": "STL",
    # City-only
    "Arizona": "ARI", "Atlanta": "ATL",
    "Baltimore": "BAL", "Boston": "BOS",
    "Cincinnati": "CIN", "Cleveland": "CLE",
    "Colorado": "COL", "Detroit": "DET",
    "Houston": "HOU", "Miami": "MIA",
    "Milwaukee": "MIL", "Minnesota": "MIN",
    "Oakland": "OAK", "Philadelphia": "PHI",
    "Pittsburgh": "PIT", "Seattle": "SEA",
    "Texas": "TEX", "Toronto": "TOR",
    "Washington": "WSN",
}

BATTER_PROP_MARKETS_API = [
    "batter_total_bases",
]

BATTER_MARKET_MAP = {
    "batter_total_bases": "total_bases",
}

_PROPS_CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "props_cache" / "mlb"


def _props_cache_path(date_key):
    """Cache path for a given date (YYYYMMDD)."""
    return _PROPS_CACHE_DIR / f"mlb_props_{date_key}.json"


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
    Ported from pyNBAPROPS/scripts/sources/odds_theoddsapi.py.
    """
    for attempt in range(1, max_retries + 1):
        try:
            res = requests.get(url, timeout=timeout)
            if res.status_code == 200:
                return res
            if res.status_code == 429:
                wait = min(base_wait * (2 ** (attempt - 1)), max_wait)
                print(f"  [mlb_props] Rate limited (429), retry {attempt}/{max_retries} in {wait:.1f}s...")
                time.sleep(wait)
                continue
            if res.status_code >= 500:
                wait = min(base_wait * (2 ** (attempt - 1)), max_wait)
                print(f"  [mlb_props] Server error {res.status_code}, retry {attempt}/{max_retries} in {wait:.1f}s...")
                time.sleep(wait)
                continue
            # Other client errors: don't retry
            return res
        except requests.exceptions.Timeout:
            wait = min(base_wait * (2 ** (attempt - 1)), max_wait)
            print(f"  [mlb_props] Timeout, retry {attempt}/{max_retries} in {wait:.1f}s...")
            time.sleep(wait)
        except Exception as e:
            if attempt == max_retries:
                raise
            wait = min(base_wait * (2 ** (attempt - 1)), max_wait)
            print(f"  [mlb_props] Error ({e}), retry {attempt}/{max_retries} in {wait:.1f}s...")
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


def _fetch_events_with_key_rotation(events_url_fmt):
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
            print(f"  [mlb_props] Events fetch error with key {idx+1}/{len(keys)}: {e}")
            continue
        if res is None:
            print(f"  [mlb_props] Events fetch failed (no response) with key {idx+1}/{len(keys)}")
            continue
        if res.status_code in (401, 429):
            print(f"  [mlb_props] Key {idx+1}/{len(keys)} returned HTTP {res.status_code} — rotating to next key")
            continue
        if res.status_code != 200:
            print(f"  [mlb_props] Events fetch HTTP {res.status_code} with key {idx+1}/{len(keys)} — rotating")
            continue
        try:
            events = res.json()
        except Exception:
            continue
        if not events:
            print(f"  [mlb_props] Key {idx+1}/{len(keys)} returned 0 events — rotating to next key")
            continue
        return events, key
    return [], None


def _pick_best_bookmaker(bookmakers):
    """Select preferred bookmaker from list."""
    preferred = ["DraftKings", "FanDuel", "BetMGM", "Caesars", "PointsBet", "BetRivers"]
    for p in preferred:
        b = next((x for x in bookmakers if x and x.get("title") == p), None)
        if b:
            return b
    return bookmakers[0] if bookmakers else None


def fetch_mlb_pitcher_props(date_key=None):
    """
    Fetch MLB pitcher prop lines for today's games.

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

    # Step 1: Get MLB event IDs — rotates through keys if first is exhausted
    events_url_fmt = f"{BASE}/sports/{SPORT_KEY}/events?apiKey={{key}}"
    events, api_key = _fetch_events_with_key_rotation(events_url_fmt)
    if not events or api_key is None:
        print("  [mlb_props] No events from any key — skipping prop odds fetch")
        return []

    print(f"  [mlb_props] Found {len(events)} MLB events")

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
                    started_games.add(event_id)
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
                new_props.append({
                    **pl,
                    "market": internal_market,
                    "event_home": home,
                    "event_away": away,
                    "_event_id": event_id,
                })

        time.sleep(0.3)  # Rate limit

    # Cache rule: upcoming games refetch+overwrite; started games frozen
    def _line_key(p):
        return (p.get("player", ""), p.get("market", ""),
                p.get("event_away", ""), p.get("event_home", ""))

    def _is_started(p):
        return p.get("_event_id") in started_games

    new_props = [p for p in new_props if not _is_started(p)]

    # Rotowire is the primary source. Drop any fresh Odds-API entry whose
    # (player, market, game) line_key already exists in the cache tagged
    # as a Rotowire row — Odds API only fills gaps Rotowire didn't cover.
    rotowire_keys = {
        _line_key(p) for p in existing_props
        if str(p.get("source", "")).startswith("rotowire")
    }
    rotowire_skipped = sum(
        1 for p in new_props if _line_key(p) in rotowire_keys
    )
    new_props = [p for p in new_props if _line_key(p) not in rotowire_keys]

    fresh_keys = {_line_key(p) for p in new_props}
    kept_props = [p for p in existing_props if _line_key(p) not in fresh_keys]
    all_props = kept_props + new_props

    print(f"  [mlb_props] Kept {len(kept_props)} cached + {len(new_props)} fresh "
          f"({len(started_games)} games locked, "
          f"{rotowire_skipped} skipped as rotowire-covered) — total {len(all_props)}")

    # Save
    if all_props:
        _save_cache(all_props, cp)

    return all_props


def fetch_historical_mlb_props(date_str, api_key=None):
    """
    Fetch historical MLB pitcher prop lines for a specific date.

    Uses a SINGLE batch API call to get all events + all markets at once,
    instead of per-event calls. This minimizes API credit usage.

    API flow:
      1. One call to /historical/sports/{sport}/events to get event list (1 credit)
      2. One call per event to /historical/sports/{sport}/events/{id}/odds
         with ALL markets comma-separated (1 credit per event, not per market)

    Caching: Results are permanently cached per date — once fetched, never re-fetched.
    For backfill, run fetch_odds.py first to pre-populate the cache, then the
    backfill reads from cache only (0 API calls).

    Parameters
    ----------
    date_str : str
        Date in YYYY-MM-DD format.
    api_key : str or None
        The Odds API key. Uses env var if None.

    Returns
    -------
    list[dict]
        Same format as fetch_mlb_pitcher_props.
    """
    date_key = date_str.replace("-", "")

    # Check permanent cache FIRST — if cached, zero API calls
    cp = _props_cache_path(date_key)
    cached = _load_cache(cp, max_age_hours=None)
    if cached is not None:
        return cached  # Silent — backfill calls this hundreds of times

    # Historical endpoint requires ISO timestamp
    ts_utc = f"{date_str}T17:00:00Z"  # ~1pm ET, before most MLB games

    # Step 1: ONE call to get all events for this date (1 API credit).
    # If api_key was passed explicitly use it; otherwise rotate through
    # the configured keys so an exhausted key falls through to the next.
    if api_key:
        events_url = (
            f"{BASE}/historical/sports/{SPORT_KEY}/events?"
            f"apiKey={quote(api_key)}"
            f"&date={quote(ts_utc)}"
        )
        try:
            res = _fetch_with_retry(events_url)
            if res is None or res.status_code != 200:
                print(f"  [mlb_props] Historical events failed for {date_str}")
                return []
            events_data = res.json().get("data", [])
        except Exception as e:
            print(f"  [mlb_props] Historical events error: {e}")
            return []
    else:
        events_url_fmt = (
            f"{BASE}/historical/sports/{SPORT_KEY}/events?"
            f"apiKey={{key}}"
            f"&date={quote(ts_utc)}"
        )
        keys = _get_odds_api_keys()
        events_data = []
        for idx, k in enumerate(keys):
            url = events_url_fmt.format(key=quote(k))
            try:
                res = _fetch_with_retry(url)
            except Exception:
                continue
            if res is None or res.status_code in (401, 429):
                print(f"  [mlb_props] Historical key {idx+1}/{len(keys)} exhausted — rotating")
                continue
            if res.status_code != 200:
                continue
            try:
                events_data = res.json().get("data", [])
            except Exception:
                continue
            if events_data:
                api_key = k
                break
            print(f"  [mlb_props] Historical key {idx+1}/{len(keys)} returned 0 events — rotating")
        if not api_key or not events_data:
            print(f"  [mlb_props] Historical events failed for {date_str} (all keys)")
            return []

    if not events_data:
        # No games this date — cache empty list to avoid re-fetching
        _save_cache([], cp)
        return []

    # Step 2: ONE call per event with ALL markets batched (1 credit each)
    # All 4 markets in a single comma-separated param = 1 API call per game
    markets_str = ",".join(PROP_MARKETS_API)
    all_props = []
    n_events = len(events_data)

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

    # Cost: 1 (events) + N (per-event odds) API credits for this date
    print(f"  [mlb_props] Fetched {len(all_props)} props for {date_str} ({n_events} games, {n_events + 1} API calls)")

    # Permanently cache — historical data never changes
    _save_cache(all_props if all_props else [], cp)

    return all_props


def fetch_historical_mlb_props_batch(start_date, end_date=None, dry_run=False, delay=1.0):
    """
    Pre-fetch and cache historical MLB props for a date range.

    Run this BEFORE the backfill to populate the cache. The backfill then
    reads from cache only — zero API calls during backtesting.

    Each date costs: 1 (events) + N_games (per-event odds) API credits.
    Already-cached dates are FREE (skipped entirely).

    Usage:
        from sources.odds_theoddsapi import fetch_historical_mlb_props_batch
        fetch_historical_mlb_props_batch("2026-04-01", "2026-04-13")

    Parameters
    ----------
    start_date : str
        Start date (YYYY-MM-DD).
    end_date : str or None
        End date (YYYY-MM-DD). Defaults to start_date.
    dry_run : bool
        If True, show what would be fetched without making API calls.
    delay : float
        Seconds between date fetches (rate limiting).
    """
    from datetime import timedelta

    if end_date is None:
        end_date = start_date

    start = datetime.datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.datetime.strptime(end_date, "%Y-%m-%d")
    total_days = (end - start).days + 1

    print(f"\n{'='*60}")
    print(f"  MLB PROP ODDS BATCH FETCH & CACHE")
    print(f"  Range: {start_date} to {end_date} ({total_days} days)")
    if dry_run:
        print(f"  MODE: DRY RUN (no API calls)")
    print(f"{'='*60}")

    cached_count = 0
    fetched_count = 0
    empty_count = 0
    error_count = 0
    total_api_calls = 0

    current = start
    while current <= end:
        ds = current.strftime("%Y-%m-%d")
        dk = current.strftime("%Y%m%d")

        # Check cache first — FREE if already cached
        cp = _props_cache_path(dk)
        cached = _load_cache(cp, max_age_hours=None)
        if cached is not None:
            print(f"  {ds}: CACHED ({len(cached)} lines)")
            cached_count += 1
            current += timedelta(days=1)
            continue

        if dry_run:
            print(f"  {ds}: WOULD FETCH (not cached)")
            current += timedelta(days=1)
            continue

        try:
            props = fetch_historical_mlb_props(ds)
            if props:
                fetched_count += 1
            else:
                empty_count += 1
        except Exception as e:
            error_count += 1
            print(f"  {ds}: ERROR — {e}")

        time.sleep(delay)
        current += timedelta(days=1)

    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"  Already cached: {cached_count} (FREE)")
    print(f"  Newly fetched:  {fetched_count}")
    print(f"  Empty dates:    {empty_count}")
    if error_count:
        print(f"  Errors:         {error_count}")
    print(f"  Cache dir: {_PROPS_CACHE_DIR}")
    print(f"{'='*60}\n")


def _batter_props_cache_path(date_key):
    """Cache path for batter props on a given date (YYYYMMDD)."""
    return _PROPS_CACHE_DIR / f"mlb_batter_props_{date_key}.json"


def fetch_mlb_batter_props(date_str=None, api_key=None):
    """
    Fetch MLB batter prop lines (total bases) for today's games.

    Parameters
    ----------
    date_str : str or None
        Date in YYYYMMDD format for caching. Auto-detected if None.
    api_key : str or None
        The Odds API key. Uses env var if None.

    Returns
    -------
    list[dict]
        [{"player": str, "player_id": None, "team": str, "market": str,
          "line": float, "over_price": int, "under_price": int}, ...]
    """
    from zoneinfo import ZoneInfo

    if date_str is None:
        now = datetime.datetime.now(ZoneInfo("America/Chicago"))
        date_str = now.strftime("%Y%m%d")

    # Load existing cache for started-game preservation
    cp = _batter_props_cache_path(date_str)
    existing_props = _load_cache(cp, max_age_hours=None) or []

    # Step 1: Get MLB event IDs — rotates through keys if first is exhausted
    if api_key:
        events_url = f"{BASE}/sports/{SPORT_KEY}/events?apiKey={quote(api_key)}"
        try:
            res = _fetch_with_retry(events_url)
            if res is None or res.status_code != 200:
                print(f"  [mlb_batter_props] Events fetch failed")
                return []
            events = res.json()
        except Exception as e:
            print(f"  [mlb_batter_props] Events fetch error: {e}")
            return []
    else:
        events_url_fmt = f"{BASE}/sports/{SPORT_KEY}/events?apiKey={{key}}"
        events, api_key = _fetch_events_with_key_rotation(events_url_fmt)
        if not events or api_key is None:
            print("  [mlb_batter_props] No events from any key — skipping batter prop odds fetch")
            return []

    print(f"  [mlb_batter_props] Found {len(events)} MLB events")

    # Step 2: For each event, fetch batter prop odds
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    new_props = []
    started_games = set()
    markets_str = ",".join(BATTER_PROP_MARKETS_API)

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
                    started_games.add(event_id)
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

        book = _pick_best_bookmaker(bookmakers)
        if not book:
            continue

        for market in book.get("markets", []):
            market_key = market.get("key", "")
            internal_market = BATTER_MARKET_MAP.get(market_key)
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
                        player_lines[desc] = {"player": desc, "player_id": None, "line": point}
                    if name == "Over":
                        player_lines[desc]["over_price"] = price
                    elif name == "Under":
                        player_lines[desc]["under_price"] = price

            for pl in player_lines.values():
                new_props.append({
                    **pl,
                    "market": internal_market,
                    "team": None,
                    "event_home": home,
                    "event_away": away,
                    "_event_id": event_id,
                })

        time.sleep(0.3)  # Rate limit

    # Cache rule: upcoming games refetch+overwrite; started games frozen
    def _line_key(p):
        return (p.get("player", ""), p.get("market", ""),
                p.get("event_away", ""), p.get("event_home", ""))

    def _is_started(p):
        return p.get("_event_id") in started_games

    new_props = [p for p in new_props if not _is_started(p)]
    fresh_keys = {_line_key(p) for p in new_props}
    kept_props = [p for p in existing_props if _line_key(p) not in fresh_keys]
    all_props = kept_props + new_props

    print(f"  [mlb_batter_props] Kept {len(kept_props)} cached + {len(new_props)} fresh "
          f"({len(started_games)} games locked) — total {len(all_props)}")

    # Save
    if all_props:
        _save_cache(all_props, cp)

    return all_props
