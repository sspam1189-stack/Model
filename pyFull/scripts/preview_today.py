#!/usr/bin/env python3
# scripts/preview_today.py
# What would each NBA full-season model project today, burn-in or not?
#
# Runs each model's run_daily -- unchanged, live feeds -- in a throwaway copy
# of the repo with MIN_GP 1 and PICKS_MIN_GP 0, then prints today's
# projections side by side. Nothing in the real repo is written. Early-season
# numbers rest on a handful of games per team: a preview, not picks.
#
# Usage (repo root or anywhere; needs stats.nba.com, so the self-hosted runner):
#   python pyFull/scripts/preview_today.py

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


def run_model(model):
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


def main():
    out = {m: run_model(m) for m, _ in MODELS}
    date = next((r["date"] for r in out.values() if r), "?")
    lines = [f"## NBA projections preview {date} (burn-in lowered: MIN_GP 1, picks at 0 GP)", ""]
    hdr = "| Game | Line (home) |" + "".join(f" {n} |" for _, n in MODELS)
    lines += [hdr, "|---|---|" + "---|" * len(MODELS)]
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
            row.append(f"{g['aS']:.1f}-{g['hS']:.1f} (home {g['hS'] - g['aS']:+.1f}) -> {team} {p:.0%}{fire}")
        lines.append(f"| {away} @ {home} | {line if line is not None else '-'} | " + " | ".join(row) + " |")
    text = "\n".join(lines)
    print("\n" + text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main()
