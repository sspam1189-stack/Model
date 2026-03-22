#!/usr/bin/env python3
# scripts/_record.py — Current season record summary
import json
import re

h = json.loads(open("data/history.json").read())

w, l = 0, 0
fav_w, fav_l, dog_w, dog_l = 0, 0, 0, 0
total_picks = 0

for r in (h.get("runs") or []):
    if r.get("burnIn"):
        continue
    for g in (r.get("games") or []):
        if not g.get("sPick") or g["sPick"] == "PASS":
            continue
        if not g.get("sResult"):
            continue
        m = re.search(r"([+-])(\d+(?:\.\d+)?)$", g["sPick"])
        is_fav = m and m.group(1) == "-"
        is_dog = m and m.group(1) == "+"
        if g["sResult"] == "WIN":
            w += 1
            if is_fav: fav_w += 1
            if is_dog: dog_w += 1
        elif g["sResult"] == "LOSS":
            l += 1
            if is_fav: fav_l += 1
            if is_dog: dog_l += 1
        total_picks += 1

print("=== NCAA Current (with 1.4x factor) ===")
print(f"Overall: {w}-{l} ({w / (w + l) * 100:.1f}%) {(w - l * 1.1):.1f}u  ({total_picks} picks, {total_picks / 73:.1f}/day)")
if fav_w + fav_l > 0:
    print(f"Favs:    {fav_w}-{fav_l} ({fav_w / (fav_w + fav_l) * 100:.1f}%) {(fav_w - fav_l * 1.1):.1f}u")
if dog_w + dog_l > 0:
    print(f"Dogs:    {dog_w}-{dog_l} ({dog_w / (dog_w + dog_l) * 100:.1f}%) {(dog_w - dog_l * 1.1):.1f}u")
