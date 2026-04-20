"""
calibrate_threshold.py — Empirical calibration of pCover thresholds for MLB props.

Reads every graded historical pick from mlb-props.json and reports:
  1. Actual win rate by pCover bucket (is the model well-calibrated?)
  2. Actual win rate by absolute edge bucket
  3. Actual win rate by market
  4. Recommended minimum pCover threshold for each market at a target WR

This is how you set MARKET_THRESHOLDS in defaults.py — not by guessing.

Usage:
    python -m scripts.calibrate_threshold
    python -m scripts.calibrate_threshold --target-wr 0.55
    python -m scripts.calibrate_threshold --market strikeouts
"""

import argparse
import json
import math
import os
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROPS_PATH = os.path.join(SCRIPT_DIR, "..", "data", "mlb-props.json")


def _wilson_ci(wins, n, z=1.96):
    """Wilson score 95% confidence interval for a binomial proportion."""
    if n == 0:
        return (0.0, 0.0)
    p = wins / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def _breakeven_wr(odds):
    """Breakeven win rate at given American odds."""
    if odds is None:
        return 0.524  # standard -110 default
    if odds < 0:
        return -odds / (-odds + 100)
    return 100 / (odds + 100)


def _normalize_result(pick):
    """Return 'W', 'L', or None (pending/pushed)."""
    r = (pick.get("result") or pick.get("actual_result") or "").upper()
    if r in ("W", "WIN", "WON"):
        return "W"
    if r in ("L", "LOSS", "LOST"):
        return "L"
    if r in ("P", "PUSH"):
        return "P"
    # Fallback: graded flag + actual value
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


def _buckets_by_pcover():
    edges = [0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 1.01]
    labels = []
    for i in range(len(edges) - 1):
        labels.append(f"{edges[i]:.2f}-{edges[i+1]:.2f}")
    return edges, labels


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


def pcover_bucket_key(p):
    pc = p.get("pCover")
    if pc is None:
        return None
    edges, labels = _buckets_by_pcover()
    for i in range(len(edges) - 1):
        if edges[i] <= pc < edges[i + 1]:
            return labels[i]
    return None


def edge_bucket_key(p):
    e = p.get("edge")
    if e is None:
        return None
    abs_e = abs(e)
    if abs_e < 0.5:
        return "0.0-0.5"
    if abs_e < 1.0:
        return "0.5-1.0"
    if abs_e < 1.5:
        return "1.0-1.5"
    if abs_e < 2.0:
        return "1.5-2.0"
    if abs_e < 3.0:
        return "2.0-3.0"
    return "3.0+"


def market_key(p):
    return p.get("market")


def recommend_threshold(picks, market, target_wr=0.55, min_n=15):
    """Find lowest pCover threshold where actual WR >= target_wr (with min sample)."""
    market_picks = [p for p in picks if p.get("market") == market]
    if not market_picks:
        return None, 0, 0.0
    # Sort by pCover ascending, sweep threshold
    market_picks.sort(key=lambda p: p.get("pCover", 0))
    n_total = len(market_picks)
    best = None
    # Try each pCover as potential threshold
    for i, p in enumerate(market_picks):
        thresh = p.get("pCover")
        if thresh is None:
            continue
        subset = [q for q in market_picks if (q.get("pCover") or 0) >= thresh]
        n = len(subset)
        if n < min_n:
            continue
        w = sum(1 for q in subset if q["_result"] == "W")
        wr = w / n
        if wr >= target_wr:
            best = (thresh, n, wr)
            break  # first threshold that satisfies
    return best if best else (None, n_total, 0.0)


