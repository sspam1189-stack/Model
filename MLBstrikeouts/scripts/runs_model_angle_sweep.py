# MLBstrikeouts/scripts/runs_model_angle_sweep.py
"""Angle sweep over runs-model prediction dumps, with a temporal holdout.

Reads per-game prediction dumps written by ``runs_model_backtest.py --dump``
and grid-searches betting angles (ML side/edge/price filters, totals
side/gap/line-height filters) — but scores every angle separately on an
in-sample half (May–June) and an out-of-sample half (July–August). An angle
only counts as a CANDIDATE when it is green in BOTH halves with enough bets;
everything else is assumed to be noise. Survivors get a robustness check
across the other model-variant dumps and a rough z-score against the
flat-betting vig drag.

This is a mining harness: with ~200 cells tested, a handful of both-halves
green cells are EXPECTED by chance. Nothing here is a betting signal until
it survives forward tracking (fade-watch style).

Usage:
  python3 runs_model_angle_sweep.py --base pred_base.json \
      [--variants pred_nodecay.json pred_hl25.json ...] [--split 2026-07-01]

Flat 1u staking throughout (repo convention for analysis scripts).
"""

import argparse
import json
import math
from collections import namedtuple

Rec = namedtuple("Rec", "bets wins profit")


def profit_1u(ml, won):
    if not won:
        return -1.0
    return ml / 100.0 if ml > 0 else 100.0 / (-ml)


def implied(ml):
    return (-ml) / (-ml + 100.0) if ml < 0 else 100.0 / (ml + 100.0)


def devig_home(g):
    ih, ia = implied(g["home_ml"]), implied(g["away_ml"])
    return ih / (ih + ia)


# ---------------------------------------------------------------------------
# Angle evaluation
# ---------------------------------------------------------------------------

def eval_ml(rows, edge, price_filter, venue_filter):
    """price_filter: all|dog|fav ; venue_filter: all|home|away (of the bet side)."""
    bets = wins = 0
    profit = 0.0
    for g in rows:
        if not g.get("home_ml") or not g.get("away_ml") or g.get("home_win") not in (0, 1):
            continue
        mh = devig_home(g)
        side = None
        if g["p_home"] - mh >= edge:
            side, ml, won = "home", g["home_ml"], g["home_win"] == 1
        elif mh - g["p_home"] >= edge:
            side, ml, won = "away", g["away_ml"], g["home_win"] == 0
        if side is None:
            continue
        if venue_filter != "all" and side != venue_filter:
            continue
        if price_filter == "dog" and ml < 0:
            continue
        if price_filter == "fav" and ml > 0:
            continue
        bets += 1
        wins += 1 if won else 0
        profit += profit_1u(ml, won)
    return Rec(bets, wins, profit)


def eval_tot(rows, gap, direction, line_bucket):
    """direction: over|under ; line_bucket: all|low(<=7.5)|mid(8-9)|high(>=9.5)."""
    bets = wins = 0
    profit = 0.0
    for g in rows:
        line = g.get("total_line")
        if line is None:
            continue
        if line_bucket == "low" and line > 7.5:
            continue
        if line_bucket == "mid" and not (8.0 <= line <= 9.0):
            continue
        if line_bucket == "high" and line < 9.5:
            continue
        mt = g["mu_home"] + g["mu_away"]
        actual = g["home_score"] + g["away_score"]
        if direction == "over":
            if mt - line < gap or not g.get("over_ml"):
                continue
            ml, won = g["over_ml"], actual > line
        else:
            if line - mt < gap or not g.get("under_ml"):
                continue
            ml, won = g["under_ml"], actual < line
        if actual == line:
            continue  # push
        bets += 1
        wins += 1 if won else 0
        profit += profit_1u(ml, won)
    return Rec(bets, wins, profit)


def zscore(rec):
    """Rough z of profit vs 0, sd ~1u/bet for near-even prices."""
    if rec.bets == 0:
        return 0.0
    return rec.profit / math.sqrt(rec.bets)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--variants", nargs="*", default=[])
    ap.add_argument("--split", default="2026-07-01")
    ap.add_argument("--min-bets", type=int, default=30, help="min bets per half")
    ap.add_argument("--min-roi", type=float, default=3.0, help="min ROI%% per half")
    args = ap.parse_args()

    base = json.load(open(args.base))
    is_rows = [g for g in base if g["date"] < args.split]
    oos_rows = [g for g in base if g["date"] >= args.split]
    variants = {p: json.load(open(p)) for p in args.variants}
    print(f"base dump: {len(base)} games  in-sample: {len(is_rows)}  holdout: {len(oos_rows)}")

    angles = []
    for edge in (0.02, 0.04, 0.06, 0.08):
        for pf in ("all", "dog", "fav"):
            for vf in ("all", "home", "away"):
                angles.append((f"ML edge>={edge:.0%} price={pf} side={vf}",
                               lambda r, e=edge, p=pf, v=vf: eval_ml(r, e, p, v)))
    for gap in (0.5, 0.75, 1.0, 1.5):
        for d in ("over", "under"):
            for lb in ("all", "low", "mid", "high"):
                angles.append((f"TOT {d} gap>={gap} line={lb}",
                               lambda r, g=gap, dd=d, l=lb: eval_tot(r, g, dd, l)))
    print(f"angles tested: {len(angles)}  (expect a few both-halves-green by chance)\n")

    survivors = []
    for name, fn in angles:
        a, b = fn(is_rows), fn(oos_rows)
        if a.bets < args.min_bets or b.bets < args.min_bets:
            continue
        roi_a, roi_b = a.profit / a.bets * 100, b.profit / b.bets * 100
        if roi_a >= args.min_roi and roi_b >= args.min_roi:
            comb = Rec(a.bets + b.bets, a.wins + b.wins, a.profit + b.profit)
            survivors.append((name, a, roi_a, b, roi_b, comb))

    if not survivors:
        print("NO angle was green in both halves at the thresholds "
              f"(>= {args.min_bets} bets and >= {args.min_roi:+.0f}% ROI per half).")
        return

    survivors.sort(key=lambda s: -min(s[2], s[4]))
    print(f"{len(survivors)} candidate angle(s) green in BOTH halves:\n")
    for name, a, roi_a, b, roi_b, comb in survivors:
        z = zscore(comb)
        print(f"  {name}")
        print(f"    in-sample : {a.bets:4d} bets  {a.wins}-{a.bets-a.wins}  {roi_a:+6.1f}%")
        print(f"    holdout   : {b.bets:4d} bets  {b.wins}-{b.bets-b.wins}  {roi_b:+6.1f}%")
        print(f"    combined  : {comb.bets:4d} bets  {comb.profit:+7.2f}u  "
              f"{comb.profit/comb.bets*100:+.1f}%  z~{z:.2f}")
        # robustness across model variants (same angle, full period)
        for path, rows in variants.items():
            fn2 = dict(angles)[name]
            v = fn2(rows)
            if v.bets:
                tag = path.rsplit("/", 1)[-1]
                print(f"    {tag:<22s}: {v.bets:4d} bets  {v.profit/v.bets*100:+6.1f}%")
        print()


if __name__ == "__main__":
    main()
