#!/usr/bin/env python3
# scripts/_check.py — Check NCAA model for specific team
import json

h = json.loads(open("data/history.json").read())
runs = h.get("runs") or []
today = [r for r in runs if r.get("date") == "20260315" or r.get("dateDisplay") == "2026-03-15"]
if not today:
    print("No NCAA run for today")
else:
    for r in today:
        for g in (r.get("games") or []):
            name = (g.get("home", "") + " " + g.get("away", "")).lower()
            if "purdue" in name:
                print("=== PURDUE (NCAA Model) ===")
                print(f"  {g['away']} @ {g['home']}")
                print(f"  Line: {g.get('line')}  Total: {g.get('total')}")
                print(f"  Proj: Away {g.get('aS')}  Home {g.get('hS')}  pT: {g.get('pT')}")
                print(f"  Margin: {g.get('margin')}  sDiff: {g.get('sDiff')}")
                print(f"  P(home cover): {g.get('pHomeCover')}  P(away cover): {g.get('pAwayCover')}")
                print(f"  sPick: {g.get('sPick')}  sConf: {g.get('sConf')}")
                print(f"  oPick: {g.get('oPick')}")
                features = g.get("_features") or {}
                print(f"  dNET: {features.get('dNET', 0):.1f}" if features.get("dNET") else "  dNET: N/A")
        print("\n=== All NCAA picks today ===")
        for g in (r.get("games") or []):
            if g.get("sPick") != "PASS":
                print(f"  {g.get('sPick')} (P={g.get('pCover')}) -- {g.get('away')} @ {g.get('home')}")
