#!/usr/bin/env python3
"""
sweep_kcap_2x2.py — 2x2 sweep: K_RATE_CAP_FLOOR {0.36, 0.38}
                     × cap variable {overall_k_pct, pitcher_k_rate}

Usage:
    cd MLBstrikeouts
    python -m scripts.sweep_kcap_2x2
"""

import sys, os, io, re, contextlib, importlib

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import defaults
import props_engine
import props_backfill
from props_backfill import _calc_units

ENGINE_PATH = os.path.join(os.path.dirname(__file__), "props_engine.py")
DEFAULTS_PATH = os.path.join(os.path.dirname(__file__), "defaults.py")


def _run_backfill():
    importlib.reload(defaults)
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
    avg_proj = (sum(p["proj"] for p in actionable) / len(actionable)
                if actionable else 0)
    avg_edge = (sum(abs(p["proj"] - p["line"]) for p in actionable)
                / len(actionable) if actionable else 0)
    return {
        "wins": wins, "losses": losses, "pct": pct, "units": units,
        "ww": ww, "wl": wl, "wpct": wpct,
        "avg_proj": avg_proj, "avg_edge": avg_edge,
        "picks": len(actionable),
    }


def main():
    with open(ENGINE_PATH, "r") as f:
        orig_engine = f.read()
    with open(DEFAULTS_PATH, "r") as f:
        orig_defaults = f.read()

    floors = [0.36, 0.38]
    cap_vars = [
        ("overall_k_pct", "raw season K%"),
        ("pitcher_k_rate", "hand-weighted K%"),
    ]

    print(f"\n  2x2 sweep: K_RATE_CAP_FLOOR × cap variable")
    print(f"  {'config':<35} {'picks':>5}  {'W':>4}-{'L':<4}  {'win%':>6}  "
          f"{'units':>8}  {'watchW':>4}-{'watchL':<4}  {'watch%':>6}  "
          f"{'avgK':>5}  {'avgEdge':>7}")
    print(f"  {'-' * 100}")

    best = None
    try:
        for cap_var, cap_label in cap_vars:
            patched_engine = re.sub(
                r"_k_cap_used = max\(K_RATE_CAP_FLOOR, overall_k_pct\)",
                f"_k_cap_used = max(K_RATE_CAP_FLOOR, {cap_var})",
                orig_engine,
            )
            with open(ENGINE_PATH, "w") as f:
                f.write(patched_engine)

            for floor in floors:
                patched_defaults = re.sub(
                    r"K_RATE_CAP_FLOOR = [0-9.]+",
                    f"K_RATE_CAP_FLOOR = {floor}",
                    orig_defaults,
                )
                with open(DEFAULTS_PATH, "w") as f:
                    f.write(patched_defaults)

                r = _run_backfill()
                label = f"floor={floor:.2f}  cap={cap_label}"
                marker = ""
                if best is None or r["units"] > best["units"]:
                    best = {**r, "label": label}
                    marker = "  <"
                print(f"  {label:<35} {r['picks']:>5}  "
                      f"{r['wins']:>4}-{r['losses']:<4}  {r['pct']:>5.1f}%  "
                      f"{r['units']:>+7.1f}u  {r['ww']:>3}-{r['wl']:<3}  "
                      f"{r['wpct']:>5.1f}%  {r['avg_proj']:>5.2f}  "
                      f"{r['avg_edge']:>+6.2f}{marker}")
    finally:
        with open(ENGINE_PATH, "w") as f:
            f.write(orig_engine)
        with open(DEFAULTS_PATH, "w") as f:
            f.write(orig_defaults)
        print(f"\n  Restored original files.")

    if best:
        print(f"\n  Best by units: {best['label']}  "
              f"{best['wins']}-{best['losses']} ({best['pct']:.1f}%)  "
              f"{best['units']:+.1f}u\n")


if __name__ == "__main__":
    main()
