#!/usr/bin/env python3
"""
Sweep PITCHER_KALMAN_DEFAULTS["kalmanBlend"] on a walk-forward backfill to
find the best Kalman/rate blend weight for K props.

Reuses props_backfill.backfill() so the projection pipeline, data sources,
and grading are identical to production. Backs up data/kalman_state.json
before each run and restores it after the sweep completes.

Usage:
    cd MLBstrikeouts
    python -m scripts.sweep_kalman_blend
"""

import sys, os, shutil, io, contextlib

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from pitcher_kalman import PITCHER_KALMAN_DEFAULTS
from props_backfill import backfill
import defaults


KALMAN_STATE = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "data", "kalman_state.json",
))


def _units(p):
    o = p.get('odds')
    if o is None:
        return 0.0
    o = int(o)
    won = p['won']
    if o > 0:
        return o / 100.0 if won else -1.0
    return 1.0 if won else -abs(o) / 100.0


def _risked(p):
    o = p.get('odds')
    if o is None or int(o) > 0:
        return 1.0
    return abs(int(o)) / 100.0


def _row(label, picks):
    w = sum(1 for p in picks if p['won'])
    l = len(picks) - w
    n = w + l
    u = sum(_units(p) for p in picks)
    r = sum(_risked(p) for p in picks)
    pct = w / n * 100 if n else 0
    roi = u / r * 100 if r else 0
    return (label, n, w, l, pct, u, roi)


def _is_lean_under(p):
    if p.get('pick') != 'PASS':
        return False
    pc = p.get('pCover') or 0
    if not (0.60 <= pc < 0.70):
        return False
    return p.get('would_be_pick') == 'UNDER'


def _is_lean_over(p):
    if p.get('pick') != 'PASS':
        return False
    pc = p.get('pCover') or 0
    if not (0.65 <= pc < 0.70):
        return False
    return p.get('would_be_pick') == 'OVER'


def run_sweep(season=None, blend_weights=None, recent_cutoff='2026-05-05',
              start_date=None):
    season = season or defaults.current_season()
    blend_weights = blend_weights or [0.0, 0.2, 0.3, 0.5, 0.7, 0.85, 1.0]

    print(f"\n{'='*72}")
    print(f"  KALMAN BLEND SWEEP — Season {season}")
    print(f"  Recent cutoff: {recent_cutoff}")
    print(f"  Weights: {blend_weights}")
    print(f"{'='*72}")

    # Back up kalman state so we don't disturb production
    backup = None
    if os.path.exists(KALMAN_STATE):
        backup = KALMAN_STATE + ".sweep-backup"
        shutil.copy2(KALMAN_STATE, backup)
        print(f"  Backed up kalman_state.json -> {backup}")

    try:
        results_by_blend = {}
        for b in blend_weights:
            print(f"\n--- Running walk-forward with kalmanBlend = {b:.2f} ---")
            PITCHER_KALMAN_DEFAULTS["kalmanBlend"] = b

            # Silence backfill's per-date prints to keep output readable.
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                results = backfill(season=season, start_date=start_date)
            picks = (results or {}).get('strikeouts', {}).get('picks', []) or []
            results_by_blend[b] = picks
            n_picks = sum(1 for p in picks if p.get('pick') in ('OVER','UNDER'))
            n_leans = sum(1 for p in picks if _is_lean_under(p) or _is_lean_over(p))
            print(f"  picks={n_picks}  leans={n_leans}  total rows={len(picks)}")

    finally:
        # Restore kalman state to whatever was there before the sweep
        if backup and os.path.exists(backup):
            shutil.move(backup, KALMAN_STATE)
            print(f"\n  Restored kalman_state.json from backup")

    # --- Reports ---
    def _print_table(title, predicate_for_arr):
        print(f"\n{'='*72}")
        print(f"  {title}")
        print(f"{'='*72}")
        print(f"  {'blend':>5s}  {'period':10s}  {'n':>4s} {'W-L':>9s} "
              f"{'Win%':>6s} {'Units':>9s} {'ROI':>7s}")
        for b in blend_weights:
            picks = results_by_blend.get(b, [])
            for label, cutoff in [('season', None),
                                  ('since-5/05', recent_cutoff)]:
                arr = predicate_for_arr(picks)
                if cutoff:
                    arr = [p for p in arr if p.get('date', '') >= cutoff]
                lbl, n, w, l, pct, u, roi = _row(label, arr)
                print(f"  {b:5.2f}  {lbl:10s}  {n:4d} {w:3d}-{l:<3d}   "
                      f"{pct:5.1f}% {u:+8.2f}u {roi:+6.1f}%")

    _print_table("PICKS (pick in OVER/UNDER, conf=high)",
                 lambda picks: [p for p in picks
                                if p.get('pick') in ('OVER', 'UNDER')])

    _print_table("LEAN U (PASS, would_be_pick=UNDER, .60-.70 pCover)",
                 lambda picks: [p for p in picks if _is_lean_under(p)])

    _print_table("LEAN O (PASS, would_be_pick=OVER, .65-.70 pCover)",
                 lambda picks: [p for p in picks if _is_lean_over(p)])

    _print_table("ALL ACTIONABLE (picks + leans, combined)",
                 lambda picks: [p for p in picks
                                if p.get('pick') in ('OVER', 'UNDER')
                                or _is_lean_under(p) or _is_lean_over(p)])


if __name__ == "__main__":
    run_sweep()
