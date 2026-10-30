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
# --date D0 --to D1 replays D0..D1 in order, walk-forward: each model starts
# fresh and learns (Kalman, self-tune) only from days already graded.
#
# Usage (repo root or anywhere; needs stats.nba.com, so the self-hosted runner):
#   python pyFull/scripts/preview_today.py
#   python pyFull/scripts/preview_today.py --date 20261026
#   python pyFull/scripts/preview_today.py --date 20261023 --to 20261029

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
          ("pyFullDNET", "NBA Full dNET"), ("pyFullKalmanEarly", "NBA Full KalmanEarly")]
SKIP = shutil.ignore_patterns("__pycache__", "archive", "run_inputs", "odds_cache", "stats_cache")
INPUTS_DIR = {"pyFull": "nba", "pyFullKalmanOff": "nba-kalmanoff", "pyFullDNET": "nba-dnet",
              "pyFullKalmanEarly": "nba-kalmanearly"}


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
    # Kalman offsets count only from the model's own projection start (its
    # real MIN_GP), as live -- the preview lowers MIN_GP to project earlier,
    # which must not let a model apply Kalman before it really would.
    real = re.search(r'"MIN_GP": (\d+)', s)
    if real and "KALMAN_APPLY_MIN_GP" not in s:
        s = s.replace('"PICKS_MIN_GP"', f'"KALMAN_APPLY_MIN_GP": {real.group(1)},\n    "PICKS_MIN_GP"', 1)
    s = re.sub(r'"MIN_GP": \d+', '"MIN_GP": 1', s)
    s = re.sub(r'"PICKS_MIN_GP": \d+', '"PICKS_MIN_GP": 0', s)
    open(d, "w", encoding="utf-8").write(s)
    return root


