# scripts/sources/odds_theoddsapi.py
# Fetch NBA spreads + totals from The Odds API and return in bot format.
# Requires env var: ODDS_API_KEY

import os
import re
import math
import requests
import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .odds_theoddsapi_historical import fetch_closing_odds_for_game
from .espn_scoreboard import fetch_scoreboard

_dir = os.path.dirname(os.path.abspath(__file__))
DAILY_ODDS_CACHE_DIR = os.path.join(_dir, "..", "..", "data", "odds_cache_live")
BASE = "https://api.the-odds-api.com/v4"


def _extract_all_espn_games(scoreboard_json):
    out = []
    for ev in (scoreboard_json or {}).get("events", []):
        comp = (ev.get("competitions") or [None])[0]
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


def _norm_team(name):
    return re.sub(r"\s+", " ", str(name or "")).strip()


def _today_iso_chicago():
    """Date-only in America/Chicago, formatted YYYY-MM-DD."""
    now = datetime.datetime.now(ZoneInfo("America/Chicago"))
    return now.strftime("%Y-%m-%d")


def _today_key_chicago():
    return _today_iso_chicago().replace("-", "")


def _daily_cache_path(date_key):
    os.makedirs(DAILY_ODDS_CACHE_DIR, exist_ok=True)
    return os.path.join(DAILY_ODDS_CACHE_DIR, f"{date_key}.json")


def _to_model_line(home_team, away_team, spread_points, team_for_spread):
    """
    Convert The Odds API spread into model convention:
    +X means HOME favored by X, -X means AWAY favored by X
    """
    if not isinstance(spread_points, (int, float)) or not math.isfinite(spread_points):
        return None

    abs_val = abs(spread_points)
    is_home = team_for_spread == home_team
    is_away = team_for_spread == away_team

    if not is_home and not is_away:
        return None

    if is_home:
        return abs_val if spread_points < 0 else -abs_val
    return -abs_val if spread_points < 0 else abs_val


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


def fetch_todays_odds():
    today = _today_iso_chicago()
    cache_path = _daily_cache_path(_today_key_chicago())
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

    res = requests.get(url, timeout=30)
    if res.status_code != 200:
        txt = res.text
        raise Exception(f"TheOddsAPI failed: {res.status_code} {res.reason} {txt}")

    data = res.json()
    games = []
    now = datetime.datetime.now(datetime.timezone.utc)

    for ev in data:
        home = _norm_team(ev.get("home_team"))
        away = _norm_team(ev.get("away_team"))
        if not home or not away:
            continue

        # Filter to today (Chicago date)
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

                # Game already started -- skip (run_daily preserves from previous run)
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
            "_book": book.get("title") if book else None,
        })

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(games, f, indent=2)

    return games
