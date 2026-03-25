#!/usr/bin/env python3
"""
scripts/run_weekly.py
Weekly NFL picks pipeline — main orchestrator.

Stages:
  fetch     — pull play-by-play, compute team stats, fetch odds
  injuries  — fetch injury data, compute injury deltas
  grade     — grade previous week's picks, update Kalman, self-tune
  project   — load Kalman states, run projections, save picks
  all       — grade -> fetch -> injuries -> project (full pipeline)

Usage:
  python scripts/run_weekly.py --stage all
  python scripts/run_weekly.py --stage fetch --week 12 --season 2025
  python scripts/run_weekly.py --stage grade
"""

import argparse
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Path setup — ensure we can import from pyNFL/scripts/ and core/
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(SCRIPT_DIR, "..", ".."))

from dotenv import load_dotenv
load_dotenv()

# ---------------------------------------------------------------------------
# Local imports — thin wrappers over core/ + NFL-specific modules
# ---------------------------------------------------------------------------
from store import load_store, save_store, upsert_run
from self_tune import tune_weights, compute_residual_var
from kalman_state import (
    load_kalman_state, save_kalman_state, initialize_kalman,
    apply_daily_drift, batch_update, kalman_summary, prune_processed_games,
)
from calibration import build_calibration_table
import defaults

# Source modules
from sources.nflfastr import fetch_pbp, current_season
from sources.nfl_stats import compute_team_stats, compute_team_stats_through_week, compute_player_stats
from sources.odds_theoddsapi import fetch_nfl_odds, fetch_historical_odds
from sources.espn_scoreboard import (
    fetch_nfl_scoreboard, extract_final_scores, fetch_week_scores, extract_all_games,
)
from sources.injuries import fetch_injury_data, classify_injuries, get_key_injuries

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
UNIT_WIN = 1.0
UNIT_LOSS = -1.1   # 1u flat, to-win-1u at standard -110 juice
CONF_ACTIONABLE = ("high", "elite")
NFL_WEEKS_REGULAR = 18
NFL_WEEKS_MAX = 22  # Includes playoffs: 19=WC, 20=Div, 21=Conf, 22=SB
BURN_IN_WEEKS = 1   # first N weeks in backfill are warm-up only

# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def fmt_num(x, digits=1):
    if isinstance(x, (int, float)) and math.isfinite(x):
        return f"{x:.{digits}f}"
    return "n/a"


def fmt_units(u):
    if not isinstance(u, (int, float)) or not math.isfinite(u):
        return "\u2014"
    return f"{'+' if u >= 0 else ''}{u:.1f}u"


def calc_units(w, l):
    return w * UNIT_WIN + l * UNIT_LOSS


def is_actionable(conf):
    return str(conf or "").lower() in CONF_ACTIONABLE


def current_nfl_week(season=None):
    """
    Estimate the current NFL week based on today's date.
    NFL Week 1 Sunday is typically the first Sunday after Labor Day + 4 days.
    """
    if season is None:
        season = current_season()

    now = datetime.now()
    # Find Labor Day (first Monday in September)
    sept1 = datetime(season, 9, 1)
    days_to_monday = (7 - sept1.weekday()) % 7
    if sept1.weekday() == 0:
        days_to_monday = 0
    labor_day = sept1 + timedelta(days=days_to_monday)

    # Week 1 starts the Tuesday after Labor Day (first game is Thursday)
    week1_start = labor_day + timedelta(days=1)  # Tuesday

    delta = (now - week1_start).days
    if delta < 0:
        return 0  # preseason
    week = delta // 7 + 1
    return min(week, NFL_WEEKS_MAX)


# ---------------------------------------------------------------------------
# Grading helpers
# ---------------------------------------------------------------------------

def parse_spread_pick(pick):
    """Parse a pick string like 'Kansas City Chiefs -3.5' into parts."""
    if not pick or pick == "PASS":
        return None
    m = re.match(r"(.+?)\s+([+-])(\d+(?:\.\d+)?)", pick)
    if not m:
        return None
    return {"team": m.group(1).strip(), "sign": m.group(2), "pts": float(m.group(3))}


