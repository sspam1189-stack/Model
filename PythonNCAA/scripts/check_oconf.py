#!/usr/bin/env python3
# scripts/check_oconf.py — Check oConf distribution
import json

h = json.loads(open("data/history.json").read())
runs = h.get("runs") or {}
confs = {}
over_w, over_l, under_w, under_l = 0, 0, 0, 0
total_picks, pass_count = 0, 0

items = runs.items() if isinstance(runs, dict) else enumerate(runs)
for _, run in items:
    if not run.get("games"): continue
    for g in run["games"]:
        if not g.get("oPick") or g["oPick"] == "PASS":
            pass_count += 1; continue
        total_picks += 1
        c = g.get("oConf", "undefined")
        confs[c] = confs.get(c, 0) + 1
        if g.get("oResult") == "WIN":
            if g["oPick"] == "OVER": over_w += 1
            else: under_w += 1
        elif g.get("oResult") == "LOSS":
            if g["oPick"] == "OVER": over_l += 1
            else: under_l += 1

print("oConf distribution:")
for c, n in confs.items():
    print(f"  {c}: {n}")
print(f"\nTotal oPicks (non-PASS): {total_picks}")
print(f"PASS: {pass_count}")
if over_w + over_l > 0:
    print(f"\nOVER:  {over_w}-{over_l} ({over_w / (over_w + over_l) * 100:.1f}%)")
if under_w + under_l > 0:
    print(f"UNDER: {under_w}-{under_l} ({under_w / (under_w + under_l) * 100:.1f}%)")
