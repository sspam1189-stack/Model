#!/usr/bin/env python3
# scripts/debug_regrade3.py — Compare gradeSpread vs margin-based method
import json
import re

h = json.loads(open("data/history.json").read())
runs = h.get("runs") or {}

def parse_spread_pick(pick):
    if not pick or pick == "PASS": return None
    m = re.match(r"(.+?)\s+([+-])(\d+(?:\.\d+)?)", pick)
    return {"team": m.group(1).strip(), "sign": m.group(2), "pts": float(m.group(3))} if m else None

def grade_spread(g):
    p = parse_spread_pick(g.get("sPick"))
    if not p: return None
    chosen_is_home = p["team"] == g.get("home")
    margin = (g["homeScore"] - g["awayScore"]) if chosen_is_home else (g["awayScore"] - g["homeScore"])
    val = margin + p["pts"] if p["sign"] == "+" else margin - p["pts"]
    if val == 0: return "PUSH"
    return "WIN" if val > 0 else "LOSS"

count = 0
items = runs.items() if isinstance(runs, dict) else enumerate(runs)
for _, run in items:
    if not run.get("games"): continue
    for g in run["games"]:
        if not g.get("sPick") or g["sPick"] == "PASS" or g.get("homeScore") is None: continue
        if count >= 10: break
        result1 = grade_spread(g)
        picks_home = g.get("margin", 0) > 0
        if picks_home:
            covers = g["homeScore"] + g.get("line", 0) > g["awayScore"]
        else:
            covers = g["awayScore"] - g.get("line", 0) > g["homeScore"]
        result2 = "WIN" if covers else "LOSS"
        if result1 != result2:
            count += 1
            p = parse_spread_pick(g["sPick"])
            print(f"MISMATCH: {g['sPick']}")
            print(f"  gradeSpread says: {result1} | margin method says: {result2}")
            print(f"  margin: {g.get('margin')} | line: {g.get('line')} | H: {g.get('homeScore')} | A: {g.get('awayScore')}")
            if p:
                chosen_is_home = p["team"] == g.get("home")
                print(f"  pick team: {p['team']} | chosenIsHome: {chosen_is_home} | sign: {p['sign']} | pts: {p['pts']}")
            print(f"  pHomeCover: {g.get('pHomeCover')} | pAwayCover: {g.get('pAwayCover')}")

if count == 0:
    print("No mismatches in first batch")

total_mm, total_checked = 0, 0
items2 = runs.items() if isinstance(runs, dict) else enumerate(runs)
for _, run in items2:
    if not run.get("games"): continue
    for g in run["games"]:
        if not g.get("sPick") or g["sPick"] == "PASS" or g.get("homeScore") is None: continue
        total_checked += 1
        result1 = grade_spread(g)
        picks_home = g.get("margin", 0) > 0
        covers = (g["homeScore"] + g.get("line", 0) > g["awayScore"]) if picks_home else (g["awayScore"] - g.get("line", 0) > g["homeScore"])
        result2 = "WIN" if covers else "LOSS"
        if result1 != result2: total_mm += 1
print(f"\nTotal checked: {total_checked} | Mismatches: {total_mm}")

margin_differs = 0
items3 = runs.items() if isinstance(runs, dict) else enumerate(runs)
for _, run in items3:
    if not run.get("games"): continue
    for g in run["games"]:
        if g.get("pHomeCover") is None or g.get("homeScore") is None: continue
        margin_says_home = g.get("margin", 0) > 0
        prob_says_home = g.get("pHomeCover", 0) >= g.get("pAwayCover", 0)
        if margin_says_home != prob_says_home: margin_differs += 1
print(f"Games where margin and pCover disagree on side: {margin_differs}")
