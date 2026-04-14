# MLBstrikeouts/scripts/sources/odds_fanduel.py
# Fetch MLB pitcher prop lines + total game hits from FanDuel's public API.
#
# Free, no API key needed. Uses the same endpoints that power the FanDuel
# sportsbook website. No historical data -- live only.
#
# Primary live odds source (saves Odds API credits).
# Adapted from pyNBAPROPS/scripts/sources/odds_fanduel.py for MLB.

import os
import json
import time
import datetime
import requests
from pathlib import Path

_PROPS_CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "props_cache" / "mlb"

# FanDuel API base (Michigan endpoint -- works outside MI too)
FD_BASE = "https://sbapi.mi.sportsbook.fanduel.com/api"
FD_API_KEY = "FhMFpcPWXMeyZxOx"  # Public key embedded in FD website

FD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

# ---------------------------------------------------------------------------
# MLB team name -> abbreviation (all 30 teams with common variations)
# ---------------------------------------------------------------------------
MLB_TEAM_NAME_TO_ABBR = {
    # Standard full names
    "Arizona Diamondbacks": "ARI", "Atlanta Braves": "ATL",
    "Baltimore Orioles": "BAL", "Boston Red Sox": "BOS",
    "Chicago Cubs": "CHC", "Chicago White Sox": "CWS",
    "Cincinnati Reds": "CIN", "Cleveland Guardians": "CLE",
    "Colorado Rockies": "COL", "Detroit Tigers": "DET",
    "Houston Astros": "HOU", "Kansas City Royals": "KC",
    "Los Angeles Angels": "LAA", "Los Angeles Dodgers": "LAD",
    "Miami Marlins": "MIA", "Milwaukee Brewers": "MIL",
    "Minnesota Twins": "MIN", "New York Mets": "NYM",
    "New York Yankees": "NYY", "Oakland Athletics": "OAK",
    "Philadelphia Phillies": "PHI", "Pittsburgh Pirates": "PIT",
    "San Diego Padres": "SD", "San Francisco Giants": "SF",
    "Seattle Mariners": "SEA", "St. Louis Cardinals": "STL",
    "Tampa Bay Rays": "TB", "Texas Rangers": "TEX",
    "Toronto Blue Jays": "TOR", "Washington Nationals": "WAS",
    # LA variations
    "LA Angels": "LAA", "L.A. Angels": "LAA", "Anaheim Angels": "LAA",
    "LA Dodgers": "LAD", "L.A. Dodgers": "LAD",
    # St. Louis variations
    "Saint Louis Cardinals": "STL", "St Louis Cardinals": "STL",
    # City-only (ESPN / some APIs)
    "Arizona": "ARI", "Atlanta": "ATL", "Baltimore": "BAL", "Boston": "BOS",
    "Cincinnati": "CIN", "Cleveland": "CLE", "Colorado": "COL",
    "Detroit": "DET", "Houston": "HOU", "Kansas City": "KC",
    "Miami": "MIA", "Milwaukee": "MIL", "Minnesota": "MIN",
    "Oakland": "OAK", "Philadelphia": "PHI", "Pittsburgh": "PIT",
    "San Diego": "SD", "San Francisco": "SF", "Seattle": "SEA",
    "St. Louis": "STL", "Saint Louis": "STL", "Tampa Bay": "TB",
    "Texas": "TEX", "Toronto": "TOR", "Washington": "WAS",
    # City-only ambiguous (Chicago / NY / LA resolved to more common team)
    "Chicago": "CHC",  # ambiguous -- default Cubs
    "New York": "NYY",  # ambiguous -- default Yankees
    "Los Angeles": "LAD",  # ambiguous -- default Dodgers
    # Mascot-only
    "Diamondbacks": "ARI", "D-backs": "ARI", "Dbacks": "ARI",
    "Braves": "ATL", "Orioles": "BAL", "Red Sox": "BOS",
    "Cubs": "CHC", "White Sox": "CWS",
    "Reds": "CIN", "Guardians": "CLE",
    "Rockies": "COL", "Tigers": "DET",
    "Astros": "HOU", "Royals": "KC",
    "Angels": "LAA", "Dodgers": "LAD",
    "Marlins": "MIA", "Brewers": "MIL",
    "Twins": "MIN", "Mets": "NYM",
    "Yankees": "NYY", "Athletics": "OAK", "A's": "OAK",
    "Phillies": "PHI", "Pirates": "PIT",
    "Padres": "SD", "Giants": "SF",
    "Mariners": "SEA", "Cardinals": "STL",
    "Rays": "TB", "Rangers": "TEX",
    "Blue Jays": "TOR", "Jays": "TOR",
    "Nationals": "WAS", "Nats": "WAS",
    # Abbreviation pass-throughs
    "ARI": "ARI", "ATL": "ATL", "BAL": "BAL", "BOS": "BOS",
    "CHC": "CHC", "CWS": "CWS", "CIN": "CIN", "CLE": "CLE",
    "COL": "COL", "DET": "DET", "HOU": "HOU", "KC": "KC",
    "LAA": "LAA", "LAD": "LAD", "MIA": "MIA", "MIL": "MIL",
    "MIN": "MIN", "NYM": "NYM", "NYY": "NYY", "OAK": "OAK",
    "PHI": "PHI", "PIT": "PIT", "SD": "SD", "SF": "SF",
    "SEA": "SEA", "STL": "STL", "TB": "TB", "TEX": "TEX",
    "TOR": "TOR", "WAS": "WAS",
}

