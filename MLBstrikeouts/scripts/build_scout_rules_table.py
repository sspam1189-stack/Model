#!/usr/bin/env python3
"""
build_scout_rules_table.py — season history for the scout rules that had none.

Flag Plays has flag-combo-table.json. Better arm ML has msum-ml-table.json.
The non-scout systems have allml-systems-table.json. Form under, Aligned ML
and Mismatch ML had nothing: their records existed only as prose in CLAUDE.md
and in rule_status.py's `why` text, which is exactly the "a number somebody
typed once" problem every other table here was built to avoid. Nothing on the
tab showed them, so a rule could drift for weeks without anyone noticing.

This replays all three as-of each game date and publishes the same shape the
other tables use, rebuilt by every daily run.

  Form under   m_sum <= -40 -> under
  Aligned ML   one offense hot-aligned (all four windows >= 110) against one
               cold-aligned (all <= 90) at the 75-PA floor -> back the hot
               side's team
  Mismatch ML  a starter at mismatch <= -45 -> back his team (tail); at
               >= +55 -> back the opponent (fade). SHADOW.

ONE CAVEAT, STATED IN THE OUTPUT TOO. The live rules read the published wRC+
cells from the props payload; this replay reads the wOBA index that
backtest_mismatch computes from the batter PA logs, because that is the only
one of the two that can be recomputed as-of an arbitrary past date. The two
are the same construction on the same input (plate appearances, indexed to the
league mark, centred on 100) and are not park-adjusted, but they are not
byte-identical. Treat these as a faithful replay of the rule, not as a
reconstruction of the exact cells the tab printed that morning.

Output: MLBstrikeouts/data/scout-rules-table.json (+ the dashboard copy)

Usage:  cd MLBstrikeouts && python -m scripts.build_scout_rules_table
"""
import collections
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "sources"))

import backtest_mismatch as bm
from rule_status import RULE_STATUS

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATHS = [
    os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data",
                                  "scout-rules-table.json")),
    os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "PythonDashboard",
                                  "data", "scout-rules-table.json")),
]

FORM_UNDER_AT = -40.0
MISMATCH_TAIL, MISMATCH_FADE = -45.0, 55.0
ALIGNED_MIN_PA = 75
ALIGNED_WINDOWS = (30, 20, 15, 7)      # slate_wrc_form's last30/20/15/7
ALIGNED_HOT, ALIGNED_COLD = 110.0, 90.0

RULES = {
    "form-under": ("Form under", "totals",
                   "m_sum <= -40: both arms outclassed by the bats -> under."),
    "aligned-ml": ("Aligned ML", "h2h",
                   "One offense hot-aligned across all four windows (>= 110) "
                   "against one cold-aligned (<= 90) at the 75-PA floor -> "
                   "back the hot side."),
    "mismatch-ml": ("Mismatch ML", "h2h",
                    "Starter at mismatch <= -45 -> back his team; at >= +55 "
                    "-> back the opponent."),
}
ORDER = ("form-under", "aligned-ml", "mismatch-ml")


def _profit(ml, won):
    return (ml / 100.0 if ml > 0 else 100.0 / -ml) if won else -1.0


def _cls(ladder):
    """hot / cold / mid, or None when any window is under the PA floor."""
    if any(v is None for v in ladder):
        return None
    if all(v <= ALIGNED_COLD for v in ladder):
        return "cold"
    if all(v >= ALIGNED_HOT for v in ladder):
        return "hot"
    return "mid"


def replay():
    logs = bm._load(bm.GAME_LOGS)
    games = [g for g in bm._load(bm.ALL_ML)["games"]
             if g.get("home_score") is not None and g.get("away_score") is not None
             and g.get("away_pitcher") and g.get("home_pitcher")
             and g.get("home_ml") and g.get("away_ml")
             and g.get("total_line") is not None]
    games.sort(key=lambda g: g["date"])
    batter_rows = bm.load_batter_rows()
    asof = bm.AsOf(games, logs, batter_rows)

    lg_cache, rows = {}, collections.defaultdict(list)
    for g in games:
        d = g["date"]
        if d not in lg_cache:
            lg_cache[d] = bm.league_woba_by_hand(batter_rows, d, bm.OFFENSE_DAYS)
        lg = lg_cache[d]
        home_won = g["home_score"] > g["away_score"]
        total = g["away_score"] + g["home_score"]

        # --- mismatch per side (drives form-under and mismatch-ml) ---
        ms = {}
        for side in ("away", "home"):
            hand = g.get(f"{side}_hand")
            opp = g["home"] if side == "away" else g["away"]
            form = asof.starter_form(g[f"{side}_pitcher"], d)
            idx = (asof.offense_index(opp, d, hand, bm.OFFENSE_DAYS,
                                      bm.MIN_WINDOW_PA, lg.get(hand))
                   if hand else None)
            ms[side] = (None if (form is None or idx is None)
                        else ((idx - 100.0) + (form["era"] - 4.20) * 8.0
                              - (form["kbb"] - 13.7) * 1.2))

        if ms["away"] is not None and ms["home"] is not None:
            msum = ms["away"] + ms["home"]
            if msum <= FORM_UNDER_AT and g.get("under_ml") and total != g["total_line"]:
                won = total < g["total_line"]
                rows["form-under"].append({
                    "date": d, "won": won,
                    "p": _profit(g["under_ml"], won),
                    "pick": f"U{g['total_line']:g}", "price": g["under_ml"]})

        for side in ("away", "home"):
            m = ms[side]
            if m is None:
                continue
            if m <= MISMATCH_TAIL:
                pick = g[side]
            elif m >= MISMATCH_FADE:
                pick = g["home"] if side == "away" else g["away"]
            else:
                continue
            ml = g["home_ml"] if pick == g["home"] else g["away_ml"]
            won = home_won == (pick == g["home"])
            rows["mismatch-ml"].append({"date": d, "won": won,
                                        "p": _profit(ml, won),
                                        "pick": pick, "price": ml})

        # --- aligned ML: each offense's ladder vs the hand it faces ---
        away_lad, home_lad = [], []
        for days in ALIGNED_WINDOWS:
            away_lad.append(asof.offense_index(
                g["away"], d, g.get("home_hand"), days, ALIGNED_MIN_PA,
                lg.get(g.get("home_hand"))) if g.get("home_hand") else None)
            home_lad.append(asof.offense_index(
                g["home"], d, g.get("away_hand"), days, ALIGNED_MIN_PA,
                lg.get(g.get("away_hand"))) if g.get("away_hand") else None)
        a_cls, h_cls = _cls(away_lad), _cls(home_lad)
        if {a_cls, h_cls} == {"hot", "cold"}:
            side = "away" if a_cls == "hot" else "home"
            ml = g[f"{side}_ml"]
            won = home_won == (side == "home")
            rows["aligned-ml"].append({"date": d, "won": won,
                                       "p": _profit(ml, won),
                                       "pick": g[side], "price": ml})
    return rows, games


