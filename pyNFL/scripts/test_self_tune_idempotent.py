"""Self-tune must fold each game into the weights exactly once.

Written 2026-09-16. The NFL Weekly Pipeline runs on a schedule, and its grade
stage passed EVERY graded game of the previous week to tune_weights on EVERY
run. grade_week_in_store returns the whole completed set even on the
early-exit path where nothing new was graded, so the same 16 games were
re-absorbed daily.

That is not convergence, it is the same evidence counted again and again, and
it walks the weights monotonically. Measured on 2026 Week 1's own 16 games:

    re-run      hfa    wRushDef    wRushOff
         0    2.754       0.097       4.499
         1    2.744       0.109       4.482
         3    2.723       0.134       4.449
        10    2.605       0.232       4.344

hfa drifts 0.15 points and wRushDef more than doubles, purely from re-reading
the same week. The Kalman filter never had this problem because
core/kalman_state.py keeps a processedGames map and skips what it has already
seen; self-tune had no equivalent.

Run:  cd pyNFL/scripts && python test_self_tune_idempotent.py
"""
import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "sources"))
sys.path.insert(0, os.path.join(HERE, "..", ".."))

from run_weekly import select_untuned_games, mark_tuned, SELF_TUNE_STAMP  # noqa: E402
from self_tune import tune_weights                                        # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


def load_real_games():
    """2026 Week 1's graded games -- real _marginFeatures, so tune_weights
    actually has something to update on."""
    p = os.path.join(HERE, "..", "data", "nfl.json")
    store = json.load(open(p, encoding="utf-8"))
    run = [r for r in store["runs"] if r.get("weekKey") == "2026_W1"][0]
    games = [copy.deepcopy(g) for g in run["games"]
             if isinstance(g.get("homeScore"), (int, float))]
    for g in games:                      # start from the pre-fix world
        g.pop(SELF_TUNE_STAMP, None)
    return games, copy.deepcopy(store["weights"]), copy.deepcopy(store["weightsVar"])


def tune_once(completed, W, V, tag):
    """One grade-stage self-tune pass, the way run_weekly does it."""
    fresh = select_untuned_games(completed)
    if not fresh:
        return W, V, 0
    out = tune_weights(W, V, fresh)
    mark_tuned(fresh, tag)
    return out["W"], out["W_var"], len(fresh)


def run():
    completed, W0, V0 = load_real_games()
    check(len(completed) >= 10, f"need graded games to test with, got {len(completed)}")

    # ---- run 1: every game is new, the weights should move --------------
    W1, V1, n1 = tune_once(completed, copy.deepcopy(W0), copy.deepcopy(V0), "2026_W1")
    check(n1 == len(completed), f"first pass should tune all {len(completed)}, got {n1}")
    check(W1 != W0, "first pass should actually move the weights")

    # ---- runs 2..8: the scheduled pipeline firing again, same games -----
    W, V = copy.deepcopy(W1), copy.deepcopy(V1)
    for i in range(2, 9):
        W, V, n = tune_once(completed, W, V, "2026_W1")
        check(n == 0, f"pass {i} re-tuned {n} already-folded games")
    check(W == W1, "a week of scheduled re-runs must not move the weights at all")
    check(V == V1, "nor the weight variances")

    # ---- a genuinely new week still tunes --------------------------------
    newgame = copy.deepcopy(completed[0])
    newgame.pop(SELF_TUNE_STAMP, None)
    newgame["home"], newgame["away"] = "Week 2 Home", "Week 2 Away"
    W2, V2, n2 = tune_once(completed + [newgame], W, V, "2026_W2")
    check(n2 == 1, f"a new week should tune exactly its new game, got {n2}")
    check(W2 != W, "a new game should move the weights")

    # ---- the migration case ---------------------------------------------
    # Games graded BEFORE this fix carry no stamp, but the weights on disk
    # already reflect them -- many times over. If they were treated as
    # untuned, the first run after deploy would re-absorb whole seasons at
    # once. run_weekly stamps them at load instead; this asserts the helper
    # respects a stamp whatever its value.
    legacy = copy.deepcopy(completed)
    for g in legacy:
        g[SELF_TUNE_STAMP] = "pre-2026-09-16"
    check(select_untuned_games(legacy) == [],
          "pre-existing graded games must count as already tuned")


run()

if failures:
    print(f"FAILED ({len(failures)})")
    for f in failures:
        print("  " + f)
    sys.exit(1)
print("PASSED  one fold per game, scheduled re-runs inert, new weeks still tune, "
      "legacy games respected")
