#!/usr/bin/env python3
"""
sweep_lineup_k_cap.py — sweep the opponent-lineup K-rate cap (LINEUP_K_CAP).

LINEUP_K_CAP is a hard ceiling applied to the opponent lineup K-rate in
props_engine.py BEFORE the matchup multiply (and before the PROJ_LINEUP_WEIGHT
league regression). 0.0 disables it. This sweep clamps the upper tail of the
lineup rate over a grid and reports walk-forward calibration / units off the
cached backfill data, so we can pick a ceiling (if any) that beats no-cap.

Clean emp_std cache is wiped per combo so each cap is graded leak-free.

Usage:
    cd MLBstrikeouts
    python -m scripts.sweep_lineup_k_cap
"""
import sys, os, glob, shutil, io, contextlib

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import defaults
from props_backfill import backfill

# Recent window cutoff (last ~4 weeks as of the cache date).
RECENT_CUTOFF = '2026-05-25'

# Cap grid. lineup_k_rate is a per-batter K fraction (league ~0.22, team-season
# spread ~0.18-0.25), so a 9-batter mean rarely clears the high-20s. Caps below
# that bite; caps above are no-ops. 0.0 = disabled baseline (current behavior).
CAP_GRID = [0.0, 0.24, 0.25, 0.26, 0.27, 0.28, 0.30, 0.32]

KALMAN = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "data", "kalman_state.json",
))
EMP_STD_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "data", "emp_std_cache", "mlb",
))


def _wipe_emp_std():
    n = 0
    for p in glob.glob(os.path.join(EMP_STD_DIR, "emp_std_*.json")):
        try:
            os.remove(p); n += 1
        except OSError:
            pass
    return n


def us(p, r, sz):
    o = p.get('odds')
    if o is None or r not in ('WIN', 'LOSS'):
        return 0.0
    if o > 0:
        return sz * (o / 100.0) if r == 'WIN' else -sz
    return sz if r == 'WIN' else sz * (-abs(o) / 100.0)


def risk(p, r, sz):
    o = p.get('odds')
    if o is None or r not in ('WIN', 'LOSS'):
        return 0.0
    return sz * (abs(o) / 100.0) if o < 0 else sz


def is_pick(p):
    return p.get('pick') in ('OVER', 'UNDER')


def is_lean(p):
    if p.get('pick') != 'PASS':
        return False
    pc = p.get('pCover') or 0
    return 0.65 <= pc < 0.72


def grade(p):
    return 'WIN' if p.get('won') else ('LOSS' if p.get('won') is False else None)


def stats(pp, cutoff=None):
    picks = [p for p in pp if is_pick(p)]
    leans = [p for p in pp if is_lean(p)]
    if cutoff:
        picks = [p for p in picks if p.get('date', '') >= cutoff]
        leans = [p for p in leans if p.get('date', '') >= cutoff]
    pu = sum(us(p, grade(p), 2.5) for p in picks)
    lu = sum(us(p, grade(p), 1.5) for p in leans)
    pr_ = sum(risk(p, grade(p), 2.5) for p in picks)
    lr = sum(risk(p, grade(p), 1.5) for p in leans)
    pw = sum(1 for p in picks if p['won'])
    pl = sum(1 for p in picks if not p['won'])
    p_wr = pw / (pw + pl) * 100 if pw + pl else 0
    p_roi = pu / pr_ * 100 if pr_ else 0
    c_roi = (pu + lu) / (pr_ + lr) * 100 if (pr_ + lr) else 0
    return {'n_p': len(picks), 'p_wr': p_wr, 'p_u': pu, 'p_roi': p_roi,
            'n_l': len(leans), 'l_u': lu,
            'c_u': pu + lu, 'c_roi': c_roi}


def main():
    print(f"\n  Sweeping LINEUP_K_CAP over {CAP_GRID}")
    print(f"  (current default: {defaults.LINEUP_K_CAP}; 0.0 = no cap)\n")

    b_cap = defaults.LINEUP_K_CAP
    if os.path.exists(KALMAN):
        shutil.copy2(KALMAN, KALMAN + '.bak')

    results = {}
    try:
        for i, cap in enumerate(CAP_GRID, 1):
            defaults.LINEUP_K_CAP = cap
            _wipe_emp_std()
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                r = backfill()
            picks = (r or {}).get('strikeouts', {}).get('picks', []) or []
            results[cap] = picks
            np_ = sum(1 for p in picks if is_pick(p))
            nl = sum(1 for p in picks if is_lean(p))
            tag = 'OFF' if cap == 0.0 else f'{cap:.2f}'
            print(f'  [{i:2d}/{len(CAP_GRID)}] CAP={tag:>4}  picks={np_} leans={nl}')
            sys.stdout.flush()
    finally:
        defaults.LINEUP_K_CAP = b_cap
        if os.path.exists(KALMAN + '.bak'):
            shutil.move(KALMAN + '.bak', KALMAN)

    rows = []
    for cap, pp in results.items():
        rows.append((cap, stats(pp), stats(pp, cutoff=RECENT_CUTOFF)))

    def _tag(cap):
        return 'OFF' if cap == 0.0 else f'{cap:.2f}'

    hdr = (f'  {"CAP":>4}  {"recent (>= " + RECENT_CUTOFF + ") picks":>30}'
           f'  {"season picks":>30}')
    print('\nBY CAP (picks-only, 2.5u):')
    print(hdr)
    for cap, s, sr in sorted(rows, key=lambda x: x[0]):
        print(f'  {_tag(cap):>4}  '
              f'{sr["n_p"]:>3d}p {sr["p_wr"]:>4.1f}% {sr["p_u"]:>+6.1f}u {sr["p_roi"]:>+5.2f}%  '
              f'{s["n_p"]:>3d}p {s["p_wr"]:>4.1f}% {s["p_u"]:>+7.1f}u {s["p_roi"]:>+5.2f}%')

    print('\nTOP by RECENT picks-only ROI:')
    for cap, s, sr in sorted(rows, key=lambda x: -x[2]['p_roi']):
        print(f'  CAP={_tag(cap):>4}  '
              f'recent {sr["n_p"]:>3d}p {sr["p_wr"]:>4.1f}% {sr["p_u"]:>+6.1f}u {sr["p_roi"]:>+5.2f}%  '
              f'season {s["n_p"]:>3d}p {s["p_wr"]:>4.1f}% {s["p_u"]:>+7.1f}u {s["p_roi"]:>+5.2f}%')

    print('\nTOP by SEASON combined ROI:')
    for cap, s, sr in sorted(rows, key=lambda x: -x[1]['c_roi']):
        print(f'  CAP={_tag(cap):>4}  '
              f'season {s["c_u"]:>+6.1f}u {s["c_roi"]:>+5.2f}%  '
              f'recent {sr["c_u"]:>+6.1f}u {sr["c_roi"]:>+5.2f}%')


if __name__ == '__main__':
    main()
