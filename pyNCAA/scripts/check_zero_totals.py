#!/usr/bin/env python3
# scripts/check_zero_totals.py — Find picks with total=0
import json

h = json.loads(open("data/history.json").read())
runs = h.get("runs") or []
if isinstance(runs, dict):
    runs = list(runs.values())
count, zero_wins = 0, 0
for run in runs:
    if not run.get("games"): continue
    for g in run["games"]:
        if not g.get("oPick") or g["oPick"] == "PASS": continue
        if not g.get("total") or g["total"] == 0:
            count += 1
            if g.get("oResult") == "WIN": zero_wins += 1
            if count <= 20:
                print(f"{run.get('date')} | {g['oPick']} {g.get('total')} | {g.get('away')} @ {g.get('home')} | {g.get('oResult')} | pOU:{g.get('pOU')}")
print(f"\nTotal zero-total picks: {count}")
print(f"Of which are 'wins': {zero_wins}")
