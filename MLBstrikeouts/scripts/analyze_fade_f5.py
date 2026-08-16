# MLBstrikeouts/scripts/analyze_fade_f5.py
#
# Feasibility analysis: bet the fade-list opponent on the FIRST 5 INNINGS
# market instead of the full game.
#
# The true test is a linescore backfill (grade every historical fade bet on
# runs through 5). That needs statsapi game data, so it lives in
# fade_f5_backfill.py. This script answers the prior question — "should we
# expect F5 to be better?" — from data already on disk:
#
#   - MLBstrikeouts/data/mlb-fade-ml.json  : 646 graded full-game fade bets
#   - data/odds_cache/mlb_ml/*.json        : both-side closing ML + total line
#   - MLBstrikeouts/data/mlb-props.json    : actual_outs for each fade starter
#
# Method
#   1. De-vig each game's closing two-way ML -> market P(home wins).
#   2. Solve a Poisson run model (lam_home + lam_away = total line) that
#      reproduces that win probability, ties broken in extras.
#   3. Calibrate a single run-differential edge `delta` so the model's mean
#      full-game win probability matches the fade list's realized 74.1%.
#      This is "how many runs per game the closing line was wrong by".
#   4. Re-run the same games over innings 1-5 only, scaling run means by the
#      share of runs scored in the first five innings, and re-attributing
#      `delta` under different assumptions about where the edge lives.
#   5. Price the F5 market off the no-edge model plus a bookmaker hold and
#      compute expected ROI for the 3-way ML and the +0.5 run line.
#
# Usage: python -m scripts.analyze_fade_f5 [--json out.json]

import argparse
import json
import math
import os
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(ROOT)
FADE_ML = os.path.join(ROOT, "data", "mlb-fade-ml.json")
PROPS = os.path.join(ROOT, "data", "mlb-props.json")
ML_CACHE = os.path.join(REPO, "data", "odds_cache", "mlb_ml")

# Share of a game's runs scored in innings 1-5. League-wide the first five
# innings carry a bit more than their 5/9 share (first inning is the highest
# scoring, and the home team often does not bat in the 9th).
F5_RUN_SHARE = 0.565

MAX_RUNS = 26  # Poisson tail truncation

# Staking. "house" is the fade-ML ledger's own convention (negative odds
# risk-to-win-1u, positive odds risk 1u) and is the default so the full-game
# baseline reconciles against mlb-fade-ml.json. "flat" is the 1u convention
# CLAUDE.md prescribes for sweep scripts; F5 prices sit near even money, where
# the two conventions diverge, so both are reported.
STAKE_MODE = "house"


# ---------------------------------------------------------------- odds utils

def implied(odds):
    """American odds -> implied probability (with vig)."""
    return 100.0 / (odds + 100.0) if odds > 0 else -odds / (-odds + 100.0)


def to_american(p):
    """Fair probability -> American odds."""
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -round(100.0 * p / (1 - p)) if p >= 0.5 else round(100.0 * (1 - p) / p)


def devig_two_way(a, b):
    """Proportional de-vig of a two-way market -> (p_a, p_b)."""
    ia, ib = implied(a), implied(b)
    return ia / (ia + ib), ib / (ia + ib)


def stake_for(odds):
    """Risk for one bet under the active staking mode."""
    if STAKE_MODE == "flat":
        return 1.0
    return abs(odds) / 100.0 if odds < 0 else 1.0


def win_profit(odds):
    """Profit on a won bet under the active staking mode."""
    if STAKE_MODE == "flat":
        return 100.0 / abs(odds) if odds < 0 else odds / 100.0
    return 1.0 if odds < 0 else odds / 100.0


# ------------------------------------------------------------- poisson model

def pois_pmf(lam, n=MAX_RUNS):
    out, p = [], math.exp(-lam)
    for k in range(n):
        out.append(p)
        p *= lam / (k + 1)
    return out


