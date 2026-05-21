#!/usr/bin/env python3
"""
Joint 4D sweep: BF_MULT x VAR_MULT['strikeouts'] x BF_CAP x kalmanBlend.

Grid trimmed around the known good region (BLEND=0 dominates from prior
sweeps; BF clustered 0.88-1.00; VAR 0.85-1.30; cap 22-25).

Usage:
    cd MLBstrikeouts
    python -m scripts.sweep_4d
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
    "..", "data", "sweep_4d_results.json",
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


def _is_lean(p):
    """Unified lean band: pCov in [0.65, current pick threshold), both directions."""
    if p.get('pick') != 'PASS':
        return False
    pc = p.get('pCover') or 0
    thresh = defaults.MARKET_THRESHOLDS.get('strikeouts', {}).get('high', 0.72)
    return 0.65 <= pc < thresh


def _actionable(p):
    return p.get('pick') in ('OVER', 'UNDER') or _is_lean(p)


def run_sweep(season=None, bf_grid=None, var_grid=None, cap_grid=None,
              blend_grid=None, recent_cutoff='2026-05-05'):
    season = season or defaults.current_season()
    bf_grid = bf_grid or [0.88, 0.91, 0.95, 1.00]
    var_grid = var_grid or [0.85, 1.00, 1.15, 1.30]
    cap_grid = cap_grid or [22.0, 23.0, 25.0]
    blend_grid = blend_grid or [0.00, 0.20]

    total = len(bf_grid) * len(var_grid) * len(cap_grid) * len(blend_grid)
    print(f"\n{'='*72}")
    print(f"  4D SWEEP: BF x VAR x CAP x BLEND  Season {season}")
    print(f"  BF:    {bf_grid}")
    print(f"  VAR:   {var_grid}")
    print(f"  CAP:   {cap_grid}")
    print(f"  BLEND: {blend_grid}")
    print(f"  Total: {total}")
    print(f"{'='*72}\n")

    bb = defaults.BF_MULT; bv = dict(defaults.VAR_MULT)
    bk = PITCHER_KALMAN_DEFAULTS["kalmanBlend"]; bc = defaults.BF_CAP

    backup = None
    if os.path.exists(KALMAN_STATE):
        backup = KALMAN_STATE + ".sweep-backup"
        shutil.copy2(KALMAN_STATE, backup)

    rows = []
    i = 0
    try:
        for bf in bf_grid:
            for var in var_grid:
                for cap in cap_grid:
                    for blend in blend_grid:
                        i += 1
                        print(f"[{i:>3}/{total}] BF={bf:.2f} VAR={var:.2f} "
                              f"CAP={cap:.0f} BLEND={blend:.2f}", end='  ')
                        defaults.BF_MULT = bf
                        defaults.VAR_MULT['strikeouts'] = var
                        defaults.BF_CAP = cap
                        PITCHER_KALMAN_DEFAULTS["kalmanBlend"] = blend

                        buf = io.StringIO()
                        with contextlib.redirect_stdout(buf):
                            r = backfill(season=season)
                        picks = (r or {}).get('strikeouts', {}).get('picks', []) or []

                        aa = [p for p in picks if _actionable(p)]
                        pa = [p for p in picks if p.get('pick') in ('OVER','UNDER')]
                        ln = [p for p in picks if _is_lean(p)]
                        rec_aa = [p for p in aa if p.get('date','') >= recent_cutoff]

                        entry = {'bf': bf, 'var': var, 'cap': cap, 'blend': blend,
                                 'all_season': _stats(aa),
                                 'all_recent': _stats(rec_aa),
                                 'picks_season': _stats(pa),
                                 'lean_season': _stats(ln)}
                        rows.append(entry)
                        s = entry['all_season']
                        print(f"-> {s['n']} {s['pct']:.1f}% {s['u']:+.1f}u {s['roi']:+.1f}%")
    finally:
        defaults.BF_MULT = bb
        defaults.VAR_MULT.clear(); defaults.VAR_MULT.update(bv)
        PITCHER_KALMAN_DEFAULTS["kalmanBlend"] = bk
        defaults.BF_CAP = bc
        if backup and os.path.exists(backup):
            shutil.move(backup, KALMAN_STATE)
            print("\n  Restored defaults and kalman_state.json")

    try:
        with open(RESULTS_FILE, 'w') as f:
            json.dump({'season': season, 'results': rows}, f, indent=2)
    except Exception as e:
        print(f"  save failed: {e}")

    def _top(title, key, n=15):
        print(f"\n{'='*92}")
        print(f"  TOP {n} by {title}")
        print(f"{'='*92}")
        print(f"  {'BF':>5s} {'VAR':>5s} {'CAP':>5s} {'BLEND':>5s}  "
              f"{'n':>4s} {'W-L':>9s} {'Win%':>6s} {'Units':>9s} {'ROI':>7s}")
        for e in sorted(rows, key=lambda x: -x[key]['roi'])[:n]:
            s = e[key]
            print(f"  {e['bf']:5.2f} {e['var']:5.2f} {e['cap']:5.1f} {e['blend']:5.2f}  "
                  f"{s['n']:4d} {s['w']:3d}-{s['l']:<3d}   {s['pct']:5.1f}% "
                  f"{s['u']:+8.2f}u {s['roi']:+6.1f}%")

    _top("ALL-ACTIONABLE season ROI", 'all_season')
    _top("ALL-ACTIONABLE since-5/05 ROI", 'all_recent', n=10)
    _top("PICKS season ROI", 'picks_season', n=10)


if __name__ == "__main__":
    run_sweep()
