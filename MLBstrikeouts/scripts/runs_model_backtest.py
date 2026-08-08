# MLBstrikeouts/scripts/runs_model_backtest.py
"""Walk-forward backtest of a component expected-runs model (ML + totals).

The model prices every game from point-in-time data only:

  Offense   — team wOBA vs the opposing starter's hand (SP-role PAs) and vs
              bullpens (RP-role PAs), computed per date from the PA-level
              splits file (build_pbp_team_pa.py output), exponentially
              date-decayed and shrunk to the league split.
  Pitcher   — each starter's allowed wOBA and batters-faced per start,
              reconstructed by joining the opponent's SP-role PA row to the
              named starter in mlb-all-ml.json for his prior starts.
              Doubleheader dates are skipped for attribution (the two
              starters' rows can't be told apart).
  Park/HFA  — static prior TB park factors (sources/park_factors.py) applied
              at the venue after deflating each side's raw inputs by their
              own park exposure; fixed home-field run multiplier.
  Runs      — offense-vs-pitcher wOBA combined multiplicatively around the
              league split (odds-ratio style), converted to runs/PA via the
              standard (wOBA - lg)/wobaScale + lgR/PA, split into
              starter-innings PAs vs bullpen PAs.
  Pricing   — each team's runs ~ negative binomial (var/mean = 2);
              convolution gives P(home win) and P(total > line).

Grading (flat 1u per bet, per repo convention for analysis scripts):
  ML     — bet a side when model win prob exceeds the devigged market
           implied prob by >= threshold, at the listed price.
  Totals — bet over/under when |model total - line| >= threshold, at the
           listed over/under price. Whole-number pushes are profit-0 and
           excluded from the risk denominator.

Usage:
  python3 runs_model_backtest.py [--start 2026-05-01] [--no-decay]
                                 [--half-life 45]

Results (first run, 2026-08-08, 1273 games from 2026-05-01):
  - Totals MAE 3.65 vs market 3.56; ML Brier 0.2499 vs market 0.2436.
    The model tracks the closing line closely but does not beat it.
  - Blend test: every model/market blend scores WORSE than the pure market
    on both ML and totals -- the model carries no information the closing
    line doesn't already have.
  - ROI vs closing is negative at every threshold; larger disagreements
    lose MORE (ML edge>=10%: -12.9%; totals gap>=2.0: -13.6%), i.e. big
    model-vs-market gaps flag the model's blind spots (aces, lineups,
    weather), not market errors. The +7.1% blip at totals gap 1.5-2.0
    (149 bets) is bracketed by losing bands on both sides -- variance.
  Conclusion: use the model for fair-value context and slate triage, not
  as a standalone betting signal against closing prices. Decay vs
  no-decay is a wash.
"""

import argparse
import json
import math
import os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
ROOT_DATA = os.path.join(HERE, "..", "..", "data")

ALL_ML = os.path.join(DATA, "mlb-all-ml.json")
PA_SPLITS = os.path.join(ROOT_DATA, "pitcher_cache", "mlb", "team_pa_splits_2026.json")

# Standard wOBA weights — same as build_team_woba_splits.py.
W_BB, W_HBP, W_1B, W_2B, W_3B, W_HR = 0.69, 0.72, 0.88, 1.24, 1.57, 2.00
WOBA_SCALE = 1.24

# Shrinkage priors (PA of league average blended into each estimate).
PRIOR_PA_OFF = 250.0   # offense split estimates
PRIOR_PA_PIT = 150.0   # pitcher allowed-wOBA (~6 starts)
PRIOR_STARTS_BF = 5.0  # pitcher batters-faced per start
BF_LEAGUE = 22.5       # league avg batters faced per start
PA_PER_TEAM = 38.0     # league avg team PA per game

HFA_RUNS = 1.024       # home run-scoring multiplier (away gets 1/HFA)
NB_VMR = 2.0           # negative binomial variance/mean ratio for team runs
MAX_RUNS = 40          # distribution support per team

ML_EDGES = [0.02, 0.04, 0.06, 0.08, 0.10]
TOT_EDGES = [0.3, 0.5, 0.75, 1.0, 1.5, 2.0]

try:
    from sources.park_factors import PRIOR_PARK_FACTORS
except ImportError:  # run from repo root
    import sys
    sys.path.insert(0, HERE)
    from sources.park_factors import PRIOR_PARK_FACTORS


