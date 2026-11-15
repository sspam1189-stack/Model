#!/usr/bin/env python3
# pyFullMar19/walk_forward.py
#
# Walk the 2026-03-19 Python full-season model (pyFullMar19/scripts, a verbatim
# copy of PythonFull/scripts at commit 088596411) forward over a date range on
# this season's saved inputs. Testing only: nothing here runs live, and nothing
# is written outside a scratch directory (plus --out, if given).
#
# The old run_daily.main() is run unchanged, once per date, in a scratch copy:
#   - the clock is set to the date;
#   - stats / injuries / player ratings / head-to-head go in through the cache
#     files the old code already looks for (NBAFullseason/data/...);
#   - odds are each game's last line before its tip, from the live runs'
#     recorded inputs (data/run_inputs/nba/<date>/), sign-flipped to the old
#     convention (line > 0 = home favored);
#   - grading and back-to-backs come from the saved ESPN scoreboards.
# The season starts from the default weights, an empty Kalman state and no LR
# model, as the old model would after a reset.
#
# One deliberate change, as in the preview: MIN_GP. The old code hard-codes 15
# games before it projects a team; --min-gp (default 1) lowers that so early
# season can be compared. Use --min-gp 15 for the model exactly as it was.
#
# Usage (from the repo root):
#   python pyFullMar19/walk_forward.py --from 20261023 --to 20261112
import argparse
import datetime as dt
import glob
import gzip
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))
DATA = os.path.join(REPO, "data")


def _days(d0, d1):
    a = dt.datetime.strptime(d0, "%Y%m%d").date()
    b = dt.datetime.strptime(d1, "%Y%m%d").date()
    while a <= b:
        yield a.strftime("%Y%m%d")
        a += dt.timedelta(days=1)


def _prev(d, n=1):
    return (dt.datetime.strptime(d, "%Y%m%d").date() - dt.timedelta(days=n)).strftime("%Y%m%d")


def _snapshots(date):
    out = []
    for p in sorted(glob.glob(os.path.join(DATA, "run_inputs", "nba", date, "*.json.gz"))):
        try:
            with gzip.open(p, "rt", encoding="utf-8") as f:
                out.append(json.load(f))
        except Exception:
            pass
    out.sort(key=lambda s: s.get("startedAt") or "")
    return out


def _val(snap, key):
    rec = (snap.get("inputs") or {}).get(key)
    return rec.get("v") if isinstance(rec, dict) and "v" in rec else None


def _ts(s):
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def odds_for(date, snaps):
    """Each game's last recorded line before its tip, in the OLD sign
    convention (line > 0 = home favored)."""
    games = {}
    for s in snaps:
        t = _ts(s.get("startedAt"))
        for g in _val(s, "odds") or []:
            tip = _ts(g.get("startTimeUTC"))
            if t and tip and t >= tip:
                continue
            games[(g.get("away"), g.get("home"))] = g
    if not games:                                   # no recorded runs: the saved board
        p = os.path.join(DATA, "odds_cache", "nba", date + ".json")
        if os.path.exists(p):
            raw = json.load(open(p, encoding="utf-8"))
            for g in (raw if isinstance(raw, list) else raw.get("games") or raw.get("odds") or []):
                games[(g.get("away"), g.get("home"))] = g
    out = []
    for g in sorted(games.values(), key=lambda g: str(g.get("startTimeUTC") or "")):
        g = dict(g)
        if isinstance(g.get("line"), (int, float)):
            g["line"] = -g["line"]
        out.append(g)
    return out


