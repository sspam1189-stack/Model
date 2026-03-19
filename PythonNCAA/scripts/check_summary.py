#!/usr/bin/env python3
# scripts/check_summary.py — Simulate OVER tally manually
import json

h = json.loads(open("data/history.json").read())
runs = h.get("runs") or {}
w, l, p = 0, 0, 0
items = runs.items() if isinstance(runs, dict) else enumerate(runs)
for _, run in items:
    if not run.get("games") or run.get("burnIn"): continue
    for g in run["games"]:
        if not isinstance(g.get("homeScore"), (int, float)) or not isinstance(g.get("awayScore"), (int, float)): continue
        if g.get("status") in ("MISSING_ODDS", "SKIPPED"): continue
        pick = g.get("oPick")
        pick_conf = g.get("oConf")
        if not pick or pick == "PASS": continue
        actionable = str(pick_conf).lower() in ("high", "elite")
        if not actionable: continue
        if pick != "OVER": continue
        actual = g["homeScore"] + g["awayScore"]
        if actual == g.get("total"): result = "PUSH"
        elif pick == "OVER": result = "WIN" if actual > g["total"] else "LOSS"
        else: continue
        if result == "WIN": w += 1
        elif result == "LOSS": l += 1
        else: p += 1
print(f"Manual OVER tally: {w}-{l}-{p}")

checked = 0
items2 = runs.items() if isinstance(runs, dict) else enumerate(runs)
for _, run in items2:
    if not run.get("games"): continue
    for g in run["games"]:
        if g.get("oPick") and g["oPick"] != "PASS" and g.get("oResult") and checked < 5:
            checked += 1
            print(f"{g['oPick']} {g.get('oConf')} | oResult: {g['oResult']} | total: {g.get('total')} | actual: {(g.get('homeScore', 0) or 0) + (g.get('awayScore', 0) or 0)}")