def summarize(rs):
    if not rs:
        return {"w": 0, "l": 0, "n": 0, "units": 0.0, "roi": 0.0}
    u = sum(r["p"] for r in rs)
    w = sum(1 for r in rs if r["won"])
    return {"w": w, "l": len(rs) - w, "n": len(rs),
            "units": round(u, 2), "roi": round(u / len(rs) * 100, 1)}


def baselines(games):
    side, under = [], []
    for g in games:
        hw = g["home_score"] > g["away_score"]
        side += [_profit(g["home_ml"], hw), _profit(g["away_ml"], not hw)]
        tot = g["away_score"] + g["home_score"]
        if tot != g["total_line"] and g.get("under_ml"):
            under.append(_profit(g["under_ml"], tot < g["total_line"]))
    pct = lambda v: round(sum(v) / len(v) * 100, 1) if v else None
    return {"side": pct(side), "under": pct(under)}


def main():
    rows, games = replay()
    dates = [g["date"] for g in games]
    mid = dates[len(dates) // 2] if dates else None
    base = baselines(games)

    out_rules = []
    for key in ORDER:
        name, market, rule = RULES[key]
        rs = rows.get(key, [])
        by = collections.defaultdict(list)
        for r in rs:
            by[r["date"][:7]].append(r)
        a = [r for r in rs if mid and r["date"] < mid]
        b = [r for r in rs if mid and r["date"] >= mid]
        out_rules.append({
            "key": key, "name": name, "market": market, "rule": rule,
            "status": RULE_STATUS.get(key, "shadow"),
            "baseline_key": "side" if market == "h2h" else "under",
            "baseline": base["side"] if market == "h2h" else base["under"],
            "record": summarize(rs),
            "halves": [summarize(a)["roi"], summarize(b)["roi"]],
            "monthly": [{"month": m, **summarize(v)} for m, v in sorted(by.items())],
            "per_day": round(len(rs) / len(set(dates)), 2) if dates else 0,
        })

    blob = {
        "sport": "MLB",
        "type": "scout-rules-table",
        "generated": datetime.datetime.now(datetime.timezone.utc)
                             .isoformat(timespec="seconds").replace("+00:00", "Z"),
        "span": {"from": dates[0], "to": dates[-1]} if dates else {},
        "games": len(games),
        "baselines": base,
        "note": ("Season history for the scout rules that had no table: Form "
                 "under, Aligned ML and Mismatch ML. Replayed as-of each game "
                 "date -- a game's own result is never in the features that "
                 "select it. CAVEAT: the live rules read published wRC+ cells; "
                 "this replay uses the wOBA index computed from the batter PA "
                 "logs, the only version recomputable for a past date. Same "
                 "construction, same input, not byte-identical -- a faithful "
                 "replay of the rule, not of the exact cells the tab printed."),
        "rules": out_rules,
    }
    for path in OUTPUT_PATHS:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(blob, fh, indent=1)
    for r in out_rules:
        rec = r["record"]
        print(f"  {r['name']:14} {rec['w']:>3}-{rec['l']:<3} {rec['roi']:>+7.1f}% "
              f"{rec['units']:>+8.2f}u  n={rec['n']:<4} base {r['baseline']:+.1f} "
              f"halves {r['halves'][0]:+.0f}/{r['halves'][1]:+.0f}")
    print(f"scout rules table: {len(out_rules)} rules over {len(games)} games "
          f"-> {len(OUTPUT_PATHS)} paths")


if __name__ == "__main__":
    main()
