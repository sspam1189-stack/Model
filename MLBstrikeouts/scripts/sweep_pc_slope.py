#!/usr/bin/env python3
"""
sweep_pc_slope.py — sweep pitch-count slope-projection knobs.

Adds weighted linear-regression slope to the pitch count projection
and blends with the existing weighted-avg PC. Sweeps:
  - N (window size; 0 = all available starts)
  - DECAY (exponential weight on older starts; 1.0 = unweighted)

Fixed blend WEIGHT = 0.5 for this sweep (sweep first; if results merit,
sweep weight separately).

Baseline (WEIGHT=0): current behavior — wavg + last_pc>=70 bump + cap.

Usage:
    cd MLBstrikeouts
    python -m scripts.sweep_pc_slope
"""

import sys
import os
import io
import contextlib

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import defaults
from props_backfill import backfill, _calc_units


def _run(weight, n, decay):
    defaults.PC_SLOPE_WEIGHT = weight
    defaults.PC_SLOPE_N = n
    defaults.PC_SLOPE_DECAY = decay
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        results = backfill()
    if not results:
        return None
    picks = results["strikeouts"]["picks"]
    actionable = [p for p in picks if p.get("pick") in ("OVER", "UNDER")]
    watch = [p for p in picks if p.get("pick") == "PASS"]
    wins = sum(1 for p in actionable if p["won"])
    losses = len(actionable) - wins
    units = _calc_units(actionable)
    watch_w = sum(1 for p in watch if p["won"])
    watch_l = len(watch) - watch_w
    return {
        "wins": wins, "losses": losses, "n": len(actionable),
        "units": units, "watch_w": watch_w, "watch_l": watch_l,
    }


def main():
    Ns = [5, 7, 10, 0]          # 0 = all available
    DECAYS = [1.0, 0.9, 0.7, 0.5]
    WEIGHT = 0.5

    print(f"\n  Sweeping PC_SLOPE: WEIGHT={WEIGHT} (blend)  N x DECAY grid\n")
    # Baseline
    b = _run(0.0, 0, 1.0)
    if b is None:
        print("  Baseline backfill failed."); return
    bp = b["wins"] / max(1, b["wins"] + b["losses"]) * 100
    print(f"  Baseline (WEIGHT=0):  {b['wins']}-{b['losses']} ({bp:.1f}%)  "
          f"{b['units']:+.1f}u  ({b['n']} picks)\n")

    header = "        " + "  ".join(f"DECAY={d:<4}" for d in DECAYS)
    print(header)
    print("       " + "-" * (len(header)-7))

    best = (0, 0, 1.0, b)
    for n in Ns:
        n_label = ("all" if n == 0 else f"N={n}")
        line = f"  {n_label:<6}"
        for d in DECAYS:
            r = _run(WEIGHT, n, d)
            if r is None:
                line += "    failed   "
                continue
            line += f"  {r['units']:+6.1f}u/{r['n']:3d}"
            if r["units"] > best[3]["units"]:
                best = (n, d, WEIGHT, r)
        print(line)

    bN, bD, bW, br = best
    bp = br["wins"] / max(1, br["wins"] + br["losses"]) * 100
    n_label = ("all" if bN == 0 else f"N={bN}")
    print(f"\n  Best: WEIGHT={bW}, {n_label}, DECAY={bD}  → "
          f"{br['wins']}-{br['losses']} ({bp:.1f}%)  {br['units']:+.1f}u  ({br['n']} picks)")
    print(f"  Delta vs baseline: {br['units'] - b['units']:+.1f}u, "
          f"{br['n'] - b['n']:+d} picks")


if __name__ == "__main__":
    main()
