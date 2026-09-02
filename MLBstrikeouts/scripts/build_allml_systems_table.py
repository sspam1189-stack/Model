#!/usr/bin/env python3
"""
build_allml_systems_table.py — the non-scout systems' season record, daily.

Same job build_flag_combo_table.py does for the flag combos and
build_msum_ml_table.py does for the better-arm rule: replay every system over
the full settled season as-of each game date, write the record the dashboard
renders, and rebuild it every run so the table on the tab is never a number
somebody typed once.

Definitions live in scripts/allml_systems.py, which the daily logger imports
too, so the table, the ledger and the tab cannot disagree about what a rule is.

Output: MLBstrikeouts/data/allml-systems-table.json (+ the dashboard copy)

Usage:  cd MLBstrikeouts && python -m scripts.build_allml_systems_table
"""
import collections
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import allml_systems as SYS
from rule_status import RULE_STATUS

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATHS = [
    os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data",
                                  "allml-systems-table.json")),
    os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "PythonDashboard",
                                  "data", "allml-systems-table.json")),
]


# Which blind baseline a system is measured against. Side bets go against
# backing every side at market price; totals against blind over / blind under.
BASELINE_KEY = {"h2h": "side", "totals": None}


def blind_baselines(settled):
    """The four do-nothing benchmarks, from the same games the systems see."""
    side, over, under = [], [], []
    for g in settled:
        hw = g["home_score"] > g["away_score"]
        side.append(SYS._profit(g["home_ml"], hw))
        side.append(SYS._profit(g["away_ml"], not hw))
        tot = g["away_score"] + g["home_score"]
        if tot == g["total_line"]:
            continue
        over.append(SYS._profit(g["over_ml"], tot > g["total_line"]))
        under.append(SYS._profit(g["under_ml"], tot < g["total_line"]))
    pct = lambda v: round(sum(v) / len(v) * 100, 1) if v else None
    return {"side": pct(side), "over": pct(over), "under": pct(under),
            "parlay": 0.0}


def split(rows, at):
    return [r for r in rows if r["date"] < at], [r for r in rows if r["date"] >= at]


def main():
    blob = SYS.load()
    rows = SYS.replay(blob)
    settled = SYS._settled(blob)
    dates = [g["date"] for g in settled]
    mid = dates[len(dates) // 2] if dates else None

    base = blind_baselines(settled)
    systems = []
    for key in SYS.CARD_ORDER:
        name, market, rule, case = SYS.SYSTEMS[key]
        rs = rows.get(key, [])
        by = collections.defaultdict(list)
        for r in rs:
            by[r["date"][:7]].append(r["p"])
        a, b = split(rs, mid) if mid else ([], [])
        if market == "parlay":
            # No blind baseline exists for a two-leg parlay; the honest
            # benchmark is break-even, so it is measured against zero.
            bkey = "parlay"
        elif market == "h2h":
            bkey = "side"
        else:
            bkey = "over" if any(r["pick"] == "over" for r in rs) else "under"
        systems.append({
            "baseline_key": bkey,
            "baseline": base.get(bkey),
            "key": key,
            "name": name,
            "market": market,
            "rule": rule,
            "plain": SYS.PLAIN.get(key, ""),
            "case": case,
            "status": RULE_STATUS.get(key, "shadow"),
            "ladder_fails": case.startswith("LADDER FAILS"),
            "record": SYS.summarize(rs),
            "halves": [SYS.summarize(a)["roi"], SYS.summarize(b)["roi"]],
            "monthly": [{"month": m, **SYS.summarize(
                [r for r in rs if r["date"][:7] == m])}
                for m in sorted(by)],
            "per_day": round(len(rs) / len(set(dates)), 2) if dates else 0,
        })

    today = SYS.today_plays(blob)
    blob_out = {
        "sport": "MLB",
        "type": "allml-systems-table",
        "generated": datetime.datetime.now(datetime.timezone.utc)
                             .isoformat(timespec="seconds").replace("+00:00", "Z"),
        "span": {"from": dates[0], "to": dates[-1]} if dates else {},
        "games": len(settled),
        "note": ("Systems derived from mlb-all-ml.json alone -- moneylines, "
                 "totals, probables, scores -- with no input from the mismatch "
                 "model. Replayed as-of each game date: a game's own result is "
                 "never in the features that select it. Each system carries the "
                 "blind baseline it is measured against: backing every side "
                 "at market price for moneylines, blind over or blind under "
                 "for totals."),
        "baselines": base,
        "systems": systems,
        "today": today,
        "today_date": (blob.get("today") or [{}])[0].get("date"),
    }

    for path in OUTPUT_PATHS:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(blob_out, fh, indent=1)
    tot = sum(s["record"]["units"] for s in systems)
    print(f"allml systems table: {len(systems)} systems, "
          f"{sum(s['record']['n'] for s in systems)} graded plays, "
          f"{tot:+.1f}u season, {len(today)} tonight -> "
          f"{len(OUTPUT_PATHS)} paths")


if __name__ == "__main__":
    main()
