#!/usr/bin/env python3
# scripts/_test_pcover.py — Test P(cover) thresholds
import json
import re

h = json.loads(open("data/history.json").read())
thresholds = [0.57, 0.58, 0.59, 0.60, 0.61, 0.62, 0.63, 0.64, 0.65, 0.67, 0.70]

print("=== NCAA: Testing P(cover) thresholds ===")
print("Thresh | W    | L    | Win%  | Units   | Picks/day | Fav W% | Dog W%")
print("-------|------|------|-------|---------|-----------|--------|-------")

for t in thresholds:
    w, l = 0, 0
    fav_w, fav_l, dog_w, dog_l = 0, 0, 0, 0
    for r in (h.get("runs") or []):
        if r.get("burnIn"): continue
        for g in (r.get("games") or []):
            if not g.get("sPick") or g["sPick"] == "PASS": continue
            if not g.get("sResult") or not g.get("pCover"): continue
            if g["pCover"] < t: continue
            m = re.search(r"([+-])(\d+(?:\.\d+)?)$", g["sPick"])
            is_fav = m and m.group(1) == "-"
            if g["sResult"] == "WIN":
                w += 1
                if is_fav: fav_w += 1
                else: dog_w += 1
            elif g["sResult"] == "LOSS":
                l += 1
                if is_fav: fav_l += 1
                else: dog_l += 1
    n = w + l
    pct = f"{w / n * 100:.1f}" if n > 0 else "N/A"
    units = f"{(w - l * 1.1):.1f}"
    per_day = f"{n / 73:.1f}"
    fav_pct = f"{fav_w / (fav_w + fav_l) * 100:.1f}" if (fav_w + fav_l) > 0 else "N/A"
    dog_pct = f"{dog_w / (dog_w + dog_l) * 100:.1f}" if (dog_w + dog_l) > 0 else "N/A"
    mark = " <- current" if t == 0.63 else ""
    print(f"{t:.2f}".ljust(7) + f"| {w!s:<5}| {l!s:<5}| {pct:<6}| {units:<8}| {per_day:<10}| {fav_pct:<7}| {dog_pct}{mark}")
