#!/usr/bin/env python3
"""
backtest_mismatch.py -- does the slate-scout mismatch score beat a moneyline?

The scout's mismatch column is the strongest DESCRIPTIVE field in the payload
(corr +0.184 with runs the offense scores, t=2.83 -- better than the starter's
own last-5 ERA). The 121-game snapshot cache hinted that a symmetric rule --
TAIL the dominant arm at m <= -T, FADE the dominated arm at m >= +T -- went
20-8 (+23.8% ROI) on the moneyline. That is 28 plays. This script asks the
question on the full season instead.

WHY A PROXY IS UNAVOIDABLE
--------------------------
mismatch() needs the opponent's wRC+ vs the starter's HAND as of the game
date. That number is only published as a current snapshot
(mlb-team-woba-splits.json), and the daily batter-split archive covers 13
dates in April -- there is no historical series to read. So the opponent term
is rebuilt from primary results: a team's runs per game against LHP / RHP over
a trailing window, indexed to the league rate and centred on 100. Same scale,
same sign, different instrument.

`validate` scores that substitution against the real thing on the snapshot
cache. A proxy that does not track the published score makes every number
below meaningless, so run it first and read the correlation before anything
else.

Everything is strictly as-of: only starts and games BEFORE the slate date feed
a row, so no result leaks into its own feature.

Usage:
    python3 scripts/backtest_mismatch.py validate
    python3 scripts/backtest_mismatch.py report
    python3 scripts/backtest_mismatch.py report --min-window-games 8
"""
import argparse
import datetime
import json
import math
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
ALL_ML = os.path.join(REPO, "MLBstrikeouts", "data", "mlb-all-ml.json")
GAME_LOGS = os.path.join(REPO, "data", "pitcher_cache", "mlb", "game_logs_2026.json")
BATTER_LOGS = os.path.join(REPO, "data", "pitcher_cache", "mlb",
                           "batter_game_logs_2026.json")
CACHE = os.path.join(REPO, "MLBstrikeouts", "data", "scout-backtest-cache.json")

# Mirrors slate_wrc_form.mismatch(). Kept as literals rather than imported so a
# change there cannot silently rewrite this backtest's history.
LG_K_PCT, LG_BB_PCT = 22.0, 8.3
BASE_ERA = 4.20
ERA_W, KBB_W = 8.0, 1.2
RECENT_STARTS = 5

OFFENSE_DAYS = 30        # trailing window for the offense-vs-hand index
MIN_WINDOW_GAMES = 6     # kept for the runs fallback
MIN_WINDOW_PA = 150      # matches slate_wrc_form.MIN_WINDOW_PA

# wOBA linear weights, copied from mlb-team-woba-splits.json so a refresh of
# that payload cannot silently restate this backtest's history.
WOBA_W = {"bb": 0.69, "hbp": 0.72, "s": 0.88, "d": 1.24, "t": 1.57, "hr": 2.0}


def _load(path):
    with open(path) as fh:
        return json.load(fh)


def _norm(name):
    return " ".join((name or "").split()).lower()


