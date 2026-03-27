# scripts/sources/espn_scoreboard.py
# Uses ESPN's public JSON scoreboard endpoint.

import os
import json
import requests
import math

_HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.normpath(os.path.join(_HERE, "..", "..", "..", "cache", "espn_nba"))


def fetch_scoreboard(date_yyyymmdd=None):
    """
    Fetch ESPN NBA scoreboard JSON.
    date_yyyymmdd: optional string like "20260130"
    """
    # Disk cache — shared across jsNBA/jsFull/pyNBA/pyFull
    if date_yyyymmdd:
        os.makedirs(CACHE_DIR, exist_ok=True)
        disk_path = os.path.join(CACHE_DIR, date_yyyymmdd + ".json")
        if os.path.exists(disk_path):
            with open(disk_path, "r") as f:
                return json.load(f)

    base = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
    url = f"{base}?dates={date_yyyymmdd}" if date_yyyymmdd else base
    res = requests.get(url, headers={"user-agent": "nba-picks-bot/1.0"})
    if res.status_code != 200:
        raise Exception(f"HTTP {res.status_code} for {url}")
    data = res.json()

    if date_yyyymmdd:
        try:
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
            "date": (scoreboard_json.get("day") or {}).get("date"),
        })
    return out
