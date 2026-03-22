#!/usr/bin/env python3
# scripts/_check_big_dogs.py — Check 7+ point dogs with pCover >= 0.60
import json

store = json.loads(open("data/history.json").read())
runs = store.get("runs") or []

wins, losses = 0, 0
loss_list = []

for r in runs:
    for g in (r.get("games") or []):
        if g.get("homeScore") is None or g.get("awayScore") is None:
            continue
        if g.get("status") in ("SKIPPED", "MISSING_STATS"):
            continue
        abs_line = abs(g.get("line", 0))
        if abs_line < 7:
            continue
        home_dog = g.get("line", 0) > 0
        if not home_dog and g.get("line", 0) >= 0:
            continue
        dog_side = "home" if home_dog else "away"
        dog_p = g.get("pHomeCover", 0) if home_dog else g.get("pAwayCover", 0)
        if dog_p < 0.60:
            continue
        margin = g["homeScore"] - g["awayScore"]
        v = margin + g["line"]
        covered = v > 0 if dog_side == "home" else v < 0
        if covered:
            wins += 1
        else:
            losses += 1
            loss_list.append({
                "date": r.get("date"), "game": f"{g['away']} @ {g['home']}",
                "line": g["line"], "score": f"{g['awayScore']}-{g['homeScore']}",
                "dogP": f"{dog_p:.3f}", "margin": margin, "spreadResult": v
            })

print(f"7+ point dogs with pCover >= 0.60: {wins}-{losses}")
print("\nLosses:")
for ll in loss_list:
    print(f"  {ll['date']} | {ll['game']} | line: {ll['line']} | score: {ll['score']} | dogP: {ll['dogP']} | margin: {ll['margin']} | spread result: {ll['spreadResult']}")

# All 7+ point dogs (no pCover filter)
all_big_dog_w, all_big_dog_l = 0, 0
for r in runs:
    for g in (r.get("games") or []):
        if g.get("homeScore") is None or g.get("awayScore") is None:
            continue
        if g.get("status") in ("SKIPPED", "MISSING_STATS"):
            continue
        abs_line = abs(g.get("line", 0))
        if abs_line < 7:
            continue
        home_dog = g.get("line", 0) > 0
        if not home_dog and g.get("line", 0) >= 0:
            continue
        margin = g["homeScore"] - g["awayScore"]
        v = margin + g["line"]
        covered = (v > 0 if home_dog else v < 0)
        if covered:
            all_big_dog_w += 1
        else:
            all_big_dog_l += 1

print(f"\nALL 7+ point dogs (no pCover filter): {all_big_dog_w}-{all_big_dog_l} ({all_big_dog_w / (all_big_dog_w + all_big_dog_l) * 100:.1f}%)")

print("\n=== SAMPLE 7+ pt dog wins (first 10) ===")
shown = 0
for r in runs:
    if shown >= 10:
        break
    for g in (r.get("games") or []):
        if shown >= 10:
            break
        if g.get("homeScore") is None or g.get("awayScore") is None:
            continue
        if g.get("status") in ("SKIPPED", "MISSING_STATS"):
            continue
        abs_line = abs(g.get("line", 0))
        if abs_line < 7:
            continue
        home_dog = g.get("line", 0) > 0
        if not home_dog and g.get("line", 0) >= 0:
            continue
        dog_side = "home" if home_dog else "away"
        dog_p = g.get("pHomeCover", 0) if home_dog else g.get("pAwayCover", 0)
        if dog_p < 0.60:
            continue
        margin = g["homeScore"] - g["awayScore"]
        v = margin + g["line"]
        covered = v > 0 if dog_side == "home" else v < 0
        if not covered:
            continue
        dog_team = g["home"] if dog_side == "home" else g["away"]
        fav_team = g["away"] if dog_side == "home" else g["home"]
        print(f"  {r.get('date')} | {dog_team} +{abs_line} vs {fav_team} | {g['awayScore']}-{g['homeScore']} | dogP={dog_p:.3f} | spreadResult={v:.1f}")
        shown += 1
