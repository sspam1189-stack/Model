#!/usr/bin/env python3
"""
BF x VAR sweep that wipes emp_std cache before each combo so each
projection batch fits its own walk-forward emp_std (avoids residual
leakage from a previously cached emp_std computed under a different
BF/VAR setting).

Usage:
    cd MLBstrikeouts
    python -m scripts.sweep_bf_var_clean
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
    pr = sum(risk(p, grade(p), 2.5) for p in picks)
    lr = sum(risk(p, grade(p), 1.5) for p in leans)
    pw = sum(1 for p in picks if p['won'])
    pl = sum(1 for p in picks if not p['won'])
    lw = sum(1 for p in leans if p['won'])
    ll = sum(1 for p in leans if not p['won'])
    roi = (pu + lu) / (pr + lr) * 100 if (pr + lr) else 0
    p_wr = pw / (pw + pl) * 100 if pw + pl else 0
    l_wr = lw / (lw + ll) * 100 if lw + ll else 0
    return {
        'n_p': len(picks), 'p_wr': p_wr, 'p_u': pu,
        'n_l': len(leans), 'l_wr': l_wr, 'l_u': lu,
        'c_u': pu + lu, 'c_roi': roi,
    }


def main():
    bf_grid = [1.10, 1.15, 1.20, 1.25, 1.30]
    var_grid = [1.10, 1.20, 1.30, 1.40]
    total = len(bf_grid) * len(var_grid)

    b_bf = defaults.BF_MULT
    b_var = dict(defaults.VAR_MULT)
    if os.path.exists(KALMAN):
        shutil.copy2(KALMAN, KALMAN + '.bak')

    results = {}
    try:
        i = 0
        for bf in bf_grid:
            for v in var_grid:
                i += 1
                defaults.BF_MULT = bf
                defaults.VAR_MULT['strikeouts'] = v
                n_wiped = _wipe_emp_std()
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    r = backfill()
                picks = (r or {}).get('strikeouts', {}).get('picks', []) or []
                results[(bf, v)] = picks
                print(f'  [{i:2d}/{total}] BF={bf:.2f} VAR={v:.2f}  (wiped {n_wiped} emp_std files)  picks={sum(1 for p in picks if is_pick(p))} leans={sum(1 for p in picks if is_lean(p))}')
                sys.stdout.flush()
    finally:
        defaults.BF_MULT = b_bf
        defaults.VAR_MULT.clear()
        defaults.VAR_MULT.update(b_var)
        if os.path.exists(KALMAN + '.bak'):
            shutil.move(KALMAN + '.bak', KALMAN)

    print()
    print('SEASON sized 2.5/1.5 — combined units (ROI%):')
    print('  BF\\VAR  ' + '  '.join(f'{v:>20.2f}' for v in var_grid))
    for bf in bf_grid:
        cells = []
        for v in var_grid:
            s = stats(results[(bf, v)])
            cells.append(f'{s["c_u"]:>+8.1f}u {s["c_roi"]:>+5.1f}%')
        print(f'  {bf:>5.2f}  ' + '  '.join(f'{c:>20}' for c in cells))

    print()
    print('RECENT 5/4+ sized — combined units (ROI%):')
    print('  BF\\VAR  ' + '  '.join(f'{v:>20.2f}' for v in var_grid))
    for bf in bf_grid:
        cells = []
        for v in var_grid:
            s = stats(results[(bf, v)], cutoff='2026-05-04')
            cells.append(f'{s["c_u"]:>+8.1f}u {s["c_roi"]:>+5.1f}%')
        print(f'  {bf:>5.2f}  ' + '  '.join(f'{c:>20}' for c in cells))

    print()
    print('TOP 10 by SEASON ROI:')
    rows = []
    for (bf, v), pp in results.items():
        s = stats(pp)
        sr = stats(pp, cutoff='2026-05-04')
        rows.append((bf, v, s, sr))
    rows.sort(key=lambda x: -x[2]['c_roi'])
    for bf, v, s, sr in rows[:10]:
        print(f'  BF={bf:.2f} VAR={v:.2f}  season {s["c_u"]:+.1f}u {s["c_roi"]:+.2f}% '
              f'({s["n_p"]}p {s["p_wr"]:.1f}% / {s["n_l"]}l {s["l_wr"]:.1f}%)  '
              f'recent {sr["c_u"]:+.1f}u {sr["c_roi"]:+.2f}%')

    print()
    print('TOP 10 by RECENT ROI (combined):')
    rows.sort(key=lambda x: -x[3]['c_roi'])
    for bf, v, s, sr in rows[:10]:
        print(f'  BF={bf:.2f} VAR={v:.2f}  recent {sr["c_u"]:+.1f}u {sr["c_roi"]:+.2f}%  '
              f'season {s["c_u"]:+.1f}u {s["c_roi"]:+.2f}%')

    # Picks-only ROI helper
    def picks_only(pp, cutoff=None):
        picks = [p for p in pp if is_pick(p)]
        if cutoff:
            picks = [p for p in picks if p.get('date', '') >= cutoff]
        pu = sum(us(p, grade(p), 2.5) for p in picks)
        pr = sum(risk(p, grade(p), 2.5) for p in picks)
        pw = sum(1 for p in picks if p['won'])
        pl = sum(1 for p in picks if not p['won'])
        roi = pu / pr * 100 if pr else 0
        wr = pw / (pw + pl) * 100 if pw + pl else 0
        return {'n': len(picks), 'wr': wr, 'u': pu, 'roi': roi}

    print()
    print('TOP 10 by RECENT PICKS-ONLY ROI:')
    rows_pp = []
    for (bf, v), pp in results.items():
        po = picks_only(pp, cutoff='2026-05-04')
        ps = picks_only(pp)
        rows_pp.append((bf, v, ps, po))
    rows_pp.sort(key=lambda x: -x[3]['roi'])
    for bf, v, ps, po in rows_pp[:10]:
        print(f'  BF={bf:.2f} VAR={v:.2f}  recent picks {po["n"]} {po["wr"]:.1f}% '
              f'{po["u"]:+.1f}u  ROI {po["roi"]:+.2f}%  '
              f'(season picks {ps["n"]} {ps["wr"]:.1f}% {ps["u"]:+.1f}u {ps["roi"]:+.2f}%)')


if __name__ == '__main__':
    main()
