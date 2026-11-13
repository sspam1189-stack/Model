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
# dNET (pyFullDNET) still runs live but is hidden from the preview (2026-11-02).
# 2026-11-13: the two additions to KalmanOff run as separate models, each in
# its own column and neither part of the AGREE groups:
#   pyFullPrior = KalmanOff + last season's ratings as the starting point
#   pyFullInj   = KalmanOff + injuries from game 1
MODELS = [("pyFull", "NBA Full Season"), ("pyFullKalmanOff", "NBA Full KalmanOff"),
          ("pyFullKalmanEarly", "NBA Full KalmanEarly"), ("pyFullPrior", "KalmanOff + Last Season"),
          ("pyFullInj", "KalmanOff + Injuries")]
SKIP = shutil.ignore_patterns("__pycache__", "archive", "run_inputs", "odds_cache", "stats_cache")
INPUTS_DIR = {"pyFull": "nba", "pyFullKalmanOff": "nba-kalmanoff", "pyFullDNET": "nba-dnet",
              "pyFullKalmanEarly": "nba-kalmanearly", "pyFullPrior": "nba-prior", "pyFullInj": "nba-inj"}
# Consensus records: every listed model fired the same side, at the smallest stake.
# Optional 4th field: every member must be at least this stake (2 = all at 2u).
AGREE = [("k3", "3 Kalman agree", ["pyFull", "pyFullKalmanOff", "pyFullKalmanEarly"]),
         ("fsko2", "Full Season + KalmanOff both 2u", ["pyFull", "pyFullKalmanOff"], 2)]
TRACKED = MODELS + [(a[0], a[1]) for a in AGREE]
# --dashboard: first day of the season's replay-graded record.
SEASON_START = "20261023"


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
    for sub in ("espn_cache", "injury_cache", "priors"):
        src = os.path.join(REPO, "data", sub, "nba")
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(root, "data", sub, "nba"))
    d = os.path.join(root, model, "scripts", "defaults.py")
    s = open(d, encoding="utf-8").read()
    # Kalman offsets count only from the model's own projection start (its
    # real MIN_GP), as live -- the preview lowers MIN_GP to project earlier,
    # which must not let a model apply Kalman before it really would.
    real = re.search(r'"MIN_GP": (\d+)', s)
    # Same for Kalman learning (self-tune learns from every game).
    if real and "KALMAN_APPLY_MIN_GP" not in s:
        s = s.replace('"PICKS_MIN_GP"', f'"KALMAN_APPLY_MIN_GP": {real.group(1)},\n    "PICKS_MIN_GP"', 1)
    if real and "LEARN_MIN_GP" not in s:
        s = s.replace('"PICKS_MIN_GP"', f'"LEARN_MIN_GP": {real.group(1)},\n    "PICKS_MIN_GP"', 1)
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
    day = {m: [0, 0, 0, 0.0] for m, _ in TRACKED}
    agree_plays = {a[0]: [] for a in AGREE}
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
            picks.append((side_home, units, f"{team} {_line(line, side_home)}") if fired else None)
            res = ""
            if fs and line is not None:
                cover = fs[1] - fs[0] + line           # home side vs the line
                res = " PUSH" if cover == 0 else (" W" if (cover > 0) == side_home else " L")
                if fired:
                    _add(day[m], res, units)
            fire = f" **PLAY {units}u**" if fired else " (no play)"
            row.append(f"{g['aS']:.1f}-{g['hS']:.1f} (home {g['hS'] - g['aS']:+.1f}) -> {team} {p:.1%}{fire}{res}")
        # consensus groups: every model in the group fired the same side, smallest stake
        by_model = dict(zip([m for m, _ in MODELS], picks))
        for k, _, members, *rest in AGREE:
            ps = [by_model.get(m) for m in members]
            if not all(ps) or len({p[0] for p in ps}) != 1:
                continue
            if rest and min(p[1] for p in ps) < rest[0]:
                continue
            units = min(p[1] for p in ps)
            res = ""
            if fs and line is not None:
                cover = fs[1] - fs[0] + line
                res = " PUSH" if cover == 0 else (" W" if (cover > 0) == ps[0][0] else " L")
                _add(day[k], res, units)
            agree_plays[k].append(f"{ps[0][2]} {units}u{res}")
        fcol = ""
        if finals:
            fcol = f" {fs[0]}-{fs[1]} (home {fs[1] - fs[0]:+d}) |" if fs else " not final |"
        lines.append(f"| {away} @ {home} | {line if line is not None else '-'} |{fcol} " + " | ".join(row) + " |")
    lines.append("")
    for k, n, *_ in AGREE:
        lines.append(f"{n}: " + (", ".join(agree_plays[k]) or "none"))
    if finals:
        lines += ["", "Plays (engine-fired, 1u/2u): " + ", ".join(
            _rec(n, day[m]) for m, n in TRACKED)]
    for k, r in day.items():
        total[k] = [x + y for x, y in zip(total[k], r)]
    return lines