def park_tb(team):
    return PRIOR_PARK_FACTORS.get(team, {}).get("tb", 1.0)


def woba_of(agg):
    """agg: dict with pa, h, doubles, triples, hr, bb, hbp (summed, may be decayed floats)."""
    pa = agg["pa"]
    if pa <= 0:
        return None
    singles = agg["h"] - agg["doubles"] - agg["triples"] - agg["hr"]
    num = (W_BB * agg["bb"] + W_HBP * agg["hbp"] + W_1B * singles
           + W_2B * agg["doubles"] + W_3B * agg["triples"] + W_HR * agg["hr"])
    return num / pa


EVENT_KEYS = ("pa", "h", "doubles", "triples", "hr", "bb", "hbp")


def add_row(agg, row, wt):
    for k in EVENT_KEYS:
        agg[k] += wt * row[k]


def shrink(woba, pa, lg, prior_pa):
    if woba is None:
        return lg
    return (woba * pa + lg * prior_pa) / (pa + prior_pa)


# ---------------------------------------------------------------------------
# Negative-binomial run distribution
# ---------------------------------------------------------------------------

def nb_pmf(mu):
    """P(runs = k) for k in 0..MAX_RUNS, NB with mean mu, var = NB_VMR * mu."""
    mu = max(mu, 0.05)
    r = mu / (NB_VMR - 1.0)
    p = r / (r + mu)  # success prob; P(X=k) = C(k+r-1,k) p^r (1-p)^k
    lgr = math.lgamma(r)
    logp_r = r * math.log(p)
    log1mp = math.log(1.0 - p)
    pmf = []
    for k in range(MAX_RUNS + 1):
        lg = math.lgamma(k + r) - lgr - math.lgamma(k + 1) + logp_r + k * log1mp
        pmf.append(math.exp(lg))
    s = sum(pmf)
    return [x / s for x in pmf]


def game_probs(mu_home, mu_away, total_line):
    ph_pmf, pa_pmf = nb_pmf(mu_home), nb_pmf(mu_away)
    p_hwin = p_awin = p_tie = 0.0
    p_over = p_push = 0.0
    for h, ph in enumerate(ph_pmf):
        for a, pa in enumerate(pa_pmf):
            pr = ph * pa
            if h > a:
                p_hwin += pr
            elif a > h:
                p_awin += pr
            else:
                p_tie += pr
            t = h + a
            if total_line is not None:
                if t > total_line:
                    p_over += pr
                elif t == total_line:
                    p_push += pr
    # ties go to extras: split by relative strength
    if p_hwin + p_awin > 0:
        p_home = p_hwin + p_tie * (p_hwin / (p_hwin + p_awin))
    else:
        p_home = 0.5
    return p_home, p_over, p_push


# ---------------------------------------------------------------------------
# Odds helpers (flat 1u staking)
# ---------------------------------------------------------------------------

def implied(ml):
    return (-ml) / (-ml + 100.0) if ml < 0 else 100.0 / (ml + 100.0)


def profit_1u(ml, won):
    if not won:
        return -1.0
    return ml / 100.0 if ml > 0 else 100.0 / (-ml)


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------