# ---------------------------------------------------------------------------
# FanDuel tabs for MLB pitcher props and game lines
# ---------------------------------------------------------------------------
FD_PROP_TABS = [
    "pitcher-strikeouts",
    "pitcher-outs",
    "pitcher-props",       # may contain hits allowed, walks
    "game-lines",          # may contain total game hits
    "innings",
]

# ---------------------------------------------------------------------------
# Map FanDuel market types to internal market names (player props)
# ---------------------------------------------------------------------------
FD_MARKET_TYPE_MAP = {
    # Pitcher strikeouts — FanDuel uses PITCHER_X_TOTAL_STRIKEOUTS format
    "TOTAL_STRIKEOUTS": "strikeouts",
    "PITCHER_STRIKEOUTS": "strikeouts",
    "TOTAL_PITCHER_STRIKEOUTS": "strikeouts",
    "STRIKEOUTS_THROWN": "strikeouts",
    "TOTAL_STRIKEOUTS_THROWN": "strikeouts",
    # Pitcher outs — FanDuel uses PITCHER_X_OUTS_RECORDED_SB format
    "OUTS_RECORDED": "outs",
    "OUTS_RECORDED_SB": "outs",
    "PITCHER_OUTS": "outs",
    "TOTAL_PITCHER_OUTS": "outs",
    "TOTAL_OUTS_RECORDED": "outs",
    "PITCHING_OUTS": "outs",
    # Hits allowed
    "HITS_ALLOWED": "hits_allowed",
    "PITCHER_HITS_ALLOWED": "hits_allowed",
    "TOTAL_PITCHER_HITS_ALLOWED": "hits_allowed",
    "TOTAL_HITS_ALLOWED": "hits_allowed",
    # Walks
    "PITCHER_WALKS": "walks",
    "TOTAL_PITCHER_WALKS": "walks",
    "WALKS_ALLOWED": "walks",
    "TOTAL_WALKS_ALLOWED": "walks",
}

# ---------------------------------------------------------------------------
# Game-level market mapping (total game hits)
# ---------------------------------------------------------------------------
FD_GAME_MARKET_MAP = {
    "TOTAL_HITS": "game_hits",
    "TOTAL_GAME_HITS": "game_hits",
    "TEAM_TOTAL_HITS": None,  # team-specific, not game total -- skip
}

# Substring keywords for fallback matching per tab
_STRIKEOUT_KEYWORDS = ("STRIKEOUT", "STRIKE_OUT", "K_THROWN", "KS_THROWN")
_OUTS_KEYWORDS = ("OUTS_RECORDED", "PITCHING_OUT", "PITCHER_OUT")


