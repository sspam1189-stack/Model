"""
slate_lineup_edge.py — Does the confirmed lineup beat the team number, against the line?

Team wRC+ is public and priced. The hypothesis worth testing is narrower: the
market prices the *club*, while the lineup actually posted that night — two
regulars resting, a callup batting sixth — is known ~2 hours before first pitch
and may not be fully in the number.

For every completed game this rebuilds, point-in-time:

  * ``team``   — the club's wRC+ vs the starter's hand, all pitchers, as
    ``slate_wrc_form`` uses it.
  * ``lineup`` — the same figure computed over only the nine hitters who
    actually batted that night, from their own prior splits vs that hand.
  * ``delta``  — lineup minus team. This is the quantity the market plausibly
    does not have: how much better or worse tonight's nine are than the club.

Only the *composition* of the lineup is taken from the game itself; every
hitter's rate is built strictly from games before that date. Posting time makes
this realistic — lineups are public pre-game.

The scored quantity is the OVER RATE, not ``mean(runs - total)``. Runs are
right-skewed — mean runs 8.96 against a mean total of 8.48 while the over rate
is 49.4% — because totals sit near the median, so a subgroup holding a few
blowouts posts a large positive mean and no edge at all.

Line-timing caveat, and the check for it
----------------------------------------
``mlb-all-ml.json`` keeps the last odds snapshot of the day (verified on
2026-08-14: the persisted totals match the final ``today`` values exactly).
That snapshot lands ~18:09 CT, after lineups post but also at or after first
pitch for eastern night games, which raises the possibility that the recorded
number reflects a game already under way.

``--split-started`` tests exactly that, partitioning on whether first pitch
preceded the snapshot. Leakage would concentrate the edge in the started group.
It does not: the lift over baseline is +10.2 points for games already in
progress and +10.3 for games not yet begun.

Usage
-----
    python MLBstrikeouts/scripts/slate_lineup_edge.py
    python MLBstrikeouts/scripts/slate_lineup_edge.py --min-pa 40 --start 2026-05-01
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import statistics as st
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import slate_wrc_form as S
import slate_wrc_form_backfill as B

BATTING_ORDERS = S.REPO / "data" / "pitcher_cache" / "mlb" / "batting_orders_2026.json"
BATTER_SPLITS = S.REPO / "data" / "pitcher_cache" / "mlb" / "batter_pa_splits_2026.json"

ACC = B.ACC
W, WOBA_SCALE, LG_R_PA = B.W, B.WOBA_SCALE, B.LG_R_PA


def index_batters(rows):
    """
    (batter_id, hand) -> (dates[], cumulative component totals[]).

    Prefix sums so "this hitter's line before date D" is one bisect rather than
    a scan over 75k rows per lookup.
    """
    grouped = defaultdict(list)
    for r in rows:
        if r.get("opp_hand") in ("L", "R") and r.get("game_date"):
            grouped[(r["batter_id"], r["opp_hand"])].append(r)

    index = {}
    for key, rs in grouped.items():
        rs.sort(key=lambda r: r["game_date"])
        dates, cums, run = [], [], {k: 0 for k in ACC}
        for r in rs:
            run = {k: run[k] + (r.get(k) or 0) for k in ACC}
            dates.append(r["game_date"])
            cums.append(run)
        index[key] = (dates, cums)
    return index


def before(index, batter_id, hand, cutoff):
    """That hitter's accumulated components strictly before ``cutoff``."""
    entry = index.get((batter_id, hand))
    if not entry:
        return None
    dates, cums = entry
    i = bisect.bisect_left(dates, cutoff)
    return cums[i - 1] if i else None


def league_woba_by_hand(rows, cutoff):
    """League wOBA vs each hand using only games before ``cutoff``."""
    lg = {"L": {k: 0 for k in ACC}, "R": {k: 0 for k in ACC}}
    for r in rows:
        h = r.get("opp_hand")
        if h in lg and r.get("game_date") and r["game_date"] < cutoff:
            for k in ACC:
                lg[h][k] += r.get(k) or 0
    return {h: B._woba(v) for h, v in lg.items()}


def wrcplus(components, lg_woba):
    if not (components["ab"] + components["bb"] + components["hbp"]):
        return None
    return 100 * ((B._woba(components) - lg_woba) / WOBA_SCALE + LG_R_PA) / LG_R_PA


