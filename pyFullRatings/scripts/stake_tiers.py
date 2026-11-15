#!/usr/bin/env python3
# scripts/stake_tiers.py
#
# Would staking MORE on the highest-confidence NBA full-season picks beat flat
# 1u? Backtests a tiered plan (elite pCover >= cut -> N u, else 1u) against the
# graded fired-pick history, sweeping elite cutoff x multiplier and reporting
# return AND risk (worst chronological drawdown + per-pick P&L stdev).
#
# Units convention matches the dashboard/store: win +stake, loss -1.1*stake
# (-110). Flat 1u = the +162.3u history baseline. Usage: python scripts/stake_tiers.py

import os, json, statistics

UNIT_LOSS = -1.1
HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, "..", "data", "history.json")


def load_fired():
    s = json.load(open(STORE))
    runs = sorted((r for r in s["runs"] if not r.get("burnIn")), key=lambda r: r["date"])
    picks = []
    for r in runs:
        for g in r.get("games") or []:
            if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
                continue
            if not (g.get("sPick") and g["sPick"] != "PASS"):
                continue
            if g.get("sResult") not in ("WIN", "LOSS", "PUSH"):
                continue
            picks.append((r["date"], g.get("pCover"), g["sResult"]))
    return picks


def pnl(stake, result):
    if result == "WIN":
        return stake
    if result == "LOSS":
        return stake * UNIT_LOSS
    return 0.0


def simulate(picks, elite_cut, mult):
    seq, staked, net = [], 0.0, 0.0
    for _, pc, res in picks:
        stake = mult if (pc is not None and pc >= elite_cut - 1e-9) else 1.0
        seq.append(pnl(stake, res)); staked += stake; net += seq[-1]
    curve = peak = maxdd = 0.0
    for x in seq:
        curve += x; peak = max(peak, curve); maxdd = min(maxdd, curve - peak)
    return {"net": net, "staked": staked, "roi": net / staked if staked else 0,
            "maxdd": maxdd, "sd": statistics.pstdev(seq) if len(seq) > 1 else 0}


def main():
    picks = load_fired()
    w = sum(1 for _, _, r in picks if r == "WIN")
    l = sum(1 for _, _, r in picks if r == "LOSS")
    print(f"Fired picks: {len(picks)}  ({w}-{l}, {100*w/(w+l):.1f}%)")
    for c in (0.63, 0.65, 0.68, 0.70):
        n = sum(1 for _, pc, _ in picks if pc is not None and pc >= c)
        print(f"  elite >= {c:.2f}: n={n}")
    print()

    base = simulate(picks, 99, 1.0)
    print(f"{'plan':<24} {'net u':>8} {'staked':>7} {'ROI%':>6} {'worstDD':>8} {'u/pk sd':>8}")
    print(f"{'FLAT 1u (baseline)':<24} {base['net']:>8.1f} {base['staked']:>7.0f} "
          f"{100*base['roi']:>6.1f} {base['maxdd']:>8.1f} {base['sd']:>8.2f}")
    print()
    for cut in (0.63, 0.65, 0.68, 0.70):
        for mult in (1.5, 2.0, 3.0):
            m = simulate(picks, cut, mult)
            print(f"elite>={cut:.2f} x{mult:<3}       {m['net']:>8.1f} {m['staked']:>7.0f} "
                  f"{100*m['roi']:>6.1f} {m['maxdd']:>8.1f} {m['sd']:>8.2f}   "
                  f"(net {m['net']-base['net']:+.1f}u vs flat)")
        print()

    for cut in (0.65,):
        eb = [(pc, r) for _, pc, r in picks if pc is not None and pc >= cut]
        ew = sum(1 for pc, r in eb if r == "WIN"); el = sum(1 for pc, r in eb if r == "LOSS")
        rest = [(pc, r) for _, pc, r in picks if pc is None or pc < cut]
        rw = sum(1 for pc, r in rest if r == "WIN"); rl = sum(1 for pc, r in rest if r == "LOSS")
        print(f"Split at {cut:.2f}:  elite {ew}-{el} ({100*ew/(ew+el):.1f}%)   "
              f"non-elite {rw}-{rl} ({100*rw/(rw+rl):.1f}%)")


if __name__ == "__main__":
    main()
