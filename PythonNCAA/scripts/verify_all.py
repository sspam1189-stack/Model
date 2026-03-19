#!/usr/bin/env python3
# scripts/verify_all.py — Verify gradeSpread matches manual cover logic
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

print("=== MANUAL VERIFICATION OF SPECIFIC GAMES ===\n")
count = 0
items = runs.items() if isinstance(runs, dict) else enumerate(runs)
for _, run in items:
    if not run.get("games"): continue
    for g in run["games"]:
        if not g.get("sPick") or g["sPick"] == "PASS" or g.get("homeScore") is None: continue
        if count >= 20: break
        count += 1
        p = parse_spread_pick(g["sPick"])
        chosen_is_home = p["team"] == g.get("home")
        is_dog = p["sign"] == "+"
        if chosen_is_home:
            covers = (g["homeScore"] + p["pts"] > g["awayScore"]) if is_dog else (g["homeScore"] - p["pts"] > g["awayScore"])
        else:
            covers = (g["awayScore"] + p["pts"] > g["homeScore"]) if is_dog else (g["awayScore"] - p["pts"] > g["homeScore"])
        graded = grade_spread(g)
        manual_result = "WIN" if covers else "LOSS"
        match = "OK" if graded == manual_result else "MISMATCH"
        if count <= 5 or match == "MISMATCH":
            print(f"{match} | {g['sPick']}")
            print(f"  H:{g['homeScore']} A:{g['awayScore']} | line:{g.get('line')} | {'DOG' if is_dog else 'FAV'} | chosenIsHome:{chosen_is_home}")
            print(f"  gradeSpread: {graded} | manual: {manual_result}\n")

print("\n=== SYSTEMATIC CHECK: Dogs vs Favs (pCover>=0.63, sDiff 3-9) ===\n")
dog_w, dog_l, fav_w, fav_l = 0, 0, 0, 0
items2 = runs.items() if isinstance(runs, dict) else enumerate(runs)
for _, run in items2:
    if not run.get("games"): continue
    for g in run["games"]:
        if g.get("homeScore") is None or g.get("pHomeCover") is None: continue
        s_diff = g.get("sDiff", 0)
        p_cover = max(g.get("pHomeCover", 0), g.get("pAwayCover", 0))
        if s_diff < 3 or s_diff > 9 or p_cover < 0.63: continue
        side = "home" if g.get("pHomeCover", 0) >= g.get("pAwayCover", 0) else "away"
        abs_line = abs(g.get("line", 0))
        home_fav = g.get("line", 0) > 0
        if side == "home":
            pick_str = f"{g['home']} -{abs_line}" if home_fav else f"{g['home']} +{abs_line}"
        else:
            pick_str = f"{g['away']} +{abs_line}" if home_fav else f"{g['away']} -{abs_line}"
        p = parse_spread_pick(pick_str)
        chosen_is_home = p["team"] == g.get("home")
        is_dog = p["sign"] == "+"
        if chosen_is_home:
            covers = (g["homeScore"] + p["pts"] > g["awayScore"]) if is_dog else (g["homeScore"] - p["pts"] > g["awayScore"])
        else:
            covers = (g["awayScore"] + p["pts"] > g["homeScore"]) if is_dog else (g["awayScore"] - p["pts"] > g["homeScore"])
        if is_dog:
            if covers: dog_w += 1
            else: dog_l += 1
        else:
            if covers: fav_w += 1
            else: fav_l += 1

if dog_w + dog_l > 0:
    print(f"DOGS: {dog_w}-{dog_l} ({dog_w / (dog_w + dog_l) * 100:.1f}%)  n={dog_w + dog_l}")
if fav_w + fav_l > 0:
    print(f"FAVS: {fav_w}-{fav_l} ({fav_w / (fav_w + fav_l) * 100:.1f}%)  n={fav_w + fav_l}")

# Line convention check
print("\n=== LINE CONVENTION CHECK ===")
line_pos_home_win, line_pos_home_total = 0, 0
line_neg_home_win, line_neg_home_total = 0, 0
items3 = runs.items() if isinstance(runs, dict) else enumerate(runs)
for _, run in items3:
    if not run.get("games"): continue
    for g in run["games"]:
        if g.get("homeScore") is None or abs(g.get("line", 0)) < 1: continue
        home_won = g["homeScore"] > g["awayScore"]
        if g["line"] > 0:
            line_pos_home_total += 1
            if home_won: line_pos_home_win += 1
        else:
            line_neg_home_total += 1
            if home_won: line_neg_home_win += 1

if line_pos_home_total > 0:
    print(f"line > 0: home wins {line_pos_home_win}/{line_pos_home_total} ({line_pos_home_win / line_pos_home_total * 100:.1f}%)")
if line_neg_home_total > 0:
    print(f"line < 0: home wins {line_neg_home_win}/{line_neg_home_total} ({line_neg_home_win / line_neg_home_total * 100:.1f}%)")
print("\nIf line>0 means home favored, home should win >50% when line>0")
