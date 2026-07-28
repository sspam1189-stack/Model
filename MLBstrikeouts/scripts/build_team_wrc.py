#!/usr/bin/env python3
# MLBstrikeouts/scripts/build_team_wrc.py
#
# Builds mlb-team-wrc.json — the "Team wRC+ by Opposing Starter Hand" reference
# table shown at the bottom of the MLB Fade ML dashboard tab.
#
# WHY THIS IS A MANUAL SNAPSHOT (not part of the daily pipeline)
# -------------------------------------------------------------
# The number shown is FanGraphs' TRUE wRC+ (park + league adjusted). wRC+ is a
# FanGraphs stat; the MLB Stats API does not expose it. FanGraphs' whole domain
# sits behind a Cloudflare bot-challenge, so a server-side fetch (this repo's
# daily GitHub Actions run) gets an HTTP 403 "Just a moment…" page, never data.
# Only a REAL browser passes the challenge. So the table is a snapshot captured
# through a browser and committed here — it is view-only, never feeds a pick or
# a grade, and the daily/backfill pipeline neither produces nor depends on it.
#
# HOW TO REFRESH (browser capture -> update SNAPSHOT below -> re-run)
# ------------------------------------------------------------------
# 1. Open these two FanGraphs Splits-Leaderboard URLs in a browser (the browser
#    solves Cloudflare automatically). statgroup=2 is the "Advanced" stat set,
#    which is the one that carries the wRC+ column. splitArr=1 is vs LHP,
#    splitArr=2 is vs RHP. Bump the season/date range as needed:
#      vs LHP: https://www.fangraphs.com/leaders/splits-leaderboards?splitArr=1&strgroup=season&statgroup=2&startDate=YYYY-03-01&endDate=YYYY-11-30&position=B&statType=team&season=YYYY
#      vs RHP: https://www.fangraphs.com/leaders/splits-leaderboards?splitArr=2&strgroup=season&statgroup=2&startDate=YYYY-03-01&endDate=YYYY-11-30&position=B&statType=team&season=YYYY
# 2. Read the POST response from /api/leaders/splits/splits-leaders (DevTools
#    Network tab, or ask Claude to capture it). Each row's "TeamNameAbb" and
#    "wRC+" are all you need.
# 3. Update SNAPSHOT below (FanGraphs abbreviations, wRC+ rounded to int) and set
#    SEASON / AS_OF. Then run:  python build_team_wrc.py
#
# The FanGraphs abbreviations are mapped to this repo's team abbreviations
# (used everywhere else in the fade model) via FG_TO_REPO.

import os
import json

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

OUTPUT_PATHS = [
    os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "mlb-team-wrc.json")),
    os.path.normpath(os.path.join(
        SCRIPT_DIR, "..", "..", "PythonDashboard", "data", "mlb-team-wrc.json")),
]

# FanGraphs team abbreviation -> this repo's abbreviation (MLB_TEAM_ID_TO_ABBR
# in sources/mlb_stats.py). Only the ones that differ need mapping; the rest
# pass through unchanged.
FG_TO_REPO = {
    "CHW": "CWS", "KCR": "KC", "SDP": "SD", "SFG": "SF",
    "TBR": "TB", "WSN": "WSH", "ATH": "OAK",
}

# --- Manual snapshot -------------------------------------------------------
# FanGraphs TRUE wRC+ (park + league adjusted), team offense split by the
# OPPOSING STARTER's hand. FanGraphs abbreviations; wRC+ rounded to integer.
SEASON = 2026
AS_OF = "2026-07-28"   # date the snapshot was captured

SNAPSHOT = {
    # vs LHP (FanGraphs splitArr=1, statgroup=2 Advanced)
    "vsLHP": {
        "LAA": 100, "BAL": 102, "BOS": 114, "CHW": 101, "CLE": 85,
        "DET": 92, "KCR": 95, "MIN": 92, "NYY": 105, "ATH": 99,
        "SEA": 89, "TBR": 95, "TEX": 113, "TOR": 80, "ARI": 112,
        "ATL": 92, "CHC": 122, "CIN": 106, "COL": 80, "MIA": 92,
        "HOU": 97, "LAD": 104, "MIL": 91, "WSN": 121, "NYM": 98,
        "PHI": 90, "PIT": 93, "STL": 95, "SDP": 87, "SFG": 91,
    },
    # vs RHP (FanGraphs splitArr=2, statgroup=2 Advanced)
    "vsRHP": {
        "LAA": 95, "BAL": 105, "BOS": 86, "CHW": 105, "CLE": 96,
        "DET": 102, "KCR": 96, "MIN": 108, "NYY": 105, "ATH": 101,
        "SEA": 104, "TBR": 111, "TEX": 103, "TOR": 95, "ARI": 86,
        "ATL": 105, "CHC": 107, "CIN": 85, "COL": 95, "MIA": 103,
        "HOU": 105, "LAD": 116, "MIL": 110, "WSN": 108, "NYM": 93,
        "PHI": 92, "PIT": 114, "STL": 95, "SDP": 97, "SFG": 103,
    },
}


def _map_abbr(fg_abbr):
    return FG_TO_REPO.get(fg_abbr, fg_abbr)


def parse_fg_rows(rows):
    """FanGraphs splits-leaders ``data`` rows -> {repo_abbr: wRC+ int}.

    ``rows`` is the list of dicts from the endpoint's JSON (each has
    'TeamNameAbb' and 'wRC+'). Handy when refreshing straight from a captured
    API response instead of hand-editing SNAPSHOT.
    """
    out = {}
    for r in rows:
        abbr = r.get("TeamNameAbb")
        wrc = r.get("wRC+")
        if abbr is None or wrc is None:
            continue
        out[_map_abbr(abbr)] = int(round(float(wrc)))
    return out


def build_payload(vs_lhp, vs_rhp, season=SEASON, as_of=AS_OF):
    """Merge the two splits (FanGraphs-abbr keyed) into the dashboard payload."""
    teams = {}
    for fg_abbr, v in vs_lhp.items():
        teams.setdefault(_map_abbr(fg_abbr), {})["vsLHP"] = int(round(v))
    for fg_abbr, v in vs_rhp.items():
        teams.setdefault(_map_abbr(fg_abbr), {})["vsRHP"] = int(round(v))
    return {
        "sport": "MLB",
        "type": "team-wrc-splits",
        "metric": "wRC+ (FanGraphs, park + league adjusted)",
        "season": season,
        "asOf": as_of,
        "source": "fangraphs-splits-leaderboards",
        "note": "Manual browser snapshot — view-only, not used in fade picks.",
        "teams": dict(sorted(teams.items())),
    }


def write_outputs(payload):
    for path in OUTPUT_PATHS:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    return OUTPUT_PATHS


def main():
    payload = build_payload(SNAPSHOT["vsLHP"], SNAPSHOT["vsRHP"])
    paths = write_outputs(payload)
    n = len(payload["teams"])
    print(f"[build_team_wrc] wrote {n} teams (season {payload['season']}, "
          f"as of {payload['asOf']}) to:")
    for p in paths:
        print("   ", p)


if __name__ == "__main__":
    main()
