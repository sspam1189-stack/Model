#!/usr/bin/env python3
"""Compare PRODUCTION config vs the 'whiff .50 1.3 24 1' config.

PROD  (shipped):  K_QUALITY_METRIC=csw   W=0.3  BF=1.00  VAR=1.20  CAP=25  KCAP=0.40
WHIFF (candidate):K_QUALITY_METRIC=whiff W=0.50 BF=1.00  VAR=1.30  CAP=24  KCAP=0.40

Both are run through the same leak-free walk-forward backfill; records are then
reported for the post-leakage-fix window (fix shipped 2026-05-28, commit
f9672c9a) as well as the full season for context.
"""
import sys, os, glob, io, contextlib

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import defaults
from props_backfill import backfill

EMP_STD_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "data", "emp_std_cache", "mlb",
))


def _wipe_emp_std():
    for p in glob.glob(os.path.join(EMP_STD_DIR, "emp_std_*.json")):
        try:
            os.remove(p)
        except OSError:
            pass


def is_pick(p):
    return p.get('pick') in ('OVER', 'UNDER')


def is_lean(p):
    if p.get('pick') != 'PASS':
        return False
    pc = p.get('pCover') or 0
    return 0.65 <= pc < 0.72


def grade(p):
    if 'won' in p:
        if p['won'] is True:
            return 'WIN'
        if p['won'] is False:
            return 'LOSS'
        return None
    if p.get('actual') is None or p.get('line') is None:
        return None
    wbp = p.get('would_be_pick') or p.get('pick') or (
        'OVER' if (p.get('proj') or 0) > (p.get('line') or 0) else 'UNDER')
    if wbp == 'OVER':
        return 'WIN' if p['actual'] > p['line'] else 'LOSS'
    return 'WIN' if p['actual'] < p['line'] else 'LOSS'


def units(p, sz):
    o = p.get('odds')
    r = grade(p)
    if o is None or r not in ('WIN', 'LOSS'):
        return 0.0, 0.0
    if o > 0:
        return (sz * (o / 100.0) if r == 'WIN' else -sz), sz
    return (sz if r == 'WIN' else sz * (-abs(o) / 100.0)), sz * (abs(o) / 100.0)


def run_config(metric, w, bf, var, cap, kcap):
    defaults.K_QUALITY_METRIC = metric
    defaults.CSW_XBA_BLEND_WEIGHT = w
    defaults.BF_MULT = bf
    defaults.VAR_MULT['strikeouts'] = var
    defaults.BF_CAP = float(cap)
    defaults.K_RATE_CAP_FLOOR = kcap
    _wipe_emp_std()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = backfill()
    return (r or {}).get('strikeouts', {}).get('picks', []) or []


def tier(plays, sz):
    w = sum(1 for p in plays if grade(p) == 'WIN')
    l = sum(1 for p in plays if grade(p) == 'LOSS')
    u = 0.0
    risk = 0.0
    for p in plays:
        pu, pr = units(p, sz)
        u += pu
        risk += pr
    return {
        'n': w + l, 'w': w, 'l': l,
        'wr': w / (w + l) * 100 if w + l else 0,
        'u': u, 'risk': risk,
        'roi': u / risk * 100 if risk else 0,
    }


def report(label, picks, leans, start, end, pick_sz=2.5, lean_sz=1.5):
    pp = [p for p in picks if start <= p.get('date', '') <= end and grade(p)]
    lp = [p for p in leans if start <= p.get('date', '') <= end and grade(p)]
    ps = tier(pp, pick_sz)
    ls = tier(lp, lean_sz)
    cu = ps['u'] + ls['u']
    crisk = ps['risk'] + ls['risk']
    cn = ps['n'] + ls['n']
    cw = ps['w'] + ls['w']
    cl = ps['l'] + ls['l']
    print(f'  {label}')
    print(f'    PICKS  {ps["n"]:>3d}  {ps["w"]:>2d}-{ps["l"]:<2d}  '
          f'{ps["wr"]:>5.1f}%  {ps["u"]:>+7.2f}u  ROI {ps["roi"]:>+6.2f}%')
    print(f'    LEANS  {ls["n"]:>3d}  {ls["w"]:>2d}-{ls["l"]:<2d}  '
          f'{ls["wr"]:>5.1f}%  {ls["u"]:>+7.2f}u  ROI {ls["roi"]:>+6.2f}%')
    print(f'    COMB   {cn:>3d}  {cw:>2d}-{cl:<2d}  '
          f'{(cw/(cw+cl)*100 if cw+cl else 0):>5.1f}%  {cu:>+7.2f}u  '
          f'ROI {(cu/crisk*100 if crisk else 0):>+6.2f}%')
    return {'cu': cu, 'cn': cn, 'cw': cw, 'cl': cl, 'pu': ps['u'], 'pn': ps['n']}


def main():
    # Snapshot production defaults so we can restore.
    snap = (defaults.K_QUALITY_METRIC, defaults.CSW_XBA_BLEND_WEIGHT,
            defaults.BF_MULT, dict(defaults.VAR_MULT),
            defaults.BF_CAP, defaults.K_RATE_CAP_FLOOR)

    print('Running PROD  (csw   W=0.3  BF=1.00 VAR=1.20 CAP=25 KCAP=0.40) ...')
    PROD = run_config('csw', 0.3, 1.00, 1.20, 25, 0.40)
    print('Running WHIFF (whiff W=0.50 BF=1.00 VAR=1.30 CAP=24 KCAP=0.40) ...')
    WHIFF = run_config('whiff', 0.50, 1.00, 1.30, 24, 0.40)

    # Restore production defaults.
    (defaults.K_QUALITY_METRIC, defaults.CSW_XBA_BLEND_WEIGHT,
     defaults.BF_MULT, _var, defaults.BF_CAP,
     defaults.K_RATE_CAP_FLOOR) = snap
    defaults.VAR_MULT.clear()
    defaults.VAR_MULT.update(_var)

    prod_p = [p for p in PROD if is_pick(p)]
    prod_l = [p for p in PROD if is_lean(p)]
    whiff_p = [p for p in WHIFF if is_pick(p)]
    whiff_l = [p for p in WHIFF if is_lean(p)]

    windows = [
        ('POST-LEAKAGE-FIX 5/28-5/31', '2026-05-28', '2026-05-31'),
        ('SEASON (context)', '2026-01-01', '2026-12-31'),
    ]
    for wlabel, start, end in windows:
        print('\n' + '=' * 62)
        print(f'  {wlabel}   (picks 2.5u / leans 1.5u)')
        print('=' * 62)
        a = report('PROD  (csw)  ', prod_p, prod_l, start, end)
        print()
        b = report('WHIFF        ', whiff_p, whiff_l, start, end)
        print(f'\n  delta (WHIFF - PROD): combined {b["cu"] - a["cu"]:+.2f}u  '
              f'| picks {b["pu"] - a["pu"]:+.2f}u over '
              f'{b["pn"]} vs {a["pn"]} picks')


if __name__ == '__main__':
    main()
