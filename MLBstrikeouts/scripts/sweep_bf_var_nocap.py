#!/usr/bin/env python3
"""
Sweep BF_MULT x VAR_MULT at BLEND=0.00 with the BF cap effectively removed
(BF_CAP = 100.0). Also includes a comparison row at BF_CAP=23.0 for the
recommended (BF, VAR) pair.

Usage:
    cd MLBstrikeouts
    python -m scripts.sweep_bf_var_nocap
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
    "..", "data", "sweep_bf_var_nocap_results.json",
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


def run_sweep(season=None, bf_grid=None, var_grid=None,
              recent_cutoff='2026-05-05', start_date=None):
    season = season or defaults.current_season()
    bf_grid = bf_grid or [0.85, 0.90, 0.95, 1.00, 1.05, 1.10]
    var_grid = var_grid or [0.85, 1.00, 1.15, 1.30]

    total = len(bf_grid) * len(var_grid)
    print(f"\n{'='*72}")
    print(f"  BF x VAR @ BLEND=0.00, BF_CAP=100 (no cap)  Season {season}")
    print(f"  BF:  {bf_grid}")
    print(f"  VAR: {var_grid}")
    print(f"  Total combos: {total}")
    print(f"{'='*72}")

    baseline_bf = defaults.BF_MULT
    baseline_var = dict(defaults.VAR_MULT)
    baseline_blend = PITCHER_KALMAN_DEFAULTS["kalmanBlend"]
    baseline_cap = defaults.BF_CAP

    backup = None
    if os.path.exists(KALMAN_STATE):
        backup = KALMAN_STATE + ".sweep-backup"
        shutil.copy2(KALMAN_STATE, backup)
        print("  Backed up kalman_state.json")

    summary = []
    i = 0
    try:
        # Lock BLEND=0 and BF_CAP=100 for the whole sweep
        PITCHER_KALMAN_DEFAULTS["kalmanBlend"] = 0.00
        defaults.BF_CAP = 100.0
        print(f"  BLEND fixed at 0.00, BF_CAP fixed at 100.0\n")

        for bf in bf_grid:
            for var in var_grid:
                i += 1
                print(f"--- [{i}/{total}] BF={bf:.2f} VAR={var:.2f} ---")
                defaults.BF_MULT = bf
                defaults.VAR_MULT['strikeouts'] = var

                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    r = backfill(season=season, start_date=start_date)
                picks = (r or {}).get('strikeouts', {}).get('picks', []) or []

                pa = [p for p in picks if p.get('pick') in ('OVER', 'UNDER')]
                lu = [p for p in picks if _is_lean_under(p)]
                lo = [p for p in picks if _is_lean_over(p)]
                aa = [p for p in picks if _all_actionable(p)]
                rec_aa = [p for p in aa if p.get('date', '') >= recent_cutoff]
                rec_pa = [p for p in pa if p.get('date', '') >= recent_cutoff]

                entry = {
                    'bf': bf, 'var': var, 'blend': 0.0, 'bf_cap': 100.0,
                    'picks_season': _stats(pa),
                    'picks_recent': _stats(rec_pa),
                    'lu_season': _stats(lu),
                    'lo_season': _stats(lo),
                    'all_season': _stats(aa),
                    'all_recent': _stats(rec_aa),
                }
                summary.append(entry)
                s = entry['all_season']; rr = entry['all_recent']
                print(f"  ALL season: {s['n']} {s['w']}-{s['l']} {s['pct']:.1f}% {s['u']:+.1f}u {s['roi']:+.1f}% | "
                      f"recent: {rr['n']} {rr['pct']:.1f}% {rr['u']:+.1f}u {rr['roi']:+.1f}%")

    finally:
        defaults.BF_MULT = baseline_bf
        defaults.VAR_MULT.clear()
        defaults.VAR_MULT.update(baseline_var)
        PITCHER_KALMAN_DEFAULTS["kalmanBlend"] = baseline_blend
        defaults.BF_CAP = baseline_cap
        if backup and os.path.exists(backup):
            shutil.move(backup, KALMAN_STATE)
            print("\n  Restored defaults and kalman_state.json")

    try:
        with open(RESULTS_FILE, 'w') as f:
            json.dump({'season': season, 'recent_cutoff': recent_cutoff,
                       'blend': 0.0, 'bf_cap': 100.0,
                       'results': summary}, f, indent=2)
    except Exception as e:
        print(f"  (save failed: {e})")

    # Reports
    def _table(title, metric_key, n=15):
        print(f"\n{'='*88}")
        print(f"  TOP {n} by {title} ROI")
        print(f"{'='*88}")
        print(f"  {'BF':>5s} {'VAR':>5s}  {'n':>4s} {'W-L':>9s} "
              f"{'Win%':>6s} {'Units':>9s} {'ROI':>7s}")
        for e in sorted(summary, key=lambda x: -x[metric_key]['roi'])[:n]:
            s = e[metric_key]
            print(f"  {e['bf']:5.2f} {e['var']:5.2f}  {s['n']:4d} {s['w']:3d}-{s['l']:<3d}   "
                  f"{s['pct']:5.1f}% {s['u']:+8.2f}u {s['roi']:+6.1f}%")

    _table("ALL-ACTIONABLE season", 'all_season')
    _table("ALL-ACTIONABLE since-5/05", 'all_recent', n=10)
    _table("PICKS season", 'picks_season', n=10)


if __name__ == "__main__":
    run_sweep()
