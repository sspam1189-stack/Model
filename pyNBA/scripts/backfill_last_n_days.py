#!/usr/bin/env python3
# scripts/backfill_last_n_days.py

import json
import math
import os
import sys
import time
from datetime import datetime, timedelta

from sources.nba_stats import fetch_nba_stats, fetch_nba_stats_enhanced
from sources.blend_stats import blend_base, blend_for_game
from sources.espn_scoreboard import fetch_scoreboard, extract_final_scores
from sources.teamrankings_trends import fetch_ats_trends, fetch_ou_trends
from sources.rest_detect import apply_b2b_adjustment

from model_engine import load_defaults, get_avgs, analyze_game
from store import load_store, save_store, upsert_run
from self_tune import tune_weights, compute_residual_var
from kalman_state import (
    load_kalman_state, save_kalman_state, initialize_kalman,
    apply_daily_drift, batch_update, prune_processed_games,
)

from sources.odds_batch_historical import fetch_odds_for_day

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, "..", "data", "stats_cache")

# Days at the START of the window (oldest) used for warm-up only -- picks not evaluated
BURN_IN_DAYS = 20

# Minimum games a team must have played before we trust its stats
MIN_GAMES = 25


def sleep(seconds):
    time.sleep(seconds)


def recency_weight(days_ago):
    if days_ago <= 15:
        return 1.0
    if days_ago <= 30:
        return 0.75
    if days_ago <= 45:
        return 0.5
    return 0.25


def to_display_date(yyyymmdd_str):
    return f"{yyyymmdd_str[:4]}-{yyyymmdd_str[4:6]}-{yyyymmdd_str[6:8]}"


def yyyymmdd_from_date(d):
    return f"{d.year:04d}{d.month:02d}{d.day:02d}"


def date_minus_days_central(days_ago):
    import pytz
    tz = pytz.timezone("America/Chicago")
    now = datetime.now(tz)
    base = datetime(now.year, now.month, now.day) - timedelta(days=days_ago)
    return yyyymmdd_from_date(base)


