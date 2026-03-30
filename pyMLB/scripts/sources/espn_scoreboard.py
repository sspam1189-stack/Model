# pyMLB/scripts/sources/espn_scoreboard.py
# Uses ESPN's public JSON scoreboard endpoint for MLB.

import os
import json
import math
import requests
import datetime as _dt

_HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.normpath(os.path.join(_HERE, "..", "..", "..", "data", "espn_cache", "mlb"))


def fetch_scoreboard(date_yyyymmdd=None):
    """
    Fetch ESPN MLB scoreboard JSON for a given date.
    Caches to disk for non-today dates (final scores don't change).
    """
    _today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d")
    if date_yyyymmdd and date_yyyymmdd != _today:
        os.makedirs(CACHE_DIR, exist_ok=True)
        disk_path = os.path.join(CACHE_DIR, date_yyyymmdd + ".json")
        if os.path.exists(disk_path):
            with open(disk_path, "r") as f:
                return json.load(f)

    base = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"
    url = f"{base}?dates={date_yyyymmdd}" if date_yyyymmdd else base
    res = requests.get(url, headers={"user-agent": "mlb-picks-bot/1.0"}, timeout=15)
    if res.status_code != 200:
        raise Exception(f"HTTP {res.status_code} for {url}")
    data = res.json()

    if date_yyyymmdd:
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(os.path.join(CACHE_DIR, date_yyyymmdd + ".json"), "w") as f:
                json.dump(data, f)
        except Exception:
            pass

    return data


def extract_final_scores(scoreboard_json):
    """Extract final scores from ESPN scoreboard JSON."""
    out = []
    for ev in scoreboard_json.get("events", []):
        comp = (ev.get("competitions") or [None])[0]
        if not comp:
            continue
        status = (comp.get("status") or {}).get("type", {}).get("name")
        is_final = status in ("STATUS_FINAL", "STATUS_FINAL_OVERTIME")
        if not is_final:
            continue
        competitors = comp.get("competitors", [])
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        if not away or not home:
            continue
        away_name = (away.get("team") or {}).get("displayName")
        home_name = (home.get("team") or {}).get("displayName")
        try:
            away_score = float(away.get("score"))
            home_score = float(home.get("score"))
        except (TypeError, ValueError):
            continue
        if not away_name or not home_name or not math.isfinite(away_score) or not math.isfinite(home_score):
            continue
        out.append({
            "away": away_name,
            "home": home_name,
            "awayScore": away_score,
            "homeScore": home_score,
            "gameId": ev.get("id"),
            "date": (scoreboard_json.get("day") or {}).get("date"),
        })
    return out


def extract_all_games(scoreboard_json):
    """Extract all games regardless of completion status. For schedule info."""
    out = []
    for ev in scoreboard_json.get("events", []):
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
        venue = (comp.get("venue") or {}).get("fullName", "")
        if not away or not home:
            continue
        out.append({
            "away": away,
            "home": home,
            "commenceTimeIso": commence_time_iso,
            "isFinal": is_final,
            "gameId": ev.get("id"),
            "venue": venue,
        })
    return out


def fetch_date_scores(date_str):
    """Convenience: fetch + extract final scores for a date."""
    sb = fetch_scoreboard(date_str)
    return extract_final_scores(sb)