def corr(a, b):
    if len(a) < 3:
        return 0.0
    ma, mb = st.mean(a), st.mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
    return num / den if den else 0.0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2026-05-01")
    ap.add_argument("--min-pa", type=int, default=40,
                    help="minimum pooled lineup PA vs the hand to trust the figure")
    ap.add_argument("--cut", type=float, default=0.0,
                    help="back the over when delta is at or below this")
    ap.add_argument("--split-started", action="store_true",
                    help="partition on whether first pitch preceded the odds "
                         "snapshot, to test for in-play leakage")
    args = ap.parse_args()

    orders = S._load(BATTING_ORDERS)
    bsplits = S._load(BATTER_SPLITS)
    team_rows = S._load(B.PA_SPLITS)
    allml = S._load(S.ALL_ML)
    index = index_batters(bsplits)

    by_date, seen = defaultdict(list), set()
    for g in allml.get("games", []):
        if g.get("home_score") is None or g.get("total_line") is None:
            continue
        key = (g.get("date"), g.get("away"), g.get("home"), g.get("commence"))
        if key in seen:
            continue
        seen.add(key)
        by_date[g["date"]].append(g)

    recs, skipped = [], defaultdict(int)
    for d in sorted(x for x in by_date if x >= args.start):
        day_orders = orders.get(d)
        if not day_orders:
            skipped["no lineup data for date"] += len(by_date[d])
            continue
        lg = league_woba_by_hand(bsplits, d)
        # Neutral, to match the park-neutral lineup figure it is differenced against.
        team_wrc = B.wrc_as_of(team_rows, d, park_adjust=False)["season"]["teams"]

        for g in by_date[d]:
            row = {"date": d, "matchup": f"{g['away']} @ {g['home']}",
                   "total": g["total_line"],
                   "runs": (g.get("home_score") or 0) + (g.get("away_score") or 0),
                   "sides": {}}
            ok = True
            # The home bats face the away starter, and vice versa.
            for bat_team, pitch_side in ((g["home"], "away"), (g["away"], "home")):
                hand = g.get(f"{pitch_side}_hand")
                ids = day_orders.get(bat_team)
                if hand not in ("L", "R") or not ids:
                    ok = False
                    skipped["missing lineup or hand"] += 1
                    break
                pooled = {k: 0 for k in ACC}
                for bid in ids:
                    c = before(index, bid, hand, d)
                    if c:
                        for k in ACC:
                            pooled[k] += c[k]
                if pooled["pa"] < args.min_pa:
                    ok = False
                    skipped["lineup below min PA"] += 1
                    break
                lu = wrcplus(pooled, lg[hand])
                tm = (team_wrc.get(bat_team) or {}).get(
                    "vsLHP" if hand == "L" else "vsRHP")
                if lu is None or not tm:
                    ok = False
                    skipped["no team figure"] += 1
                    break
                row["sides"][bat_team] = {"lineup": lu, "team": tm["wrcplus"],
                                          "delta": lu - tm["wrcplus"],
                                          "pa": pooled["pa"]}
            # The day's final snapshot lands ~23:00Z; anything starting earlier
            # was already under way when the recorded line was captured.
            row["started"] = bool(g.get("commence")) and g["commence"] < f"{d}T23:00:00Z"
            if ok and len(row["sides"]) == 2:
                recs.append(row)

    if not recs:
        print("no usable games")
        return

    print(f"games scored: {len(recs)}  ({recs[0]['date']} .. {recs[-1]['date']})")
    for k, v in sorted(skipped.items(), key=lambda x: -x[1]):
        print(f"  skipped — {k}: {v}")

    lineup = [sum(s["lineup"] for s in r["sides"].values()) / 2 for r in recs]
    team = [sum(s["team"] for s in r["sides"].values()) / 2 for r in recs]
    delta = [sum(s["delta"] for s in r["sides"].values()) / 2 for r in recs]
    runs = [r["runs"] for r in recs]
    total = [r["total"] for r in recs]
    resid = [r["runs"] - r["total"] for r in recs]

    print(f"\nmean lineup wRC+ {st.mean(lineup):.1f} | mean team wRC+ "
          f"{st.mean(team):.1f} | mean delta {st.mean(delta):+.1f} "
          f"(sd {st.pstdev(delta):.1f})")
    print(f"\n{'signal':<28}{'vs runs':>10}{'vs total':>10}{'vs runs-total':>15}")
    for name, series in (("team wRC+", team), ("lineup wRC+", lineup),
                         ("delta (lineup - team)", delta)):
        print(f"{name:<28}{corr(series, runs):>+10.4f}"
              f"{corr(series, total):>+10.4f}{corr(series, resid):>+15.4f}")

    def over_rate(rs):
        o = sum(1 for r in rs if r["runs"] > r["total"])
        u = sum(1 for r in rs if r["runs"] < r["total"])
        return (o / (o + u) * 100 if o + u else 0.0), o + u

    if args.split_started:
        print(f"\nin-play leakage check (rule: over when delta <= {args.cut:g})")
        print(f"{'group':<36}{'baseline':>12}{'rule':>16}{'lift':>8}")
        for lbl, keep in (("all games", lambda r: True),
                          ("in progress at snapshot", lambda r: r["started"]),
                          ("not yet started", lambda r: not r["started"])):
            base = [r for r in recs if keep(r)]
            rule = [r for r in base
                    if sum(x["delta"] for x in r["sides"].values()) / 2 <= args.cut]
            br, bn = over_rate(base)
            rr, rn = over_rate(rule)
            print(f"{lbl:<36}{br:>8.1f}% n={bn:<5}{rr:>8.1f}% n={rn:<5}{rr - br:>+7.1f}")

    print("\ndelta quintile — is the market missing lineup quality?")
    s = sorted(recs, key=lambda r: sum(x["delta"] for x in r["sides"].values()) / 2)
    k = len(s) // 5
    print(f"{'quintile':<10}{'n':>5}{'mean delta':>12}{'mean runs':>11}"
          f"{'mean total':>12}{'runs-total':>12}")
    for i in range(5):
        ch = s[i * k:(i + 1) * k] if i < 4 else s[4 * k:]
        md = st.mean(sum(x["delta"] for x in r["sides"].values()) / 2 for r in ch)
        print(f"{i + 1:<10}{len(ch):>5}{md:>12.1f}"
              f"{st.mean(r['runs'] for r in ch):>11.2f}"
              f"{st.mean(r['total'] for r in ch):>12.2f}"
              f"{st.mean(r['runs'] - r['total'] for r in ch):>+12.2f}")


if __name__ == "__main__":
    main()
