#!/usr/bin/env python3
# scripts/calibrate_probhigh.py
#
# Offline uncensored walk-forward probHigh sweep for the WNBA full-season spread
# model. The forward ATS validation the historical-odds paywall blocked earlier
# (see defaults.py header) is possible on the live season because every day's
# stats/odds/ESPN finals are cached on disk.
#
# It reproduces the real 2026 season day-by-day from those caches, running the
# SAME engine + Kalman + self-tune trajectory as run_daily/backfill — but
# captures EVERY spread candidate (the engine's leaned side + its pCover)
# regardless of the shipped probHigh, then grades each against the closing line.
# This lifts the right-censoring in the live store (which only keeps fired picks
# >=probHigh), so the P(cover) cutoff can be swept DOWN as well as up.
#
# Writes nothing to the production store — Kalman/store paths are redirected to
# a temp dir; it only reads the read-only caches.
#
# Usage:  python scripts/calibrate_probhigh.py

import os, sys, json, datetime, tempfile

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.join(SCRIPTS, "..", ".."))

# Redirect persistence to a temp dir BEFORE importing anything that binds paths.
SCRATCH = os.environ.get("SWEEP_SCRATCH") or tempfile.mkdtemp(prefix="wnba_sweep_")
os.makedirs(SCRATCH, exist_ok=True)
import core.store as _cstore
_cstore.DATA_PATH = os.path.join(SCRATCH, "_sweep_store.json")
import kalman_state as _ks
_ks._ks.STATE_PATH = os.path.join(SCRATCH, "_sweep_kalman.json")

from sources.blend_stats import blend_base, blend_for_game
from sources.espn_scoreboard import extract_final_scores
from sources.rest_detect import apply_b2b_adjustment
from sources.odds_batch_historical import fetch_odds_for_day
from model_engine import load_defaults, get_avgs, analyze_game
from self_tune import tune_weights, compute_residual_var
from kalman_state import (initialize_kalman, apply_daily_drift, batch_update,
                          prune_processed_games)
from backfill_last_n_days import pick_home_away_from_scoreboard_event

ABS_LINE_CAP = 13  # engine spread filter (exclusive); the only non-prob filter
STATS_CACHE_DIR = os.path.join(SCRIPTS, "..", "data", "stats_cache", "wnba")
ESPN_CACHE_DIR = os.path.join(SCRIPTS, "..", "data", "espn_cache", "wnba")


def fetch_scoreboard_cached(date):
    """Read the on-disk ESPN cache directly (fetch_scoreboard skips its cache
    for the container's 'today', but every day in range is already cached)."""
    disk = os.path.join(ESPN_CACHE_DIR, date + ".json")
    with open(disk, "r") as f:
        return json.load(f)


