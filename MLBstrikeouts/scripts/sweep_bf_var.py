#!/usr/bin/env python3
"""
Sweep BF_MULT × VAR_MULT['strikeouts'] on a walk-forward backfill.

Reuses props_backfill.backfill() so the projection pipeline, data sources,
and grading are identical to production. Backs up data/kalman_state.json
before each run.

Usage:
    cd MLBstrikeouts
    python -m scripts.sweep_bf_var
"""

import sys, os, shutil, io, contextlib

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import defaults
from props_backfill import backfill


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


def _row(picks):
    w = sum(1 for p in picks if p['won'])
    l = len(picks) - w
    n = w + l
    u = sum(_units(p) for p in picks)
    r = sum(_risked(p) for p in picks)
    pct = w / n * 100 if n else 0
    roi = u / r * 100 if r else 0
    return n, w, l, pct, u, roi


def _is_lean(p):
    """Unified lean band: pCov in [0.65, current pick threshold), both directions."""
    if p.get('pick') != 'PASS':
        return False
    pc = p.get('pCover') or 0
    thresh = defaults.MARKET_THRESHOLDS.get('strikeouts', {}).get('high', 0.72)
    return 0.65 <= pc < thresh


def run_sweep(season=None, bf_grid=None, var_grid=None,
              recent_cutoff='2026-05-05', start_date=None):
    season = season or defaults.current_season()
    bf_grid = bf_grid or [0.82, 0.85, 0.88, 0.91, 0.94]
    var_grid = var_grid or [0.85, 1.0, 1.15, 1.30]

    print(f"\n{'='*72}")
    print(f"  BF_MULT x VAR_MULT SWEEP — Season {season}")
    print(f"  BF grid:  {bf_grid}")
    print(f"  VAR grid: {var_grid}")
    print(f"  Recent cutoff: {recent_cutoff}")
    print(f"  Total combos: {len(bf_grid) * len(var_grid)}")
    print(f"{'='*72}")

    # Capture baselines for restore
    baseline_bf = defaults.BF_MULT
    baseline_var = dict(defaults.VAR_MULT)

    # Back up Kalman state
    backup = None
    if os.path.exists(KALMAN_STATE):
        backup = KALMAN_STATE + ".sweep-backup"
        shutil.copy2(KALMAN_STATE, backup)
        print(f"  Backed up kalman_state.json")

    results = {}  # (bf, var) -> picks list

    try:
        i = 0
        for bf in bf_grid:
            for var in var_grid:
                i += 1
                print(f"\n--- [{i}/{len(bf_grid)*len(var_grid)}] "
                      f"BF={bf:.2f}  VAR={var:.2f} ---")
                defaults.BF_MULT = bf
                defaults.VAR_MULT['strikeouts'] = var

                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    r = backfill(season=season, start_date=start_date)
                picks = (r or {}).get('strikeouts', {}).get('picks', []) or []
                results[(bf, var)] = picks

                n_picks = sum(1 for p in picks
                              if p.get('pick') in ('OVER', 'UNDER'))
                n_lean = sum(1 for p in picks if _is_lean(p))
                print(f"  picks={n_picks}  leans={n_lean}")

    finally:
        defaults.BF_MULT = baseline_bf
        defaults.VAR_MULT.clear()
        defaults.VAR_MULT.update(baseline_var)
        if backup and os.path.exists(backup):
            shutil.move(backup, KALMAN_STATE)
            print(f"\n  Restored defaults and kalman_state.json")

    # --- Reports ---
    def _table(title, predicate, cutoff=None):
        print(f"\n{'='*82}")
        print(f"  {title}{' (since ' + cutoff + ')' if cutoff else ' (season)'}")
        print(f"{'='*82}")
        # Header: VAR across columns, BF down rows
        header = ['BF \\ VAR'] + [f'{v:.2f}' for v in var_grid]
        print(f"  {header[0]:>10s}  " + "  ".join(f"{h:>16s}" for h in header[1:]))
        for bf in bf_grid:
            row = [f"{bf:.2f}"]
            for var in var_grid:
                picks = results.get((bf, var), [])
                arr = [p for p in picks if predicate(p)]
                if cutoff:
                    arr = [p for p in arr if p.get('date', '') >= cutoff]
                n, w, l, pct, u, roi = _row(arr)
                cell = f"{n:3d} {pct:4.1f}% {u:+5.1f}u"
                row.append(cell)
            print(f"  {row[0]:>10s}  " + "  ".join(f"{c:>16s}" for c in row[1:]))

    pred_picks = lambda p: p.get('pick') in ('OVER', 'UNDER')
    pred_lean = _is_lean
    pred_all = lambda p: pred_picks(p) or pred_lean(p)

    _table("PICKS", pred_picks)
    _table("PICKS", pred_picks, cutoff=recent_cutoff)
    _table("LEAN (0.65-thresh)", pred_lean)
    _table("LEAN (0.65-thresh)", pred_lean, cutoff=recent_cutoff)
    _table("ALL ACTIONABLE", pred_all)
    _table("ALL ACTIONABLE", pred_all, cutoff=recent_cutoff)

    # Rank by total actionable season ROI
    print(f"\n{'='*82}")
    print(f"  TOP COMBOS by season-wide ALL-ACTIONABLE ROI")
    print(f"{'='*82}")
    print(f"  {'BF':>5s} {'VAR':>5s}  {'n':>4s} {'W-L':>9s} "
          f"{'Win%':>6s} {'Units':>9s} {'ROI':>7s}")
    scored = []
    for (bf, var), picks in results.items():
        arr = [p for p in picks if pred_all(p)]
        n, w, l, pct, u, roi = _row(arr)
        scored.append((bf, var, n, w, l, pct, u, roi))
    scored.sort(key=lambda x: -x[7])  # roi desc
    for bf, var, n, w, l, pct, u, roi in scored[:10]:
        print(f"  {bf:5.2f} {var:5.2f}  {n:4d} {w:3d}-{l:<3d}   "
              f"{pct:5.1f}% {u:+8.2f}u {roi:+6.1f}%")


if __name__ == "__main__":
    run_sweep()