def grade_spread_pick(g):
    """Grade a spread pick against final scores.  Returns WIN / LOSS / PUSH."""
    parsed = parse_spread_pick(g.get("sPick"))
    if not parsed:
        return None
    team, sign, pts = parsed["team"], parsed["sign"], parsed["pts"]
    chosen_is_home = team == g.get("home")
    margin = (g["homeScore"] - g["awayScore"]) if chosen_is_home else (g["awayScore"] - g["homeScore"])
    val = margin + pts if sign == "+" else margin - pts
    if val == 0:
        return "PUSH"
    return "WIN" if val > 0 else "LOSS"


def grade_total_pick(g):
    o_pick = g.get("oPick")
    if not o_pick or o_pick == "PASS":
        return None
    actual = g["homeScore"] + g["awayScore"]
    if actual == g.get("total"):
        return "PUSH"
    if o_pick == "OVER":
        return "WIN" if actual > g["total"] else "LOSS"
    return "WIN" if actual < g["total"] else "LOSS"


# ---------------------------------------------------------------------------
# Grade a week in the store against final scores
# ---------------------------------------------------------------------------

def grade_week_in_store(store, week, season):
    """Fetch final scores for *week* and fill in results for the matching run."""
    run_key = f"{season}_W{week}"
    run = None
    for r in store.get("runs", []):
        if r.get("weekKey") == run_key:
            run = r
            break
    if not run:
        return []

    needs_grading = any(
        g.get("status") not in ("MISSING_ODDS", "SKIPPED")
        and not isinstance(g.get("homeScore"), (int, float))
        for g in run.get("games", [])
    )
    if not needs_grading:
        # Already graded — return completed games for Kalman
        return [
            g for g in run.get("games", [])
            if g.get("status") not in ("MISSING_ODDS", "SKIPPED")
            and isinstance(g.get("homeScore"), (int, float))
        ]

    try:
        finals = fetch_week_scores(week, season=season)
    except Exception as e:
        print(f"  [grade] Could not fetch scores for {run_key}: {e}")
        return []

    if not finals:
        return []

    graded_count = 0
    graded_games = []

    for g in run.get("games", []):
        if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
            continue
        if isinstance(g.get("homeScore"), (int, float)):
            graded_games.append(g)
            continue

        # Match by team names
        f = None
        for x in finals:
            if _match_team(x["away"], g.get("away", "")) and _match_team(x["home"], g.get("home", "")):
                f = x
                break
        if not f:
            continue

        g["awayScore"] = f["awayScore"]
        g["homeScore"] = f["homeScore"]

        if g.get("sPick") and g["sPick"] != "PASS":
            g["sResult"] = grade_spread_pick(g)
        if g.get("oPick") and g["oPick"] != "PASS":
            g["oResult"] = grade_total_pick(g)
        graded_count += 1
        graded_games.append(g)

    if graded_count > 0:
        save_store(store)
        print(f"  [grade] Graded {graded_count} games for {run_key}")

    return graded_games


def _norm_team(name):
    """Normalize a team name for fuzzy matching."""
    s = str(name or "").lower().strip()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _match_team(a, b):
    if not a or not b:
        return False
    if a == b:
        return True
    na, nb = _norm_team(a), _norm_team(b)
    if na == nb:
        return True
    if na in nb or nb in na:
        return True
    return False


# ---------------------------------------------------------------------------
# Record / summary computation
# ---------------------------------------------------------------------------

def get_graded_picks(store):
    picks = []
    for r in store.get("runs", []):
        if r.get("burnIn"):
            continue
        for g in r.get("games", []):
            hs = g.get("homeScore")
            aas = g.get("awayScore")
            if not isinstance(hs, (int, float)) or not isinstance(aas, (int, float)):
                continue
            if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
                continue
            pick = dict(g)
            pick["weekKey"] = r.get("weekKey", "")
            picks.append(pick)
    return picks


def tally_picks(picks, pick_type="spread", conf=None):
    w = l = p = 0
    for g in picks:
        pick = g.get("sPick") if pick_type == "spread" else g.get("oPick")
        pick_conf = g.get("sConf") if pick_type == "spread" else g.get("oConf")
        if not pick or pick == "PASS":
            continue
        if conf and pick_conf != conf:
            continue

        if pick_type == "spread":
            result = g.get("sResult") or grade_spread_pick(g)
        else:
            result = g.get("oResult") or grade_total_pick(g)
        if not result:
            continue

        if result == "WIN":
            w += 1
        elif result == "LOSS":
            l += 1
        else:
            p += 1

    total = w + l
    pct = f"{w / total * 100:.1f}" if total > 0 else "n/a"
    units = calc_units(w, l)
    return {"w": w, "l": l, "p": p, "pct": pct, "played": w + l + p, "units": units}