def outcome_probs(lam_a, lam_b):
    """P(a > b), P(a == b), P(a < b) for independent Poisson run counts."""
    pa, pb = pois_pmf(lam_a), pois_pmf(lam_b)
    cb = []
    run = 0.0
    for x in pb:
        cb.append(run)  # P(b < k)
        run += x
    win = tie = 0.0
    for k, x in enumerate(pa):
        win += x * cb[k]
        tie += x * pb[k]
    return win, tie, max(0.0, 1.0 - win - tie)


def win_prob_regulation_plus_extras(lam_a, lam_b):
    """P(a wins the game): regulation win + half of the ties (extra innings)."""
    w, t, _ = outcome_probs(lam_a, lam_b)
    return w + 0.5 * t


def solve_lambdas(total, p_home):
    """Split `total` into (lam_home, lam_away) reproducing P(home wins)."""
    lo, hi = 0.05 * total, 0.95 * total
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if win_prob_regulation_plus_extras(mid, total - mid) < p_home:
            lo = mid
        else:
            hi = mid
    lam_h = (lo + hi) / 2.0
    return lam_h, total - lam_h


def shift(lam_bet, lam_opp, delta):
    """Move `delta` runs of expected margin to the bet side, total held fixed."""
    return max(0.05, lam_bet + delta / 2.0), max(0.05, lam_opp - delta / 2.0)


# ------------------------------------------------------------------ data load

def load_games():
    fade = json.load(open(FADE_ML))
    cache = {}
    for fn in os.listdir(ML_CACHE):
        if not fn.startswith("mlb_ml_"):
            continue
        for r in json.load(open(os.path.join(ML_CACHE, fn))):
            cache[(r.get("date"), r.get("home"), r.get("away"))] = r

    rows = []
    for b in fade["bets"]:
        if b.get("result") not in ("WIN", "LOSS"):
            continue
        r = cache.get((b["date"], b["home"], b["away"]))
        if not r or r.get("home_ml") is None or r.get("away_ml") is None:
            continue
        if not r.get("total_line"):
            continue
        p_home, p_away = devig_two_way(r["home_ml"], r["away_ml"])
        bet_is_home = b["selection"] == b["home"]
        rows.append({
            "date": b["date"],
            "source": b.get("source"),
            "pitchers": b.get("pitchers") or [],
            "bet": b["selection"],
            "opp": b["fadeTeam"],
            "bet_is_home": bet_is_home,
            "odds": b["odds"],
            "result": b["result"],
            "profit": b.get("profit", 0.0),
            "stake": b.get("stake", 0.0),
            "total": float(r["total_line"]),
            "p_bet_mkt": p_home if bet_is_home else p_away,
        })
    return fade, rows


def attach_lambdas(rows):
    for g in rows:
        p_home = g["p_bet_mkt"] if g["bet_is_home"] else 1.0 - g["p_bet_mkt"]
        lam_h, lam_a = solve_lambdas(g["total"], p_home)
        g["lam_bet"] = lam_h if g["bet_is_home"] else lam_a
        g["lam_opp"] = lam_a if g["bet_is_home"] else lam_h


def calibrate_delta(rows, target_wr):
    """Runs of expected margin the closing line missed on the fade opponent."""
    def mean_wp(d):
        s = 0.0
        for g in rows:
            lb, lo = shift(g["lam_bet"], g["lam_opp"], d)
            s += win_prob_regulation_plus_extras(lb, lo)
        return s / len(rows)

    lo, hi = 0.0, 4.0
    for _ in range(50):
        mid = (lo + hi) / 2.0
        if mean_wp(mid) < target_wr:
            lo = mid
        else:
            hi = mid
    d = (lo + hi) / 2.0
    return d, mean_wp(d)


# ------------------------------------------------------------------ scenarios

