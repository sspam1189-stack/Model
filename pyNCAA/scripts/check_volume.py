#!/usr/bin/env python3
# scripts/check_volume.py — Check pick volume and pCover distribution
import json

h = json.loads(open("data/history.json").read())

print("=== PICK VOLUME (last 20 runs) ===")
last20 = (h.get("runs") or [])[-20:]
for r in last20:
    picks = [g for g in (r.get("games") or []) if g.get("sPick") and g["sPick"] != "PASS"]
    total = len(r.get("games") or [])
    skipped = sum(1 for g in (r.get("games") or []) if g.get("status") == "SKIPPED")
    print(f"{r.get('date')} | {total} games | {len(picks)} spread picks | {skipped} skipped")

print("\n=== pCover DISTRIBUTION (all games with Kalman) ===")
all_pcovers = [g["pCover"] for r in (h.get("runs") or []) for g in (r.get("games") or []) if g.get("pCover")]
pass_pcovers = []
for r in (h.get("runs") or [])[-10:]:
    for g in (r.get("games") or []):
        if g.get("sPick") == "PASS" and g.get("pHomeCover"):
            pass_pcovers.append({"date": r.get("date"), "game": f"{g.get('away')} @ {g.get('home')}", "best": max(g.get("pHomeCover", 0), g.get("pAwayCover", 0))})

print(f"Games with pCover (picked): {len(all_pcovers)}")
if all_pcovers:
    all_pcovers.sort()
    print(f"Min: {all_pcovers[0]}, Max: {all_pcovers[-1]}, Median: {all_pcovers[len(all_pcovers) // 2]}")
    for t in [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]:
        count = sum(1 for p in all_pcovers if p >= t)
        print(f"  >= {t}: {count} picks")

print("\n=== SEASON RECORD ===")
w, l, p = 0, 0, 0
for r in (h.get("runs") or []):
    for g in (r.get("games") or []):
        if g.get("sPick") and g["sPick"] != "PASS" and g.get("sResult"):
            if g["sResult"] == "WIN": w += 1
            elif g["sResult"] == "LOSS": l += 1
            elif g["sResult"] == "PUSH": p += 1
if w + l > 0:
    print(f"Spread: {w}-{l}-{p} ({w / (w + l) * 100:.1f}%)")

print("\n=== WHAT IF LOWER THRESHOLDS? ===")
thresh_results = {t: {"w": 0, "l": 0, "p": 0, "total": 0} for t in [0.55, 0.57, 0.60, 0.63, 0.65, 0.67, 0.70]}
games_with_probs = 0
for r in (h.get("runs") or []):
    for g in (r.get("games") or []):
        if g.get("status") in ("SKIPPED", "MISSING_ODDS"): continue
        if not g.get("pHomeCover") and not g.get("pAwayCover"): continue
        games_with_probs += 1
        best_p = max(g.get("pHomeCover", 0), g.get("pAwayCover", 0))
        s_diff = g.get("sDiff", 0)
        abs_line = abs(g.get("line", 0))
        if s_diff < 3 or s_diff > 12 or abs_line == 0: continue
        side = "home" if g.get("pHomeCover", 0) >= g.get("pAwayCover", 0) else "away"
        is_dog = (side == "home" and g.get("line", 0) < 0) or (side == "away" and g.get("line", 0) > 0)
        line_ok = True if is_dog else abs_line <= 6
        if not line_ok: continue
        if not isinstance(g.get("homeScore"), (int, float)): continue
        actual_margin = g["homeScore"] - g["awayScore"]
        spread = g.get("line", 0)
        cover_margin = actual_margin + spread
        if side == "home":
            result = "WIN" if cover_margin > 0 else ("PUSH" if cover_margin == 0 else "LOSS")
        else:
            result = "WIN" if cover_margin < 0 else ("PUSH" if cover_margin == 0 else "LOSS")
        for thresh in thresh_results:
            if best_p >= thresh:
                thresh_results[thresh]["total"] += 1
                if result == "WIN": thresh_results[thresh]["w"] += 1
                elif result == "LOSS": thresh_results[thresh]["l"] += 1
                else: thresh_results[thresh]["p"] += 1

print(f"Games with probability data: {games_with_probs}")
for thresh, r in sorted(thresh_results.items()):
    pct = f"{r['w'] / (r['w'] + r['l']) * 100:.1f}" if r["w"] + r["l"] > 0 else "n/a"
    units = r["w"] - r["l"] * 1.1
    print(f"  >= {thresh}: {r['w']}-{r['l']}-{r['p']} ({pct}%) {r['total']} games, {units:.1f}u")
