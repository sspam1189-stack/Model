#!/usr/bin/env python3
"""
BF_MULT x BF_CAP sweep at VAR=1.30, clean emp_std per combo.

Tests whether the BF_CAP=23.0 ceiling is too restrictive now that
BF_MULT >= 1.20 pushes raw projected BF toward 25-30 for elite starters.

Usage:
    cd MLBstrikeouts
    python -m scripts.sweep_bf_cap_clean
"""
import sys, os, glob, shutil, io, contextlib

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import defaults
from props_backfill import backfill

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
    bf_grid = [1.10, 1.20, 1.25, 1.30]
    cap_grid = [22, 23, 24, 25, 26, 28]
    var_fixed = 1.30
    total = len(bf_grid) * len(cap_grid)

    b_bf = defaults.BF_MULT
    b_var = dict(defaults.VAR_MULT)
    b_cap = defaults.BF_CAP
    if os.path.exists(KALMAN):
        shutil.copy2(KALMAN, KALMAN + '.bak')

    defaults.VAR_MULT['strikeouts'] = var_fixed

    results = {}
    try:
        i = 0
        for bf in bf_grid:
            for cap in cap_grid:
                i += 1
                defaults.BF_MULT = bf
                defaults.BF_CAP = float(cap)
                n_wiped = _wipe_emp_std()
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    r = backfill()
                picks = (r or {}).get('strikeouts', {}).get('picks', []) or []
                results[(bf, cap)] = picks
                np_ = sum(1 for p in picks if is_pick(p))
                nl = sum(1 for p in picks if is_lean(p))
                print(f'  [{i:2d}/{total}] BF={bf:.2f} CAP={cap}  picks={np_} leans={nl}')
                sys.stdout.flush()
    finally:
        defaults.BF_MULT = b_bf
        defaults.VAR_MULT.clear()
        defaults.VAR_MULT.update(b_var)
        defaults.BF_CAP = b_cap
        if os.path.exists(KALMAN + '.bak'):
            shutil.move(KALMAN + '.bak', KALMAN)

    print()
    print(f'SEASON sized 2.5/1.5 @ VAR={var_fixed} — combined units (combined ROI):')
    print('  BF\\CAP  ' + '  '.join(f'{c:>20d}' for c in cap_grid))
    for bf in bf_grid:
        cells = []
        for cap in cap_grid:
            s = stats(results[(bf, cap)])
            cells.append(f'{s["c_u"]:>+8.1f}u {s["c_roi"]:>+5.1f}%')
        print(f'  {bf:>5.2f}  ' + '  '.join(f'{c:>20}' for c in cells))

    print()
    print(f'RECENT 5/4+ sized — combined units (combined ROI):')
    print('  BF\\CAP  ' + '  '.join(f'{c:>20d}' for c in cap_grid))
    for bf in bf_grid:
        cells = []
        for cap in cap_grid:
            s = stats(results[(bf, cap)], cutoff='2026-05-04')
            cells.append(f'{s["c_u"]:>+8.1f}u {s["c_roi"]:>+5.1f}%')
        print(f'  {bf:>5.2f}  ' + '  '.join(f'{c:>20}' for c in cells))

    print()
    print('RECENT picks-only ROI:')
    print('  BF\\CAP  ' + '  '.join(f'{c:>20d}' for c in cap_grid))
    for bf in bf_grid:
        cells = []
        for cap in cap_grid:
            s = stats(results[(bf, cap)], cutoff='2026-05-04')
            cells.append(f'{s["n_p"]:>3d}p {s["p_wr"]:>4.1f}% {s["p_roi"]:>+5.1f}%')
        print(f'  {bf:>5.2f}  ' + '  '.join(f'{c:>20}' for c in cells))

    rows = []
    for (bf, cap), pp in results.items():
        s = stats(pp)
        sr = stats(pp, cutoff='2026-05-04')
        rows.append((bf, cap, s, sr))

    print()
    print('TOP 10 by RECENT COMBINED ROI:')
    rows.sort(key=lambda x: -x[3]['c_roi'])
    for bf, cap, s, sr in rows[:10]:
        print(f'  BF={bf:.2f} CAP={cap}  recent combined {sr["c_u"]:+.1f}u {sr["c_roi"]:+.2f}%  '
              f'(picks {sr["n_p"]} {sr["p_wr"]:.1f}% {sr["p_roi"]:+.2f}%)  '
              f'season {s["c_u"]:+.1f}u {s["c_roi"]:+.2f}%')

    print()
    print('TOP 10 by RECENT PICKS-ONLY ROI:')
    rows.sort(key=lambda x: -x[3]['p_roi'])
    for bf, cap, s, sr in rows[:10]:
        print(f'  BF={bf:.2f} CAP={cap}  recent picks {sr["n_p"]} {sr["p_wr"]:.1f}% '
              f'{sr["p_u"]:+.1f}u  ROI {sr["p_roi"]:+.2f}%  '
              f'(season {s["n_p"]} {s["p_wr"]:.1f}% {s["p_roi"]:+.2f}%)')


if __name__ == '__main__':
    main()
