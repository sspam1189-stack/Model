#!/usr/bin/env python3
# scripts/march_check.py — March performance + threshold comparison
import json

h = json.loads(open("data/history.json").read())
runs = h.get("runs") or []
if isinstance(runs, dict):
    runs = list(runs.values())

print("=== DAILY SPREAD PICK COUNTS (last 3 weeks) ===")
for run in runs:
    if not run.get("date") or int(run["date"]) < 20260224: continue
    if not run.get("games"): continue
    s_picks, s_wins, s_loss = 0, 0, 0
    total_games = len(run["games"])
    for g in run["games"]:
        if g.get("sPick") and g["sPick"] != "PASS" and g.get("sResult"):
            s_picks += 1
            if g["sResult"] == "WIN": s_wins += 1
            elif g["sResult"] == "LOSS": s_loss += 1
    pct = f"{s_wins / (s_wins + s_loss) * 100:.0f}" if s_picks > 0 and (s_wins + s_loss) > 0 else "-"
    print(f"  {run['date']} | {s_picks} picks / {total_games} games | {s_wins}-{s_loss} ({pct}%)")

def check_cover(g, side):
    abs_line = abs(g.get("line", 0))
    home_fav = g.get("line", 0) > 0
    if side == "home":
        return (g["homeScore"] - g["awayScore"]) - abs_line > 0 if home_fav else (g["homeScore"] - g["awayScore"]) + abs_line > 0
    else:
        return (g["awayScore"] - g["homeScore"]) + abs_line > 0 if home_fav else (g["awayScore"] - g["homeScore"]) - abs_line > 0

# Threshold comparison
w63, l63, w65, l65, w67, l67 = 0, 0, 0, 0, 0, 0
for run in runs:
    if not run.get("games"): continue
    for g in run["games"]:
        if g.get("homeScore") is None or g.get("pHomeCover") is None: continue
        if abs(g.get("line", 0)) == 0: continue
        s_diff = g.get("sDiff", 0)
        p_cover = max(g.get("pHomeCover", 0), g.get("pAwayCover", 0))
        side = "home" if g.get("pHomeCover", 0) >= g.get("pAwayCover", 0) else "away"
        abs_line = abs(g["line"])
        is_dog = (side == "home" and g["line"] < 0) or (side == "away" and g["line"] > 0)
        line_ok = True if is_dog else abs_line <= 6
        if s_diff < 3 or s_diff > 12 or not line_ok: continue
        covers = check_cover(g, side)
        if p_cover >= 0.63:
            if covers: w63 += 1
            else: l63 += 1
        if p_cover >= 0.65:
            if covers: w65 += 1
            else: l65 += 1
        if p_cover >= 0.67:
            if covers: w67 += 1
            else: l67 += 1

print("\n=== THRESHOLD COMPARISON (sDiff 3-12, favs cap6) ===")
for label, w, l in [("0.63", w63, l63), ("0.65", w65, l65), ("0.67", w67, l67)]:
    if w + l > 0:
        print(f"  {label}: {w}-{l} ({w / (w + l) * 100:.1f}%) n={w + l} | ROI: {(w * 0.91 - l) / (w + l) * 100:.1f}%")

# March only
print("\n=== MARCH ONLY (all dates >= 20260301) ===")
mw63, ml63, mw65, ml65, mw67, ml67 = 0, 0, 0, 0, 0, 0
for run in runs:
    if not run.get("date") or int(run["date"]) < 20260301: continue
    if not run.get("games"): continue
    for g in run["games"]:
        if g.get("homeScore") is None or g.get("pHomeCover") is None: continue
        if abs(g.get("line", 0)) == 0: continue
        s_diff = g.get("sDiff", 0)
        p_cover = max(g.get("pHomeCover", 0), g.get("pAwayCover", 0))
        side = "home" if g.get("pHomeCover", 0) >= g.get("pAwayCover", 0) else "away"
        abs_line = abs(g["line"])
        is_dog = (side == "home" and g["line"] < 0) or (side == "away" and g["line"] > 0)
        line_ok = True if is_dog else abs_line <= 6
        if s_diff < 3 or s_diff > 12 or not line_ok: continue
        covers = check_cover(g, side)
        if p_cover >= 0.63:
            if covers: mw63 += 1
            else: ml63 += 1
        if p_cover >= 0.65:
            if covers: mw65 += 1
            else: ml65 += 1
        if p_cover >= 0.67:
            if covers: mw67 += 1
            else: ml67 += 1

for label, w, l in [("0.63", mw63, ml63), ("0.65", mw65, ml65), ("0.67", mw67, ml67)]:
    if w + l > 0:
        print(f"  {label}: {w}-{l} ({w / (w + l) * 100:.1f}%) n={w + l}")
    else:
        print(f"  {label}: 0-0 (N/A) n=0")
