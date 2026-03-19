# scripts/update.py
# Daily NBA picks pipeline (update variant):
# Same as run_daily.py but applies Kalman drift BEFORE feeding graded games
# (drift then learn, vs run_daily which learns then drifts).

import os
import sys
import json
import math
import re
from datetime import datetime, timedelta

from dotenv import load_dotenv
load_dotenv()

# Import everything from run_daily -- this file shares all the same logic
from run_daily import (
    esc, fmt_num, calc_units, total_pick_unit, fmt_units, win_pct, is_actionable,
    norm_key, MASCOTS, TEAM_ALIASES, extract_mascot, resolve_key, match_team,
    central_date_parts, today_central_yyyymmdd, yyyymmdd_from_central_offset, to_display_date,
    join_trends, parse_spread_pick, grade_spread_pick, grade_total_pick, grade_result,
    grade_date_in_store, compute_team_records, build_team_record_table,
    get_graded_picks, tally_picks, compute_summary_obj, compute_summary_text,
    get_actionable_picks, compute_last10, last10_text,
    compute_rolling_windows, compute_weekly_breakdown,
    compute_yesterday_recap, build_recap_html, build_game_injury_adj,
    build_email_html, build_text_email,
    TIMEZONE, CONF_ACTIONABLE, UNIT_LOSS,
)

from sources.nba_stats import fetch_nba_stats_enhanced
from sources.blend_stats import blend_base, blend_for_game
from sources.odds_theoddsapi import fetch_todays_odds
from sources.espn_scoreboard import fetch_scoreboard, extract_final_scores
from sources.teamrankings_trends import fetch_ats_trends, fetch_ou_trends
from sources.injuries import fetch_injury_data, get_key_injuries
from sources.lineup_adjust import fetch_player_advanced, adjust_team_stats
from sources.rest_detect import detect_b2b, apply_b2b_adjustment
from sources.season_type import get_season_type, get_espn_season_type
from model_engine import load_defaults, get_avgs, analyze_game
from store import load_store, save_store, upsert_run
from self_tune import tune_weights, compute_residual_var
from kalman_state import (
    load_kalman_state, save_kalman_state, initialize_kalman,
    apply_daily_drift, batch_update, kalman_summary, prune_processed_games,
)
from calibration import build_calibration_table, build_calibration_html
from email_report import send_email


