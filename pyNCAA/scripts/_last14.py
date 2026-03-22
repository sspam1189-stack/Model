#!/usr/bin/env python3
# scripts/_last14.py — Last 14 days record summary
import json

h = json.loads(open("data/history.json").read())
runs = [r for r in (h.get("runs") or []) if not r.get("burnIn")]
last14 = runs[-14:]

sw, sl, tw, tl = 0, 0, 0, 0
for r in last14:
    print(f"{r.get('date')}: {len(r.get('games', []))} games")
    for g in (r.get("games") or []):
        if g.get("sResult") == "WIN": sw += 1
        elif g.get("sResult") == "LOSS": sl += 1
        if g.get("oResult") == "WIN": tw += 1
        elif g.get("oResult") == "LOSS": tl += 1

print(f"\nLAST 14 DAYS:")
print(f"Spreads: {sw}-{sl} ({sw / (sw + sl) * 100:.1f}%)  {sw + sl} picks")
print(f"Totals:  {tw}-{tl} ({tw / (tw + tl) * 100:.1f}%)  {tw + tl} picks")
print(f"Combined: {sw + tw}-{sl + tl} ({(sw + tw) / (sw + tw + sl + tl) * 100:.1f}%)")
