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

_PROPS_CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "props_cache" / "nba"

# FanDuel API base (Michigan endpoint — works outside MI too)
FD_BASE = "https://sbapi.mi.sportsbook.fanduel.com/api"
FD_API_KEY = "FhMFpcPWXMeyZxOx"  # Public key embedded in FD website

FD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

# Full team name -> abbreviation (for consistent format with game logs)
# Includes all known variations from Odds API, FanDuel, ESPN, NBA.com
TEAM_NAME_TO_ABBR = {
    # Standard full names
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA", "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN", "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "Memphis Grizzlies": "MEM", "Miami Heat": "MIA", "Milwaukee Bucks": "MIL",
    "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NOP",
    "New York Knicks": "NYK", "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL",
    "Philadelphia 76ers": "PHI", "Phoenix Suns": "PHX",
    "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC",
    "San Antonio Spurs": "SAS", "Toronto Raptors": "TOR",
    "Utah Jazz": "UTA", "Washington Wizards": "WAS",
    # LA teams — all variations
    "Los Angeles Lakers": "LAL", "LA Lakers": "LAL", "L.A. Lakers": "LAL",
    "Los Angeles Clippers": "LAC", "LA Clippers": "LAC", "L.A. Clippers": "LAC",
    # NY/BKN variations
    "NY Knicks": "NYK", "New York Nets": "BKN",
    # City-only (ESPN/some APIs)
    "Atlanta": "ATL", "Boston": "BOS", "Brooklyn": "BKN",
    "Charlotte": "CHA", "Chicago": "CHI", "Cleveland": "CLE",
    "Dallas": "DAL", "Denver": "DEN", "Detroit": "DET",
    "Golden State": "GSW", "Houston": "HOU", "Indiana": "IND",
    "Memphis": "MEM", "Miami": "MIA", "Milwaukee": "MIL",
    "Minnesota": "MIN", "New Orleans": "NOP",
    "New York": "NYK", "Oklahoma City": "OKC", "Orlando": "ORL",
    "Philadelphia": "PHI", "Phoenix": "PHX",
    "Portland": "POR", "Sacramento": "SAC",
    "San Antonio": "SAS", "Toronto": "TOR",
    "Utah": "UTA", "Washington": "WAS",
    # Mascot-only (rare but some APIs)
    "Hawks": "ATL", "Celtics": "BOS", "Nets": "BKN",
    "Hornets": "CHA", "Bulls": "CHI", "Cavaliers": "CLE", "Cavs": "CLE",
    "Mavericks": "DAL", "Mavs": "DAL", "Nuggets": "DEN", "Pistons": "DET",
    "Warriors": "GSW", "Dubs": "GSW", "Rockets": "HOU", "Pacers": "IND",
    "Clippers": "LAC", "Lakers": "LAL",
    "Grizzlies": "MEM", "Grizz": "MEM", "Heat": "MIA", "Bucks": "MIL",
    "Timberwolves": "MIN", "Wolves": "MIN", "TWolves": "MIN", "T-Wolves": "MIN",
    "Pelicans": "NOP", "Pels": "NOP",
    "Knicks": "NYK", "Thunder": "OKC", "Magic": "ORL",
    "76ers": "PHI", "Sixers": "PHI", "Suns": "PHX",
    "Trail Blazers": "POR", "Blazers": "POR", "Kings": "SAC",
    "Spurs": "SAS", "Raptors": "TOR",
    "Jazz": "UTA", "Wizards": "WAS",
    # Already abbreviations (pass-through)
    "ATL": "ATL", "BOS": "BOS", "BKN": "BKN", "CHA": "CHA", "CHI": "CHI",
    "CLE": "CLE", "DAL": "DAL", "DEN": "DEN", "DET": "DET", "GSW": "GSW",
    "HOU": "HOU", "IND": "IND", "LAC": "LAC", "LAL": "LAL", "MEM": "MEM",
    "MIA": "MIA", "MIL": "MIL", "MIN": "MIN", "NOP": "NOP", "NYK": "NYK",
    "OKC": "OKC", "ORL": "ORL", "PHI": "PHI", "PHX": "PHX", "POR": "POR",
    "SAC": "SAC", "SAS": "SAS", "TOR": "TOR", "UTA": "UTA", "WAS": "WAS",
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
    "TOTAL_POINTS":                     "points",
    "TOTAL_REBOUNDS":                   "rebounds",
    "TOTAL_ASSISTS":                    "assists",
    "TOTAL_POINTS_+_REB_+_AST":        "pts_rebs_asts",
    # 3-pointers made — FanDuel uses various names for this market
    "TOTAL_3_POINTERS_MADE":            "threes",
    "TOTAL_MADE_3_POINTERS":            "threes",
    "TOTAL_3POINTERS_MADE":             "threes",
    "TOTAL_3_PT_FIELD_GOALS_MADE":      "threes",
    "TOTAL_3_POINT_FIELD_GOALS_MADE":   "threes",
    "TOTAL_MADE_THREES":                "threes",
    "TOTAL_MADE_THREE_POINTERS":        "threes",
    "TOTAL_THREE_POINTERS_MADE":        "threes",
}

