# scripts/sources/rest_detect.py
# Detects back-to-back situations for tonight's games.

import re
import requests
from datetime import datetime, timedelta


def _yesterday_espn():
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/Chicago"))
    yesterday = now - timedelta(days=1)
    return yesterday.strftime("%Y%m%d")


def detect_b2b():
    date = _yesterday_espn()
    url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?dates={date}&groups=50&limit=365"

    res = requests.get(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json"
    })
    if not res.ok:
        return set()

    sb = res.json()
    teams = set()

    for ev in (sb.get("events") or []):
        comp = ((ev.get("competitions") or [None]))[0]
        if not comp:
            continue
        status = (comp.get("status") or {}).get("type", {})
        if not status.get("completed"):
            continue
        for c in (comp.get("competitors") or []):
            name = (c.get("team") or {}).get("displayName")
            if name:
                teams.add(name)

    return teams


B2B_OFF_PENALTY = -1.5
B2B_DEF_PENALTY = 0.8


def apply_b2b_adjustment(team_stats, b2b_teams, todays_games):
    if not b2b_teams or not len(b2b_teams):
        return {"adjusted": team_stats, "b2bNotes": {}}

    adjusted = dict(team_stats)
    b2b_notes = {}

    teams_tonight = set()
    for g in todays_games:
        if g.get("away"):
            teams_tonight.add(g["away"])
        if g.get("home"):
            teams_tonight.add(g["home"])

    for b2b_name in b2b_teams:
        stat_key = _find_stat_key(team_stats, b2b_name)
        if not stat_key:
            continue

        is_tonight = stat_key in teams_tonight or \
            any(_norm_key(t) == _norm_key(stat_key) for t in teams_tonight)
        if not is_tonight:
            continue

        orig = team_stats[stat_key]
        adjusted[stat_key] = {
            **orig,
            "OFF": round((orig["OFF"] + B2B_OFF_PENALTY) * 100) / 100,
            "DEF": round((orig["DEF"] + B2B_DEF_PENALTY) * 100) / 100,
        }

        b2b_notes[stat_key] = f"B2B: OFF {B2B_OFF_PENALTY}, DEF +{B2B_DEF_PENALTY}"

    return {"adjusted": adjusted, "b2bNotes": b2b_notes}


def _norm_key(s):
    s = str(s or "").lower()
    s = re.sub(r'[^a-z0-9 ]', ' ', s)
    s = re.sub(r' +', ' ', s).strip()
    return s


def _safe_fuzzy(a, b):
    if not a or not b:
        return False
    if a == b:
        return True
    shorter = a if len(a) < len(b) else b
    longer = b if len(a) < len(b) else a
    if len(shorter) / len(longer) < 0.85:
        return False
    return shorter in longer


def _find_stat_key(stats, team_name):
    if team_name in stats:
        return team_name
    wanted = _norm_key(team_name)
    for k in stats:
        nk = _norm_key(k)
        if nk == wanted or _safe_fuzzy(nk, wanted):
            return k
    mascot = wanted.split(" ")[-1] if wanted else ""
    for k in stats:
        if _norm_key(k).split(" ")[-1] == mascot:
            return k
    return None
