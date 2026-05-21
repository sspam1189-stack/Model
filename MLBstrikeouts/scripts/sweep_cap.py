#!/usr/bin/env python3
"""
Sweep BF_CAP at the recommended config (BF=0.95, VAR=1.15, BLEND=0.00) to
find the optimal cap value.

Usage:
    cd MLBstrikeouts
    python -m scripts.sweep_cap
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


def run_sweep(season=None, cap_grid=None, recent_cutoff='2026-05-05'):
    season = season or defaults.current_season()
    cap_grid = cap_grid or [21.0, 22.0, 23.0, 24.0, 25.0, 26.0, 27.0, 30.0]

    print(f"\n{'='*72}")
    print(f"  BF_CAP SWEEP  BF=0.95 VAR=1.15 BLEND=0.00  Season {season}")
    print(f"  Caps: {cap_grid}")
    print(f"{'='*72}\n")

    bb = defaults.BF_MULT; bv = dict(defaults.VAR_MULT)
    bk = PITCHER_KALMAN_DEFAULTS["kalmanBlend"]; bc = defaults.BF_CAP

    backup = None
    if os.path.exists(KALMAN_STATE):
        backup = KALMAN_STATE + ".sweep-backup"
        shutil.copy2(KALMAN_STATE, backup)

    rows = []
    try:
        defaults.BF_MULT = 0.95
        defaults.VAR_MULT['strikeouts'] = 1.15
        PITCHER_KALMAN_DEFAULTS["kalmanBlend"] = 0.00

        for i, cap in enumerate(cap_grid, 1):
            defaults.BF_CAP = cap
            print(f"--- [{i}/{len(cap_grid)}] BF_CAP = {cap:.1f} ---")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                r = backfill(season=season)
            picks = (r or {}).get('strikeouts', {}).get('picks', []) or []
            aa = [p for p in picks if _actionable(p)]
            pa = [p for p in picks if p.get('pick') in ('OVER','UNDER')]
            ln = [p for p in picks if _is_lean(p)]
            rec_aa = [p for p in aa if p.get('date','') >= recent_cutoff]
            entry = {'cap': cap,
                     'all_season': _stats(aa), 'all_recent': _stats(rec_aa),
                     'picks_season': _stats(pa),
                     'lean_season': _stats(ln)}
            rows.append(entry)
            s = entry['all_season']; rr = entry['all_recent']
            print(f"  ALL season: {s['n']} {s['w']}-{s['l']} {s['pct']:.1f}% {s['u']:+.1f}u {s['roi']:+.1f}% | "
                  f"recent: {rr['n']} {rr['pct']:.1f}% {rr['u']:+.1f}u {rr['roi']:+.1f}%")
    finally:
        defaults.BF_MULT = bb
        defaults.VAR_MULT.clear(); defaults.VAR_MULT.update(bv)
        PITCHER_KALMAN_DEFAULTS["kalmanBlend"] = bk
        defaults.BF_CAP = bc
        if backup and os.path.exists(backup):
            shutil.move(backup, KALMAN_STATE)
            print("\n  Restored defaults and kalman_state.json")

    def _table(title, key):
        print(f"\n{'='*72}\n  {title}\n{'='*72}")
        print(f"  {'CAP':>5s}  {'n':>4s} {'W-L':>9s} {'Win%':>6s} {'Units':>9s} {'ROI':>7s}")
        for e in rows:
            s = e[key]
            print(f"  {e['cap']:5.1f}  {s['n']:4d} {s['w']:3d}-{s['l']:<3d}   "
                  f"{s['pct']:5.1f}% {s['u']:+8.2f}u {s['roi']:+6.1f}%")

    _table("ALL-ACTIONABLE season (by CAP)", 'all_season')
    _table("ALL-ACTIONABLE since-5/05", 'all_recent')
    _table("PICKS only", 'picks_season')
    _table("LEAN season", 'lean_season')


if __name__ == "__main__":
    run_sweep()
