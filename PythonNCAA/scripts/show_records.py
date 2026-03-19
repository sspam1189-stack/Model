#!/usr/bin/env python3
# scripts/show_records.py — Show all-time records
import json

h = json.loads(open("data/history.json").read())
runs = h.get("runs") or {}
s_w, s_l, s_p, o_w, o_l, o_p = 0, 0, 0, 0, 0, 0

items = runs.items() if isinstance(runs, dict) else enumerate(runs)
for _, run in items:
    if not run.get("games"): continue
    for g in run["games"]:
        if g.get("sResult") == "WIN": s_w += 1
        elif g.get("sResult") == "LOSS": s_l += 1
        elif g.get("sResult") == "PUSH": s_p += 1
        if g.get("oResult") == "WIN": o_w += 1
        elif g.get("oResult") == "LOSS": o_l += 1
        elif g.get("oResult") == "PUSH": o_p += 1

print("NCAA MODEL ALL-TIME RECORDS")
print("=" * 31)
s_push = f"-{s_p}" if s_p else ""
o_push = f"-{o_p}" if o_p else ""
print(f"Spreads: {s_w}-{s_l}{s_push} ({s_w / (s_w + s_l) * 100:.1f}%)" if s_w + s_l > 0 else "Spreads: 0-0")
print(f"Totals:  {o_w}-{o_l}{o_push} ({o_w / (o_w + o_l) * 100:.1f}%)" if o_w + o_l > 0 else "Totals: 0-0")
combined_w, combined_l = s_w + o_w, s_l + o_l
if combined_w + combined_l > 0:
    print(f"Combined: {combined_w}-{combined_l} ({combined_w / (combined_w + combined_l) * 100:.1f}%)")
