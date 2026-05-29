#!/usr/bin/env python3
"""
4D sweep: BF_MULT x BF_CAP x VAR_MULT x WHIFF_XBA_BLEND_WEIGHT.
Clean emp_std per combo. Runs on the leakage-fixed backfill() path.

Grid (54 combos):
  BF:    [0.90, 1.00, 1.10]
  CAP:   [23, 24]
  VAR:   [1.20, 1.30, 1.40]
  WHIFF: [0.60, 0.80, 1.00]

Usage:
    cd MLBstrikeouts
    python -m scripts.sweep_4d_whiff
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
    bf_grid = [0.90, 1.00, 1.10]
    cap_grid = [23, 24]
    var_grid = [1.20, 1.30, 1.40]
    whiff_grid = [0.60, 0.80, 1.00]
    total = len(bf_grid) * len(cap_grid) * len(var_grid) * len(whiff_grid)

    b_bf = defaults.BF_MULT
    b_var = dict(defaults.VAR_MULT)
    b_cap = defaults.BF_CAP
    b_whiff = defaults.WHIFF_XBA_BLEND_WEIGHT
    if os.path.exists(KALMAN):
        shutil.copy2(KALMAN, KALMAN + '.bak')

    results = {}
    try:
        i = 0
        for bf in bf_grid:
            for cap in cap_grid:
                for v in var_grid:
                    for wh in whiff_grid:
                        i += 1
                        defaults.BF_MULT = bf
                        defaults.BF_CAP = float(cap)
                        defaults.VAR_MULT['strikeouts'] = v
                        defaults.WHIFF_XBA_BLEND_WEIGHT = wh
                        _wipe_emp_std()
                        buf = io.StringIO()
                        with contextlib.redirect_stdout(buf):
                            r = backfill()
                        picks = (r or {}).get('strikeouts', {}).get('picks', []) or []
                        results[(bf, cap, v, wh)] = picks
                        np_ = sum(1 for p in picks if is_pick(p))
                        nl = sum(1 for p in picks if is_lean(p))
                        print(f'  [{i:2d}/{total}] BF={bf:.2f} CAP={cap} VAR={v:.2f} WHIFF={wh:.2f}  picks={np_} leans={nl}')
                        sys.stdout.flush()
    finally:
        defaults.BF_MULT = b_bf
        defaults.VAR_MULT.clear()
        defaults.VAR_MULT.update(b_var)
        defaults.BF_CAP = b_cap
        defaults.WHIFF_XBA_BLEND_WEIGHT = b_whiff
        if os.path.exists(KALMAN + '.bak'):
            shutil.move(KALMAN + '.bak', KALMAN)

    rows = []
    for key, pp in results.items():
        bf, cap, v, wh = key
        s = stats(pp)
        sr = stats(pp, cutoff='2026-05-04')
        rows.append((bf, cap, v, wh, s, sr))

    print()
    print('TOP 15 by RECENT PICKS-ONLY ROI:')
    print(f'  {"BF":>4} {"CAP":>3} {"VAR":>4} {"WHIF":>4}  {"recent picks":>26}  {"season picks":>26}')
    rows.sort(key=lambda x: -x[5]['p_roi'])
    for bf, cap, v, wh, s, sr in rows[:15]:
        print(f'  {bf:>4.2f} {cap:>3d} {v:>4.2f} {wh:>4.2f}  '
              f'{sr["n_p"]:>3d}p {sr["p_wr"]:>4.1f}% {sr["p_u"]:>+6.1f}u {sr["p_roi"]:>+5.2f}%  '
              f'{s["n_p"]:>3d}p {s["p_wr"]:>4.1f}% {s["p_u"]:>+7.1f}u {s["p_roi"]:>+5.2f}%')

    print()
    print('TOP 15 by RECENT COMBINED ROI:')
    rows.sort(key=lambda x: -x[5]['c_roi'])
    for bf, cap, v, wh, s, sr in rows[:15]:
        print(f'  {bf:>4.2f} {cap:>3d} {v:>4.2f} {wh:>4.2f}  '
              f'recent {sr["c_u"]:>+6.1f}u {sr["c_roi"]:>+5.2f}%  '
              f'(p {sr["n_p"]:>3d} {sr["p_wr"]:>4.1f}% / l {sr["n_l"]:>3d})  '
              f'season {s["c_u"]:>+6.1f}u {s["c_roi"]:>+5.2f}%')

    print()
    print('TOP 15 by SEASON COMBINED ROI:')
    rows.sort(key=lambda x: -x[4]['c_roi'])
    for bf, cap, v, wh, s, sr in rows[:15]:
        print(f'  {bf:>4.2f} {cap:>3d} {v:>4.2f} {wh:>4.2f}  '
              f'season {s["c_u"]:>+6.1f}u {s["c_roi"]:>+5.2f}%  '
              f'recent {sr["c_u"]:>+6.1f}u {sr["c_roi"]:>+5.2f}%')

    print()
    print('FULL GRID (season picks-only, by BF/CAP/VAR/WHIFF):')
    rows.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
    for bf, cap, v, wh, s, sr in rows:
        print(f'  BF={bf:.2f} CAP={cap} VAR={v:.2f} WHIFF={wh:.2f}  '
              f'{s["n_p"]:>3d}p {s["p_wr"]:>4.1f}% {s["p_u"]:>+7.1f}u {s["p_roi"]:>+5.2f}%')


if __name__ == '__main__':
    main()