class AsOf:
    """As-of-date accessors. Everything filters strictly to dates < the game."""

    def __init__(self, games, logs, batter_rows):
        self.starts = defaultdict(list)
        for r in logs:
            if r.get("is_start"):
                self.starts[_norm(r["pitcher_name"])].append((r["game_date"], r))
        for v in self.starts.values():
            v.sort(key=lambda x: x[0])

        # team -> [(date, hand, pa, woba_numerator)] from real plate appearances
        self.vs_hand = defaultdict(list)
        for team, date, hand, pa, num in batter_rows:
            self.vs_hand[team].append((date, hand, pa, num))
        for v in self.vs_hand.values():
            v.sort(key=lambda x: x[0])

    def starter_form(self, pitcher, date):
        rows = [r for d, r in self.starts.get(_norm(pitcher), []) if d < date]
        rows = rows[-RECENT_STARTS:]
        if len(rows) < 3:
            return None
        outs = sum(int(r.get("outs") or 0) for r in rows)
        bf = sum(int(r.get("bf") or 0) for r in rows)
        if not outs or not bf:
            return None
        ip = outs / 3.0
        return {"era": sum(int(r.get("er") or 0) for r in rows) * 9.0 / ip,
                "kbb": 100.0 * (sum(int(r.get("k") or 0) for r in rows)
                                - sum(int(r.get("bb") or 0) for r in rows)) / bf}

    def woba(self, team, date, hand, days):
        """(PA, wOBA) for this team vs this hand over the trailing window."""
        start = (datetime.date.fromisoformat(date)
                 - datetime.timedelta(days=days)).isoformat()
        pa = num = 0.0
        for d, h, p, n in self.vs_hand.get(team, []):
            if h == hand and start <= d < date:
                pa += p
                num += n
        return (pa, num / pa) if pa else (0.0, None)

    def offense_index(self, team, date, hand, days, min_pa, lg_woba):
        """Team wOBA vs this hand, indexed to the league mark, centred on 100.

        This is the faithful stand-in for "opponent wRC+ vs his hand": same
        input (plate appearances, not runs), same 100-centred scale. It is not
        park-adjusted, which is the one thing the published column does that
        this does not.
        """
        pa, w = self.woba(team, date, hand, days)
        if pa < min_pa or w is None or not lg_woba:
            return None
        return 100.0 * w / lg_woba


def load_batter_rows():
    """(team, date, opposing-starter hand, PA, wOBA numerator) per batter-game.

    The batter logs carry opp_pitcher_id but leave opp_pitcher_hand blank for
    every one of the 41,711 rows, so the hand is joined id -> name (pitcher
    game logs) -> hand (the starter hands in mlb-all-ml). That resolves 100%.
    """
    from collections import Counter
    name2hand = {}
    for g in _load(ALL_ML)["games"]:
        for side in ("home", "away"):
            n, h = g.get(f"{side}_pitcher"), g.get(f"{side}_hand")
            if n and h:
                name2hand.setdefault(_norm(n), Counter())[h] += 1
    name2hand = {k: v.most_common(1)[0][0] for k, v in name2hand.items()}
    id2name = {str(r["pitcher_id"]): _norm(r["pitcher_name"])
               for r in _load(GAME_LOGS)}

    rows = []
    for logs in _load(BATTER_LOGS).values():
        for r in logs:
            hand = name2hand.get(id2name.get(str(r.get("opp_pitcher_id")), ""))
            pa = int(r.get("pa") or 0)
            if not hand or not pa:
                continue
            h_, d_, t_, hr = (int(r.get(k) or 0)
                              for k in ("h", "doubles", "triples", "hr"))
            singles = max(0, h_ - d_ - t_ - hr)
            num = (WOBA_W["bb"] * int(r.get("bb") or 0)
                   + WOBA_W["hbp"] * int(r.get("hbp") or 0)
                   + WOBA_W["s"] * singles + WOBA_W["d"] * d_
                   + WOBA_W["t"] * t_ + WOBA_W["hr"] * hr)
            rows.append((r["team"], r["game_date"], hand, pa, num))
    return rows


def league_woba_by_hand(batter_rows, date, days=OFFENSE_DAYS):
    """League wOBA vs each hand over the same trailing window."""
    start = (datetime.date.fromisoformat(date)
             - datetime.timedelta(days=days)).isoformat()
    agg = defaultdict(lambda: [0.0, 0.0])
    for _, d, hand, pa, num in batter_rows:
        if start <= d < date:
            agg[hand][0] += pa
            agg[hand][1] += num
    return {h: (n / p if p else None) for h, (p, n) in agg.items()}


def mismatch(opp_index, form):
    """slate_wrc_form.mismatch() with the opponent term swapped for the proxy."""
    if opp_index is None or form is None:
        return None
    return round(float(opp_index - 100)
                 + (form["era"] - BASE_ERA) * ERA_W
                 - (form["kbb"] - (LG_K_PCT - LG_BB_PCT)) * KBB_W, 1)


