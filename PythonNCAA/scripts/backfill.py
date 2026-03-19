#!/usr/bin/env python3
# scripts/backfill.py
# Replays NCAA games from a start date through yesterday to train the model
# AND build a win/loss record using historical odds from The Odds API.
#
# Usage:
#   python scripts/backfill.py                  # Jan 1 -> yesterday
#   python scripts/backfill.py 20260201         # Feb 1 -> yesterday
#   python scripts/backfill.py 20260101 20260301 # Jan 1 -> Mar 1

import os
import sys
import re
import json
import math
import time
from datetime import datetime, timedelta
from urllib.parse import quote

from dotenv import load_dotenv
load_dotenv()

import requests

from sources.ncaa_stats import fetch_ncaa_stats
from sources.espn_scoreboard import fetch_scoreboard, extract_final_scores
from store import load_store, save_store, upsert_run
from kalman_state import (
    load_kalman_state, save_kalman_state, initialize_kalman,
    apply_daily_drift, batch_update, kalman_summary, prune_processed_games,
)
from self_tune import tune_weights, compute_residual_var
from model_engine import analyze_game, get_avgs, load_defaults
from sources.blend_stats import blend_base

# -- Date helpers --------------------------------------------------------------

def today_cst():
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/Chicago"))
    return now.strftime("%Y%m%d")


def add_days(yyyymmdd, n):
    d = datetime(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:8]))
    d += timedelta(days=n)
    return d.strftime("%Y%m%d")


def date_range(start, end):
    dates = []
    cur = start
    while cur <= end:
        dates.append(cur)
        cur = add_days(cur, 1)
    return dates


def fmt_date(d):
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}"

# -- Fuzzy team name matcher ---------------------------------------------------

