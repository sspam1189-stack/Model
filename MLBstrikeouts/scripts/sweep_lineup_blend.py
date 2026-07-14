#!/usr/bin/env python3
"""Narrow 2D walk-forward sweep: PROJ_LINEUP_WEIGHT x CSW_XBA_BLEND_WEIGHT,
around the currently-shipped values (lineup 0.8, blend 0.2 live / 0.4 shadow).
Fine-tunes locally rather than re-litigating the full grid from the
2026-07-02 sweep (lineup {1.0,0.8,0.7,0.6} x blend {0.0,0.2,0.4,0.6}) —
see defaults.py:462-480 and docs/superpowers/specs/2026-07-06-mlb-whiff04-variant.md.

Metric fixed to whiff, calib stays ON (shipped default), all other config
(BF_MULT/BF_CAP/VAR_MULT/K_RATE_CAP_FLOOR) held at shipped values so only
the two knobs under test move.

Usage:
    cd MLBstrikeouts
    python -m scripts.sweep_lineup_blend
"""
import sys, os, glob, io, contextlib

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import numpy as np
import defaults
from props_backfill import backfill

EMP_STD_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "data", "emp_std_cache", "mlb",
))

LINEUP_WEIGHTS = [0.7, 0.8, 0.9]
BLEND_WEIGHTS = [0.1, 0.2, 0.3, 0.5, 0.6]

WINDOWS = [
    ('SEASON', '2026-01-01', '2026-12-31'),
    ('LAST 3W', '2026-06-23', '2026-07-14'),
    ('SINCE W04 SHADOW (7/6-)', '2026-07-06', '2026-07-14'),
]


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


def units(p, sz=2.5):
    o = p.get('odds'); r = grade(p)
    if o is None or r not in ('WIN', 'LOSS'): return 0.0, 0.0
    if o > 0:
        return (sz * (o / 100.0) if r == 'WIN' else -sz), sz
    return (sz if r == 'WIN' else sz * (-abs(o) / 100.0)), sz * (abs(o) / 100.0)


def run_config(lineup_w, blend_w):
    defaults.PROJ_LINEUP_WEIGHT = lineup_w
    defaults.CSW_XBA_BLEND_WEIGHT = blend_w
    defaults.K_QUALITY_METRIC = "whiff"
    _wipe_emp_std()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = backfill()
    return (r or {}).get('strikeouts', {}).get('picks', []) or []


def report(pp, start, end):
    rows = [p for p in pp if p.get('actual') is not None and start <= p.get('date', '') <= end]
    picks = [p for p in rows if is_pick(p)]
    w = sum(1 for p in picks if grade(p) == 'WIN')
    l = sum(1 for p in picks if grade(p) == 'LOSS')
    n = w + l
    pu = pr = 0.0
    for p in picks:
        u, r = units(p)
        pu += u; pr += r
    roi = pu / pr * 100 if pr else 0
    wr = w / n * 100 if n else 0
    mae_rows = [p for p in rows if p.get('proj') is not None]
    mae = float(np.mean(np.abs(np.array([p['proj'] for p in mae_rows]) -
                                np.array([p['actual'] for p in mae_rows])))) if mae_rows else 0.0
    return {'n': n, 'w': w, 'l': l, 'wr': wr, 'u': pu, 'roi': roi, 'mae': mae}


def main():
    b_lineup = defaults.PROJ_LINEUP_WEIGHT
    b_blend = defaults.CSW_XBA_BLEND_WEIGHT
    b_metric = getattr(defaults, 'K_QUALITY_METRIC', 'whiff')

    runs = {}
    for lw in LINEUP_WEIGHTS:
        for bw in BLEND_WEIGHTS:
            print(f"Running lineup={lw} blend={bw} ...")
            runs[(lw, bw)] = run_config(lw, bw)

    defaults.PROJ_LINEUP_WEIGHT = b_lineup
    defaults.CSW_XBA_BLEND_WEIGHT = b_blend
    defaults.K_QUALITY_METRIC = b_metric

    for label, start, end in WINDOWS:
        print(f"\n=== {label} ===")
        print(f"  {'lineup':>7s} {'blend':>7s} {'n':>4s} {'W-L':>8s} {'WR':>7s} {'ROI':>8s} {'units':>9s} {'MAE':>7s}")
        for lw in LINEUP_WEIGHTS:
            for bw in BLEND_WEIGHTS:
                s = report(runs[(lw, bw)], start, end)
                tag = " *" if (lw, bw) == (b_lineup, b_blend) else ""
                print(f"  {lw:>7.2f} {bw:>7.2f} {s['n']:>4d} "
                      f"{str(s['w'])+'-'+str(s['l']):>8s} {s['wr']:>6.1f}% "
                      f"{s['roi']:>+7.2f}% {s['u']:>+8.2f}u {s['mae']:>6.3f}{tag}")

    print(f"\n  (* = currently shipped: lineup {b_lineup}, blend {b_blend})")


if __name__ == '__main__':
    main()
