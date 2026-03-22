#!/usr/bin/env python3
# scripts/_check_today.py — Check today's games and pCover distribution
import json

h = json.loads(open("data/history.json").read())
last = h["runs"][-1] if isinstance(h.get("runs"), list) and h["runs"] else None
if not last:
    print("No runs found")
    exit()

print(f"Date: {last.get('date')} | Games: {len(last.get('games', []))}")
print("\nAll non-skipped games:")
for x in (last.get("games") or []):
    if x.get("status") == "SKIPPED":
        continue
    p_h = x.get("pHomeCover") or x.get("pCover") or 0
    p_a = x.get("pAwayCover", 0)
    best = max(p_h, p_a)
    print(f"{x.get('away')} @ {x.get('home')} | bestP: {best:.3f} | sDiff: {(x.get('sDiff') or 0):.1f} | line: {x.get('line')} | pick: {x.get('sPick', 'PASS')}")

print("\n--- Backtest: recent pCover distribution ---")
all_games = [g for r in h.get("runs", []) for g in (r.get("games") or [])
             if g.get("status") not in ("SKIPPED", "MISSING_STATS") and g.get("result") is not None]
graded = [g for g in all_games if g.get("pCover", 0) > 0]
print(f"Total graded games with pCover: {len(graded)}")

for thresh in [0.55, 0.58, 0.60, 0.62, 0.65, 0.67, 0.70]:
    picks = [g for g in graded if g.get("pCover", 0) >= thresh]
    wins = sum(1 for g in picks if g.get("result") == "WIN")
    losses_v = sum(1 for g in picks if g.get("result") == "LOSS")
    pushes = sum(1 for g in picks if g.get("result") == "PUSH")
    pct = f"{wins / (wins + losses_v) * 100:.1f}" if (wins + losses_v) > 0 else "n/a"
    print(f"  >= {thresh}: {wins}-{losses_v}-{pushes} ({pct}%) | {len(picks)} picks total")