def build_rows(min_window_pa=MIN_WINDOW_PA, days=OFFENSE_DAYS):
    games = [g for g in _load(ALL_ML)["games"]
             if g.get("home_score") is not None
             and g.get("home_pitcher") and g.get("away_pitcher")
             and g.get("home_ml") and g.get("away_ml")]
    games.sort(key=lambda g: g["date"])
    batter_rows = load_batter_rows()
    asof = AsOf(games, _load(GAME_LOGS), batter_rows)

    lg_cache = {}
    out = []
    for g in games:
        d = g["date"]
        if d not in lg_cache:
            lg_cache[d] = league_woba_by_hand(batter_rows, d, days)
        lg = lg_cache[d]
        sides = {}
        for side in ("away", "home"):
            pitcher = g[f"{side}_pitcher"]
            hand = g.get(f"{side}_hand")
            opp_team = g["home"] if side == "away" else g["away"]
            form = asof.starter_form(pitcher, d)
            idx = (asof.offense_index(opp_team, d, hand, days, min_window_pa,
                                      lg.get(hand)) if hand else None)
            sides[side] = {"pitcher": pitcher, "m": mismatch(idx, form)}
        out.append({"game": g, "sides": sides})
    return out


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------

def profit(won, ml):
    if not won:
        return -1.0
    return ml / 100.0 if ml > 0 else 100.0 / -ml


def plays(rows, lo, hi=None):
    """One bet per game, on the widest |mismatch| side.

    m <= -T  the arm outclasses the offense  -> TAIL him (back his team)
    m >= +T  the offense outclasses the arm  -> FADE him (back the opponent)
    """
    out = []
    for r in rows:
        best, bside = None, None
        for side in ("away", "home"):
            m = r["sides"][side]["m"]
            if m is None:
                continue
            if best is None or abs(m) > abs(best):
                best, bside = m, side
        if best is None:
            continue
        a = abs(best)
        if a < lo or (hi is not None and a >= hi):
            continue
        g = r["game"]
        arm = g["away"] if bside == "away" else g["home"]
        opp = g["home"] if bside == "away" else g["away"]
        pick = opp if best > 0 else arm
        won = (g["home_score"] > g["away_score"]) if pick == g["home"] \
            else (g["away_score"] > g["home_score"])
        ml = g["home_ml"] if pick == g["home"] else g["away_ml"]
        out.append({"date": g["date"], "won": won, "ml": ml, "m": best})
    return out


def score(ps, label, width=34):
    w = sum(1 for p in ps if p["won"])
    l = len(ps) - w
    u = sum(profit(p["won"], p["ml"]) for p in ps)
    n = w + l
    if not n:
        print(f"{label:<{width}} n=0")
        return None
    pct = w / n
    se = math.sqrt(pct * (1 - pct) / n)
    print(f"{label:<{width}} {w}-{l} ({pct:5.1%})  {u:+7.2f}u  "
          f"ROI {u/n:+6.1%}  CI {pct-1.96*se:4.0%}-{pct+1.96*se:4.0%}")
    return {"n": n, "pct": pct, "roi": u / n}


