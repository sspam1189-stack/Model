#!/usr/bin/env python3
# scripts/_optvar.py — Find optimal residualVar via calibration sweep
import json
import math

def normal_cdf(x):
    a1, a2, a3 = 0.254829592, -0.284496736, 1.421413741
    a4, a5, p = -1.453152027, 1.061405429, 0.3275911
    sign = -1 if x < 0 else 1
    z = abs(x) / math.sqrt(2)
    t = 1.0 / (1.0 + p * z)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-z * z)
    return 0.5 * (1.0 + sign * y)

def normal_inv_cdf(p):
    if p <= 0: return float("-inf")
    if p >= 1: return float("inf")
    x = 0.0
    for _ in range(50):
        err = normal_cdf(x) - p
        pdf = math.exp(-x * x / 2) / math.sqrt(2 * math.pi)
        x -= err / pdf
    return x

h = json.loads(open("data/history.json").read())
runs = h.get("runs") or []

games = []
for r in runs:
    if r.get("burnIn"):
        continue
    for g in (r.get("games") or []):
        if not g.get("sPick") or g["sPick"] == "PASS":
            continue
        if g.get("sResult") not in ("WIN", "LOSS"):
            continue
        if not isinstance(g.get("margin"), (int, float)):
            continue
        games.append({
            "margin": abs(g["margin"]),
            "win": 1 if g["sResult"] == "WIN" else 0,
            "pCover": g.get("pCover", 0),
        })

print(f"Total graded picks: {len(games)}")
print(f"Overall record: {sum(g['win'] for g in games)}-{sum(1 for g in games if not g['win'])} ({sum(g['win'] for g in games) / len(games) * 100:.1f}%)")

print("\n=== Testing variance scaling factors ===")
print("Factor | AvgCalibErr | Brier | 57-60% | 60-63% | 63-66% | 66-69% | 69-72%")

for factor_10 in range(10, 31):
    factor = factor_10 / 10
    buckets = {}
    brier_sum = 0
    for g in games:
        orig_z = normal_inv_cdf(g["pCover"]) if g["pCover"] > 0 and g["pCover"] < 1 else 0
        new_p = normal_cdf(orig_z / math.sqrt(factor))
        bucket = math.floor(new_p * 20) / 20
        key = f"{bucket:.2f}"
        if key not in buckets:
            buckets[key] = {"w": 0, "l": 0}
        if g["win"]:
            buckets[key]["w"] += 1
        else:
            buckets[key]["l"] += 1
        brier_sum += (new_p - g["win"]) ** 2

    calib_err = 0
    calib_n = 0
    bucket_results = {}
    for k, v in buckets.items():
        n = v["w"] + v["l"]
        if n < 10:
            continue
        actual = v["w"] / n
        expected = float(k) + 0.025
        calib_err += (actual - expected) ** 2 * n
        calib_n += n
        bucket_results[k] = f"{actual * 100:.0f}%"

    avg_calib_err = math.sqrt(calib_err / max(calib_n, 1))
    brier = brier_sum / len(games)

    print(f"{factor:.1f}".ljust(7) + f"| {avg_calib_err:.4f}".ljust(13) + f"| {brier:.4f}".ljust(7)
          + f"| {bucket_results.get('0.55', '-'):<7}"
          + f"| {bucket_results.get('0.60', '-'):<7}"
          + f"| {bucket_results.get('0.65', '-'):<7}"
          + f"| {bucket_results.get('0.55', '-'):<7}"
          + f"| {bucket_results.get('0.60', '-'):<7}")
