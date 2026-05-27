#!/usr/bin/env python3
"""
sweep_bf_pctile.py — Sweep per-pitcher BF ceiling percentile vs global hard cap.

Tests BF_CAP_PCTILE {0 (off), 0.75, 0.80, 0.85, 0.90} at BF_CAP {24, 27, 30}.
Percentile=0 with cap=24 is the current baseline.

Usage:
    cd MLBstrikeouts
    python -m scripts.sweep_bf_pctile
"""

import sys, os, io, contextlib, importlib

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import defaults
import props_engine
import props_backfill
from props_backfill import _calc_units


def _run_backfill(pctile, cap):
    importlib.reload(defaults)
    defaults.BF_CAP_PCTILE = pctile
    defaults.BF_CAP = float(cap)
    importlib.reload(props_engine)
    importlib.reload(props_backfill)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        results = props_backfill.backfill()
    all_picks = results["strikeouts"]["picks"]
    actionable = [p for p in all_picks if p.get("pick") in ("OVER", "UNDER")]
    watch = [p for p in all_picks if p.get("pick") == "PASS"]
    wins = sum(1 for p in actionable if p["won"])
    losses = len(actionable) - wins
    units = _calc_units(actionable)
    ww = sum(1 for p in watch if p["won"])
    wl = len(watch) - ww
    pct = wins / (wins + losses) * 100 if (wins + losses) else 0
    wpct = ww / (ww + wl) * 100 if (ww + wl) else 0
    overs = [p for p in actionable if p["pick"] == "OVER"]
    unders = [p for p in actionable if p["pick"] == "UNDER"]
    ow = sum(1 for p in overs if p["won"])
    uw = sum(1 for p in unders if p["won"])
    return {
        "wins": wins, "losses": losses, "pct": pct, "units": units,
        "ww": ww, "wl": wl, "wpct": wpct,
        "picks": len(actionable),
        "overs": len(overs), "over_w": ow,
        "unders": len(unders), "under_w": uw,
    }


def main():
    pctiles = [0.0, 0.75, 0.80, 0.85, 0.90]
    caps = [24, 27, 30]

    print(f"\n  Sweep: BF_CAP_PCTILE × BF_CAP (hard safety net)")
    print(f"  pctile=0.0 means per-pitcher ceiling OFF (global cap only)\n")
    print(f"  {'config':<28} {'picks':>5}  {'W':>4}-{'L':<4}  {'win%':>6}  "
          f"{'units':>8}  {'OVERs':>5} {'Ow':>3}  {'UNDERs':>6} {'Uw':>3}  "
          f"{'watchW':>4}-{'watchL':<4}  {'watch%':>6}")
    print(f"  {'-' * 110}")

    orig_pctile = defaults.BF_CAP_PCTILE
    orig_cap = defaults.BF_CAP
    best = None

    try:
        for cap in caps:
            for pctile in pctiles:
                if pctile > 0 and cap == 24:
                    continue
                r = _run_backfill(pctile, cap)
                pct_label = f"{pctile:.2f}" if pctile > 0 else "off "
                label = f"pctile={pct_label}  cap={cap}"
                marker = ""
                if best is None or r["units"] > best["units"]:
                    best = {**r, "label": label}
                    marker = "  <"
                over_pct = (r["over_w"] / r["overs"] * 100) if r["overs"] else 0
                under_pct = (r["under_w"] / r["unders"] * 100) if r["unders"] else 0
                print(f"  {label:<28} {r['picks']:>5}  "
                      f"{r['wins']:>4}-{r['losses']:<4}  {r['pct']:>5.1f}%  "
                      f"{r['units']:>+7.1f}u  "
                      f"{r['overs']:>5} {r['over_w']:>3}  "
                      f"{r['unders']:>6} {r['under_w']:>3}  "
                      f"{r['ww']:>3}-{r['wl']:<3}  "
                      f"{r['wpct']:>5.1f}%{marker}")
            if cap < caps[-1]:
                print()
    finally:
        defaults.BF_CAP_PCTILE = orig_pctile
        defaults.BF_CAP = orig_cap
        print(f"\n  Restored defaults.")

    if best:
        print(f"\n  Best by units: {best['label']}  "
              f"{best['wins']}-{best['losses']} ({best['pct']:.1f}%)  "
              f"{best['units']:+.1f}u\n")


if __name__ == "__main__":
    main()