def replay_model(model, dates, stats, boards):
    """Replay each date's last pre-tip run in order, from a fresh season state
    (default weights, empty Kalman), with stats through the day before each
    date. Earlier days are graded from the final scoreboards before the next
    day runs, so Kalman and self-tune only ever learn from games already
    played. Returns {date: run}."""
    root = _setup(model)
    for sub in ("odds_cache", "stats_cache", "espn_cache"):
        os.makedirs(os.path.join(root, "data", sub, "nba"), exist_ok=True)
    for d, sb in boards.items():             # final scoreboards, for grading
        with open(os.path.join(root, "data", "espn_cache", "nba", d + ".json"), "w", encoding="utf-8") as f:
            json.dump(sb, f)
    for date in dates:
        src = os.path.join(REPO, "data", "odds_cache", "nba", date + ".json")
        if os.path.exists(src):
            shutil.copy(src, os.path.join(root, "data", "odds_cache", "nba"))
        with open(os.path.join(root, "data", "stats_cache", "nba", date + ".json"), "w", encoding="utf-8") as f:
            json.dump(stats[date], f)
        # pyFull's recorded live inputs for the day (same lines for every
        # model), with the stats the burn-in kept out of them.
        dst = os.path.join(root, "data", "run_inputs", INPUTS_DIR[model], date)
        os.makedirs(dst, exist_ok=True)
        for p in glob.glob(os.path.join(REPO, "data", "run_inputs", "nba", date, "*.json.gz")):
            with gzip.open(p, "rt", encoding="utf-8") as f:
                snap = json.load(f)
            snap["inputs"]["stats"] = {"v": [stats[date], None]}
            for k in [k for k in snap["inputs"] if k.startswith("scoreboard:")]:
                del snap["inputs"][k]        # grade from the final scoreboards
            with gzip.open(os.path.join(dst, os.path.basename(p)), "wt", encoding="utf-8") as f:
                json.dump(snap, f)
    out = os.path.join(root, "out.json")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GMAIL")}
    env["PYFULL_BACKFILL_REPO"] = root
    p = subprocess.run([sys.executable, "replay_season.py", "--from", dates[0], "--to", dates[-1], "--out", out],
                       cwd=os.path.join(root, model, "scripts"), env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(f"--- {model} replay exit {p.returncode}; last lines:")
    print("\n".join((p.stdout + p.stderr).strip().splitlines()[-15:]))
    try:
        return {r["date"]: r for r in json.load(open(out, encoding="utf-8"))["runs"]}
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


def _days(d0, d1):
    import datetime as dt
    a = dt.date(int(d0[:4]), int(d0[4:6]), int(d0[6:]))
    b = dt.date(int(d1[:4]), int(d1[4:6]), int(d1[6:]))
    while a <= b:
        yield a.strftime("%Y%m%d")
        a += dt.timedelta(days=1)


def scoreboard(date):
    sys.path.insert(0, HERE)
    from sources.espn_scoreboard import fetch_scoreboard
    return fetch_scoreboard(date)


def final_scores(sb):
    """{(away, home): (away_score, home_score)} for finished games."""
    out = {}
    for ev in (sb or {}).get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        if not ((comp.get("status") or {}).get("type") or {}).get("completed"):
            continue
        t = {c.get("homeAway"): ((c.get("team") or {}).get("displayName"), int(c.get("score") or 0))
             for c in comp.get("competitors", [])}
        if "away" in t and "home" in t:
            out[(t["away"][0], t["home"][0])] = (t["away"][1], t["home"][1])
    return out


def table(date, runs, finals, total):
    """One day's table; adds fired plays into total[model] and total['all']."""
    lines = [f"## {date}", ""]
    hdr = "| Game | Line (home) |" + (" Final |" if finals else "") + "".join(f" {n} |" for _, n in MODELS)
    lines += [hdr, "|---|---|" + ("---|" if finals else "") + "---|" * len(MODELS)]
    day = {m: [0, 0, 0, 0.0] for m, _ in MODELS + [("all", "")]}
    keys = []
    for r in runs.values():
        for g in (r or {}).get("games", []):
            if (g["away"], g["home"]) not in keys:
                keys.append((g["away"], g["home"]))
    for away, home in keys:
        row, line, picks = [], None, []
        fs = finals.get((away, home))
        for m, _ in MODELS:
            g = next((x for x in (runs.get(m) or {}).get("games", []) if (x["away"], x["home"]) == (away, home)), None)
            if not g or g.get("hS") is None:
                row.append("no projection")
                picks.append(None)
                continue
            line = g.get("line")
            side_home = (g.get("pHomeCover") or 0) >= (g.get("pAwayCover") or 0)
            p = max(g.get("pHomeCover") or 0, g.get("pAwayCover") or 0)
            team = home if side_home else away
            # Fired or not, and the stake, come from the engine itself (sPick /
            # stakeUnits: P(cover) >= probHigh, 2u at the elite cut), never from
            # the rounded percentage printed here.
            fired = g.get("sPick") not in (None, "PASS")
            units = (g.get("stakeUnits") or 1) if fired else 0
            picks.append((side_home, units) if fired else None)
            res = ""
            if fs and line is not None:
                cover = fs[1] - fs[0] + line           # home side vs the line
                res = " PUSH" if cover == 0 else (" W" if (cover > 0) == side_home else " L")
                if fired:
                    _add(day[m], res, units)
            fire = f" **PLAY {units}u**" if fired else " (no play)"
            row.append(f"{g['aS']:.1f}-{g['hS']:.1f} (home {g['hS'] - g['aS']:+.1f}) -> {team} {p:.1%}{fire}{res}")
        # every model fired the same side: smallest stake
        if fs and line is not None and all(picks) and len({sh for sh, _ in picks}) == 1:
            cover = fs[1] - fs[0] + line
            sh = picks[0][0]
            res = " PUSH" if cover == 0 else (" W" if (cover > 0) == sh else " L")
            _add(day["all"], res, min(u for _, u in picks))
        fcol = ""
        if finals:
            fcol = f" {fs[0]}-{fs[1]} (home {fs[1] - fs[0]:+d}) |" if fs else " not final |"
        lines.append(f"| {away} @ {home} | {line if line is not None else '-'} |{fcol} " + " | ".join(row) + " |")
    if finals:
        lines += ["", "Plays (engine-fired, 1u/2u): " + ", ".join(
            _rec(n, day[m]) for m, n in MODELS + [("all", "All agree")])]
    for k, r in day.items():
        total[k] = [x + y for x, y in zip(total[k], r)]
    return lines


def _add(r, res, units):
    r[{" W": 0, " L": 1, " PUSH": 2}[res]] += 1
    r[3] += units if res == " W" else (-1.1 * units if res == " L" else 0)


def _rec(name, r):
    w, l, pu, u = r
    return f"{name} {w}-{l}" + (f"-{pu}" if pu else "") + f" ({u:+.1f}u)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="past day YYYYMMDD (default: today, live)")
    ap.add_argument("--to", help="with --date: replay --date..--to in order (walk-forward)")
    a = ap.parse_args()
    if a.date:
        import datetime as dt
        dates = list(_days(a.date, a.to or a.date))
        stats = {}
        for d in dates:
            stats[d] = fetch_stats(d)
            print(f"stats through the day before {d}: {len(stats[d]['season'])} teams")
        first = dt.date(int(dates[0][:4]), int(dates[0][4:6]), int(dates[0][6:])) - dt.timedelta(days=1)
        boards = {}
        for d in [first.strftime("%Y%m%d")] + dates:
            try:
                boards[d] = scoreboard(d)
            except Exception as e:
                print(f"scoreboard {d} failed: {e}")
        per = {m: replay_model(m, dates, stats, boards) for m, _ in MODELS}
        out = {d: {m: per[m].get(d) for m, _ in MODELS} for d in dates}
        finals = {d: final_scores(boards.get(d)) for d in dates}
    else:
        runs = {m: run_model(m) for m, _ in MODELS}
        d = next((r["date"] for r in runs.values() if r), "?")
        dates, out, finals = [d], {d: runs}, {d: {}}
    total = {m: [0, 0, 0, 0.0] for m, _ in MODELS + [("all", "")]}
    lines = [f"# NBA projections preview {dates[0]}" + (f"-{dates[-1]}" if len(dates) > 1 else "")
             + " (burn-in lowered: MIN_GP 1, picks at 0 GP)", ""]
    for d in dates:
        lines += table(d, out[d], finals[d], total) + [""]
    if len(dates) > 1:
        lines += ["## Total", "", "Plays (engine-fired, 1u/2u): " + ", ".join(
            _rec(n, total[m]) for m, n in MODELS + [("all", "All agree")])]
    text = "\n".join(lines)
    print("\n" + text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main()
