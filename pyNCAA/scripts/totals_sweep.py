#!/usr/bin/env python3
# scripts/totals_sweep.py — Full totals filter combination sweep
import json

h = json.loads(open("data/history.json").read())
runs = h.get("runs") or []
if isinstance(runs, dict):
    runs = list(runs.values())

games = []
for run in runs:
    if not run.get("games"): continue
    for g in run["games"]:
        if g.get("homeScore") is None or g.get("pOver") is None or g.get("pUnder") is None: continue
        if not g.get("total") or g["total"] == 0: continue
        actual = g["homeScore"] + g["awayScore"]
        p_over, p_under = g["pOver"], g["pUnder"]
        best_p = max(p_over, p_under)
        side = "OVER" if p_over >= p_under else "UNDER"
        t_diff = abs(g.get("tDiff", 0))
        covers = (actual > g["total"]) if side == "OVER" else (actual < g["total"])
        games.append({"side": side, "bestP": best_p, "total": g["total"], "tDiff": t_diff, "covers": covers})
print(f"Total games with totals data: {len(games)}")

# Over sweep
print("\n=== OVER FILTER SWEEP ===")
for p_thresh in [0.60, 0.63, 0.65, 0.67, 0.70, 0.75, 0.80]:
    for total_cap in [140, 145, 150, 155, 160, 999]:
        w, l = 0, 0
        for g in games:
            if g["side"] != "OVER" or g["bestP"] < p_thresh or g["total"] > total_cap: continue
            if g["covers"]: w += 1
            else: l += 1
        if w + l < 15: continue
        n = w + l
        print(f"  OVER p>={p_thresh:.2f} total<={total_cap}: {w}-{l} ({w / n * 100:.1f}%) n={n} ROI:{(w * 0.91 - l) / n * 100:.1f}%")

# Under sweep
print("\n=== UNDER FILTER SWEEP ===")
for p_thresh in [0.60, 0.63, 0.65, 0.67, 0.70, 0.75, 0.80, 0.85]:
    w, l = 0, 0
    for g in games:
        if g["side"] != "UNDER" or g["bestP"] < p_thresh: continue
        if g["covers"]: w += 1
        else: l += 1
    if w + l < 10: continue
    n = w + l
    print(f"  UNDER p>={p_thresh:.2f}: {w}-{l} ({w / n * 100:.1f}%) n={n} ROI:{(w * 0.91 - l) / n * 100:.1f}%")

# Top combined
print("\n=== TOP COMBINED TOTALS FILTERS (min 50 picks) ===")
combos = []
for over_p in [0.60, 0.63, 0.65, 0.67, 0.70]:
    for total_cap in [140, 145, 150, 160, 999]:
        for over_td in [0, 3, 5, 7]:
            for under_p in [0.65, 0.70, 0.75, 0.80, 0.85]:
                for under_td in [0, 3, 5, 7]:
                    w, l = 0, 0
                    for g in games:
                        over_q = g["side"] == "OVER" and g["bestP"] >= over_p and g["total"] <= total_cap and g["tDiff"] >= over_td
                        under_q = g["side"] == "UNDER" and g["bestP"] >= under_p and g["tDiff"] >= under_td
                        if not over_q and not under_q: continue
                        if g["covers"]: w += 1
                        else: l += 1
                    n = w + l
                    if n < 50: continue
                    pct = w / n * 100
                    roi = (w * 0.91 - l) / n * 100
                    combos.append({"overP": over_p, "totalCap": total_cap, "overTD": over_td, "underP": under_p, "underTD": under_td, "w": w, "l": l, "n": n, "pct": pct, "roi": roi})
combos.sort(key=lambda c: c["roi"], reverse=True)
for c in combos[:20]:
    print(f"  O:p>={c['overP']:.2f} t<={c['totalCap']} td>={c['overTD']} | U:p>={c['underP']:.2f} td>={c['underTD']} | {c['w']}-{c['l']} ({c['pct']:.1f}%) n={c['n']} ROI:{c['roi']:.1f}%")