def _line(line, side_home):
    if line is None:
        return ""
    v = line if side_home else -line
    return f"{v:+g}" if v else "PK"


def _add(r, res, units):
    r[{" W": 0, " L": 1, " PUSH": 2}[res]] += 1
    r[3] += units if res == " W" else (-1.1 * units if res == " L" else 0)


def _rec(name, r):
    w, l, pu, u = r
    return f"{name} {w}-{l}" + (f"-{pu}" if pu else "") + f" ({u:+.1f}u)"


def seed_history(model, runs, dates):
    """Write the walk-forward days into the model's live history.json as
    burn-in runs (projected and graded, no official picks), replacing the
    placeholder runs for those dates, so live self-tune / Kalman learn from
    them like every other graded game."""
    path = os.path.join(REPO, model, "data", "history.json")
    raw = open(path, encoding="utf-8", newline="").read()
    store = json.loads(raw)
    by_date = {r["date"]: r for r in store.get("runs", [])}
    n = 0
    for d in dates:
        r = runs.get(d)
        if not r:
            continue
        for g in r.get("games", []):
            g["burnIn"] = True
            g["sPick"], g["sConf"], g["pCover"] = "PASS", "low", None
            g["oPick"], g["oConf"], g["pOU"] = "PASS", "low", None
            g.pop("stakeUnits", None)
            g["note"] = "Seeded 2026-10-30 from the walk-forward preview (stats through the day before)"
        r["burnIn"] = False
        by_date[d] = r
        n += len(r.get("games", []))
    store["runs"] = [by_date[k] for k in sorted(by_date)]
    out = json.dumps(store, indent=2)
    if "\r\n" in raw:
        out = out.replace("\n", "\r\n")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(out + ("\r\n" if raw.endswith("\r\n") else "\n" if raw.endswith("\n") else ""))
    print(f"[seed] {model}: {n} games over {len(dates)} day(s) -> {os.path.relpath(path, REPO)}")


def dashboard_games(runs, finals):
    """One replayed day for the dashboard: each model's lean, P(cover), stake
    (0 = no play) and ATS result -- the same engine fields table() grades."""
    keys = []
    for r in runs.values():
        for g in (r or {}).get("games", []):
            if (g["away"], g["home"]) not in keys:
                keys.append((g["away"], g["home"]))
    out = []
    for away, home in keys:
        fs = finals.get((away, home))
        row = {"away": away, "home": home, "line": None, "start": None,
               "final": list(fs) if fs else None, "models": {}}
        for m, _ in MODELS:
            g = next((x for x in (runs.get(m) or {}).get("games", [])
                      if (x["away"], x["home"]) == (away, home)), None)
            if not g or g.get("hS") is None:
                continue
            line = g.get("line")
            if line is not None:
                row["line"] = line
            row["start"] = row["start"] or g.get("startTimeUTC")
            side_home = (g.get("pHomeCover") or 0) >= (g.get("pAwayCover") or 0)
            fired = g.get("sPick") not in (None, "PASS")
            res = None
            if fs and line is not None:
                cover = fs[1] - fs[0] + line
                res = "P" if cover == 0 else ("W" if (cover > 0) == side_home else "L")
            row["models"][m] = {
                "aS": round(g["aS"], 1), "hS": round(g["hS"], 1), "line": line,
                "side": "home" if side_home else "away",
                "p": round(max(g.get("pHomeCover") or 0, g.get("pAwayCover") or 0), 4),
                "units": (g.get("stakeUnits") or 1) if fired else 0,
                "result": res,
            }
        out.append(row)
    return out


