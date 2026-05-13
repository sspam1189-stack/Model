#!/usr/bin/env python3
"""
sweep_krate_cap.py — sweep the upper cap on expected_k_rate in props_engine.

Currently capped at 0.50 (props_engine.py line 324). Diagnostic showed
K/BF projections >= 0.30 systematically overshoot actuals by ~5-13%.
This sweep monkey-patches the cap and tests calibration / units.

Usage:
    cd MLBstrikeouts
    python -m scripts.sweep_krate_cap
"""

import sys, os, io, contextlib
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import defaults
import props_engine
from props_backfill import backfill, _calc_units

# Patch props_engine.project_pitcher_props to apply a configurable cap
_KRATE_CAP = 0.50

_orig_min = min  # not needed but for clarity

def main():
    caps = [0.28, 0.30, 0.32, 0.34, 0.35, 0.36, 0.38, 0.40, 0.45, 0.50]
    print(f"\n  Sweeping expected_k_rate upper cap over {caps}")
    print(f"  (current code: 0.50)\n")
    print(f"  {'cap':>5}  {'wins':>4}-{'L':<4}  {'win%':>6}  {'units':>8}   "
          f"{'watchW':>4}-{'watchL':<5}  {'watch%':>6}  {'avgK':>5}  {'avgEdge':>7}")
    print(f"  {'-'*82}")

    # Patch source line via runtime patch
    import re
    src_path = os.path.join(os.path.dirname(__file__), 'props_engine.py')
    with open(src_path, 'r', encoding='utf-8') as f:
        orig_src = f.read()
    pattern = r"expected_k_rate = max\(0\.05, min\([0-9.]+, expected_k_rate\)\)"
    assert re.search(pattern, orig_src), "cap line not found in props_engine.py"

    best = None
    try:
        for cap in caps:
            new_src = re.sub(pattern,
                             f"expected_k_rate = max(0.05, min({cap}, expected_k_rate))",
                             orig_src)
            with open(src_path, 'w', encoding='utf-8') as f:
                f.write(new_src)
            # reload
            import importlib
            importlib.reload(props_engine)
            import props_backfill
            importlib.reload(props_backfill)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                results = props_backfill.backfill()
            all_picks = results["strikeouts"]["picks"]
            actionable = [p for p in all_picks if p.get("pick") in ("OVER","UNDER")]
            watch = [p for p in all_picks if p.get("pick") == "PASS"]
            wins = sum(1 for p in actionable if p["won"])
            losses = len(actionable) - wins
            units = props_backfill._calc_units(actionable)
            ww = sum(1 for p in watch if p["won"])
            wl = len(watch) - ww
            pct = wins/(wins+losses)*100 if (wins+losses) else 0
            wpct = ww/(ww+wl)*100 if (ww+wl) else 0
            avg_proj = sum(p['proj'] for p in actionable)/len(actionable) if actionable else 0
            avg_edge = sum(abs(p['proj']-p['line']) for p in actionable)/len(actionable) if actionable else 0
            marker = ""
            if best is None or units > best[1]:
                best = (cap, units, wins, losses, pct, ww, wl, wpct)
                marker = "  <"
            print(f"  {cap:>5.2f}  {wins:>4}-{losses:<4}  {pct:>5.1f}%  "
                  f"{units:>+7.1f}u   {ww:>3}-{wl:<3}  {wpct:>5.1f}%  "
                  f"{avg_proj:>5.2f}  {avg_edge:>+6.2f}{marker}")
    finally:
        with open(src_path, 'w', encoding='utf-8') as f:
            f.write(orig_src)
        print(f"\n  Restored original cap.")

    if best:
        print(f"\n  Best by units: cap={best[0]:.2f}  {best[2]}-{best[3]} ({best[4]:.1f}%)  {best[1]:+.1f}u")

if __name__ == "__main__":
    main()
