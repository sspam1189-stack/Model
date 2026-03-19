#!/usr/bin/env python3
# scripts/recalculate.py
# Re-runs every game in history.json through the CURRENT model engine
# using CACHED stats only -- no API calls.
#
# Usage:
#   python scripts/recalculate.py              # recalculate + save (default)
#   python scripts/recalculate.py --dry-run    # preview changes without saving

import os
import sys
import json
import re
import math
import shutil
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from model_engine import load_defaults, get_avgs, analyze_game
from self_tune import tune_weights, compute_residual_var
from kalman_state import (
    initialize_kalman, apply_daily_drift, batch_update,
    save_kalman_state, prune_processed_games, kalman_summary,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "..", "data")
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")
CACHE_DIR = os.path.join(DATA_DIR, "stats_cache")

SAVE = "--dry-run" not in sys.argv


# --- Inline Grading ---

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
    return "PUSH"


def grade_total(g):
    o_pick = g.get("oPick")
    if not o_pick or o_pick == "PASS":
        return None
    home_score = g.get("homeScore")
    away_score = g.get("awayScore")
    if not isinstance(home_score, (int, float)) or not math.isfinite(home_score):
        return None
    if not isinstance(away_score, (int, float)) or not math.isfinite(away_score):
        return None
    total = g.get("total")
    if not isinstance(total, (int, float)) or not math.isfinite(total):
        return None

    actual = home_score + away_score
    if o_pick == "OVER":
        return "WIN" if actual > total else ("LOSS" if actual < total else "PUSH")
    if o_pick == "UNDER":
        return "WIN" if actual < total else ("LOSS" if actual > total else "PUSH")
    return None


# --- Per-Date Stats from Disk Cache Only ---

def cache_file(date_str):
    return os.path.join(CACHE_DIR, date_str + ".json")


def get_stats_for_date(date_str):
    cf = cache_file(date_str)
    if not os.path.exists(cf):
        return None
    with open(cf, "r") as f:
        raw = json.load(f)
    if "season" in raw:
        return raw
    return {"season": raw, "last10": None, "home": None, "away": None}


# --- Main ---

