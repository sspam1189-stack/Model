import requests
import os
import math
import re
from datetime import datetime
from urllib.parse import quote
import pytz

from .odds_theoddsapi_historical import fetch_closing_odds_for_game
from .espn_scoreboard import fetch_scoreboard

_dir = os.path.dirname(os.path.abspath(__file__))
# Bump the cache namespace so runners stop reusing odds captured under the
# previous line-sign convention.
DAILY_ODDS_CACHE_DIR = os.path.join(_dir, "..", "..", "data", "odds_cache_live_v2")
BASE = "https://api.the-odds-api.com/v4"


def _extract_all_espn_games(scoreboard_json):
    out = []
    for ev in (scoreboard_json or {}).get("events", []):
        comp = ((ev.get("competitions") or [None])[0])
        if not comp:
            continue
        competitors = comp.get("competitors", [])
        away_c = next((c for c in competitors if c.get("homeAway") == "away"), None)
        home_c = next((c for c in competitors if c.get("homeAway") == "home"), None)
        if not away_c or not home_c:
            continue
        away = (away_c.get("team") or {}).get("displayName")
        home = (home_c.get("team") or {}).get("displayName")
        commence_time_iso = comp.get("date") or ev.get("date")
        status_name = ((comp.get("status") or {}).get("type") or {}).get("name", "")
        is_final = "FINAL" in status_name
        is_in_progress = status_name in ("STATUS_IN_PROGRESS", "STATUS_HALFTIME")
        if not away or not home:
            continue
        out.append({
            "away": away,
            "home": home,
            "commenceTimeIso": commence_time_iso,
            "isFinal": is_final,
            "isInProgress": is_in_progress,
        })
    return out


def norm_team(name):
    return re.sub(r"\s+", " ", str(name or "").strip())


def today_iso_chicago():
    tz = pytz.timezone("America/Chicago")
    now = datetime.now(tz)
    return now.strftime("%Y-%m-%d")


def today_key_chicago():
    return today_iso_chicago().replace("-", "")


def daily_cache_path(date_key):
    os.makedirs(DAILY_ODDS_CACHE_DIR, exist_ok=True)
    return os.path.join(DAILY_ODDS_CACHE_DIR, f"{date_key}.json")


def to_model_line(home_team, away_team, spread_points, team_for_spread):
    """
    -X means HOME favored by X, +X means AWAY favored by X
    """
    if not math.isfinite(spread_points):
        return None

    is_home = team_for_spread == home_team
    is_away = team_for_spread == away_team

    if not is_home and not is_away:
        return None

    if is_home:
        return spread_points
    return -spread_points


def pick_best_bookmaker(bookmakers):
    if not isinstance(bookmakers, list) or len(bookmakers) == 0:
        return None

    preferred = ["DraftKings", "FanDuel", "BetMGM", "Caesars", "PointsBet", "BetRivers"]
    for p in preferred:
        b = next((x for x in bookmakers if (x or {}).get("title") == p), None)
        if b:
            return b
    return bookmakers[0]


def find_market(bookmaker, key):
    if not bookmaker or not bookmaker.get("markets"):
        return None
    return next((m for m in bookmaker["markets"] if (m or {}).get("key") == key), None)


def fetch_todays_odds():
    today = today_iso_chicago()
    cache_path = daily_cache_path(today_key_chicago())
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            cached = json.load(f)
        print(f"  [odds] Using cached daily odds for {today} ({len(cached)} games)")
        return cached

    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        raise Exception("Missing ODDS_API_KEY env var (The Odds API key).")

    url = (
        f"{BASE}/sports/basketball_nba/odds?"
        f"apiKey={quote(api_key)}"
        f"&regions=us"
        f"&markets=spreads,totals"
        f"&oddsFormat=american"
    )

    res = requests.get(url)
    if res.status_code != 200:
        txt = res.text
        raise Exception(f"TheOddsAPI failed: {res.status_code} {res.reason} {txt}")

    data = res.json()
    games = []
    now = datetime.now(pytz.UTC)

    for ev in data:
        home = norm_team((ev or {}).get("home_team"))
        away = norm_team((ev or {}).get("away_team"))
        if not home or not away:
            continue

        # Filter to today (Chicago date)
        commence_str = (ev or {}).get("commence_time")
        if commence_str:
            try:
                commence = datetime.fromisoformat(commence_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                commence = None

            if commence:
                tz = pytz.timezone("America/Chicago")
                d = commence.astimezone(tz).strftime("%Y-%m-%d")
                if d != today:
                    continue

                # Game already started -- skip (run_daily preserves from previous run)
                if commence <= now:
                    print(f"  [odds] Game already started: {away} @ {home} -- skipping")
                    continue

        book = pick_best_bookmaker((ev or {}).get("bookmakers"))

        line = None
        total = None

        spreads = find_market(book, "spreads") if book else None
        totals = find_market(book, "totals") if book else None

        if spreads and spreads.get("outcomes"):
            out = next(
                (o for o in spreads["outcomes"] if math.isfinite(float((o or {}).get("point", "nan")))),
                None,
            )
            if out:
                team_for_spread = norm_team(out.get("name"))
                pts = float(out["point"])
                line = to_model_line(home, away, pts, team_for_spread)

        if totals and totals.get("outcomes"):
            out = next(
                (o for o in totals["outcomes"] if math.isfinite(float((o or {}).get("point", "nan")))),
                None,
            )
            if out:
                total = float(out["point"])

        games.append({
            "away": away,
            "home": home,
            "line": line,
            "total": total,
            "_book": (book or {}).get("title"),
        })

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(games, f, indent=2)

    return games
