#!/usr/bin/env python3
"""
Joint 3D sweep: BF_MULT x VAR_MULT['strikeouts'] x PITCHER_KALMAN_DEFAULTS['kalmanBlend'].

Runs the full walk-forward backfill at each combination, then slices the
results for conditional answers:
  - BF=1.00 fixed   -> best VAR x Blend
  - VAR=1.00 fixed  -> best BF x Blend
  - Blend=0.50 fixed -> best BF x VAR
  - Global top-N overall

Usage:
    cd MLBstrikeouts
    python -m scripts.sweep_3d
"""

import sys, os, shutil, io, contextlib, json
from collections import defaultdict

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
    "..", "data", "sweep_3d_results.json",
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


def _stats(picks):
    w = sum(1 for p in picks if p['won'])
    l = len(picks) - w
    n = w + l
    u = sum(_units(p) for p in picks)
    r = sum(_risked(p) for p in picks)
    pct = w / n * 100 if n else 0
    roi = u / r * 100 if r else 0
    return {'n': n, 'w': w, 'l': l, 'pct': pct, 'u': u, 'roi': roi}


def _is_lean_under(p):
    return (p.get('pick') == 'PASS'
            and p.get('would_be_pick') == 'UNDER'
            and 0.60 <= (p.get('pCover') or 0) < 0.70)


def _is_lean_over(p):
    return (p.get('pick') == 'PASS'
            and p.get('would_be_pick') == 'OVER'
            and 0.65 <= (p.get('pCover') or 0) < 0.70)


def _all_actionable(p):
    return p.get('pick') in ('OVER', 'UNDER') or _is_lean_under(p) or _is_lean_over(p)


