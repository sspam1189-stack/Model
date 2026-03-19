#!/usr/bin/env python3
# scripts/debug_regrade.py — Debug regrade: check Bayesian picks
import json

h = json.loads(open("data/history.json").read())
runs = h.get("runs") or {}

with_bayes, without_bayes = 0, 0
new_picks, new_wins, new_losses = 0, 0, 0

items = runs.items() if isinstance(runs, dict) else enumerate(runs)
for _, run in items:
    if not run.get("games"): continue
    for g in run["games"]:
        if g.get("homeScore") is None: continue
        if g.get("pHomeCover") is not None and g.get("pAwayCover") is not None:
            with_bayes += 1
            s_diff = g.get("sDiff", 0)
            best_p = max(g["pHomeCover"], g["pAwayCover"])
            side = "home" if g["pHomeCover"] >= g["pAwayCover"] else "away"
            is_dog = (side == "home" and g.get("line", 0) < 0) or (side == "away" and g.get("line", 0) > 0)
            if is_dog and best_p >= 0.60 and 3 <= s_diff <= 9:
                new_picks += 1
                if side == "home":
                    covers = g["homeScore"] + g["line"] > g["awayScore"]
                else:
                    covers = g["awayScore"] - g["line"] > g["homeScore"]
                if covers: new_wins += 1
                else: new_losses += 1
        else:
            without_bayes += 1

print(f"Games with Bayesian probs: {with_bayes}")
print(f"Games without: {without_bayes}\n")
print(f"New filter (dogs only, pCover>=0.60, sDiff 3-9):")
if new_wins + new_losses > 0:
    print(f"Record: {new_wins}-{new_losses} ({new_wins / (new_wins + new_losses) * 100:.1f}%)  n={new_picks}")

s_pick_wins, s_pick_losses = 0, 0
items2 = runs.items() if isinstance(runs, dict) else enumerate(runs)
for _, run in items2:
    if not run.get("games"): continue
    for g in run["games"]:
        if g.get("sResult") == "WIN": s_pick_wins += 1
        elif g.get("sResult") == "LOSS": s_pick_losses += 1
print(f"\nCurrent sResult in history: {s_pick_wins}-{s_pick_losses}")

loss_count = 0
items3 = runs.items() if isinstance(runs, dict) else enumerate(runs)
for _, run in items3:
    if not run.get("games"): continue
    for g in run["games"]:
        if g.get("sResult") != "LOSS" or loss_count >= 5: continue
        if g.get("pHomeCover") is None: continue
        side = "home" if g.get("pHomeCover", 0) >= g.get("pAwayCover", 0) else "away"
        is_dog = (side == "home" and g.get("line", 0) < 0) or (side == "away" and g.get("line", 0) > 0)
        loss_count += 1
        print(f"\nLOSS: {g.get('away')} @ {g.get('home')}")
        print(f"  sPick: {g.get('sPick')} | line: {g.get('line')}")
        print(f"  pHomeCover: {g.get('pHomeCover')} | pAwayCover: {g.get('pAwayCover')}")
        print(f"  side: {side} | isDog: {is_dog}")
        print(f"  H: {g.get('homeScore')} | A: {g.get('awayScore')}")
