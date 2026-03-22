# scripts/_calib.py
import json
import math
from calibration import build_calibration_table

def main():
    with open("data/history.json", "r") as f:
        h = json.load(f)

    # Check sResult values
    result_vals = set()
    buckets = {}

    for r in h.get("runs", []):
        if r.get("burnIn"):
            continue
        for g in r.get("games", []):
            s_pick = g.get("sPick")
            if not s_pick or s_pick == "PASS":
                continue
            s_result = g.get("sResult")
            if s_result:
                result_vals.add(s_result)
            if s_result not in ("WIN", "LOSS"):
                continue
            p = g.get("pCover", 0) or 0
            b = math.floor(p * 20) / 20
            key = f"{b:.2f}"
            if key not in buckets:
                buckets[key] = {"w": 0, "l": 0}
            if s_result == "WIN":
                buckets[key]["w"] += 1
            else:
                buckets[key]["l"] += 1

    print("sResult values:", sorted(result_vals))
    print("\n=== NBA P(cover) Calibration ===")
    print("Bucket    | W    | L    | Actual | Expected | D")
    for k in sorted(buckets.keys(), key=lambda x: float(x)):
        v = buckets[k]
        n = v["w"] + v["l"]
        actual = f"{v['w'] / n * 100:.1f}"
        expected = f"{float(k) * 100 + 2.5:.0f}"
        delta = f"{v['w'] / n * 100 - float(k) * 100 - 2.5:.1f}"
        print(f"P={k}  | {str(v['w']):<5}| {str(v['l']):<5}| {actual:<7}| {expected:<9}| {delta}")

    # Also try calibration module
    print("\n=== NBA Calibration Module Output ===")
    table = build_calibration_table(h)
    for row in table:
        if row["spread"]["n"] == 0:
            continue
        mid = round(row["midpoint"] * 100)
        win_pct = row["spread"].get("winPct")
        delta = win_pct - mid if win_pct is not None else None
        print(f"{row['label']:<10} n={str(row['spread']['n']):<5} actual={win_pct}%  expected={mid}%  D={delta}  units={row['spread']['units']}")

if __name__ == "__main__":
    main()
