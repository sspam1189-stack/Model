#!/usr/bin/env python3
"""A/B: high-tier calibration exemption for high-K arms.

Tests PROJ_CALIB_ELITE_K_EXEMPT = 0.28 (skip the a + b*proj high-tier
calibration map for pitchers whose season starter K% >= 28%) against the
shipped baseline (calibration applies to everyone above the knot), for BOTH
whiff-blend models:

    A1 = whiff 0.2, exempt OFF (live model)
    A2 = whiff 0.2, exempt 0.28
    B1 = whiff 0.4, exempt OFF (w04 variant)
    B2 = whiff 0.4, exempt 0.28

All other config held at shipped values. Wipes the shared emp_std cache
between runs (all four runs share data/emp_std_cache/mlb because defaults
are mutated in-process, not via MLB_K_VARIANT). Restore tracked data files
with `git checkout -- data/emp_std_cache MLBstrikeouts/data` afterwards.
"""
import sys, os, glob, io, json, contextlib

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import defaults
from props_backfill import backfill

EMP_STD_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "data", "emp_std_cache", "mlb",
))
CHECKPOINT_DIR = os.environ.get("CALIB_AB_CKPT_DIR", "")


def _wipe_emp_std():
    for p in glob.glob(os.path.join(EMP_STD_DIR, "emp_std_*.json")):
        try: os.remove(p)
        except OSError: pass


def is_pick(p): return p.get('pick') in ('OVER', 'UNDER')


def grade(p):
    if 'won' in p:
        if p['won'] is True: return 'WIN'
        if p['won'] is False: return 'LOSS'
        return None
    if p.get('actual') is None or p.get('line') is None: return None
    wbp = p.get('would_be_pick') or p.get('pick')
    if wbp == 'OVER': return 'WIN' if p['actual'] > p['line'] else 'LOSS'
    return 'WIN' if p['actual'] < p['line'] else 'LOSS'


def units(p, sz):
    o = p.get('odds'); r = grade(p)
    if o is None or r not in ('WIN', 'LOSS'): return 0.0, 0.0
    if o > 0:
        return (sz * (o / 100.0) if r == 'WIN' else -sz), sz
    return (sz if r == 'WIN' else sz * (-abs(o) / 100.0)), sz * (abs(o) / 100.0)


def run_config(w, exempt, tag):
    defaults.CSW_XBA_BLEND_WEIGHT = w
    defaults.PROJ_CALIB_ELITE_K_EXEMPT = exempt
    _wipe_emp_std()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = backfill()
    picks = (r or {}).get('strikeouts', {}).get('picks', []) or []
    if CHECKPOINT_DIR:
        try:
            os.makedirs(CHECKPOINT_DIR, exist_ok=True)
            with open(os.path.join(CHECKPOINT_DIR, f"picks_{tag}.json"), "w") as f:
                json.dump(picks, f, default=str)
        except Exception as e:
            print(f"  [ckpt] write failed for {tag}: {e}")
    return picks


def report(pp, start, end):
    rows = [p for p in pp if p.get('actual') is not None
            and start <= p.get('date', '') <= end]
    picks = [p for p in rows if is_pick(p)]
    pw = sum(1 for p in picks if grade(p) == 'WIN')
    pl = sum(1 for p in picks if grade(p) == 'LOSS')
    pu = 0.0; pr = 0.0
    for p in picks:
        u, r = units(p, 2.5); pu += u; pr += r
    errs = [abs((p.get('proj') or 0) - p['actual']) for p in picks
            if p.get('proj') is not None]
    return {
        'pn': len(picks), 'pw': pw, 'pl': pl,
        'pwr': pw / (pw + pl) * 100 if pw + pl else 0,
        'pu': pu, 'p_roi': pu / pr * 100 if pr else 0,
        'mae': sum(errs) / len(errs) if errs else 0,
    }


def diff_picks(base, exp):
    """Which graded picks changed between runs (added / dropped / flipped)."""
    def key(p): return (p.get('date', ''), p.get('player', ''))
    b = {key(p): p for p in base if is_pick(p) and p.get('actual') is not None}
    e = {key(p): p for p in exp if is_pick(p) and p.get('actual') is not None}
    added = [e[k] for k in e.keys() - b.keys()]
    dropped = [b[k] for k in b.keys() - e.keys()]
    flipped = [(b[k], e[k]) for k in b.keys() & e.keys()
               if b[k].get('pick') != e[k].get('pick')]
    return added, dropped, flipped


