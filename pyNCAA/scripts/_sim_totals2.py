#!/usr/bin/env python3
# scripts/_sim_totals2.py — Totals simulation sweep v2 (targeting 60%+)
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
            "total": g.get("total", 0), "line": abs(g.get("line", 0)),
        })

def test(label, filter_fn):
    filtered = [p for p in picks if filter_fn(p)]
    w = sum(p["result"] for p in filtered)
    l = len(filtered) - w
    pct = f"{w / len(filtered) * 100:.1f}" if filtered else "n/a"
    units = f"{(w - l * 1.1):.1f}"
    roi = f"{float(units) / len(filtered) * 100:.1f}" if filtered else "n/a"
    print(f"{label:<55} {w}-{l!s:<10} {pct}%  {units}u  ROI: {roi}%")

print("=== Targeting 60%+ ===\n")
test("pOU>=0.75 AND total<=150", lambda p: p["pOU"] >= 0.75 and p["total"] <= 150)
test("pOU>=0.75 AND total<=155", lambda p: p["pOU"] >= 0.75 and p["total"] <= 155)
test("pOU>=0.78", lambda p: p["pOU"] >= 0.78)
test("pOU>=0.80", lambda p: p["pOU"] >= 0.80)
test("tDiff>=10 AND pOU>=0.70", lambda p: p["tDiff"] >= 10 and p["pOU"] >= 0.70)
test("tDiff>=12", lambda p: p["tDiff"] >= 12)

print("\n=== OVER only combos ===\n")
test("OVER pOU>=0.70 total<=155", lambda p: p["side"] == "OVER" and p["pOU"] >= 0.70 and p["total"] <= 155)
test("OVER pOU>=0.65 total<=150", lambda p: p["side"] == "OVER" and p["pOU"] >= 0.65 and p["total"] <= 150)

print("\n=== UNDER only combos ===\n")
test("UNDER pOU>=0.75", lambda p: p["side"] == "UNDER" and p["pOU"] >= 0.75)
test("UNDER pOU>=0.78", lambda p: p["side"] == "UNDER" and p["pOU"] >= 0.78)
test("UNDER tDiff>=10", lambda p: p["side"] == "UNDER" and p["tDiff"] >= 10)

print("\n=== Split strategy: different thresholds per side ===\n")
test("OVER pOU>=0.65 OR UNDER pOU>=0.75", lambda p: (p["side"] == "OVER" and p["pOU"] >= 0.65) or (p["side"] == "UNDER" and p["pOU"] >= 0.75))
test("OVER pOU>=0.70 OR UNDER pOU>=0.78", lambda p: (p["side"] == "OVER" and p["pOU"] >= 0.70) or (p["side"] == "UNDER" and p["pOU"] >= 0.78))

print("\n=== Best combo: asymmetric + total filter ===\n")
test("(OVER pOU>=0.65) OR (UNDER pOU>=0.75 total<=155)", lambda p: (p["side"] == "OVER" and p["pOU"] >= 0.65) or (p["side"] == "UNDER" and p["pOU"] >= 0.75 and p["total"] <= 155))
test("(OVER pOU>=0.65) OR (UNDER pOU>=0.78)", lambda p: (p["side"] == "OVER" and p["pOU"] >= 0.65) or (p["side"] == "UNDER" and p["pOU"] >= 0.78))
