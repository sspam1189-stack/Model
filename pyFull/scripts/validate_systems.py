"""
validate_systems.py -- is anything screen_systems.py found actually real?

Two tests, because "it cleared 10% ROI on 1,320 games" answers neither.

TEST 1 -- PERMUTATION.  Reassign every game's result to a different game,
keeping all lines and all situational labels exactly where they were, then
re-run the entire screen.  Repeat N times.  This measures what a screen of
this size returns from pure noise: how many conditions clear the shipping
bar, and how large the best one looks.  A real system has to beat that null,
not beat breakeven.

The permutation swaps the RESIDUAL pair (margin+line, points-total) between
games, so every row recomputes from its own spread:
    adj    = +/- ats_residual        (sign by home/away)
    margin = adj - spread            -> ML win
    over   = total_residual > 0
That preserves the true spread->win-rate curve; only the pairing between a
situation and an outcome is destroyed, which is exactly the null we want.

Caveat, stated rather than hidden: conditions built from PRIOR results
(streaks, season-to-date SU%, previous-game margin) are held fixed under the
permutation instead of being recomputed.  For line-only conditions (home dog
+5, pick'em, spread buckets, calendar) the null is exact.  For form-based
ones it is approximate and, if anything, slightly conservative -- real
outcome sequences produce streakier features than shuffled ones.

TEST 2 -- WALK-FORWARD.  Run the whole screen on the FIRST half of the
season only, take everything that clears the bar there, and score it on the
second half, which the selection never saw.  This does not ask whether these
six systems are real; it asks whether the PROCEDURE that found them finds
real things.  If first-half winners land flat in the second half, the honest
read is that the screen fits noise and no amount of mechanism-storytelling
about a particular survivor rescues it.

Usage:  python pyFull/scripts/validate_systems.py [--perms 300]
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import screen_systems as S

JUICE = S.JUICE
MIN_N = 25
MIN_ROI = 0.10


# ---------------------------------------------------------------------------
def build_masks(rows, games):
    """Condition membership, computed once -- it never depends on outcomes."""
    team_conds = S.team_conditions()
    tot_conds = S.total_conditions()
    home_rows = [r for r in rows if r["is_home"]]

    team_masks = {}
    for name, f in team_conds.items():
        m = np.fromiter((bool(f(r)) for r in rows), dtype=bool, count=len(rows))
        if m.sum() >= MIN_N:
            team_masks[name] = m
    tot_masks = {}
    for name, f in tot_conds.items():
        m = np.fromiter((bool(f(r)) for r in home_rows), dtype=bool, count=len(home_rows))
        if m.sum() >= MIN_N:
            tot_masks[name] = m
    return team_masks, tot_masks, home_rows


def outcome_arrays(rows, home_rows, games):
    gi = np.array([r["gi"] for r in rows])
    sign = np.where(np.array([r["is_home"] for r in rows]), 1.0, -1.0)
    spread = np.array([r["spread"] for r in rows], dtype=float)
    hgi = np.array([r["gi"] for r in home_rows])
    ats_resid = np.array([g["margin_home"] + g["line"] for g in games], dtype=float)
    tot_resid = np.array([g["pts"] - g["total"] for g in games], dtype=float)
    return gi, sign, spread, hgi, ats_resid, tot_resid


def screen_once(team_masks, tot_masks, gi, sign, spread, hgi, ats_r, tot_r):
    """Returns (n_survivors, best_units, per-name units) for one outcome set."""
    adj = sign * ats_r[gi]
    cover = np.where(adj > 0, 1, np.where(adj < 0, -1, 0))      # 1 win, -1 loss, 0 push
    margin = adj - spread
    win = (margin > 0).astype(np.int8)
    over = np.sign(tot_r[hgi])                                   # 1 over, -1 under, 0 push

    survivors, best, units_by = 0, 0.0, {}
    for name, m in team_masks.items():
        c = cover[m]
        w = int((c == 1).sum()); l = int((c == -1).sum()); n = w + l
        if n:
            u = w * JUICE - l
            units_by[("spread", name)] = u
            if n >= MIN_N and u / n >= MIN_ROI:
                survivors += 1; best = max(best, u)
    for name, m in tot_masks.items():
        o = over[m]
        for side, want in (("OVER", 1), ("UNDER", -1)):
            w = int((o == want).sum()); l = int((o == -want).sum()); n = w + l
            if n:
                u = w * JUICE - l
                units_by[(f"total {side}", name)] = u
                if n >= MIN_N and u / n >= MIN_ROI:
                    survivors += 1; best = max(best, u)
    return survivors, best, units_by


def ml_z(rows, masks, gi, sign, spread, ats_r):
    """Max |z| over ML conditions vs a bucket-matched baseline (price-free)."""
    adj = sign * ats_r[gi]
    win = ((adj - spread) > 0).astype(float)
    # baseline win rate by spread band, recomputed from THIS outcome set
    order = np.argsort(spread)
    ss, ws = spread[order], win[order]
    uniq = np.unique(ss)
    base_by = {}
    for s in uniq:
        sel = np.abs(ss - s) <= 1.5
        base_by[s] = ws[sel].mean()
    base = np.array([base_by[s] for s in spread])
    best = 0.0
    for name, m in masks.items():
        if m.sum() < MIN_N:
            continue
        exp = base[m].sum()
        var = (base[m] * (1 - base[m])).sum()
        if var <= 0:
            continue
        z = (win[m].sum() - exp) / np.sqrt(var)
        best = max(best, abs(z))
    return best


# ---------------------------------------------------------------------------
def walk_forward(rows, games, split_date):
    """Select on the first half only; score the winners on the second half."""
    team_conds = S.team_conditions()
    tot_conds = S.total_conditions()
    home_rows = [r for r in rows if r["is_home"]]

    def ats(sub):
        c = [r["cover"] for r in sub if r["cover"] is not None]
        w = sum(c); n = len(c)
        return w, n - w, (w * JUICE - (n - w))

    def ou(sub, side):
        s = [r for r in sub if r["_pts"] != r["total"]]
        w = sum(1 for r in s if (r["_pts"] > r["total"]) == (side == "OVER"))
        return w, len(s) - w, (w * JUICE - (len(s) - w))

    h1r = [r for r in rows if r["d"] < split_date]
    h2r = [r for r in rows if r["d"] >= split_date]
    h1h = [r for r in home_rows if r["d"] < split_date]
    h2h = [r for r in home_rows if r["d"] >= split_date]

    picked = []
    for name, f in team_conds.items():
        sub = [r for r in h1r if f(r)]
        w, l, u = ats(sub)
        if w + l >= MIN_N and u / (w + l) >= MIN_ROI:
            picked.append(("spread", name, (w, l, u), f))
    for name, f in tot_conds.items():
        for side in ("OVER", "UNDER"):
            sub = [r for r in h1h if f(r)]
            w, l, u = ou(sub, side)
            if w + l >= MIN_N and u / (w + l) >= MIN_ROI:
                picked.append((f"total {side}", name, (w, l, u), f))

    out = []
    for market, name, h1rec, f in picked:
        if market == "spread":
            w, l, u = ats([r for r in h2r if f(r)])
        else:
            w, l, u = ou([r for r in h2h if f(r)], market.split()[1])
        out.append((market, name, h1rec, (w, l, u)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--perms", type=int, default=300)
    ap.add_argument("--seed", type=int, default=20260902)
    args = ap.parse_args()

    games = S.load_games()
    stats = S.load_stats()
    rows, playoff_start = S.build(games, stats)
    S.enrich(rows, games)
    S.fit_ml(rows)
    dates = sorted({r["d"] for r in rows})
    split_date = dates[len(dates) // 2]

    team_masks, tot_masks, home_rows = build_masks(rows, games)
    gi, sign, spread, hgi, ats_r, tot_r = outcome_arrays(rows, home_rows, games)
    n_cond = len(team_masks) + 2 * len(tot_masks)

    print(f"Games {len(games)} | conditions screened at n>={MIN_N}: {n_cond} "
          f"({len(team_masks)} spread, {2*len(tot_masks)} total sides)")
    print(f"Shipping bar: n>={MIN_N} and ROI>={MIN_ROI*100:.0f}% at -110\n")

    # ---- observed -------------------------------------------------------
    obs_n, obs_best, obs_units = screen_once(
        team_masks, tot_masks, gi, sign, spread, hgi, ats_r, tot_r)
    obs_ml = ml_z(rows, team_masks, gi, sign, spread, ats_r)

    # ---- permutation null ----------------------------------------------
    rng = np.random.default_rng(args.seed)
    ng = len(games)
    null_n, null_best, null_ml = [], [], []
    for _ in range(args.perms):
        p = rng.permutation(ng)
        n_s, best, _ = screen_once(
            team_masks, tot_masks, gi, sign, spread, hgi, ats_r[p], tot_r[p])
        null_n.append(n_s); null_best.append(best)
        null_ml.append(ml_z(rows, team_masks, gi, sign, spread, ats_r[p]))
    null_n = np.array(null_n); null_best = np.array(null_best); null_ml = np.array(null_ml)

    print("=== TEST 1: PERMUTATION (%d shuffles) ===" % args.perms)
    print(f"  survivors clearing the bar   observed {obs_n:3d}   "
          f"noise {null_n.mean():5.1f} avg, {np.percentile(null_n,95):.0f} at 95th pct, "
          f"{null_n.max():.0f} max")
    print(f"     -> P(noise >= {obs_n}) = {(null_n >= obs_n).mean():.3f}")
    print(f"  best single system, units    observed {obs_best:+6.1f}u  "
          f"noise {null_best.mean():+5.1f}u avg, {np.percentile(null_best,95):+.1f}u at 95th, "
          f"{null_best.max():+.1f}u max")
    print(f"     -> P(noise best >= {obs_best:.1f}u) = {(null_best >= obs_best).mean():.3f}")
    print(f"  best ML |z| (price-free)     observed {obs_ml:5.2f}    "
          f"noise {null_ml.mean():4.2f} avg, {np.percentile(null_ml,95):.2f} at 95th, "
          f"{null_ml.max():.2f} max")
    print(f"     -> P(noise best |z| >= {obs_ml:.2f}) = {(null_ml >= obs_ml).mean():.3f}")

    # ---- walk-forward ---------------------------------------------------
    print(f"\n=== TEST 2: WALK-FORWARD (select on games before {split_date}, "
          f"score after) ===")
    wf = walk_forward(rows, games, split_date)
    if not wf:
        print("  nothing cleared the bar in the first half.")
        return
    tot_u = tot_n = tot_w = tot_l = 0
    pos = 0
    print(f"  {'system':46s} {'H1 (selected on)':>20s} {'H2 (out of sample)':>22s}")
    for market, name, (w1, l1, u1), (w2, l2, u2) in sorted(wf, key=lambda x: -x[3][2]):
        lbl = f"{market} · {name}"
        print(f"  {lbl:46s} {w1:3d}-{l1:<3d} {u1:+6.1f}u   {w2:3d}-{l2:<3d} {u2:+6.1f}u")
        tot_u += u2; tot_w += w2; tot_l += l2; pos += (u2 > 0)
    tot_n = tot_w + tot_l
    print(f"  {'':46s} {'':20s} {'-'*22}")
    print(f"  {len(wf)} systems selected on H1 -> H2: {tot_w}-{tot_l} "
          f"({tot_w/tot_n*100:.1f}%) {tot_u:+.1f}u, ROI {tot_u/tot_n*100:+.1f}%")
    print(f"  {pos}/{len(wf)} stayed positive out of sample "
          f"(coin flip would be ~{len(wf)/2:.0f})")


if __name__ == "__main__":
    main()
