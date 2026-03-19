#!/usr/bin/env python3
# scripts/_check_grading.py — Trace grading logic on specific games
import json

store = json.loads(open("data/history.json").read())
runs = store.get("runs") or []

for r in runs:
    for g in (r.get("games") or []):
        if r.get("date") == "20260102" and g.get("away") and "Michigan" in g["away"] and g.get("home") and "USC" in g["home"]:
            print("Found game:", json.dumps(g, indent=2))
            print("\n--- Grading analysis ---")
            print(f"away: {g['away']} | home: {g['home']}")
            print(f"awayScore: {g.get('awayScore')} | homeScore: {g.get('homeScore')}")
            print(f"line: {g.get('line')} (positive = home is dog)")
            abs_line = abs(g.get("line", 0))
            home_dog = g.get("line", 0) > 0
            print(f"homeDog: {home_dog} | absLine: {abs_line}")
            if home_dog:
                margin = g["homeScore"] - g["awayScore"]
                v = margin + g["line"]
                print(f"Home margin: {margin} + line: {g['line']} = spreadResult: {v}")
                print(f"Home covers? {v > 0}")
            else:
                margin = g["homeScore"] - g["awayScore"]
                v = margin + g["line"]
                print(f"Home margin: {margin} + line: {g['line']} = v: {v}")
                print(f"Away covers? {v < 0}")

print("\n\n=== VERIFY WITH ACTUAL GRADED PICKS ===")
checked = 0
for r in runs:
    for g in (r.get("games") or []):
        if checked >= 5:
            break
        if not g.get("sPick") or g["sPick"] == "PASS":
            continue
        if not g.get("result"):
            continue
        print(f"\n{r.get('date')}: {g['away']} @ {g['home']}")
        print(f"  sPick: {g['sPick']} | result: {g['result']}")
        print(f"  line: {g.get('line')} | score: {g.get('awayScore')}-{g.get('homeScore')}")
        print(f"  pHomeCover: {g.get('pHomeCover')} | pAwayCover: {g.get('pAwayCover')}")
        checked += 1
