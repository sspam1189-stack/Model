# pyNBAPROPS/scripts/sources/odds_fanduel.py
# Fetch NBA player prop lines from FanDuel's public sportsbook API.
#
# Free, no API key needed. Uses the same endpoints that power the FanDuel
# sportsbook website. No historical data — live only.
#
# Used as the primary live odds source (saves Odds API credits).
# The Odds API is kept for backtesting (historical lines).

import os
import json
import time
import datetime
import requests
from pathlib import Path

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from defaults import MARKET_MAP

_PROPS_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "props_cache"

# FanDuel API base (Michigan endpoint — works outside MI too)
FD_BASE = "https://sbapi.mi.sportsbook.fanduel.com/api"
FD_API_KEY = "FhMFpcPWXMeyZxOx"  # Public key embedded in FD website

FD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

# FanDuel tab names that contain over/under player prop markets
FD_PROP_TABS = [
    "player-points",
    "player-rebounds",
    "player-assists",
    "player-threes",
    "player-combos",
]

# Map FanDuel market types to our internal market names
FD_MARKET_TYPE_MAP = {
    "TOTAL_POINTS":   "points",
    "TOTAL_REBOUNDS":  "rebounds",
    "TOTAL_ASSISTS":   "assists",
    "TOTAL_POINTS_+_REB_+_AST": "pts_rebs_asts",
}


def _match_fd_market_type(market_type):
    """Map a FanDuel marketType string to our internal market name."""
    mt = market_type.upper()
    # Strip PLAYER_X_ prefix (e.g. PLAYER_B_TOTAL_POINTS -> TOTAL_POINTS)
    for prefix_len in range(len(mt)):
        suffix = mt[prefix_len:]
        for fd_key, internal in FD_MARKET_TYPE_MAP.items():
            if suffix == fd_key:
                return internal
    return None


def _props_cache_path(date_key):
    return _PROPS_CACHE_DIR / f"nba_props_{date_key}.json"


def _load_cache(path, max_age_hours=None):
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


def _save_cache(data, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def fetch_fanduel_nba_props(date_key=None):
    """
    Fetch NBA player prop lines from FanDuel for today's games.

    Parameters
    ----------
    date_key : str or None
        Date in YYYYMMDD format. Auto-detected if None.

    Returns
    -------
    list[dict]
        Same format as The Odds API fetcher:
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
        print(f"  [fanduel] Using cache: {cp.name} ({len(cached)} lines)")
        return cached

    # Step 1: Get NBA events from FanDuel
    nba_url = f"{FD_BASE}/content-managed-page?page=CUSTOM&customPageId=nba&_ak={FD_API_KEY}"
    try:
        res = requests.get(nba_url, headers=FD_HEADERS, timeout=15)
        if res.status_code != 200:
            print(f"  [fanduel] NBA page failed: {res.status_code}")
            return []
        data = res.json()
    except Exception as e:
        print(f"  [fanduel] NBA page error: {e}")
        return []

    events = data.get("attachments", {}).get("events", {})
    # Filter to actual games (have " @ " in name)
    game_events = {eid: ev for eid, ev in events.items() if " @ " in ev.get("name", "")}
    print(f"  [fanduel] Found {len(game_events)} NBA games")

    if not game_events:
        return []

    # Step 2: For each game, fetch player prop tabs
    all_props = []

    for event_id, ev in game_events.items():
        event_name = ev.get("name", "")
        # Parse "Miami Heat @ Cleveland Cavaliers"
        parts = event_name.split(" @ ")
        if len(parts) == 2:
            away_team = parts[0].strip()
            home_team = parts[1].strip()
        else:
            away_team = event_name
            home_team = ""

        for tab in FD_PROP_TABS:
            tab_url = (
                f"{FD_BASE}/event-page?eventId={event_id}"
                f"&tab={tab}&_ak={FD_API_KEY}"
            )
            try:
                r = requests.get(tab_url, headers=FD_HEADERS, timeout=10)
                if r.status_code != 200:
                    continue
                tab_data = r.json()
            except Exception:
                continue

            markets = tab_data.get("attachments", {}).get("markets", {})

            for mk, mv in markets.items():
                market_type = mv.get("marketType", "")
                internal_market = _match_fd_market_type(market_type)
                if not internal_market:
                    continue

                runners = mv.get("runners", [])
                if len(runners) != 2:
                    continue

                # Extract over/under from runners
                over_runner = None
                under_runner = None
                for rn in runners:
                    name = rn.get("runnerName", "")
                    if "Over" in name:
                        over_runner = rn
                    elif "Under" in name:
                        under_runner = rn

                if not over_runner or not under_runner:
                    continue

                # Get player name from runner (e.g. "Donovan Mitchell Over")
                player_name = over_runner.get("runnerName", "").replace(" Over", "").strip()
                line = over_runner.get("handicap")
                if line is None:
                    continue

                over_odds = (over_runner.get("winRunnerOdds", {})
                             .get("americanDisplayOdds", {})
                             .get("americanOdds"))
                under_odds = (under_runner.get("winRunnerOdds", {})
                              .get("americanDisplayOdds", {})
                              .get("americanOdds"))

                try:
                    over_price = int(over_odds) if over_odds else None
                    under_price = int(under_odds) if under_odds else None
                except (ValueError, TypeError):
                    over_price = None
                    under_price = None

                all_props.append({
                    "player": player_name,
                    "market": internal_market,
                    "line": float(line),
                    "over_price": over_price,
                    "under_price": under_price,
                    "event_home": home_team,
                    "event_away": away_team,
                    "source": "fanduel",
                })

            time.sleep(0.2)  # Rate limit between tab requests

    print(f"  [fanduel] Fetched {len(all_props)} player prop lines")

    # Cache results
    if all_props:
        _save_cache(all_props, cp)

    return all_props
