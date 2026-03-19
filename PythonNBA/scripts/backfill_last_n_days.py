# scripts/backfill_last_n_days.py
import os
import sys
import json
import math
import time
from datetime import datetime, timedelta

from sources.nba_stats import fetch_nba_stats, fetch_nba_stats_enhanced
from sources.blend_stats import blend_base, blend_for_game
from sources.espn_scoreboard import fetch_scoreboard, extract_final_scores
from sources.teamrankings_trends import fetch_ats_trends, fetch_ou_trends
from sources.odds_theoddsapi_historical import fetch_closing_odds_for_game
from model_engine import load_defaults, get_avgs, analyze_game
from store import load_store, save_store, upsert_run
from self_tune import tune_weights

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
            enhanced = raw if "season" in raw else {"season": raw, "last10": None, "home": None, "away": None}
            stats_cache[date_yyyymmdd] = enhanced
            return enhanced

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

    for i in range(days, -1, -1):
        date = date_minus_days_central(i)
        date_display = to_display_date(date)

        base_w = store.get("weights", {})
        if not base_w or not isinstance(base_w, dict) or len(base_w) == 0:
            base_w = defaults["DEFAULT_W"]
        base_w_var = store.get("weightsVar", {})
        if not base_w_var or not isinstance(base_w_var, dict) or len(base_w_var) == 0:
            base_w_var = defaults["DEFAULT_W_VAR"]

        # Fetch enhanced stats
        try:
            enhanced = get_stats_for_date(date)
            base_stats = blend_base(enhanced["season"], enhanced.get("last10"), base_w.get("recentWeight", 0.35))
            a = get_avgs(base_stats)
        except Exception as e:
            print(f"  [backfill] Could not fetch stats for {date_display}: {e} -- skipping")
            continue

        sb = fetch_scoreboard(date)
        finals = extract_final_scores(sb)
        events = sb.get("events", []) if isinstance(sb, dict) else []

        games = []

        for ev in events:
            ha = pick_home_away_from_scoreboard_event(ev)
            if not ha:
                continue

            f = None
            for x in finals:
                if x["away"] == ha["away"] and x["home"] == ha["home"]:
                    f = x
                    break
            if not f:
                continue

            odds_data = {"line": None, "total": None, "_book": None, "_note": None}
            try:
                odds_data = fetch_closing_odds_for_game({
                    "home": ha["home"],
                    "away": ha["away"],
                    "commenceTimeIso": ha["commenceTimeIso"]
                })
            except Exception as e:
                odds_data = {"line": None, "total": None, "_book": None, "_note": str(e)}

            sleep(0.35)

            g = {
                "away": ha["away"],
                "home": ha["home"],
                "line": odds_data.get("line"),
                "total": odds_data.get("total"),
                "_book": odds_data.get("_book"),
            }

            if not isinstance(g["line"], (int, float)) or not isinstance(g["total"], (int, float)):
                g["awayScore"] = f["awayScore"]
                g["homeScore"] = f["homeScore"]
                g["status"] = "MISSING_ODDS"
                g["note"] = odds_data.get("_note") or "Historical odds not available for this game"
                games.append(g)
                continue

            game_stats = blend_for_game(
                base_stats, enhanced.get("home"), enhanced.get("away"),
                g["home"], g["away"], base_w.get("locationWeight", 0.25)
            )
            game_avgs = get_avgs(game_stats)

            r = analyze_game(g, game_stats, game_avgs, base_w)
            if not r:
                g["awayScore"] = f["awayScore"]
                g["homeScore"] = f["homeScore"]
                g["status"] = "SKIPPED"
                g["note"] = "analyzeGame returned null (team name mismatch or bad inputs)"
                games.append(g)
                continue

            r["awayScore"] = f["awayScore"]
            r["homeScore"] = f["homeScore"]
            r["_recencyWeight"] = recency_weight(i)

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

        # BURN-IN GUARD
        in_burn_in = i > (days - BURN_IN_DAYS)
        if in_burn_in:
            tuned_w, tuned_w_var = base_w, base_w_var
        else:
            result = tune_weights(base_w, base_w_var, completed)
            tuned_w = result["W"]
            tuned_w_var = result["W_var"]
        store["weights"] = tuned_w
        store["weightsVar"] = tuned_w_var

        run = {
            "date": date,
            "dateDisplay": date_display,
            "burnIn": in_burn_in,
            "weightsUsed": base_w,
            "weightsNext": tuned_w,
            "weightsVar": tuned_w_var,
            "games": games,
            "summaryText": "",
        }

        upsert_run(store, run)
        save_store(store)

        counts = {}
        for x in games:
            k = x.get("status", "OK")
            counts[k] = counts.get(k, 0) + 1
        burn_in_tag = " [BURN-IN]" if in_burn_in else ""
        print(f"Backfilled {date_display}{burn_in_tag}: games={len(games)}, completed={len(completed)} statuses={json.dumps(counts)}")

    print("Backfill complete.")


if __name__ == "__main__":
    try:
        main()
    except Exception as err:
        import traceback
        traceback.print_exc()
        sys.exit(1)
