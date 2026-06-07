#!/usr/bin/env python3
"""Quick side-by-side A/B for the K-rate regression's CONTACT-quality regressor:
A = CSW + xBA (shipped)  vs  B = CSW + xwOBACON (candidate).
All other config (BF/VAR/CAP/KCAP/W and the CSW stuff-axis) held at the shipped
values so only the SECOND regressor differs. xBA carries strikeouts in its own
AB denominator; xwOBACON (expected wOBA on contact) strips that K component and
isolates pure contact quality — a cleaner complement to CSW. Slopes refit
dynamically for whichever column is active (compute_whiff_xba_regression
x2_key).

NOTE: a leak-free walk-forward A/B needs daily savant snapshots carrying the
xwobacon column. Those began on 2026-06-07 (fetch_savant_pitcher_rates), so on
earlier dates the xwobacon variant has no as-of snapshot and degrades to
no-blend there — the B column will only diverge from A once enough xwobacon
snapshots have accumulated. Run on a networked runner after that data exists."""
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


def run_config(bf, var, cap, kcap, w, metric="whiff", metric2="xba"):
    defaults.BF_MULT = bf
    defaults.BF_CAP = float(cap)
    defaults.VAR_MULT['strikeouts'] = var
    defaults.K_RATE_CAP_FLOOR = kcap
    defaults.CSW_XBA_BLEND_WEIGHT = w
    defaults.K_QUALITY_METRIC = metric
    defaults.CONTACT_QUALITY_METRIC = metric2
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
    b_kcap=defaults.K_RATE_CAP_FLOOR; b_w=defaults.CSW_XBA_BLEND_WEIGHT
    b_metric=getattr(defaults, 'K_QUALITY_METRIC', 'whiff')
    b_metric2=getattr(defaults, 'CONTACT_QUALITY_METRIC', 'xba')

    # Shipped config, held fixed; only the contact-quality (2nd) regressor
    # differs A vs B. The CSW stuff-axis is the shipped first regressor in both.
    BF, VAR, CAP, KCAP, W = b_bf, b_var.get('strikeouts'), b_cap, b_kcap, b_w
    print(f'Running A (CSW+xBA):      BF={BF} VAR={VAR} CAP={CAP} KCAP={KCAP} W={W}...')
    A = run_config(BF, VAR, CAP, KCAP, W, metric="csw", metric2="xba")
    print(f'Running B (CSW+xwOBACON): BF={BF} VAR={VAR} CAP={CAP} KCAP={KCAP} W={W}...')
    B = run_config(BF, VAR, CAP, KCAP, W, metric="csw", metric2="xwobacon")

    defaults.BF_MULT = b_bf
    defaults.VAR_MULT.clear(); defaults.VAR_MULT.update(b_var)
    defaults.BF_CAP = b_cap
    defaults.K_RATE_CAP_FLOOR = b_kcap
    defaults.CSW_XBA_BLEND_WEIGHT = b_w
    defaults.K_QUALITY_METRIC = b_metric
    defaults.CONTACT_QUALITY_METRIC = b_metric2

    windows = [
        ('SEASON', '2026-01-01', '2026-12-31'),
        ('RECENT 5/4-5/28', '2026-05-04', '2026-05-28'),
        ('THIS WEEK 5/22-5/28', '2026-05-22', '2026-05-28'),
    ]

    for label, start, end in windows:
        a = report(A, 'A', start, end)
        b = report(B, 'B', start, end)
        d_cu = b['cu'] - a['cu']
        d_pp = (b['pn'] - a['pn'])
        print()
        print(f'=== {label} ===')
        print(f'  {"":<22s} {"A: CSW+xBA":>16s}  {"B: CSW+xwOBACON":>16s}  {"B-A":>10s}')
        print(f'  {"Picks n":<22s} {a["pn"]:>16d}  {b["pn"]:>16d}  {d_pp:>+10d}')
        print(f'  {"Picks W-L":<22s} {(str(a["pw"])+"-"+str(a["pl"])):>16s}  {(str(b["pw"])+"-"+str(b["pl"])):>16s}')
        print(f'  {"Picks WR":<22s} {a["pwr"]:>15.1f}%  {b["pwr"]:>15.1f}%  {b["pwr"]-a["pwr"]:>+9.1f}p')
        print(f'  {"Picks units @ 2.5u":<22s} {a["pu"]:>+14.2f}u  {b["pu"]:>+14.2f}u  {b["pu"]-a["pu"]:>+9.2f}u')
        print(f'  {"Picks ROI":<22s} {a["p_roi"]:>+14.2f}%  {b["p_roi"]:>+14.2f}%  {b["p_roi"]-a["p_roi"]:>+9.2f}p')
        print(f'  {"Leans n":<22s} {a["ln"]:>16d}  {b["ln"]:>16d}')
        print(f'  {"Leans W-L":<22s} {(str(a["lw"])+"-"+str(a["ll"])):>16s}  {(str(b["lw"])+"-"+str(b["ll"])):>16s}')
        print(f'  {"Leans WR":<22s} {a["lwr"]:>15.1f}%  {b["lwr"]:>15.1f}%')
        print(f'  {"Leans units @ 1.5u":<22s} {a["lu"]:>+14.2f}u  {b["lu"]:>+14.2f}u  {b["lu"]-a["lu"]:>+9.2f}u')
        print(f'  {"Combined units":<22s} {a["cu"]:>+14.2f}u  {b["cu"]:>+14.2f}u  {d_cu:>+9.2f}u')
        print(f'  {"Combined ROI":<22s} {a["c_roi"]:>+14.2f}%  {b["c_roi"]:>+14.2f}%  {b["c_roi"]-a["c_roi"]:>+9.2f}p')

    # Decision bar reminder: marginal metric swaps need >0.20u/pick to ship.
    sa = report(A, 'A', '2026-01-01', '2026-12-31')
    sb = report(B, 'B', '2026-01-01', '2026-12-31')
    n = max(sb['pn'], 1)
    print(f'\n  [bar] season B-A = {sb["cu"]-sa["cu"]:+.2f}u over {sb["pn"]} picks '
          f'= {(sb["cu"]-sa["cu"])/n:+.3f}u/pick (ship if >+0.20)')


if __name__ == '__main__':
    main()