def norm_key(s):
    s = str(s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\bstate\b", "st", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def safe_fuzzy(a, b):
    if not a or not b:
        return False
    if a == b:
        return True
    shorter = a if len(a) < len(b) else b
    longer = b if len(a) < len(b) else a
    if len(shorter) / len(longer) < 0.85:
        return False
    return shorter in longer


def resolve_team_fuzzy(stats, name):
    if not name:
        return None
    if name in stats:
        return name
    keys = list(stats.keys())
    wanted = norm_key(name)

    for k in keys:
        if norm_key(k) == wanted:
            return k
    for k in keys:
        if safe_fuzzy(norm_key(k), wanted):
            return k
    prefix_match = None
    for k in keys:
        nk = norm_key(k)
        if len(nk) < 3:
            continue
        if wanted.startswith(nk + " ") or wanted == nk:
            if not prefix_match or len(nk) > len(norm_key(prefix_match)):
                prefix_match = k
    if prefix_match:
        return prefix_match
    first_word = wanted.split(" ")[0]
    if len(first_word) >= 3:
        for k in keys:
            nk = norm_key(k)
            if nk == first_word or (len(nk) >= 3 and first_word.startswith(nk)):
                return k
    last_word = wanted.split(" ")[-1]
    if len(last_word) >= 4:
        for k in keys:
            k_last = norm_key(k).split(" ")[-1]
            if k_last == last_word:
                return k
    return None

# -- Historical Odds from The Odds API ----------------------------------------

BASE = "https://api.the-odds-api.com/v4"


def norm_team(name):
    return str(name or "").strip()


def pick_best_bookmaker(bookmakers):
    if not bookmakers:
        return None
    preferred = ["DraftKings", "FanDuel", "BetMGM", "Caesars", "PointsBet", "BetRivers"]
    for p in preferred:
        for b in bookmakers:
            if b and b.get("title") == p:
                return b
    return bookmakers[0]


def to_model_line(home_team, away_team, spread_points, team_for_spread):
    if not isinstance(spread_points, (int, float)) or not math.isfinite(spread_points):
        return None
    abs_val = abs(spread_points)
    is_home = team_for_spread == home_team
    is_away = team_for_spread == away_team
    if not is_home and not is_away:
        return None
    if is_home:
        return abs_val if spread_points < 0 else -abs_val
    return -abs_val if spread_points < 0 else abs_val


def parse_odds_snapshot(data):
    if not isinstance(data, list):
        return []
    games = []
    for ev in data:
        home = norm_team(ev.get("home_team"))
        away = norm_team(ev.get("away_team"))
        if not home or not away:
            continue
        commence = ev.get("commence_time")
        commence_dt = datetime.fromisoformat(commence.replace("Z", "+00:00")) if commence else None
        book = pick_best_bookmaker(ev.get("bookmakers"))
        line = None
        total = None
        if book:
            spreads = next((m for m in (book.get("markets") or []) if m and m.get("key") == "spreads"), None)
            totals = next((m for m in (book.get("markets") or []) if m and m.get("key") == "totals"), None)
            if spreads and spreads.get("outcomes"):
                for out in spreads["outcomes"]:
                    pt = out.get("point")
                    if pt is not None and isinstance(pt, (int, float)):
                        line = to_model_line(home, away, float(pt), norm_team(out.get("name")))
                        break
            if totals and totals.get("outcomes"):
                for out in totals["outcomes"]:
                    pt = out.get("point")
                    if pt is not None and isinstance(pt, (int, float)):
                        total = float(pt)
                        break
        if line is not None:
            games.append({"away": away, "home": home, "line": line, "total": total, "commence": commence_dt, "_book": book.get("title") if book else None})
    return games


def fetch_snapshot_at(date_yyyymmdd, hour_utc):
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        raise RuntimeError("Missing ODDS_API_KEY in .env")
    hh = str(hour_utc).zfill(2)
    iso_date = f"{fmt_date(date_yyyymmdd)}T{hh}:00:00Z"
    url = (f"{BASE}/historical/sports/basketball_ncaab/odds?"
           f"apiKey={quote(api_key)}&regions=us&markets=spreads,totals"
           f"&oddsFormat=american&date={quote(iso_date)}")
    resp = requests.get(url)
    if not resp.ok:
        if resp.status_code == 422:
            return {"data": [], "snapshotTime": datetime.fromisoformat(iso_date.replace("Z", "+00:00"))}
        raise RuntimeError(f"Odds API {resp.status_code}: {resp.text[:200]}")
    j = resp.json()
    raw = j.get("data") or j
    remaining = resp.headers.get("x-requests-remaining")
    if remaining:
        rem = int(remaining)
        if rem < 100:
            print(f"  Warning: Odds API credits remaining: {rem}")
    return {"data": parse_odds_snapshot(raw), "snapshotTime": datetime.fromisoformat(iso_date.replace("Z", "+00:00"))}


def fetch_historical_odds(date_yyyymmdd):
    snapshot_hours = [13, 15, 17, 19, 21, 23]
    snapshots = []
    for h in snapshot_hours:
        try:
            snap = fetch_snapshot_at(date_yyyymmdd, h)
            if snap["data"]:
                snapshots.append(snap)
            time.sleep(0.2)
        except Exception:
            pass
    if not snapshots:
        return []
    best_lines = {}
    for snap in snapshots:
        for g in snap["data"]:
            key = f"{norm_key(g['home'])}|{norm_key(g['away'])}"
            tipoff = g.get("commence")
            if not tipoff:
                if key not in best_lines:
                    best_lines[key] = {"game": g, "timeBefore": float("inf")}
                continue
            ms_before = (tipoff - snap["snapshotTime"]).total_seconds()
            min_before = ms_before / 60
            if min_before < 60:
                continue
            existing = best_lines.get(key)
            if not existing:
                best_lines[key] = {"game": g, "timeBefore": min_before}
            elif min_before < existing["timeBefore"]:
                best_lines[key] = {"game": g, "timeBefore": min_before}
    return [v["game"] for v in best_lines.values()]

# -- Grading functions ---------------------------------------------------------

def parse_spread_pick(pick):
    if not pick or pick == "PASS":
        return None
    m = re.match(r"(.+?)\s+([+-])(\d+(?:\.\d+)?)", pick)
    return {"team": m.group(1).strip(), "sign": m.group(2), "pts": float(m.group(3))} if m else None


def grade_spread(g):
    p = parse_spread_pick(g.get("sPick"))
    if not p:
        return None
    chosen_is_home = p["team"] == g.get("home")
    margin = (g["homeScore"] - g["awayScore"]) if chosen_is_home else (g["awayScore"] - g["homeScore"])
    val = margin + p["pts"] if p["sign"] == "+" else margin - p["pts"]
    if val == 0:
        return "PUSH"
    return "WIN" if val > 0 else "LOSS"


def grade_total(g):
    if not g.get("oPick") or g["oPick"] == "PASS":
        return None
    actual = g["homeScore"] + g["awayScore"]
    if actual == g.get("total"):
        return "PUSH"
    if g["oPick"] == "OVER":
        return "WIN" if actual > g["total"] else "LOSS"
    return "WIN" if actual < g["total"] else "LOSS"

# -- Main ----------------------------------------------------------------------

def main():
    args = sys.argv[1:]
    start_date = args[0] if args else "20260101"
    end_date = args[1] if len(args) > 1 else add_days(today_cst(), -1)

    dates = date_range(start_date, end_date)
    print(f"\n{'=' * 58}")
    print(f"  NCAA BACKFILL (with historical odds)")
    print(f"  {fmt_date(start_date)} -> {fmt_date(end_date)} ({len(dates)} days)")
    print(f"{'=' * 58}\n")

    # Step 1: Fetch current season stats
    print("[1/3] Fetching Barttorvik team stats...")
    season_stats = fetch_ncaa_stats()
    team_count = len(season_stats)
    print(f"  Got {team_count} teams\n")

    blended = blend_base(season_stats, None, 0)
    avgs = get_avgs(blended)

    # Step 2: Load / initialize state
    store = load_store()
    defs = load_defaults()
    W = {**defs["DEFAULT_W"], **(store.get("weights") or {})}
    W_var = {**defs["DEFAULT_W_VAR"], **(store.get("weightsVar") or {})}

    kalman = load_kalman_state()
    if not kalman.get("teams"):
        print("[2/3] Initializing Kalman state...")
        kalman = initialize_kalman(blended)
    else:
        print(f"[2/3] Loaded existing Kalman state ({len(kalman['teams'])} teams)")

    # Step 3: Process each day
    print("\n[3/3] Processing games day by day...\n")
    total_games = 0
    total_days = 0
    match_failures = 0
    total_picks = 0
    total_wins = 0
    total_losses = 0
    total_pushes = 0

    for date in dates:
        odds = []
        try:
            odds = fetch_historical_odds(date)
        except Exception as err:
            print(f"  {fmt_date(date)}: Odds API failed ({err}) -- no lines for this day")

        odds_map = {}
        for o in odds:
            key = f"{norm_key(o['home'])}|{norm_key(o['away'])}"
            odds_map[key] = o

        try:
            sb = fetch_scoreboard(date)
            scores = extract_final_scores(sb)
        except Exception as err:
            print(f"  {fmt_date(date)}: ESPN fetch failed ({err}) -- skipping")
            continue

        if not scores:
            continue

        apply_daily_drift(kalman, date)

        games = []
        day_matched = 0
        day_unmatched = 0
        day_picks = 0
        day_wins = 0
        day_losses = 0

        residual_var = compute_residual_var(store.get("runs") or [])

        for s in scores:
            home_key = resolve_team_fuzzy(blended, s["home"])
            away_key = resolve_team_fuzzy(blended, s["away"])
            if not home_key or not away_key:
                day_unmatched += 1
                continue

            matched_odds = None
            espn_home_norm = norm_key(s["home"])
            espn_away_norm = norm_key(s["away"])
            bart_home_norm = norm_key(home_key)
            bart_away_norm = norm_key(away_key)

            for key, o in odds_map.items():
                odds_home_norm = norm_key(o["home"])
                odds_away_norm = norm_key(o["away"])
                home_match = (odds_home_norm == espn_home_norm or odds_home_norm == bart_home_norm
                              or odds_home_norm in espn_home_norm or espn_home_norm in odds_home_norm
                              or odds_home_norm in bart_home_norm or bart_home_norm in odds_home_norm)
                away_match = (odds_away_norm == espn_away_norm or odds_away_norm == bart_away_norm
                              or odds_away_norm in espn_away_norm or espn_away_norm in odds_away_norm
                              or odds_away_norm in bart_away_norm or bart_away_norm in odds_away_norm)
                if home_match and away_match:
                    matched_odds = o
                    break

            line = matched_odds["line"] if matched_odds else 0
            total = matched_odds["total"] if matched_odds and matched_odds.get("total") else 0

            g = {"away": away_key, "home": home_key, "line": line, "total": total}
            result = analyze_game(g, blended, avgs, W, None, kalman, W_var, residual_var)
            if not result:
                day_unmatched += 1
                continue

            result["homeScore"] = s["homeScore"]
            result["awayScore"] = s["awayScore"]
            result["_kalmanDate"] = date

            if not matched_odds:
                result["sPick"] = "PASS"
                result["sConf"] = "low"
                result["oPick"] = "PASS"
                result["oConf"] = "low"

            if result.get("sPick") and result["sPick"] != "PASS":
                s_result = grade_spread(result)
                result["sResult"] = s_result
                if s_result in ("WIN", "LOSS"):
                    day_picks += 1
                    if s_result == "WIN":
                        day_wins += 1
                    else:
                        day_losses += 1
                elif s_result == "PUSH":
                    total_pushes += 1

            if result.get("oPick") and result["oPick"] != "PASS":
                result["oResult"] = grade_total(result)

            games.append(result)
            day_matched += 1

        if not games:
            match_failures += day_unmatched
            continue

        batch_update(kalman, games)
        tuned = tune_weights(W, W_var, games)
        W = tuned["W"]
        W_var = tuned["W_var"]

        upsert_run(store, {
            "date": date, "dateDisplay": fmt_date(date), "burnIn": False,
            "weightsUsed": {**W}, "games": games,
        })
        store["weights"] = W
        store["weightsVar"] = W_var
        store["lastTuneDate"] = date

        total_games += day_matched
        total_days += 1
        total_picks += day_picks
        total_wins += day_wins
        total_losses += day_losses
        match_failures += day_unmatched

        if total_days % 7 == 0 or date == end_date:
            pct = f"{(total_wins / (total_wins + total_losses) * 100):.1f}" if total_picks > 0 else "N/A"
            print(f"  {fmt_date(date)}: {day_matched} games ({day_picks} picks: {day_wins}W-{day_losses}L) | "
                  f"Season: {total_wins}-{total_losses} ({pct}%) | "
                  f"hca={W.get('hca')} wTS={W.get('wTS')}")

        time.sleep(0.3)

    # Prune + Save
    prune_processed_games(kalman, 90)
    save_store(store)
    save_kalman_state(kalman)

    # Summary
    win_pct_val = f"{(total_wins / (total_wins + total_losses) * 100):.1f}" if total_picks > 0 else "N/A"
    units = total_wins * 0.91 - total_losses

    print(f"\n{'=' * 58}")
    print(f"  BACKFILL COMPLETE")
    print(f"{'=' * 58}")
    print(f"  Days processed:   {total_days}")
    print(f"  Games processed:  {total_games}")
    print(f"  Unmatched teams:  {match_failures}")
    print(f"{'-' * 58}")
    print(f"  SPREAD RECORD:    {total_wins}-{total_losses}-{total_pushes}  ({win_pct_val}%)")
    print(f"  Units:            {'+'if units > 0 else ''}{units:.2f}u")
    print(f"{'-' * 58}")
    print(f"  Final weights:")
    print(f"    wTS={W.get('wTS')}  wTO={W.get('wTO')}  wORR={W.get('wORR')}  wNET={W.get('wNET')}")
    print(f"    hca={W.get('hca')}  constant={W.get('constant')}  paceAdj={W.get('paceAdj')}")
    print(f"    probHigh={W.get('probHigh')}  probOUElite={W.get('probOUElite')}")
    print(f"{'-' * 58}")
    print(kalman_summary(kalman, 15))
    print(f"{'=' * 58}\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as err:
        print(f"Backfill failed: {err}", file=sys.stderr)
        sys.exit(1)
