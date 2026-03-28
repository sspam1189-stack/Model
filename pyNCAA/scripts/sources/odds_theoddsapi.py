# scripts/sources/odds_theoddsapi.py
# Fetch NCAA basketball spreads + totals from The Odds API.
# For games already started, fetches historical odds from 1.5 hours before tip.
# Requires env var: ODDS_API_KEY

import os
import math
import re
import requests
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

from .espn_scoreboard import fetch_scoreboard

BASE = "https://api.the-odds-api.com/v4"
HISTORICAL_OFFSET_MS = 90 * 60 * 1000  # 1.5 hours before tip-off
HISTORICAL_OFFSET_SEC = 90 * 60


def extract_all_espn_games(scoreboard_json):
    out = []
    for ev in (scoreboard_json.get("events") or []):
        comp = (ev.get("competitions") or [None])[0]
        if not comp:
            continue
        competitors = comp.get("competitors") or []
        away_c = next((c for c in competitors if c.get("homeAway") == "away"), None)
        home_c = next((c for c in competitors if c.get("homeAway") == "home"), None)
        if not away_c or not home_c:
            continue
        away = (away_c.get("team") or {}).get("displayName")
        home = (home_c.get("team") or {}).get("displayName")
        commence_time_iso = comp.get("date") or ev.get("date")
        status_name = (comp.get("status") or {}).get("type", {}).get("name", "")
        is_final = "FINAL" in status_name
        is_in_progress = status_name == "STATUS_IN_PROGRESS" or status_name == "STATUS_HALFTIME"
        if not away or not home:
            continue
        out.append({
            "away": away,
            "home": home,
            "commenceTimeIso": commence_time_iso,
            "isFinal": is_final,
            "isInProgress": is_in_progress
        })
    return out


def norm_team(name):
    return re.sub(r'\s+', ' ', str(name or "").strip())


def today_iso_chicago():
    """Get today's date in YYYY-MM-DD format in Chicago timezone."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/Chicago"))
    return now.strftime("%Y-%m-%d")


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


# -- Extract odds from a single event object --
def extract_odds_from_event(ev):
    home = norm_team((ev or {}).get("home_team"))
    away = norm_team((ev or {}).get("away_team"))
    if not home or not away:
        return None

    book = pick_best_bookmaker((ev or {}).get("bookmakers"))
    line = None
    total = None

    spreads = find_market(book, "spreads") if book else None
    totals = find_market(book, "totals") if book else None

    if spreads and spreads.get("outcomes"):
        out = next((o for o in spreads["outcomes"] if _is_finite_point(o)), None)
        if out:
            team_for_spread = norm_team(out.get("name"))
            pts = float(out["point"])
            line = to_model_line(home, away, pts, team_for_spread)

    if totals and totals.get("outcomes"):
        out = next((o for o in totals["outcomes"] if _is_finite_point(o)), None)
        if out:
            total = float(out["point"])

    return {"away": away, "home": home, "line": line, "total": total, "_book": (book or {}).get("title")}


def _is_finite_point(o):
    try:
        return math.isfinite(float(o.get("point")))
    except (TypeError, ValueError):
        return False


# -- Fetch historical odds for a single started game --
# Hits the historical odds endpoint at 1.5 hours before tip-off.
def fetch_historical_odds_for_game(api_key, event_id, commence):
    snapshot_date = commence - timedelta(seconds=HISTORICAL_OFFSET_SEC)
    date_param = snapshot_date.strftime("%Y-%m-%dT%H:%M:%SZ")

    url = (
        f"{BASE}/historical/sports/basketball_ncaab/events/{event_id}/odds?"
        f"apiKey={quote(api_key)}"
        f"&regions=us"
        f"&markets=spreads,totals"
        f"&oddsFormat=american"
        f"&date={quote(date_param)}"
    )

    try:
        res = requests.get(url)
        if not res.ok:
            txt = ""
            try:
                txt = res.text
            except Exception:
                pass
            print(f"  [odds] Historical fetch failed for {event_id}: {res.status_code} {txt[:100]}")
            return None
        json_data = res.json()
        # Historical response wraps the event data in a "data" field
        return json_data.get("data") or json_data
    except Exception as err:
        print(f"  [odds] Historical fetch error for {event_id}: {err}")
        return None


def _format_chicago_time(dt):
    """Format a datetime to a human-readable time in Chicago timezone."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo
    ct = dt.astimezone(ZoneInfo("America/Chicago"))
    return ct.strftime("%-I:%M %p") if os.name != "nt" else ct.strftime("%#I:%M %p")


def fetch_todays_odds():
    api_key = os.environ.get("ODDS_API_KEY", "6c5699682d30fc8664737160274f8d12")

    url = (
        f"{BASE}/sports/basketball_ncaab/odds?"
        f"apiKey={quote(api_key)}"
        f"&regions=us"
        f"&markets=spreads,totals"
        f"&oddsFormat=american"
    )

    res = requests.get(url)
    if not res.ok:
        txt = ""
        try:
            txt = res.text
        except Exception:
            pass
        raise Exception(f"TheOddsAPI failed: {res.status_code} {res.reason} {txt}")

    data = res.json()
    today = today_iso_chicago()
    games = []
    now = datetime.now(timezone.utc)

    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo
    chicago_tz = ZoneInfo("America/Chicago")

    for ev in data:
        home = norm_team((ev or {}).get("home_team"))
        away = norm_team((ev or {}).get("away_team"))
        if not home or not away:
            continue

        commence_str = (ev or {}).get("commence_time")
        commence = None
        if commence_str:
            try:
                commence = datetime.fromisoformat(commence_str.replace("Z", "+00:00"))
            except Exception:
                pass

        if commence:
            d = commence.astimezone(chicago_tz).strftime("%Y-%m-%d")
            if d != today:
                continue

            # Game already started — skip fetch, will backfill from cache
            if commence <= now:
                continue

        game = extract_odds_from_event(ev)
        if game:
            games.append(game)

    # Backfill started/finished games from cache
    date_key = today.replace("-", "")
    cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "data", "odds_cache", "ncaab")
    cache_path = os.path.join(cache_dir, date_key + ".json")
    existing = {}
    try:
        if os.path.exists(cache_path):
            with open(cache_path, "r") as f:
                existing = json.load(f)
    except Exception:
        pass

    fresh_keys = {f"{g['away']}@{g['home']}" for g in games}
    for key, val in existing.items():
        if key not in fresh_keys and val.get("line") is not None:
            parts = key.split("@", 1)
            if len(parts) == 2:
                games.append({"away": parts[0], "home": parts[1], "line": val["line"],
                              "total": val.get("total"), "_book": val.get("_book")})
                print(f"  [odds] Backfilled started/finished game from cache: {key}")

    print(f"  [odds] Got {len(games)} NCAAB games with odds for today")
    return games
