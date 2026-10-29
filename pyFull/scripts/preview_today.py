#!/usr/bin/env python3
# scripts/preview_today.py
# What would each NBA full-season model project today, burn-in or not?
#
# Runs each model's run_daily -- unchanged, live feeds -- in a throwaway copy
# of the repo with MIN_GP 1 and PICKS_MIN_GP 0, then prints today's
# projections side by side. Nothing in the real repo is written. Early-season
# numbers rest on a handful of games per team: a preview, not picks.
#
# --date YYYYMMDD previews a past day instead: stats through the day before
# are fetched from NBA.com, and that day's last pre-tip run is replayed
# (replay_season.py) with the lines/injuries pyFull recorded live then.
#
# Usage (repo root or anywhere; needs stats.nba.com, so the self-hosted runner):
#   python pyFull/scripts/preview_today.py
#   python pyFull/scripts/preview_today.py --date 20261026

import argparse
import glob
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
MODELS = [("pyFull", "NBA Full Season"), ("pyFullKalmanOff", "NBA Full KalmanOff"),
          ("pyFullDNET", "NBA Full dNET")]
SKIP = shutil.ignore_patterns("__pycache__", "archive", "run_inputs", "odds_cache", "stats_cache")
INPUTS_DIR = {"pyFull": "nba", "pyFullKalmanOff": "nba-kalmanoff", "pyFullDNET": "nba-dnet"}


def fetch_stats(date):
    """Team stats through the day before date, every team (no GP filter)."""
    sys.path.insert(0, HERE)
    from sources.nba_stats import fetch_nba_stats_enhanced
    return fetch_nba_stats_enhanced(date, min_gp=0)


def _setup(model):
    root = tempfile.mkdtemp(prefix=f"preview-{model}-")
    shutil.copytree(os.path.join(REPO, "core"), os.path.join(root, "core"), ignore=SKIP)
    shutil.copytree(os.path.join(REPO, model), os.path.join(root, model), ignore=SKIP)
    os.makedirs(os.path.join(root, "data"), exist_ok=True)
    os.makedirs(os.path.join(root, "PythonDashboard", "data"), exist_ok=True)
    for sub in ("espn_cache", "injury_cache"):
        src = os.path.join(REPO, "data", sub, "nba")
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(root, "data", sub, "nba"))
    d = os.path.join(root, model, "scripts", "defaults.py")
    s = open(d, encoding="utf-8").read()
    s = re.sub(r'"MIN_GP": \d+', '"MIN_GP": 1', s)
    s = re.sub(r'"PICKS_MIN_GP": \d+', '"PICKS_MIN_GP": 0', s)
    open(d, "w", encoding="utf-8").write(s)
    return root