def run_sweep(season=None, bf_grid=None, var_grid=None, blend_grid=None,
              recent_cutoff='2026-05-05', start_date=None):
    season = season or defaults.current_season()
    bf_grid = bf_grid or [0.85, 0.88, 0.91, 0.95, 1.00]
    var_grid = var_grid or [0.85, 1.00, 1.15, 1.30]
    blend_grid = blend_grid or [0.00, 0.20, 0.30, 0.50, 0.70]

    total = len(bf_grid) * len(var_grid) * len(blend_grid)
    print(f"\n{'='*72}")
    print(f"  3D SWEEP  BF x VAR x BLEND  Season {season}")
    print(f"  BF:    {bf_grid}")
    print(f"  VAR:   {var_grid}")
    print(f"  BLEND: {blend_grid}")
    print(f"  Total combos: {total}")
    print(f"  Recent cutoff: {recent_cutoff}")
    print(f"{'='*72}")

    # Save baselines for restore
    baseline_bf = defaults.BF_MULT
    baseline_var = dict(defaults.VAR_MULT)
    baseline_blend = PITCHER_KALMAN_DEFAULTS["kalmanBlend"]

    # Back up Kalman state
    backup = None
    if os.path.exists(KALMAN_STATE):
        backup = KALMAN_STATE + ".sweep-backup"
        shutil.copy2(KALMAN_STATE, backup)
        print(f"  Backed up kalman_state.json")

    summary = []  # list of dicts with (bf, var, blend) and aggregated metrics
    i = 0
    try:
        for bf in bf_grid:
            for var in var_grid:
                for blend in blend_grid:
                    i += 1
                    print(f"\n--- [{i}/{total}] BF={bf:.2f} VAR={var:.2f} BLEND={blend:.2f} ---")
                    defaults.BF_MULT = bf
                    defaults.VAR_MULT['strikeouts'] = var
                    PITCHER_KALMAN_DEFAULTS["kalmanBlend"] = blend

                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        r = backfill(season=season, start_date=start_date)
                    picks = (r or {}).get('strikeouts', {}).get('picks', []) or []

                    picks_arr = [p for p in picks if p.get('pick') in ('OVER', 'UNDER')]
                    lu_arr = [p for p in picks if _is_lean_under(p)]
                    lo_arr = [p for p in picks if _is_lean_over(p)]
                    all_arr = [p for p in picks if _all_actionable(p)]

                    recent_picks_arr = [p for p in picks_arr if p.get('date', '') >= recent_cutoff]
                    recent_all_arr = [p for p in all_arr if p.get('date', '') >= recent_cutoff]

                    entry = {
                        'bf': bf, 'var': var, 'blend': blend,
                        'picks_season': _stats(picks_arr),
                        'picks_recent': _stats(recent_picks_arr),
                        'lu_season': _stats(lu_arr),
                        'lo_season': _stats(lo_arr),
                        'all_season': _stats(all_arr),
                        'all_recent': _stats(recent_all_arr),
                    }
                    summary.append(entry)
                    s = entry['all_season']
                    print(f"  ALL season: n={s['n']} {s['w']}-{s['l']} "
                          f"{s['pct']:.1f}% {s['u']:+.1f}u {s['roi']:+.1f}%")

    finally:
        defaults.BF_MULT = baseline_bf
        defaults.VAR_MULT.clear()
        defaults.VAR_MULT.update(baseline_var)
        PITCHER_KALMAN_DEFAULTS["kalmanBlend"] = baseline_blend
        if backup and os.path.exists(backup):
            shutil.move(backup, KALMAN_STATE)
            print(f"\n  Restored defaults and kalman_state.json")

    # Persist to disk for ad-hoc analysis
    try:
        with open(RESULTS_FILE, 'w') as f:
            json.dump({'season': season, 'recent_cutoff': recent_cutoff,
                       'results': summary}, f, indent=2)
        print(f"  Saved raw results: {RESULTS_FILE}")
    except Exception as e:
        print(f"  (could not save raw results: {e})")

    # --- Reports ---
    def _key_for(metric_key, sort_key='roi'):
        return lambda e: -e[metric_key][sort_key]

    def _print_top(label, metric_key, n=10):
        print(f"\n{'='*88}")
        print(f"  TOP {n} by {label} ROI")
        print(f"{'='*88}")
        print(f"  {'BF':>5s} {'VAR':>5s} {'BLEND':>5s}  {'n':>4s} {'W-L':>9s} "
              f"{'Win%':>6s} {'Units':>9s} {'ROI':>7s}")
        for e in sorted(summary, key=_key_for(metric_key))[:n]:
            s = e[metric_key]
            print(f"  {e['bf']:5.2f} {e['var']:5.2f} {e['blend']:5.2f}  "
                  f"{s['n']:4d} {s['w']:3d}-{s['l']:<3d}   {s['pct']:5.1f}% "
                  f"{s['u']:+8.2f}u {s['roi']:+6.1f}%")

    def _print_slice(label, predicate, metric_key='all_season', n=8):
        sub = [e for e in summary if predicate(e)]
        if not sub:
            print(f"\n(no entries for {label})")
            return
        print(f"\n{'='*88}")
        print(f"  SLICE: {label}  ranked by {metric_key} ROI")
        print(f"{'='*88}")
        print(f"  {'BF':>5s} {'VAR':>5s} {'BLEND':>5s}  {'n':>4s} {'W-L':>9s} "
              f"{'Win%':>6s} {'Units':>9s} {'ROI':>7s}")
        for e in sorted(sub, key=_key_for(metric_key))[:n]:
            s = e[metric_key]
            print(f"  {e['bf']:5.2f} {e['var']:5.2f} {e['blend']:5.2f}  "
                  f"{s['n']:4d} {s['w']:3d}-{s['l']:<3d}   {s['pct']:5.1f}% "
                  f"{s['u']:+8.2f}u {s['roi']:+6.1f}%")

    # Conditional slices
    _print_slice("BF = 1.00 (best VAR x BLEND)",
                 lambda e: abs(e['bf'] - 1.00) < 1e-6)
    _print_slice("VAR = 1.00 (best BF x BLEND)",
                 lambda e: abs(e['var'] - 1.00) < 1e-6)
    _print_slice("BLEND = 0.50 (best BF x VAR)",
                 lambda e: abs(e['blend'] - 0.50) < 1e-6)

    # Top globally by each metric
    _print_top("ALL-ACTIONABLE season", 'all_season', n=15)
    _print_top("PICKS season", 'picks_season', n=10)
    _print_top("ALL-ACTIONABLE since 5/05", 'all_recent', n=10)


if __name__ == "__main__":
    run_sweep()