def build_dashboard(path, since, force=False):
    """Replay-graded record for the dashboard's NBA Preview tab: the season
    walk-forward replay (since..yesterday, CT), exactly what --date/--to prints.
    Today's games come from the daily-run files in the browser. Skips the
    replay when the feed already covers yesterday with every game final."""
    import datetime as dt
    from zoneinfo import ZoneInfo
    now = dt.datetime.now(ZoneInfo("America/Chicago"))
    yday = (now.date() - dt.timedelta(days=1)).strftime("%Y%m%d")
    if since > yday:
        print("[dashboard] season has not started -- nothing to replay")
        return
    try:
        old = json.load(open(path, encoding="utf-8"))
    except Exception:
        old = None
    if not force and old and old.get("through") == yday and old.get("since") == since \
            and all(g.get("final") for d in old.get("days", []) if d["date"] == yday for g in d["games"]):
        print(f"[dashboard] feed already covers {yday}, all final -- skipping replay")
        return
    dates = list(_days(since, yday))
    stats = {d: fetch_stats(d) for d in dates}
    first = dt.date(int(dates[0][:4]), int(dates[0][4:6]), int(dates[0][6:])) - dt.timedelta(days=1)
    boards = {}
    for d in [first.strftime("%Y%m%d")] + dates:
        try:
            boards[d] = scoreboard(d)
        except Exception as e:
            print(f"scoreboard {d} failed: {e}")
    per = {m: replay_model(m, dates, stats, boards) for m, _ in MODELS}
    feed = {
        "generated": now.isoformat(timespec="seconds"), "since": since, "through": yday,
        "days": [{"date": d, "games": dashboard_games({m: per[m].get(d) for m, _ in MODELS},
                                                      final_scores(boards.get(d)))} for d in dates],
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(feed, f, separators=(",", ":"))
    print(f"[dashboard] replayed {dates[0]}..{yday}: {sum(len(d['games']) for d in feed['days'])} games -> {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dashboard", metavar="PATH",
                    help="write the replay-graded season record for the dashboard to PATH")
    ap.add_argument("--since", default=SEASON_START, help="with --dashboard: first day (YYYYMMDD)")
    ap.add_argument("--force", action="store_true", help="with --dashboard: replay even if up to date")
    ap.add_argument("--date", help="past day YYYYMMDD (default: today, live)")
    ap.add_argument("--to", help="with --date: replay --date..--to in order (walk-forward)")
    ap.add_argument("--only", metavar="MODEL",
                    help="with --date/--to: replay (and --seed) just this model, e.g. pyFullPrior")
    ap.add_argument("--seed", action="store_true",
                    help="with --date/--to: write the replayed days into each model's live history.json")
    a = ap.parse_args()
    if a.only:
        global MODELS, TRACKED, AGREE
        MODELS = [m for m in MODELS if m[0] == a.only]
        if not MODELS:
            sys.exit(f"--only: unknown model {a.only}")
        AGREE = []
        TRACKED = list(MODELS)
    if a.dashboard:
        return build_dashboard(a.dashboard, a.since, a.force)
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
        if a.seed:
            for m, _ in MODELS:
                seed_history(m, per[m], dates)
        out = {d: {m: per[m].get(d) for m, _ in MODELS} for d in dates}
        finals = {d: final_scores(boards.get(d)) for d in dates}
    else:
        runs = {m: run_model(m) for m, _ in MODELS}
        d = next((r["date"] for r in runs.values() if r), "?")
        dates, out, finals = [d], {d: runs}, {d: {}}
    total = {m: [0, 0, 0, 0.0] for m, _ in TRACKED}
    lines = [f"# NBA projections preview {dates[0]}" + (f"-{dates[-1]}" if len(dates) > 1 else "")
             + " (burn-in lowered: MIN_GP 1, picks at 0 GP)", ""]
    for d in dates:
        lines += table(d, out[d], finals[d], total) + [""]
    if len(dates) > 1:
        lines += ["## Total", "", "Plays (engine-fired, 1u/2u): " + ", ".join(
            _rec(n, total[m]) for m, n in TRACKED)]
    text = "\n".join(lines)
    print("\n" + text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main()
