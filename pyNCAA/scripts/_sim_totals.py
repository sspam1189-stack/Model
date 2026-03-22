#!/usr/bin/env python3
# scripts/_sim_totals.py — Totals simulation sweep
import json

h = json.loads(open("data/history.json").read())
picks = []
for r in (h.get("runs") or []):
    if r.get("burnIn"): continue
    for g in (r.get("games") or []):
        if not g.get("oPick") or g["oPick"] == "PASS": continue
        if g.get("oResult") not in ("WIN", "LOSS"): continue
        picks.append({
            "side": g["oPick"], "result": 1 if g["oResult"] == "WIN" else 0,
            "pOU": g.get("pOU", 0), "tDiff": abs(g.get("tDiff") or g.get("cleanTDiff") or 0),
            "conf": g.get("oConf", "low"), "total": g.get("total", 0),
            "sDiff": abs(g.get("sDiff", 0)), "line": abs(g.get("line", 0)),
        })
print(f"Total graded picks: {len(picks)}\n")

def test(label, filter_fn):
    filtered = [p for p in picks if filter_fn(p)]
    w = sum(p["result"] for p in filtered)
    l = len(filtered) - w
    pct = f"{w / len(filtered) * 100:.1f}" if filtered else "n/a"
    units = f"{(w - l * 1.1):.1f}"
    roi = f"{float(units) / len(filtered) * 100:.1f}" if filtered else "n/a"
    print(f"{label:<45} {w}-{l} ({pct}%) {units}u  ROI: {roi}%")

print("=== pOU threshold ===")
for t in [0.65, 0.68, 0.70, 0.72, 0.75]: test(f"pOU >= {t}", lambda p, t=t: p["pOU"] >= t)
print("\n=== tDiff threshold ===")
for t in [4, 5, 6, 8, 10]: test(f"tDiff >= {t}", lambda p, t=t: p["tDiff"] >= t)
print("\n=== Combined: pOU + tDiff ===")
for pt in [0.65, 0.68, 0.70, 0.72, 0.75]:
    for td in [5, 6, 8]:
        test(f"pOU>={pt} AND tDiff>={td}", lambda p, pt=pt, td=td: p["pOU"] >= pt and p["tDiff"] >= td)
print("\n=== By side ===")
test("UNDER only", lambda p: p["side"] == "UNDER")
test("OVER only", lambda p: p["side"] == "OVER")
test("UNDER pOU>=0.70", lambda p: p["side"] == "UNDER" and p["pOU"] >= 0.70)
test("OVER pOU>=0.70", lambda p: p["side"] == "OVER" and p["pOU"] >= 0.70)
print("\n=== Elite only ===")
test("elite conf only", lambda p: p["conf"] == "elite")
test("elite + pOU>=0.70", lambda p: p["conf"] == "elite" and p["pOU"] >= 0.70)
print("\n=== Spread size filter ===")
test("spread <= 10", lambda p: p["line"] <= 10)
test("spread <= 15", lambda p: p["line"] <= 15)
print("\n=== Total line filter ===")
test("total >= 130", lambda p: p["total"] >= 130)
test("total >= 140", lambda p: p["total"] >= 140)
test("total <= 140", lambda p: p["total"] <= 140)
test("total <= 150", lambda p: p["total"] <= 150)
test("total 130-155", lambda p: 130 <= p["total"] <= 155)
