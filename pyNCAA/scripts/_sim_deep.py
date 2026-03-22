#!/usr/bin/env python3
# scripts/_sim_deep.py — Deep simulation: why are favorites bad?
import json

h = json.loads(open("data/history.json").read())
picks = []
for r in (h.get("runs") or []):
    if r.get("burnIn"):
        continue
    for g in (r.get("games") or []):
        if not g.get("sPick") or g["sPick"] == "PASS":
            continue
        if g.get("sResult") not in ("WIN", "LOSS"):
            continue
        picks.append({
            "result": 1 if g["sResult"] == "WIN" else 0,
            "pCover": g.get("pCover", 0), "sDiff": abs(g.get("sDiff", 0)),
            "line": abs(g.get("line", 0)), "conf": g.get("sConf", "low"),
            "isFav": "-" in (g.get("sPick") or ""), "margin": g.get("margin", 0),
        })

def test(label, filter_fn):
    filtered = [p for p in picks if filter_fn(p)]
    w = sum(p["result"] for p in filtered)
    l = len(filtered) - w
    pct = f"{w / len(filtered) * 100:.1f}" if filtered else "n/a"
    units = f"{(w - l * 1.1):.1f}"
    roi = f"{float(units) / len(filtered) * 100:.1f}" if filtered else "n/a"
    print(f"{label:<55} {w}-{l!s:<10} {pct}%  {units}u  ROI: {roi}%")

print("=== WHY ARE FAVORITES BAD? ===\n")
test("Favs ALL", lambda p: p["isFav"])
test("Favs line <= 3", lambda p: p["isFav"] and p["line"] <= 3)
test("Favs line 3-6", lambda p: p["isFav"] and 3 <= p["line"] <= 6)
test("Favs line 6-10", lambda p: p["isFav"] and 6 <= p["line"] <= 10)
test("Favs pCover >= 0.65", lambda p: p["isFav"] and p["pCover"] >= 0.65)
test("Favs pCover >= 0.67", lambda p: p["isFav"] and p["pCover"] >= 0.67)
test("Favs pCover >= 0.70", lambda p: p["isFav"] and p["pCover"] >= 0.70)
test("Favs sDiff >= 5", lambda p: p["isFav"] and p["sDiff"] >= 5)
test("Favs sDiff >= 6", lambda p: p["isFav"] and p["sDiff"] >= 6)
test("Favs line<=5 pCover>=0.65", lambda p: p["isFav"] and p["line"] <= 5 and p["pCover"] >= 0.65)
test("Favs line<=5 pCover>=0.67", lambda p: p["isFav"] and p["line"] <= 5 and p["pCover"] >= 0.67)

print("\n=== DOGS DETAIL ===\n")
test("Dogs ALL", lambda p: not p["isFav"])
test("Dogs line <= 3", lambda p: not p["isFav"] and p["line"] <= 3)
test("Dogs line 3-6", lambda p: not p["isFav"] and 3 <= p["line"] <= 6)
test("Dogs line 6-10", lambda p: not p["isFav"] and 6 <= p["line"] <= 10)
test("Dogs pCover >= 0.65", lambda p: not p["isFav"] and p["pCover"] >= 0.65)
test("Dogs pCover >= 0.67", lambda p: not p["isFav"] and p["pCover"] >= 0.67)

print("\n=== BOTH SIDES -- pCover + sDiff combos ===\n")
test("pCover>=0.65 sDiff>=5", lambda p: p["pCover"] >= 0.65 and p["sDiff"] >= 5)
test("pCover>=0.65 sDiff>=6", lambda p: p["pCover"] >= 0.65 and p["sDiff"] >= 6)
test("pCover>=0.67 sDiff>=4", lambda p: p["pCover"] >= 0.67 and p["sDiff"] >= 4)
test("pCover>=0.67 sDiff>=5", lambda p: p["pCover"] >= 0.67 and p["sDiff"] >= 5)
test("pCover>=0.67 sDiff>=6", lambda p: p["pCover"] >= 0.67 and p["sDiff"] >= 6)
test("pCover>=0.70 sDiff>=4", lambda p: p["pCover"] >= 0.70 and p["sDiff"] >= 4)

print("\n=== REQUIRE HIGHER pCover FOR FAVS ===\n")
test("Dogs pCover>=0.63 OR Favs pCover>=0.67", lambda p: (not p["isFav"] and p["pCover"] >= 0.63) or (p["isFav"] and p["pCover"] >= 0.67))
test("Dogs pCover>=0.63 OR Favs pCover>=0.70", lambda p: (not p["isFav"] and p["pCover"] >= 0.63) or (p["isFav"] and p["pCover"] >= 0.70))
test("Dogs pCover>=0.65 OR Favs pCover>=0.67", lambda p: (not p["isFav"] and p["pCover"] >= 0.65) or (p["isFav"] and p["pCover"] >= 0.67))
test("Dogs pCover>=0.65 OR Favs pCover>=0.70", lambda p: (not p["isFav"] and p["pCover"] >= 0.65) or (p["isFav"] and p["pCover"] >= 0.70))

print("\n=== MARGIN-BASED (model disagreement size) ===\n")
test("abs(margin) >= 3", lambda p: abs(p["margin"]) >= 3)
test("abs(margin) >= 4", lambda p: abs(p["margin"]) >= 4)
test("abs(margin) >= 5", lambda p: abs(p["margin"]) >= 5)
test("abs(margin) >= 3 AND pCover>=0.65", lambda p: abs(p["margin"]) >= 3 and p["pCover"] >= 0.65)
test("abs(margin) >= 4 AND pCover>=0.65", lambda p: abs(p["margin"]) >= 4 and p["pCover"] >= 0.65)
