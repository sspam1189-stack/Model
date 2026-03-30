#!/usr/bin/env python3
"""
pyMLB/scripts/run_daily.py
Daily MLB picks pipeline — main orchestrator.

Stages:
  grade    — grade yesterday's picks, update Kalman, self-tune
  fetch    — pull team stats, pitcher stats, odds, weather, injuries
  project  — load Kalman states, run projections, save picks
  all      — grade -> fetch -> project (full pipeline)

Usage:
  python scripts/run_daily.py --stage all
  python scripts/run_daily.py --stage fetch --date 20260715
  python scripts/run_daily.py --stage grade
  python scripts/run_daily.py --stage project --date 20260715
"""

import argparse
import json
import math
import os
import re
import sys
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Path setup — ensure we can import from pyMLB/scripts/ and core/
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(SCRIPT_DIR, "..", ".."))

from dotenv import load_dotenv
load_dotenv()

# ---------------------------------------------------------------------------
# Local imports — thin wrappers over core/ + MLB-specific modules
# ---------------------------------------------------------------------------
from store import load_store, save_store, upsert_run
from self_tune import tune_weights, compute_residual_var
from kalman_state import (
    load_kalman_state, save_kalman_state, initialize_kalman,
    apply_daily_drift, batch_update, kalman_summary, prune_processed_games,
)
from calibration import build_calibration_table
import defaults

from sources.mlb_stats import fetch_team_stats
from sources.pitcher_stats import fetch_pitcher_stats
from sources.odds_fanduel import fetch_fanduel_mlb_odds as fetch_mlb_odds_fanduel
from sources.odds_theoddsapi import fetch_mlb_odds
from sources.espn_scoreboard import fetch_scoreboard, extract_final_scores, extract_all_games, fetch_date_scores
from sources.injuries import fetch_injury_data, get_probable_pitchers
from sources.park_factors import get_park_factor, get_stadium_coords, is_dome
from sources.weather import fetch_forecast, compute_weather_features

from pitcher_layer import compute_pitcher_adjustments
from model_engine import analyze_game

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
UNIT_WIN = 1.0
UNIT_LOSS = -1.1   # 1u flat, to-win-1u at standard -110 juice
CONF_ACTIONABLE = ("high", "elite")

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


# ---------------------------------------------------------------------------
# Grading helpers
# ---------------------------------------------------------------------------

def parse_spread_pick(pick):
    """Parse a pick string like 'New York Yankees -1.5' into parts."""
    if not pick or pick == "PASS":
        return None
    m = re.match(r"(.+?)\s+([+-])(\d+(?:\.\d+)?)", pick)
    if not m:
        return None
    return {"team": m.group(1).strip(), "sign": m.group(2), "pts": float(m.group(3))}