def stats_for(date, snaps):
    """Team stats through the day before: what the live run recorded, else the
    saved cache, else a fresh fetch (every team, no games-played filter)."""
    for s in reversed(snaps):
        v = _val(s, "stats")
        v = v[0] if isinstance(v, list) and v else v
        if isinstance(v, dict) and len(v.get("season") or {}) >= 20:
            return v, "recorded"
    p = os.path.join(DATA, "stats_cache", "nba", date + ".json")
    if os.path.exists(p):
        v = json.load(open(p, encoding="utf-8"))
        if len(v.get("season") or {}) >= 20:
            return v, "cache"
    sys.path.insert(0, os.path.join(REPO, "pyFull", "scripts"))
    from sources.nba_stats import fetch_nba_stats_enhanced
    return fetch_nba_stats_enhanced(date, min_gp=0), "fetched"


def injuries_for(date, snaps, first_tip):
    """Report, minutes and player ratings from the last run before the first tip."""
    pick = None
    for s in snaps:
        t = _ts(s.get("startedAt"))
        if _val(s, "injury_data") and (pick is None or not first_tip or (t and t < first_tip)):
            pick = s
    if pick:
        inj = _val(pick, "injury_data") or {}
        return ({"report": inj.get("report") or {}, "playerMPG": inj.get("playerMPG") or {}},
                _val(pick, "player_advanced") or {})
    p = os.path.join(DATA, "injury_cache", "nba", date + ".json")
    if os.path.exists(p):
        c = json.load(open(p, encoding="utf-8"))
        return ({"report": c.get("report") or {}, "playerMPG": c.get("playerMPG") or {}},
                c.get("playerAdvanced") or {})
    return {"report": {}, "playerMPG": {}}, {}


