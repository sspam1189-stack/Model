#!/usr/bin/env python3
# scripts/_analyze.py — Analyze spread pick history by buckets
import json

h = json.loads(open("data/history.json").read())

w, l, p = 0, 0, 0
for r in (h.get("runs") or []):
    if r.get("burnIn"):
        continue
    for g in (r.get("games") or []):
        if g.get("sResult") == "WIN": w += 1
        elif g.get("sResult") == "LOSS": l += 1
        elif g.get("sResult") == "PUSH": p += 1
print(f"Overall: {w}-{l}-{p} ({w / (w + l) * 100:.1f}%)")

# Last 14 days
recent = [r for r in (h.get("runs") or []) if not r.get("burnIn")][-14:]
rw, rl = 0, 0
for r in recent:
    for g in (r.get("games") or []):
        if g.get("sResult") == "WIN": rw += 1
        elif g.get("sResult") == "LOSS": rl += 1
print(f"Last 14 days: {rw}-{rl} ({rw / (rw + rl) * 100:.1f}%)")

# By spread size
print("\nBy spread size:")
s_buckets = {"0-5": [], "5-10": [], "10-15": [], "15-20": [], "20+": []}
for r in (h.get("runs") or []):
    if r.get("burnIn"):
        continue
    for g in (r.get("games") or []):
        if g.get("sResult") not in ("WIN", "LOSS"):
            continue
        line = abs(g.get("line", 0))
        v = 1 if g["sResult"] == "WIN" else 0
        if line <= 5: s_buckets["0-5"].append(v)
        elif line <= 10: s_buckets["5-10"].append(v)
        elif line <= 15: s_buckets["10-15"].append(v)
        elif line <= 20: s_buckets["15-20"].append(v)
        else: s_buckets["20+"].append(v)
for k, v in s_buckets.items():
    wins = sum(v)
    t = len(v)
    if t > 0:
        print(f"  {k}: {wins}-{t - wins} ({wins / t * 100:.1f}%)")

# By sDiff
print("\nBy sDiff:")
sd_buckets = {"0-3": [], "3-6": [], "6-9": [], "9-12": [], "12+": []}
for r in (h.get("runs") or []):
    if r.get("burnIn"):
        continue
    for g in (r.get("games") or []):
        if g.get("sResult") not in ("WIN", "LOSS"):
            continue
        sd = abs(g.get("sDiff", 0))
        v = 1 if g["sResult"] == "WIN" else 0
        if sd <= 3: sd_buckets["0-3"].append(v)
        elif sd <= 6: sd_buckets["3-6"].append(v)
        elif sd <= 9: sd_buckets["6-9"].append(v)
        elif sd <= 12: sd_buckets["9-12"].append(v)
        else: sd_buckets["12+"].append(v)
for k, v in sd_buckets.items():
    wins = sum(v)
    t = len(v)
    if t > 0:
        print(f"  {k}: {wins}-{t - wins} ({wins / t * 100:.1f}%)")

# Fav vs dog
fav_w, fav_l, dog_w, dog_l = 0, 0, 0, 0
for r in (h.get("runs") or []):
    if r.get("burnIn"):
        continue
    for g in (r.get("games") or []):
        if g.get("sResult") not in ("WIN", "LOSS"):
            continue
        pick = g.get("sPick", "")
        is_fav = "-" in pick
        v = 1 if g["sResult"] == "WIN" else 0
        if is_fav: fav_w += v; fav_l += (1 - v)
        else: dog_w += v; dog_l += (1 - v)
print(f"\nFavorites: {fav_w}-{fav_l} ({fav_w / (fav_w + fav_l) * 100:.1f}%)")
print(f"Underdogs: {dog_w}-{dog_l} ({dog_w / (dog_w + dog_l) * 100:.1f}%)")

# pCover buckets
print("\nBy pCover:")
pc_buckets = {"0.50-0.57": [], "0.57-0.60": [], "0.60-0.65": [], "0.65-0.70": [], "0.70+": []}
for r in (h.get("runs") or []):
    if r.get("burnIn"):
        continue
    for g in (r.get("games") or []):
        if g.get("sResult") not in ("WIN", "LOSS"):
            continue
        pc = g.get("pCover", 0)
        v = 1 if g["sResult"] == "WIN" else 0
        if pc < 0.57: pc_buckets["0.50-0.57"].append(v)
        elif pc < 0.60: pc_buckets["0.57-0.60"].append(v)
        elif pc < 0.65: pc_buckets["0.60-0.65"].append(v)
        elif pc < 0.70: pc_buckets["0.65-0.70"].append(v)
        else: pc_buckets["0.70+"].append(v)
for k, v in pc_buckets.items():
    wins = sum(v)
    t = len(v)
    if t > 0:
        print(f"  {k}: {wins}-{t - wins} ({wins / t * 100:.1f}%)")

# ResidualVar
errors = []
for r in (h.get("runs") or []):
    if r.get("burnIn"):
        continue
    for g in (r.get("games") or []):
        if not isinstance(g.get("homeScore"), (int, float)) or not isinstance(g.get("awayScore"), (int, float)):
            continue
        if not isinstance(g.get("margin"), (int, float)) or not isinstance(g.get("line"), (int, float)):
            continue
        err = (g["homeScore"] - g["awayScore"]) - g["line"] - g["margin"]
        errors.append(err)
mean = sum(errors) / len(errors) if errors else 0
variance = sum((x - mean) ** 2 for x in errors) / len(errors) if errors else 0
print(f"\nResidualVar: {variance:.1f} (std={variance ** 0.5:.1f}) from {len(errors)} games")

# Current weights
wt = h.get("weights", {})
print("\nCurrent weights:")
for k, v in wt.items():
    if isinstance(v, (int, float)):
        print(f"  {k}: {v:.3f}")
