#!/usr/bin/env python3
# scripts/_sweep_recent.py — Compare recent vs older performance
import json

store = json.loads(open("data/history.json").read())
runs = store.get("runs") or []

cutoff = "20260303"
recent_runs = [r for r in runs if r.get("date", "") >= cutoff]
older_runs = [r for r in runs if r.get("date", "") < cutoff]

print(f"Recent runs (>= {cutoff}): {len(recent_runs)} days")
print(f"Older runs (< {cutoff}): {len(older_runs)} days\n")

def grade_spread(g, side):
    margin = g["homeScore"] - g["awayScore"]
    v = margin - g["line"]
    if side == "home": return "WIN" if v > 0 else ("PUSH" if v == 0 else "LOSS")
    return "WIN" if v < 0 else ("PUSH" if v == 0 else "LOSS")

def sweep(run_set, label):
    games = []
    for r in run_set:
        for g in (r.get("games") or []):
            if g.get("homeScore") is None or g.get("awayScore") is None: continue
            if g.get("line") is None and g.get("line") != 0: continue
            if g.get("status") in ("SKIPPED", "MISSING_STATS"): continue
            if not g.get("pHomeCover") and not g.get("pAwayCover"): continue
            games.append(g)
    print(f"=== {label} ({len(games)} games) ===")
    print("pCover | W    - L   - P | Pct%  | Picks/Day | Units")
    days = len(run_set) or 1
    for p_thresh in [0.55, 0.58, 0.60, 0.62, 0.65, 0.67]:
        w, l, p = 0, 0, 0
        for g in games:
            pH, pA = g.get("pHomeCover", 0), g.get("pAwayCover", 0)
            best_p = max(pH, pA)
            side = "home" if pH >= pA else "away"
            abs_line = abs(g.get("line", 0))
            is_dog = (side == "home" and g["line"] < 0) or (side == "away" and g["line"] > 0)
            line_ok = True if is_dog else abs_line <= 6
            if best_p >= p_thresh and line_ok and abs_line > 0:
                result = grade_spread(g, side)
                if result == "WIN": w += 1
                elif result == "LOSS": l += 1
                else: p += 1
        total = w + l
        pct = f"{w / total * 100:.1f}" if total > 0 else "n/a"
        units = f"{(w - l * 1.1):.1f}"
        ppd = f"{(w + l + p) / days:.1f}"
        print(f"  {p_thresh:.2f}  | {w:>4}-{l:>4}-{p:>2} | {pct:>5} | {ppd:>9} | {units:>6}")
    print()

sweep(recent_runs, "LAST 2 WEEKS")
sweep(older_runs, "BEFORE LAST 2 WEEKS")
sweep(runs, "FULL SEASON")