def print_table(title, rows, key_label="bucket"):
    print(f"\n{'='*72}")
    print(f"  {title}")
    print(f"{'='*72}")
    print(f"  {key_label:<20} {'n':>5} {'W':>4} {'L':>4} {'WR':>7} "
          f"{'95% CI':>16}")
    print(f"  {'-'*20} {'-'*5} {'-'*4} {'-'*4} {'-'*7} {'-'*16}")
    for r in rows:
        ci = f"[{r['ci_lo']:.2f},{r['ci_hi']:.2f}]"
        print(f"  {str(r['key']):<20} {r['n']:>5} {r['W']:>4} {r['L']:>4} "
              f"{r['wr']:>7.3f} {ci:>16}")


def _avg_odds(picks, direction_field="pick"):
    prices = []
    for p in picks:
        d = p.get(direction_field)
        price = p.get("over_price") if d == "OVER" else p.get("under_price")
        if price is not None:
            prices.append(price)
    return sum(prices) / len(prices) if prices else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=PROPS_PATH)
    ap.add_argument("--target-wr", type=float, default=None,
                    help="Target win rate for threshold recommendation. "
                         "Defaults to breakeven WR at average pick odds.")
    ap.add_argument("--market", default=None,
                    help="Filter to a single market (strikeouts, total_bases, etc.)")
    args = ap.parse_args()

    path = os.path.normpath(args.path)
    print(f"\n  Reading picks from {path}")
    picks = load_graded_picks(path)
    if args.market:
        picks = [p for p in picks if p.get("market") == args.market]
    print(f"  {len(picks)} graded picks "
          f"({sum(1 for p in picks if p['_result']=='W')} W, "
          f"{sum(1 for p in picks if p['_result']=='L')} L) "
          f"-> overall WR {sum(1 for p in picks if p['_result']=='W')/max(1,len(picks)):.3f}")

    if not picks:
        print("  No graded picks found — run and grade some first.")
        return

    # Tables
    print_table("Calibration by pCover bucket (model claim vs actual)",
                calibration_table(picks, pcover_bucket_key),
                key_label="pCover bucket")

    print_table("Calibration by absolute edge bucket",
                calibration_table(picks, edge_bucket_key),
                key_label="|edge| bucket")

    print_table("Performance by market",
                calibration_table(picks, market_key),
                key_label="market")

    # Threshold recommendation
    print(f"\n{'='*72}")
    print(f"  Recommended minimum pCover thresholds")
    print(f"{'='*72}")
    markets = sorted({p.get("market") for p in picks if p.get("market")})
    for m in markets:
        m_picks = [p for p in picks if p.get("market") == m]
        avg_odds = _avg_odds(m_picks)
        target = args.target_wr or _breakeven_wr(avg_odds)
        thresh, n, wr = recommend_threshold(picks, m, target_wr=target)
        odds_str = f"{avg_odds:+.0f}" if avg_odds else "n/a"
        if thresh is None:
            print(f"  {m:<14} target WR {target:.3f} (avg odds {odds_str}): "
                  f"NO threshold hits target with min sample")
        else:
            print(f"  {m:<14} target WR {target:.3f} (avg odds {odds_str}): "
                  f"use pCover >= {thresh:.3f} "
                  f"(n={n}, actual WR={wr:.3f})")

    # Miscalibration flag: if any pCover bucket's actual WR is far below claimed
    print(f"\n{'='*72}")
    print(f"  Miscalibration check (actual WR vs mid-bucket claim)")
    print(f"{'='*72}")
    for r in calibration_table(picks, pcover_bucket_key):
        # Parse mid-bucket
        try:
            lo, hi = [float(x) for x in r["key"].split("-")]
            claimed = (lo + hi) / 2
        except Exception:
            continue
        gap = r["wr"] - claimed
        flag = ""
        if r["n"] >= 10:
            if gap < -0.05:
                flag = f"  <- OVERCONFIDENT by {abs(gap):.2f}"
            elif gap > 0.05:
                flag = f"  <- under-confident by {gap:.2f}"
        print(f"  {r['key']:<14} n={r['n']:>3}  claimed~{claimed:.3f}  "
              f"actual={r['wr']:.3f}  diff={gap:+.3f}{flag}")


if __name__ == "__main__":
    main()
