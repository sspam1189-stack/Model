#!/usr/bin/env python3
# scripts/grade_only.py
# ────────────────────────────────────────────────────────────────────────────
# Adds sResult / oResult to every game in history.json WITHOUT re-analyzing.
# Picks, projections, weights, Kalman — all untouched. Just grading.
#
# Usage:
#   python scripts/grade_only.py              # grade + save
#   python scripts/grade_only.py --dry-run    # preview only
# ────────────────────────────────────────────────────────────────────────────

import json
import math
import os
import re
import shutil
import sys
from datetime import datetime

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_PATH = os.path.join(SCRIPTS_DIR, "..", "data", "history.json")
SAVE = "--dry-run" not in sys.argv


def grade_spread(g):
    s_pick = g.get("sPick")
    if not s_pick or s_pick == "PASS":
        return None
    home_score = g.get("homeScore")
    away_score = g.get("awayScore")
    if not isinstance(home_score, (int, float)) or not math.isfinite(home_score):
        return None
    if not isinstance(away_score, (int, float)) or not math.isfinite(away_score):
        return None

    m = re.match(r"(.+?)\s+([+-])(\d+(?:\.\d+)?)", s_pick)
    if not m:
        return None
    team = m.group(1).strip()
    spread = -float(m.group(3)) if m.group(2) == "-" else float(m.group(3))
    chosen_is_home = team == g.get("home")
    margin = (home_score - away_score) if chosen_is_home else (away_score - home_score)
    cover = margin + spread
    if cover > 0:
        return "WIN"
    elif cover < 0:
        return "LOSS"
    else:
        return "PUSH"


def grade_total(g):
    o_pick = g.get("oPick")
    if not o_pick or o_pick == "PASS":
        return None
    home_score = g.get("homeScore")
    away_score = g.get("awayScore")
    total = g.get("total")
    if not isinstance(home_score, (int, float)) or not math.isfinite(home_score):
        return None
    if not isinstance(away_score, (int, float)) or not math.isfinite(away_score):
        return None
    if not isinstance(total, (int, float)) or not math.isfinite(total):
        return None

    actual = home_score + away_score
    if o_pick == "OVER":
        return "WIN" if actual > total else ("LOSS" if actual < total else "PUSH")
    if o_pick == "UNDER":
        return "WIN" if actual < total else ("LOSS" if actual > total else "PUSH")
    return None


def main():
    with open(HISTORY_PATH, "r", encoding="utf-8") as f:
        store = json.load(f)

    runs = store.get("runs", [])

    graded = 0
    already_graded = 0
    no_score = 0
    no_pick = 0
    s_w = 0
    s_l = 0
    o_w = 0
    o_l = 0

    for run in runs:
        for g in run.get("games", []):
            s_pick = g.get("sPick")
            if not s_pick or s_pick == "PASS":
                no_pick += 1
                continue
            home_score = g.get("homeScore")
            away_score = g.get("awayScore")
            if not isinstance(home_score, (int, float)) or not math.isfinite(home_score):
                no_score += 1
                continue
            if not isinstance(away_score, (int, float)) or not math.isfinite(away_score):
                no_score += 1
                continue

            sr = grade_spread(g)
            or_ = grade_total(g)

            if g.get("sResult") and g.get("sResult") == sr:
                already_graded += 1
            else:
                graded += 1

            g["sResult"] = sr
            g["oResult"] = or_

            if sr == "WIN":
                s_w += 1
            if sr == "LOSS":
                s_l += 1
            if or_ == "WIN":
                o_w += 1
            if or_ == "LOSS":
                o_l += 1

    def pct(w, l):
        return f"{(100 * w / (w + l)):.1f}" if (w + l) > 0 else "0.0"

    def u(w, l):
        v = w * 0.9091 - l
        return f"+{v:.1f}" if v >= 0 else f"{v:.1f}"

    print("=== Grade Only ===")
    print(f"  Graded:  {graded} games (newly graded)")
    print(f"  Already: {already_graded} (unchanged)")
    print(f"  No score:{no_score} (future/missing)")
    print(f"  No pick: {no_pick} (PASS/no pick)")
    print()
    print(f"  Spread: {s_w}-{s_l} ({pct(s_w, s_l)}%) {u(s_w, s_l)}u")
    print(f"  Totals: {o_w}-{o_l} ({pct(o_w, o_l)}%) {u(o_w, o_l)}u")

    if SAVE:
        ts = datetime.now().isoformat().replace(":", "-").replace(".", "-")[:19]
        backup = HISTORY_PATH.replace(".json", f"_pre_grade_{ts}.json")
        shutil.copy2(HISTORY_PATH, backup)
        print(f"\n  Backup: {os.path.basename(backup)}")

        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=2)
        print(f"  Saved {graded} new grades to history.json")
    else:
        print("\n  DRY RUN — no changes. Remove --dry-run to save.")


if __name__ == "__main__":
    main()
