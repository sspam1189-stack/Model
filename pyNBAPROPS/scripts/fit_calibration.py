"""
fit_calibration.py — Fit empirical calibration curves per NBA market.

Reads graded picks from nba-props.json, groups by market, and fits an
isotonic (pool-adjacent-violators) mapping from raw pCover → actual WR
per market. Outputs Python dict literals ready to paste into defaults.py.

Usage:
    python -m scripts.fit_calibration
    python -m scripts.fit_calibration --bin-size 0.025
"""

import argparse
import json
import os
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATHS = [
    os.path.join(SCRIPT_DIR, "..", "data", "nba-props.json"),
    os.path.join(SCRIPT_DIR, "..", "..", "PythonDashboard", "data", "nba-props.json"),
]


def _normalize_result(pick):
    r = (pick.get("result") or "").upper()
    if r in ("W", "WIN", "WON"): return "W"
    if r in ("L", "LOSS", "LOST"): return "L"
    return None


def load_picks(path):
    with open(path) as f:
        data = json.load(f)
    out = []
    for p in data.get("props", []):
        if p.get("pick") in (None, "PASS"):
            continue
        r = _normalize_result(p)
        if r in ("W", "L"):
            p["_w"] = 1 if r == "W" else 0
            out.append(p)
    return out


def pool_adjacent_violators(ys, weights):
    """Isotonic (non-decreasing) regression via PAV.

    Stack-based: merge adjacent groups when left mean > right mean.
    Returns the smoothed ys (same length as input).
    """
    if not ys:
        return []
    # Stack entries: [sum_value_weighted, sum_weight, count]
    stack = []
    for v, w in zip(ys, weights):
        stack.append([v * w, w, 1])
        while len(stack) >= 2 and (stack[-2][0] / stack[-2][1]) > (stack[-1][0] / stack[-1][1]):
            top = stack.pop()
            stack[-1][0] += top[0]
            stack[-1][1] += top[1]
            stack[-1][2] += top[2]
    out = []
    for sum_v, sum_w, cnt in stack:
        avg = sum_v / sum_w
        out.extend([avg] * cnt)
    return out


def linear_fit(xs, ys, weights):
    """Weighted linear regression: actual_wr = a * raw_p + b."""
    n = len(xs)
    if n < 2:
        return None
    sw = sum(weights)
    mx = sum(x * w for x, w in zip(xs, weights)) / sw
    my = sum(y * w for y, w in zip(ys, weights)) / sw
    sxy = sum(w * (x - mx) * (y - my) for x, y, w in zip(xs, ys, weights))
    sxx = sum(w * (x - mx) ** 2 for x, w in zip(xs, weights))
    if sxx == 0:
        return (0.0, my)
    a = sxy / sxx
    b = my - a * mx
    return (a, b)


def fit_market_curve(picks, bin_size=0.05):
    """Bin picks by pCover, compute per-bin actual WR, apply PAV."""
    bins = defaultdict(lambda: {"w": 0, "n": 0, "p_sum": 0.0})
    for p in picks:
        pc = p.get("pCover")
        if pc is None:
            continue
        b = round(pc / bin_size) * bin_size
        bins[b]["w"] += p["_w"]
        bins[b]["n"] += 1
        bins[b]["p_sum"] += pc
    if not bins:
        return []
    keys = sorted(bins.keys())
    xs = [bins[k]["p_sum"] / bins[k]["n"] for k in keys]
    ys = [bins[k]["w"] / bins[k]["n"] for k in keys]
    weights = [bins[k]["n"] for k in keys]
    xs2, ys2 = pool_adjacent_violators(xs, ys, weights)
    # Pair up so xs are sorted
    paired = sorted(zip(xs2, ys2, weights, keys))
    return [(x, y, w) for x, y, w, _ in paired]


def format_dict(curves):
    lines = ["CALIBRATION_CURVES = {"]
    for market in sorted(curves.keys()):
        lines.append(f"    {market!r}: [")
        for x, y, w in curves[market]:
            lines.append(f"        ({x:.4f}, {y:.4f}),  # n={w}")
        lines.append("    ],")
    lines.append("}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=None)
    ap.add_argument("--bin-size", type=float, default=0.05,
                    help="pCover bin width (default 0.05)")
    args = ap.parse_args()

    path = args.path
    if path is None:
        for cand in DEFAULT_PATHS:
            cand = os.path.normpath(cand)
            if os.path.exists(cand):
                path = cand
                break
    if not path or not os.path.exists(path):
        print(f"  No nba-props.json found in {DEFAULT_PATHS}")
        return

    print(f"  Reading {path}")
    picks = load_picks(path)
    print(f"  {len(picks)} graded picks")

    by_market = defaultdict(list)
    for p in picks:
        m = p.get("market")
        if m:
            by_market[m].append(p)

    curves = {}
    for m, mp in by_market.items():
        if len(mp) < 30:
            print(f"  [skip] {m}: only {len(mp)} graded picks")
            continue
        curves[m] = fit_market_curve(mp, bin_size=args.bin_size)
        print(f"\n=== {m} (n={len(mp)}) — calibration curve ===")
        print(f"  {'raw_p':>6}  {'cal_p':>6}  {'n':>4}")
        for x, y, w in curves[m]:
            print(f"  {x:>6.4f}  {y:>6.4f}  {w:>4d}")

    print("\n" + format_dict(curves))


if __name__ == "__main__":
    main()