# Substring keywords for three-pointer fallback matching
_THREES_KEYWORDS = ("3_POINT", "3POINT", "THREE_POINT", "THREEPOINT", "3_PT", "3PT_")


def _match_fd_market_type(market_type, tab=None):
    """Map a FanDuel marketType string to our internal market name."""
    mt = market_type.upper()
    # Strip PLAYER_X_ prefix (e.g. PLAYER_B_TOTAL_POINTS -> TOTAL_POINTS)
    for prefix_len in range(len(mt)):
        suffix = mt[prefix_len:]
        for fd_key, internal in FD_MARKET_TYPE_MAP.items():
            if suffix == fd_key:
                return internal

    # Fallback: if fetching the threes tab, match any market type containing
    # three-pointer keywords (catches unexpected FanDuel naming variations)
    if tab == "player-threes":
        for kw in _THREES_KEYWORDS:
            if kw in mt:
                return "threes"
        # Log unmatched types from the threes tab so we can add exact mappings
        print(f"  [fanduel] unmatched threes market type: {market_type!r}")

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

    # Load existing cache for started-game preservation
    cp = _props_cache_path(date_key)
    existing_props = _load_cache(cp, max_age_hours=None) or []

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

    # Filter to actual games (have " @ " in name) on the requested date
    # FanDuel openDate is UTC (e.g. "2026-03-27T23:40:00.000Z")
    # NBA games tip between ~7pm-10pm ET = next day UTC for evening games
    # So a "March 27" game in ET could be "March 27" or "March 28" in UTC
    target_date = f"{date_key[:4]}-{date_key[4:6]}-{date_key[6:8]}"
    # Also accept next day UTC (evening ET games show as next day in UTC)
    from datetime import timedelta
    target_dt = datetime.datetime.strptime(target_date, "%Y-%m-%d")
    next_day = (target_dt + timedelta(days=1)).strftime("%Y-%m-%d")

    game_events = {}
    for eid, ev in events.items():
        if " @ " not in ev.get("name", ""):
            continue
        open_date = ev.get("openDate", "")[:10]  # "2026-03-27"
        if open_date == target_date or open_date == next_day:
            game_events[eid] = ev

    print(f"  [fanduel] Found {len(game_events)} NBA games for {target_date}")

    if not game_events:
        return []

    # Step 2: For each game, fetch player prop tabs
    # Always fetch fresh for pre-game, skip started/finished games
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    new_props = []
    started_games = set()  # track which games have started (preserve from cache)

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

        game_key = f"{away_team} @ {home_team}"

        # Check if game has started (openDate is UTC)
        open_date_str = ev.get("openDate", "")
        if open_date_str:
            try:
                open_dt = datetime.datetime.fromisoformat(open_date_str.replace("Z", "+00:00"))
                if open_dt <= now_utc:
                    started_games.add(game_key)
                    continue
            except (ValueError, AttributeError):
                pass

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
                internal_market = _match_fd_market_type(market_type, tab=tab)
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

                new_props.append({
                    "player": player_name,
                    "market": internal_market,
                    "line": float(line),
                    "over_price": over_price,
                    "under_price": under_price,
                    "event_home": TEAM_NAME_TO_ABBR.get(home_team, home_team),
                    "event_away": TEAM_NAME_TO_ABBR.get(away_team, away_team),
                    "source": "fanduel",
                })

            time.sleep(0.2)  # Rate limit between tab requests

    # Merge: keep cached props ONLY for started/finished games, fresh for everything else
    kept_props = [p for p in existing_props
                  if f"{p.get('event_away', '')} @ {p.get('event_home', '')}" in started_games
                  # Also keep if game key matches using abbreviations
                  or any(TEAM_NAME_TO_ABBR.get(sg.split(" @ ")[0], "") == p.get("event_away", "")
                         and TEAM_NAME_TO_ABBR.get(sg.split(" @ ")[1], "") == p.get("event_home", "")
                         for sg in started_games)]
    all_props = kept_props + new_props

    if started_games:
        print(f"  [fanduel] Preserved {len(kept_props)} cached lines for {len(started_games)} started games")
    print(f"  [fanduel] Fetched {len(new_props)} new lines, {len(all_props)} total")

    # Save
    if all_props:
        _save_cache(all_props, cp)

    return all_props