def project_f5(rows, delta, attribution, run_share, hold_3way, hold_rl):
    """Expected F5 record and ROI for the same slate of fade bets.

    `attribution` = share of the full-game edge that lives in innings 1-5.
    1.0 means the entire market error is the starting pitcher (the thesis);
    `run_share` means the edge is spread evenly across the game (the null).
    """
    n = len(rows)
    agg = {
        "n": n, "win": 0.0, "tie": 0.0, "loss": 0.0,
        "ml_risk": 0.0, "ml_ev": 0.0, "ml_price_sum": 0,
        "rl_risk": 0.0, "rl_ev": 0.0, "rl_price_sum": 0,
        "fg_risk": 0.0, "fg_ev": 0.0,
        "ml_be_num": 0.0, "ml_be_den": 0.0,
    }
    for g in rows:
        # --- market's own F5 view (no edge) -> fair prices
        m_bet, m_opp = g["lam_bet"] * run_share, g["lam_opp"] * run_share
        q_win, q_tie, q_loss = outcome_probs(m_bet, m_opp)

        # --- model's F5 view (edge re-attributed to innings 1-5)
        e_bet, e_opp = shift(m_bet, m_opp, delta * attribution)
        p_win, p_tie, p_loss = outcome_probs(e_bet, e_opp)
        agg["win"] += p_win
        agg["tie"] += p_tie
        agg["loss"] += p_loss

        # --- 3-way F5 moneyline, priced off the market view + hold
        ml_odds = to_american(min(0.97, q_win * (1 + hold_3way)))
        st, wp = stake_for(ml_odds), win_profit(ml_odds)
        agg["ml_risk"] += st
        agg["ml_ev"] += p_win * wp - (p_tie + p_loss) * st
        agg["ml_price_sum"] += ml_odds
        # Break-even bookkeeping: EV is zero when sum(p_win * (win + stake))
        # equals sum(stake), so this ratio scales the projected win rate down
        # to the rate the actual price/stake mix requires.
        agg["ml_be_num"] += st
        agg["ml_be_den"] += p_win * (wp + st)

        # --- F5 +0.5 run line (bet side wins or ties through 5)
        rl_odds = to_american(min(0.97, (q_win + q_tie) * (1 + hold_rl)))
        st2, wp2 = stake_for(rl_odds), win_profit(rl_odds)
        agg["rl_risk"] += st2
        agg["rl_ev"] += (p_win + p_tie) * wp2 - p_loss * st2
        agg["rl_price_sum"] += rl_odds

        # --- full game at the price actually taken (model validation)
        lb, lo_ = shift(g["lam_bet"], g["lam_opp"], delta)
        p_fg = win_prob_regulation_plus_extras(lb, lo_)
        st3, wp3 = stake_for(g["odds"]), win_profit(g["odds"])
        agg["fg_risk"] += st3
        agg["fg_ev"] += p_fg * wp3 - (1 - p_fg) * st3

    return {
        "attribution": attribution,
        "run_share": run_share,
        "hold_3way": hold_3way,
        "hold_rl": hold_rl,
        "f5_win_pct": agg["win"] / n,
        "f5_tie_pct": agg["tie"] / n,
        "f5_loss_pct": agg["loss"] / n,
        "ml_avg_price": agg["ml_price_sum"] / n,
        "ml_breakeven_win_pct": (agg["win"] / n) * agg["ml_be_num"] / agg["ml_be_den"],
        "ml_units": agg["ml_ev"],
        "ml_roi": agg["ml_ev"] / agg["ml_risk"],
        "rl_avg_price": agg["rl_price_sum"] / n,
        "rl_units": agg["rl_ev"],
        "rl_roi": agg["rl_ev"] / agg["rl_risk"],
        "fg_model_units": agg["fg_ev"],
        "fg_model_roi": agg["fg_ev"] / agg["fg_risk"],
    }


# ------------------------------------------------------------ starter workload

