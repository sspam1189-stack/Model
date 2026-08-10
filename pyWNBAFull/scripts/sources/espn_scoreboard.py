import os
import json
import requests
import math

_HERE = os.path.dirname(os.path.abspath(__file__))
# Self-contained cache under pyWNBAFull/data (not shared with any other model).
CACHE_DIR = os.path.normpath(os.path.join(_HERE, "..", "..", "data", "espn_cache", "wnba"))


def fetch_scoreboard(date_yyyymmdd=None):
    """
    Uses ESPN's public JSON WNBA scoreboard endpoint.
    Base: https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard?dates=YYYYMMDD
    Verified 2026-07-01: returns WNBA games w/ final scores for 2024/2025/2026.
    """
    # Disk cache — WNBA engine only (skip for today — statuses change)
    import datetime as _dt
    _today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d")
    if date_yyyymmdd and date_yyyymmdd != _today:
        os.makedirs(CACHE_DIR, exist_ok=True)
        disk_path = os.path.join(CACHE_DIR, date_yyyymmdd + ".json")
        if os.path.exists(disk_path):
            with open(disk_path, "r") as f:
                cached = json.load(f)
            all_final = all(
                (ev.get("competitions", [{}])[0].get("status", {}).get("type", {}).get("name", "")
                 in ("STATUS_FINAL", "STATUS_FINAL_OVERTIME"))
                for ev in cached.get("events", [])
            ) if cached.get("events") else False
            if all_final:
                return cached

    base = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard"
    url = f"{base}?dates={date_yyyymmdd}" if date_yyyymmdd else base
    # ESPN's CDN 403s bot/browser-spoof UAs (since 2026-08-06); the
    # requests default UA is allowed, so send no custom User-Agent.
    res = requests.get(url)
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
            "date": (scoreboard_json.get("day") or {}).get("date")
        })
    return out