def replay_model(model, date, stats):
    """Replay date's last pre-tip run with stats through the day before."""
    root = _setup(model)
    for sub in ("odds_cache", "stats_cache"):
        os.makedirs(os.path.join(root, "data", sub, "nba"), exist_ok=True)
    src = os.path.join(REPO, "data", "odds_cache", "nba", date + ".json")
    if os.path.exists(src):
        shutil.copy(src, os.path.join(root, "data", "odds_cache", "nba"))
    with open(os.path.join(root, "data", "stats_cache", "nba", date + ".json"), "w", encoding="utf-8") as f:
        json.dump(stats, f)
    # pyFull's recorded live inputs for the day (same lines for all three
    # models), with the stats the burn-in kept out of them.
    dst = os.path.join(root, "data", "run_inputs", INPUTS_DIR[model], date)
    os.makedirs(dst, exist_ok=True)
    for p in glob.glob(os.path.join(REPO, "data", "run_inputs", "nba", date, "*.json.gz")):
        with gzip.open(p, "rt", encoding="utf-8") as f:
            snap = json.load(f)
        snap["inputs"]["stats"] = {"v": [stats, None]}
        with gzip.open(os.path.join(dst, os.path.basename(p)), "wt", encoding="utf-8") as f:
            json.dump(snap, f)
    out = os.path.join(root, "out.json")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GMAIL")}
    env["PYFULL_BACKFILL_REPO"] = root
    p = subprocess.run([sys.executable, "replay_season.py", "--from", date, "--to", date, "--out", out],
                       cwd=os.path.join(root, model, "scripts"), env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(f"--- {model} replay exit {p.returncode}; last lines:")
    print("\n".join((p.stdout + p.stderr).strip().splitlines()[-15:]))
    try:
        runs = json.load(open(out, encoding="utf-8"))["runs"]
        return next((r for r in runs if r["date"] == date), None)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def run_model(model):
    root = _setup(model)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GMAIL")}
    p = subprocess.run([sys.executable, "run_daily.py"], cwd=os.path.join(root, model, "scripts"),
                       env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(f"--- {model} run_daily exit {p.returncode}; last lines:")
    print("\n".join((p.stdout + p.stderr).strip().splitlines()[-15:]))
    try:
        runs = json.load(open(os.path.join(root, model, "data", "history.json"), encoding="utf-8"))["runs"]
        return runs[-1]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def final_scores(date):
    """{(away, home): (away_score, home_score)} for finished games."""
    sys.path.insert(0, HERE)
    from sources.espn_scoreboard import fetch_scoreboard
    out = {}
    for ev in fetch_scoreboard(date).get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        if not ((comp.get("status") or {}).get("type") or {}).get("completed"):
            continue
        t = {c.get("homeAway"): ((c.get("team") or {}).get("displayName"), int(c.get("score") or 0))
             for c in comp.get("competitors", [])}
        if "away" in t and "home" in t:
            out[(t["away"][0], t["home"][0])] = (t["away"][1], t["home"][1])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="past day YYYYMMDD (default: today, live)")
    a = ap.parse_args()
    if a.date:
        stats = fetch_stats(a.date)
        print(f"stats through the day before {a.date}: {len(stats['season'])} teams")
        out = {m: replay_model(m, a.date, stats) for m, _ in MODELS}
    else:
        out = {m: run_model(m) for m, _ in MODELS}
    date = next((r["date"] for r in out.values() if r), "?")
    finals = final_scores(date) if a.date else {}
    lines = [f"## NBA projections preview {date} (burn-in lowered: MIN_GP 1, picks at 0 GP)", ""]
    hdr = "| Game | Line (home) |" + (" Final |" if finals else "") + "".join(f" {n} |" for _, n in MODELS)
    lines += [hdr, "|---|---|" + ("---|" if finals else "") + "---|" * len(MODELS)]
    record = {m: [0, 0, 0] for m, _ in MODELS}
    keys = []
    for r in out.values():
        for g in (r or {}).get("games", []):
            k = (g["away"], g["home"])
            if k not in keys:
                keys.append(k)
    for away, home in keys:
        row, line = [], None
        for m, _ in MODELS:
            g = next((x for x in (out[m] or {}).get("games", []) if (x["away"], x["home"]) == (away, home)), None)
            if not g or g.get("hS") is None:
                row.append("no projection")
                continue
            line = g.get("line")
            side_home = (g.get("pHomeCover") or 0) >= (g.get("pAwayCover") or 0)
            p = max(g.get("pHomeCover") or 0, g.get("pAwayCover") or 0)
            team = home if side_home else away
            fire = " **65%+**" if p >= 0.65 else ""
            res = ""
            fs = finals.get((away, home))
            if fs and line is not None:
                cover = fs[1] - fs[0] + line           # home side vs the line
                res = " PUSH" if cover == 0 else (" W" if (cover > 0) == side_home else " L")
                if p >= 0.65:
                    record[m][{" W": 0, " L": 1, " PUSH": 2}[res]] += 1
            row.append(f"{g['aS']:.1f}-{g['hS']:.1f} (home {g['hS'] - g['aS']:+.1f}) -> {team} {p:.1%}{fire}{res}")
        fcol = ""
        if finals:
            fs = finals.get((away, home))
            fcol = f" {fs[0]}-{fs[1]} (home {fs[1] - fs[0]:+d}) |" if fs else " not final |"
        lines.append(f"| {away} @ {home} | {line if line is not None else '-'} |{fcol} " + " | ".join(row) + " |")
    if finals:
        lines += ["", "65%+ plays: " + ", ".join(
            f"{n} {w}-{l}" + (f"-{pu}" if pu else "") + f" ({w - 1.1 * l:+.1f}u)"
            for (m, n) in MODELS for w, l, pu in [record[m]])]
    text = "\n".join(lines)
    print("\n" + text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main()
