"""
calibrate_threshold.py — Empirical calibration of WNBA pCover thresholds.

Reads every graded historical pick from wnba-props.json and reports:
  1. Actual win rate by pCover bucket per market (is the model calibrated?)
  2. Suggested VAR_MULT scaling factor per market to fix miscalibration

Usage:
    python -m scripts.calibrate_threshold
    python -m scripts.calibrate_threshold --target-wr 0.55
"""

import argparse
import json
import math
import os
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATHS = [
    os.path.join(SCRIPT_DIR, "..", "data", "wnba-props.json"),
    os.path.join(SCRIPT_DIR, "..", "..", "PythonDashboard", "data", "wnba-props.json"),
]


def _wilson_ci(wins, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = wins / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def _normalize_result(pick):
    r = (pick.get("result") or pick.get("actual_result") or "").upper()
    if r in ("W", "WIN", "WON"):
        return "W"
    if r in ("L", "LOSS", "LOST"):
        return "L"
    if r in ("P", "PUSH"):
        return "P"
    actual = pick.get("actual") or pick.get("actual_stat")
    line = pick.get("line")
    direction = pick.get("pick")
    if actual is not None and line is not None and direction in ("OVER", "UNDER"):
        if actual == line:
            return "P"
        if direction == "OVER":
            return "W" if actual > line else "L"
        return "W" if actual < line else "L"
    return None


def load_graded_picks(path):
    with open(path, "r") as f:
        data = json.load(f)
    picks = data.get("props", [])
    graded = []
    for p in picks:
        if p.get("pick") in (None, "PASS"):
            continue
        r = _normalize_result(p)
        if r in ("W", "L"):
            p["_result"] = r
            graded.append(p)
    return graded


def pcover_bucket_key(p):
    pc = p.get("pCover")
    if pc is None:
        return None
    edges = [0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 1.01]
    for i in range(len(edges) - 1):
        if edges[i] <= pc < edges[i + 1]:
            return f"{edges[i]:.2f}-{edges[i+1]:.2f}"
    return None


def calibration_table(picks, group_key):
    by_group = defaultdict(lambda: {"W": 0, "L": 0, "picks": []})
    for p in picks:
        key = group_key(p)
        if key is None:
            continue
        by_group[key][p["_result"]] += 1
        by_group[key]["picks"].append(p)
    rows = []
    for key in sorted(by_group.keys()):
        g = by_group[key]
        n = g["W"] + g["L"]
        wr = g["W"] / n if n else 0.0
        lo, hi = _wilson_ci(g["W"], n)
        rows.append({"key": key, "n": n, "W": g["W"], "L": g["L"],
                     "wr": wr, "ci_lo": lo, "ci_hi": hi,
                     "picks": g["picks"]})
    return rows


def print_table(title, rows, key_label="bucket"):
    print(f"\n{'='*78}")
    print(f"  {title}")
    print(f"{'='*78}")
    print(f"  {key_label:<20} {'n':>5} {'W':>4} {'L':>4} {'WR':>7} "
          f"{'95% CI':>16}  {'claim':>6}  {'diff':>6}")
    print(f"  {'-'*20} {'-'*5} {'-'*4} {'-'*4} {'-'*7} {'-'*16}  {'-'*6}  {'-'*6}")
    for r in rows:
        ci = f"[{r['ci_lo']:.2f},{r['ci_hi']:.2f}]"
        try:
            lo, hi = [float(x) for x in r["key"].split("-")]
            claimed = (lo + hi) / 2
            diff = r["wr"] - claimed
            cs = f"{claimed:.2f}"
            ds = f"{diff:+.2f}"
        except Exception:
            cs, ds = "  -- ", "  -- "
        print(f"  {str(r['key']):<20} {r['n']:>5} {r['W']:>4} {r['L']:>4} "
              f"{r['wr']:>7.3f} {ci:>16}  {cs:>6}  {ds:>6}")


def suggest_var_mult_scaling(picks, current_var_mult=2.5):
    """For each market, suggest a VAR_MULT scaling factor that would bring
    pCover claims into line with realized win rates.

    Approach: compute average claim mid-bucket vs realized WR. If claim is
    overconfident (e.g. claims 0.85, delivers 0.60), implied probability
    needs to drop. Z-scores from t-distribution at df=5:
        target_z corresponding to actual WR
        current implied z corresponding to claimed pCover
    Scale factor = current_z / target_z (always >= 1 if overconfident).
    """
    from scipy.stats import t as tdist
    df = 5

    by_market = defaultdict(list)
    for p in picks:
        m = p.get("market")
        if m:
            by_market[m].append(p)

    print(f"\n{'='*78}")
    print(f"  Per-market VAR_MULT scaling recommendation (current={current_var_mult})")
    print(f"{'='*78}")
    print(f"  {'market':<18} {'n':>4} {'WR':>6} {'claim':>6} {'gap':>6} "
          f"{'scale':>6} {'-> new VAR_MULT':>18}")
    print(f"  {'-'*18} {'-'*4} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*18}")

    for m in sorted(by_market.keys()):
        mp = by_market[m]
        w = sum(1 for p in mp if p["_result"] == "W")
        l = sum(1 for p in mp if p["_result"] == "L")
        n = w + l
        if n < 20:
            continue
        actual_wr = w / n
        claimed_wr = sum(p.get("pCover", 0) or 0 for p in mp) / n
        if actual_wr <= 0.5 or claimed_wr <= 0.5:
            scale = 1.0
        else:
            try:
                z_claim = tdist.ppf(claimed_wr, df=df)
                z_actual = tdist.ppf(actual_wr, df=df)
                scale = z_claim / z_actual if z_actual > 0 else 1.0
            except Exception:
                scale = 1.0
        new_vm = current_var_mult * scale
        gap = actual_wr - claimed_wr
        print(f"  {m:<18} {n:>4d} {actual_wr:>6.3f} {claimed_wr:>6.3f} "
              f"{gap:>+6.2f} {scale:>6.2f} {new_vm:>18.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=None)
    ap.add_argument("--var-mult", type=float, default=2.5,
                    help="Current VAR_MULT (for scaling recommendation)")
    args = ap.parse_args()

    path = args.path
    if path is None:
        for cand in DEFAULT_PATHS:
            cand = os.path.normpath(cand)
            if os.path.exists(cand):
                path = cand
                break
    if not path or not os.path.exists(path):
        print(f"  No wnba-props.json found in {DEFAULT_PATHS}")
        return

    print(f"\n  Reading picks from {path}")
    picks = load_graded_picks(path)
    if not picks:
        print("  No graded picks found.")
        return
    overall_w = sum(1 for p in picks if p["_result"] == "W")
    overall_l = sum(1 for p in picks if p["_result"] == "L")
    print(f"  {len(picks)} graded picks ({overall_w} W, {overall_l} L) "
          f"-> overall WR {overall_w/len(picks):.3f}")

    # Overall calibration
    print_table("Calibration by pCover bucket (all markets)",
                calibration_table(picks, pcover_bucket_key),
                key_label="pCover bucket")

    # Per-market calibration
    by_market = defaultdict(list)
    for p in picks:
        m = p.get("market")
        if m:
            by_market[m].append(p)
    for m in sorted(by_market.keys()):
        mp = by_market[m]
        if len(mp) < 20:
            continue
        print_table(f"{m} — n={len(mp)}",
                    calibration_table(mp, pcover_bucket_key),
                    key_label="pCover bucket")

    # VAR_MULT scaling recommendation
    suggest_var_mult_scaling(picks, current_var_mult=args.var_mult)


if __name__ == "__main__":
    main()
