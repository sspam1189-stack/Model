import requests
import time
import math
import os
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

BASE = "https://api.the-odds-api.com/v4"


def norm_team(name):
    return re.sub(r"\s+", " ", str(name or "").strip())


def norm_key(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", str(s or "").lower())).strip()


def to_historical_iso(iso_like, offset_minutes=0):
    try:
        d = datetime.fromisoformat(iso_like.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    d = d + timedelta(minutes=offset_minutes)
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def to_model_line(home_team, away_team, spread_points, team_for_spread):
    """
    Sportsbook convention (home perspective):
    -X = home favored by X, +X = away favored by X
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


def pick_bookmaker(bookmakers):
    if not isinstance(bookmakers, list) or len(bookmakers) == 0:
        return None

    preferred = ["DraftKings", "FanDuel", "BetMGM", "Caesars", "PointsBet", "BetRivers"]
    for title in preferred:
        found = next((b for b in bookmakers if (b or {}).get("title") == title), None)
        if found:
            return found
    return bookmakers[0]


def find_market(bookmaker, key):
    if not bookmaker or not bookmaker.get("markets"):
        return None
    return next((m for m in bookmaker["markets"] if (m or {}).get("key") == key), None)


def _sleep(ms):
    time.sleep(ms / 1000.0)


def fetch_with_retry(url, tries=5):
    wait = 700
    for i in range(tries):
        res = requests.get(url)
        if res.status_code != 429:
            return res
        _sleep(wait)
        wait = min(wait * 2, 8000)
    return requests.get(url)


# Maps Hollinger short names -> possible odds API full names
TEAM_ALIASES = {
    "la lakers": ["los angeles lakers", "la lakers"],
    "la clippers": ["los angeles clippers", "la clippers"],
    "golden state": ["golden state warriors", "golden state"],
    "oklahoma city": ["oklahoma city thunder", "oklahoma city"],
    "new orleans": ["new orleans pelicans", "new orleans"],
    "new york": ["new york knicks", "new york"],
    "san antonio": ["san antonio spurs", "san antonio"],
    "portland": ["portland trail blazers", "portland"],
    "philadelphia": ["philadelphia 76ers", "philadelphia"],
}


def expand_aliases(name):
    k = norm_key(name)
    return TEAM_ALIASES.get(k, [k])


def extract_odds(data, home, away):
    home_aliases = expand_aliases(home)
    away_aliases = expand_aliases(away)

    # exact match against any alias
    ev = next(
        (e for e in data
         if norm_key((e or {}).get("home_team")) in home_aliases
         and norm_key((e or {}).get("away_team")) in away_aliases),
        None
    )
    # partial includes fallback
    if not ev:
        ev = next(
            (e for e in data
             if any(ha in norm_key((e or {}).get("home_team", "")) or norm_key((e or {}).get("home_team", "")) in ha for ha in home_aliases)
             and any(aa in norm_key((e or {}).get("away_team", "")) or norm_key((e or {}).get("away_team", "")) in aa for aa in away_aliases)),
            None
        )

    if not ev:
        return None

    home_team = norm_team(ev.get("home_team"))
    away_team = norm_team(ev.get("away_team"))
    book = pick_bookmaker(ev.get("bookmakers"))

    line = None
    total = None

    spreads = find_market(book, "spreads") if book else None
    totals = find_market(book, "totals") if book else None

    if spreads and spreads.get("outcomes"):
        out = next((o for o in spreads["outcomes"] if math.isfinite(float(o.get("point", "nan")))), None)
        if out:
            line = to_model_line(home_team, away_team, float(out["point"]), norm_team(out.get("name")))

    if totals and totals.get("outcomes"):
        out = next((o for o in totals["outcomes"] if math.isfinite(float(o.get("point", "nan")))), None)
        if out:
            total = float(out["point"])

    return {"line": line, "total": total, "_book": (book or {}).get("title")}


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


def fetch_snapshot(api_key, ts):
    # If api_key is None or "ROTATE", try each configured key until one
    # returns 200 with data. Otherwise use the explicit key as before.
    if api_key and api_key != "ROTATE":
        keys = [api_key]
    else:
        keys = _get_odds_api_keys()

    last_err = None
    for idx, k in enumerate(keys):
        url = (
            f"{BASE}/historical/sports/basketball_wnba/odds?"
            f"apiKey={quote(k)}"
            f"&regions=us"
            f"&markets=spreads,totals"
            f"&oddsFormat=american"
            f"&date={quote(ts)}"
        )
        res = fetch_with_retry(url)
        if res.status_code in (401, 429):
            print(f"  [nba_historical] Key {idx+1}/{len(keys)} HTTP {res.status_code} — rotating")
            last_err = f"HTTP {res.status_code}"
            continue
        if res.status_code != 200:
            last_err = f"HTTP {res.status_code} {res.reason} {res.text[:200]}"
            if len(keys) == 1:
                raise Exception(f"Historical odds fetch failed: {last_err}")
            continue
        json_data = res.json()
        data = json_data.get("data")
        return data if isinstance(data, list) else []
    raise Exception(f"Historical odds fetch failed (all {len(keys)} keys): {last_err}")


def fetch_closing_odds_for_game(home, away, commence_time_iso):
    """
    Fetch historical odds snapshot near tipoff and extract spread/total.
    Pass commence_time_iso from ESPN (UTC ISO string).
    """
    # Sentinel triggers key rotation inside fetch_snapshot.
    api_key = "ROTATE"

    # Try snapshots at -90min, -30min, and -10min before tipoff.
    offsets = [-90, -30, -10]

    for offset in offsets:
        ts = to_historical_iso(commence_time_iso, offset)
        if not ts:
            return {"line": None, "total": None, "_book": None, "_note": "Bad commenceTimeIso"}

        data = fetch_snapshot(api_key, ts)
        result = extract_odds(data, home, away)

        if result and (isinstance(result.get("line"), (int, float)) or isinstance(result.get("total"), (int, float))):
            return {**result, "_note": f"snapshot at {offset}min before tipoff"}

    return {"line": None, "total": None, "_book": None, "_note": "No odds found in pre-tipoff snapshots"}
