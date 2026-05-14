#!/usr/bin/env python3
"""
3D sweep: BF_MULT x VAR_MULT['strikeouts'] x pCover threshold.

Re-evaluates the prior 4D optimum (BF=0.95 / VAR=1.15 / thr=0.72) under
the new emp_std default (2.0) and blend-window floor (0.80). Threshold
is rescored post-hoc from each backfill's picks+watch list, so total
backfill runs = |BF_grid| * |VAR_grid|, not the full 3D cross-product.

Holds: BF_CAP=23, kalmanBlend=0.00 (from prior optima).

Usage:
    cd MLBstrikeouts
    python -m scripts.sweep_bf_var_thr
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
RESULTS_FILE = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "data", "sweep_bf_var_thr_results.json",
))


def _units(p):
    o = p.get('odds')
    if o is None: return 0.0
    o = int(o); won = p['won']
    if o > 0: return o / 100.0 if won else -1.0
    return 1.0 if won else -abs(o) / 100.0


def _risked(p):
    o = p.get('odds')
    if o is None or int(o) > 0: return 1.0
    return abs(int(o)) / 100.0


def _stats(picks):
    w = sum(1 for p in picks if p['won']); l = len(picks) - w
    n = w + l
    u = sum(_units(p) for p in picks); r = sum(_risked(p) for p in picks)
    pct = w / n * 100 if n else 0
    roi = u / r * 100 if r else 0
    return {'n': n, 'w': w, 'l': l, 'pct': pct, 'u': u, 'roi': roi}


def _picks_at_threshold(all_entries, thr):
    """Re-filter the engine's picks+watch list at a different pCover threshold.

    Engine emits pick='OVER'/'UNDER' when pCover>=engine_threshold, and
    pick='PASS' + would_be_pick set when pCover>=0.60 but below the engine
    threshold. To rescore at thr:
      - keep entries with pCover >= thr
      - direction = pick if pick in (OVER, UNDER) else would_be_pick
      - skip if no direction (no `pick` and no `would_be_pick`)
    """
    out = []
    for p in all_entries:
        pcover = p.get('pCover') or 0
        if pcover < thr:
            continue
        direction = p.get('pick')
        if direction not in ('OVER', 'UNDER'):
            direction = p.get('would_be_pick')
        if direction not in ('OVER', 'UNDER'):
            continue
        out.append(p)
    return out


def run_sweep(season=None, bf_grid=None, var_grid=None, thr_grid=None):
    season = season or defaults.current_season()
    bf_grid  = bf_grid  or [0.90, 0.95, 1.00]
    var_grid = var_grid or [1.05, 1.10, 1.15, 1.20, 1.25]
    thr_grid = thr_grid or [0.68, 0.70, 0.72, 0.74, 0.76]

    total_backfills = len(bf_grid) * len(var_grid)
    total_cells = total_backfills * len(thr_grid)
    print(f"\n{'='*72}")
    print(f"  3D SWEEP: BF x VAR x THRESHOLD  Season {season}")
    print(f"  BF:    {bf_grid}")
    print(f"  VAR:   {var_grid}")
    print(f"  THR:   {thr_grid}")
    print(f"  Backfill runs: {total_backfills}  (cells: {total_cells})")
    print(f"  Held: BF_CAP={defaults.BF_CAP}, "
          f"blend={PITCHER_KALMAN_DEFAULTS['kalmanBlend']}, "
          f"blend_floor={os.environ.get('BLEND_FLOOR_MULT', '0.8')}, "
          f"emp_std_default={defaults.DEFAULT_EMPIRICAL_STD['strikeouts']}")
    print(f"{'='*72}\n")

    bb = defaults.BF_MULT
    bv = dict(defaults.VAR_MULT)

    backup = None
    if os.path.exists(KALMAN_STATE):
        backup = KALMAN_STATE + ".sweep-backup"
        shutil.copy2(KALMAN_STATE, backup)

    rows = []
    i = 0
    try:
        for bf in bf_grid:
            for var in var_grid:
                i += 1
                print(f"[{i:>2}/{total_backfills}] BF={bf:.2f} VAR={var:.2f}", end='  ', flush=True)
                defaults.BF_MULT = bf
                defaults.VAR_MULT['strikeouts'] = var

                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    r = backfill(season=season)
                picks = (r or {}).get('strikeouts', {}).get('picks', []) or []

                engine_pa = [p for p in picks if p.get('pick') in ('OVER', 'UNDER')]
                base = _stats(engine_pa)
                print(f"engine(thr=current): n={base['n']} {base['pct']:.1f}% "
                      f"{base['u']:+.1f}u {base['roi']:+.1f}%", flush=True)

                for thr in thr_grid:
                    rescored = _picks_at_threshold(picks, thr)
                    s = _stats(rescored)
                    rows.append({
                        'bf': bf, 'var': var, 'thr': thr,
                        'n': s['n'], 'w': s['w'], 'l': s['l'],
                        'pct': s['pct'], 'u': s['u'], 'roi': s['roi'],
                    })
    finally:
        defaults.BF_MULT = bb
        defaults.VAR_MULT.clear(); defaults.VAR_MULT.update(bv)
        if backup and os.path.exists(backup):
            shutil.move(backup, KALMAN_STATE)
            print("\n  Restored defaults and kalman_state.json")

    try:
        with open(RESULTS_FILE, 'w') as f:
            json.dump({'season': season, 'rows': rows}, f, indent=2)
        print(f"  Wrote {RESULTS_FILE}")
    except Exception as e:
        print(f"  save failed: {e}")

    def _top(title, sort_key, n=15):
        print(f"\n{'='*88}")
        print(f"  TOP {n} by {title}")
        print(f"{'='*88}")
        print(f"  {'BF':>5s} {'VAR':>5s} {'THR':>5s}  "
              f"{'n':>4s} {'W-L':>9s} {'Win%':>6s} {'Units':>9s} {'ROI':>7s}")
        for e in sorted(rows, key=lambda x: -x[sort_key])[:n]:
            print(f"  {e['bf']:5.2f} {e['var']:5.2f} {e['thr']:5.2f}  "
                  f"{e['n']:4d} {e['w']:3d}-{e['l']:<3d}   {e['pct']:5.1f}% "
                  f"{e['u']:+8.2f}u {e['roi']:+6.1f}%")

    _top("ROI", 'roi')
    _top("Total units", 'u', n=10)
    _top("Win %", 'pct', n=10)


if __name__ == "__main__":
    run_sweep()
