#!/usr/bin/env python3
# scripts/replay_season.py
# Backfill through the live code. Runs run_daily.main() -- unchanged -- once
# per date, with the clock set to that date's run time and every outside
# input served from the data live itself reads and saves:
#
#   - the inputs a live run recorded that day (data/run_inputs/nba/<date>/,
#     see run_inputs.py): the last run that started before the first tip;
#   - otherwise the repo's saved data for that date, in the shape live reads
#     it: stats_cache (through the day before), odds_cache (falling back to
#     the archived line), ESPN scoreboards (tip times, finals for grading,
#     yesterday's games for back-to-backs) and injury_cache (report, minutes,
#     player stats, and the previous 10 days for returning-star detection).
#     Display-only feeds (TeamRankings trends) are empty; the out-for-season
#     list is empty where it was not saved.
#
# Same code path as live: grading, Kalman, self-tune, fire floor, playoff
# overrides and pick logic all come from run_daily. It runs in a scratch copy
# of core/ + pyFull/scripts/, so the real history.json and caches are never
# touched. The season starts from the default weights and an empty Kalman
# state, as live does after a rollover.
#
# Usage (from pyFull/scripts):
#   python replay_season.py --season 2025-26 --out ../data/backtests/x.json
#   python replay_season.py --from 20251021 --to 20251130
#   python replay_season.py --season 2026-27 --compare   # diff against live history.json

import argparse
import datetime as dt
import glob
import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
# The inner process runs in the scratch copy; it reads saved data from the real repo.
REPO = os.environ.get("PYFULL_BACKFILL_REPO") or os.path.normpath(os.path.join(HERE, "..", ".."))
DATA = os.path.join(REPO, "data")
INPUTS = os.path.join(DATA, "run_inputs", "nba-dnet")
LIVE_HISTORY = os.path.join(REPO, "pyFullDNET", "data", "history.json")
ARCHIVE = os.path.join(REPO, "pyFull", "data", "archive")
TZ = ZoneInfo("America/Chicago")
RUN_TIME = (10, 45)  # first live run of the day (Central)
PLAYOFF_STARTS = {"2025-26": "20260414"}  # past seasons; the current one uses season_type.PLAYOFF_START
CHECK_FIELDS = ("status", "sPick", "sConf", "pCover", "hS", "aS", "line", "total",
                "homeScore", "awayScore", "sResult", "stakeUnits")


def season_of(d):
    y, m = int(d[:4]), int(d[4:6])
    s = y if m >= 10 else y - 1
    return f"{s}-{str(s + 1)[2:]}"


def _days(d0, d1):
    a = dt.date(int(d0[:4]), int(d0[4:6]), int(d0[6:]))
    b = dt.date(int(d1[:4]), int(d1[4:6]), int(d1[6:]))
    while a <= b:
        yield a.strftime("%Y%m%d")
        a += dt.timedelta(days=1)