def main():
    print("==========================================================")
    print("  RECALCULATE - Replay history with cached stats (no API)")
    print("==========================================================")
    print("  Mode:  " + ("SAVE" if SAVE else "DRY RUN (--dry-run flag detected)"))
    print()

    # 1. Load history
    if not os.path.exists(HISTORY_PATH):
        print("  ERROR: " + HISTORY_PATH + " not found")
        sys.exit(1)
    with open(HISTORY_PATH, "r") as f:
        store = json.load(f)
    runs = store.get("runs", [])
    print(f"  {len(runs)} runs in history.json")

    # 2. Weights (start from defaults, evolve forward)
    defaults = load_defaults()
    dw = defaults["DEFAULT_W"]
    print(f"  Starting weights: sprHigh={dw.get('sprHigh')} ouHigh={dw.get('ouHigh')} wNET={dw.get('wNET')} hca={dw.get('hca')}")

    # 3. Unique dates + cache check
    unique_dates = sorted(set(r["date"] for r in runs))
    cached = sum(1 for d in unique_dates if os.path.exists(cache_file(d)))
    missing = len(unique_dates) - cached
    print(f"  {len(unique_dates)} unique dates - {cached} cached, {missing} missing")
    if missing > 0:
        print(f"  WARNING: {missing} dates have no cached stats -- those games will be skipped")
    print()

    # 4. Load all cached stats into memory
    print("-- Loading cached stats --")
    stats_map = {}
    cache_hits = 0
    failures = 0

    for date in unique_dates:
        stats = get_stats_for_date(date)
        if stats:
            stats_map[date] = stats
            cache_hits += 1
        else:
            failures += 1
    print(f"  Ready: {len(stats_map)} dates cached, {failures} missing")
    print()

    # 5. Replay every game
    BURN_IN_DAYS = 20
    print("-- Replaying games (with Kalman rebuild + evolving weights) --")

    dynamic_residual_var = compute_residual_var(runs)

    total = done = skip = no_stats = 0
    pick_flips = conf_flips = new_picks = lost_picks = 0
    old_rec = {"w": 0, "l": 0}
    new_rec = {"w": 0, "l": 0}

    kalman_state = None
    W = dict(defaults["DEFAULT_W"])
    W_var = dict(defaults["DEFAULT_W_VAR"])
    prev_date = None
    date_index = 0
    prev_graded = []

    for run in runs:
        enhanced = stats_map.get(run["date"])
        if not enhanced:
            for g in run.get("games", []):
                total += 1
                no_stats += 1
            continue

        # Initialize Kalman on first date with stats
        if kalman_state is None:
            kalman_state = initialize_kalman(enhanced["season"])
            kalman_state["lastDriftDate"] = run["date"]
            print(f"  Kalman initialized from {run['date']}")

        # Apply daily drift
        if run["date"] != prev_date:
            apply_daily_drift(kalman_state, run["date"])
            prev_date = run["date"]
            date_index += 1

            # Tune on PREVIOUS date's graded games before analyzing today
            was_in_burn_in = date_index - 1 <= BURN_IN_DAYS
            if not was_in_burn_in and prev_graded:
                result = tune_weights(W, W_var, prev_graded)
                W.update(result["W"])
                W_var.update(result["W_var"])
            prev_graded = []
        in_burn_in = date_index <= BURN_IN_DAYS

        game_stats_base = enhanced["season"]
        graded_this_date = []

        for g in run.get("games", []):
            total += 1

            if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
                skip += 1
                continue
            if not isinstance(g.get("line"), (int, float)) or not isinstance(g.get("total"), (int, float)):
                skip += 1
                continue

            # Save old values
            prev_pick = g.get("sPick")
            prev_conf = g.get("sConf")
            prev_sr = g.get("sResult")

            if prev_sr == "WIN":
                old_rec["w"] += 1
            if prev_sr == "LOSS":
                old_rec["l"] += 1

            game_stats = dict(game_stats_base)

            # Replay cached lineup/B2B deltas
            if g.get("_adjDeltas"):
                STAT_KEYS = ["OFF", "DEF", "TS", "TO", "ORR", "PACE"]
                for side, team_name in [("away", g["away"]), ("home", g["home"])]:
                    delta = g["_adjDeltas"].get(side)
                    if delta and team_name in game_stats:
                        game_stats[team_name] = dict(game_stats[team_name])
                        for k in STAT_KEYS:
                            if k in delta:
                                game_stats[team_name][k] = (game_stats[team_name].get(k, 0) or 0) + delta[k]

            game_avgs = get_avgs(game_stats)

            result = analyze_game(
                {"away": g["away"], "home": g["home"], "line": g["line"], "total": g["total"]},
                game_stats, game_avgs, W, None, kalman_state, W_var, dynamic_residual_var
            )

            if not result:
                skip += 1
                continue
            done += 1

            # Track changes
            if prev_pick != result.get("sPick"):
                if prev_pick == "PASS" and result.get("sPick") != "PASS":
                    new_picks += 1
                elif prev_pick != "PASS" and result.get("sPick") == "PASS":
                    lost_picks += 1
                else:
                    pick_flips += 1
            if prev_pick == result.get("sPick") and prev_conf != result.get("sConf"):
                conf_flips += 1

            # Apply recalculated values
            for key in ["aS", "hS", "pT", "margin", "sDiff", "tDiff", "sPick", "sConf", "oPick", "oConf",
                        "pHomeCover", "pAwayCover", "pOver", "pUnder", "pCover", "pOU", "marginVar", "marginStd",
                        "_features", "_marginFeatures"]:
                if key in result:
                    g[key] = result[key]

            if not isinstance(g.get("uncertaintyScore"), (int, float)):
                g["uncertaintyScore"] = 0

            run["burnIn"] = in_burn_in

            # Re-grade
            home_score = g.get("homeScore")
            away_score = g.get("awayScore")
            if isinstance(home_score, (int, float)) and math.isfinite(home_score) and \
               isinstance(away_score, (int, float)) and math.isfinite(away_score):
                g["sResult"] = grade_spread(g)
                g["oResult"] = grade_total(g)
                if g["sResult"] == "WIN":
                    new_rec["w"] += 1
                if g["sResult"] == "LOSS":
                    new_rec["l"] += 1
                graded_entry = dict(g)
                graded_entry["_kalmanDate"] = run["date"]
                graded_this_date.append(graded_entry)
            else:
                g.pop("sResult", None)
                g.pop("oResult", None)

        # Feed this date's graded games into Kalman
        if graded_this_date:
            batch_update(kalman_state, graded_this_date)

        prev_graded = graded_this_date

    # 6. Report
    def pct(w, l):
        return f"{100 * w / (w + l):.1f}" if (w + l) > 0 else "0.0"

    def u(w, l):
        v = w * 0.9091 - l
        return f"{'+' if v >= 0 else ''}{v:.1f}"

    print()
    print("-- Summary --")
    print(f"  Games:  {total} total, {done} recalculated, {skip} skipped, {no_stats} no-stats")
    print()
    print(f"  Pick flips:   {pick_flips} (different team/side)")
    print(f"  Conf changes: {conf_flips} (same pick, different tier)")
    print(f"  New picks:    {new_picks} (PASS -> pick)")
    print(f"  Lost picks:   {lost_picks} (pick -> PASS)")
    print()

    print("-- Spread Record --")
    print(f"  OLD: {old_rec['w']}-{old_rec['l']} ({pct(old_rec['w'], old_rec['l'])}%) {u(old_rec['w'], old_rec['l'])}u")
    print(f"  NEW: {new_rec['w']}-{new_rec['l']} ({pct(new_rec['w'], new_rec['l'])}%) {u(new_rec['w'], new_rec['l'])}u")
    print()

    # Fav/dog split
    fav = dog = fav_w = dog_w = fav_l = dog_l = 0
    for run in runs:
        for g in run.get("games", []):
            s_pick = g.get("sPick")
            if not s_pick or s_pick == "PASS" or g.get("sConf") not in ("high", "elite"):
                continue
            m = re.search(r"([+-])(\d+(?:\.\d+)?)$", s_pick)
            if not m:
                continue
            if m.group(1) == "-":
                fav += 1
                if g.get("sResult") == "WIN":
                    fav_w += 1
                if g.get("sResult") == "LOSS":
                    fav_l += 1
            else:
                dog += 1
                if g.get("sResult") == "WIN":
                    dog_w += 1
                if g.get("sResult") == "LOSS":
                    dog_l += 1

    tp = fav + dog
    print("-- Fav / Dog Split --")
    print(f"  Fav: {fav} ({round(100 * fav / tp) if tp else 0}%) {fav_w}-{fav_l} {u(fav_w, fav_l)}u")
    print(f"  Dog: {dog} ({round(100 * dog / tp) if tp else 0}%) {dog_w}-{dog_l} {u(dog_w, dog_l)}u")
    print()

    # Over/Under split
    o_w = o_l = u_w = u_l = 0
    for run in runs:
        for g in run.get("games", []):
            o_pick = g.get("oPick")
            if not o_pick or o_pick == "PASS" or g.get("oConf") not in ("high", "elite"):
                continue
            if o_pick == "OVER":
                if g.get("oResult") == "WIN":
                    o_w += 1
                if g.get("oResult") == "LOSS":
                    o_l += 1
            else:
                if g.get("oResult") == "WIN":
                    u_w += 1
                if g.get("oResult") == "LOSS":
                    u_l += 1

    print("-- Over / Under Split --")
    print(f"  OVER:  {o_w}-{o_l} ({pct(o_w, o_l)}%) {u(o_w, o_l)}u")
    print(f"  UNDER: {u_w}-{u_l} ({pct(u_w, u_l)}%) {u(u_w, u_l)}u")
    print()

    # Team records
    teams = {}
    for run in runs:
        for g in run.get("games", []):
            s_pick = g.get("sPick")
            if not s_pick or s_pick == "PASS" or g.get("sConf") not in ("high", "elite") or not g.get("sResult"):
                continue
            m = re.match(r"(.+?)\s+([+-])", s_pick)
            if not m:
                continue
            team = m.group(1).strip()
            is_fav = m.group(2) == "-"
            if team not in teams:
                teams[team] = {"w": 0, "l": 0, "p": 0, "fav": 0, "dog": 0}
            if g["sResult"] == "WIN":
                teams[team]["w"] += 1
            elif g["sResult"] == "LOSS":
                teams[team]["l"] += 1
            else:
                teams[team]["p"] += 1
            if is_fav:
                teams[team]["fav"] += 1
            else:
                teams[team]["dog"] += 1

    sorted_teams = sorted(teams.items(), key=lambda x: x[1]["w"] + x[1]["l"], reverse=True)
    if sorted_teams:
        print("-- Team Records (top 15) --")
        for name, t in sorted_teams[:15]:
            tot = t["w"] + t["l"]
            wp = round(100 * t["w"] / tot) if tot > 0 else 0
            record = f"{t['w']}-{t['l']}-{t['p']}"
            print(f"  {name:<26} {record:<9} {wp}%{u(t['w'], t['l']):>8}  ({t['fav']}F/{t['dog']}D)")
        if len(sorted_teams) > 15:
            print(f"  ... +{len(sorted_teams) - 15} more")
        print()

    # Kalman summary
    if kalman_state:
        print(kalman_summary(kalman_state, 10))
        print()

    # 7. Save
    if SAVE:
        ts = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        backup = HISTORY_PATH.replace(".json", f"_pre_recalc_{ts}.json")
        shutil.copy2(HISTORY_PATH, backup)
        print(f"  Backup: {os.path.basename(backup)}")

        store["weights"] = dict(W)
        store["weightsVar"] = dict(W_var)
        store["residualVar"] = dynamic_residual_var

        with open(HISTORY_PATH, "w") as f:
            json.dump(store, f, indent=2)
        print(f"  history.json updated ({done} games recalculated)")

        if kalman_state:
            prune_processed_games(kalman_state, 60)
            save_kalman_state(kalman_state)
            print("  kalman_state.json rebuilt")
    else:
        print("  DRY RUN -- no changes written. Remove --dry-run to save.")

    print()
    print(f"  Stats cache: {CACHE_DIR}")
    print("  Cached dates are reused automatically.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print("FATAL:", e)
        traceback.print_exc()
        sys.exit(1)
