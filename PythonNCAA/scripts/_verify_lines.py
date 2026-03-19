#!/usr/bin/env python3
# scripts/_verify_lines.py — Verify line sign convention
import json

store = json.loads(open("data/history.json").read())
runs = store.get("runs") or []

print("=== Games with |line| > 15 (first 20) ===")
count = 0
for r in runs:
    for g in (r.get("games") or []):
        if count >= 20: break
        abs_line = abs(g.get("line", 0))
        if abs_line < 15: continue
        if g.get("homeScore") is None: continue
        margin = g["homeScore"] - g["awayScore"]
        print(f"{r.get('date')} | {g['away']} @ {g['home']} | line={g['line']} | score: away={g['awayScore']} home={g['homeScore']} | homeMargin={margin}")
        if g["line"] > 0:
            dog_covered = margin + g["line"] > 0
            print(f"  Home is {g['line']} pt dog. Away won by {-margin}. Dog covered? {dog_covered}")
        else:
            dog_covered = -(margin + g["line"]) > 0
            print(f"  Away is {abs_line} pt dog. Home won by {margin}. Dog covered? {dog_covered}")
        count += 1

total_dog_w, total_dog_l = 0, 0
for r in runs:
    for g in (r.get("games") or []):
        if g.get("homeScore") is None or g.get("awayScore") is None: continue
        if g.get("status") in ("SKIPPED", "MISSING_STATS"): continue
        if not g.get("line") or g["line"] == 0: continue
        margin = g["homeScore"] - g["awayScore"]
        v = margin + g["line"]
        if g["line"] > 0:
            if v > 0: total_dog_w += 1
            elif v < 0: total_dog_l += 1
        else:
            if v < 0: total_dog_w += 1
            elif v > 0: total_dog_l += 1

print(f"\n=== ALL dogs ATS (no model filter): {total_dog_w}-{total_dog_l} ({total_dog_w / (total_dog_w + total_dog_l) * 100:.1f}%) ===")
print("(Should be ~50% if lines/grading are correct)")
