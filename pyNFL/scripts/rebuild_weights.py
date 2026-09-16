"""Rebuild the self-tuned weights from scratch, folding each game in ONCE.

Why this exists
---------------
Until 2026-09-16 the grade stage handed EVERY graded game of the previous
week to tune_weights on EVERY scheduled run (see select_untuned_games in
run_weekly.py). The weights therefore absorbed the same evidence many times
over -- monotonically, not convergently: ten re-reads of a single week moved
hfa 2.754 -> 2.605 and more than doubled wRushDef.

c656e76b8 stopped the bleeding. It did not rewind it, because the weights on
disk had already been walked by an unknown number of extra passes. This
replays the whole history the way the FIXED code would have run it: start at
defaults.DEFAULT_W, walk the weeks in order, and tune once per week on that
week's graded games.

What it does NOT touch
----------------------
  residualVar   recomputed from history on every run (run_weekly ~line 928),
                so it was never cumulative and needs no rebuild.
  Kalman state  core/kalman_state.py has always deduped via processedGames.
  grading       results, scores and picks are read, never written.

Usage
-----
  cd pyNFL/scripts
  python rebuild_weights.py              # dry run: report only, writes nothing
  python rebuild_weights.py --apply      # write the rebuilt weights
  python rebuild_weights.py --apply --backup-dir DIR

A backup of nfl.json is always written before --apply.
"""
import argparse
import copy
import json
import os
import shutil
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "sources"))
sys.path.insert(0, os.path.join(HERE, "..", ".."))

import defaults                                    # noqa: E402
from self_tune import tune_weights                 # noqa: E402

STORE = os.path.normpath(os.path.join(HERE, "..", "data", "nfl.json"))
DASH = os.path.normpath(os.path.join(HERE, "..", "..", "PythonDashboard",
                                     "data", "nfl.json"))

WATCH = ("wPassOff", "wRushOff", "wPassDef", "wRushDef", "hfa", "constant")


def week_order(run):
    """(season, week) so postseason W19-22 sort after the regular season."""
    key = str(run.get("weekKey") or "")
    try:
        season, wk = key.split("_W")
        return (int(season), int(wk))
    except (ValueError, TypeError):
        return (run.get("season") or 0, run.get("week") or 0)


def graded_with_features(run):
    """The games a week would legitimately have contributed, once.

    tune_weights can only update on rows carrying _marginFeatures, so a game
    without them contributes nothing and is not counted here either.
    """
    return [g for g in run.get("games", [])
            if isinstance(g.get("homeScore"), (int, float))
            and isinstance(g.get("awayScore"), (int, float))
            and g.get("_marginFeatures")]


def rebuild(store, verbose=True):
    W = copy.deepcopy(defaults.DEFAULT_W)
    V = copy.deepcopy(defaults.DEFAULT_W_VAR)
    runs = sorted(store.get("runs", []), key=week_order)

    folded = 0
    trail = []
    for run in runs:
        games = graded_with_features(run)
        if not games:
            continue
        out = tune_weights(W, V, games)
        W, V = out["W"], out["W_var"]
        folded += len(games)
        trail.append((run.get("weekKey"), len(games), copy.deepcopy(W)))

    if verbose:
        print(f"replayed {len(trail)} weeks, {folded} games, one fold each\n")
        print(f"{'week':10}{'n':>4}  " + "".join(f"{k:>11}" for k in WATCH))
        for i, (wk, n, w) in enumerate(trail):
            if i < 3 or i >= len(trail) - 3 or i % 12 == 0:
                print(f"{wk:10}{n:>4}  " + "".join(f"{w.get(k, 0):>11.4f}" for k in WATCH))
    return W, V, folded


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write the rebuilt weights (default: report only)")
    ap.add_argument("--backup-dir", default=os.environ.get("TEMP") or "/tmp")
    args = ap.parse_args()

    store = json.load(open(STORE, encoding="utf-8"))
    old_w = copy.deepcopy(store.get("weights") or {})
    old_v = copy.deepcopy(store.get("weightsVar") or {})

    new_w, new_v, folded = rebuild(store)

    print(f"\n{'weight':16}{'on disk':>12}{'rebuilt':>12}{'delta':>12}{'':>4}")
    keys = [k for k in new_w if isinstance(new_w[k], (int, float))]
    for k in sorted(keys, key=lambda x: -abs(float(new_w.get(x, 0)) - float(old_w.get(x, 0) or 0))):
        o, n = float(old_w.get(k, 0) or 0), float(new_w[k])
        if abs(n - o) < 1e-9:
            continue
        pct = (n - o) / o * 100 if o else float("inf")
        flag = "  <<" if abs(pct) >= 25 else ""
        print(f"{k:16}{o:>12.4f}{n:>12.4f}{n - o:>+12.4f}{flag}")

    if not args.apply:
        print("\nDRY RUN -- nothing written. Re-run with --apply to keep these.")
        return

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = os.path.join(args.backup_dir, f"nfl.json.pre-rebuild-{stamp}")
    shutil.copy2(STORE, bak)
    print(f"\nbacked up {STORE} -> {bak}")

    store["weights"] = new_w
    store["weightsVar"] = new_v
    store.setdefault("meta", {})
    store["meta"]["weightsRebuiltAt"] = stamp
    store["meta"]["weightsRebuiltGames"] = folded
    with open(STORE, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2)
    print(f"wrote rebuilt weights to {STORE}")
    if os.path.exists(DASH):
        shutil.copy2(STORE, DASH)
        print(f"copied to {DASH}")


if __name__ == "__main__":
    main()
