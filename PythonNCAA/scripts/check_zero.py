#!/usr/bin/env python3
# scripts/check_zero.py — Count remaining +0 picks
import json

h = json.loads(open("data/history.json").read())
runs = h.get("runs") or {}
z = 0
items = runs.items() if isinstance(runs, dict) else enumerate(runs)
for _, run in items:
    if not run.get("games"): continue
    for g in run["games"]:
        if g.get("sPick") and g["sPick"] != "PASS" and "+0" in g["sPick"]:
            z += 1
print(f"Remaining +0 picks: {z}")
