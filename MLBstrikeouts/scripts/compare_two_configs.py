#!/usr/bin/env python3
"""Compare two configs with multiple sizing scenarios."""
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
    """Risk-to-win-1u scaled to sz. + odds: risk 1, win odds/100. - odds: risk |o|/100, win 1."""
    o = p.get('odds'); r = grade(p)
    if o is None or r not in ('WIN', 'LOSS'): return 0.0, 0.0
    if o > 0:
        win_u = sz * (o / 100.0); risk_u = sz
    else:
        win_u = sz; risk_u = sz * (abs(o) / 100.0)
    return (win_u if r == 'WIN' else -risk_u), risk_u


def tier_stats(plays, sz):
    if not plays:
        return {'n': 0, 'w': 0, 'l': 0, 'wr': 0, 'u': 0, 'risk': 0, 'roi': 0}
    w = sum(1 for p in plays if p['won'])
    l = sum(1 for p in plays if not p['won'])
    u_sum = 0.0; r_sum = 0.0
    for p in plays:
        u_p, r_p = units(p, sz)
        u_sum += u_p; r_sum += r_p
    return {'n': len(plays), 'w': w, 'l': l, 'wr': w / (w + l) * 100 if w + l else 0,
            'u': u_sum, 'risk': r_sum, 'roi': u_sum / r_sum * 100 if r_sum else 0}


def run_config(label, bf, cap, var, kcap):
    print(f'  Running {label}: BF={bf} CAP={cap} VAR={var} KCAP={kcap}')
    defaults.BF_MULT = bf
    defaults.BF_CAP = float(cap)
    defaults.VAR_MULT['strikeouts'] = var
    defaults.K_RATE_CAP_FLOOR = kcap
    _wipe_emp_std()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = backfill()
    return (r or {}).get('strikeouts', {}).get('picks', []) or []


def report(label, picks_pp, picks_lp, scenario_name, pick_sz, lean_sz):
    """Report stats for a config and sizing scenario.
    picks_pp = all PICK plays. picks_lp = all LEAN plays.
    """
    print(f'\n  {label} | {scenario_name} (picks {pick_sz}u, leans {lean_sz}u)')
    print(f'    {"window":<10s}  {"tier":<6s}  {"n":>4s} {"W-L":>9s} {"WR":>6s} {"units":>9s} {"risked":>9s} {"ROI":>7s}')
    for window_name, cutoff in [('SEASON', None), ('RECENT', '2026-05-04')]:
        pp = picks_pp if not cutoff else [p for p in picks_pp if p.get('date', '') >= cutoff]
        lp = picks_lp if not cutoff else [p for p in picks_lp if p.get('date', '') >= cutoff]
        ps = tier_stats(pp, pick_sz)
        ls = tier_stats(lp, lean_sz)
        # combined
        cu = ps['u'] + ls['u']; crisk = ps['risk'] + ls['risk']
        croi = cu / crisk * 100 if crisk else 0
        cn = ps['n'] + ls['n']; cw = ps['w'] + ls['w']; cl = ps['l'] + ls['l']
        cwr = cw / (cw + cl) * 100 if cw + cl else 0
        print(f'    {window_name:<10s}  {"PICKS":<6s}  {ps["n"]:>4d} {ps["w"]:>3d}-{ps["l"]:<3d}    {ps["wr"]:>5.1f}% {ps["u"]:>+8.2f}u {ps["risk"]:>8.2f}u {ps["roi"]:>+6.2f}%')
        print(f'    {"":<10s}  {"LEANS":<6s}  {ls["n"]:>4d} {ls["w"]:>3d}-{ls["l"]:<3d}    {ls["wr"]:>5.1f}% {ls["u"]:>+8.2f}u {ls["risk"]:>8.2f}u {ls["roi"]:>+6.2f}%')
        print(f'    {"":<10s}  {"COMB":<6s}  {cn:>4d} {cw:>3d}-{cl:<3d}    {cwr:>5.1f}% {cu:>+8.2f}u {crisk:>8.2f}u {croi:>+6.2f}%')


def main():
    b_bf, b_var, b_cap, b_kcap = defaults.BF_MULT, dict(defaults.VAR_MULT), defaults.BF_CAP, defaults.K_RATE_CAP_FLOOR

    print('=== Running two configs ===\n')
    configs = [
        ('A: BF=1.30 CAP=23', 1.30, 23, 1.30, 0.36),
        ('B: BF=1.30 CAP=26', 1.30, 26, 1.30, 0.36),
    ]
    results = {}
    try:
        for label, bf, cap, v, kc in configs:
            results[label] = run_config(label, bf, cap, v, kc)
    finally:
        defaults.BF_MULT = b_bf
        defaults.VAR_MULT.clear(); defaults.VAR_MULT.update(b_var)
        defaults.BF_CAP = b_cap
        defaults.K_RATE_CAP_FLOOR = b_kcap

    for label, _, _, _, _ in configs:
        pp = results[label]
        picks = [p for p in pp if is_pick(p)]
        leans = [p for p in pp if is_lean(p)]
        print('\n' + '=' * 100)
        print(f'  CONFIG {label}')
        print('=' * 100)
        # Scenario 1: 2/1 combined sizing
        report(label, picks, leans, 'Combined-bet sizing', pick_sz=2.0, lean_sz=1.0)
        # Scenario 2: 3u picks only (no lean betting)
        report(label, picks, [], 'Picks-only sizing', pick_sz=3.0, lean_sz=0.0)


if __name__ == '__main__':
    main()
