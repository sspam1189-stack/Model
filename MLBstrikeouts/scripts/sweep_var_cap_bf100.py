#!/usr/bin/env python3
"""
VAR_MULT x BF_CAP sweep at BF_MULT=1.00, KCAP=0.36.
Tests whether BF=1.00 (no inflation) plus a sensible VAR+CAP captures
the OVER/UNDER asymmetry better than BF=1.30 + CAP=23.
Clean emp_std per combo.
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
    for p in glob.glob(os.path.join(EMP_STD_DIR, "emp_std_*.json")):
        try: os.remove(p)
        except OSError: pass


def us(p, sz):
    o = p.get('odds'); won = p.get('won')
    if o is None or won is None: return 0.0, 0.0
    if o > 0:
        return (sz*(o/100.0) if won else -sz), sz
    return (sz if won else sz*(-abs(o)/100.0)), sz*(abs(o)/100.0)


def is_pick(p): return p.get('pick') in ('OVER','UNDER')
def is_lean(p):
    if p.get('pick') != 'PASS': return False
    pc = p.get('pCover') or 0
    return 0.65 <= pc < 0.72


def stats(pp, cutoff=None, direction=None):
    plays = [p for p in pp if is_pick(p)]
    if direction:
        plays = [p for p in plays if p['pick'] == direction]
    if cutoff:
        plays = [p for p in plays if p.get('date','') >= cutoff]
    if not plays:
        return {'n':0,'w':0,'l':0,'wr':0,'u':0,'risk':0,'roi':0}
    w = sum(1 for p in plays if p['won']); l = len(plays) - w
    pu=0.0; pr=0.0
    for p in plays:
        u, r = us(p, 2.5); pu+=u; pr+=r
    return {'n':len(plays),'w':w,'l':l,'wr':w/len(plays)*100,
            'u':pu,'risk':pr,'roi':pu/pr*100 if pr else 0}


def lean_stats(pp, cutoff=None):
    plays = [p for p in pp if is_lean(p)]
    if cutoff:
        plays = [p for p in plays if p.get('date','') >= cutoff]
    if not plays:
        return {'n':0,'w':0,'l':0,'wr':0,'u':0}
    w = sum(1 for p in plays if p['won']); l = len(plays) - w
    pu=0.0; pr=0.0
    for p in plays:
        u, r = us(p, 1.5); pu+=u; pr+=r
    return {'n':len(plays),'w':w,'l':l,'wr':w/len(plays)*100,'u':pu,'risk':pr,'roi':pu/pr*100 if pr else 0}


def main():
    var_grid = [1.10, 1.20, 1.30, 1.40, 1.50]
    cap_grid = [22, 23, 24, 25, 26, 28]
    bf_fixed = 1.00
    kcap_fixed = 0.36
    total = len(var_grid) * len(cap_grid)

    b_bf=defaults.BF_MULT; b_var=dict(defaults.VAR_MULT); b_cap=defaults.BF_CAP; b_kcap=defaults.K_RATE_CAP_FLOOR
    defaults.BF_MULT = bf_fixed
    defaults.K_RATE_CAP_FLOOR = kcap_fixed
    if os.path.exists(KALMAN): shutil.copy2(KALMAN, KALMAN+'.bak')

    results = {}
    try:
        i=0
        for v in var_grid:
            for cap in cap_grid:
                i+=1
                defaults.VAR_MULT['strikeouts'] = v
                defaults.BF_CAP = float(cap)
                _wipe_emp_std()
                buf=io.StringIO()
                with contextlib.redirect_stdout(buf):
                    r=backfill()
                results[(v,cap)] = (r or {}).get('strikeouts',{}).get('picks',[]) or []
                np_=sum(1 for p in results[(v,cap)] if is_pick(p))
                nl=sum(1 for p in results[(v,cap)] if is_lean(p))
                print(f'  [{i:2d}/{total}] VAR={v} CAP={cap}  picks={np_} leans={nl}')
                sys.stdout.flush()
    finally:
        defaults.BF_MULT=b_bf
        defaults.VAR_MULT.clear(); defaults.VAR_MULT.update(b_var)
        defaults.BF_CAP=b_cap
        defaults.K_RATE_CAP_FLOOR=b_kcap
        if os.path.exists(KALMAN+'.bak'): shutil.move(KALMAN+'.bak',KALMAN)

    rows=[]
    for (v,cap), pp in results.items():
        s = stats(pp); sr = stats(pp, cutoff='2026-05-04')
        so = stats(pp, direction='OVER'); su = stats(pp, direction='UNDER')
        sro = stats(pp, cutoff='2026-05-04', direction='OVER')
        sru = stats(pp, cutoff='2026-05-04', direction='UNDER')
        ls = lean_stats(pp); lsr = lean_stats(pp, cutoff='2026-05-04')
        rows.append((v,cap,s,sr,so,su,sro,sru,ls,lsr))

    print()
    print(f'BF={bf_fixed} KCAP={kcap_fixed} fixed | sized 2.5u picks / 1.5u leans')
    print()
    print('Season picks-only ROI:')
    print('  VAR\\CAP  ' + '  '.join(f'{c:>10d}' for c in cap_grid))
    for v in var_grid:
        cells = []
        for cap in cap_grid:
            s = [r for r in rows if r[0]==v and r[1]==cap][0][2]
            cells.append(f'{s["n"]:>3d} {s["roi"]:>+5.1f}%')
        print(f'  {v:>5.2f}    ' + '  '.join(f'{c:>10}' for c in cells))

    print()
    print('Recent picks-only ROI:')
    print('  VAR\\CAP  ' + '  '.join(f'{c:>10d}' for c in cap_grid))
    for v in var_grid:
        cells = []
        for cap in cap_grid:
            s = [r for r in rows if r[0]==v and r[1]==cap][0][3]
            cells.append(f'{s["n"]:>3d} {s["roi"]:>+5.1f}%')
        print(f'  {v:>5.2f}    ' + '  '.join(f'{c:>10}' for c in cells))

    print()
    print('TOP 10 by RECENT picks-only ROI:')
    rows.sort(key=lambda r: -r[3]['roi'])
    for v,cap,s,sr,so,su,sro,sru,ls,lsr in rows[:10]:
        print(f'  VAR={v} CAP={cap}  '
              f'recent {sr["n"]}p {sr["wr"]:.1f}% {sr["u"]:+.1f}u {sr["roi"]:+.2f}% '
              f'(O {sro["n"]} {sro["wr"]:.1f}% / U {sru["n"]} {sru["wr"]:.1f}%)  '
              f'season {s["roi"]:+.2f}%')

    print()
    print('TOP 10 by SEASON picks-only ROI:')
    rows.sort(key=lambda r: -r[2]['roi'])
    for v,cap,s,sr,so,su,sro,sru,ls,lsr in rows[:10]:
        print(f'  VAR={v} CAP={cap}  '
              f'season {s["n"]}p {s["wr"]:.1f}% {s["u"]:+.1f}u {s["roi"]:+.2f}% '
              f'(O {so["n"]} {so["wr"]:.1f}% / U {su["n"]} {su["wr"]:.1f}%)  '
              f'recent {sr["roi"]:+.2f}%')

    print()
    print('TOP 10 by RECENT combined (picks+leans) ROI:')
    def combined_roi(s, ls):
        return (s['u']+ls['u'])/(s['risk']+ls['risk'])*100 if s['risk']+ls['risk'] else 0
    rows.sort(key=lambda r: -combined_roi(r[3], r[9]))
    for v,cap,s,sr,so,su,sro,sru,ls,lsr in rows[:10]:
        croi = combined_roi(sr, lsr)
        cu = sr['u']+lsr['u']
        print(f'  VAR={v} CAP={cap}  recent combined {cu:+.1f}u {croi:+.2f}%  '
              f'(p {sr["n"]} {sr["wr"]:.1f}% / l {lsr["n"]} {lsr["wr"]:.1f}%)  '
              f'season picks ROI {s["roi"]:+.2f}%')


if __name__ == '__main__':
    main()