def _match_fd_market_type(market_type, tab=None):
    """Map a FanDuel marketType string to our internal market name."""
    mt = market_type.upper()

    # --- Player prop markets ---
    # Strip PLAYER_X_ prefix (e.g. PLAYER_B_TOTAL_STRIKEOUTS -> TOTAL_STRIKEOUTS)
    for prefix_len in range(len(mt)):
        suffix = mt[prefix_len:]
        for fd_key, internal in FD_MARKET_TYPE_MAP.items():
            if suffix == fd_key:
                return internal

    # --- Game-level markets ---
    for prefix_len in range(len(mt)):
        suffix = mt[prefix_len:]
        for fd_key, internal in FD_GAME_MARKET_MAP.items():
            if suffix == fd_key:
                return internal  # may be None for TEAM_TOTAL_HITS

    # Fallback: if fetching the strikeouts tab, match any market type containing
    # strikeout keywords (catches unexpected FanDuel naming variations)
    if tab == "pitcher-strikeouts":
        for kw in _STRIKEOUT_KEYWORDS:
            if kw in mt:
                return "strikeouts"

    if tab == "pitcher-outs":
        for kw in _OUTS_KEYWORDS:
            if kw in mt:
                return "outs"

    return None


def _props_cache_path(date_key):
    return _PROPS_CACHE_DIR / f"mlb_props_{date_key}.json"


def _game_hits_cache_path(date_key):
    return _PROPS_CACHE_DIR / f"mlb_game_hits_{date_key}.json"


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


