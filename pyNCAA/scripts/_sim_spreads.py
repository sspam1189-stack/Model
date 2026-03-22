#!/usr/bin/env python3
# scripts/_sim_spreads.py — Spread simulation sweep
import json

h = json.loads(open("data/history.json").read())
picks = []
for r in (h.get("runs") or []):
    if r.get("burnIn"): continue
    for g in (r.get("games") or []):
        if not g.get("sPick") or g["sPick"] == "PASS": continue
        if g.get("sResult") not in ("WIN", "LOSS"): continue
        picks.append({
            "result": 1 if g["sResult"] == "WIN" else 0,
            "pCover": g.get("pCover", 0), "sDiff": abs(g.get("sDiff", 0)),
            "line": abs(g.get("line", 0)), "conf": g.get("sConf", "low"),
            "isFav": "-" in (g.get("sPick") or ""),
        })

print(f"Total graded spread picks: {len(picks)}\n")

def test(label, filter_fn):
    filtered = [p for p in picks if filter_fn(p)]
    w = sum(p["result"] for p in filtered)
    l = len(filtered) - w
    pct = f"{w / len(filtered) * 100:.1f}" if filtered else "n/a"
    units = f"{(w - l * 1.1):.1f}"
    roi = f"{float(units) / len(filtered) * 100:.1f}" if filtered else "n/a"
    print(f"{label:<50} {w}-{l!s:<10} {pct}%  {units}u  ROI: {roi}%")

print("=== Current (baseline) ===")
test("ALL picks", lambda p: True)
print("\n=== By pCover ===")
for t in [0.58, 0.60, 0.62, 0.63, 0.65, 0.67, 0.70]:
    test(f"pCover >= {t}", lambda p, t=t: p["pCover"] >= t)
print("\n=== By sDiff ===")
for t in [2, 3, 4, 5, 6]:
    test(f"sDiff >= {t}", lambda p, t=t: p["sDiff"] >= t)
print("\n=== By spread size ===")
for lo, hi in [(0,5),(0,8),(0,10),(0,12),(0,15),(3,10),(3,12)]:
    test(f"line {lo}-{hi}" if lo else f"line <= {hi}", lambda p, lo=lo, hi=hi: lo <= p["line"] <= hi)
print("\n=== Fav vs Dog ===")
test("Favorites only", lambda p: p["isFav"])
test("Underdogs only", lambda p: not p["isFav"])
test("Dogs pCover>=0.63", lambda p: not p["isFav"] and p["pCover"] >= 0.63)
test("Dogs pCover>=0.65", lambda p: not p["isFav"] and p["pCover"] >= 0.65)
test("Favs pCover>=0.65", lambda p: p["isFav"] and p["pCover"] >= 0.65)
print("\n=== Combined filters ===")
test("pCover>=0.63 AND sDiff>=3", lambda p: p["pCover"] >= 0.63 and p["sDiff"] >= 3)
test("pCover>=0.63 AND line<=10", lambda p: p["pCover"] >= 0.63 and p["line"] <= 10)
test("pCover>=0.63 AND line<=12", lambda p: p["pCover"] >= 0.63 and p["line"] <= 12)
test("pCover>=0.65 AND sDiff>=3", lambda p: p["pCover"] >= 0.65 and p["sDiff"] >= 3)
test("pCover>=0.65 AND line<=10", lambda p: p["pCover"] >= 0.65 and p["line"] <= 10)
test("pCover>=0.65 AND line<=12", lambda p: p["pCover"] >= 0.65 and p["line"] <= 12)
test("pCover>=0.65 AND sDiff>=4", lambda p: p["pCover"] >= 0.65 and p["sDiff"] >= 4)
print("\n=== Dogs + combined ===")
test("Dogs pCover>=0.63 line<=10", lambda p: not p["isFav"] and p["pCover"] >= 0.63 and p["line"] <= 10)
test("Dogs pCover>=0.63 line<=12", lambda p: not p["isFav"] and p["pCover"] >= 0.63 and p["line"] <= 12)
test("Dogs pCover>=0.65 line<=12", lambda p: not p["isFav"] and p["pCover"] >= 0.65 and p["line"] <= 12)
test("Dogs pCover>=0.65 line<=10", lambda p: not p["isFav"] and p["pCover"] >= 0.65 and p["line"] <= 10)
test("Dogs pCover>=0.67 line<=12", lambda p: not p["isFav"] and p["pCover"] >= 0.67 and p["line"] <= 12)
