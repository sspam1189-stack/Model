# scripts/sources/espn_scoreboard.py
# ESPN scoreboard for NCAA men's basketball

import requests
import math


def fetch_scoreboard(date_yyyymmdd=None):
    base = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
    params = f"?dates={date_yyyymmdd}&groups=50&limit=365" if date_yyyymmdd else "?groups=50&limit=365"
    url = f"{base}{params}"
    res = requests.get(url, headers={"user-agent": "ncaa-picks-bot/1.0"})
    if not res.ok:
        raise Exception(f"HTTP {res.status_code} for {url}")
    return res.json()


def extract_final_scores(scoreboard_json):
    out = []
    for ev in (scoreboard_json.get("events") or []):
        comp = (ev.get("competitions") or [None])[0]
        if not comp:
            continue

        status = (comp.get("status") or {}).get("type", {}).get("name")
        is_final = status == "STATUS_FINAL" or status == "STATUS_FINAL_OVERTIME"
        if not is_final:
            continue

        competitors = comp.get("competitors") or []
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