def fetch_fanduel_mlb_props(date_key=None):
    """
    Fetch MLB pitcher prop lines + total game hits from FanDuel.

    Parameters
    ----------
    date_key : str or None
        Date in YYYYMMDD format. Auto-detected if None.

    Returns
    -------
    tuple(list[dict], list[dict])
        (pitcher_props, game_hits)
        pitcher_props: [{"player": str, "market": str, "line": float,
                        "over_price": int, "under_price": int,
                        "event_home": str, "event_away": str}, ...]
        game_hits: [{"event_home": str, "event_away": str, "market": "game_hits",
                    "line": float, "over_price": int, "under_price": int}, ...]
    """
    from zoneinfo import ZoneInfo

    if date_key is None:
        now = datetime.datetime.now(ZoneInfo("America/Chicago"))
        date_key = now.strftime("%Y%m%d")

    # Load existing caches for started-game preservation
    cp = _props_cache_path(date_key)
    gh_cp = _game_hits_cache_path(date_key)
    existing_props = _load_cache(cp, max_age_hours=None) or []
    existing_game_hits = _load_cache(gh_cp, max_age_hours=None) or []

    # Step 1: Get MLB events from FanDuel
    mlb_url = f"{FD_BASE}/content-managed-page?page=CUSTOM&customPageId=mlb&_ak={FD_API_KEY}"
    try:
        res = requests.get(mlb_url, headers=FD_HEADERS, timeout=15)
        if res.status_code != 200:
            print(f"  [fanduel] MLB page failed: {res.status_code}")
            return ([], [])
        data = res.json()
    except Exception as e:
        print(f"  [fanduel] MLB page error: {e}")
        return ([], [])

    events = data.get("attachments", {}).get("events", {})

    # Filter to actual games (have " @ " in name) on the requested date
    # FanDuel openDate is UTC -- MLB games typically 1pm-10pm ET
    # So a game on the target date in ET could be that date or next day in UTC
    target_date = f"{date_key[:4]}-{date_key[4:6]}-{date_key[6:8]}"
    from datetime import timedelta
    target_dt = datetime.datetime.strptime(target_date, "%Y-%m-%d")
    next_day = (target_dt + timedelta(days=1)).strftime("%Y-%m-%d")

    game_events = {}
    for eid, ev in events.items():
        if " @ " not in ev.get("name", ""):
            continue
        open_date = ev.get("openDate", "")[:10]  # "2026-04-13"
        if open_date == target_date or open_date == next_day:
            game_events[eid] = ev

    print(f"  [fanduel] Found {len(game_events)} MLB games for {target_date}")

    if not game_events:
        return (existing_props, existing_game_hits)

    # Step 2: For each game, fetch prop tabs
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    new_props = []
    new_game_hits = []
    started_games = set()  # track which games have started (preserve from cache)

    for event_id, ev in game_events.items():
        event_name = ev.get("name", "")
        # Parse "Chicago Cubs (R Martin) @ Philadelphia Phillies (A Nola)"
        # Strip pitcher names in parentheses before splitting
        import re
        clean_name = re.sub(r'\s*\([^)]*\)', '', event_name)
        parts = clean_name.split(" @ ")
        if len(parts) == 2:
            away_team = parts[0].strip()
            home_team = parts[1].strip()
        else:
            away_team = clean_name
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

        home_abbr = MLB_TEAM_NAME_TO_ABBR.get(home_team, home_team)
        away_abbr = MLB_TEAM_NAME_TO_ABBR.get(away_team, away_team)

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

                # Get line: prefer handicap field, fall back to parsing runner name
                # FanDuel K markets: handicap=5.5 on runner
                # FanDuel outs markets: handicap=0, line embedded in name ("Over 17.5")
                line = over_runner.get("handicap")
                if line is None or line == 0:
                    # Try parsing from runner name: "Over 17.5" -> 17.5
                    import re as _re
                    m = _re.search(r'(\d+\.?\d*)', over_runner.get("runnerName", ""))
                    if m:
                        line = float(m.group(1))
                    else:
                        continue
                if line is None or line == 0:
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

                # Game-level market (total game hits) vs player prop
                if internal_market == "game_hits":
                    new_game_hits.append({
                        "event_home": home_abbr,
                        "event_away": away_abbr,
                        "market": "game_hits",
                        "line": float(line),
                        "over_price": over_price,
                        "under_price": under_price,
                        "source": "fanduel",
                    })
                else:
                    # Player name: try runner first ("Corbin Burnes Over"),
                    # fall back to marketName ("Aaron Nola Outs Recorded")
                    runner_name = over_runner.get("runnerName", "")
                    if runner_name.startswith("Over"):
                        # Runner has no player name (e.g. "Over 17.5") — use marketName
                        market_name = mv.get("marketName", "")
                        # Strip suffixes like " - Strikeouts", " Outs Recorded", " - Hits Allowed"
                        import re as _re2
                        player_name = _re2.sub(r'\s*[-–]\s*(Strikeouts|Hits Allowed|Walks|Alt \w+).*', '', market_name)
                        player_name = _re2.sub(r'\s*(Outs Recorded|Strikeouts|Hits Allowed|Walks).*', '', player_name).strip()
                    else:
                        player_name = runner_name.replace(" Over", "").strip()
                    if not player_name:
                        continue
                    new_props.append({
                        "player": player_name,
                        "market": internal_market,
                        "line": float(line),
                        "over_price": over_price,
                        "under_price": under_price,
                        "event_home": home_abbr,
                        "event_away": away_abbr,
                        "source": "fanduel",
                    })

            time.sleep(0.2)  # Rate limit between tab requests

    # Merge: keep cached props ONLY for started/finished games, fresh for everything else
    def _game_key_from_prop(p):
        return f"{p.get('event_away', '')} @ {p.get('event_home', '')}"

    started_abbr_keys = set()
    for sg in started_games:
        sg_parts = sg.split(" @ ")
        if len(sg_parts) == 2:
            a = MLB_TEAM_NAME_TO_ABBR.get(sg_parts[0], sg_parts[0])
            h = MLB_TEAM_NAME_TO_ABBR.get(sg_parts[1], sg_parts[1])
            started_abbr_keys.add(f"{a} @ {h}")

    kept_props = [p for p in existing_props
                  if _game_key_from_prop(p) in started_abbr_keys]
    kept_game_hits = [g for g in existing_game_hits
                      if _game_key_from_prop(g) in started_abbr_keys]

    all_props = kept_props + new_props
    all_game_hits = kept_game_hits + new_game_hits

    if started_games:
        print(f"  [fanduel] Preserved {len(kept_props)} cached prop lines "
              f"+ {len(kept_game_hits)} game-hit lines for {len(started_games)} started games")
    print(f"  [fanduel] Fetched {len(new_props)} prop lines + {len(new_game_hits)} game-hit lines, "
          f"totals: {len(all_props)} props / {len(all_game_hits)} game hits")

    # Save
    if all_props:
        _save_cache(all_props, cp)
    if all_game_hits:
        _save_cache(all_game_hits, gh_cp)

    return (all_props, all_game_hits)