def starter_workload(rows):
    """How much of the first five innings the faded starter actually pitched."""
    props = json.load(open(PROPS))["props"]
    idx = {}
    for p in props:
        if p.get("actual_outs") is None:
            continue
        idx[(p.get("date"), (p.get("player") or "").strip().lower())] = p["actual_outs"]

    outs, matched = [], 0
    for g in rows:
        for name in g["pitchers"]:
            o = idx.get((g["date"], name.strip().lower()))
            if o is not None:
                outs.append(o)
                matched += 1
                break
    if not outs:
        return {"matched": 0}
    outs.sort()
    hist = Counter(min(o, 21) for o in outs)
    return {
        "matched": matched,
        "of_bets": len(rows),
        "mean_ip": sum(outs) / len(outs) / 3.0,
        "median_ip": outs[len(outs) // 2] / 3.0,
        "pct_reach_5th": sum(1 for o in outs if o >= 12) / len(outs),
        "pct_complete_5": sum(1 for o in outs if o >= 15) / len(outs),
        "pct_out_before_4": sum(1 for o in outs if o < 9) / len(outs),
        "mean_f5_outs_covered": sum(min(o, 15) for o in outs) / len(outs),
        "hist_outs": dict(sorted(hist.items())),
    }


# ------------------------------------------------------------------- reporting

def pct(x):
    return f"{100 * x:5.1f}%"


def analyze_sample(label, rows, note):
    """Baseline, calibration and F5 projection for one subset of bets."""
    wins = sum(1 for g in rows if g["result"] == "WIN")
    wr = wins / len(rows)
    risked = sum(g["stake"] for g in rows)
    units = sum(g["profit"] for g in rows)
    mkt_wr = sum(g["p_bet_mkt"] for g in rows) / len(rows)
    se = math.sqrt(mkt_wr * (1 - mkt_wr) / len(rows))

    print("\n" + "=" * 78)
    print(f"SAMPLE: {label}  ({rows[0]['date']} .. {rows[-1]['date']})")
    print(f"  {note}")
    print("=" * 78)
    print(f"  bets                {len(rows)}  ({wins}-{len(rows) - wins})")
    print(f"  realized win rate   {pct(wr)}")
    print(f"  market (de-vigged)  {pct(mkt_wr)}   ->  edge {pct(wr - mkt_wr)}"
          f"  ({(wr - mkt_wr) / se:.1f} sigma)")
    print(f"  units / risked      {units:+.2f}u / {risked:.2f}u   ROI {pct(units / risked)}")

    delta, fit = calibrate_delta(rows, wr)
    print(f"  calibrated delta    {delta:.3f} runs of expected margin per game")

    base = project_f5(rows, delta, 1.0, F5_RUN_SHARE, 0.06, 0.045)
    print(f"  model validation    full-game ROI {pct(base['fg_model_roi'])} "
          f"vs realized {pct(units / risked)}")

    print(f"\n  F5 projection — where does the edge live?")
    print(f"  {'edge in innings 1-5':>20} {'W':>7} {'T':>7} {'L':>7} "
          f"{'3way px':>8} {'3way ROI':>9} {'+0.5 px':>8} {'+0.5 ROI':>9}")
    results = []
    for slabel, attr in (("uniform (null)", F5_RUN_SHARE), ("70% starter", 0.70),
                         ("85% starter", 0.85), ("100% starter", 1.00)):
        r = project_f5(rows, delta, attr, F5_RUN_SHARE, 0.06, 0.045)
        r["label"] = slabel
        results.append(r)
        print(f"  {slabel:>20} {pct(r['f5_win_pct'])} {pct(r['f5_tie_pct'])} "
              f"{pct(r['f5_loss_pct'])} {r['ml_avg_price']:>8.0f} "
              f"{pct(r['ml_roi']):>9} {r['rl_avg_price']:>8.0f} {pct(r['rl_roi']):>9}")

    fg_roi = units / risked
    for r in results:
        r["ml_roi_vs_fg"] = r["ml_roi"] - fg_roi
        r["rl_roi_vs_fg"] = r["rl_roi"] - fg_roi
    return {
        "label": label, "n": len(rows), "win_rate": wr, "market_win_rate": mkt_wr,
        "edge_pp": wr - mkt_wr, "sigma": (wr - mkt_wr) / se,
        "roi": fg_roi, "units": units, "risked": risked,
        "delta_runs": delta, "fit": fit, "scenarios": results,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="write the full result set to this path")
    ap.add_argument("--stake", choices=("house", "flat"), default="house",
                    help="staking convention (default: the fade-ML ledger's)")
    args = ap.parse_args()

    global STAKE_MODE
    STAKE_MODE = args.stake

    fade, rows = load_games()
    attach_lambdas(rows)
    rows.sort(key=lambda g: g["date"])

    print("=" * 78)
    print("FADE FIRST-FIVE FEASIBILITY — model projection off closing lines")
    print("=" * 78)

    wl = starter_workload(rows)
    if wl.get("matched"):
        print(f"\nFaded starter workload  (n={wl['matched']} of {wl['of_bets']} bets)")
        print(f"  mean / median IP    {wl['mean_ip']:.2f} / {wl['median_ip']:.2f}")
        print(f"  reached the 5th     {pct(wl['pct_reach_5th'])}")
        print(f"  completed 5 innings {pct(wl['pct_complete_5'])}")
        print(f"  gone before the 4th {pct(wl['pct_out_before_4'])}")
        print(f"  F5 outs covered     {wl['mean_f5_outs_covered']:.1f} of 15 "
              f"({pct(wl['mean_f5_outs_covered'] / 15)})")

    live = [g for g in rows if g["source"] == "fanduel_api"]
    samples = [
        analyze_sample(
            "LIVE / prospective picks only", live,
            "priced by the daily run against the fade list as it stood that day"),
        analyze_sample(
            "FULL season incl. backfill", rows,
            "backfilled dates re-grade April games against TODAY's 69-name list"),
    ]

    print("\n" + "-" * 78)
    print("SENSITIVITY — F5 hold and inning-1-5 run share, live sample, "
          "100% starter edge")
    print("-" * 78)
    delta_live = samples[0]["delta_runs"]
    print(f"{'run share':>10} {'hold':>7} {'3way ROI':>10} {'+0.5 ROI':>10}")
    grid = []
    for share in (0.54, 0.565, 0.59):
        for hold in (0.045, 0.06, 0.08):
            r = project_f5(live, delta_live, 1.0, share, hold, hold * 0.75)
            grid.append(r)
            print(f"{share:>10.3f} {hold:>7.3f} {pct(r['ml_roi']):>10} "
                  f"{pct(r['rl_roi']):>10}")

    print("\n" + "-" * 78)
    print("STAKING CONVENTION — live sample, 100% starter edge")
    print("-" * 78)
    saved = STAKE_MODE
    print(f"{'mode':>10} {'3way ROI':>10} {'+0.5 ROI':>10} {'3way units':>12}")
    for mode in ("house", "flat"):
        STAKE_MODE = mode
        r = project_f5(live, delta_live, 1.0, F5_RUN_SHARE, 0.06, 0.045)
        print(f"{mode:>10} {pct(r['ml_roi']):>10} {pct(r['rl_roi']):>10} "
              f"{r['ml_units']:>+11.2f}u")
    STAKE_MODE = saved

    print("\n" + "-" * 78)
    print("BREAK-EVEN — live sample")
    print("-" * 78)
    for r in samples[0]["scenarios"]:
        print(f"  {r['label']:>16}: the 3-way price mix needs "
              f"{pct(r['ml_breakeven_win_pct'])} F5 wins to break even; "
              f"model projects {pct(r['f5_win_pct'])} "
              f"({pct(r['f5_win_pct'] - r['ml_breakeven_win_pct'])} cushion)")

    if args.json:
        with open(args.json, "w") as f:
            json.dump({
                "generated_from": os.path.basename(FADE_ML),
                "n_bets": len(rows),
                "starter_workload": wl,
                "samples": samples,
                "sensitivity": grid,
            }, f, indent=2)
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
