# scripts/_test_factors.py
# Test different CALIB_FACTOR values by running recalculate logic in-memory
# Reports record + units for each factor without saving

import os
import sys
import json
import re
import math

from dotenv import load_dotenv
load_dotenv()

from model_engine import load_defaults, get_avgs, analyze_game
from self_tune import tune_weights
from kalman_state import initialize_kalman, apply_daily_drift, batch_update

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "..", "data")
CACHE_DIR = os.path.join(DATA_DIR, "stats_cache")
HISTORY_PATH = os.path.join(DATA_DIR, "history_pre_recalc_2026-03-15T15-00-39.json")
KALMAN_PATH = os.path.join(DATA_DIR, "kalman_state.json")

BURN_IN_DAYS = 20


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
    spread = -float(m.group(3)) if m.group(2) == "-" else float(m.group(3))
    chosen_is_home = m.group(1).strip() == g.get("home")
    margin = (home_score - away_score) if chosen_is_home else (away_score - home_score)
    cover = margin + spread
    if cover > 0:
        return "WIN"
    elif cover < 0:
        return "LOSS"
    return "PUSH"


def get_stats_for_date(date_str):
    cf = os.path.join(CACHE_DIR, date_str + ".json")
    if not os.path.exists(cf):
        return None
    with open(cf, "r") as f:
        raw = json.load(f)
    if "season" in raw:
        return raw
    return {"season": raw, "last10": None, "home": None, "away": None}


def compute_residual_var_with_factor(runs, factor):
    errors = []
    for r in runs:
        if r.get("burnIn"):
            continue
        for g in r.get("games", []):
            home_score = g.get("homeScore")
            away_score = g.get("awayScore")
            margin = g.get("margin")
            line = g.get("line")
            if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in [home_score, away_score, margin, line]):
                continue
            actual_edge = (home_score - away_score) - line
            proj_edge = margin
            errors.append(actual_edge - proj_edge)
    if len(errors) < 30:
        return 130  # default
    mean = sum(errors) / len(errors)
    variance = sum((x - mean) ** 2 for x in errors) / len(errors)
    return variance * factor


def test_factor(factor, store):
    import copy
    runs = copy.deepcopy(store.get("runs", []))
    defaults = load_defaults()
    W = dict(defaults["DEFAULT_W"])
    W_var = dict(defaults["DEFAULT_W_VAR"])

    residual_var = compute_residual_var_with_factor(runs, factor)

    stats_map = {}
    unique_dates = sorted(set(r["date"] for r in runs))
    for d in unique_dates:
        s = get_stats_for_date(d)
        if s:
            stats_map[d] = s

    kalman_state = None
    prev_date = None
    date_index = 0
    prev_graded = []
    total_w = total_l = total_picks = 0

    for run in runs:
        enhanced = stats_map.get(run["date"])
        if not enhanced:
            continue

        if kalman_state is None:
            kalman_state = initialize_kalman(enhanced["season"])
            kalman_state["lastDriftDate"] = run["date"]

        if run["date"] != prev_date:
            apply_daily_drift(kalman_state, run["date"])
            prev_date = run["date"]
            date_index += 1

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
            if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
                continue
            if not isinstance(g.get("line"), (int, float)) or not isinstance(g.get("total"), (int, float)):
                continue

            game_stats = dict(game_stats_base)
            game_avgs = get_avgs(game_stats)

            result = analyze_game(
                {"away": g["away"], "home": g["home"], "line": g["line"], "total": g["total"]},
                game_stats, game_avgs, W, None, kalman_state, W_var, residual_var
            )

            if not result:
                continue

            result["awayScore"] = g.get("awayScore")
            result["homeScore"] = g.get("homeScore")

            home_score = g.get("homeScore")
            away_score = g.get("awayScore")
            if isinstance(home_score, (int, float)) and math.isfinite(home_score) and \
               isinstance(away_score, (int, float)) and math.isfinite(away_score):
                result["sResult"] = grade_spread(result)
                if not in_burn_in and result.get("sResult"):
                    if result["sResult"] == "WIN":
                        total_w += 1
                    if result["sResult"] == "LOSS":
                        total_l += 1
                    if result["sResult"] in ("WIN", "LOSS"):
                        total_picks += 1
                merged = dict(g)
                merged.update(result)
                merged["sPick"] = result.get("sPick")
                merged["sResult"] = result.get("sResult")
                merged["margin"] = result.get("margin")
                graded_this_date.append(merged)

        if graded_this_date:
            batch_update(kalman_state, graded_this_date)
        prev_graded = graded_this_date

    pct = f"{total_w / total_picks * 100:.1f}" if total_picks > 0 else "N/A"
    units = f"{total_w * (100 / 110) - total_l:.1f}"
    return {
        "factor": factor, "totalW": total_w, "totalL": total_l,
        "totalPicks": total_picks, "pct": pct, "units": units
    }


def main():
    # Use backup (pre-recalc) history
    hist_path = HISTORY_PATH
    if not os.path.exists(hist_path):
        hist_path = os.path.join(DATA_DIR, "history.json")
    with open(hist_path, "r") as f:
        store = json.load(f)
    print(f"Testing factors on {len(store.get('runs', []))} runs from {os.path.basename(hist_path)}\n")

    print("Factor | Record      | Win%  | Units")
    print("-------|-------------|-------|------")

    for factor in [1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.4]:
        # Need to reset kalman file each time
        if os.path.exists(KALMAN_PATH):
            os.unlink(KALMAN_PATH)
        r = test_factor(factor, store)
        record = f"{r['totalW']}-{r['totalL']}"
        mark = " <- BEST" if float(r["units"]) > 40 else ""
        print(f"{r['factor']:<7.1f}| {record:<12}| {r['pct']:<6}| {r['units']}{mark}")


if __name__ == "__main__":
    main()
