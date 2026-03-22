#!/usr/bin/env python3
# scripts/_sweep_favcap.py — Sweep fav line cap options
import json

store = json.loads(open("data/history.json").read())
runs = store.get("runs") or []

games = []
for r in runs:
    for g in (r.get("games") or []):
        if g.get("homeScore") is None or g.get("awayScore") is None: continue
        if g.get("line") is None and g.get("line") != 0: continue
        if g.get("status") in ("SKIPPED", "MISSING_STATS"): continue
        if not g.get("pHomeCover") and not g.get("pAwayCover"): continue
        games.append(g)

def grade_spread(g, side):
    margin = g["homeScore"] - g["awayScore"]
    v = margin - g["line"]
    if side == "home": return "WIN" if v > 0 else ("PUSH" if v == 0 else "LOSS")
    return "WIN" if v < 0 else ("PUSH" if v == 0 else "LOSS")

total_days = len(set(r.get("date") for r in runs))

print("=== FAV PICKS BY LINE SIZE (pCover >= 0.60) ===")
line_ranges = [(0.5,3),(3.5,5),(5.5,6),(6.5,8),(8.5,10),(10.5,15),(15.5,30)]
for lo, hi in line_ranges:
    w, l = 0, 0
    for g in games:
        pH, pA = g.get("pHomeCover", 0), g.get("pAwayCover", 0)
        best_p = max(pH, pA)
        side = "home" if pH >= pA else "away"
        abs_line = abs(g.get("line", 0))
        is_dog = (side == "home" and g["line"] < 0) or (side == "away" and g["line"] > 0)
        if is_dog: continue
        if best_p < 0.60: continue
        if abs_line < lo or abs_line > hi: continue
        result = grade_spread(g, side)
        if result == "WIN": w += 1
        elif result == "LOSS": l += 1
    total = w + l
    pct = f"{w / total * 100:.1f}" if total > 0 else "n/a"
    print(f"  Line {lo}-{hi}: {w}-{l} ({pct}%) | {total} picks")

print("\n=== ALL SIDES @ pCover >= 0.60 with different fav caps ===")
print("FavCap | W    - L   | Pct%  | Picks/Day | Units")
for cap in [4, 5, 6, 8, 10, 15, 99]:
    w, l, p = 0, 0, 0
    for g in games:
        pH, pA = g.get("pHomeCover", 0), g.get("pAwayCover", 0)
        best_p = max(pH, pA)
        side = "home" if pH >= pA else "away"
        abs_line = abs(g.get("line", 0))
        is_dog = (side == "home" and g["line"] < 0) or (side == "away" and g["line"] > 0)
        line_ok = True if is_dog else abs_line <= cap
        if best_p >= 0.60 and line_ok and abs_line > 0:
            result = grade_spread(g, side)
            if result == "WIN": w += 1
            elif result == "LOSS": l += 1
            else: p += 1
    total = w + l
    pct = f"{w / total * 100:.1f}" if total > 0 else "n/a"
    units = f"{(w - l * 1.1):.1f}"
    ppd = f"{(w + l + p) / total_days:.1f}"
    label = "none" if cap == 99 else str(cap)
    print(f"  {label:>4}  | {w:>4}-{l:>4} | {pct:>5} | {ppd:>9} | {units:>6}")