def main():
    date = today_central_yyyymmdd()
    date_display = to_display_date(date)
    store = load_store()
    defaults = load_defaults()

    print(f"\n\u2550\u2550 NBA Picks Pipeline \u2014 {date_display} \u2550\u2550\n")

    # 1. Grade recent days
    days_to_grade = set()
    for d in range(1, 6):
        days_to_grade.add(yyyymmdd_from_central_offset(d))
    for r in store.get("runs", []):
        has_ungraded = any(
            g.get("status") not in ("MISSING_ODDS", "SKIPPED") and
            not isinstance(g.get("homeScore"), (int, float)) and
            ((g.get("sPick") and g["sPick"] != "PASS") or (g.get("oPick") and g["oPick"] != "PASS"))
            for g in r.get("games", [])
        )
        if has_ungraded:
            days_to_grade.add(r["date"])

    yesterday = yyyymmdd_from_central_offset(1)
    for d in days_to_grade:
        grade_date_in_store(store, d)

    # 2. Fetch today's data
    season_type = get_season_type(date)
    espn_type = get_espn_season_type(date)
    print(f"[1/7] Fetching stats, odds, trends, injuries, player data... [{season_type}]")

    enhanced_stats = fetch_nba_stats_enhanced(date, season_type=season_type)
    odds = fetch_todays_odds()
    ats = fetch_ats_trends()
    ou = fetch_ou_trends()
    try:
        injury_data = fetch_injury_data(None, season_type=season_type, espn_type=espn_type)
    except Exception as e:
        print(f"  Warning: Injury fetch failed: {e}")
        injury_data = {"report": {}, "playerMPG": {}}
    try:
        player_advanced = fetch_player_advanced(season_type=season_type)
    except Exception as e:
        print(f"  Warning: Player advanced fetch failed: {e}")
        player_advanced = {}
    try:
        b2b_teams = detect_b2b()
    except Exception:
        b2b_teams = set()

    base_w = store.get("weights") or defaults["DEFAULT_W"]
    base_w_var = store.get("weightsVar") or defaults["DEFAULT_W_VAR"]

    stats = blend_base(enhanced_stats["season"], enhanced_stats.get("last10"), base_w.get("recentWeight", 0.35))

    # Cache stats to disk
    try:
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        cache_dir = os.path.join(scripts_dir, "..", "data", "stats_cache")
        os.makedirs(cache_dir, exist_ok=True)
        with open(os.path.join(cache_dir, date + ".json"), "w") as f:
            json.dump(enhanced_stats, f)
    except Exception:
        pass

    # 3. Lineup-adjusted stats + B2B rest + Kalman state
    print("[2/7] Applying lineup adjustments + B2B rest + Kalman filter...")

    lineup_stats = adjust_team_stats(stats, injury_data.get("report", {}), injury_data.get("playerMPG", {}), player_advanced, odds)
    result = apply_b2b_adjustment(lineup_stats, b2b_teams, odds)
    adjusted_stats = result["adjusted"]
    b2b_notes = result.get("b2bNotes", {})
    a = get_avgs(adjusted_stats)

    STAT_KEYS = ["OFF", "DEF", "TS", "TO", "ORR", "PACE"]
    def compute_team_delta(team):
        base = stats.get(team)
        adj = adjusted_stats.get(team)
        if not base or not adj or base is adj:
            return None
        d = {}
        any_diff = False
        for k in STAT_KEYS:
            diff = (adj.get(k, 0) or 0) - (base.get(k, 0) or 0)
            if abs(diff) > 0.0001:
                d[k] = round(diff * 10000) / 10000
                any_diff = True
        return d if any_diff else None

    kalman_state = load_kalman_state()
    if not kalman_state.get("teams"):
        kalman_state = initialize_kalman(stats)

    # KEY DIFFERENCE: drift BEFORE learning (update.mjs order)
    apply_daily_drift(kalman_state, date)

    # Feed newly graded games into Kalman
    newly_graded = []
    for r in (store.get("runs") or [])[-14:]:
        if r.get("date") == date:
            continue
        for g in r.get("games", []):
            if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
                continue
            if not isinstance(g.get("homeScore"), (int, float)) or not isinstance(g.get("awayScore"), (int, float)):
                continue
            if not isinstance(g.get("hS"), (int, float)) or not isinstance(g.get("aS"), (int, float)):
                continue
            g["_kalmanDate"] = r["date"]
            newly_graded.append(g)
    batch_update(kalman_state, newly_graded)

    # 3b. Self-tune weights
    yesterday_run = None
    for r in store.get("runs", []):
        if r.get("date") == yesterday:
            yesterday_run = r
            break
    recent_graded = []
    if yesterday_run:
        for g in yesterday_run.get("games", []):
            if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
                continue
            if not isinstance(g.get("homeScore"), (int, float)) or not isinstance(g.get("awayScore"), (int, float)):
                continue
            if not g.get("_features"):
                continue
            recent_graded.append(g)
    if recent_graded and store.get("lastTuneDate") != date:
        tuned = tune_weights(base_w, base_w_var, recent_graded)
        base_w = tuned["W"]
        base_w_var = tuned["W_var"]
        store["weights"] = tuned["W"]
        store["weightsVar"] = tuned["W_var"]
        store["lastTuneDate"] = date
        print(f"[3b] Weights tuned on {len(recent_graded)} graded games from {yesterday}")
    elif store.get("lastTuneDate") == date:
        print("[3b] Weights already tuned today -- skipping")

    # 3c. Compute dynamic residualVar
    dynamic_residual_var = compute_residual_var(store.get("runs", []))
    store["residualVar"] = dynamic_residual_var

    # 4. Analyze each game
    print(f"[3/7] Analyzing {len(odds)} games...")
    games = []
    for g in odds:
        if not isinstance(g.get("line"), (int, float)) or not isinstance(g.get("total"), (int, float)):
            games.append(dict(g, status="MISSING_ODDS"))
            continue

        try:
            injury_adj = build_game_injury_adj(g["away"], g["home"], injury_data.get("report", {}), injury_data.get("playerMPG"))
        except Exception:
            injury_adj = None

        game_stats = blend_for_game(
            adjusted_stats, enhanced_stats.get("home"), enhanced_stats.get("away"),
            g["home"], g["away"], base_w.get("locationWeight", 0.25)
        )
        game_avgs = get_avgs(game_stats)

        r = analyze_game(g, game_stats, game_avgs, base_w, injury_adj, kalman_state, base_w_var, dynamic_residual_var)

        if not r:
            games.append(dict(g, status="SKIPPED"))
            continue

        away_delta = compute_team_delta(g["away"])
        home_delta = compute_team_delta(g["home"])
        if away_delta or home_delta:
            r["_adjDeltas"] = {}
            if away_delta:
                r["_adjDeltas"]["away"] = away_delta
            if home_delta:
                r["_adjDeltas"]["home"] = home_delta

        games.append(r)

    # 5. Attach trends
    for g in games:
        if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
            continue
        ats_key_a = resolve_key(ats, g["away"])
        ats_key_h = resolve_key(ats, g["home"])
        ou_key_a = resolve_key(ou, g["away"])
        ou_key_h = resolve_key(ou, g["home"])
        g["trends"] = {
            "away": join_trends(g["away"], ats.get(ats_key_a) if ats_key_a else None, ou.get(ou_key_a) if ou_key_a else None),
            "home": join_trends(g["home"], ats.get(ats_key_h) if ats_key_h else None, ou.get(ou_key_h) if ou_key_h else None),
        }
        away_b2b = b2b_notes.get(g["away"])
        home_b2b = b2b_notes.get(g["home"])
        if away_b2b or home_b2b:
            parts = []
            if away_b2b:
                parts.append(f"{g['away']}: {away_b2b}")
            if home_b2b:
                parts.append(f"{g['home']}: {home_b2b}")
            g["b2bNote"] = " | ".join(parts)

    # 6. Build run record
    print("[4/7] Saving...")
    run = {"date": date, "dateDisplay": date_display, "burnIn": False, "weightsUsed": base_w, "weightsNext": base_w, "games": games, "summaryText": ""}

    # 7. Save state
    print("[5/7] Saving...")
    run["summaryText"] = compute_summary_text(store)
    upsert_run(store, run)
    save_store(store)

    prune_processed_games(kalman_state, 30)
    save_kalman_state(kalman_state)

    # 8. Compute trend data
    summary_obj = compute_summary_obj(store)
    l10 = compute_last10(store, "spread")
    l10t = compute_last10(store, "total")
    weekly_spread = compute_weekly_breakdown(store, "spread")
    weekly_total = compute_weekly_breakdown(store, "total")
    rolling_spread = compute_rolling_windows(store, "spread")
    rolling_total = compute_rolling_windows(store, "total")
    team_records_data = compute_team_records(store)
    calib_rows = build_calibration_table(store)
    yesterday_recap = compute_yesterday_recap(store, yesterday)

    # 9. Send email
    print("[6/7] Sending email...")
    html = build_email_html(run, summary_obj, l10, l10t, weekly_spread, weekly_total, rolling_spread, rolling_total, team_records_data, calib_rows, yesterday_recap)
    text = build_text_email(run, store)
    subject = f"[PY] NBA Update Picks \u2014 {run['dateDisplay']}"

    send_email(subject, text, html)

    # 10. Summary
    print(f"\nDone: {subject}")


if __name__ == "__main__":
    try:
        main()
    except Exception as err:
        import traceback
        traceback.print_exc()
        sys.exit(1)