def _json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _gz(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


# -- inputs from saved data ---------------------------------------------------

def _espn(d):
    return _json(os.path.join(DATA, "espn_cache", "nba", f"{d}.json"), {}) or {}


def _compact(sb, as_scheduled=False):
    events = []
    for ev in sb.get("events", []):
        comps = []
        for comp in (ev.get("competitions") or [])[:1]:
            st = ((comp.get("status") or {}).get("type") or {})
            status = ({"name": "STATUS_SCHEDULED", "completed": False, "state": "pre"} if as_scheduled
                      else {k: st.get(k) for k in ("name", "completed", "state")})
            comps.append({
                "date": comp.get("date"), "status": {"type": status},
                "competitors": [{"homeAway": c.get("homeAway"),
                                 "score": "0" if as_scheduled else c.get("score"),
                                 "team": {"displayName": (c.get("team") or {}).get("displayName")}}
                                for c in comp.get("competitors", [])],
            })
        events.append({"date": ev.get("date"), "competitions": comps})
    return {"day": sb.get("day"), "events": events}


def _slate(d):
    out = []
    for ev in _espn(d).get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        t = {c.get("homeAway"): (c.get("team") or {}).get("displayName") for c in comp.get("competitors", [])}
        if t.get("away") and t.get("home"):
            out.append((t["away"], t["home"], comp.get("date") or ev.get("date")))
    return out


_archived = {}


def _archived_lines(d):
    s = season_of(d)
    if s not in _archived:
        h = _json(os.path.join(ARCHIVE, f"history-{s}.json"), {}) or {}
        _archived[s] = {r["date"]: {(g["away"], g["home"]): g for g in r.get("games", [])}
                        for r in h.get("runs", [])}
    return _archived[s].get(d, {})


def _odds(d):
    cached = _json(os.path.join(DATA, "odds_cache", "nba", f"{d}.json"), {}) or {}
    arch = _archived_lines(d)
    out = []
    for away, home, start in _slate(d):
        o = cached.get(f"{away}@{home}") or {}
        if o.get("line") is None and (away, home) in arch:
            g = arch[(away, home)]
            o = {"line": g.get("line"), "total": g.get("total"), "_book": g.get("_book")}
        if o.get("line") is None:
            continue
        out.append({"away": away, "home": home, "line": o.get("line"), "total": o.get("total"),
                    "_book": o.get("_book"), "startTimeUTC": start})
    return out


def _stats(d):
    c = _json(os.path.join(DATA, "stats_cache", "nba", f"{d}.json"))
    if not c or not c.get("season"):
        return [None, f"stats incomplete: no saved stats for {d}"]
    sys.path.insert(0, HERE)
    from defaults import ENGINE_CONFIG
    min_gp = ENGINE_CONFIG.get("MIN_GP", 10)   # as run_daily passes to nba_stats
    season = {k: v for k, v in c["season"].items() if v.get("GP", 0) >= min_gp}
    if len(season) < 20:
        return [None, f"stats incomplete: only {len(season)} teams with >= {min_gp} GP"]
    return [{"season": season, "last10": c.get("last10"), "home": c.get("home"), "away": c.get("away")}, None]


def _injury_cache(d):
    return _json(os.path.join(DATA, "injury_cache", "nba", f"{d}.json"), {}) or {}


def _recent_injury_caches(d):
    inj_dir = os.path.join(DATA, "injury_cache", "nba")
    files = sorted((f for f in os.listdir(inj_dir) if f.endswith(".json") and f < d + ".json"),
                   reverse=True)[:10] if os.path.isdir(inj_dir) else []
    out = {}
    for f in files:
        c = _json(os.path.join(inj_dir, f), {}) or {}
        rep = c.get("report") or (c.get("injuryData") or {}).get("report") or {}
        if rep:
            out[f[:-5]] = rep
    return out


def _b2b(d):
    y = (dt.date(int(d[:4]), int(d[4:6]), int(d[6:])) - dt.timedelta(days=1)).strftime("%Y%m%d")
    teams = set()
    for ev in _espn(y).get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        if ((comp.get("status") or {}).get("type") or {}).get("completed"):
            teams |= {(c.get("team") or {}).get("displayName") for c in comp.get("competitors", [])}
    teams.discard(None)
    return teams


def _to_rec(v):
    sys.path.insert(0, HERE)
    import run_inputs
    return {"v": run_inputs._to_json(v)}


def built_snapshot(d):
    started = dt.datetime(int(d[:4]), int(d[4:6]), int(d[6:]), *RUN_TIME, tzinfo=TZ)
    inj = _injury_cache(d)
    return {
        "date": d, "startedAt": started.isoformat(), "status": "ok", "source": "saved data",
        "inputs": {
            "stats": _to_rec(_stats(d)),
            "odds": _to_rec(_odds(d)),
            "scoreboard_today": _to_rec(_compact(_espn(d), as_scheduled=True)),
            "ats_trends": _to_rec({}), "ou_trends": _to_rec({}),
            "injury_cache_today": _to_rec({}),
            "injury_data": _to_rec({"report": inj.get("report") or {}, "playerMPG": inj.get("playerMPG") or {}}),
            "player_advanced": _to_rec(inj.get("playerAdvanced") or {}),
            "b2b_teams": _to_rec(_b2b(d)),
            "recent_injury_caches": _to_rec(_recent_injury_caches(d)),
            "out_for_season": _to_rec(set(inj.get("ofsPlayers") or [])),
        },
    }


def _grading_scoreboard(key):
    # scoreboard:<date> -- final scores for grading earlier days
    if key.startswith("scoreboard:"):
        return {"v": _compact(_espn(key.split(":", 1)[1]))}
    raise KeyError(key)


def recorded_snapshot(d):
    """The last recorded live run that started before the day's first tip."""
    paths = sorted(glob.glob(os.path.join(INPUTS, d, "*.json.gz")))
    if not paths:
        return None
    tips = [s for _, _, s in _slate(d) if s]
    first_tip = min(tips) if tips else None
    best = None
    for p in paths:
        snap = _gz(p)
        started = dt.datetime.fromisoformat(snap["startedAt"])
        if first_tip and started >= dt.datetime.fromisoformat(first_tip.replace("Z", "+00:00")):
            break
        best = snap
    snap = best or _gz(paths[0])
    snap["source"] = "recorded " + snap["startedAt"][11:19]
    return snap


# -- driver --------------------------------------------------------------------

def _inner(d0, d1):
    """Runs inside the scratch copy: one run_daily per date."""
    sys.path.insert(0, os.getcwd())
    import run_inputs
    import run_daily
    import core.kalman_state as ks
    import core.store as st
    import sources.season_type as stype
    frozen = (run_daily, ks, st)
    current_ps = stype.PLAYOFF_START
    for d in _days(d0, d1):
        if not _slate(d) and not glob.glob(os.path.join(INPUTS, d, "*.json.gz")):
            continue  # no NBA games
        snap = recorded_snapshot(d) or built_snapshot(d)
        snap["provider"] = _grading_scoreboard
        ps = PLAYOFF_STARTS.get(season_of(d), current_ps)
        stype.PLAYOFF_START = run_daily.PLAYOFF_START = ps
        run_inputs.begin_replay(snap, modules=frozen)
        try:
            run_daily.main()
            outcome = "ok"
        except Exception as e:
            outcome = f"error: {e}"
        finally:
            run_inputs.end_replay(modules=frozen)
        print(f"[backfill] {d} ({snap['source']}) {outcome}", flush=True)


def _setup(root, d0):
    shutil.copytree(os.path.join(REPO, "core"), os.path.join(root, "core"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(HERE, os.path.join(root, "pyFull", "scripts"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    for d in ("pyFull/data", "PythonDashboard/data", "data/injury_cache/nba",
              "data/stats_cache/nba", "data/espn_cache/nba"):
        os.makedirs(os.path.join(root, d), exist_ok=True)
    sys.path.insert(0, HERE)
    from defaults import DEFAULT_W, DEFAULT_W_VAR
    start = {"runs": [], "weights": dict(DEFAULT_W), "weightsVar": dict(DEFAULT_W_VAR)}
    json.dump(start, open(os.path.join(root, "pyFull", "data", "history.json"), "w"), indent=2)


def _compare(replayed, d0, d1):
    live = {r["date"]: r for r in (_json(LIVE_HISTORY, {}) or {}).get("runs", [])}
    mine = {r["date"]: r for r in replayed.get("runs", [])}
    diffs, games = [], 0
    for d in _days(d0, d1):
        lg = {(g.get("away"), g.get("home")): g for g in live.get(d, {}).get("games", [])}
        mg = {(g.get("away"), g.get("home")): g for g in mine.get(d, {}).get("games", [])}
        for k in sorted(set(lg) | set(mg), key=str):
            games += 1
            if k not in lg or k not in mg:
                diffs.append(f"{d} {k[0]} @ {k[1]}: only in {'live' if k in lg else 'backfill'}")
                continue
            for f in CHECK_FIELDS:
                if lg[k].get(f) != mg[k].get(f):
                    diffs.append(f"{d} {k[0]} @ {k[1]}: {f} live={lg[k].get(f)!r} backfill={mg[k].get(f)!r}")
    return games, diffs


SEASONS = {"2025-26": ("20251021", "20260615")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", help="e.g. 2025-26")
    ap.add_argument("--from", dest="d0")
    ap.add_argument("--to", dest="d1")
    ap.add_argument("--out", help="write the backfilled history.json here")
    ap.add_argument("--compare", action="store_true", help="diff against live history.json")
    ap.add_argument("--inner", nargs=2, help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.inner:
        return _inner(*a.inner)

    d0, d1 = SEASONS.get(a.season, (None, None)) if a.season else (None, None)
    d0, d1 = a.d0 or d0, a.d1 or d1
    if not d0:
        ap.error("give --season or --from")
    d1 = d1 or (dt.datetime.now(TZ) - dt.timedelta(days=1)).strftime("%Y%m%d")
    out = a.out or os.path.abspath(f"backfill-{d0}-{d1}.json")

    root = tempfile.mkdtemp(prefix="pyfull-backfill-")
    try:
        _setup(root, d0)
        scripts = os.path.join(root, "pyFull", "scripts")
        print(f"Backfilling {d0} .. {d1} through run_daily (one run per date) in {root}")
        env = dict(os.environ, PYTHONHASHSEED="0", PYFULL_BACKFILL_REPO=REPO)
        rc = subprocess.call([sys.executable, os.path.join(scripts, "replay_season.py"), "--inner", d0, d1],
                             cwd=scripts, env=env)
        if rc != 0:
            print("Backfill process failed")
            return rc
        result = _json(os.path.join(root, "pyFull", "data", "history.json"), {})
        json.dump(result, open(out, "w", encoding="utf-8"), indent=2)
        print(f"Backfilled history -> {out}")
        if a.compare:
            games, diffs = _compare(result, d0, d1)
            print(f"\n{'MATCH' if not diffs else 'DIFFERENCES'}: {len(diffs)} across {games} games")
            for line in diffs[:200]:
                print("  " + line)
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main() or 0)
