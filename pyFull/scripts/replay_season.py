#!/usr/bin/env python3
# scripts/replay_season.py
# Backfill = live. Re-runs run_daily.main() -- the live code, unchanged -- over
# the inputs every live run saved (data/run_inputs/nba/<date>/<HHMMSS>.json.gz),
# in the order they ran, with the clock frozen at each run's start time.
#
# It runs in a scratch copy of core/ + pyFull/scripts/, so it never touches
# the real history.json, kalman_state.json or caches. Start state: the live
# history.json's runs from before the first recorded run (e.g. pre-recording
# burn-in days), the weights that first run started from, and an empty
# Kalman state -- i.e. the season as it stood when recording began.
#
# Usage (from pyFull/scripts):
#   python replay_season.py                 # replay every saved run, compare to live
#   python replay_season.py --to 20261130   # stop after a date
#   python replay_season.py --out FILE      # where to write the replayed history.json
#
# --compare (default on) checks every replayed game against live history.json:
# pick, confidence, P(cover), projected scores, line and result. Exit code 1 on
# any difference.

import argparse
import glob
import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
INPUTS = os.path.join(REPO, "data", "run_inputs", "nba")
LIVE_HISTORY = os.path.join(REPO, "pyFull", "data", "history.json")
CHECK_FIELDS = ("status", "sPick", "sConf", "pCover", "hS", "aS", "line", "total",
                "homeScore", "awayScore", "sResult", "stakeUnits")


def _snapshots(to_date=None):
    paths = sorted(glob.glob(os.path.join(INPUTS, "*", "*.json.gz")))
    if to_date:
        paths = [p for p in paths if os.path.basename(os.path.dirname(p)) <= to_date]
    return paths


def _load(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def _setup(root, first):
    shutil.copytree(os.path.join(REPO, "core"), os.path.join(root, "core"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(HERE, os.path.join(root, "pyFull", "scripts"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    for d in ("pyFull/data", "PythonDashboard/data", "data/injury_cache/nba",
              "data/stats_cache/nba", "data/espn_cache/nba"):
        os.makedirs(os.path.join(root, d), exist_ok=True)
    live = json.load(open(LIVE_HISTORY, encoding="utf-8"))
    state_in = ((first.get("inputs", {}).get("_notes") or {}).get("v") or {}).get("stateIn") or {}
    start = {"runs": [r for r in live.get("runs", []) if r.get("date", "") < first["date"]]}
    for k in ("weights", "weightsVar", "lastTuneDate", "residualVar"):
        if state_in.get(k) is not None:
            start[k] = state_in[k]
    json.dump(start, open(os.path.join(root, "pyFull", "data", "history.json"), "w"), indent=2)


def _inner(paths):
    """Runs inside the scratch copy: replay each saved run in order."""
    sys.path.insert(0, os.getcwd())
    import run_inputs
    import run_daily
    import core.kalman_state as ks
    import core.store as st
    frozen = (run_daily, ks, st)
    for p in paths:
        snap = _load(p)
        tag = f"{snap['date']} {snap['startedAt'][11:19]}"
        run_inputs.begin_replay(snap, modules=frozen)
        try:
            run_daily.main()
            outcome = "ok"
        except Exception as e:
            outcome = f"error: {e}"
        finally:
            run_inputs.end_replay(modules=frozen)
        live = "ok" if snap.get("status") == "ok" else f"error: {snap.get('error')}"
        flag = "" if (outcome == "ok") == (live == "ok") else "   <-- live was " + live
        print(f"[replay] {tag}  {outcome}{flag}", flush=True)


def _compare(replayed, dates):
    live = {r["date"]: r for r in json.load(open(LIVE_HISTORY, encoding="utf-8")).get("runs", [])}
    mine = {r["date"]: r for r in replayed.get("runs", [])}
    diffs, games = [], 0
    for d in sorted(dates):
        lg = {(g.get("away"), g.get("home")): g for g in live.get(d, {}).get("games", [])}
        mg = {(g.get("away"), g.get("home")): g for g in mine.get(d, {}).get("games", [])}
        for k in sorted(set(lg) | set(mg), key=str):
            games += 1
            if k not in lg or k not in mg:
                diffs.append(f"{d} {k[0]} @ {k[1]}: only in {'live' if k in lg else 'replay'}")
                continue
            for f in CHECK_FIELDS:
                if lg[k].get(f) != mg[k].get(f):
                    diffs.append(f"{d} {k[0]} @ {k[1]}: {f} live={lg[k].get(f)!r} replay={mg[k].get(f)!r}")
    return games, diffs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", help="last date to replay (YYYYMMDD)")
    ap.add_argument("--out", help="write the replayed history.json here")
    ap.add_argument("--no-compare", action="store_true")
    ap.add_argument("--keep", action="store_true", help="keep the scratch copy")
    ap.add_argument("--inner", nargs="*", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.inner is not None:
        return _inner(a.inner)

    paths = _snapshots(a.to)
    if not paths:
        print(f"No recorded runs under {INPUTS}")
        return 1
    first = _load(paths[0])
    root = tempfile.mkdtemp(prefix="pyfull-replay-")
    try:
        _setup(root, first)
        scripts = os.path.join(root, "pyFull", "scripts")
        print(f"Replaying {len(paths)} recorded runs ({first['date']} .. "
              f"{os.path.basename(os.path.dirname(paths[-1]))}) in {root}")
        env = dict(os.environ, PYTHONHASHSEED="0")
        rc = subprocess.call([sys.executable, os.path.join(scripts, "replay_season.py"), "--inner", *paths],
                             cwd=scripts, env=env)
        if rc != 0:
            print("Replay process failed")
            return rc
        replayed = json.load(open(os.path.join(root, "pyFull", "data", "history.json"), encoding="utf-8"))
        if a.out:
            json.dump(replayed, open(a.out, "w", encoding="utf-8"), indent=2)
            print(f"Replayed history -> {a.out}")
        if a.no_compare:
            return 0
        dates = {os.path.basename(os.path.dirname(p)) for p in paths}
        games, diffs = _compare(replayed, dates)
        if diffs:
            print(f"\nMISMATCH: {len(diffs)} difference(s) across {games} games")
            for line in diffs[:200]:
                print("  " + line)
            return 1
        print(f"\nMATCH: {games} games on {len(dates)} days identical to live "
              f"({', '.join(CHECK_FIELDS)})")
        return 0
    finally:
        if a.keep:
            print(f"Scratch copy kept at {root}")
        else:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main() or 0)