def grade_spread_pick(g):
    """Grade a spread pick against final scores. Returns WIN / LOSS / PUSH."""
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
# Team name normalization
# ---------------------------------------------------------------------------

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
            aws = g.get("awayScore")
            if not isinstance(hs, (int, float)) or not isinstance(aws, (int, float)):
                continue
            if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
                continue
            pick = dict(g)
            pick["dateKey"] = r.get("dateKey", "")
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
    o_all = tally_picks(picks, "total")

    lines = [
        "RECORD (graded picks only)", "",
        "SPREAD (ATS)",
        f"  All:      {s_all['w']}-{s_all['l']}-{s_all['p']}  ({s_all['pct']}%)  {fmt_units(s_all['units'])}",
        f"  Elite:    {s_elite['w']}-{s_elite['l']}-{s_elite['p']}  ({s_elite['pct']}%)  {fmt_units(s_elite['units'])}",
        "",
        "TOTALS (O/U)",
        f"  All:      {o_all['w']}-{o_all['l']}-{o_all['p']}  ({o_all['pct']}%)  {fmt_units(o_all['units'])}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def today_str():
    """Return today's date as YYYYMMDD in America/Chicago time."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/Chicago")).strftime("%Y%m%d")


def yesterday_str():
    from zoneinfo import ZoneInfo
    d = datetime.now(ZoneInfo("America/Chicago")) - timedelta(days=1)
    return d.strftime("%Y%m%d")


def date_to_display(date_str):
    """Convert YYYYMMDD to 'Mon Apr 7, 2026'"""
    try:
        d = datetime.strptime(date_str, "%Y%m%d")
        return d.strftime("%a %b %-d, %Y")
    except Exception:
        return date_str


def _prev_date(date_str):
    """Return the date one day before date_str as YYYYMMDD."""
    d = datetime.strptime(date_str, "%Y%m%d") - timedelta(days=1)
    return d.strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# STAGE: grade
# ---------------------------------------------------------------------------

def stage_grade(date_str, store):
    """
    Grade yesterday's picks.
    Update Kalman states and self-tune weights on graded results.
    """
    yesterday_date = _prev_date(date_str)
    print(f"\n== GRADE — grading {yesterday_date} ==\n")

    # 1. Find run for yesterday
    run = None
    for r in store.get("runs", []):
        if r.get("dateKey") == yesterday_date:
            run = r
            break

    if not run:
        print(f"  [grade] No run found for {yesterday_date} — skipping grade")
    else:
        # 2. Fetch yesterday's final scores
        print(f"[grade 1/4] Fetching final scores for {yesterday_date}...")
        try:
            finals = fetch_date_scores(yesterday_date)
        except Exception as e:
            print(f"  [grade] Could not fetch scores for {yesterday_date}: {e}")
            finals = []

        # 3. Fill in scores and grade picks
        graded_count = 0
        completed_games = []

        for g in run.get("games", []):
            if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
                continue
            if isinstance(g.get("homeScore"), (int, float)):
                completed_games.append(g)
                continue

            # Match final score by team names
            f = None
            for x in finals:
                if _match_team(x.get("away", ""), g.get("away", "")) and \
                   _match_team(x.get("home", ""), g.get("home", "")):
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
            completed_games.append(g)

        # Tally W-L-P
        wins = sum(1 for g in completed_games if g.get("sResult") == "WIN")
        losses = sum(1 for g in completed_games if g.get("sResult") == "LOSS")
        pushes = sum(1 for g in completed_games if g.get("sResult") == "PUSH")
        units = calc_units(wins, losses)
        print(f"  [grade] {graded_count} games graded for {yesterday_date}: "
              f"{wins}W-{losses}L-{pushes}P ({fmt_units(units)})")

    # 4. Load Kalman state
    print(f"[grade 2/4] Updating Kalman filter...")
    kalman_state = load_kalman_state()

    if not kalman_state.get("teams"):
        # Try to initialize from team stats if available
        try:
            season = int(date_str[:4])
            month = int(date_str[4:6])
            if month < 4:
                season -= 1
            team_stats = fetch_team_stats(season=season, date_str=date_str)
            if team_stats:
                kalman_state = initialize_kalman(team_stats)
                print("  Kalman initialized from current team stats")
        except Exception as e:
            print(f"  WARNING: Could not initialize Kalman from team stats: {e}")

    completed_games_list = completed_games if run else []
    if completed_games_list:
        for g in completed_games_list:
            g["_kalmanDate"] = yesterday_date
        batch_update(kalman_state, completed_games_list)
        print(f"  Kalman updated with {len(completed_games_list)} games")

    # 5. Self-tune weights
    print(f"[grade 3/4] Self-tuning weights...")
    base_w = store.get("weights") or defaults.DEFAULT_W
    base_w_var = store.get("weightsVar") or defaults.DEFAULT_W_VAR
    base_w = {**base_w}
    base_w_var = {**base_w_var}

    if completed_games_list:
        try:
            tuned = tune_weights(base_w, base_w_var, completed_games_list)
            store["weights"] = tuned["W"]
            store["weightsVar"] = tuned["W_var"]
            print(f"  Weights tuned on {len(completed_games_list)} games")
        except Exception as e:
            print(f"  WARNING: Self-tune failed: {e}")

    # 6. Apply drift, prune, save
    print(f"[grade 4/4] Saving Kalman state...")
    apply_daily_drift(kalman_state, date_str)
    prune_processed_games(kalman_state, 90)
    save_kalman_state(kalman_state)
    save_store(store)
    print("  Kalman state saved")


# ---------------------------------------------------------------------------
# STAGE: fetch
# ---------------------------------------------------------------------------

def stage_fetch(date_str, store):
    """
    Pull team stats, pitcher stats, odds, weather, injuries for the given date.
    """
    month = int(date_str[4:6])
    season = int(date_str[:4]) if month >= 4 else int(date_str[:4]) - 1

    print(f"\n== FETCH — {date_to_display(date_str)} (season {season}) ==\n")

    # 1. Fetch team stats
    print("[fetch 1/5] Fetching team stats...")
    try:
        team_stats = fetch_team_stats(season=season, date_str=date_str)
        print(f"  Got stats for {len(team_stats)} teams")
    except Exception as e:
        print(f"  WARNING: Team stats fetch failed: {e}")
        team_stats = {}

    # 2. Fetch pitcher stats
    print("[fetch 2/5] Fetching pitcher stats...")
    try:
        pitcher_stats = fetch_pitcher_stats(season=season, date_str=date_str)
        print(f"  Got pitcher stats for {len(pitcher_stats)} pitchers")
    except Exception as e:
        print(f"  WARNING: Pitcher stats fetch failed: {e}")
        pitcher_stats = {}

    # 3. Fetch injury data
    print("[fetch 3/5] Fetching injury data...")
    try:
        injury_data = fetch_injury_data()
        report = injury_data.get("report", {}) if isinstance(injury_data, dict) else {}
        print(f"  Got injury data for {len(report)} teams")
    except Exception as e:
        print(f"  WARNING: Injury fetch failed: {e}")
        injury_data = {}

    # 4. Fetch probable pitchers
    print("[fetch 4/5] Fetching probable pitchers...")
    try:
        probable_pitchers = get_probable_pitchers()
        print(f"  Got probable pitchers for {len(probable_pitchers)} games")
    except Exception as e:
        print(f"  WARNING: Probable pitchers fetch failed: {e}")
        probable_pitchers = {}

    # 5. Fetch odds (FanDuel first, fall back to TheOddsAPI)
    print("[fetch 5/5] Fetching odds...")
    odds = []
    odds_source = "None"
    try:
        odds = fetch_mlb_odds_fanduel()
        if odds:
            odds_source = "FanDuel"
            print(f"  Got odds for {len(odds)} games from FanDuel")
        else:
            raise ValueError("FanDuel returned empty odds")
    except Exception as e:
        print(f"  FanDuel odds failed ({e}), falling back to TheOddsAPI...")
        try:
            odds = fetch_mlb_odds(date_str=date_str)
            if odds:
                odds_source = "TheOddsAPI"
                print(f"  Got odds for {len(odds)} games from TheOddsAPI")
            else:
                print("  WARNING: No odds returned from either source")
        except Exception as e2:
            print(f"  WARNING: TheOddsAPI odds also failed: {e2}")
            odds = []

    # 6. Fetch today's games from ESPN (for game times and enrichment)
    print("[fetch] Fetching ESPN scoreboard...")
    try:
        espn_games = extract_all_games(fetch_scoreboard(date_str))
        print(f"  Got {len(espn_games)} games from ESPN")
    except Exception as e:
        print(f"  WARNING: ESPN scoreboard fetch failed: {e}")
        espn_games = []

    # Cache all fetched data in store
    store["_fetch"] = {
        "date": date_str,
        "season": season,
        "team_stats": team_stats,
        "pitcher_stats": pitcher_stats,
        "injury_data": injury_data,
        "probable_pitchers": probable_pitchers,
        "odds": odds,
        "odds_source": odds_source,
        "games": espn_games,
    }

    print(f"\nFetch complete: {len(team_stats)} teams, {len(pitcher_stats)} pitchers, "
          f"{len(odds)} games with odds (source: {odds_source})")


# ---------------------------------------------------------------------------
# STAGE: project
# ---------------------------------------------------------------------------

def stage_project(date_str, store):
    """
    Load Kalman states, weights, thresholds.
    For each matchup: run analyze_game from model_engine.py.
    Save picks to store. Print summary.
    """
    month = int(date_str[4:6])
    season = int(date_str[:4]) if month >= 4 else int(date_str[:4]) - 1

    print(f"\n== PROJECT — {date_to_display(date_str)} (season {season}) ==\n")

    # 1. Load Kalman state
    kalman_state = load_kalman_state()
    team_stats = store.get("_fetch", {}).get("team_stats", {})

    if not kalman_state.get("teams"):
        if team_stats:
            kalman_state = initialize_kalman(team_stats)
            print("  Kalman initialized from team stats")
        else:
            print("  ERROR: No Kalman state and no team stats available")
            return

    # 2. Apply daily drift
    apply_daily_drift(kalman_state, date_str)

    # 3. Build weights dict
    weights_dict = {}
    try:
        weights_path = os.path.join(SCRIPT_DIR, "..", "data", "weights.json")
        if os.path.exists(weights_path):
            with open(weights_path) as f:
                weights_dict = json.load(f)
    except Exception:
        import model_engine as _me
        weights_dict = _me._default_weights_dict()

    # Overlay tuned threshold values from store
    for k, v in (store.get("weights") or {}).items():
        weights_dict[k] = v

    weights_used = {**weights_dict}

    # 4. Compute dynamic residual variance
    dynamic_residual_var = compute_residual_var(store.get("runs", []))
    store["residualVar"] = dynamic_residual_var

    # 5. Get odds
    odds = store.get("_fetch", {}).get("odds", [])
    if not odds:
        print("  WARNING: No odds in store — attempting fresh fetch...")
        try:
            odds = fetch_mlb_odds_fanduel()
        except Exception:
            odds = []
        if not odds:
            try:
                odds = fetch_mlb_odds(date_str=date_str)
            except Exception as e:
                print(f"  ERROR: Could not fetch odds: {e}")
                odds = []

    pitcher_stats = store.get("_fetch", {}).get("pitcher_stats", {})
    injury_data = store.get("_fetch", {}).get("injury_data", {})
    probable_pitchers = store.get("_fetch", {}).get("probable_pitchers", {})

    # 6. Analyze each matchup
    print(f"[project] Analyzing {len(odds)} matchups...")
    games = []

    for g in odds:
        if not isinstance(g.get("line"), (int, float)) or not isinstance(g.get("total"), (int, float)):
            games.append({**g, "status": "MISSING_ODDS"})
            continue

        home_name = g.get("home", "")
        away_name = g.get("away", "")

        # a. Pitcher adjustments
        try:
            pitcher_adjustments = compute_pitcher_adjustments(
                g,
                pitcher_stats,
                {"report": injury_data, "probable_pitchers": probable_pitchers},
            )
        except Exception as e:
            print(f"  WARNING: pitcher adjustments failed for {away_name}@{home_name}: {e}")
            pitcher_adjustments = {}

        # b. Park factor and weather enrichment
        try:
            park_factor = get_park_factor(home_name)
        except Exception:
            park_factor = 1.0

        try:
            if not is_dome(home_name):
                coords = get_stadium_coords(home_name)
                if coords and g.get("commenceTimeIso"):
                    wx = fetch_forecast(coords[0], coords[1], g["commenceTimeIso"])
                    weather_features = compute_weather_features(wx)
                else:
                    weather_features = {"temperature_adj": 0.0, "wind_adj": 0.0}
            else:
                weather_features = {"temperature_adj": 0.0, "wind_adj": 0.0}
        except Exception as e:
            print(f"  WARNING: weather fetch failed for {home_name}: {e}")
            weather_features = {"temperature_adj": 0.0, "wind_adj": 0.0}

        g["_parkFactor"] = park_factor
        g["_weather"] = weather_features

        # c. Run model
        try:
            r = analyze_game(
                g,
                team_stats,
                pitcher_adjustments,
                weights_dict,
                kalman_states=kalman_state,
                residual_var=dynamic_residual_var,
            )
        except Exception as e:
            print(f"  WARNING: analyze_game failed for {away_name}@{home_name}: {e}")
            r = None

        if not r:
            games.append({**g, "status": "SKIPPED", "note": "analyze_game returned None"})
            continue

        games.append(r)

    # 7. Build and save run
    run = {
        "dateKey": date_str,
        "weekKey": date_str,   # compatibility with upsert_run (matches on "date" field)
        "season": season,
        "date": date_str,
        "dateDisplay": date_to_display(date_str),
        "burnIn": False,
        "weightsUsed": {**weights_used},
        "games": games,
        "summaryText": compute_summary_text(store),
    }

    upsert_run(store, run)
    save_kalman_state(kalman_state)
    save_store(store)

    # 8. Print picks table
    print(f"\n{'='*80}")
    print(f"  MLB PICKS — {date_to_display(date_str)}")
    print(f"{'='*80}\n")

    actionable = []
    for g in games:
        if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
            continue

        pick = g.get("sPick", "PASS")
        conf = g.get("sConf", "")
        o_pick = g.get("oPick", "PASS")
        o_conf = g.get("oConf", "")
        proj_spread = g.get("projSpread", 0)
        line = g.get("line", 0)
        proj_total = g.get("projTotal", 0)
        total = g.get("total", 0)
        home_sp = g.get("_homeSP", "")
        away_sp = g.get("_awaySP", "")

        marker = ""
        if is_actionable(conf) or is_actionable(o_conf):
            marker = " ***" if (conf == "elite" or o_conf == "elite") else " **"
            actionable.append(g)

        sp_str = f"[{away_sp} vs {home_sp}]" if (away_sp or home_sp) else ""
        print(
            f"  {g.get('away', '?'):24s} @ {g.get('home', '?'):24s}  "
            f"Line:{fmt_num(line, 1):>6s}  Proj:{fmt_num(proj_spread, 1):>6s}  "
            f"Ou:{fmt_num(total, 1):>5s}  ProjTot:{fmt_num(proj_total, 1):>5s}  "
            f"=> {pick:28s} [{conf}] / {o_pick:5s} [{o_conf}]{marker}"
        )
        if sp_str:
            print(f"    {sp_str}")

    print(f"\n  Total games: {len(games)}")
    print(f"  Actionable picks: {len(actionable)}")
    if actionable:
        print(f"\n  Actionable summary:")
        for g in actionable:
            s_line = f"    {g.get('sPick', 'PASS')} ({g.get('sConf', '')})" if g.get("sPick", "PASS") != "PASS" else ""
            o_line = f"    {g.get('oPick', 'PASS')} ({g.get('oConf', '')})" if g.get("oPick", "PASS") != "PASS" else ""
            if s_line:
                print(s_line)
            if o_line:
                print(o_line)

    print(f"\n{compute_summary_text(store)}")
    print()


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="MLB daily picks pipeline")
    parser.add_argument(
        "--stage",
        choices=["fetch", "grade", "project", "all"],
        default="all",
        help="Pipeline stage to run (default: all)",
    )
    parser.add_argument("--date", type=str, default=None, help="Date YYYYMMDD (default: today)")
    args = parser.parse_args()

    date_str = args.date or today_str()

    print(f"\n{'='*60}")
    print(f"  MLB Pipeline — {date_to_display(date_str)} — Stage: {args.stage}")
    print(f"{'='*60}\n")

    store = load_store()

    if args.stage == "all":
        stage_grade(date_str, store)
        stage_fetch(date_str, store)
        stage_project(date_str, store)
    elif args.stage == "fetch":
        stage_fetch(date_str, store)
    elif args.stage == "grade":
        stage_grade(date_str, store)
    elif args.stage == "project":
        stage_project(date_str, store)

    print("\nPipeline complete.\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as err:
        import traceback
        traceback.print_exc()
        sys.exit(1)
