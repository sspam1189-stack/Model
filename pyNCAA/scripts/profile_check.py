#!/usr/bin/env python3
# scripts/profile_check.py — Performance by game profile (power conf vs mid vs small)
import json

h = json.loads(open("data/history.json").read())
runs = h.get("runs") or []
if isinstance(runs, dict):
    runs = list(runs.values())

POWER = {
    "Alabama","Arkansas","Auburn","Florida","Georgia","Kentucky","LSU","Mississippi St.",
    "Missouri","Oklahoma","South Carolina","Tennessee","Texas","Texas A&M","Vanderbilt","Ole Miss",
    "Arizona","Arizona St.","BYU","Baylor","Cincinnati","Colorado","Houston","Iowa St.",
    "Kansas","Kansas St.","Oklahoma St.","Oregon St.","TCU","Texas Tech","UCF","Utah","West Virginia",
    "Illinois","Indiana","Iowa","Maryland","Michigan","Michigan St.","Minnesota","Nebraska",
    "Northwestern","Ohio St.","Oregon","Penn St.","Purdue","Rutgers","UCLA","USC","Washington","Wisconsin",
    "Boston College","Clemson","Duke","Florida St.","Georgia Tech","Louisville","Miami FL",
    "NC State","North Carolina","Notre Dame","Pittsburgh","SMU","Stanford","Syracuse","Virginia",
    "Virginia Tech","Wake Forest","California",
    "Butler","UConn","Creighton","DePaul","Georgetown","Marquette","Providence",
    "Seton Hall","St. John's","Villanova","Xavier",
}

def check_cover(g, side):
    abs_line = abs(g.get("line", 0))
    home_fav = g.get("line", 0) < 0
    if side == "home":
        return (g["homeScore"] - g["awayScore"]) - abs_line > 0 if home_fav else (g["homeScore"] - g["awayScore"]) + abs_line > 0
    else:
        return (g["awayScore"] - g["homeScore"]) + abs_line > 0 if home_fav else (g["awayScore"] - g["homeScore"]) - abs_line > 0

power_w, power_l, mid_w, mid_l, low_w, low_l = 0, 0, 0, 0, 0, 0

for run in runs:
    if not run.get("games"): continue
    for g in run["games"]:
        if g.get("homeScore") is None or g.get("pHomeCover") is None: continue
        if abs(g.get("line", 0)) == 0: continue
        s_diff = g.get("sDiff", 0)
        p_cover = max(g.get("pHomeCover", 0), g.get("pAwayCover", 0))
        side = "home" if g.get("pHomeCover", 0) >= g.get("pAwayCover", 0) else "away"
        abs_line = abs(g["line"])
        is_dog = (side == "home" and g["line"] > 0) or (side == "away" and g["line"] < 0)
        line_ok = True if is_dog else abs_line <= 6
        if p_cover < 0.67 or s_diff < 3 or s_diff > 12 or not line_ok: continue
        covers = check_cover(g, side)
        home_power = g.get("home") in POWER
        away_power = g.get("away") in POWER
        if home_power and away_power:
            if covers: power_w += 1
            else: power_l += 1
        elif home_power or away_power:
            if covers: mid_w += 1
            else: mid_l += 1
        else:
            if covers: low_w += 1
            else: low_l += 1

print("=== SPREAD PERFORMANCE BY GAME PROFILE ===")
for label, w, l in [("Power vs Power", power_w, power_l), ("Power vs Mid", mid_w, mid_l), ("Small vs Small", low_w, low_l)]:
    n = w + l
    pct = f"{w / n * 100:.1f}" if n > 0 else "N/A"
    print(f"{label}: {w}-{l} ({pct}%) n={n}")

print("\n=== BY LINE SIZE (proxy for market sharpness) ===")
for lo, hi, label in [(0,3,"0-3 (sharp)"),(3,6,"3-6"),(6,10,"6-10"),(10,20,"10-20 (soft)"),(20,99,"20+ (very soft)")]:
    w, l = 0, 0
    for run in runs:
        if not run.get("games"): continue
        for g in run["games"]:
            if g.get("homeScore") is None or g.get("pHomeCover") is None: continue
            abs_line = abs(g.get("line", 0))
            if abs_line == 0 or abs_line < lo or abs_line >= hi: continue
            s_diff = g.get("sDiff", 0)
            p_cover = max(g.get("pHomeCover", 0), g.get("pAwayCover", 0))
            side = "home" if g.get("pHomeCover", 0) >= g.get("pAwayCover", 0) else "away"
            is_dog = (side == "home" and g["line"] > 0) or (side == "away" and g["line"] < 0)
            line_ok = True if is_dog else abs_line <= 6
            if p_cover < 0.67 or s_diff < 3 or s_diff > 12 or not line_ok: continue
            covers = check_cover(g, side)
            if covers: w += 1
            else: l += 1
    if w + l == 0: continue
    print(f"  Line {label}: {w}-{l} ({w / (w + l) * 100:.1f}%) n={w + l}")
