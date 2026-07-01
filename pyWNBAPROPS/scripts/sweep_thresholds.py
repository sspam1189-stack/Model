"""
Sweep pCover thresholds per market.

Reads graded picks from wnba-props.json and reports W/L/units/ROI for
each market across thresholds 0.50 → 0.85 in 0.025 steps.

Run AFTER backfilling with MARKET_THRESHOLDS lowered to 0.50 so the
file contains the full pCover distribution per pick (not just picks
that crossed the previous threshold).
"""
import json, os, sys
from collections import defaultdict
sys.stdout.reconfigure(encoding="utf-8")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROPS_PATH = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "wnba-props.json"))


def units_for(pick):
    odds = pick.get("odds") or -110
    r = pick.get("result")
    if r == "WIN":
        return (odds / 100.0) if odds > 0 else 1.0
    elif r == "LOSS":
        return -1.0 if odds > 0 else -(abs(odds) / 100.0)
    return 0.0


def main():
    with open(PROPS_PATH) as f:
        data = json.load(f)
    all_picks = [p for p in data.get("props", [])
                 if p.get("pick") in ("OVER", "UNDER")
                 and p.get("result") in ("WIN", "LOSS")
                 and p.get("pCover") is not None]

    by_market = defaultdict(list)
    for p in all_picks:
        by_market[p.get("market", "?")].append(p)

    print(f"Total graded picks: {len(all_picks)}")
    print(f"By market: {dict((m, len(v)) for m, v in by_market.items())}\n")

    thresholds = [0.50 + 0.025 * i for i in range(15)]  # 0.500 → 0.850

    for market in sorted(by_market.keys()):
        picks = by_market[market]
        print(f"\n=== {market}  (n={len(picks)}) ===")
        print(f"{'thresh':>8s} {'n':>5s} {'W':>4s} {'L':>4s} {'WR':>7s} "
              f"{'units':>9s} {'ROI':>7s} {'u/pick':>8s}")
        print("-" * 60)
        for t in thresholds:
            kept = [p for p in picks if p["pCover"] >= t]
            n = len(kept)
            if n == 0:
                continue
            w = sum(1 for p in kept if p["result"] == "WIN")
            l = sum(1 for p in kept if p["result"] == "LOSS")
            u = sum(units_for(p) for p in kept)
            wr = w / n if n else 0
            roi = u / n if n else 0
            per = u / n if n else 0
            mark = ""
            # Mark sweet spot: max units while WR > breakeven (52% at -110)
            print(f"{t:>8.3f} {n:>5d} {w:>4d} {l:>4d} {wr:>6.1%} "
                  f"{u:>+8.2f}u {roi:>+6.1%} {per:>+7.3f}u")

    # Combined optimum search per market: pick threshold that maximizes total units
    print("\n\n=== Per-market threshold suggestions ===")
    print(f"{'market':>10s} {'best_t':>7s} {'n':>4s} {'WR':>7s} "
          f"{'units':>9s} {'ROI':>7s}  {'reason':<25s}")
    print("-" * 80)
    for market in sorted(by_market.keys()):
        picks = by_market[market]
        best = None
        for t in thresholds:
            kept = [p for p in picks if p["pCover"] >= t]
            if len(kept) < 5:  # need minimum sample
                continue
            n = len(kept)
            w = sum(1 for p in kept if p["result"] == "WIN")
            u = sum(units_for(p) for p in kept)
            score = u  # maximize total profit
            if best is None or score > best[0]:
                best = (score, t, n, w / n, u, u / n)
        if best:
            score, t, n, wr, u, per = best
            roi = per
            print(f"{market:>10s} {t:>7.3f} {n:>4d} {wr:>6.1%} "
                  f"{u:>+8.2f}u {roi:>+6.1%}  max total units (n>=5)")


if __name__ == "__main__":
    main()
