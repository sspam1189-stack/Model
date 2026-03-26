#!/usr/bin/env python3
# scripts/_sweep_thresh.py — Full threshold sweep (spreads)
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

total_days = len(set(r.get("date") for r in runs))

def grade_spread(g, side):
    margin = g["homeScore"] - g["awayScore"]
    v = margin + g["line"]
    if side == "home": return "WIN" if v > 0 else ("PUSH" if v == 0 else "LOSS")
    return "WIN" if v < 0 else ("PUSH" if v == 0 else "LOSS")

# Sanity check
dog_w, dog_l = 0, 0
for g in games:
    if g.get("line", 0) == 0: continue
    dog_side = "home" if g["line"] > 0 else "away"
    r = grade_spread(g, dog_side)
    if r == "WIN": dog_w += 1
    elif r == "LOSS": dog_l += 1
print(f"SANITY: ALL dogs ATS = {dog_w}-{dog_l} ({dog_w / (dog_w + dog_l) * 100:.1f}%) -- should be ~50%")
print(f"Total games: {len(games)}, Total days: {total_days}\n")

print("=== ALL SIDES (best pCover side, fav line cap 6) ===")
print("pCover | sDiffMin | W    - L   - P | Pct%  | Picks/Day | Units")
for p_thresh in [0.55, 0.58, 0.60, 0.62, 0.63, 0.65, 0.67, 0.70]:
    for sd_min in [0, 3]:
        w, l, p = 0, 0, 0
        for g in games:
            pH, pA = g.get("pHomeCover", 0), g.get("pAwayCover", 0)
            best_p = max(pH, pA)
            side = "home" if pH >= pA else "away"
            abs_line = abs(g.get("line", 0))
            sd = g.get("sDiff", 0)
            is_dog = (side == "home" and g["line"] > 0) or (side == "away" and g["line"] < 0)
            line_ok = True if is_dog else abs_line <= 6
            if best_p >= p_thresh and sd >= sd_min and sd <= 12 and line_ok and abs_line > 0:
                result = grade_spread(g, side)
                if result == "WIN": w += 1
                elif result == "LOSS": l += 1
                else: p += 1
        total = w + l
        pct = f"{w / total * 100:.1f}" if total > 0 else "n/a"
        units = f"{(w - l * 1.1):.1f}"
        ppd = f"{(w + l + p) / total_days:.1f}"
        print(f"  {p_thresh:.2f}  |    {sd_min}     | {w:>4}-{l:>4}-{p:>2} | {pct:>5} | {ppd:>9} | {units:>6}")

print("\n=== DOGS ONLY (dog's pCover directly) ===")
print("pCover | minLine | W    - L   - P | Pct%  | Picks/Day | Units")
for p_thresh in [0.50, 0.52, 0.53, 0.55, 0.57, 0.58, 0.60, 0.62, 0.65]:
    for min_line in [0.5, 2.5, 3.5]:
        w, l, p = 0, 0, 0
        for g in games:
            abs_line = abs(g.get("line", 0))
            if abs_line < min_line: continue
            home_dog = g.get("line", 0) > 0
            away_dog = g.get("line", 0) < 0
            if not home_dog and not away_dog: continue
            dog_side = "home" if home_dog else "away"
            dog_p_cover = g.get("pHomeCover", 0) if home_dog else g.get("pAwayCover", 0)
            if dog_p_cover >= p_thresh:
                result = grade_spread(g, dog_side)
                if result == "WIN": w += 1
                elif result == "LOSS": l += 1
                else: p += 1
        total = w + l
        pct = f"{w / total * 100:.1f}" if total > 0 else "n/a"
        units = f"{(w - l * 1.1):.1f}"
        ppd = f"{(w + l + p) / total_days:.1f}"
        print(f"  {p_thresh:.2f}  |  {min_line:4.1f} | {w:>4}-{l:>4}-{p:>2} | {pct:>5} | {ppd:>9} | {units:>6}")