def load_games():
    d = json.load(open(ALL_ML))
    by_pk = {}
    for g in d.get("games", []) + d.get("today", []):
        if g.get("home_score") is None or g.get("away_score") is None:
            continue
        by_pk[g["gamePk"]] = g
    return sorted(by_pk.values(), key=lambda g: (g["date"], g.get("commence") or ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-05-01")
    ap.add_argument("--no-decay", action="store_true")
    ap.add_argument("--half-life", type=float, default=45.0)
    ap.add_argument("--hfa", type=float, default=None, help="override HFA_RUNS")
    ap.add_argument("--vmr", type=float, default=None, help="override NB_VMR")
    ap.add_argument("--dump", default=None, help="write per-game predictions JSON here")
    args = ap.parse_args()
    global HFA_RUNS, NB_VMR
    if args.hfa is not None:
        HFA_RUNS = args.hfa
    if args.vmr is not None:
        NB_VMR = args.vmr

    rows = json.load(open(PA_SPLITS))
    rows.sort(key=lambda r: r["game_date"])
    games = load_games()

    # date -> ordinal for decay weights
    def dord(s):
        y, m, dd = int(s[:4]), int(s[5:7]), int(s[8:10])
        return (m - 1) * 31 + dd + (0 if y == 2026 else -10000)

    # doubleheader dates per matchup: skip for pitcher attribution
    matchup_count = defaultdict(int)
    for g in games:
        matchup_count[(g["date"], g["home"], g["away"])] += 1

    # starter history: name -> list of (date, opp_offense_team, own_team)
    starts = defaultdict(list)
    for g in games:
        if matchup_count[(g["date"], g["home"], g["away"])] > 1:
            continue
        if g.get("home_pitcher"):
            starts[g["home_pitcher"]].append((g["date"], g["away"], g["home"]))
        if g.get("away_pitcher"):
            starts[g["away_pitcher"]].append((g["date"], g["home"], g["away"]))

    # index PA rows: (date, team, opp, role) -> summed row (merges DH halves;
    # DH dates are excluded from pitcher attribution above)
    pa_idx = {}
    for r in rows:
        key = (r["game_date"], r["team"], r["opp"], r["role"])
        if key in pa_idx:
            for k in EVENT_KEYS:
                pa_idx[key][k] += r[k]
        else:
            pa_idx[key] = {k: r[k] for k in EVENT_KEYS}

    results = []
    pending_dates = sorted({g["date"] for g in games if g["date"] >= args.start})
    date_set = set(pending_dates)

    # walk forward date by date, keeping a cursor into the PA rows
    cursor = 0
    # offense aggregates rebuilt per date (decay makes incremental updates messy;
    # full rebuild is fine: ~120 dates * 9.5k rows)
    for date in pending_dates:
        # rows strictly before this date
        while cursor < len(rows) and rows[cursor]["game_date"] < date:
            cursor += 1
        hist = rows[:cursor]
        if not hist:
            continue
        today = dord(date)

        off_sp = defaultdict(lambda: defaultdict(float))   # (team, hand) -> agg
        off_rp = defaultdict(lambda: defaultdict(float))   # team -> agg
        lg_sp = defaultdict(lambda: defaultdict(float))    # hand -> agg
        lg_rp = defaultdict(float)
        for r in hist:
            wt = 1.0 if args.no_decay else 0.5 ** ((today - dord(r["game_date"])) / args.half_life)
            if r["role"] == "SP":
                add_row(off_sp[(r["team"], r["opp_hand"])], r, wt)
                add_row(lg_sp[r["opp_hand"]], r, wt)
            else:
                add_row(off_rp[r["team"]], r, wt)
                add_row(lg_rp, r, wt)

        lg_sp_w = {h: woba_of(lg_sp[h]) for h in ("L", "R")}
        lg_rp_w = woba_of(lg_rp)
        # league runs/game per team from completed games before date
        prior_totals = [g["home_score"] + g["away_score"] for g in games if g["date"] < date]
        lg_rpg = (sum(prior_totals) / len(prior_totals) / 2.0) if prior_totals else 4.4
        lg_rppa = lg_rpg / PA_PER_TEAM

        # pitcher allowed history as of date (decayed), from opponents' SP rows
        def pitcher_stats(name):
            agg = defaultdict(float)
            n_starts, bf_sum, wsum = 0, 0.0, 0.0
            for (sdate, opp_team, own_team) in starts.get(name, ()):
                if sdate >= date:
                    continue
                row = pa_idx.get((sdate, opp_team, own_team, "SP"))
                if not row:
                    continue
                wt = 1.0 if args.no_decay else 0.5 ** ((today - dord(sdate)) / args.half_life)
                add_row(agg, row, wt)
                n_starts += 1
                bf_sum += wt * row["pa"]
                wsum += wt
            w = woba_of(agg) if agg["pa"] > 0 else None
            bf = bf_sum / wsum if wsum > 0 else None
            return w, agg["pa"], bf, n_starts

        for g in (x for x in games if x["date"] == date):
            hand_home_sp = g.get("home_hand")   # hand the AWAY offense faces
            hand_away_sp = g.get("away_hand")
            if not hand_home_sp or not hand_away_sp:
                continue

            def team_mu(off_team, opp_sp_name, opp_sp_hand, opp_team, is_home):
                lg_split = lg_sp_w.get(opp_sp_hand) or 0.310
                o = off_sp.get((off_team, opp_sp_hand))
                off_w = shrink(woba_of(o) if o else None, o["pa"] if o else 0.0,
                               lg_split, PRIOR_PA_OFF)
                orp = off_rp.get(off_team)
                off_w_rp = shrink(woba_of(orp) if orp else None, orp["pa"] if orp else 0.0,
                                  lg_rp_w or 0.310, PRIOR_PA_OFF)
                pit_w_raw, pit_pa, bf_raw, _ = pitcher_stats(opp_sp_name) if opp_sp_name else (None, 0.0, None, 0)
                pit_w = shrink(pit_w_raw, pit_pa, lg_split, PRIOR_PA_PIT)
                bf = ((bf_raw if bf_raw is not None else BF_LEAGUE) * min(pit_pa / BF_LEAGUE, PRIOR_STARTS_BF)
                      + BF_LEAGUE * PRIOR_STARTS_BF) / (min(pit_pa / BF_LEAGUE, PRIOR_STARTS_BF) + PRIOR_STARTS_BF)

                # deflate raw inputs by each side's season park exposure
                off_exposure = (1.0 + park_tb(off_team)) / 2.0
                pit_exposure = (1.0 + park_tb(opp_team)) / 2.0

                # combine offense vs pitcher around the league split (ratio space)
                w_vs_sp = lg_split * (off_w / lg_split / off_exposure) * (pit_w / lg_split / pit_exposure)
                w_vs_rp = (lg_rp_w or 0.310) * (off_w_rp / (lg_rp_w or 0.310) / off_exposure)

                rppa_sp = lg_rppa + (w_vs_sp - lg_split) / WOBA_SCALE
                rppa_rp = lg_rppa + (w_vs_rp - (lg_rp_w or 0.310)) / WOBA_SCALE
                mu = bf * max(rppa_sp, 0.005) + (PA_PER_TEAM - bf) * max(rppa_rp, 0.005)
                mu *= park_tb(g["home"])  # venue
                mu *= HFA_RUNS if is_home else (1.0 / HFA_RUNS)
                return mu

            mu_home = team_mu(g["home"], g.get("away_pitcher"), hand_away_sp, g["away"], True)
            mu_away = team_mu(g["away"], g.get("home_pitcher"), hand_home_sp, g["home"], False)

            p_home, p_over, p_push = game_probs(mu_home, mu_away, g.get("total_line"))
            results.append({
                "g": g, "mu_home": mu_home, "mu_away": mu_away,
                "p_home": p_home, "p_over": p_over, "p_push": p_push,
            })

    if args.dump:
        out = []
        for r in results:
            g = r["g"]
            out.append({
                "date": g["date"], "home": g["home"], "away": g["away"],
                "home_ml": g.get("home_ml"), "away_ml": g.get("away_ml"),
                "total_line": g.get("total_line"),
                "over_ml": g.get("over_ml"), "under_ml": g.get("under_ml"),
                "home_score": g["home_score"], "away_score": g["away_score"],
                "home_win": g.get("home_win"),
                "mu_home": r["mu_home"], "mu_away": r["mu_away"],
                "p_home": r["p_home"], "p_over": r["p_over"],
            })
        with open(args.dump, "w") as f:
            json.dump(out, f)
        print(f"dumped {len(out)} predictions -> {args.dump}")
    report(results)


def report(results):
    n = len(results)
    print(f"graded games: {n}")
    if not n:
        return

    # ---- totals accuracy ----
    tot_rows = [r for r in results if r["g"].get("total_line") is not None]
    mae_model = sum(abs((r["mu_home"] + r["mu_away"]) - (r["g"]["home_score"] + r["g"]["away_score"]))
                    for r in tot_rows) / len(tot_rows)
    mae_market = sum(abs(r["g"]["total_line"] - (r["g"]["home_score"] + r["g"]["away_score"]))
                     for r in tot_rows) / len(tot_rows)
    bias = sum((r["mu_home"] + r["mu_away"]) - (r["g"]["home_score"] + r["g"]["away_score"])
               for r in tot_rows) / len(tot_rows)
    print(f"\nTOTALS  n={len(tot_rows)}  model MAE={mae_model:.3f}  market MAE={mae_market:.3f}  model bias={bias:+.3f}")

    # ---- ML accuracy ----
    def devig_home(g):
        ih, ia = implied(g["home_ml"]), implied(g["away_ml"])
        return ih / (ih + ia)

    ml_rows = [r for r in results
               if r["g"].get("home_ml") and r["g"].get("away_ml")
               and r["g"].get("home_win") in (0, 1)]
    brier_model = sum((r["p_home"] - r["g"]["home_win"]) ** 2 for r in ml_rows) / len(ml_rows)
    brier_market = sum((devig_home(r["g"]) - r["g"]["home_win"]) ** 2 for r in ml_rows) / len(ml_rows)
    print(f"ML      n={len(ml_rows)}  model Brier={brier_model:.4f}  market Brier={brier_market:.4f}")

    # ---- does the model add signal on top of the market? (blend test) ----
    print("\nBlend test (does model improve the market?):")
    for a in (0.0, 0.25, 0.5):
        bb = sum(((a * r["p_home"] + (1 - a) * devig_home(r["g"])) - r["g"]["home_win"]) ** 2
                 for r in ml_rows) / len(ml_rows)
        print(f"  ML Brier  {a:.0%} model + {1-a:.0%} market: {bb:.4f}")
    for a in (0.0, 0.25, 0.5):
        bm = sum(abs((a * (r["mu_home"] + r["mu_away"]) + (1 - a) * r["g"]["total_line"])
                     - (r["g"]["home_score"] + r["g"]["away_score"])) for r in tot_rows) / len(tot_rows)
        print(f"  Tot MAE   {a:.0%} model + {1-a:.0%} market: {bm:.3f}")

    # ---- calibration (model home prob) ----
    print("\nML calibration (model p_home vs actual):")
    buckets = defaultdict(lambda: [0, 0])
    for r in ml_rows:
        b = min(int(r["p_home"] * 10), 9)
        buckets[b][0] += 1
        buckets[b][1] += r["g"]["home_win"]
    for b in sorted(buckets):
        cnt, wins = buckets[b]
        print(f"  p={b/10:.1f}-{(b+1)/10:.1f}  n={cnt:4d}  actual={wins/cnt:.3f}")

    # ---- ML ROI by edge threshold ----
    print("\nML flat-1u ROI by edge vs devigged market:")
    for e in ML_EDGES:
        stake = profit = wins = bets = 0
        for r in ml_rows:
            g = r["g"]
            mh = devig_home(g)
            side = None
            if r["p_home"] - mh >= e:
                side, ml, won = "H", g["home_ml"], g["home_win"] == 1
            elif (1 - r["p_home"]) - (1 - mh) >= e:
                side, ml, won = "A", g["away_ml"], g["home_win"] == 0
            if side:
                bets += 1
                stake += 1
                pr = profit_1u(ml, won)
                profit += pr
                wins += 1 if won else 0
        roi = profit / stake * 100 if stake else 0.0
        print(f"  edge>={e:.0%}:  bets={bets:4d}  W-L={wins}-{bets-wins}  profit={profit:+.2f}u  ROI={roi:+.1f}%")

    # ---- totals ROI by run-gap threshold ----
    print("\nTotals flat-1u ROI by |model total - line|:")
    for e in TOT_EDGES:
        stake = profit = wins = bets = pushes = 0
        for r in tot_rows:
            g = r["g"]
            line = g["total_line"]
            mt = r["mu_home"] + r["mu_away"]
            actual = g["home_score"] + g["away_score"]
            side = None
            if mt - line >= e and g.get("over_ml"):
                side, ml, won = "O", g["over_ml"], actual > line
            elif line - mt >= e and g.get("under_ml"):
                side, ml, won = "U", g["under_ml"], actual < line
            if side:
                if actual == line:
                    pushes += 1
                    continue
                bets += 1
                stake += 1
                pr = profit_1u(ml, won)
                profit += pr
                wins += 1 if won else 0
        roi = profit / stake * 100 if stake else 0.0
        print(f"  gap>={e:.2f}: bets={bets:4d}  W-L={wins}-{bets-wins}  push={pushes}  profit={profit:+.2f}u  ROI={roi:+.1f}%")

    # ---- monthly totals-model drift ----
    print("\nMonthly model-vs-market (totals MAE):")
    by_m = defaultdict(list)
    for r in tot_rows:
        by_m[r["g"]["date"][:7]].append(r)
    for m in sorted(by_m):
        rs = by_m[m]
        mm = sum(abs((r["mu_home"] + r["mu_away"]) - (r["g"]["home_score"] + r["g"]["away_score"])) for r in rs) / len(rs)
        mk = sum(abs(r["g"]["total_line"] - (r["g"]["home_score"] + r["g"]["away_score"])) for r in rs) / len(rs)
        print(f"  {m}: n={len(rs):3d}  model={mm:.3f}  market={mk:.3f}")


if __name__ == "__main__":
    main()
