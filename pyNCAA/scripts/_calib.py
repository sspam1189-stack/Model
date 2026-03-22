#!/usr/bin/env python3
# scripts/_calib.py — Calibration check
import json
import math
from calibration import build_calibration_table

h = json.loads(open("data/history.json").read())
runs = h.get("runs") or []

result_vals = set()
buckets = {}

for r in runs:
    for g in (r.get("games") or []):
        if not g.get("sPick") or g["sPick"] == "PASS" or g.get("sResult") is None:
            continue
        result_vals.add(g["sResult"])
        p = g.get("pCover", 0)
        b = math.floor(p * 20) / 20
        key = f"{b:.2f}"
        if key not in buckets:
            buckets[key] = {"w": 0, "l": 0, "total": 0}
        if g["sResult"] in ("WIN", "W"):
            buckets[key]["w"] += 1
        elif g["sResult"] in ("LOSS", "L"):
            buckets[key]["l"] += 1
        buckets[key]["total"] += 1

print("sResult values found:", list(result_vals))
print("\npCover bucket | W    | L    | Total | ActualRate | Expected")
sorted_b = sorted(buckets.items(), key=lambda x: float(x[0]))
for k, v in sorted_b:
    n = v["w"] + v["l"]
    rate = f"{v['w'] / n * 100:.1f}" if n > 0 else "N/A"
    expected = f"{float(k) * 100 + 2.5:.0f}"
    delta = f"{v['w'] / n * 100 - float(k) * 100 - 2.5:.1f}" if n > 0 else "N/A"
    print(f"{k:<14}| {str(v['w']):<5}| {str(v['l']):<5}| {str(v['total']):<6}| {rate}%".ljust(55) + f"| ~{expected}%  D={delta}")

print("\n=== Calibration module output ===")
table = build_calibration_table(h)
if table:
    for r in table:
        mid = round(r["midpoint"] * 100)
        s_delta = r["spread"]["winPct"] - mid if r["spread"].get("winPct") is not None else None
        print(f"{r['label']:<10} Spread: n={str(r['spread']['n']):<5} actual={r['spread']['winPct']}%  expected={mid}%  D={s_delta}  units={r['spread']['units']}")
