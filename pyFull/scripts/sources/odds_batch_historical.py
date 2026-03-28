import os
import json
import time
import math
import re
import requests
from urllib.parse import quote
from datetime import datetime, timedelta

_dir = os.path.dirname(os.path.abspath(__file__))
ODDS_CACHE_DIR = os.path.join(_dir, "..", "..", "..", "data", "odds_cache", "nba")

BASE = "https://api.the-odds-api.com/v4"


def _sleep(ms):
    time.sleep(ms / 1000.0)


def norm_key(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", str(s or "").lower())).strip()


def norm_team(name):
    return re.sub(r"\s+", " ", str(name or "").strip())


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


def to_model_line(home_team, away_team, spread_points, team_for_spread):
    if not math.isfinite(spread_points):
        return None
    abs_val = abs(spread_points)
    is_home = team_for_spread == home_team
    is_away = team_for_spread == away_team
    if not is_home and not is_away:
        return None
    if is_home:
        return abs_val if spread_points < 0 else -abs_val
    return -abs_val if spread_points < 0 else abs_val


def fetch_with_retry(url, tries=5):
    wait = 700
    for i in range(tries):
        res = requests.get(url)
        if res.status_code != 429:
            return res
        print(f"  [odds_batch] 429 rate limited, retrying in {wait}ms...")
        _sleep(wait)
        wait = min(wait * 2, 8000)
    return requests.get(url)


# -- Fetch one snapshot from the API -------------------------------------------

def fetch_snapshot(api_key, ts):
    url = (
        f"{BASE}/historical/sports/basketball_nba/odds?"
        f"apiKey={quote(api_key)}"
        f"&regions=us"
        f"&markets=spreads,totals"
        f"&oddsFormat=american"
        f"&date={quote(ts)}"
    )

    res = fetch_with_retry(url)
    if res.status_code != 200:
        txt = res.text
        raise Exception(f"Historical odds fetch failed: {res.status_code} {txt}")

    json_data = res.json()
    data = json_data.get("data")
    return data if isinstance(data, list) else []


# -- Extract odds for one game from a snapshot ---------------------------------

def extract_odds_for_game(data, home, away):
    home_aliases = expand_aliases(home)
    away_aliases = expand_aliases(away)

    ev = next(
        (e for e in data
         if norm_key((e or {}).get("home_team")) in home_aliases
         and norm_key((e or {}).get("away_team")) in away_aliases),
        None
    )
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
        out = next((o for o in spreads["outcomes"] if math.isfinite(float((o or {}).get("point", "nan")))), None)
        if out:
            line = to_model_line(home_team, away_team, float(out["point"]), norm_team(out.get("name")))

    if totals and totals.get("outcomes"):
        out = next((o for o in totals["outcomes"] if math.isfinite(float((o or {}).get("point", "nan")))), None)
        if out:
            total = float(out["point"])

    return {"line": line, "total": total, "_book": (book or {}).get("title")}


# -- Main export: fetch ALL odds for a date in 1-2 API calls -------------------

def fetch_odds_for_day(date_yyyymmdd, games_list):
    """
    Fetch ALL odds for a date in 1-2 API calls instead of per-game.
    Uses disk cache so re-runs don't hit the API at all.
    Returns: dict of "away@home" -> { line, total, _book, _note }
    """
    api_key = os.environ.get("ODDS_API_KEY")

    # Check disk cache first
    if not os.path.exists(ODDS_CACHE_DIR):
        os.makedirs(ODDS_CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(ODDS_CACHE_DIR, date_yyyymmdd + ".json")
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            cached = json.load(f)
        # Fuzzy-match cache keys to ESPN names via alias expansion
        if games_list:
            cache_entries = list(cached.items())
            for g in games_list:
                espn_key = f"{g['away']}@{g['home']}"
                if espn_key in cached:
                    continue
                away_aliases = expand_aliases(g["away"])
                home_aliases = expand_aliases(g["home"])
                for ck, cv in cache_entries:
                    parts = ck.split("@")
                    if len(parts) == 2:
                        c_away, c_home = norm_key(parts[0]), norm_key(parts[1])
                        if c_away in away_aliases and c_home in home_aliases:
                            cached[espn_key] = cv
                            break
        print(f"  [odds_batch] {date_yyyymmdd}: loaded from cache ({len(cached)} games)")
        return cached

    if not api_key:
        raise Exception("Missing ODDS_API_KEY env var (no cache found, need API).")

    # Build snapshot timestamps to try
    date_str = f"{date_yyyymmdd[0:4]}-{date_yyyymmdd[4:6]}-{date_yyyymmdd[6:8]}"

    # Game-specific times: 90 min before each game's tipoff
    game_specific_times = []
    for g in games_list:
        commence = g.get("commenceTimeIso")
        if commence:
            try:
                d = datetime.fromisoformat(commence.replace("Z", "+00:00"))
                d = d - timedelta(minutes=90)
                game_specific_times.append(d.strftime("%Y-%m-%dT%H:%M:%SZ"))
            except (ValueError, AttributeError):
                pass

    # Fixed fallbacks: 4 PM UTC (11 AM ET) + 11 PM UTC (6 PM ET)
    fallbacks = [
        f"{date_str}T16:00:00Z",
        f"{date_str}T23:00:00Z",
    ]

    # Deduplicate: game-specific first, then fallbacks
    seen = set()
    all_times = []
    for t in game_specific_times + fallbacks:
        if t not in seen:
            seen.add(t)
            all_times.append(t)

    results = {}
    snapshot_data = None

    for ts in all_times:
        try:
            snapshot_data = fetch_snapshot(api_key, ts)
            _sleep(500)
        except Exception as e:
            print(f"  [odds_batch] Snapshot {ts} failed: {e}")
            continue

        if not snapshot_data or len(snapshot_data) == 0:
            continue

        # Extract odds for all games we're looking for
        found = 0
        for g in games_list:
            key = f"{g['away']}@{g['home']}"
            if key in results and isinstance(results[key].get("line"), (int, float)):
                continue  # already found

            odds = extract_odds_for_game(snapshot_data, g["home"], g["away"])
            if odds and isinstance(odds.get("line"), (int, float)):
                results[key] = {**odds, "_note": f"batch snapshot {ts}"}
                found += 1

        print(f"  [odds_batch] Snapshot {ts[11:16]} UTC: found {found} new games ({len(results)}/{len(games_list)} total)")

        # If we found all games, stop
        if len(results) >= len(games_list):
            break

    # Fill missing games with null
    for g in games_list:
        key = f"{g['away']}@{g['home']}"
        if key not in results:
            results[key] = {"line": None, "total": None, "_book": None, "_note": "Not found in any snapshot"}

    # Cache to disk
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
    except Exception:
        pass  # non-critical

    return results
