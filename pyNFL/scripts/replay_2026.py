#!/usr/bin/env python3
"""
Sandboxed replay of the 2026 NFL season through the backfill engine.

Why: the live pipeline read a week-1-only 2026 PBP cache for weeks 2-4 (fixed in
f0c6ff5a3), so weeks 3-4 projections -- and the Kalman/self-tuned weights learned
from them -- were built on thin data. This replays 2026 week by week with the
complete PBP, using the market lines already stored in nfl.json (no Odds API
credits), and writes ONLY to a scratch dir. Nothing live is touched.

Usage (from pyNFL/scripts):
  python replay_2026.py --out ../../scratch/nfl_replay_2026
"""
import argparse, copy, json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", ".."))

import backfill_last_n_weeks as bf   # noqa: E402

LIVE = os.path.join(HERE, "..", "data", "nfl.json")
PRESEASON_COMMIT = "66d452946"   # last nfl.json before the first 2026 run


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)

    live = json.load(open(LIVE, encoding="utf-8"))
    pre = json.loads(subprocess.check_output(
        ["git", "show", f"{PRESEASON_COMMIT}:pyNFL/data/nfl.json"], cwd=HERE))

    # Stored market lines per 2026 week (what the live run actually saw).
    stored = {}
    for r in live["runs"]:
        if r.get("season") == 2026:
            stored[r["week"]] = [
                {"away": g["away"], "home": g["home"], "line": g.get("line"),
                 "total": g.get("total"), "_book": g.get("_book") or "stored"}
                for g in r.get("games", [])]

    # Sandbox store: 2023-25 history retained (walk-forward calibrations see it,
    # as live did), 2026 removed, pre-season weights restored. The stale
    # "backfill" flag is dropped on the in-memory copies so backfill()'s
    # clean-reset doesn't discard them.
    store = copy.deepcopy(live)
    store["runs"] = [dict(r, backfill=False) for r in store["runs"] if r.get("season") != 2026]
    store["weights"] = pre.get("weights")
    store["weightsVar"] = pre.get("weightsVar")

    sandbox_path = os.path.join(out, "nfl_replay_store.json")
    bf.load_store = lambda: store
    bf.save_store = lambda s: json.dump(s, open(sandbox_path, "w"))
    bf.save_kalman_state = lambda s: None          # never touch the live state
    bf.fetch_historical_odds = lambda season, week: stored.get(week, [])
    bf.ODDS_API_DELAY = 0

    bf.backfill([2026], output_dir=out)

    runs26 = [r for r in store["runs"] if r.get("season") == 2026]
    json.dump(runs26, open(os.path.join(out, "replay_2026_runs.json"), "w"))
    print(f"\nreplay runs: {[r['week'] for r in runs26]} -> {out}")


if __name__ == "__main__":
    main()
