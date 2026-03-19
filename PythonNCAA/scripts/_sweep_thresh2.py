#!/usr/bin/env python3
# scripts/_sweep_thresh2.py — Dog line distribution + sweep with min line floor
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

print("=== LINE DISTRIBUTION OF DOG PICKS (pCover >= 0.60) ===")
bucket_names = ["0", "0.5-1", "1.5-2.5", "3-5", "5.5-7", "7+"]
bucket_counts = {b: 0 for b in bucket_names}
bucket_wl = {b: [0, 0] for b in bucket_names}

for g in games:
    abs_line = abs(g.get("line", 0))
    home_dog = g.get("line", 0) > 0
    away_dog = g.get("line", 0) < 0
    if not home_dog and not away_dog: continue
    dog_p_cover = g.get("pHomeCover", 0) if home_dog else g.get("pAwayCover", 0)
    dog_side = "home" if home_dog else "away"
    if dog_p_cover < 0.60: continue
    if abs_line == 0: bucket = "0"
    elif abs_line <= 1: bucket = "0.5-1"
    elif abs_line <= 2.5: bucket = "1.5-2.5"
    elif abs_line <= 5: bucket = "3-5"
    elif abs_line <= 7: bucket = "5.5-7"
    else: bucket = "7+"
    bucket_counts[bucket] += 1
    result = grade_spread(g, dog_side)
    if result == "WIN": bucket_wl[bucket][0] += 1
    elif result == "LOSS": bucket_wl[bucket][1] += 1

for b in bucket_names:
    w, l = bucket_wl[b]
    pct = f"{w / (w + l) * 100:.1f}" if (w + l) > 0 else "n/a"
    print(f"  Line {b:<8}: {bucket_counts[b]} picks | {w}-{l} ({pct}%)")

print("\n=== DOGS ONLY -- with minimum line floor ===")
print("pCover | minLine | W    - L   - P | Pct%  | Picks/Day | Units")
for p_thresh in [0.55, 0.57, 0.58, 0.60, 0.62, 0.65]:
    for min_line in [0.5, 1.5, 2.5, 3.5]:
        w, l, p = 0, 0, 0
        for g in games:
            abs_line = abs(g.get("line", 0))
            if abs_line < min_line: continue
            home_dog = g.get("line", 0) > 0
            away_dog = g.get("line", 0) < 0
            if not home_dog and not away_dog: continue
            dog_p_cover = g.get("pHomeCover", 0) if home_dog else g.get("pAwayCover", 0)
            dog_side = "home" if home_dog else "away"
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

zero_line = sum(1 for g in games if g.get("line", 0) == 0)
print(f"\nGames with line = 0: {zero_line} out of {len(games)}")

dog_favored, dog_not_favored = 0, 0
for g in games:
    home_dog = g.get("line", 0) > 0
    away_dog = g.get("line", 0) < 0
    if not home_dog and not away_dog: continue
    dog_p = g.get("pHomeCover", 0) if home_dog else g.get("pAwayCover", 0)
    if dog_p >= 0.50: dog_favored += 1
    else: dog_not_favored += 1
print(f"Dog pCover >= 0.50: {dog_favored} | Dog pCover < 0.50: {dog_not_favored}")
print(f"Model thinks dog covers {dog_favored / (dog_favored + dog_not_favored) * 100:.1f}% of the time")
