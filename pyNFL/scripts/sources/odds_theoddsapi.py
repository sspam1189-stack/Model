# pyNFL/scripts/sources/odds_theoddsapi.py
# Fetch NFL spreads + totals from The Odds API and return in bot format.
# Requires env var: ODDS_API_KEY

import os
import re
import json
import math
import time
import requests
import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

BASE = "https://api.the-odds-api.com/v4"
SPORT_KEY = "americanfootball_nfl"

# --------------- disk cache helpers ---------------

_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "odds"


def _cache_path(season, week):
    """Return the cache file path for a given season/week."""
    return _CACHE_DIR / f"nfl_odds_{season}_W{week}.json"


def _save_cache(data, path):
    """Write *data* as JSON to *path*, creating directories as needed."""
    try:
        os.makedirs(path.parent, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as exc:
        print(f"  [odds-cache] WARNING: could not write cache {path}: {exc}")


def _load_cache(path, max_age_hours=None):
    """
    Load JSON from *path* if it exists and is fresh enough.

    Args:
        path: Path to cache file.
        max_age_hours: Maximum age in hours.  ``None`` means never expire
                       (use for historical / backfill data that won't change).

    Returns:
        Parsed JSON data, or ``None`` if cache miss / stale / unreadable.
    """
    try:
        if not path.exists():
            return None
        if max_age_hours is not None:
            age_s = time.time() - path.stat().st_mtime
            if age_s > max_age_hours * 3600:
                return None
        with open(path) as f:
            return json.load(f)
    except Exception as exc:
        print(f"  [odds-cache] WARNING: could not read cache {path}: {exc}")
        return None


def clear_cache():
    """Remove all cached odds files from disk."""
    try:
        if _CACHE_DIR.exists():
            for f in _CACHE_DIR.glob("nfl_odds_*.json"):
                f.unlink(missing_ok=True)
            print(f"  [odds-cache] Cleared cache directory {_CACHE_DIR}")
    except Exception as exc:
        print(f"  [odds-cache] WARNING: could not clear cache: {exc}")


def _norm_team(name):
    return re.sub(r"\s+", " ", str(name or "")).strip()


def _norm_key(s):
    s = str(s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _today_iso_chicago():
    """Date-only in America/Chicago, formatted YYYY-MM-DD."""
    now = datetime.datetime.now(ZoneInfo("America/Chicago"))
    return now.strftime("%Y-%m-%d")


def _to_model_line(home_team, away_team, spread_points, team_for_spread):
    """
    Convert The Odds API spread into standard betting convention from home
    perspective: -X means home favored by X, +X means away favored by X.

    The Odds API returns ``point: -4.5`` for the team listed in the outcome,
    meaning that team is giving 4.5 points (i.e. the favorite).
    """
    if not isinstance(spread_points, (int, float)) or not math.isfinite(spread_points):
        return None

    is_home = team_for_spread == home_team
    is_away = team_for_spread == away_team

    if not is_home and not is_away:
        return None

    if is_home:
        return spread_points  # home team's spread directly
    return -spread_points  # flip sign since we store from home perspective


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


def _fetch_with_retry(url, tries=5):
    wait = 0.7
    for i in range(tries):
        res = requests.get(url, timeout=30)
        if res.status_code != 429:
            return res
        time.sleep(wait)
        wait = min(wait * 2, 8.0)
    return requests.get(url, timeout=30)


# -- NFL team aliases for matching between ESPN and The Odds API --

TEAM_ALIASES = {
    "new york giants": ["new york giants", "ny giants"],
    "new york jets": ["new york jets", "ny jets"],
    "los angeles rams": ["los angeles rams", "la rams"],
    "los angeles chargers": ["los angeles chargers", "la chargers"],
    "san francisco": ["san francisco 49ers", "san francisco"],
    "kansas city": ["kansas city chiefs", "kansas city"],
    "green bay": ["green bay packers", "green bay"],
    "new england": ["new england patriots", "new england"],
    "new orleans": ["new orleans saints", "new orleans"],
    "tampa bay": ["tampa bay buccaneers", "tampa bay"],
    "jacksonville": ["jacksonville jaguars", "jacksonville"],
}


def _expand_aliases(name):
    k = _norm_key(name)
    return TEAM_ALIASES.get(k, [k])


# -- Live odds fetch --

def fetch_nfl_odds(api_key=None, season=None, week=None):
    """
    Fetch current NFL odds (spreads + totals) from The Odds API.
    Returns list of dicts: [{ away, home, line, total, _book }]
    Line convention: -X = home favored, +X = away favored (standard betting).

    If *season* and *week* are provided the result is cached to disk
    (< 2 hours for live odds).
    """
    # --- try cache first (live odds: 2-hour freshness) ---
    if season and week:
        cp = _cache_path(season, week)
        cached = _load_cache(cp, max_age_hours=2)
        if cached is not None:
            print(f"  [odds] Using cached live odds for {season} W{week} ({cp.name})")
            return cached

    api_key = api_key or os.environ.get("ODDS_API_KEY")
    if not api_key:
        raise Exception("Missing ODDS_API_KEY env var (The Odds API key).")

    url = (
        f"{BASE}/sports/{SPORT_KEY}/odds?"
        f"apiKey={quote(api_key)}"
        f"&regions=us"
        f"&markets=spreads,totals"
        f"&oddsFormat=american"
    )

    res = requests.get(url, timeout=30)
    if res.status_code != 200:
        txt = res.text
        raise Exception(f"TheOddsAPI failed: {res.status_code} {res.reason} {txt}")

    data = res.json()
    today = _today_iso_chicago()

    games = []
    now = datetime.datetime.now(datetime.timezone.utc)

    for ev in data:
        home = _norm_team(ev.get("home_team"))
        away = _norm_team(ev.get("away_team"))
        if not home or not away:
            continue

        # Filter to today (Chicago date) — but NFL games span Thu/Sun/Mon,
        # so we include all upcoming games (no date filter for NFL)
        commence_str = ev.get("commence_time")
        commence = None
        if commence_str:
            try:
                commence = datetime.datetime.fromisoformat(commence_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                commence = None

            if commence:
                # Skip games already started
                if commence <= now:
                    print(f"  [odds] Game already started: {away} @ {home} -- skipping")
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

    # --- save to cache ---
    if season and week:
        _save_cache(games, _cache_path(season, week))

    return games


# -- Historical odds (for backfill) --

def _to_historical_iso(iso_like, offset_minutes=0):
    """Convert ISO string to historical API format, with optional minute offset."""
    try:
        dt = datetime.datetime.fromisoformat(iso_like.replace("Z", "+00:00"))
        dt = dt + datetime.timedelta(minutes=offset_minutes)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, AttributeError):
        return None


def _fetch_snapshot(api_key, ts):
    url = (
        f"{BASE}/historical/sports/{SPORT_KEY}/odds?"
        f"apiKey={quote(api_key)}"
        f"&regions=us"
        f"&markets=spreads,totals"
        f"&oddsFormat=american"
        f"&date={quote(ts)}"
    )

    res = _fetch_with_retry(url)
    if res.status_code != 200:
        txt = res.text
        raise Exception(f"Historical odds fetch failed: {res.status_code} {res.reason} {txt}")

    json_data = res.json()
    return json_data.get("data", []) if isinstance(json_data.get("data"), list) else []


def _extract_odds(data, home, away):
    home_aliases = _expand_aliases(home)
    away_aliases = _expand_aliases(away)

    # Exact match against any alias
    ev = next(
        (e for e in data
         if _norm_key(e.get("home_team")) in home_aliases
         and _norm_key(e.get("away_team")) in away_aliases),
        None,
    )

    # Partial includes fallback
    if not ev:
        ev = next(
            (e for e in data
             if any(ha in _norm_key(e.get("home_team")) or _norm_key(e.get("home_team")) in ha for ha in home_aliases)
             and any(aa in _norm_key(e.get("away_team")) or _norm_key(e.get("away_team")) in aa for aa in away_aliases)),
            None,
        )

    if not ev:
        return None

    home_team = _norm_team(ev.get("home_team"))
    away_team = _norm_team(ev.get("away_team"))
    book = _pick_best_bookmaker(ev.get("bookmakers"))

    line = None
    total = None

    spreads = _find_market(book, "spreads") if book else None
    totals = _find_market(book, "totals") if book else None

    if spreads and spreads.get("outcomes"):
        out = next((o for o in spreads["outcomes"] if isinstance(o.get("point"), (int, float)) and math.isfinite(float(o["point"]))), None)
        if out:
            line = _to_model_line(home_team, away_team, float(out["point"]), _norm_team(out.get("name")))

    if totals and totals.get("outcomes"):
        out = next((o for o in totals["outcomes"] if isinstance(o.get("point"), (int, float)) and math.isfinite(float(o["point"]))), None)
        if out:
            total = float(out["point"])

    return {"line": line, "total": total, "_book": book.get("title") if book else None}


def fetch_closing_odds_for_game(home, away, commence_time_iso):
    """
    Fetch historical odds snapshot near kickoff and extract spread/total.
    Pass commence_time_iso from ESPN (UTC ISO string).
    """
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        raise Exception("Missing ODDS_API_KEY env var.")

    # Try snapshots at -90min, -30min, and -10min before kickoff.
    offsets = [-90, -30, -10]

    for offset in offsets:
        ts = _to_historical_iso(commence_time_iso, offset)
        if not ts:
            return {"line": None, "total": None, "_book": None, "_note": "Bad commenceTimeIso"}

        data = _fetch_snapshot(api_key, ts)
        result = _extract_odds(data, home, away)

        if result and (isinstance(result.get("line"), (int, float)) or isinstance(result.get("total"), (int, float))):
            return {**result, "_note": f"snapshot at {offset}min before kickoff"}

    return {"line": None, "total": None, "_book": None, "_note": "No odds found in pre-kickoff snapshots"}


def fetch_historical_odds(api_key=None, season=None, week=None):
    """
    Fetch historical closing odds for a given NFL season/week.
    Uses The Odds API historical endpoint with a timestamp near Sunday 1pm ET
    of the given week.

    Results are cached permanently (historical data never changes).

    Returns list of dicts: [{ away, home, line, total, _book }]
    """
    # --- try cache first (historical: never expires) ---
    if season and week:
        cp = _cache_path(season, week)
        cached = _load_cache(cp, max_age_hours=None)
        if cached is not None:
            print(f"  [odds] Using cached historical odds for {season} W{week} ({cp.name})")
            return cached

    api_key = api_key or os.environ.get("ODDS_API_KEY")
    if not api_key:
        raise Exception("Missing ODDS_API_KEY env var.")

    if not season or not week:
        raise Exception("season and week are required for historical odds fetch.")

    # Compute target dates for this NFL week.
    # NFL games occur on Thursday, Saturday (late season/playoffs), Sunday, and Monday.
    # We take multiple snapshots to capture all game windows.

    year = int(season)
    wk = int(week)

    # Find first Monday in September (Labor Day)
    sept1 = datetime.date(year, 9, 1)
    days_to_monday = (7 - sept1.weekday()) % 7
    if sept1.weekday() == 0:
        days_to_monday = 0
    labor_day = sept1 + datetime.timedelta(days=days_to_monday)

    # Week 1 Sunday is the day after the Thursday following Labor Day = Labor Day + 6
    week1_sunday = labor_day + datetime.timedelta(days=6)

    if wk <= 18:
        # Regular season: straightforward week offset
        target_sunday = week1_sunday + datetime.timedelta(weeks=wk - 1)
        target_thursday = target_sunday - datetime.timedelta(days=3)
        target_saturday = target_sunday - datetime.timedelta(days=1)

        # Take snapshots at multiple windows to capture TNF, Saturday, and Sunday games
        snapshots = [
            (target_thursday, "12:00:00", "Thu"),   # Thursday Night Football
            (target_saturday, "12:00:00", "Sat"),   # Saturday games (Weeks 15-18)
            (target_sunday, "12:00:00", "Sun"),     # Main Sunday slate
        ]
    else:
        # Playoff weeks (19-22):
        # Week 19 = Wild Card (1 week after Week 18)
        # Week 20 = Divisional (2 weeks after Week 18)
        # Week 21 = Conference Championship (3 weeks after Week 18)
        # Week 22 = Super Bowl (5 weeks after Week 18 -- extra bye week)
        week18_sunday = week1_sunday + datetime.timedelta(weeks=17)

        if wk == 22:
            # Super Bowl: 5 weeks after Week 18 (bye week between Conf Champ and SB)
            playoff_offset_weeks = 5
        else:
            # Wild Card=1, Divisional=2, Conference=3
            playoff_offset_weeks = wk - 18

        target_sunday = week18_sunday + datetime.timedelta(weeks=playoff_offset_weeks)
        target_saturday = target_sunday - datetime.timedelta(days=1)

        # Playoff games are primarily Saturday and Sunday (no Thursday games)
        snapshots = [
            (target_saturday, "12:00:00", "Sat"),   # Saturday playoff games
            (target_sunday, "12:00:00", "Sun"),      # Sunday playoff games / Super Bowl
        ]

    # Deduplicate games across all snapshots (same matchup from different windows)
    seen = set()
    all_games = []

    for snap_date, snap_time, label in snapshots:
        ts = f"{snap_date.isoformat()}T{snap_time}-05:00"
        ts_utc = _to_historical_iso(ts)
        if not ts_utc:
            continue

        try:
            data = _fetch_snapshot(api_key, ts_utc)
        except Exception as e:
            continue

        if not data:
            continue

        count_before = len(all_games)
        for ev in data:
            home = _norm_team(ev.get("home_team"))
            away = _norm_team(ev.get("away_team"))
            if not home or not away:
                continue
            key = (away.lower(), home.lower())
            if key in seen:
                continue
            seen.add(key)
            all_games.append(ev)

        new = len(all_games) - count_before
        if new > 0:
            print(f"  [odds] {label} snapshot ({ts_utc}): +{new} new games")

    print(f"  [odds] Total historical odds for {season} Week {week}: {len(all_games)} games")

    games = []
    for ev in all_games:
        home = _norm_team(ev.get("home_team"))
        away = _norm_team(ev.get("away_team"))
        if not home or not away:
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
            "commenceTimeIso": ev.get("commence_time"),
            "_book": book.get("title") if book else None,
        })

    print(f"  [odds] Got historical odds for {len(games)} games")

    # --- save to cache (permanent for historical) ---
    if season and week and games:
        _save_cache(games, _cache_path(season, week))

    return games
