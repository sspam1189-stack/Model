#!/usr/bin/env python3
# scripts/stake_tiers.py
#
# Prototype: does staking MORE on the highest-confidence WNBA spread picks beat
# flat 1u staking? The >=0.72 pCover band hit ~75% this season (vs ~65% overall),
# so a stake-up "elite" tier should lift ROI — at the cost of concentrating money
# in a thin, high-variance bucket. This backtests the tradeoff on the graded
# season (authoritative live store), sweeping the elite cutoff x multiplier, and
# reports return AND risk (per-pick P&L stdev + worst chronological drawdown).
#
# In-sample on one partial season (~71 picks, ~20-28 elite) — a sizing sketch,
# not a proven staking system. Usage:  python scripts/stake_tiers.py

import os, json, math, statistics

JUICE = 1.10
HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, "..", "data", "wnba-full.json")


def load_picks():
    s = json.load(open(STORE))
    runs = sorted((r for r in s["runs"] if not r.get("burnIn")), key=lambda r: r["date"])
    picks = []
    for r in runs:
        for g in r.get("games") or []:
            if g.get("pCover") is not None and g.get("sResult") in ("WIN", "LOSS", "PUSH"):
                picks.append((r["date"], g["pCover"], g["sResult"]))
    return picks


def pnl(stake, result):
    if result == "WIN":
        return stake / JUICE
    if result == "LOSS":
        return -stake
    return 0.0


def simulate(picks, elite_cut, mult):
    """Return metrics for a plan: base 1u, elite (pCover>=cut) staked mult u."""
    seq, staked, net = [], 0.0, 0.0
    for _, pc, res in picks:
        stake = mult if pc >= elite_cut - 1e-9 else 1.0
        p = pnl(stake, res)
        staked += stake
        net += p
        seq.append(p)
    # worst peak-to-trough drawdown over the chronological unit curve
    curve, peak, maxdd = 0.0, 0.0, 0.0
    for p in seq:
        curve += p
        peak = max(peak, curve)
        maxdd = min(maxdd, curve - peak)
    roi = net / staked if staked else 0.0
    sd = statistics.pstdev(seq) if len(seq) > 1 else 0.0
    return {"net": net, "staked": staked, "roi": roi, "maxdd": maxdd,
            "sd": sd, "n": len(seq)}


def main():
    picks = load_picks()
    n_elite = {c: sum(1 for _, pc, _ in picks if pc >= c) for c in (0.70, 0.72, 0.74)}
    w = sum(1 for _, _, r in picks if r == "WIN")
    l = sum(1 for _, _, r in picks if r == "LOSS")
    print(f"Season graded picks: {len(picks)}  ({w}-{l}, {100*w/(w+l):.1f}%)")
    print(f"Elite-band sizes: >=0.70 n={n_elite[0.70]}, >=0.72 n={n_elite[0.72]}, >=0.74 n={n_elite[0.74]}\n")

    base = simulate(picks, 99, 1.0)  # flat 1u baseline
    print(f"{'plan':<26} {'net u':>7} {'staked':>7} {'ROI%':>6} {'worstDD':>8} {'u/pk sd':>8}")
    print(f"{'FLAT 1u (baseline)':<26} {base['net']:>7.2f} {base['staked']:>7.0f} "
          f"{100*base['roi']:>6.1f} {base['maxdd']:>8.2f} {base['sd']:>8.2f}")
    print()
    for cut in (0.70, 0.72, 0.74):
        for mult in (1.5, 2.0, 3.0):
            m = simulate(picks, cut, mult)
            d_net = m["net"] - base["net"]
            print(f"elite>={cut:.2f} x{mult:<3}          {m['net']:>7.2f} {m['staked']:>7.0f} "
                  f"{100*m['roi']:>6.1f} {m['maxdd']:>8.2f} {m['sd']:>8.2f}   "
                  f"(net {d_net:+.2f}u vs flat)")
        print()

    # Elite band standalone record at the recommended cutoff
    for cut in (0.72,):
        eb = [(pc, res) for _, pc, res in picks if pc >= cut]
        ew = sum(1 for pc, r in eb if r == "WIN"); el = sum(1 for pc, r in eb if r == "LOSS")
        rest = [(pc, res) for _, pc, res in picks if pc < cut]
        rw = sum(1 for pc, r in rest if r == "WIN"); rl = sum(1 for pc, r in rest if r == "LOSS")
        print(f"Split at {cut:.2f}:  elite {ew}-{el} ({100*ew/(ew+el):.1f}%)   "
              f"non-elite {rw}-{rl} ({100*rw/(rw+rl):.1f}%)")


if __name__ == "__main__":
    main()