def scoreboard(date):
    p = os.path.join(DATA, "espn_cache", "nba", date + ".json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def finals(date):
    out = []
    for ev in (scoreboard(date) or {}).get("events", []):
        comp = (ev.get("competitions") or [None])[0] or {}
        if ((comp.get("status") or {}).get("type") or {}).get("name") not in ("STATUS_FINAL", "STATUS_FINAL_OVERTIME"):
            continue
        cs = comp.get("competitors") or []
        a = next((c for c in cs if c.get("homeAway") == "away"), None)
        h = next((c for c in cs if c.get("homeAway") == "home"), None)
        try:
            out.append({"date": date, "away": a["team"]["displayName"], "home": h["team"]["displayName"],
                        "awayScore": float(a["score"]), "homeScore": float(h["score"])})
        except Exception:
            pass
    return out


def h2h_before(date, season_start):
    """This season's meetings before `date`, in the shape the old
    fetch_h2h_matchups returned (it read the current season's game log)."""
    m = {}
    for d in _days(season_start, _prev(date)):
        for f in finals(d):
            teams = sorted([f["home"], f["away"]])
            rec = m.setdefault("::".join(teams), {"team1": teams[0], "team2": teams[1], "games": []})
            rec["games"].append({"gameId": d + f["away"] + f["home"], "date": d,
                                 "home": {"team": f["home"], "pts": f["homeScore"]},
                                 "away": {"team": f["away"], "pts": f["awayScore"]}})
    return m


RUNNER = r'''
import json, os, sys, time
date = sys.argv[1]
inp = json.load(open(sys.argv[2], encoding="utf-8"))
time.sleep = lambda *_a, **_k: None
import datetime as _dt
import run_daily as rd
def _off(n=0):
    return (_dt.datetime.strptime(date, "%Y%m%d") - _dt.timedelta(days=n)).strftime("%Y%m%d")
rd.today_central_yyyymmdd = lambda: date
rd.yyyymmdd_from_central_offset = _off
rd.fetch_todays_odds = lambda: inp["odds"]
rd.fetch_ats_trends = lambda: {}
rd.fetch_ou_trends = lambda: {}
rd.detect_b2b = lambda: set(inp["b2b"])
rd.fetch_scoreboard = lambda d=None: inp["scoreboards"].get(d or date)
rd.send_email = lambda *_a, **_k: None
try:
    rd.main()
except Exception as e:                  # the run is saved before the email is built
    print(f"[walk-forward] after-save step failed (ignored): {type(e).__name__}: {e}")
if inp.get("final"):                    # last date: grade it too
    st = rd.load_store(); rd.grade_date_in_store(st, date)
'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d0", required=True)
    ap.add_argument("--to", dest="d1", required=True)
    ap.add_argument("--min-gp", type=int, default=1,
                    help="games before a team is projected (the old code hard-codes 15)")
    ap.add_argument("--out", help="write the resulting history.json here")
    ap.add_argument("--keep", action="store_true", help="keep the scratch directory")
    a = ap.parse_args()

    root = tempfile.mkdtemp(prefix="mar19-")
    scripts = os.path.join(root, "PythonFull", "scripts")
    shutil.copytree(os.path.join(HERE, "scripts"), scripts, ignore=shutil.ignore_patterns("__pycache__"))
    os.makedirs(os.path.join(root, "PythonFull", "data"), exist_ok=True)
    for sub in ("stats_cache", "injury_cache"):
        os.makedirs(os.path.join(root, "NBAFullseason", "data", sub), exist_ok=True)
    eng = os.path.join(scripts, "model_engine.py")
    src = open(eng, encoding="utf-8").read()
    assert "MIN_GP = 15" in src
    open(eng, "w", encoding="utf-8").write(src.replace("MIN_GP = 15", f"MIN_GP = {a.min_gp}"))
    open(os.path.join(scripts, "_wf_runner.py"), "w", encoding="utf-8").write(RUNNER)

    dates = list(_days(a.d0, a.d1))
    env = {k: v for k, v in os.environ.items() if not k.startswith("GMAIL")}
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    for i, date in enumerate(dates):
        snaps = _snapshots(date)
        odds = odds_for(date, snaps)
        stats, src_name = stats_for(date, snaps)
        tips = [t for t in (_ts(g.get("startTimeUTC")) for g in odds) if t]
        inj, padv = injuries_for(date, snaps, min(tips) if tips else None)
        with open(os.path.join(root, "NBAFullseason", "data", "stats_cache", date + ".json"), "w", encoding="utf-8") as f:
            json.dump(stats, f)
        with open(os.path.join(root, "NBAFullseason", "data", "injury_cache", date + ".json"), "w", encoding="utf-8") as f:
            json.dump({"injuryData": inj, "playerAdvanced": padv, "h2hMatchups": h2h_before(date, dates[0])}, f)
        sbs = {d: scoreboard(d) for d in [date] + [_prev(date, n) for n in range(1, 6)]}
        b2b = sorted({t for f in finals(_prev(date)) for t in (f["away"], f["home"])})
        inp = os.path.join(root, "inputs.json")
        with open(inp, "w", encoding="utf-8") as f:
            json.dump({"odds": odds, "b2b": b2b, "scoreboards": sbs, "final": i == len(dates) - 1}, f)
        p = subprocess.run([sys.executable, "_wf_runner.py", date, inp], cwd=scripts, env=env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        tail = [l for l in (p.stdout + p.stderr).strip().splitlines()
                if any(k in l for k in ("tuned", "LR model", "lr_model", "Traceback", "Error", "walk-forward", "probHigh"))][-4:]
        print(f"[mar19] {date}: {len(odds)} games, stats {src_name}, exit {p.returncode}" + ("".join("\n    " + l.strip() for l in tail)))
        if p.returncode != 0:
            print((p.stdout + p.stderr)[-2000:])
            sys.exit(1)

    hist = os.path.join(root, "PythonFull", "data", "history.json")
    store = json.load(open(hist, encoding="utf-8"))
    report(store, dates)
    if a.out:
        shutil.copy(hist, a.out)
        print(f"\nhistory -> {a.out}")
    if a.keep:
        print(f"scratch kept: {root}")
    else:
        shutil.rmtree(root, ignore_errors=True)


def _rec(rows):
    w = sum(1 for s, r in rows if r == "W"); l = sum(1 for s, r in rows if r == "L")
    u = sum(s if r == "W" else -1.1 * s for s, r in rows if r in ("W", "L"))
    return f"{w}-{l} ({u:+.1f}u)"


def report(store, dates):
    """Per-day table and totals. Lines are printed in today's convention
    (home line, negative = home favored)."""
    official, r5865, errs, vegas = [], [], [], []
    print(f"\n# 2026-03-19 model, walk-forward {dates[0]}-{dates[-1]}\n")
    for run in store.get("runs", []):
        if run["date"] not in dates:
            continue
        day_off, day_58 = [], []
        print(f"## {run['date']}\n\n| Game | Line (home) | Final | 2026-03-19 model |\n|---|---|---|---|")
        for g in run.get("games", []):
            name = f"{g.get('away')} @ {g.get('home')}"
            if g.get("status") in ("MISSING_ODDS", "SKIPPED") or not isinstance(g.get("hS"), (int, float)):
                print(f"| {name} | | | skipped ({g.get('status')}) |"); continue
            line = -g["line"]                                   # today's convention
            ph = g.get("pHomeCover")
            side_home = (ph if ph is not None else (0.5 if g["margin"] == 0 else float(g["margin"] > 0))) >= 0.5
            pc = max(ph, 1 - ph) if ph is not None else None
            fin, res = "", ""
            has = isinstance(g.get("homeScore"), (int, float)) and isinstance(g.get("awayScore"), (int, float))
            if has:
                act = g["homeScore"] - g["awayScore"]
                cov = (act + line) * (1 if side_home else -1)
                res = "W" if cov > 0 else "L" if cov < 0 else "P"
                fin = f"{g['awayScore']:.0f}-{g['homeScore']:.0f} (home {act:+.0f})"
                errs.append(abs(act - (g["hS"] - g["aS"]))); vegas.append(abs(act + line))
            st58 = 0 if (pc is None or abs(line) >= 18) else 2 if pc >= 0.65 else 1 if pc >= 0.58 else 0
            off = g.get("sPick") and g["sPick"] != "PASS"
            tag = []
            if off:
                r = {"WIN": "W", "LOSS": "L", "PUSH": "P"}.get(g.get("sResult"), res)
                tag.append(f"**OFFICIAL 1u** {r}".strip());
                if has: day_off.append((1, r))
            elif g.get("lrVetoed"):
                tag.append("(LR veto)")
            if st58:
                tag.append(f"58/65 rule {st58}u {res}".strip())
                if has: day_58.append((st58, res))
            team = g["home"] if side_home else g["away"]
            pcs = f"{pc * 100:.1f}%" if pc is not None else "n/a"
            print(f"| {name} | {line:+g} | {fin} | {g['aS']}-{g['hS']} (home {g['hS'] - g['aS']:+.1f}) -> {team} {pcs} {' · '.join(tag) or '(no play)'} |")
        print(f"\nOfficial (model's own rule): {_rec(day_off)} | 58/65 rule: {_rec(day_58)}\n")
        official += day_off; r5865 += day_58
    w = store.get("weights") or {}
    print("## Total\n")
    print(f"Official picks (P >= probHigh, edge <= 10, |line| < 12, LR veto; flat 1u): {_rec(official)}")
    print(f"58/65 rule on its probabilities (|line| < 18): {_rec(r5865)} | 2u {_rec([x for x in r5865 if x[0] == 2])} | 1u {_rec([x for x in r5865 if x[0] == 1])}")
    if errs:
        print(f"Average margin miss: {sum(errs) / len(errs):.2f} over {len(errs)} games (Vegas {sum(vegas) / len(vegas):.2f})")
    print("Final weights: " + ", ".join(f"{k} {w.get(k)}" for k in ("wTS", "wTO", "wORR", "wNET", "hca", "constant", "paceAdj", "probHigh")))


if __name__ == "__main__":
    main()
