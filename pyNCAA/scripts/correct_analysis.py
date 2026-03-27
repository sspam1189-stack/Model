#!/usr/bin/env python3
# scripts/correct_analysis.py — Corrected cover analysis by dog/fav
import json

h = json.loads(open("data/history.json").read())
runs = h.get("runs") or {}

def check_cover(g, side):
    cover_margin = (g["homeScore"] - g["awayScore"]) + g.get("line", 0)
    if side == "home":
        return cover_margin > 0
    else:
        return cover_margin < 0

buckets = {"DOG": {"w": 0, "l": 0}, "FAV": {"w": 0, "l": 0}}
dog_by_line = {"dog 0-6": {"w": 0, "l": 0}, "dog 6-10": {"w": 0, "l": 0}, "dog 10+": {"w": 0, "l": 0}}
dog_by_dnet = {f"dNET {lo}-{hi}": {"w": 0, "l": 0} for lo, hi in [(0,5),(5,10),(10,15),(15,20)]}
dog_by_dnet["dNET 20+"] = {"w": 0, "l": 0}
fav_by_line = {"fav 0-6": {"w": 0, "l": 0}, "fav 6-10": {"w": 0, "l": 0}, "fav 10+": {"w": 0, "l": 0}}
dog_by_pcover = {}

items = runs.items() if isinstance(runs, dict) else enumerate(runs)
for _, run in items:
    if not run.get("games"): continue
    for g in run["games"]:
        if g.get("homeScore") is None or g.get("pHomeCover") is None: continue
        s_diff = g.get("sDiff", 0)
        p_cover = max(g.get("pHomeCover", 0), g.get("pAwayCover", 0))
        if s_diff < 3 or s_diff > 9 or p_cover < 0.60: continue
        side = "home" if g.get("pHomeCover", 0) >= g.get("pAwayCover", 0) else "away"
        abs_line = abs(g.get("line", 0))
        is_dog = (side == "home" and g.get("line", 0) < 0) or (side == "away" and g.get("line", 0) > 0)
        d_net = abs((g.get("_features") or {}).get("dNET", 0))
        covers = check_cover(g, side)
        if is_dog:
            if covers: buckets["DOG"]["w"] += 1
            else: buckets["DOG"]["l"] += 1
            if abs_line <= 6: k = "dog 0-6"
            elif abs_line <= 10: k = "dog 6-10"
            else: k = "dog 10+"
            if covers: dog_by_line[k]["w"] += 1
            else: dog_by_line[k]["l"] += 1
            if d_net <= 5: k = "dNET 0-5"
            elif d_net <= 10: k = "dNET 5-10"
            elif d_net <= 15: k = "dNET 10-15"
            elif d_net <= 20: k = "dNET 15-20"
            else: k = "dNET 20+"
            if covers: dog_by_dnet[k]["w"] += 1
            else: dog_by_dnet[k]["l"] += 1
            for thresh in [0.55, 0.58, 0.60, 0.63, 0.65, 0.67, 0.70]:
                if p_cover >= thresh:
                    k2 = f"pCover>={thresh:.2f}"
                    if k2 not in dog_by_pcover: dog_by_pcover[k2] = {"w": 0, "l": 0}
                    if covers: dog_by_pcover[k2]["w"] += 1
                    else: dog_by_pcover[k2]["l"] += 1
        else:
            if covers: buckets["FAV"]["w"] += 1
            else: buckets["FAV"]["l"] += 1
            if abs_line <= 6: k = "fav 0-6"
            elif abs_line <= 10: k = "fav 6-10"
            else: k = "fav 10+"
            if covers: fav_by_line[k]["w"] += 1
            else: fav_by_line[k]["l"] += 1

print("=== CORRECTED ANALYSIS (pCover >= 0.60, sDiff 3-9) ===\n")
for name, wl in buckets.items():
    n = wl["w"] + wl["l"]
    print(f"{name:<10}{wl['w']}-{wl['l']} ({wl['w'] / n * 100:.1f}%)  n={n}")
print("\n=== DOGS by line size ===")
for name, wl in dog_by_line.items():
    n = wl["w"] + wl["l"]
    if n == 0: continue
    print(f"{name:<15}{wl['w']}-{wl['l']} ({wl['w'] / n * 100:.1f}%)  n={n}")
print("\n=== FAVS by line size ===")
for name, wl in fav_by_line.items():
    n = wl["w"] + wl["l"]
    if n == 0: continue
    print(f"{name:<15}{wl['w']}-{wl['l']} ({wl['w'] / n * 100:.1f}%)  n={n}")
print("\n=== DOGS by dNET ===")
for name, wl in dog_by_dnet.items():
    n = wl["w"] + wl["l"]
    if n == 0: continue
    print(f"{name:<15}{wl['w']}-{wl['l']} ({wl['w'] / n * 100:.1f}%)  n={n}")
print("\n=== DOGS by pCover threshold ===")
for k, wl in dog_by_pcover.items():
    n = wl["w"] + wl["l"]
    print(f"{k:<18}{wl['w']}-{wl['l']} ({wl['w'] / n * 100:.1f}%)  n={n}")
