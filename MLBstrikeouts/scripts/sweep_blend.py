"""
Sweep the outer Kalman blend weight (currently 0.5/0.5 in props_engine.py:341).

Reads MLBstrikeouts/data/blend_trace.json (produced by an instrumented backfill
run) and re-grades every projection across w in [0.0, 0.05, ..., 1.0]:

    proj_w = w * model_pure + (1 - w) * kalman_proj + rest_delta

Pick rules match props_engine: pCover >= 0.70 (Student-t df=5), then per-team
paired filter that keeps only the highest-pCover OVER and the highest-pCover
UNDER per (team, date). Pushes (actual == line) skipped, mirroring the live
grading path.
"""
import json
import os
from collections import defaultdict
from scipy.stats import t as t_dist

PROP_T_DF = 5
PCOVER_THRESH = 0.70

TRACE = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "data", "blend_trace.json")
)


def american_to_units(price):
    if price is None:
        return None
    price = int(price)
    if price > 0:
        return price / 100.0
    return 100.0 / abs(price)


def grade_at_weight(rows, w):
    candidates = []
    for r in rows:
        line = r.get("line")
        if line is None:
            continue
        std = r.get("std") or 0
        if std <= 0:
            continue

        proj = w * r["model_pure"] + (1 - w) * r["kalman_proj"] + r["rest_delta"]
        diff = proj - line
        z = diff / std
        p_over = float(t_dist.cdf(z, df=PROP_T_DF))
        p_under = 1.0 - p_over
        if p_over >= p_under:
            direction, p, price = "OVER", p_over, r.get("over_price")
        else:
            direction, p, price = "UNDER", p_under, r.get("under_price")

        if p < PCOVER_THRESH:
            continue

        candidates.append({
            "date": r["date"],
            "team": r["team"],
            "direction": direction,
            "pCover": p,
            "price": price,
            "actual": r["actual"],
            "line": line,
        })

    # Paired-teammate filter: per (date, team, direction), keep best pCover.
    by_key = defaultdict(list)
    for c in candidates:
        by_key[(c["date"], c["team"], c["direction"])].append(c)
    final = []
    for picks in by_key.values():
        picks.sort(key=lambda x: -x["pCover"])
        final.append(picks[0])

    wins = losses = pushes = no_price = 0
    units = 0.0
    for p in final:
        actual = p["actual"]
        line = p["line"]
        if actual == line:
            pushes += 1
            continue
        won = (p["direction"] == "OVER" and actual > line) or \
              (p["direction"] == "UNDER" and actual < line)
        if p["price"] is None:
            no_price += 1
            # treat as 1u flat with no payout info
            if won:
                wins += 1
            else:
                losses += 1
            continue
        u = american_to_units(p["price"])
        if won:
            wins += 1
            units += u
        else:
            losses += 1
            units -= 1.0

    n = wins + losses
    win_pct = wins / n if n else 0.0
    return {
        "w": w,
        "picks": n + pushes,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "win_pct": win_pct,
        "units": units,
        "no_price": no_price,
    }


def main():
    with open(TRACE) as f:
        rows = json.load(f)
    print(f"Loaded {len(rows)} trace rows")

    weights = [round(0.05 * i, 2) for i in range(0, 21)]
    results = [grade_at_weight(rows, w) for w in weights]

    print(f"\n{'w':>5}  {'picks':>5}  {'W-L':>9}  {'win%':>6}  {'units':>7}  {'noprc':>5}")
    print("-" * 50)
    for r in results:
        print(f"{r['w']:>5.2f}  {r['picks']:>5d}  "
              f"{r['wins']:>3d}-{r['losses']:<3d}  "
              f"{r['win_pct']:>6.1%}  {r['units']:>+7.2f}  {r['no_price']:>5d}")

    best_units = max(results, key=lambda r: r["units"])
    best_winpct = max(results, key=lambda r: (r["win_pct"], r["wins"]))
    print(f"\nBest by units : w={best_units['w']:.2f} "
          f"({best_units['wins']}-{best_units['losses']}, {best_units['win_pct']:.1%}, "
          f"{best_units['units']:+.2f}u)")
    print(f"Best by win%  : w={best_winpct['w']:.2f} "
          f"({best_winpct['wins']}-{best_winpct['losses']}, {best_winpct['win_pct']:.1%}, "
          f"{best_winpct['units']:+.2f}u)")


if __name__ == "__main__":
    main()
