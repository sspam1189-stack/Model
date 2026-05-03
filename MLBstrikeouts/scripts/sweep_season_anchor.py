#!/usr/bin/env python3
"""
sweep_season_anchor.py — grid-search SEASON_ANCHOR_WEIGHT for K market.

Walks the backfill at multiple anchor weights and reports W-L, win%,
units, and watchlist performance per setting. Suppresses backfill's
internal logging so only the sweep table is visible.

Usage:
    cd MLBstrikeouts
    python -m scripts.sweep_season_anchor
"""

import sys
import os
import io
import contextlib

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import defaults
from props_backfill import backfill, _calc_units


def _run_at(weight):
    defaults.SEASON_ANCHOR_WEIGHT["strikeouts"] = weight
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        results = backfill()
    if not results:
        return None
    all_picks = results["strikeouts"]["picks"]
    actionable = [p for p in all_picks if p.get("pick") in ("OVER", "UNDER")]
    watch = [p for p in all_picks if p.get("pick") == "PASS"]
    wins = sum(1 for p in actionable if p["won"])
    losses = len(actionable) - wins
    units = _calc_units(actionable)
    watch_w = sum(1 for p in watch if p["won"])
    watch_l = len(watch) - watch_w
    return {
        "wins": wins,
        "losses": losses,
        "n": len(actionable),
        "units": units,
        "watch_w": watch_w,
        "watch_l": watch_l,
    }


def main():
    weights = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0]
    print(f"\n  Sweeping SEASON_ANCHOR_WEIGHT['strikeouts'] over {weights}\n")
    print(f"  {'W':>5}  {'wins':>4}-{'L':<4}  {'win%':>6}  {'units':>8}   "
          f"{'watch':>5}  {'watchW%':>7}")
    print(f"  {'-'*55}")

    best = None
    for w in weights:
        r = _run_at(w)
        if r is None:
            continue
        pct = r["wins"] / max(1, r["wins"] + r["losses"]) * 100
        wpct = r["watch_w"] / max(1, r["watch_w"] + r["watch_l"]) * 100
        marker = ""
        if best is None or r["units"] > best[1]["units"]:
            best = (w, r)
            marker = "  <"
        print(f"  {w:>5.2f}  {r['wins']:>4}-{r['losses']:<4}  {pct:>5.1f}%  "
              f"{r['units']:>+7.1f}u   {r['watch_w']:>2}-{r['watch_l']:<2}  "
              f"{wpct:>6.1f}%{marker}")

    if best is not None:
        w, r = best
        pct = r["wins"] / max(1, r["wins"] + r["losses"]) * 100
        print(f"\n  Best by units: w={w:.2f}  "
              f"{r['wins']}-{r['losses']} ({pct:.1f}%)  {r['units']:+.1f}u")


if __name__ == "__main__":
    main()
