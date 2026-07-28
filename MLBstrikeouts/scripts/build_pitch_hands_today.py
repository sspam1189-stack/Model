#!/usr/bin/env python3
# MLBstrikeouts/scripts/build_pitch_hands_today.py
#
# Writes mlb-pitch-hands.json — a small {normalized_name: 'L'|'R'} map of TODAY's
# probable starting pitchers, so the Fade ML dashboard can tag each pitcher in
# "Today's plays" as RHP/LHP (fade-list picks, hand-tails, and the watchlist all
# join against it by name).
#
# Source: MLB Stats API. The schedule's probablePitcher hydrate carries the
# pitcher id + name but not the throwing hand, so we resolve hand via the
# /people endpoint (pitchHand.code). Refreshed each daily run; view-only.
#
# Usage:  cd MLBstrikeouts && python -m scripts.build_pitch_hands_today
#         (or import build_and_write() from run_daily_ml)

import os
import json
import datetime

import requests

from fade_list import _norm

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATS_BASE = "https://statsapi.mlb.com/api/v1"

# MLB team id -> this repo's abbreviation (mirrors MLB_TEAM_ID_TO_ABBR in
# sources/mlb_stats.py). Used to key the per-team starter map.
TEAM_ID_TO_ABBR = {
    108: "LAA", 109: "ARI", 110: "BAL", 111: "BOS", 112: "CHC",
    113: "CIN", 114: "CLE", 115: "COL", 116: "DET", 117: "HOU",
    118: "KC", 119: "LAD", 120: "WSH", 121: "NYM", 133: "OAK",
    134: "PIT", 135: "SD", 136: "SEA", 137: "SF", 138: "STL",
    139: "TB", 140: "TEX", 141: "TOR", 142: "MIN", 143: "PHI",
    144: "ATL", 145: "CWS", 146: "MIA", 147: "NYY", 158: "MIL",
}

OUTPUT_PATHS = [
    os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "mlb-pitch-hands.json")),
    os.path.normpath(os.path.join(
        SCRIPT_DIR, "..", "..", "PythonDashboard", "data", "mlb-pitch-hands.json")),
]


def fetch_probable_hands(date_iso):
    """Return ({normName: 'L'|'R'}, {normName: displayName}, {abbr: {name,hand}})
    for date_iso's probable starters. Empty on any failure (fail-open). The
    third dict is the per-team starter (used to show the take's SP)."""
    hands, names, starters = {}, {}, {}
    try:
        s = requests.get(
            f"{STATS_BASE}/schedule?sportId=1&date={date_iso}&hydrate=probablePitcher",
            timeout=30).json()
    except Exception:
        return hands, names, starters
    id_to_name = {}
    team_starter = {}   # abbr -> (pid, name)
    for day in s.get("dates", []):
        for g in day.get("games", []):
            for side in ("home", "away"):
                sd = g.get("teams", {}).get(side, {}) or {}
                pp = sd.get("probablePitcher") or {}
                pid, full = pp.get("id"), pp.get("fullName")
                abbr = TEAM_ID_TO_ABBR.get((sd.get("team") or {}).get("id"))
                if pid and full:
                    id_to_name[pid] = full
                    if abbr:
                        team_starter[abbr] = (pid, full)
    if not id_to_name:
        return hands, names, starters
    ids = ",".join(str(i) for i in id_to_name)
    try:
        p = requests.get(f"{STATS_BASE}/people?personIds={ids}", timeout=30).json()
    except Exception:
        return hands, names, starters
    id_to_hand = {}
    for person in p.get("people", []):
        full = person.get("fullName")
        code = (person.get("pitchHand") or {}).get("code")
        if code in ("L", "R"):
            id_to_hand[person.get("id")] = code
        if not full or code not in ("L", "R"):
            continue
        nn = _norm(full)
        if nn:
            hands[nn] = code
            names[nn] = full
    for abbr, (pid, full) in team_starter.items():
        starters[abbr] = {"name": full, "hand": id_to_hand.get(pid)}
    return hands, names, starters


def build_payload(date_iso, hands, names, starters):
    return {
        "sport": "MLB",
        "type": "pitch-hands-today",
        "date": date_iso,
        "generated": datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "hands": hands,       # normalized name -> 'L' | 'R'
        "names": names,       # normalized name -> display name (debug/traceability)
        "starters": starters, # team abbr -> {name, hand} of today's probable SP
    }


def write_outputs(payload):
    for path in OUTPUT_PATHS:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    return OUTPUT_PATHS


def build_and_write(date_iso=None):
    """Fetch today's probable-pitcher hands and write the file. Fail-open:
    returns 0 (and writes nothing) if the slate/API yields no hands, so a daily
    run is never broken by this view-only extra."""
    date_iso = date_iso or datetime.date.today().strftime("%Y-%m-%d")
    hands, names, starters = fetch_probable_hands(date_iso)
    if not hands:
        print(f"[pitch-hands] {date_iso}: no probable-pitcher hands resolved (skipped)")
        return 0
    write_outputs(build_payload(date_iso, hands, names, starters))
    print(f"[pitch-hands] {date_iso}: wrote {len(hands)} pitcher hands, "
          f"{len(starters)} team starters")
    return len(hands)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Build today's pitcher L/R map")
    ap.add_argument("--date", default=None, help="ISO date YYYY-MM-DD (default: today)")
    args = ap.parse_args()
    build_and_write(args.date)
