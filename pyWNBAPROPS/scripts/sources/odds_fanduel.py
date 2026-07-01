# pyWNBAPROPS/scripts/sources/odds_fanduel.py
# Fetch WNBA player prop lines from FanDuel's public sportsbook API.
#
# Free, no API key needed. Uses the same endpoints that power the FanDuel
# sportsbook website. No historical data — live only.
#
# Fork of pyNBAPROPS's odds_fanduel.py: swaps the FanDuel customPageId
# (nba -> wnba), the team-name map (WNBA clubs), and points the cache at a
# self-contained WNBA props dir. If FanDuel has no WNBA coverage the fetcher
# degrades gracefully (logs + returns []), and run_daily falls back to The
# Odds API.

import os
import json
import time
import datetime
import requests
from pathlib import Path

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from defaults import MARKET_MAP

# Self-contained WNBA props cache inside pyWNBAPROPS/data.
_PROPS_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "props_cache" / "wnba"

# FanDuel API base (Michigan endpoint — works outside MI too)
FD_BASE = "https://sbapi.mi.sportsbook.fanduel.com/api"
FD_API_KEY = "FhMFpcPWXMeyZxOx"  # Public key embedded in FD website

FD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

# Full team name -> abbreviation (normalize to nba_api WNBA abbreviations).
# Includes all known variations from The Odds API, FanDuel, ESPN, WNBA.com.
TEAM_NAME_TO_ABBR = {
    # Standard full names
    "Atlanta Dream": "ATL", "Chicago Sky": "CHI", "Connecticut Sun": "CON",
    "Dallas Wings": "DAL", "Golden State Valkyries": "GSV", "Indiana Fever": "IND",
    "Los Angeles Sparks": "LAS", "Las Vegas Aces": "LVA", "Minnesota Lynx": "MIN",
    "New York Liberty": "NYL", "Phoenix Mercury": "PHX", "Seattle Storm": "SEA",
    "Washington Mystics": "WAS",
    # 2026 expansion
    "Portland Fire": "PDX", "Toronto Tempo": "TOR",
    # LA / NY variations
    "LA Sparks": "LAS", "L.A. Sparks": "LAS",
    # City-only (ESPN / some APIs)
    "Atlanta": "ATL", "Chicago": "CHI", "Connecticut": "CON", "Dallas": "DAL",
    "Golden State": "GSV", "Indiana": "IND", "Los Angeles": "LAS",
    "Las Vegas": "LVA", "Minnesota": "MIN", "New York": "NYL", "Phoenix": "PHX",
    "Seattle": "SEA", "Washington": "WAS", "Portland": "PDX", "Toronto": "TOR",
    # Mascot-only
    "Dream": "ATL", "Sky": "CHI", "Sun": "CON", "Wings": "DAL",
    "Valkyries": "GSV", "Fever": "IND", "Sparks": "LAS", "Aces": "LVA",
    "Lynx": "MIN", "Liberty": "NYL", "Mercury": "PHX", "Storm": "SEA",
    "Mystics": "WAS", "Fire": "PDX", "Tempo": "TOR",
    # Already abbreviations (pass-through)
    "ATL": "ATL", "CHI": "CHI", "CON": "CON", "DAL": "DAL", "GSV": "GSV",
    "IND": "IND", "LAS": "LAS", "LVA": "LVA", "MIN": "MIN", "NYL": "NYL",
    "PHX": "PHX", "SEA": "SEA", "WAS": "WAS", "PDX": "PDX", "TOR": "TOR",
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
        # Silently ignore known non-prop market types that bleed into player tabs
        # (alt threes like N+_MADE_THREES, game-level markets, etc.)

    return None


def _props_cache_path(date_key):
    return _PROPS_CACHE_DIR / f"wnba_props_{date_key}.json"


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


def fetch_fanduel_wnba_props(date_key=None):
    """
    Fetch WNBA player prop lines from FanDuel for a given ET game date.

    Parameters
    ----------
    date_key : str or None
        ET game date in YYYYMMDD format (the date the games tip off in
        America/New_York). Auto-detected from America/Chicago "now" when
        None — callers wanting tomorrow's slate must pass tomorrow's key
        explicitly. Each ET date gets its own cache file.

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

    # Step 1: Get WNBA events from FanDuel
    wnba_url = f"{FD_BASE}/content-managed-page?page=CUSTOM&customPageId=wnba&_ak={FD_API_KEY}"
    try:
        res = requests.get(wnba_url, headers=FD_HEADERS, timeout=15)
        if res.status_code != 200:
            # Degrade gracefully — caller falls back to The Odds API.
            print(f"  [fanduel] WNBA page failed: {res.status_code} — skipping FanDuel")
            return []
        data = res.json()
    except Exception as e:
        print(f"  [fanduel] WNBA page error: {e} — skipping FanDuel")
        return []

    events = data.get("attachments", {}).get("events", {})

    # Filter to actual games (have " @ " in name) whose ET tip-off date
    # matches the requested ET date. FanDuel `openDate` is UTC
    # (e.g. "2026-05-16T01:40:00.000Z"). NBA games tip in the evening ET,
    # which can fall on the same UTC day (afternoon ET games) or the
    # next UTC day (late-evening ET games like 9:40pm ET = 01:40 UTC).
    # Earlier versions matched openDate[:10] against {target, target+1day}
    # as a UTC heuristic, but that silently dropped late-night ET tip-offs
    # that crossed two UTC days. Convert UTC → ET explicitly instead.
    target_date = f"{date_key[:4]}-{date_key[4:6]}-{date_key[6:8]}"
    et_tz = ZoneInfo("America/New_York")

    game_events = {}
    for eid, ev in events.items():
        if " @ " not in ev.get("name", ""):
            continue
        open_date_str = ev.get("openDate", "")
        if not open_date_str:
            continue
        try:
            open_dt_utc = datetime.datetime.fromisoformat(
                open_date_str.replace("Z", "+00:00")
            )
            et_date = open_dt_utc.astimezone(et_tz).strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            continue
        if et_date == target_date:
            game_events[eid] = ev

    print(f"  [fanduel] Found {len(game_events)} WNBA games for {target_date} (ET)")

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