def pick_home_away_from_scoreboard_event(ev):
    comp = (ev or {}).get("competitions", [{}])[0] if ev else {}
    competitors = comp.get("competitors", [])
    if not isinstance(competitors, list) or len(competitors) != 2:
        return None

    home = None
    away = None
    for c in competitors:
        if c.get("homeAway") == "home":
            home = c
        elif c.get("homeAway") == "away":
            away = c
    if not home or not away:
        return None

    home_team = home.get("team", {})
    away_team = away.get("team", {})
    home_name = str(home_team.get("displayName") or home_team.get("name") or home_team.get("location") or "").strip()
    away_name = str(away_team.get("displayName") or away_team.get("name") or away_team.get("location") or "").strip()

    commence_time_iso = comp.get("date")
    return {"home": home_name, "away": away_name, "commenceTimeIso": commence_time_iso}


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    if days <= 0:
        print("Usage: python scripts/backfill_last_n_days.py 8")
        sys.exit(1)

    store = load_store()
    defaults = load_defaults()

    ats = fetch_ats_trends()
    ou = fetch_ou_trends()

    # Cache stats per date
    stats_cache = {}

    def get_stats_for_date(date_yyyymmdd):
        if date_yyyymmdd in stats_cache:
            return stats_cache[date_yyyymmdd]

        # Check disk cache first
        os.makedirs(CACHE_DIR, exist_ok=True)
        disk_path = os.path.join(CACHE_DIR, date_yyyymmdd + ".json")
        if os.path.exists(disk_path):
            with open(disk_path, "r") as f:
                raw = json.load(f)
            if "season" in raw and raw.get("last10"):
                stats_cache[date_yyyymmdd] = raw
                return raw
            print(f"  [backfill] Old-format cache for {date_yyyymmdd} -- re-fetching enhanced stats...")

        # Fetch from NBA.com
        date_to = f"{date_yyyymmdd[:4]}-{date_yyyymmdd[4:6]}-{date_yyyymmdd[6:8]}"
        try:
            enhanced = fetch_nba_stats_enhanced(date_to)
        except Exception as e:
            print(f"  [backfill] Enhanced fetch failed: {e} -- trying season only")
            stats_obj = fetch_nba_stats(date_to)
            enhanced = {"season": stats_obj, "last10": None, "home": None, "away": None}
        stats_cache[date_yyyymmdd] = enhanced

        # Write to disk cache
        try:
            with open(disk_path, "w") as f:
                json.dump(enhanced, f)
        except Exception as e:
            print(f"  [backfill] cache write failed: {e}")

        return enhanced

    # Kalman state -- initialized on first date with stats, evolves forward
    kalman_state = None
    prev_date = None
    prev_graded = []       # games with projections -> Kalman + self-tune
    b2b_teams = set()      # teams that played yesterday -> B2B penalty

    for i in range(days, 0, -1):  # stop at yesterday -- never backfill today
        date = date_minus_days_central(i)
        date_display = to_display_date(date)

        base_w = store.get("weights", {})
        if not base_w or not isinstance(base_w, dict) or len(base_w) == 0:
            base_w = defaults["DEFAULT_W"]
        base_w = {**base_w}

        base_w_var = store.get("weightsVar", {})
        if not base_w_var or not isinstance(base_w_var, dict) or len(base_w_var) == 0:
            base_w_var = defaults["DEFAULT_W_VAR"]
        base_w_var = {**base_w_var}

        # Fetch enhanced stats
        try:
            enhanced = get_stats_for_date(date)
            base_stats = blend_base(enhanced["season"], enhanced.get("last10"), base_w.get("recentWeight", 0.35))
            a = get_avgs(base_stats)
        except Exception as e:
            print(f"  [backfill] Could not fetch stats for {date_display}: {e} -- skipping")
            continue

        # Initialize Kalman on first date with stats
        if kalman_state is None:
            kalman_state = initialize_kalman(enhanced["season"])
            kalman_state["lastDriftDate"] = date
            print(f"  [backfill] Kalman initialized from {date_display}")

        # -- Start of new day: process yesterday's results before today's analysis --
        # Flow: Kalman update -> self-tune -> drift -> residualVar -> analyze
        if date != prev_date:
            prev_date = date

            # 1-2. Kalman + self-tune (requires projections -- only completed games)
            if prev_graded:
                batch_update(kalman_state, prev_graded)

                in_burn_in_prev = (i + 1) > (days - BURN_IN_DAYS)
                if not in_burn_in_prev:
                    result = tune_weights(base_w, base_w_var, prev_graded)
                    base_w = result["W"]
                    base_w_var = result["W_var"]
                    store["weights"] = result["W"]
                    store["weightsVar"] = result["W_var"]

            # Build B2B set for TODAY -- teams that played yesterday
            b2b_teams = set()
            for g in prev_graded:
                if g.get("home"):
                    b2b_teams.add(g["home"])
                if g.get("away"):
                    b2b_teams.add(g["away"])

            prev_graded = []

            # Drift AFTER learning -- account for time passing to today
            apply_daily_drift(kalman_state, date)

        # Compute dynamic residualVar from history so far
        dynamic_residual_var = compute_residual_var(store.get("runs", []))

        sb = fetch_scoreboard(date)
        finals = extract_final_scores(sb)
        events = sb.get("events", []) if isinstance(sb, dict) else []

        # Collect all games for batch odds fetch
        games_list = []
        for ev in events:
            ha = pick_home_away_from_scoreboard_event(ev)
            if not ha:
                continue
            f = next((x for x in finals if x["away"] == ha["away"] and x["home"] == ha["home"]), None)
            if not f:
                continue
            games_list.append({**ha, "awayScore": f["awayScore"], "homeScore": f["homeScore"]})

        # Batch fetch ALL odds for this date in 1-2 API calls (cached to disk)
        day_odds = {}
        if games_list:
            try:
                day_odds = fetch_odds_for_day(date, games_list)
            except Exception as e:
                print(f"  [backfill] Batch odds fetch failed: {e}")

        games = []

        # Apply B2B rest penalty to today's stats (derived from yesterday's games)
        result = apply_b2b_adjustment(base_stats, b2b_teams, games_list)
        adjusted_stats = result["adjusted"]
        b2b_notes = result["b2bNotes"]

        for gl in games_list:
            key = f"{gl['away']}@{gl['home']}"
            odds = day_odds.get(key, {"line": None, "total": None, "_book": None, "_note": "No batch odds"})

            g = {
                "away": gl["away"],
                "home": gl["home"],
                "line": odds.get("line"),
                "total": odds.get("total"),
                "_book": odds.get("_book"),
            }

            if not isinstance(g["line"], (int, float)) or not isinstance(g["total"], (int, float)):
                games.append({
                    **g,
                    "awayScore": gl["awayScore"],
                    "homeScore": gl["homeScore"],
                    "status": "MISSING_ODDS",
                    "note": odds.get("_note", "Historical odds not available for this game"),
                })
                continue

            game_stats = blend_for_game(
                adjusted_stats, enhanced.get("home"), enhanced.get("away"),
                g["home"], g["away"], base_w.get("locationWeight", 0.25)
            )
            game_avgs = get_avgs(game_stats)

            # No H2H matchups for NBA model -- pass None as extra arg
            r = analyze_game(g, game_stats, game_avgs, base_w, None, kalman_state, base_w_var, dynamic_residual_var, None)
            if not r:
                games.append({
                    **g,
                    "awayScore": gl["awayScore"],
                    "homeScore": gl["homeScore"],
                    "status": "SKIPPED",
                    "note": "analyzeGame returned null (team name mismatch or bad inputs)",
                })
                continue

            r["awayScore"] = gl["awayScore"]
            r["homeScore"] = gl["homeScore"]
            r["_recencyWeight"] = recency_weight(i)

            # B2B notes for display
            away_b2b = b2b_notes.get(g["away"])
            home_b2b = b2b_notes.get(g["home"])
            if away_b2b or home_b2b:
                parts = []
                if away_b2b:
                    parts.append(f"{g['away']}: {away_b2b}")
                if home_b2b:
                    parts.append(f"{g['home']}: {home_b2b}")
                r["b2bNote"] = " | ".join(parts)

            r["trends"] = {
                "away": {
                    "atsPct": (ats.get(r["away"]) or {}).get("atsPct"),
                    "atsPlusMinus": (ats.get(r["away"]) or {}).get("atsPlusMinus"),
                    "overPct": (ou.get(r["away"]) or {}).get("overPct"),
                    "underPct": (ou.get(r["away"]) or {}).get("underPct"),
                    "totalPlusMinus": (ou.get(r["away"]) or {}).get("totalPlusMinus"),
                },
                "home": {
                    "atsPct": (ats.get(r["home"]) or {}).get("atsPct"),
                    "atsPlusMinus": (ats.get(r["home"]) or {}).get("atsPlusMinus"),
                    "overPct": (ou.get(r["home"]) or {}).get("overPct"),
                    "underPct": (ou.get(r["home"]) or {}).get("underPct"),
                    "totalPlusMinus": (ou.get(r["home"]) or {}).get("totalPlusMinus"),
                },
            }

            games.append(r)

        completed = [
            x for x in games
            if x.get("status") not in ("MISSING_ODDS", "SKIPPED")
            and isinstance(x.get("homeScore"), (int, float)) and math.isfinite(x["homeScore"])
            and isinstance(x.get("awayScore"), (int, float)) and math.isfinite(x["awayScore"])
        ]

        in_burn_in = i > (days - BURN_IN_DAYS)

        # Collect graded games for next day's processing
        for g in completed:
            g["_kalmanDate"] = date
        prev_graded = completed

        run = {
            "date": date,
            "dateDisplay": date_display,
            "burnIn": in_burn_in,
            "weightsUsed": {**base_w},
            "weightsNext": {**base_w},
            "games": games,
            "summaryText": "",
        }

        upsert_run(store, run)
        save_store(store)

        # Save Kalman state every day
        if kalman_state:
            save_kalman_state(kalman_state)

        counts = {}
        for x in games:
            k = x.get("status", "OK")
            counts[k] = counts.get(k, 0) + 1
        burn_in_tag = " [BURN-IN]" if in_burn_in else ""
        b2b_count = len(b2b_notes)
        print(
            f"Backfilled {date_display}{burn_in_tag}: games={len(games)}, completed={len(completed)} statuses={json.dumps(counts)}"
            + (f" b2b={b2b_count}" if b2b_count else "")
        )

    # Save final Kalman state
    if kalman_state:
        prune_processed_games(kalman_state, 60)
        save_kalman_state(kalman_state)
        print("Kalman state saved.")

    print("Backfill complete.")


if __name__ == "__main__":
    try:
        main()
    except Exception as err:
        import traceback
        traceback.print_exc()
        sys.exit(1)
