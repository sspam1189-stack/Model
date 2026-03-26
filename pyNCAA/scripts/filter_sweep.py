#!/usr/bin/env python3
# scripts/filter_sweep.py — Full spread filter combination sweep
import json

h = json.loads(open("data/history.json").read())
runs = h.get("runs") or {}

def check_cover(g, side):
    abs_line = abs(g.get("line", 0))
    home_fav = g.get("line", 0) < 0
    if side == "home":
        return (g["homeScore"] - g["awayScore"]) - abs_line > 0 if home_fav else (g["homeScore"] - g["awayScore"]) + abs_line > 0
    else:
        return (g["awayScore"] - g["homeScore"]) + abs_line > 0 if home_fav else (g["awayScore"] - g["homeScore"]) - abs_line > 0

games = []
items = runs.items() if isinstance(runs, dict) else enumerate(runs)
for _, run in items:
    if not run.get("games"): continue
    for g in run["games"]:
        if g.get("homeScore") is None or g.get("pHomeCover") is None: continue
        if abs(g.get("line", 0)) == 0: continue
        s_diff = g.get("sDiff", 0)
        p_cover = max(g.get("pHomeCover", 0), g.get("pAwayCover", 0))
        side = "home" if g.get("pHomeCover", 0) >= g.get("pAwayCover", 0) else "away"
        abs_line = abs(g.get("line", 0))
        is_dog = (side == "home" and g.get("line", 0) > 0) or (side == "away" and g.get("line", 0) < 0)
        covers = check_cover(g, side)
        games.append({"sDiff": s_diff, "pCover": p_cover, "absLine": abs_line, "isDog": is_dog, "covers": covers})

print(f"Total games with data: {len(games)}\n")
print("=== SPREAD FILTER COMBINATIONS ===")
print(f"{'pCover':<7} | {'sDiff':<9} | {'dogRule':<13} | {'favRule':<13} | {'W-L (pct)':<16} | ROI")
print("-" * 85)

for p_thresh in [0.58, 0.60, 0.63, 0.65, 0.67]:
    for s_min, s_max in [(3,9),(3,12),(4,9),(3,7)]:
        for fav_mode in ["all", "cap6", "cap4", "none"]:
            w, l = 0, 0
            for g in games:
                if g["pCover"] < p_thresh: continue
                if g["sDiff"] < s_min or g["sDiff"] > s_max: continue
                if not g["isDog"]:
                    if fav_mode == "none": continue
                    if fav_mode == "cap6" and g["absLine"] > 6: continue
                    if fav_mode == "cap4" and g["absLine"] > 4: continue
                if g["covers"]: w += 1
                else: l += 1
            if w + l < 20: continue
            n = w + l
            pct = f"{w / n * 100:.1f}"
            roi = f"{(w * 0.91 - l) / n * 100:.1f}"
            print(f">={p_thresh:.2f} | {s_min}-{s_max}".ljust(20) + f" | dogs:all".ljust(14) + f" | favs:{fav_mode}".ljust(14) + f" | {w}-{l} ({pct}%)".ljust(18) + f" | {roi}%")

# Dogs only
print("\n=== DOGS ONLY -- by pCover threshold ===")
for p_thresh in [0.55, 0.58, 0.60, 0.63, 0.65, 0.67, 0.70]:
    for s_min, s_max in [(3,9),(3,12),(2,9)]:
        w, l = 0, 0
        for g in games:
            if not g["isDog"]: continue
            if g["pCover"] < p_thresh: continue
            if g["sDiff"] < s_min or g["sDiff"] > s_max: continue
            if g["covers"]: w += 1
            else: l += 1
        if w + l < 10: continue
        n = w + l
        pct = f"{w / n * 100:.1f}"
        roi = f"{(w * 0.91 - l) / n * 100:.1f}"
        print(f"  p>={p_thresh:.2f} sDiff {s_min}-{s_max}: {w}-{l} ({pct}%) n={n} ROI:{roi}%")

# Top combos
print("\n=== TOP 15 COMBOS BY ROI (min 50 picks) ===")
combos = []
for p_thresh in [0.58, 0.60, 0.63, 0.65, 0.67, 0.70]:
    for s_min, s_max in [(3,9),(3,12),(4,9),(3,7),(2,9)]:
        for fav_mode in ["all", "cap6", "cap4", "none"]:
            w, l = 0, 0
            for g in games:
                if g["pCover"] < p_thresh: continue
                if g["sDiff"] < s_min or g["sDiff"] > s_max: continue
                if g["isDog"]: pass
                else:
                    if fav_mode == "none": continue
                    if fav_mode == "cap6" and g["absLine"] > 6: continue
                    if fav_mode == "cap4" and g["absLine"] > 4: continue
                if g["covers"]: w += 1
                else: l += 1
            n = w + l
            if n < 50: continue
            pct = w / n * 100
            roi = (w * 0.91 - l) / n * 100
            combos.append({"pThresh": p_thresh, "sMin": s_min, "sMax": s_max, "favMode": fav_mode, "w": w, "l": l, "n": n, "pct": pct, "roi": roi})
combos.sort(key=lambda c: c["roi"], reverse=True)
for c in combos[:15]:
    print(f"  p>={c['pThresh']:.2f} sDiff {c['sMin']}-{c['sMax']} favs:{c['favMode']:<5} | {c['w']}-{c['l']} ({c['pct']:.1f}%) n={c['n']} ROI:{c['roi']:.1f}%")
