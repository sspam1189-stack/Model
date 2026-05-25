#!/usr/bin/env python3
"""Analyze MLB strikeout model performance by day of the week."""

import json
import os
from datetime import datetime
from collections import defaultdict

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

def load_props():
    data_path = os.path.join(os.path.dirname(__file__), "..", "data", "mlb-props.json")
    with open(data_path) as f:
        data = json.load(f)
    return [p for p in data["props"] if p.get("result") in ("WIN", "LOSS")]


def analyze(graded):
    day_stats = defaultdict(lambda: {"w": 0, "l": 0, "edges": [], "errors": [],
                                      "over_w": 0, "over_l": 0,
                                      "under_w": 0, "under_l": 0,
                                      "wagered": 0, "profit": 0.0})

    for p in graded:
        dt = datetime.strptime(p["date"], "%Y-%m-%d")
        day = DAY_NAMES[dt.weekday()]
        d = day_stats[day]

        d["wagered"] += 1
        edge = p.get("edge", 0) or 0
        proj = p.get("proj", 0) or 0
        actual = p.get("actual", 0) or 0
        d["edges"].append(abs(edge))
        d["errors"].append(abs(proj - actual))

        pick = p.get("pick", "")
        won = p["result"] == "WIN"

        if won:
            d["w"] += 1
        else:
            d["l"] += 1

        if pick == "OVER":
            if won: d["over_w"] += 1
            else: d["over_l"] += 1
        elif pick == "UNDER":
            if won: d["under_w"] += 1
            else: d["under_l"] += 1

        odds = p.get("odds")
        if odds is not None:
            if won:
                d["profit"] += odds / 100 if odds > 0 else 100 / abs(odds)
            else:
                d["profit"] -= 1

    return day_stats


def print_report(day_stats, graded):
    dates = [p["date"] for p in graded]
    print(f"MLB Strikeout Model — Day-of-Week Analysis")
    print(f"Date range: {min(dates)} to {max(dates)}  |  {len(graded)} graded picks\n")

    sorted_days = sorted(day_stats.items(),
                         key=lambda x: x[1]["w"] / (x[1]["w"] + x[1]["l"]))

    print(f"{'Day':<12} {'W':>5} {'L':>5} {'Total':>6} {'Win%':>7} {'ROI%':>7} "
          f"{'Over W%':>8} {'Under W%':>9} {'Avg|Err|':>9}")
    print("-" * 78)
    for day, s in sorted_days:
        total = s["w"] + s["l"]
        wr = s["w"] / total * 100
        roi = s["profit"] / s["wagered"] * 100 if s["wagered"] else 0
        ot = s["over_w"] + s["over_l"]
        ut = s["under_w"] + s["under_l"]
        over_pct = f"{s['over_w'] / ot * 100:.1f}%" if ot else "N/A"
        under_pct = f"{s['under_w'] / ut * 100:.1f}%" if ut else "N/A"
        avg_err = sum(s["errors"]) / len(s["errors"]) if s["errors"] else 0
        print(f"{day:<12} {s['w']:>5} {s['l']:>5} {total:>6} {wr:>6.1f}% {roi:>6.1f}% "
              f"{over_pct:>8} {under_pct:>9} {avg_err:>9.2f}")

    worst_day, worst = sorted_days[0]
    best_day, best = sorted_days[-1]
    worst_wr = worst["w"] / (worst["w"] + worst["l"]) * 100
    best_wr = best["w"] / (best["w"] + best["l"]) * 100

    print(f"\nWorst day: {worst_day} ({worst_wr:.1f}% win rate, "
          f"{worst['w']}W-{worst['l']}L)")
    print(f"Best day:  {best_day} ({best_wr:.1f}% win rate, "
          f"{best['w']}W-{best['l']}L)")


if __name__ == "__main__":
    graded = load_props()
    day_stats = analyze(graded)
    print_report(day_stats, graded)
