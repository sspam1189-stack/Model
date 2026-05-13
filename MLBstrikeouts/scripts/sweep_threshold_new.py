#!/usr/bin/env python3
"""
Sweep the pCover threshold at the proposed new config
(BF=0.95, VAR=1.15, BLEND=0.00, CAP=23) to check if 0.70 is still optimal
or if a different cutoff produces better picks under the new projection
distribution.

Runs the walk-forward backfill ONCE, captures all picks + leans (pCover>=0.60),
then post-hoc filters at each candidate threshold.

Usage:
    cd MLBstrikeouts
    python -m scripts.sweep_threshold_new
"""

import sys, os, shutil, io, contextlib, json

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import defaults
from pitcher_kalman import PITCHER_KALMAN_DEFAULTS
from props_backfill import backfill


KALMAN_STATE = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "data", "kalman_state.json",
))


def _units(p, won):
    o = p.get('odds')
    if o is None: return 0.0
    o = int(o)
    if o > 0: return o / 100.0 if won else -1.0
    return 1.0 if won else -abs(o) / 100.0


def _risked(p):
    o = p.get('odds')
    if o is None or int(o) > 0: return 1.0
    return abs(int(o)) / 100.0


def _summarize(arr, label):
    w = sum(1 for p in arr if p['won']); l = len(arr) - w; n = w + l
    u = sum(_units(p, p['won']) for p in arr)
    r = sum(_risked(p) for p in arr)
    pct = w / n * 100 if n else 0
    roi = u / r * 100 if r else 0
    return f"  {label:35s} n={n:4d} {w:3d}-{l:<3d} pct={pct:5.1f}% u={u:+7.2f} roi={roi:+6.1f}%"


def run_sweep(season=None, recent_cutoff='2026-05-05'):
    season = season or defaults.current_season()
    bb, bv, bk, bc = (defaults.BF_MULT, dict(defaults.VAR_MULT),
                     PITCHER_KALMAN_DEFAULTS["kalmanBlend"], defaults.BF_CAP)

    backup = None
    if os.path.exists(KALMAN_STATE):
        backup = KALMAN_STATE + ".sweep-backup"
        shutil.copy2(KALMAN_STATE, backup)

    try:
        defaults.BF_MULT = 0.95
        defaults.VAR_MULT['strikeouts'] = 1.15
        defaults.BF_CAP = 23.0
        PITCHER_KALMAN_DEFAULTS["kalmanBlend"] = 0.00

        print(f"\n{'='*72}")
        print(f"  Running backfill at: BF=0.95 VAR=1.15 CAP=23 BLEND=0.00")
        print(f"{'='*72}")

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            r = backfill(season=season)
        picks = (r or {}).get('strikeouts', {}).get('picks', []) or []
        print(f"  Backfill returned {len(picks)} graded picks (incl. leans)")
    finally:
        defaults.BF_MULT = bb
        defaults.VAR_MULT.clear(); defaults.VAR_MULT.update(bv)
        PITCHER_KALMAN_DEFAULTS["kalmanBlend"] = bk
        defaults.BF_CAP = bc
        if backup and os.path.exists(backup):
            shutil.move(backup, KALMAN_STATE)

    # Each pick has: pCover, would_be_pick (for PASS leans), pick (for high-conv).
    # Combined "side" = pick if not PASS, else would_be_pick.
    def _side(p):
        return p['pick'] if p['pick'] in ('OVER','UNDER') else p.get('would_be_pick')

    for p in picks:
        p['_side'] = _side(p)

    pCovers = sorted(set(round((p.get('pCover') or 0), 2) for p in picks))
    print(f"  pCover range in data: {min(pCovers):.2f} - {max(pCovers):.2f}")

    print(f"\n{'='*72}")
    print(f"  THRESHOLD SWEEP — pick fires when pCover >= threshold")
    print(f"{'='*72}")
    print(f"  {'thresh':>7s} {'period':10s} {'n':>4s} {'W-L':>9s} "
          f"{'Win%':>6s} {'Units':>9s} {'ROI':>7s}")
    for thresh in [0.58, 0.60, 0.62, 0.65, 0.67, 0.68, 0.70, 0.72, 0.75, 0.78]:
        for label, cutoff in [('season', None), ('since-5/05', recent_cutoff)]:
            arr = [p for p in picks
                   if (p.get('pCover') or 0) >= thresh
                   and p.get('_side') in ('OVER', 'UNDER')]
            if cutoff:
                arr = [p for p in arr if p.get('date','') >= cutoff]
            w = sum(1 for p in arr if p['won']); l = len(arr) - w; n = w + l
            u = sum(_units(p, p['won']) for p in arr); r = sum(_risked(p) for p in arr)
            pct = w/n*100 if n else 0; roi = u/r*100 if r else 0
            print(f"  {thresh:7.2f} {label:10s} {n:4d} {w:3d}-{l:<3d}   {pct:5.1f}% "
                  f"{u:+8.2f}u {roi:+6.1f}%")

    # By direction
    print(f"\n{'='*72}")
    print(f"  THRESHOLD SWEEP — by direction (season)")
    print(f"{'='*72}")
    for side in ('OVER', 'UNDER'):
        print(f"\n  --- {side} ---")
        print(f"  {'thresh':>7s} {'n':>4s} {'W-L':>9s} {'Win%':>6s} {'Units':>9s} {'ROI':>7s}")
        for thresh in [0.58, 0.60, 0.62, 0.65, 0.68, 0.70, 0.72, 0.75, 0.78]:
            arr = [p for p in picks
                   if (p.get('pCover') or 0) >= thresh
                   and p.get('_side') == side]
            w = sum(1 for p in arr if p['won']); l = len(arr) - w; n = w + l
            u = sum(_units(p, p['won']) for p in arr); r = sum(_risked(p) for p in arr)
            pct = w/n*100 if n else 0; roi = u/r*100 if r else 0
            print(f"  {thresh:7.2f} {n:4d} {w:3d}-{l:<3d}   {pct:5.1f}% "
                  f"{u:+8.2f}u {roi:+6.1f}%")


if __name__ == "__main__":
    run_sweep()
