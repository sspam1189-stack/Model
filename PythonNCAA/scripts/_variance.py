#!/usr/bin/env python3
# scripts/_variance.py — Analyze model variance and calibration
import json
import math
from defaults import BAYES_HYPER

h = json.loads(open("data/history.json").read())
runs = h.get("runs") or []
errors = []

for r in runs:
    if r.get("burnIn"): continue
    for g in (r.get("games") or []):
        if not isinstance(g.get("homeScore"), (int, float)) or not isinstance(g.get("awayScore"), (int, float)):
            continue
        if not isinstance(g.get("margin"), (int, float)) or not isinstance(g.get("line"), (int, float)):
            continue
        actual_edge = (g["homeScore"] - g["awayScore"]) - g["line"]
        proj_edge = g["margin"]
        errors.append(actual_edge - proj_edge)

mean = sum(errors) / len(errors) if errors else 0
variance = sum((x - mean) ** 2 for x in errors) / len(errors) if errors else 0
print(f"residualVar from data: {variance:.1f} (std={variance ** 0.5:.1f}) from {len(errors)} games")
print(f"BAYES_HYPER.residualVar default: {BAYES_HYPER.get('residualVar')}")
print(f"BAYES_HYPER.marginNoise default: {BAYES_HYPER.get('marginNoise')}")
print(f"\nActual marginStd used: ~{variance ** 0.5:.1f} pts")
print(f"If using default: ~{BAYES_HYPER.get('residualVar', 100) ** 0.5:.1f} pts")

by_p = {}
for r in runs:
    if r.get("burnIn"): continue
    for g in (r.get("games") or []):
        if not g.get("sPick") or g["sPick"] == "PASS": continue
        if not g.get("pCover"): continue
        bucket = math.floor(g["pCover"] * 20) / 20
        key = f"{bucket:.2f}"
        if key not in by_p:
            by_p[key] = {"margins": [], "results": []}
        by_p[key]["margins"].append(g.get("margin", 0))
        by_p[key]["results"].append(1 if g.get("sResult") == "WIN" else 0)

print("\n=== P(cover) bucket analysis ===")
for k in sorted(by_p.keys()):
    v = by_p[k]
    avg_margin = sum(abs(x) for x in v["margins"]) / len(v["margins"])
    hit_rate = sum(v["results"]) / len(v["results"])
    print(f"P={k}: n={len(v['margins'])}  avgAbsMargin={avg_margin:.1f}  actualHitRate={hit_rate * 100:.1f}%  expectedP={float(k) * 100 + 2.5:.0f}%")
