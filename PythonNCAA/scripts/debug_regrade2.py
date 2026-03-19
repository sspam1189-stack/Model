#!/usr/bin/env python3
# scripts/debug_regrade2.py — Check dog vs fav after regrade
import json
import re

h = json.loads(open("data/history.json").read())
runs = h.get("runs") or {}

dogs, favs = 0, 0
dog_w, dog_l, fav_w, fav_l = 0, 0, 0, 0

items = runs.items() if isinstance(runs, dict) else enumerate(runs)
for _, run in items:
    if not run.get("games"): continue
    for g in run["games"]:
        if not g.get("sPick") or g["sPick"] == "PASS" or g.get("sResult") not in ("WIN", "LOSS"): continue
        m = re.match(r"(.+?)\s+([+-])(\d+(?:\.\d+)?)", g["sPick"])
        if not m: continue
        sign = m.group(2)
        is_dog_pick = sign == "+"
        if is_dog_pick:
            dogs += 1
            if g["sResult"] == "WIN": dog_w += 1
            else: dog_l += 1
        else:
            favs += 1
            if g["sResult"] == "WIN": fav_w += 1
            else: fav_l += 1

print("After regrade:")
print(f"Dog picks: {dogs} | Record: {dog_w}-{dog_l} ({dog_w / (dog_w + dog_l) * 100:.1f}%)" if dog_w + dog_l > 0 else f"Dog picks: {dogs}")
print(f"Fav picks: {favs} | Record: {fav_w}-{fav_l} ({fav_w / (fav_w + fav_l) * 100:.1f}%)" if fav_w + fav_l > 0 else f"Fav picks: {favs}")
print(f"Total: {dog_w + fav_w}-{dog_l + fav_l}")

if favs > 0:
    count = 0
    items2 = runs.items() if isinstance(runs, dict) else enumerate(runs)
    for _, run in items2:
        if not run.get("games"): continue
        for g in run["games"]:
            if not g.get("sPick") or g["sPick"] == "PASS": continue
            m = re.match(r"(.+?)\s+([+-])(\d+(?:\.\d+)?)", g["sPick"])
            if not m or m.group(2) != "-": continue
            if count >= 3: break
            count += 1
            print(f"\nFAV PICK FOUND: {g['sPick']}")
            print(f"  line: {g.get('line')} | margin: {g.get('margin')}")
            print(f"  pHomeCover: {g.get('pHomeCover')} | pAwayCover: {g.get('pAwayCover')}")
