#!/usr/bin/env python3
"""Daily side-by-side: BF=1.30 CAP=23 (picks+leans 2/1) vs BF=1.30 CAP=26 (picks-only 3u)."""
import sys, os, glob, shutil, io, contextlib

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


def is_pick(p): return p.get('pick') in ('OVER', 'UNDER')
def is_lean(p):
    if p.get('pick') != 'PASS': return False
    pc = p.get('pCover') or 0
    return 0.65 <= pc < 0.72
def grade(p): return 'WIN' if p.get('won') else ('LOSS' if p.get('won') is False else None)


def units(p, sz):
    o = p.get('odds'); r = grade(p)
    if o is None or r not in ('WIN', 'LOSS'): return 0.0
    if o > 0:
        return sz * (o / 100.0) if r == 'WIN' else -sz
    return sz if r == 'WIN' else sz * (-abs(o) / 100.0)


def run_config(bf, cap, var, kcap):
    defaults.BF_MULT = bf
    defaults.BF_CAP = float(cap)
    defaults.VAR_MULT['strikeouts'] = var
    defaults.K_RATE_CAP_FLOOR = kcap
    _wipe_emp_std()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = backfill()
    return (r or {}).get('strikeouts', {}).get('picks', []) or []


def daily(pp, label, pick_sz, lean_sz):
    """Aggregate per date."""
    dates = sorted({p.get('date', '') for p in pp
                    if p.get('date', '') >= '2026-05-04' and p.get('actual') is not None})
    out = []
    for d in dates:
        picks = [p for p in pp if p.get('date') == d and is_pick(p)]
        leans = [p for p in pp if p.get('date') == d and is_lean(p)] if lean_sz else []
        pw = sum(1 for p in picks if p['won']); pl = sum(1 for p in picks if not p['won'])
        lw = sum(1 for p in leans if p['won']); ll = sum(1 for p in leans if not p['won'])
        pu = sum(units(p, pick_sz) for p in picks)
        lu = sum(units(p, lean_sz) for p in leans)
        out.append({'date': d, 'n_p': len(picks), 'pw': pw, 'pl': pl, 'p_u': pu,
                    'n_l': len(leans), 'lw': lw, 'll': ll, 'l_u': lu,
                    'c_u': pu + lu, 'cn': len(picks)+len(leans),
                    'cw': pw+lw, 'cl': pl+ll})
    return out


def main():
    b_bf, b_var, b_cap, b_kcap = defaults.BF_MULT, dict(defaults.VAR_MULT), defaults.BF_CAP, defaults.K_RATE_CAP_FLOOR
    try:
        print('Running A: BF=1.30 CAP=23 VAR=1.30 KCAP=0.36...')
        a_picks = run_config(1.30, 23, 1.30, 0.36)
        print('Running B: BF=1.30 CAP=26 VAR=1.30 KCAP=0.36...')
        b_picks = run_config(1.30, 26, 1.30, 0.36)
    finally:
        defaults.BF_MULT = b_bf
        defaults.VAR_MULT.clear(); defaults.VAR_MULT.update(b_var)
        defaults.BF_CAP = b_cap
        defaults.K_RATE_CAP_FLOOR = b_kcap

    a_days = daily(a_picks, 'A', pick_sz=2.0, lean_sz=1.0)
    b_days = daily(b_picks, 'B', pick_sz=2.5, lean_sz=0.0)

    print()
    print('Daily breakdown since 5/4')
    print('  A = picks+leans @ 2/1, CAP=23   |   B = picks-only @ 2.5u, CAP=26')
    print()
    print(f'{"date":>10s}  | {"A: P n/W-L":>14s} {"A: L n/W-L":>14s} {"A: comb-u":>10s}  | {"B: P n/W-L":>14s} {"B: comb-u":>10s}')
    print('-' * 100)
    ta_p_u = ta_l_u = ta_c_u = 0.0
    ta_pw = ta_pl = ta_lw = ta_ll = 0
    tb_p_u = 0.0; tb_pw = tb_pl = 0
    by_date_a = {d['date']: d for d in a_days}
    by_date_b = {d['date']: d for d in b_days}
    all_dates = sorted(set(by_date_a) | set(by_date_b))
    for d in all_dates:
        a = by_date_a.get(d, {'n_p':0,'pw':0,'pl':0,'p_u':0,'n_l':0,'lw':0,'ll':0,'l_u':0,'c_u':0})
        b = by_date_b.get(d, {'n_p':0,'pw':0,'pl':0,'p_u':0,'c_u':0})
        print(f'{d:>10s}  | {a["n_p"]:>2d}p {a["pw"]:>2d}-{a["pl"]:<2d}   {a["n_l"]:>2d}l {a["lw"]:>2d}-{a["ll"]:<2d}   {a["c_u"]:>+7.2f}u  | {b["n_p"]:>2d}p {b["pw"]:>2d}-{b["pl"]:<2d}    {b["c_u"]:>+7.2f}u')
        ta_p_u += a['p_u']; ta_l_u += a['l_u']; ta_c_u += a['c_u']
        ta_pw += a['pw']; ta_pl += a['pl']; ta_lw += a['lw']; ta_ll += a['ll']
        tb_p_u += b['p_u']; tb_pw += b['pw']; tb_pl += b['pl']
    print('-' * 100)
    a_pwr = ta_pw/(ta_pw+ta_pl)*100 if ta_pw+ta_pl else 0
    a_lwr = ta_lw/(ta_lw+ta_ll)*100 if ta_lw+ta_ll else 0
    b_pwr = tb_pw/(tb_pw+tb_pl)*100 if tb_pw+tb_pl else 0
    print(f'{"TOTAL":>10s}  | {ta_pw}-{ta_pl} ({a_pwr:.1f}%)  {ta_lw}-{ta_ll} ({a_lwr:.1f}%)  {ta_c_u:>+7.2f}u  | {tb_pw}-{tb_pl} ({b_pwr:.1f}%)         {tb_p_u:>+7.2f}u')


if __name__ == '__main__':
    main()
