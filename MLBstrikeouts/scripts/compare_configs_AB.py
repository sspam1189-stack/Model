#!/usr/bin/env python3
"""Quick side-by-side: A=W0.4/V1.40/C25 vs B=W0.8/V1.30/C25."""
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
        try: os.remove(p)
        except OSError: pass


def is_pick(p): return p.get('pick') in ('OVER','UNDER')
def is_lean(p):
    if p.get('pick') != 'PASS': return False
    pc = p.get('pCover') or 0
    return 0.65 <= pc < 0.72


def grade(p):
    # In-memory backfill picks use the 'won' boolean
    if 'won' in p:
        if p['won'] is True: return 'WIN'
        if p['won'] is False: return 'LOSS'
        return None
    # Fallback to actual/line comparison (for leans which may lack 'won')
    if p.get('actual') is None or p.get('line') is None: return None
    wbp = p.get('would_be_pick') or p.get('pick') or ('OVER' if (p.get('proj') or 0)>(p.get('line') or 0) else 'UNDER')
    if wbp == 'OVER': return 'WIN' if p['actual'] > p['line'] else 'LOSS'
    return 'WIN' if p['actual'] < p['line'] else 'LOSS'


def units(p, sz):
    o = p.get('odds'); r = grade(p)
    if o is None or r not in ('WIN','LOSS'): return 0.0, 0.0
    if o > 0:
        return (sz*(o/100.0) if r=='WIN' else -sz), sz
    return (sz if r=='WIN' else sz*(-abs(o)/100.0)), sz*(abs(o)/100.0)


def run_config(bf, var, cap, kcap, w):
    defaults.BF_MULT = bf
    defaults.BF_CAP = float(cap)
    defaults.VAR_MULT['strikeouts'] = var
    defaults.K_RATE_CAP_FLOOR = kcap
    defaults.WHIFF_XBA_BLEND_WEIGHT = w
    _wipe_emp_std()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = backfill()
    return (r or {}).get('strikeouts',{}).get('picks',[]) or []


def report(pp, label, start, end):
    rows = [p for p in pp if p.get('actual') is not None and start <= p.get('date','') <= end]
    picks = [p for p in rows if is_pick(p)]
    leans = [p for p in rows if is_lean(p)]
    pw=sum(1 for p in picks if grade(p)=='WIN'); pl=sum(1 for p in picks if grade(p)=='LOSS')
    lw=sum(1 for p in leans if grade(p)=='WIN'); ll=sum(1 for p in leans if grade(p)=='LOSS')
    pu=0.0; pr=0.0
    for p in picks: u, r = units(p, 2.5); pu+=u; pr+=r
    lu=0.0; lr=0.0
    for p in leans: u, r = units(p, 1.5); lu+=u; lr+=r
    p_roi = pu/pr*100 if pr else 0
    l_roi = lu/lr*100 if lr else 0
    c_roi = (pu+lu)/(pr+lr)*100 if pr+lr else 0
    return {
        'pn':len(picks),'pw':pw,'pl':pl,'pwr':pw/(pw+pl)*100 if pw+pl else 0,'pu':pu,'p_roi':p_roi,
        'ln':len(leans),'lw':lw,'ll':ll,'lwr':lw/(lw+ll)*100 if lw+ll else 0,'lu':lu,'l_roi':l_roi,
        'cu':pu+lu,'c_roi':c_roi,
    }


def main():
    b_bf=defaults.BF_MULT; b_var=dict(defaults.VAR_MULT); b_cap=defaults.BF_CAP
    b_kcap=defaults.K_RATE_CAP_FLOOR; b_w=defaults.WHIFF_XBA_BLEND_WEIGHT

    print('Running X: BF=1.00 VAR=1.10 CAP=24 KCAP=0.36 W=0.6...')
    A = run_config(1.00, 1.10, 24, 0.36, 0.6)
    print('Running B: BF=1.00 VAR=1.30 CAP=25 KCAP=0.36 W=0.8...')
    B = run_config(1.00, 1.30, 25, 0.36, 0.8)
    print('Running C: BF=1.00 VAR=1.15 CAP=23 KCAP=0.36 W=0.0...')
    C = run_config(1.00, 1.15, 23, 0.36, 0.0)

    defaults.BF_MULT = b_bf
    defaults.VAR_MULT.clear(); defaults.VAR_MULT.update(b_var)
    defaults.BF_CAP = b_cap
    defaults.K_RATE_CAP_FLOOR = b_kcap
    defaults.WHIFF_XBA_BLEND_WEIGHT = b_w

    windows = [
        ('RECENT 5/4-5/20', '2026-05-04', '2026-05-20'),
        ('THIS WEEK 5/18-5/20', '2026-05-18', '2026-05-20'),
    ]

    for label, start, end in windows:
        a = report(A, 'A', start, end)
        b = report(B, 'B', start, end)
        c = report(C, 'C', start, end)
        print()
        print(f'=== {label} ===')
        print(f'  {"":<22s} {"X: W=.6 V=1.10 C=24":>20s}  {"B: W=.8 V=1.30 C=25":>20s}  {"C: W=0 V=1.15 C=23":>20s}')
        print(f'  {"Picks n":<22s} {a["pn"]:>20d}  {b["pn"]:>20d}  {c["pn"]:>20d}')
        print(f'  {"Picks W-L":<22s} {(str(a["pw"])+"-"+str(a["pl"])):>20s}  {(str(b["pw"])+"-"+str(b["pl"])):>20s}  {(str(c["pw"])+"-"+str(c["pl"])):>20s}')
        print(f'  {"Picks WR":<22s} {a["pwr"]:>19.1f}%  {b["pwr"]:>19.1f}%  {c["pwr"]:>19.1f}%')
        print(f'  {"Picks units @ 2.5u":<22s} {a["pu"]:>+18.2f}u  {b["pu"]:>+18.2f}u  {c["pu"]:>+18.2f}u')
        print(f'  {"Picks ROI":<22s} {a["p_roi"]:>+18.2f}%  {b["p_roi"]:>+18.2f}%  {c["p_roi"]:>+18.2f}%')
        print(f'  {"Leans n":<22s} {a["ln"]:>20d}  {b["ln"]:>20d}  {c["ln"]:>20d}')
        print(f'  {"Leans W-L":<22s} {(str(a["lw"])+"-"+str(a["ll"])):>20s}  {(str(b["lw"])+"-"+str(b["ll"])):>20s}  {(str(c["lw"])+"-"+str(c["ll"])):>20s}')
        print(f'  {"Leans WR":<22s} {a["lwr"]:>19.1f}%  {b["lwr"]:>19.1f}%  {c["lwr"]:>19.1f}%')
        print(f'  {"Leans units @ 1.5u":<22s} {a["lu"]:>+18.2f}u  {b["lu"]:>+18.2f}u  {c["lu"]:>+18.2f}u')
        print(f'  {"Leans ROI":<22s} {a["l_roi"]:>+18.2f}%  {b["l_roi"]:>+18.2f}%  {c["l_roi"]:>+18.2f}%')
        print(f'  {"Combined units":<22s} {a["cu"]:>+18.2f}u  {b["cu"]:>+18.2f}u  {c["cu"]:>+18.2f}u')
        print(f'  {"Combined ROI":<22s} {a["c_roi"]:>+18.2f}%  {b["c_roi"]:>+18.2f}%  {c["c_roi"]:>+18.2f}%')


if __name__ == '__main__':
    main()