def baseline_favorites(rows):
    w = l = 0
    u = 0.0
    for r in rows:
        g = r["game"]
        if g["home_ml"] < g["away_ml"]:
            won, ml = g["home_score"] > g["away_score"], g["home_ml"]
        else:
            won, ml = g["away_score"] > g["home_score"], g["away_ml"]
        u += profit(won, ml)
        w, l = (w + 1, l) if won else (w, l + 1)
    print(f"{'BASELINE back every favorite':<34} {w}-{l} ({w/(w+l):5.1%})  "
          f"{u:+7.2f}u  ROI {u/(w+l):+6.1%}")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_validate(args):
    """Score the proxy against the published mismatch on the snapshot cache."""
    if not os.path.exists(CACHE):
        raise SystemExit("no scout-backtest-cache.json to validate against")
    cached = _load(CACHE)["games"]
    real = {}
    for g in cached:
        for side in ("away", "home"):
            m = g["sides"][side].get("mismatch")
            if m is not None:
                real[(g["date"], _norm(g["sides"][side].get("pitcher")))] = m

    rows = build_rows(args.min_window_pa, args.days)
    pairs = []
    for r in rows:
        for side in ("away", "home"):
            s = r["sides"][side]
            k = (r["game"]["date"], _norm(s["pitcher"]))
            if k in real and s["m"] is not None:
                pairs.append((real[k], s["m"]))
    if len(pairs) < 10:
        raise SystemExit(f"only {len(pairs)} overlapping sides -- cannot validate")

    xs = [a for a, _ in pairs]
    ys = [b for _, b in pairs]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    num = sum((a - mx) * (b - my) for a, b in pairs)
    den = math.sqrt(sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys))
    c = num / den if den else 0.0
    mae = sum(abs(a - b) for a, b in pairs) / len(pairs)
    agree = sum(1 for a, b in pairs if (a >= 0) == (b >= 0)) / len(pairs)
    print(f"proxy vs published mismatch, n={len(pairs)} pitcher-sides")
    print(f"  correlation   {c:+.3f}")
    print(f"  mean abs err  {mae:.1f} (mismatch units)")
    print(f"  sign agrees   {agree:.1%}")
    print(f"  means         published {mx:+.1f} / proxy {my:+.1f}")
    print()
    if c < 0.5:
        print("VERDICT: proxy does NOT track the published score. Treat the")
        print("report below as untested -- the substitution failed.")
    elif c < 0.75:
        print("VERDICT: proxy is directionally similar but noisy. Any edge it")
        print("finds is an edge in the PROXY, not necessarily in the column.")
    else:
        print("VERDICT: proxy tracks the published score closely enough to test.")


def cmd_report(args):
    rows = build_rows(args.min_window_pa, args.days)
    dated = [r for r in rows if r["game"]["date"]]
    print(f"season games with both starters priced: {len(dated)}")
    scored = sum(1 for r in dated
                 if any(r["sides"][s]["m"] is not None for s in ("away", "home")))
    print(f"games with at least one mismatch computable: {scored}\n")

    baseline_favorites(dated)
    print()

    print("CUMULATIVE THRESHOLDS  (tail the dominant arm / fade the dominated)")
    for th in (20, 25, 30, 35, 40, 45, 50):
        ps = plays(dated, th)
        score(ps, f"  |mismatch| >= {th}")

    print("\nNON-OVERLAPPING BANDS  (where does the edge actually live?)")
    edges = [15, 20, 25, 30, 35, 40, 45, 50]
    for lo, hi in zip(edges, edges[1:]):
        score(plays(dated, lo, hi), f"  |m| {lo}-{hi}")
    score(plays(dated, edges[-1]), f"  |m| >= {edges[-1]}")

    print("\nWALK-FORWARD  (first half in-sample / second half out-of-sample)")
    dates = sorted({r["game"]["date"] for r in dated})
    mid = dates[len(dates) // 2]
    print(f"  split at {mid}")
    for th in (25, 30, 35, 40):
        first = [r for r in dated if r["game"]["date"] < mid]
        second = [r for r in dated if r["game"]["date"] >= mid]
        a = score(plays(first, th), f"    >= {th}  first half")
        b = score(plays(second, th), f"    >= {th}  second half")
        if a and b:
            print(f"{'':<34} drift {b['roi']-a['roi']:+.1%}")

    print("\nPLAYS PER SLATE")
    nd = len(dates)
    for th in (25, 30, 35, 40, 45):
        print(f"  threshold {th}: {len(plays(dated, th))} plays "
              f"= {len(plays(dated, th))/nd:.1f}/day over {nd} slates")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("validate", cmd_validate), ("report", cmd_report)):
        p = sub.add_parser(name)
        p.add_argument("--min-window-pa", type=int, default=MIN_WINDOW_PA)
        p.add_argument("--days", type=int, default=OFFENSE_DAYS,
                       help="trailing offense window in calendar days "
                            "(20 matches slate_wrc_form PRIMARY_WINDOW=last20)")
        p.set_defaults(fn=fn)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