def compute_summary_text(store):
    picks = get_graded_picks(store)
    s_all = tally_picks(picks, "spread")
    s_elite = tally_picks(picks, "spread", conf="elite")

    lines = [
        "RECORD (graded picks only)", "",
        "SPREAD (ATS)",
        f"  All:      {s_all['w']}-{s_all['l']}-{s_all['p']}  ({s_all['pct']}%)  {fmt_units(s_all['units'])}",
        f"  Elite:    {s_elite['w']}-{s_elite['l']}-{s_elite['p']}  ({s_elite['pct']}%)  {fmt_units(s_elite['units'])}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# STAGE: fetch
# ---------------------------------------------------------------------------

def stage_fetch(season, week, store):
    """
    Pull current week's play-by-play, compute team stats, fetch odds.
    Optionally fetch PFR + NGS supplementary data.
    """
    print(f"\n== FETCH — {season} Week {week} ==\n")

    # 1. Pull play-by-play
    print("[fetch 1/4] Pulling play-by-play via nflfastR...")
    try:
        pbp = fetch_pbp(season)
        print(f"  Got {len(pbp):,} plays for {season}")
    except Exception as e:
        print(f"  ERROR: PBP fetch failed: {e}")
        raise

    # 2. Compute team stats with decay (through previous week)
    print("[fetch 2/4] Computing team stats with exponential decay...")
    through_week = max(1, week - 1)
    team_stats = compute_team_stats_through_week(pbp, through_week, decay=0.85)
    if not team_stats:
        print(f"  WARNING: No stats through week {through_week}")
        team_stats = {}
    else:
        print(f"  Computed stats for {len(team_stats)} teams through week {through_week}")

    # Compute player stats for injury layer
    print("  Computing player stats...")
    try:
        player_stats = compute_player_stats(pbp[pbp["week"] <= through_week])
    except Exception as e:
        print(f"  WARNING: Player stats failed: {e}")
        player_stats = {}

    # 3. Fetch odds
    print("[fetch 3/4] Fetching current NFL odds...")
    try:
        odds = fetch_nfl_odds()
        print(f"  Got odds for {len(odds)} games")
    except Exception as e:
        print(f"  WARNING: Odds fetch failed: {e}")
        odds = []

    # 4. Optional supplementary sources (PFR, NGS)
    print("[fetch 4/4] Optional supplementary data...")
    pfr_data = None
    ngs_data = None
    try:
        from sources.pfr_stats import fetch_pfr_data
        pfr_data = fetch_pfr_data(season)
        print(f"  PFR: OK")
    except Exception as e:
        print(f"  PFR: Skipped ({e})")

    try:
        from sources.nextgenstats import fetch_ngs_data
        ngs_data = fetch_ngs_data(season)
        print(f"  NGS: OK")
    except Exception as e:
        print(f"  NGS: Skipped ({e})")

    # Cache in store for downstream stages
    store["_fetch"] = {
        "season": season,
        "week": week,
        "team_stats": team_stats,
        "player_stats": player_stats,
        "odds": odds,
        "pfr_data": pfr_data,
        "ngs_data": ngs_data,
        "pbp_loaded": True,
    }

    print(f"\nFetch complete: {len(team_stats)} teams, {len(odds)} games, {len(player_stats)} players")


# ---------------------------------------------------------------------------
# STAGE: injuries
# ---------------------------------------------------------------------------

def stage_injuries(season, week, store):
    """Fetch injury data and compute injury deltas."""
    print(f"\n== INJURIES — {season} Week {week} ==\n")

    # 1. Fetch injury data
    print("[inj 1/2] Fetching injury reports from ESPN...")
    try:
        inj_data = fetch_injury_data()
        report = inj_data.get("report", {})
        if report:
            total_inj = sum(len(v) for v in report.values())
            print(f"  Got injury data for {len(report)} teams ({total_inj} total injuries)")
        else:
            print("  WARNING: Empty injury report")
    except Exception as e:
        print(f"  WARNING: Injury fetch failed: {e} — proceeding without injury data")
        inj_data = {"report": {}}
        report = {}

    # 2. Compute injury deltas via injury_layer.py
    print("[inj 2/2] Computing injury deltas...")
    injury_deltas = {}
    try:
        from injury_layer import compute_injury_deltas

        player_stats = store.get("_fetch", {}).get("player_stats", {})
        injury_deltas = compute_injury_deltas(report, player_stats)
        if injury_deltas:
            print(f"  Computed deltas for {len(injury_deltas)} teams")
        else:
            print("  No significant injury deltas")
    except ImportError:
        print("  WARNING: injury_layer.py not yet available — skipping deltas")
    except Exception as e:
        print(f"  WARNING: Injury delta computation failed: {e}")

    store["_injuries"] = {
        "report": report,
        "deltas": injury_deltas,
    }

    # Print key injuries
    for team_name, injuries_list in report.items():
        key = get_key_injuries(report, team_name)
        if key:
            print(f"  {team_name}: {len(key)} key injuries")


# ---------------------------------------------------------------------------
# STAGE: grade
# ---------------------------------------------------------------------------

def stage_grade(season, week, store):
    """
    Grade previous week's picks.
    Update Kalman states and self-tune weights on graded results.
    """
    print(f"\n== GRADE — {season} Week {week} ==\n")

    prev_week = week - 1
    if prev_week < 1:
        print("  No previous week to grade (Week 1)")
        return

    # 1. Grade previous week
    print(f"[grade 1/4] Grading Week {prev_week} picks...")
    graded_games = grade_week_in_store(store, prev_week, season)

    completed = [
        g for g in graded_games
        if isinstance(g.get("homeScore"), (int, float)) and math.isfinite(g["homeScore"])
        and isinstance(g.get("awayScore"), (int, float)) and math.isfinite(g["awayScore"])
    ]

    if not completed:
        print(f"  No completed games to grade for Week {prev_week}")
    else:
        # Tally
        wins = sum(1 for g in completed if g.get("sResult") == "WIN")
        losses = sum(1 for g in completed if g.get("sResult") == "LOSS")
        pushes = sum(1 for g in completed if g.get("sResult") == "PUSH")
        units = calc_units(wins, losses)
        print(f"  Week {prev_week}: {wins}W-{losses}L-{pushes}P ({fmt_units(units)})")

    # 2. Update Kalman state
    print(f"[grade 2/4] Updating Kalman filter...")
    kalman_state = load_kalman_state()
    team_stats = store.get("_fetch", {}).get("team_stats")

    if not kalman_state.get("teams") and team_stats:
        kalman_state = initialize_kalman(team_stats)
        print("  Kalman initialized from current team stats")

    if completed:
        for g in completed:
            g["_kalmanDate"] = f"{season}_W{prev_week}"
        batch_update(kalman_state, completed)
        print(f"  Kalman updated with {len(completed)} games")

    # 3. Self-tune weights
    print(f"[grade 3/4] Self-tuning weights...")
    base_w = store.get("weights") or defaults.DEFAULT_W
    base_w_var = store.get("weightsVar") or defaults.DEFAULT_W_VAR
    base_w = {**base_w}
    base_w_var = {**base_w_var}

    if completed:
        try:
            tuned = tune_weights(base_w, base_w_var, completed)
            store["weights"] = tuned["W"]
            store["weightsVar"] = tuned["W_var"]
            print(f"  Weights tuned on {len(completed)} games")
        except Exception as e:
            print(f"  WARNING: Self-tune failed: {e}")

    # 4. Save Kalman state
    print(f"[grade 4/4] Saving Kalman state...")
    apply_daily_drift(kalman_state, f"{season}_W{week}")
    prune_processed_games(kalman_state, 60)
    save_kalman_state(kalman_state)
    save_store(store)
    print("  Kalman state saved")


# ---------------------------------------------------------------------------
# STAGE: project
# ---------------------------------------------------------------------------

def stage_project(season, week, store):
    """
    Load Kalman states, weights, thresholds.
    For each matchup: run analyze_game from model_engine.py.
    Run LR confirmation. Save picks to store. Print summary.
    """
    print(f"\n== PROJECT — {season} Week {week} ==\n")

    # Load Kalman state
    kalman_state = load_kalman_state()
    team_stats = store.get("_fetch", {}).get("team_stats", {})

    if not kalman_state.get("teams"):
        if team_stats:
            kalman_state = initialize_kalman(team_stats)
            print("  Kalman initialized from team stats")
        else:
            print("  ERROR: No Kalman state and no team stats available")
            return

    # Apply daily drift
    apply_daily_drift(kalman_state, f"{season}_W{week}")

    # Load weights and thresholds
    base_w = store.get("weights") or defaults.DEFAULT_W
    base_w_var = store.get("weightsVar") or defaults.DEFAULT_W_VAR

    # Load trained weights from file if available
    weights_path = os.path.join(SCRIPT_DIR, "..", "data", "weights.json")
    thresholds_path = os.path.join(SCRIPT_DIR, "..", "data", "thresholds.json")
    try:
        if os.path.exists(weights_path):
            with open(weights_path, "r") as f:
                file_weights = json.load(f)
            if file_weights and isinstance(file_weights, dict) and len(file_weights) > 1:
                # Merge file weights with store weights (store takes precedence for tuned values)
                for k, v in file_weights.items():
                    if k not in base_w:
                        base_w[k] = v
    except Exception as e:
        print(f"  WARNING: Could not load weights.json: {e}")

    thresholds = {}
    try:
        if os.path.exists(thresholds_path):
            with open(thresholds_path, "r") as f:
                thresholds = json.load(f)
    except Exception:
        pass

    # Compute residualVar from history
    dynamic_residual_var = compute_residual_var(store.get("runs", []))
    store["residualVar"] = dynamic_residual_var

    # Get odds
    odds = store.get("_fetch", {}).get("odds", [])
    if not odds:
        print("  WARNING: No odds available — fetching now...")
        try:
            odds = fetch_nfl_odds()
        except Exception as e:
            print(f"  ERROR: Could not fetch odds: {e}")
            odds = []

    # Get injury deltas
    injury_deltas = store.get("_injuries", {}).get("deltas", {})

    # Load model engine
    try:
        from model_engine import analyze_game
    except ImportError:
        print("  ERROR: model_engine.py not yet available — cannot project")
        return

    # Load LR model
    lr_bundle = None
    try:
        from lr_model import load_or_train_lr, extract_lr_features, predict_lr
        lr_bundle = load_or_train_lr(store)
        if lr_bundle:
            print(f"  LR model ready ({lr_bundle.get('n_train', '?')} training games)")
        else:
            print("  LR model not ready (insufficient training data)")
    except ImportError:
        print("  WARNING: lr_model.py not yet available — skipping LR confirmation")
    except Exception as e:
        print(f"  WARNING: LR model load failed: {e}")

    # Analyze each matchup
    print(f"[project] Analyzing {len(odds)} matchups...")
    games = []

    for g in odds:
        if not isinstance(g.get("line"), (int, float)) or not isinstance(g.get("total"), (int, float)):
            games.append({**g, "status": "MISSING_ODDS"})
            continue

        # Get injury delta for this matchup
        away_delta = injury_deltas.get(g.get("away"), {})
        home_delta = injury_deltas.get(g.get("home"), {})
        game_injury = None
        if away_delta or home_delta:
            game_injury = {"away": away_delta, "home": home_delta}

        try:
            r = analyze_game(
                g, team_stats, base_w, kalman_state,
                base_w_var, dynamic_residual_var,
                injury_adj=game_injury,
                thresholds=thresholds,
            )
        except Exception as e:
            print(f"  WARNING: analyze_game failed for {g.get('away')}@{g.get('home')}: {e}")
            r = None

        if not r:
            games.append({**g, "status": "SKIPPED", "note": "analyze_game returned None"})
            continue

        # LR confirmation / veto
        if lr_bundle and r.get("sPick") and r["sPick"] != "PASS":
            try:
                lr_features = extract_lr_features(r, g, team_stats, kalman_state)
                lr_result = predict_lr(lr_bundle, lr_features, game=g)
                r["lrProb"] = lr_result.get("lr_prob")
                r["lrVerdict"] = lr_result.get("lr_verdict")

                if lr_result.get("lr_verdict") == "VETO":
                    r["lrVetoed"] = r["sPick"]
                    r["lrReasons"] = lr_result.get("lr_reasons", [])
                    r["sPick"] = "PASS"
                    r["sConf"] = "vetoed"
            except Exception as e:
                print(f"  WARNING: LR predict failed: {e}")

        games.append(r)

    # Build and save run
    run_key = f"{season}_W{week}"
    run = {
        "weekKey": run_key,
        "season": season,
        "week": week,
        "date": datetime.now().strftime("%Y%m%d"),
        "dateDisplay": f"{season} Week {week}",
        "burnIn": False,
        "weightsUsed": {**base_w},
        "games": games,
        "summaryText": "",
    }

    run["summaryText"] = compute_summary_text(store)
    upsert_run(store, run)
    save_store(store)

    # Save Kalman state
    save_kalman_state(kalman_state)

    # Print summary table
    print(f"\n{'='*80}")
    print(f"  NFL PICKS — {season} Week {week}")
    print(f"{'='*80}\n")

    actionable = []
    for g in games:
        if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
            continue

        pick = g.get("sPick", "PASS")
        conf = g.get("sConf", "")
        s_diff = g.get("sDiff", 0)
        p_cover = g.get("pCover", 0)
        proj_spread = g.get("projSpread", 0)
        line = g.get("line", 0)

        marker = ""
        if is_actionable(conf):
            marker = " ***" if conf == "elite" else " **"
            actionable.append(g)

        lr_tag = ""
        if g.get("lrVerdict"):
            lr_tag = f" [LR:{g['lrVerdict']}]"
        if g.get("lrVetoed"):
            lr_tag = f" [VETOED: was {g['lrVetoed']}]"

        print(
            f"  {g.get('away', '?'):24s} @ {g.get('home', '?'):24s}  "
            f"Line:{fmt_num(line, 1):>6s}  Proj:{fmt_num(proj_spread, 1):>6s}  "
            f"sDiff:{fmt_num(s_diff, 1):>5s}  P:{fmt_num(p_cover, 2):>5s}  "
            f"=> {pick:30s} [{conf}]{marker}{lr_tag}"
        )

    print(f"\n  Total games: {len(games)}")
    print(f"  Actionable picks: {len(actionable)}")
    if actionable:
        print(f"\n  Actionable summary:")
        for g in actionable:
            print(f"    {g.get('sPick', 'PASS')} ({g.get('sConf', '')})")

    # Print cumulative record
    print(f"\n{compute_summary_text(store)}")
    print()


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="NFL weekly picks pipeline")
    parser.add_argument(
        "--stage",
        choices=["fetch", "injuries", "grade", "project", "all"],
        default="all",
        help="Pipeline stage to run (default: all)",
    )
    parser.add_argument("--week", type=int, default=None, help="NFL week number (auto-detected if omitted)")
    parser.add_argument("--season", type=int, default=None, help="NFL season year (auto-detected if omitted)")
    args = parser.parse_args()

    season = args.season or current_season()
    week = args.week or current_nfl_week(season)

    if week < 1 or week > NFL_WEEKS_MAX:
        print(f"WARNING: Week {week} is outside season range (1-{NFL_WEEKS_MAX})")
        if week < 1:
            week = 1

    print(f"\n{'='*60}")
    print(f"  NFL Pipeline — {season} Week {week} — Stage: {args.stage}")
    print(f"{'='*60}\n")

    store = load_store()

    stage = args.stage

    if stage == "all":
        # Full pipeline: grade -> fetch -> injuries -> project
        stage_grade(season, week, store)
        stage_fetch(season, week, store)
        stage_injuries(season, week, store)
        stage_project(season, week, store)
    elif stage == "fetch":
        stage_fetch(season, week, store)
    elif stage == "injuries":
        stage_injuries(season, week, store)
    elif stage == "grade":
        stage_grade(season, week, store)
    elif stage == "project":
        stage_project(season, week, store)

    print("\nPipeline complete.\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as err:
        import traceback
        traceback.print_exc()
        sys.exit(1)
