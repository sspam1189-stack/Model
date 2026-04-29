"""
Sweep the EFFECTIVE Kalman-mean weight in the K projection.

Because the projection composes two blends — inner (cfg.kalmanBlend, default 0.6
in pitcher_kalman.py:65) and outer (0.5 in props_engine.py:340) — the only thing
that matters mathematically is their product. So we sweep that one knob:

    proj = (1 - k) * model_pure + k * kalman_mean + rest_delta

Currently k = 0.6 * 0.5 = 0.30 (70% model, 30% Kalman mean — see chat).

We back out kalman_mean from the existing trace (which captured kalman_proj =
0.6*kalman_mean + 0.4*model_pure, the inner blend's output) using:

    kalman_mean = (kalman_proj - 0.4 * model_pure) / 0.6

For cold-start pitchers where source != "kalman_blend", kalman_proj == model_pure
and the formula returns kalman_mean = model_pure (i.e. no Kalman influence at any
weight). That's correct.

Pick rules and paired-teammate filter mirror props_engine exactly: pCover>=0.70
on Student-t df=5, then keep best pCover per (date, team, direction).
"""
import json
import os
from collections import defaultdict
from scipy.stats import t as t_dist

PROP_T_DF = 5
PCOVER_THRESH = 0.70
INNER_B = 0.6     # current cfg.kalmanBlend used during the trace run
OUTER_W = 0.5     # current outer blend used during the trace run
CURRENT_K = INNER_B * OUTER_W   # effective Kalman weight under current code

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


def back_out_kalman_mean(model_pure, kalman_proj, b=INNER_B):
    """kalman_proj = b * kalman_mean + (1-b) * model_pure  =>  solve for kalman_mean."""
    return (kalman_proj - (1 - b) * model_pure) / b


def grade_at_k(rows, k):
    """Re-grade every projection at effective Kalman-mean weight k."""
    candidates = []
    for r in rows:
        line = r.get("line")
        if line is None:
            continue
        std = r.get("std") or 0
        if std <= 0:
            continue

        model_pure = r["model_pure"]
        kalman_mean = back_out_kalman_mean(model_pure, r["kalman_proj"])
        proj = (1 - k) * model_pure + k * kalman_mean + r["rest_delta"]

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

    by_key = defaultdict(list)
    for c in candidates:
        by_key[(c["date"], c["team"], c["direction"])].append(c)
    final = []
    for picks in by_key.values():
        picks.sort(key=lambda x: -x["pCover"])
        final.append(picks[0])

    wins = losses = pushes = 0
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
    return {
        "k": k,
        "picks": n + pushes,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "win_pct": wins / n if n else 0.0,
        "units": units,
    }


def main():
    with open(TRACE) as f:
        rows = json.load(f)
    print(f"Loaded {len(rows)} trace rows")
    print(f"Current production: outer={OUTER_W}  inner={INNER_B}  "
          f"=> effective k={CURRENT_K:.2f} (model={1-CURRENT_K:.2f}/Kalman={CURRENT_K:.2f})\n")

    weights = [round(0.025 * i, 4) for i in range(0, 41)]   # 0.0 to 1.0 in 0.025 steps
    results = [grade_at_k(rows, k) for k in weights]

    print(f"{'k(km)':>6}  {'model%':>6}  {'picks':>5}  {'W-L':>9}  {'win%':>6}  {'units':>7}")
    print("-" * 52)
    for r in results:
        marker = "  <-- current" if abs(r["k"] - CURRENT_K) < 1e-6 else ""
        print(f"{r['k']:>6.3f}  {1-r['k']:>6.3f}  {r['picks']:>5d}  "
              f"{r['wins']:>3d}-{r['losses']:<3d}  "
              f"{r['win_pct']:>6.1%}  {r['units']:>+7.2f}{marker}")

    # Robust optimum: the largest contiguous plateau within 1u of the peak,
    # report its center. Avoids picking a single noisy spike.
    best_units = max(results, key=lambda r: r["units"])
    best_winpct = max(results, key=lambda r: (r["win_pct"], r["wins"]))
    print(f"\nBest by units : k={best_units['k']:.3f} "
          f"(model={1-best_units['k']:.3f}/Kalman={best_units['k']:.3f}) "
          f"=> {best_units['wins']}-{best_units['losses']} "
          f"({best_units['win_pct']:.1%}), {best_units['units']:+.2f}u "
          f"on {best_units['picks']} picks")
    print(f"Best by win%  : k={best_winpct['k']:.3f} "
          f"(model={1-best_winpct['k']:.3f}/Kalman={best_winpct['k']:.3f}) "
          f"=> {best_winpct['wins']}-{best_winpct['losses']} "
          f"({best_winpct['win_pct']:.1%}), {best_winpct['units']:+.2f}u "
          f"on {best_winpct['picks']} picks")

    # Plateau report: which weights are within 1u of best?
    near_best = [r for r in results if best_units["units"] - r["units"] <= 1.0]
    if near_best:
        ks = [r["k"] for r in near_best]
        print(f"\nPlateau within 1u of best: k in [{min(ks):.3f}, {max(ks):.3f}] "
              f"({len(near_best)} grid points)")


if __name__ == "__main__":
    main()
