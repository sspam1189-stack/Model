#!/usr/bin/env python3
"""Sweep the blend weight for CSW+xBA and compare each to the whiff@0.5
benchmark. Earlier A/B was unfair: it ran CSW at W=0.5, which is whiff's tuned
optimum (sweep_whiff_floor). CSW's signal-to-noise differs, so its best W may
sit elsewhere. W=0.0 is the shared no-blend baseline (metric-independent).
All other config held at shipped values (BF=1.0/VAR=1.2/CAP=24/KCAP=0.36)."""
import defaults
from compare_configs_AB import run_config, report

SHIPPED = dict(bf=defaults.BF_MULT, var=defaults.VAR_MULT.get('strikeouts'),
               cap=defaults.BF_CAP, kcap=defaults.K_RATE_CAP_FLOOR)
WINDOWS = [
    ('SEASON', '2026-01-01', '2026-12-31'),
    ('RECENT 5/4-5/28', '2026-05-04', '2026-05-28'),
]
CSW_WEIGHTS = [0.3, 0.4, 0.5, 0.6, 0.8]


def main():
    b_w = defaults.CSW_XBA_BLEND_WEIGHT
    b_metric = getattr(defaults, 'K_QUALITY_METRIC', 'whiff')

    runs = []  # (label, picks_list)
    print(f"Benchmark whiff W=0.5 ...")
    runs.append(('whiff W=0.5', run_config(SHIPPED['bf'], SHIPPED['var'],
                 SHIPPED['cap'], SHIPPED['kcap'], 0.5, metric='whiff')))
    for w in CSW_WEIGHTS:
        print(f"CSW W={w} ...")
        runs.append((f'CSW  W={w}', run_config(SHIPPED['bf'], SHIPPED['var'],
                     SHIPPED['cap'], SHIPPED['kcap'], w, metric='csw')))

    # restore
    defaults.CSW_XBA_BLEND_WEIGHT = b_w
    defaults.K_QUALITY_METRIC = b_metric

    # benchmark season metrics for the u/pick bar
    bench = report(runs[0][1], 'bench', '2026-01-01', '2026-12-31')

    for wlabel, start, end in WINDOWS:
        print(f"\n=== {wlabel} ===")
        print(f"  {'config':<14s} {'picks':>6s} {'WR':>7s} "
              f"{'pick u':>9s} {'pick ROI':>9s} {'comb u':>9s} {'comb ROI':>9s}")
        for label, pp in runs:
            r = report(pp, label, start, end)
            print(f"  {label:<14s} {r['pn']:>6d} {r['pwr']:>6.1f}% "
                  f"{r['pu']:>+8.2f}u {r['p_roi']:>+7.2f}% "
                  f"{r['cu']:>+8.2f}u {r['c_roi']:>+7.2f}%")

    # Decision bar: each CSW weight's season combined-u edge over whiff@0.5,
    # expressed per pick. Ship only if best CSW clears +0.20 u/pick.
    print(f"\n  [bar] season vs whiff@0.5 (ship if >+0.20 u/pick):")
    for label, pp in runs[1:]:
        r = report(pp, label, '2026-01-01', '2026-12-31')
        d = r['cu'] - bench['cu']
        n = max(r['pn'], 1)
        print(f"    {label:<14s} comb {d:>+7.2f}u  picks {r['pu']-bench['pu']:>+7.2f}u  "
              f"= {d/n:>+.3f} u/pick")


if __name__ == '__main__':
    main()
