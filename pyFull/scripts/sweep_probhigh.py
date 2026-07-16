#!/usr/bin/env python3
# scripts/sweep_probhigh.py
#
# Uncensored probHigh sweep for the NBA full-season spread model. Unlike a
# fired-picks-only record (right-censored at the shipped probHigh), this reads
# EVERY graded game from the store and grades the model's leaned side
# (max(pHomeCover, pAwayCover)) against the closing line. Because those
# per-game probabilities were produced walk-forward live (each day used that
# day's self-tuned weights), the sweep is a genuine out-of-sample-at-pick-time
# read of where the P(cover) cutoff belongs — swept DOWN as well as up.
#
# The candidate universe is every graded game the model produced probabilities
# for (NBA fires across all line sizes — no abs-line cap applied).
# Usage: python scripts/sweep_probhigh.py

import os, json, statistics

# Units convention MATCHES the dashboard/store (core: calc_units = w + l*UNIT_LOSS):
# a win is +1.0u and a loss is -1.1u (risk 1.1 to win 1 at -110). Break-even WR is
# 1.1/2.1 = 52.4%. Keep this in sync with UNIT_LOSS so sweep units reconcile with
# the history record shown in the dashboard.
UNIT_LOSS = -1.1
BREAK_EVEN = 100 * 1.1 / (1 + 1.1)
HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, "..", "data", "history.json")


def grade_lean(home_s, away_s, line, lean_home):
    val = (home_s - away_s) + line   # home covers iff val > 0
    if abs(val) < 1e-9:
        return "PUSH"
    home_covers = val > 0
    return "WIN" if (home_covers == lean_home) else "LOSS"


def main():
    store = json.load(open(STORE))
    runs = [r for r in store["runs"] if not r.get("burnIn")]

    cands = []          # (leanP, result)   — uncensored leaned side
    fired = []          # (pCover, sResult) — what actually shipped at probHigh
    for r in runs:
        for g in r.get("games") or []:
            if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
                continue
            ph, pa, line = g.get("pHomeCover"), g.get("pAwayCover"), g.get("line")
            hs, as_ = g.get("homeScore"), g.get("awayScore")
            if None in (ph, pa, line, hs, as_):
                continue
            lean_home = ph >= pa
            leanP = max(ph, pa)
            res = grade_lean(hs, as_, line, lean_home)
            cands.append((leanP, res))
            if g.get("sPick") and g["sPick"] != "PASS" and g.get("sResult"):
                fired.append((g.get("pCover"), g["sResult"]))

    def tally(rows):
        w = sum(1 for _, r in rows if r == "WIN")
        l = sum(1 for _, r in rows if r == "LOSS")
        return w, l, w + l * UNIT_LOSS

    fw, fl, fu = tally(fired)
    print(f"Store fired picks (@ shipped probHigh): {fw}-{fl} "
          f"({100*fw/(fw+fl):.1f}%)  {fu:+.1f}u  n={len(fired)}  [matches dashboard history]")
    print(f"Uncensored candidates (all graded): {len(cands)}")
    print(f"Break-even WR at -110: {BREAK_EVEN:.1f}%\n")

    print(f"{'thresh':>7} {'N':>5} {'W':>4} {'L':>4} {'WR%':>6} {'units':>8} {'u/pick':>7} {'+EV?':>5}")
    for t in [0.50, 0.53, 0.55, 0.56, 0.57, 0.58, 0.59, 0.60, 0.61, 0.62,
              0.63, 0.65, 0.67, 0.70]:
        sel = [c for c in cands if c[0] >= t - 1e-9]
        w, l, u = tally(sel)
        n = w + l
        if n == 0:
            continue
        print(f"{t:>7.2f} {len(sel):>5} {w:>4} {l:>4} {100*w/n:>6.1f} "
              f"{u:>8.1f} {u/len(sel):>7.3f} {'YES' if 100*w/n >= BREAK_EVEN else 'no':>5}")

    print("\n--- Calibration by leaned-side prob bucket (predicted vs realized) ---")
    print(f"{'bucket':>11} {'n':>5} {'pred%':>6} {'real%':>6} {'Δ':>6}")
    edges = [0.50, 0.55, 0.58, 0.61, 0.64, 0.68, 1.01]
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        sel = [c for c in cands if lo <= c[0] < hi]
        w = sum(1 for _, r in sel if r == "WIN")
        l = sum(1 for _, r in sel if r == "LOSS")
        n = w + l
        if not n:
            continue
        pred = 100 * statistics.mean([c[0] for c in sel])
        real = 100 * w / n
        print(f"{lo:.2f}-{min(hi,1.0):.2f} {n:>5} {pred:>6.1f} {real:>6.1f} {real-pred:>+6.1f}")


if __name__ == "__main__":
    main()
