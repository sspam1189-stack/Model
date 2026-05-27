# scripts/sources/odds_theoddsapi.py
# Fetch NBA spreads + totals from The Odds API and return in bot format.
# Requires env var: ODDS_API_KEY

import os
import re
import math
import json
import requests
import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

_dir = os.path.dirname(os.path.abspath(__file__))
ODDS_CACHE_DIR = os.path.join(_dir, "..", "..", "..", "data", "odds_cache", "nba")

from .odds_theoddsapi_historical import fetch_closing_odds_for_game
from .espn_scoreboard import fetch_scoreboard

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


def _to_model_line(home_team, away_team, spread_points, team_for_spread):
    """
    Convert The Odds API spread into sportsbook convention (home perspective):
    -X = home favored by X, +X = away favored by X
    The Odds API already uses sportsbook convention for the named team.
    """
    if not isinstance(spread_points, (int, float)) or not math.isfinite(spread_points):
        return None

    is_home = team_for_spread == home_team
    is_away = team_for_spread == away_team

    if not is_home and not is_away:
        return None

    # If spread is for home team, return directly; if away, negate.
    if is_home:
        return spread_points
    return -spread_points


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


def fetch_todays_odds():
    keys = _get_odds_api_keys()
    last_err = None
    for idx, api_key in enumerate(keys):
        url = (
            f"{BASE}/sports/basketball_nba/odds?"
            f"apiKey={quote(api_key)}"
            f"&regions=us"
            f"&markets=spreads,totals"
            f"&oddsFormat=american"
        )
        res = requests.get(url, timeout=30)
        if res.status_code in (401, 429):
            print(f"  [nba_odds] Key {idx+1}/{len(keys)} HTTP {res.status_code} — rotating")
            last_err = f"HTTP {res.status_code}"
            continue
        if res.status_code != 200:
            last_err = f"HTTP {res.status_code} {res.reason} {res.text[:200]}"
            continue
        data = res.json()
        if not data:
            print(f"  [nba_odds] Key {idx+1}/{len(keys)} returned empty — rotating")
            last_err = "empty response"
            continue
        break
    else:
        raise Exception(f"TheOddsAPI failed (all {len(keys)} keys): {last_err}")
    today = _today_iso_chicago()

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

                # Game already started — skip fetch, will backfill from cache
                if commence <= now:
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
            "startTimeUTC": commence_str,
            "_book": book.get("title") if book else None,
        })

    # Load existing cache
    date_key = today.replace("-", "")
    cache_path = os.path.join(ODDS_CACHE_DIR, date_key + ".json")
    existing = {}
    try:
        if os.path.exists(cache_path):
            with open(cache_path, "r") as f:
                existing = json.load(f)
    except Exception:
        pass

    # Write fresh pre-game odds to cache
    if games:
        try:
            os.makedirs(ODDS_CACHE_DIR, exist_ok=True)
            for g in games:
                key = f"{g['away']}@{g['home']}"
                existing[key] = {"line": g["line"], "total": g["total"], "_book": g["_book"], "_note": "live fetch"}
            with open(cache_path, "w") as f:
                json.dump(existing, f, indent=2)
            print(f"  [odds] Cached {len(games)} games to {date_key}.json")
        except Exception:
            pass

    # Backfill started/finished games from cache (so they still appear in output)
    fresh_keys = {f"{g['away']}@{g['home']}" for g in games}
    for key, val in existing.items():
        if key not in fresh_keys and val.get("line") is not None:
            parts = key.split("@", 1)
            if len(parts) == 2:
                games.append({"away": parts[0], "home": parts[1], "line": val["line"],
                              "total": val.get("total"), "_book": val.get("_book")})
                print(f"  [odds] Backfilled started/finished game from cache: {key}")

    return games
