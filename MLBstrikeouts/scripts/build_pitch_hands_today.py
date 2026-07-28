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

OUTPUT_PATHS = [
    os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "mlb-pitch-hands.json")),
    os.path.normpath(os.path.join(
        SCRIPT_DIR, "..", "..", "PythonDashboard", "data", "mlb-pitch-hands.json")),
]


def fetch_probable_hands(date_iso):
    """Return ({normName: 'L'|'R'}, {normName: displayName}) for date_iso's
    probable starters. Empty dicts on any failure (fail-open)."""
    hands, names = {}, {}
    try:
        s = requests.get(
            f"{STATS_BASE}/schedule?sportId=1&date={date_iso}&hydrate=probablePitcher",
            timeout=30).json()
    except Exception:
        return hands, names
    id_to_name = {}
    for day in s.get("dates", []):
        for g in day.get("games", []):
            for side in ("home", "away"):
                pp = (g.get("teams", {}).get(side, {}) or {}).get("probablePitcher") or {}
                pid, full = pp.get("id"), pp.get("fullName")
                if pid and full:
                    id_to_name[pid] = full
    if not id_to_name:
        return hands, names
    ids = ",".join(str(i) for i in id_to_name)
    try:
        p = requests.get(f"{STATS_BASE}/people?personIds={ids}", timeout=30).json()
    except Exception:
        return hands, names
    for person in p.get("people", []):
        full = person.get("fullName")
        code = (person.get("pitchHand") or {}).get("code")
        if not full or code not in ("L", "R"):
            continue
        nn = _norm(full)
        if nn:
            hands[nn] = code
            names[nn] = full
    return hands, names


def build_payload(date_iso, hands, names):
    return {
        "sport": "MLB",
        "type": "pitch-hands-today",
        "date": date_iso,
        "generated": datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "hands": hands,   # normalized name -> 'L' | 'R'
        "names": names,   # normalized name -> display name (debug/traceability)
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
    hands, names = fetch_probable_hands(date_iso)
    if not hands:
        print(f"[pitch-hands] {date_iso}: no probable-pitcher hands resolved (skipped)")
        return 0
    write_outputs(build_payload(date_iso, hands, names))
    print(f"[pitch-hands] {date_iso}: wrote {len(hands)} pitcher hands")
    return len(hands)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Build today's pitcher L/R map")
    ap.add_argument("--date", default=None, help="ISO date YYYY-MM-DD (default: today)")
    args = ap.parse_args()
    build_and_write(args.date)
