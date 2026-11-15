#!/usr/bin/env python3
# scripts/recalculate.py
# ────────────────────────────────────────────────────────────────────────────
# Re-runs every game in history.json through the CURRENT model engine
# using CACHED stats only — no API calls.
#
# Usage:
#   python scripts/recalculate.py              # recalculate + save (default)
#   python scripts/recalculate.py --dry-run    # preview changes without saving
# ────────────────────────────────────────────────────────────────────────────

import json
import math
import os
import re
import shutil
import sys
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from model_engine import load_defaults, get_avgs, analyze_game
from self_tune import tune_weights, compute_residual_var
from kalman_state import (
    initialize_kalman, apply_daily_drift, batch_update,
    save_kalman_state, prune_processed_games, kalman_summary,
)
from sources.rest_detect import apply_b2b_adjustment

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPTS_DIR, "..", "data")
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")
CACHE_DIR = os.path.join(DATA_DIR, "stats_cache")

SAVE = "--dry-run" not in sys.argv


# ─── Inline Grading ─────────────────────────────────────────────────────────

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


# ─── Per-Date Stats from Disk Cache Only ────────────────────────────────────

def cache_file(date_str):
    return os.path.join(CACHE_DIR, date_str + ".json")


def get_stats_for_date(date_str):
    cf = cache_file(date_str)
    if not os.path.exists(cf):
        return None
    with open(cf, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if raw.get("season"):
        return raw  # enhanced
    return {"season": raw, "last10": None, "home": None, "away": None}  # legacy


# ─── Main ───────────────────────────────────────────────────────────────────

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
    with open(HISTORY_PATH, "r", encoding="utf-8") as f:
        store = json.load(f)
    runs = sorted(store.get("runs", []), key=lambda r: r.get("date", ""))
    print(f"  {len(runs)} runs in history.json (sorted chronologically)")

    # 2. Weights (start from defaults, evolve forward)
    defaults = load_defaults()
    dw = defaults["DEFAULT_W"]
    print(f"  Starting weights: sprHigh={dw['sprHigh']} ouHigh={dw['ouHigh']} wNET={dw['wNET']} hca={dw['hca']}")

    # 3. Unique dates + cache check
    unique_dates = sorted(set(r["date"] for r in runs))
    cached = sum(1 for d in unique_dates if os.path.exists(cache_file(d)))
    missing = len(unique_dates) - cached
    print(f"  {len(unique_dates)} unique dates - {cached} cached, {missing} missing")
    if missing > 0:
        print(f"  WARNING: {missing} dates have no cached stats — those games will be skipped")
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

    total = 0
    done = 0
    skip = 0
    no_stats = 0
    pick_flips = 0
    conf_flips = 0
    new_picks = 0
    lost_picks = 0
    old_rec = {"w": 0, "l": 0}
    new_rec = {"w": 0, "l": 0}

    kalman_state = None
    W = {**defaults["DEFAULT_W"]}
    W_var = {**defaults["DEFAULT_W_VAR"]}
    prev_date = None
    date_index = 0
    prev_graded = []
    h2h_matchups = {}
    b2b_teams = set()
    prev_day_teams = set()

    # Freeze residualVar from original projections BEFORE replay
    dynamic_residual_var = compute_residual_var(runs)
    print(f"  Frozen residualVar: {dynamic_residual_var:.1f if dynamic_residual_var else 'null'} (from original projections, held constant during replay)")

    B2B_OFF_DELTA = -1.5
    B2B_DEF_DELTA = 0.8

    STAT_KEYS = ["OFF", "DEF", "TS", "TO", "ORR", "PACE"]

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

        # ── Start of new day ──
        if run["date"] != prev_date:
            prev_date = run["date"]
            date_index += 1
            date_label = f"{run['date'][:4]}-{run['date'][4:6]}-{run['date'][6:]}"
            sys.stdout.write(f"  [{date_index}/{len(unique_dates)}] {date_label}")

            if prev_graded:
                batch_update(kalman_state, prev_graded)

                was_in_burn_in = (date_index - 1) <= BURN_IN_DAYS
                if not was_in_burn_in:
                    result = tune_weights(W, W_var, prev_graded)
                    W.update(result["W"])
                    W_var.update(result["W_var"])

                for g in prev_graded:
                    if not isinstance(g.get("homeScore"), (int, float)) or not math.isfinite(g.get("homeScore", float("nan"))):
                        continue
                    if not isinstance(g.get("awayScore"), (int, float)) or not math.isfinite(g.get("awayScore", float("nan"))):
                        continue
                    key = "::".join(sorted([g["home"], g["away"]]))
                    if key not in h2h_matchups:
                        sorted_teams = sorted([g["home"], g["away"]])
                        h2h_matchups[key] = {"team1": sorted_teams[0], "team2": sorted_teams[1], "games": []}
                    h2h_matchups[key]["games"].append({
                        "date": prev_graded[0].get("_kalmanDate", run["date"]) if prev_graded else run["date"],
                        "home": {"team": g["home"], "pts": g["homeScore"]},
                        "away": {"team": g["away"], "pts": g["awayScore"]},
                    })

            prev_graded = []
            b2b_teams = prev_day_teams
            prev_day_teams = set()
            apply_daily_drift(kalman_state, run["date"])

            games_this_date = len([g for g in run.get("games", []) if g.get("status") not in ("MISSING_ODDS", "SKIPPED")])
            burn_in_label = " (burn-in)" if date_index <= BURN_IN_DAYS else ""
            sys.stdout.write(f" — {games_this_date} games{burn_in_label}\n")
            sys.stdout.flush()

        in_burn_in = date_index <= BURN_IN_DAYS

        game_stats_base = enhanced["season"]
        todays_games = [{"away": g.get("away"), "home": g.get("home")} for g in run.get("games", [])]
        result = apply_b2b_adjustment(game_stats_base, b2b_teams, todays_games)
        b2b_adjusted = result["adjusted"]
        b2b_notes = result["b2bNotes"]

        graded_this_date = []

        for g in run.get("games", []):
            total += 1

            if g.get("home"):
                prev_day_teams.add(g["home"])
            if g.get("away"):
                prev_day_teams.add(g["away"])

            if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
                skip += 1
                continue
            if not isinstance(g.get("line"), (int, float)) or not isinstance(g.get("total"), (int, float)):
                skip += 1
                continue

            prev_pick = g.get("sPick")
            prev_conf = g.get("sConf")
            prev_sr = g.get("sResult")

            if prev_sr == "WIN":
                old_rec["w"] += 1
            if prev_sr == "LOSS":
                old_rec["l"] += 1

            game_stats = {**b2b_adjusted}

            # Replay cached lineup deltas
            if g.get("_adjDeltas"):
                for side, team_name in [("away", g.get("away")), ("home", g.get("home"))]:
                    delta = (g.get("_adjDeltas") or {}).get(side)
                    if delta and team_name in game_stats:
                        game_stats[team_name] = {**game_stats[team_name]}
                        adj = {**delta}
                        if team_name in b2b_teams:
                            if adj.get("OFF"):
                                adj["OFF"] = round((adj["OFF"] - B2B_OFF_DELTA) * 10000) / 10000
                            if adj.get("DEF"):
                                adj["DEF"] = round((adj["DEF"] - B2B_DEF_DELTA) * 10000) / 10000
                        for k in STAT_KEYS:
                            if adj.get(k) and abs(adj[k]) > 0.0001:
                                game_stats[team_name][k] = (game_stats[team_name].get(k, 0) or 0) + adj[k]

            # Update b2bNote
            away_b2b = b2b_notes.get(g.get("away"))
            home_b2b = b2b_notes.get(g.get("home"))
            if away_b2b or home_b2b:
                parts = []
                if away_b2b:
                    parts.append(f"{g['away']}: {away_b2b}")
                if home_b2b:
                    parts.append(f"{g['home']}: {home_b2b}")
                g["b2bNote"] = " | ".join(parts)
            else:
                g.pop("b2bNote", None)

            game_avgs = get_avgs(game_stats)

            result = analyze_game(
                {"away": g["away"], "home": g["home"], "line": g["line"], "total": g["total"]},
                game_stats, game_avgs, W, None, kalman_state, W_var, dynamic_residual_var, h2h_matchups
            )

            if not result:
                skip += 1
                continue
            done += 1

            # Track changes
            if prev_pick != result["sPick"]:
                if prev_pick == "PASS" and result["sPick"] != "PASS":
                    new_picks += 1
                elif prev_pick != "PASS" and result["sPick"] == "PASS":
                    lost_picks += 1
                else:
                    pick_flips += 1
            if prev_pick == result["sPick"] and prev_conf != result["sConf"]:
                conf_flips += 1

            # Apply recalculated values
            for key in ["aS", "hS", "pT", "margin", "sDiff", "tDiff", "sPick", "sConf", "oPick", "oConf",
                        "pHomeCover", "pAwayCover", "pOver", "pUnder", "pCover", "pOU", "marginVar", "marginStd",
                        "h2hAdj", "h2hGames", "h2hRecord", "h2hNote", "_features", "_marginFeatures"]:
                if key in result:
                    g[key] = result[key]

            if not isinstance(g.get("uncertaintyScore"), (int, float)):
                g["uncertaintyScore"] = 0

            run["burnIn"] = in_burn_in

            # Re-grade against actual scores
            if (isinstance(g.get("homeScore"), (int, float)) and math.isfinite(g["homeScore"])
                    and isinstance(g.get("awayScore"), (int, float)) and math.isfinite(g["awayScore"])):
                g["sResult"] = grade_spread(g)
                g["oResult"] = grade_total(g)
                if g["sResult"] == "WIN":
                    new_rec["w"] += 1
                if g["sResult"] == "LOSS":
                    new_rec["l"] += 1

                graded_this_date.append({**g, "_kalmanDate": run["date"]})
            else:
                g.pop("sResult", None)
                g.pop("oResult", None)

        prev_graded = graded_this_date
        run["weightsUsed"] = {**W}
        run["weightsNext"] = {**W}

    # 6. Report
    def pct(w, l):
        return f"{(100 * w / (w + l)):.1f}" if (w + l) > 0 else "0.0"

    def u(w, l):
        v = w * 0.9091 - l
        return f"+{v:.1f}" if v >= 0 else f"{v:.1f}"

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

    sorted_teams = sorted(teams.items(), key=lambda x: (x[1]["w"] + x[1]["l"]), reverse=True)
    if sorted_teams:
        print("-- Team Records (top 15) --")
        for name, t in sorted_teams[:15]:
            tot = t["w"] + t["l"]
            wp = round(100 * t["w"] / tot) if tot > 0 else 0
            print(f"  {name:<26} {t['w']}-{t['l']}-{t['p']:<9} {wp}%{u(t['w'], t['l']):>7}  ({t['fav']}F/{t['dog']}D)")
        if len(sorted_teams) > 15:
            print(f"  ... +{len(sorted_teams) - 15} more")
        print()

    # Kalman summary
    if kalman_state:
        print(kalman_summary(kalman_state, 10))
        print()

    # 7. Save
    if SAVE:
        ts = datetime.now().isoformat().replace(":", "-").replace(".", "-")[:19]
        backup = HISTORY_PATH.replace(".json", f"_pre_recalc_{ts}.json")
        shutil.copy2(HISTORY_PATH, backup)
        print(f"  Backup: {os.path.basename(backup)}")

        store["weights"] = {**W}
        store["weightsVar"] = {**W_var}
        store["residualVar"] = compute_residual_var(runs)

        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=2)
        print(f"  history.json updated ({done} games recalculated)")

        if kalman_state:
            prune_processed_games(kalman_state, 60)
            save_kalman_state(kalman_state)
            print("  kalman_state.json rebuilt")
    else:
        print("  DRY RUN — no changes written. Remove --dry-run to save.")

    print()
    print(f"  Stats cache: {CACHE_DIR}")
    print("  Cached dates are reused automatically.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FATAL: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