def _slice_units(rows):
    w = sum(1 for p in rows if grade(p) == 'WIN')
    l = sum(1 for p in rows if grade(p) == 'LOSS')
    u = sum(units(p, 2.5)[0] for p in rows)
    return w, l, u


def main():
    b_w = defaults.CSW_XBA_BLEND_WEIGHT
    b_ex = defaults.PROJ_CALIB_ELITE_K_EXEMPT

    runs = [
        ('w02_base',   0.2, 0.0,  'whiff 0.2, calib for all (LIVE)'),
        ('w02_exempt', 0.2, 0.28, 'whiff 0.2, calib OFF for K%>=28'),
        ('w04_base',   0.4, 0.0,  'whiff 0.4, calib for all (w04 variant)'),
        ('w04_exempt', 0.4, 0.28, 'whiff 0.4, calib OFF for K%>=28'),
    ]
    results = {}
    for tag, w, ex, desc in runs:
        print(f'Running {tag}: {desc} ...', flush=True)
        results[tag] = run_config(w, ex, tag)
        print(f'  -> {len(results[tag])} pick rows', flush=True)

    defaults.CSW_XBA_BLEND_WEIGHT = b_w
    defaults.PROJ_CALIB_ELITE_K_EXEMPT = b_ex

    windows = [
        ('SEASON', '2026-01-01', '2026-12-31'),
        ('LAST 4 WEEKS 6/10-7/7', '2026-06-10', '2026-07-07'),
        ('LAST 2 WEEKS 6/24-7/7', '2026-06-24', '2026-07-07'),
    ]

    for model, base_tag, ex_tag in [('WHIFF 0.2', 'w02_base', 'w02_exempt'),
                                    ('WHIFF 0.4', 'w04_base', 'w04_exempt')]:
        print(f'\n================ {model} ================')
        for label, start, end in windows:
            a = report(results[base_tag], start, end)
            b = report(results[ex_tag], start, end)
            print(f'\n=== {label} ===')
            print(f'  {"":<20s} {"calib ALL":>14s}  {"exempt 28+":>14s}  {"diff":>10s}')
            print(f'  {"Picks n":<20s} {a["pn"]:>14d}  {b["pn"]:>14d}  {b["pn"]-a["pn"]:>+10d}')
            print(f'  {"Picks W-L":<20s} {(str(a["pw"])+"-"+str(a["pl"])):>14s}  {(str(b["pw"])+"-"+str(b["pl"])):>14s}')
            print(f'  {"Picks WR":<20s} {a["pwr"]:>13.1f}%  {b["pwr"]:>13.1f}%  {b["pwr"]-a["pwr"]:>+9.1f}p')
            print(f'  {"Units @ 2.5u":<20s} {a["pu"]:>+12.2f}u  {b["pu"]:>+12.2f}u  {b["pu"]-a["pu"]:>+9.2f}u')
            print(f'  {"ROI":<20s} {a["p_roi"]:>+12.2f}%  {b["p_roi"]:>+12.2f}%  {b["p_roi"]-a["p_roi"]:>+9.2f}p')
            print(f'  {"Pick MAE":<20s} {a["mae"]:>14.4f}  {b["mae"]:>14.4f}  {b["mae"]-a["mae"]:>+10.4f}')

        added, dropped, flipped = diff_picks(results[base_tag], results[ex_tag])
        aw, al, au = _slice_units(added)
        dw, dl, du = _slice_units(dropped)
        print(f'\n  --- pick-set diff (exempt vs base, season) ---')
        print(f'  added by exemption:   {len(added):>3d}  ({aw}-{al}, {au:+.2f}u)')
        print(f'  dropped by exemption: {len(dropped):>3d}  ({dw}-{dl}, {du:+.2f}u)')
        print(f'  direction flipped:    {len(flipped):>3d}')
        for pb, pe in flipped[:10]:
            print(f'    {pb.get("date")} {pb.get("player")}: {pb.get("pick")} -> {pe.get("pick")}'
                  f' (line {pb.get("line")}, actual {pb.get("actual")})')

        sa = report(results[base_tag], '2026-01-01', '2026-12-31')
        sb = report(results[ex_tag], '2026-01-01', '2026-12-31')
        n = max(sb['pn'], 1)
        print(f'  [bar] season exempt-base = {sb["pu"]-sa["pu"]:+.2f}u over {sb["pn"]} picks '
              f'= {(sb["pu"]-sa["pu"])/n:+.3f}u/pick (ship if >+0.20)')


if __name__ == '__main__':
    main()
