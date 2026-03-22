#!/usr/bin/env python3
# scripts/_analyze_totals.py — Analyze totals pick history
import json

h = json.loads(open("data/history.json").read())

ow, ol, op = 0, 0, 0
over_w, over_l, under_w, under_l = 0, 0, 0, 0
diff_buckets = {"0-2": [], "2-4": [], "4-6": [], "6-8": [], "8+": []}
p_ou_buckets = {"0.50-0.55": [], "0.55-0.60": [], "0.60-0.65": [], "0.65-0.70": [], "0.70+": []}
total_picks, total_pass = 0, 0

for r in (h.get("runs") or []):
    if r.get("burnIn"):
        continue
    for g in (r.get("games") or []):
        if g.get("oPick") in ("PASS", None, ""):
            total_pass += 1
            continue
        total_picks += 1
        if not g.get("oResult"):
            continue
        if g["oResult"] == "PUSH":
            op += 1
            continue
        v = 1 if g["oResult"] == "WIN" else 0
        if g["oResult"] == "WIN": ow += 1
        else: ol += 1
        if g["oPick"] == "OVER":
            if v: over_w += 1
            else: over_l += 1
        elif g["oPick"] == "UNDER":
            if v: under_w += 1
            else: under_l += 1
        td = abs(g.get("tDiff") or g.get("cleanTDiff") or 0)
        if td <= 2: diff_buckets["0-2"].append(v)
        elif td <= 4: diff_buckets["2-4"].append(v)
        elif td <= 6: diff_buckets["4-6"].append(v)
        elif td <= 8: diff_buckets["6-8"].append(v)
        else: diff_buckets["8+"].append(v)
        pou = g.get("pOU", 0)
        if pou < 0.55: p_ou_buckets["0.50-0.55"].append(v)
        elif pou < 0.60: p_ou_buckets["0.55-0.60"].append(v)
        elif pou < 0.65: p_ou_buckets["0.60-0.65"].append(v)
        elif pou < 0.70: p_ou_buckets["0.65-0.70"].append(v)
        else: p_ou_buckets["0.70+"].append(v)

print(f"TOTALS OVERALL: {ow}-{ol}-{op} ({ow / (ow + ol) * 100:.1f}%)")
print(f"Total picks made: {total_picks} / Passed: {total_pass}")
print(f"Units (flat -110): {(ow - ol * 1.1):.1f}u")
print()
if over_w + over_l > 0:
    print(f"OVER picks:  {over_w}-{over_l} ({over_w / (over_w + over_l) * 100:.1f}%)")
if under_w + under_l > 0:
    print(f"UNDER picks: {under_w}-{under_l} ({under_w / (under_w + under_l) * 100:.1f}%)")

print("\nBy total diff:")
for k, v in diff_buckets.items():
    wins = sum(v)
    t = len(v)
    if t > 0:
        print(f"  {k}: {wins}-{t - wins} ({wins / t * 100:.1f}%)")

print("\nBy pOU:")
for k, v in p_ou_buckets.items():
    wins = sum(v)
    t = len(v)
    if t > 0:
        print(f"  {k}: {wins}-{t - wins} ({wins / t * 100:.1f}%)")

over_count = sum(1 for r in (h.get("runs") or []) if not r.get("burnIn") for g in (r.get("games") or []) if g.get("oPick") == "OVER")
under_count = sum(1 for r in (h.get("runs") or []) if not r.get("burnIn") for g in (r.get("games") or []) if g.get("oPick") == "UNDER")
print(f"\nPick bias: {over_count} OVER / {under_count} UNDER")
