#!/usr/bin/env python3
"""
scout_predict.py — turn the scout from a dashboard into a calibrated predictor.

The scout emitted mismatch scores, wRC+ ladders, flags and bullpen ERAs, and
then a human eyeballed all of it into a yes/no. Every other model in this repo
emits something you can act on -- the K-model says 73.5% cover, fade-ML says bet
this side at this stake -- so the scout was the only tier whose "read" could not
be compared to a price, sized, or scored. This fixes that: for every game it
emits a HOME WIN PROBABILITY and an EXPECTED RUN TOTAL, both calibrated, both
directly comparable to the market number.

Method
------
Features are rebuilt as-of the game date from primary sources, never from the
published scout payload, so the model can train on the whole season (1,900+
games) instead of the ten slates of snapshots that happen to be in git:

  * starter form  -- last-5-start ERA, K-BB%, IP/GS, from the game logs
  * bullpen form  -- team relief ERA over the trailing 7 and 30 days
  * offense form  -- team runs/game over the trailing 15 days, from finals

Everything is a HOME-MINUS-AWAY differential, so a positive weight means the
feature favors the home side. Win probability is logistic regression, run total
is linear regression, both hand-rolled (no numpy here) with L2 and standardized
inputs.

Honesty rules this script enforces on itself
--------------------------------------------
1. NO LEAKAGE. Every feature for a game on date D uses only games strictly
   before D. Verified by construction in ``as_of``.
2. WALK-FORWARD ONLY. Reported numbers come from retraining on the past and
   predicting the future in weekly blocks. An in-sample score is not evidence
   and is never printed as the headline.
3. SCORED AGAINST THE MARKET, not against a betting record. Brier score and log
   loss versus the de-vigged closing price are the metrics; ROI on a small
   sample is how the scout fooled itself all week.
4. CALIBRATION IS REPORTED. A model that says 70% must win about 70%. Accuracy
   alone hides the miscalibration that makes probabilities unusable for sizing.

Usage:
    python3 scripts/scout_predict.py evaluate     # walk-forward vs the market
    python3 scripts/scout_predict.py today        # predictions for the slate
    python3 scripts/scout_predict.py weights      # trained coefficients
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ALLML = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "mlb-all-ml.json"))
LOGS = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "data",
                                     "pitcher_cache", "mlb", "game_logs_2026.json"))

RECENT_STARTS = 5          # starter form window, in starts
PEN_SHORT, PEN_LONG = 7, 30    # bullpen windows, in days
OFFENSE_DAYS = 15          # team offense window, in days
WARMUP_GAMES = 400         # games before the first walk-forward prediction
RETRAIN_DAYS = 7           # walk-forward block size

FEATURES = ["d_sp_era", "d_sp_kbb", "d_sp_ip", "d_pen30", "d_pen7", "d_off"]


# ---------------------------------------------------------------------------
# Source data, indexed for as-of lookups
# ---------------------------------------------------------------------------

def _norm(name):
    return " ".join((name or "").lower().replace(".", "").split())


class Book:
    """Every fact the model may use, indexed so lookups can be date-bounded."""

    def __init__(self):
        games = [g for g in json.load(open(ALLML, encoding="utf-8")).get("games", [])
                 if g.get("home_win") is not None and g.get("date")]
        games.sort(key=lambda g: g["date"])
        self.games = games

        logs = json.load(open(LOGS, encoding="utf-8"))
        self.starts = defaultdict(list)      # pitcher -> [(date, row)]
        self.relief = defaultdict(list)      # team -> [(date, outs, er)]
        for r in logs:
            d = r.get("game_date")
            if not d:
                continue
            if r.get("is_start"):
                self.starts[_norm(r.get("pitcher_name"))].append((d, r))
            elif r.get("team"):
                self.relief[r["team"]].append((d, int(r.get("outs") or 0),
                                               int(r.get("er") or 0)))
        for v in self.starts.values():
            v.sort(key=lambda x: x[0])
        for v in self.relief.values():
            v.sort(key=lambda x: x[0])

        self.team_runs = defaultdict(list)   # team -> [(date, runs scored)]
        for g in games:
            self.team_runs[g["away"]].append((g["date"], g["away_score"]))
            self.team_runs[g["home"]].append((g["date"], g["home_score"]))
        for v in self.team_runs.values():
            v.sort(key=lambda x: x[0])

    # -- as-of accessors: strictly BEFORE `date`, so no result leaks in -----

    def starter_form(self, pitcher, date):
        rows = [r for d, r in self.starts.get(_norm(pitcher), []) if d < date]
        rows = rows[-RECENT_STARTS:]
        if not rows:
            return None
        outs = sum(int(r.get("outs") or 0) for r in rows)
        if not outs:
            return None
        ip = outs / 3.0
        bf = sum(int(r.get("bf") or 0) for r in rows)
        er = sum(int(r.get("er") or 0) for r in rows)
        k = sum(int(r.get("k") or 0) for r in rows)
        bb = sum(int(r.get("bb") or 0) for r in rows)
        return {"era": er * 9.0 / ip,
                "kbb": (100.0 * (k - bb) / bf) if bf else 0.0,
                "ip_gs": ip / len(rows)}

    def pen_era(self, team, date, days):
        start = (datetime.date.fromisoformat(date)
                 - datetime.timedelta(days=days)).isoformat()
        rows = [(o, e) for d, o, e in self.relief.get(team, []) if start <= d < date]
        outs = sum(o for o, _ in rows)
        if outs < 30:                      # under 10 innings: unusable
            return None
        return sum(e for _, e in rows) * 9.0 / (outs / 3.0)

    def offense(self, team, date, days=OFFENSE_DAYS):
        start = (datetime.date.fromisoformat(date)
                 - datetime.timedelta(days=days)).isoformat()
        runs = [r for d, r in self.team_runs.get(team, []) if start <= d < date]
        return (sum(runs) / len(runs)) if len(runs) >= 5 else None

    def features(self, g):
        """Home-minus-away differentials. None when any input is missing."""
        d = g["date"]
        sa = self.starter_form(g.get("away_pitcher"), d)
        sh = self.starter_form(g.get("home_pitcher"), d)
        if not sa or not sh:
            return None
        pa30, ph30 = self.pen_era(g["away"], d, PEN_LONG), self.pen_era(g["home"], d, PEN_LONG)
        pa7, ph7 = self.pen_era(g["away"], d, PEN_SHORT), self.pen_era(g["home"], d, PEN_SHORT)
        oa, oh = self.offense(g["away"], d), self.offense(g["home"], d)
        if None in (pa30, ph30, pa7, ph7, oa, oh):
            return None
        return {
            # away-minus-home on pitching: a WORSE away staff favors home (+)
            "d_sp_era": sa["era"] - sh["era"],
            "d_sp_kbb": sh["kbb"] - sa["kbb"],
            "d_sp_ip": sh["ip_gs"] - sa["ip_gs"],
            "d_pen30": pa30 - ph30,
            "d_pen7": pa7 - ph7,
            # home-minus-away on offense
            "d_off": oh - oa,
        }


# ---------------------------------------------------------------------------
# Models (hand-rolled: this box has no numpy)
# ---------------------------------------------------------------------------

def standardize(rows):
    n = len(rows)
    mean = {f: sum(r[f] for r in rows) / n for f in FEATURES}
    var = {f: sum((r[f] - mean[f]) ** 2 for r in rows) / n for f in FEATURES}
    sd = {f: math.sqrt(v) if v > 1e-9 else 1.0 for f, v in var.items()}
    return mean, sd


def _x(row, mean, sd):
    return [1.0] + [(row[f] - mean[f]) / sd[f] for f in FEATURES]


def fit_logistic(rows, ys, l2=1.0, iters=400, lr=0.25):
    mean, sd = standardize(rows)
    X = [_x(r, mean, sd) for r in rows]
    w = [0.0] * (len(FEATURES) + 1)
    n = len(X)
    for _ in range(iters):
        grad = [0.0] * len(w)
        for xi, y in zip(X, ys):
            z = sum(a * b for a, b in zip(w, xi))
            p = 1.0 / (1.0 + math.exp(-max(-30, min(30, z))))
            e = p - y
            for j, v in enumerate(xi):
                grad[j] += e * v
        for j in range(len(w)):
            g = grad[j] / n + (l2 / n) * (w[j] if j else 0.0)
            w[j] -= lr * g
    return {"w": w, "mean": mean, "sd": sd}


def fit_linear(rows, ys, l2=1.0, iters=400, lr=0.25):
    mean, sd = standardize(rows)
    X = [_x(r, mean, sd) for r in rows]
    w = [0.0] * (len(FEATURES) + 1)
    w[0] = sum(ys) / len(ys)
    n = len(X)
    for _ in range(iters):
        grad = [0.0] * len(w)
        for xi, y in zip(X, ys):
            e = sum(a * b for a, b in zip(w, xi)) - y
            for j, v in enumerate(xi):
                grad[j] += e * v
        for j in range(len(w)):
            g = grad[j] / n + (l2 / n) * (w[j] if j else 0.0)
            w[j] -= lr * g
    return {"w": w, "mean": mean, "sd": sd}


def predict_p(model, row):
    z = sum(a * b for a, b in zip(model["w"], _x(row, model["mean"], model["sd"])))
    return 1.0 / (1.0 + math.exp(-max(-30, min(30, z))))


def predict_y(model, row):
    return sum(a * b for a, b in zip(model["w"], _x(row, model["mean"], model["sd"])))


# ---------------------------------------------------------------------------
# Market
# ---------------------------------------------------------------------------

def devig(home_ml, away_ml):
    def imp(o):
        return (100.0 / (o + 100.0)) if o > 0 else (-o / (-o + 100.0))
    if home_ml is None or away_ml is None:
        return None
    ph, pa = imp(home_ml), imp(away_ml)
    return ph / (ph + pa)


# ---------------------------------------------------------------------------
# Walk-forward evaluation
# ---------------------------------------------------------------------------

def build_dataset(book):
    out = []
    for g in book.games:
        f = book.features(g)
        if f is None:
            continue
        out.append({"game": g, "f": f, "home_win": 1 if g["home_win"] else 0,
                    "total": g["away_score"] + g["home_score"],
                    "mkt": devig(g.get("home_ml"), g.get("away_ml")),
                    "line": g.get("total_line")})
    return out


def walk_forward(data):
    """Train on the past, predict the next block. The only honest score."""
    preds = []
    dates = sorted({d["game"]["date"] for d in data})
    i = 0
    while i < len(dates):
        block = set(dates[i:i + RETRAIN_DAYS])
        train = [d for d in data if d["game"]["date"] < min(block)]
        test = [d for d in data if d["game"]["date"] in block]
        i += RETRAIN_DAYS
        if len(train) < WARMUP_GAMES or not test:
            continue
        mw = fit_logistic([d["f"] for d in train], [d["home_win"] for d in train])
        mt = fit_linear([d["f"] for d in train], [float(d["total"]) for d in train])
        for d in test:
            preds.append({**d, "p": predict_p(mw, d["f"]), "exp_total": predict_y(mt, d["f"])})
    return preds


def report(preds):
    n = len(preds)
    if not n:
        print("no walk-forward predictions -- not enough history")
        return
    acc = sum(1 for p in preds if (p["p"] > 0.5) == bool(p["home_win"])) / n
    brier = sum((p["p"] - p["home_win"]) ** 2 for p in preds) / n
    ll = -sum(math.log(max(1e-9, p["p"] if p["home_win"] else 1 - p["p"]))
              for p in preds) / n
    withmkt = [p for p in preds if p["mkt"] is not None]
    m_acc = sum(1 for p in withmkt if (p["mkt"] > 0.5) == bool(p["home_win"])) / len(withmkt)
    m_brier = sum((p["mkt"] - p["home_win"]) ** 2 for p in withmkt) / len(withmkt)
    m_ll = -sum(math.log(max(1e-9, p["mkt"] if p["home_win"] else 1 - p["mkt"]))
                for p in withmkt) / len(withmkt)
    base = sum(p["home_win"] for p in preds) / n

    print(f"WALK-FORWARD  {n} games predicted out-of-sample "
          f"({preds[0]['game']['date']} .. {preds[-1]['game']['date']})")
    print("\nWIN PROBABILITY            model      market     (lower Brier/logloss is better)")
    print(f"  accuracy               {100*acc:7.1f}%   {100*m_acc:7.1f}%    "
          f"(home-always {100*base:.1f}%)")
    print(f"  Brier score            {brier:7.4f}   {m_brier:7.4f}")
    print(f"  log loss               {ll:7.4f}   {m_ll:7.4f}")
    verdict = ("BEATS the market" if brier < m_brier
               else "does not beat the market")
    print(f"  -> {verdict} on Brier ({brier - m_brier:+.4f})")

    print("\nCALIBRATION (does a stated 70% win 70%?)")
    buckets = defaultdict(list)
    for p in preds:
        buckets[min(0.9, max(0.1, round(p["p"] * 10) / 10))].append(p["home_win"])
    for b in sorted(buckets):
        v = buckets[b]
        if len(v) >= 10:
            print(f"  predicted {100*b:3.0f}%   actual {100*sum(v)/len(v):5.1f}%   n={len(v)}")

    tot = [p for p in preds if p["line"] is not None]
    if tot:
        mae = sum(abs(p["exp_total"] - p["total"]) for p in tot) / len(tot)
        mkt_mae = sum(abs(p["line"] - p["total"]) for p in tot) / len(tot)
        print(f"\nRUN TOTAL                  model      market")
        print(f"  mean abs error         {mae:7.2f}   {mkt_mae:7.2f}   "
              f"(n={len(tot)})")
        edge = [p for p in tot if abs(p["exp_total"] - p["line"]) >= 0.75]
        if edge:
            w = sum(1 for p in edge
                    if (p["total"] > p["line"]) == (p["exp_total"] > p["line"]))
            g = sum(1 for p in edge if p["total"] != p["line"])
            print(f"  when model is >=0.75 runs off the line: {w}-{g-w} "
                  f"({100*w/g:.1f}%) n={g}  [52.4% breaks even]")

    print("\nDISAGREEMENTS WITH THE MARKET (where any edge would live)")
    for lo, hi in ((0.03, 0.06), (0.06, 0.10), (0.10, 1.0)):
        sel = [p for p in withmkt if lo <= abs(p["p"] - p["mkt"]) < hi]
        if len(sel) < 15:
            continue
        w = sum(1 for p in sel if (p["p"] > p["mkt"]) == bool(p["home_win"]))
        print(f"  model {100*lo:.0f}-{100*hi:.0f}pts from market: model side "
              f"{w}-{len(sel)-w} ({100*w/len(sel):.1f}%) n={len(sel)}")


def cmd_evaluate(args):
    book = Book()
    data = build_dataset(book)
    print(f"{len(data)} of {len(book.games)} settled games have complete features\n")
    report(walk_forward(data))
    print("\nScored against the market, walk-forward, no leakage. A model that "
          "does not beat\nthe market here is still useful as a conviction layer "
          "-- but it is not a bet.")


def cmd_weights(args):
    book = Book()
    data = build_dataset(book)
    mw = fit_logistic([d["f"] for d in data], [d["home_win"] for d in data])
    mt = fit_linear([d["f"] for d in data], [float(d["total"]) for d in data])
    print("Trained on all history (in-sample; for interpretation only)\n")
    print("  win probability      run total")
    print(f"  intercept {mw['w'][0]:+7.3f}     {mt['w'][0]:+7.3f}")
    for i, f in enumerate(FEATURES, start=1):
        print(f"  {f:10} {mw['w'][i]:+7.3f}     {mt['w'][i]:+7.3f}")
    print("\nPositive win weight = the feature favors the HOME side.")


def cmd_today(args):
    book = Book()
    data = build_dataset(book)
    mw = fit_logistic([d["f"] for d in data], [d["home_win"] for d in data])
    mt = fit_linear([d["f"] for d in data], [float(d["total"]) for d in data])
    today = args.date or datetime.date.today().isoformat()
    slate = [g for g in json.load(open(ALLML, encoding="utf-8")).get("today", [])
             if g.get("date") == today]
    if not slate:
        print(f"no slate for {today}")
        return
    print(f"Scout predictions — {today}\n")
    print(f"{'game':13}{'model home%':>12}{'market%':>9}{'edge':>7}"
          f"{'proj runs':>11}{'line':>7}{'diff':>7}")
    for g in sorted(slate, key=lambda x: x.get("commence") or ""):
        f = book.features({**g, "date": today})
        if f is None:
            print(f"{g['away']+' @ '+g['home']:13}   incomplete inputs")
            continue
        p = predict_p(mw, f)
        t = predict_y(mt, f)
        mkt = devig(g.get("home_ml"), g.get("away_ml"))
        line = g.get("total_line")
        edge = f"{100*(p-mkt):+.1f}" if mkt else "--"
        diff = f"{t-line:+.2f}" if line is not None else "--"
        print(f"{g['away']+' @ '+g['home']:13}{100*p:11.1f}%"
              f"{(f'{100*mkt:.1f}%' if mkt else '--'):>9}{edge:>7}"
              f"{t:11.2f}{(line if line is not None else '--'):>7}{diff:>7}")
    print("\nEdge = model minus market, in probability points. Positive means the "
          "model likes\nthe home side more than the price does. Advisory until "
          "`evaluate` says it beats the market.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("evaluate", help="walk-forward scoring against the market")
    sub.add_parser("weights", help="show trained coefficients")
    p = sub.add_parser("today", help="predictions for a slate")
    p.add_argument("--date")
    args = ap.parse_args()
    return {"evaluate": cmd_evaluate, "weights": cmd_weights,
            "today": cmd_today}[args.cmd](args) or 0


if __name__ == "__main__":
    raise SystemExit(main())