def get_stats_for_date(date):
    """Read the on-disk enhanced stats cache (network is blocked)."""
    disk = os.path.join(STATS_CACHE_DIR, date + ".json")
    with open(disk, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not (raw.get("season") and raw.get("last10")):
        raise RuntimeError(f"cache for {date} not in enhanced format")
    return raw

def grade_lean(home_s, away_s, line, side):
    """Grade the engine's leaned side against the closing line. Mirrors
    backfill.grade_spread: val = margin_home + line; home covers iff val>0."""
    val = (home_s - away_s) + line
    if abs(val) < 1e-9:
        return "PUSH"
    home_covers = val > 0
    won = home_covers if side == "home" else (not home_covers)
    return "WIN" if won else "LOSS"


def run_replay(kalman_opts=None):
    """Walk the cached season forward and return every uncensored spread
    candidate. kalman_opts overrides the Kalman config (e.g. toggling
    adaptiveBias) so callers can A/B the filter. Returns
    (candidates, fired_at_062, kalman_state)."""
    defs = load_defaults()
    # Fresh trajectory from seed weights — reproduces the real self-tune path.
    store = {"runs": [], "weights": {**defs["DEFAULT_W"]},
             "weightsVar": {**defs["DEFAULT_W_VAR"]}}

    # Derive the window from the cached 2026 stats so this keeps working as more
    # days accrue (network is blocked; we can only replay what's cached).
    cached = sorted(f[:-5] for f in os.listdir(STATS_CACHE_DIR)
                    if f.startswith("2026") and f.endswith(".json"))
    start = datetime.datetime.strptime(cached[0], "%Y%m%d").date()
    end = datetime.datetime.strptime(cached[-1], "%Y%m%d").date()
    print(f"Replay window: {start} -> {end} ({len(cached)} cached stat days)")

    kalman_state = None
    prev_date = None
    prev_graded = []
    prev_all_scored = []
    b2b_teams = set()

    candidates = []  # (date, pCover_lean, side, result, line)
    fired_at_062 = []  # sanity check vs live store

    d = start
    while d <= end:
        date = d.strftime("%Y%m%d")
        base_w = {**store["weights"]}
        base_w_var = {**store["weightsVar"]}

        try:
            enhanced = get_stats_for_date(date)
            base_stats = blend_base(enhanced["season"], enhanced.get("last10"),
                                    base_w.get("recentWeight", 0.87))
        except Exception as e:
            print(f"  {date} stats FAIL: {e}")
            d += datetime.timedelta(days=1)
            continue

        if kalman_state is None:
            kalman_state = initialize_kalman(enhanced["season"], kalman_opts)
            kalman_state["lastDriftDate"] = date

        if date != prev_date:
            prev_date = date
            if prev_graded:
                batch_update(kalman_state, prev_graded, kalman_opts)
                res = tune_weights(base_w, base_w_var, prev_graded)
                base_w = res["W"]; base_w_var = res["W_var"]
                store["weights"] = res["W"]; store["weightsVar"] = res["W_var"]
            b2b_teams = set()
            for g in prev_all_scored:
                if g.get("home"): b2b_teams.add(g["home"])
                if g.get("away"): b2b_teams.add(g["away"])
            prev_graded = []; prev_all_scored = []
            apply_daily_drift(kalman_state, date, kalman_opts)

        dyn_rv = compute_residual_var(store.get("runs", []))
        store["residualVar"] = dyn_rv

        try:
            sb = fetch_scoreboard_cached(date)
        except Exception as e:
            print(f"  {date} espn FAIL: {e}")
            d += datetime.timedelta(days=1); continue
        finals = extract_final_scores(sb)
        events = sb.get("events", []) if isinstance(sb, dict) else []

        games_list = []
        for ev in events:
            ha = pick_home_away_from_scoreboard_event(ev)
            if not ha:
                continue
            f = next((x for x in finals if x["away"] == ha["away"] and x["home"] == ha["home"]), None)
            if not f:
                continue
            games_list.append({**ha, "awayScore": f["awayScore"], "homeScore": f["homeScore"]})

        day_odds = {}
        if games_list:
            try:
                day_odds = fetch_odds_for_day(date, games_list)
            except Exception as e:
                print(f"  {date} odds FAIL: {e}")

        r_b2b = apply_b2b_adjustment(base_stats, b2b_teams, games_list)
        adjusted_stats = r_b2b["adjusted"]

        completed = []
        for gl in games_list:
            key = f"{gl['away']}@{gl['home']}"
            odds = day_odds.get(key, {"line": None, "total": None, "_book": None})
            line = odds.get("line"); total = odds.get("total")
            if not isinstance(line, (int, float)) or not isinstance(total, (int, float)):
                continue
            g = {"away": gl["away"], "home": gl["home"], "line": line, "total": total,
                 "_book": odds.get("_book")}
            game_stats = blend_for_game(adjusted_stats, enhanced.get("home"),
                                        enhanced.get("away"), g["home"], g["away"],
                                        base_w.get("locationWeight", 0.25))
            r = analyze_game(g, game_stats, get_avgs(game_stats), base_w, None,
                             kalman_state, base_w_var, dyn_rv)
            if not r:
                continue
            r["awayScore"] = gl["awayScore"]; r["homeScore"] = gl["homeScore"]
            completed.append(r)

            # UNCENSORED capture: engine's leaned side + prob, if |line|<cap.
            if abs(line) < ABS_LINE_CAP:
                ph = r.get("pHomeCover"); pa = r.get("pAwayCover")
                if ph is not None and pa is not None:
                    side = "home" if ph >= pa else "away"
                    pcov = max(ph, pa)
                    result = grade_lean(gl["homeScore"], gl["awayScore"], line, side)
                    picked = g["home"] if side == "home" else g["away"]
                    candidates.append({"date": date, "pcov": pcov, "side": side,
                                       "result": result, "line": line,
                                       "away": g["away"], "home": g["home"],
                                       "picked": picked})
                    if pcov >= 0.62:
                        fired_at_062.append((pcov, result))

        for g in completed:
            g["_kalmanDate"] = date
        prev_graded = completed
        prev_all_scored = completed
        store["runs"].append({"date": date, "burnIn": False, "games": completed})
        d += datetime.timedelta(days=1)

    if kalman_state:
        prune_processed_games(kalman_state, 60)

    return candidates, fired_at_062, kalman_state


JUICE = 1.10
BREAK_EVEN = 100 * JUICE / (1 + JUICE)


def _wl(cands):
    w = sum(1 for c in cands if c["result"] == "WIN")
    l = sum(1 for c in cands if c["result"] == "LOSS")
    units = w * (1 / JUICE) - l
    return w, l, units


def print_sweep(candidates, fired_at_062):
    w0, l0, u0 = _wl([{"result": r} for _, r in fired_at_062])
    print(f"\nUncensored spread candidates captured: {len(candidates)}")
    print(f"Sanity — replayed fired @>=0.62: {w0}-{l0} ({100*w0/(w0+l0):.1f}%)  [live store: 44-26 (62.9%)]")
    print(f"\nBreak-even WR at -110: {BREAK_EVEN:.1f}%")
    print(f"\n{'thresh':>7} {'N':>4} {'W':>3} {'L':>3} {'WR%':>6} {'units':>7} {'u/pick':>7}")
    for t in [0.50, 0.55, 0.58, 0.60, 0.62, 0.65, 0.67, 0.70, 0.72]:
        sel = [c for c in candidates if c["pcov"] >= t - 1e-9]
        w, l, units = _wl(sel)
        n = w + l
        if n == 0:
            continue
        print(f"{t:>7.2f} {len(sel):>4} {w:>3} {l:>3} {100*w/n:>6.1f} {units:>7.2f} {units/len(sel):>7.3f}")


def main():
    # Single-config sweep using whatever KALMAN_DEFAULTS ships in defaults.py.
    candidates, fired, _ = run_replay()
    print_sweep(candidates, fired)


if __name__ == "__main__":
    main()
