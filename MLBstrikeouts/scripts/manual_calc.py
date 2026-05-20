#!/usr/bin/env python3
"""
Manual recalculation of MLB pitcher K projections for 2026-05-20, simulating
"opponent lineup unconfirmed" for the 9 target pitchers by overwriting their
opponent's lineup_data entry with get_recent_batting_order (same-hand preferred).

Uses ONLY cached data on disk — no live API calls.
"""

import os
import sys
import json
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))

from sources.mlb_stats import (
    fetch_pitcher_game_logs, fetch_pitcher_advanced_stats,
    fetch_pitcher_sabermetrics, fetch_pitcher_handedness_splits,
    fetch_pitcher_handedness_splits_season,
    fetch_team_batting_stats, fetch_team_pitching_stats,
    fetch_today_probable_pitchers,
    fetch_player_bat_sides, fetch_lineup_handedness,
    fetch_batter_k_rates, load_pitch_hands,
    fetch_savant_pitcher_rates,
    get_recent_batting_order,
    build_team_opp_hand_by_date,
    _compute_pct_lhb,
    CACHE_DIR as MLB_CACHE_DIR,
)
from sources.weather import fetch_game_weather
from props_engine import organize_pitcher_logs, project_pitcher_props
from pitcher_kalman import load_pitcher_kalman_state
from defaults import current_season

DATE_ISO = "2026-05-20"
DATE_KEY = "20260520"
SEASON = 2026

TARGETS = ["freeland", "burrows", "nola", "matz", "mahle",
           "leiter", "ryan", "kelly", "abbott", "burke", "hancock",
           "sale", "mlodzinski", "mcgreevy", "junk", "early", "bibee", "yesavage"]


def main():
    print(f"Manual projection run for {DATE_ISO} (cache-only)")

    # Kalman state
    kalman_path = os.path.join(SCRIPT_DIR, "..", "data", "kalman_state.json")
    kalman_state = load_pitcher_kalman_state(kalman_path)

    # Pitcher game logs (cached, never expires)
    pitcher_game_logs = fetch_pitcher_game_logs(season=SEASON)
    pitcher_logs = organize_pitcher_logs(pitcher_game_logs)

    # Advanced stats / sabermetrics — these read cache when fresh
    adv_stats = fetch_pitcher_advanced_stats(season=SEASON)
    saber_stats = fetch_pitcher_sabermetrics(season=SEASON)

    # Probable pitchers (cached for today)
    probable = fetch_today_probable_pitchers(date_str=DATE_ISO)
    print(f"  {len(probable)} probable games loaded")

    # Splits (cached per-pitcher)
    pitcher_ids = set()
    for game in probable:
        for key in ("home_pitcher_id", "away_pitcher_id"):
            pid = game.get(key)
            if pid:
                pitcher_ids.add(pid)
    splits = {}
    for pid in pitcher_ids:
        s = fetch_pitcher_handedness_splits(pid, season=SEASON, through_date=DATE_ISO)
        s_season = fetch_pitcher_handedness_splits_season(pid, season=SEASON, through_date=DATE_ISO)
        if s and s_season:
            s["vs_left_season"] = s_season.get("vs_left", {})
            s["vs_right_season"] = s_season.get("vs_right", {})
        if s:
            splits[str(pid)] = s

    # Team batting + lineup handedness
    team_batting = fetch_team_batting_stats(season=SEASON, through_date=DATE_ISO)
    bat_sides = fetch_player_bat_sides(season=SEASON)
    lineup_hand = fetch_lineup_handedness(DATE_ISO, bat_sides=bat_sides, season=SEASON)
    for abbr, hand_data in lineup_hand.items():
        if abbr in team_batting:
            team_batting[abbr]["PCT_LHB"] = hand_data["PCT_LHB"]
        else:
            team_batting[abbr] = {"PCT_LHB": hand_data["PCT_LHB"]}

    # Lineup data — load existing cache, then overwrite target opponents
    lineup_cache_path = MLB_CACHE_DIR / f"lineups_{DATE_KEY}.json"
    with open(lineup_cache_path, "r") as f:
        lineup_data = json.load(f) or {}

    # Pitch hands + opp starter hand by date (for same-hand preference)
    pitch_hands = load_pitch_hands(season=SEASON)
    opp_starter_hand_by_date = build_team_opp_hand_by_date(pitcher_logs, pitch_hands)

    # Identify the 9 target pitchers and their opponents from probable list
    target_info = []  # (target_name, pitcher_name, pitcher_id, team, opp, pitcher_hand)
    for g in probable:
        for side in ("home", "away"):
            nm = g.get(f"{side}_pitcher_name", "") or ""
            for t in TARGETS:
                if t.lower() in nm.lower():
                    pid = g.get(f"{side}_pitcher_id")
                    opp_side = "away" if side == "home" else "home"
                    team = g.get(f"{side}_team")
                    opp = g.get(f"{opp_side}_team")
                    p_hand = pitch_hands.get(pid)
                    target_info.append((t, nm, pid, team, opp, p_hand))

    # DISABLED: do not overwrite cached lineups — use whatever's in the cache as-is
    overwritten = set()
    for (t, nm, pid, team, opp, p_hand) in target_info:
        entry = lineup_data.get(opp) or {}
        n_batters = len(entry.get("player_ids") or [])
        confirmed = entry.get("confirmed")
        source = entry.get("source", "")
        print(f"  Using cached lineup for {opp} (pitcher {nm}): "
              f"{n_batters} batters, confirmed={confirmed}, source={source}")

    # Attach pitch_hand to adv_stats
    for pid_str, adv in adv_stats.items():
        try:
            adv["pitch_hand"] = pitch_hands.get(int(pid_str), "R")
        except (ValueError, TypeError):
            pass

    # Batter K rates + savant
    batter_k_rates = fetch_batter_k_rates(season=SEASON, through_date=DATE_ISO)
    savant_rates = fetch_savant_pitcher_rates(season=SEASON)

    # Weather (cached)
    weather_data = fetch_game_weather(DATE_ISO)

    # Prop lines — load existing mlb-props.json to pull today's prop lines
    # for the 9 target pitchers
    prop_lines = []
    props_path = os.path.join(SCRIPT_DIR, "..", "data", "mlb-props.json")
    if os.path.exists(props_path):
        with open(props_path, "r") as f:
            existing = json.load(f)
        for p in (existing.get("props", []) or []) + (existing.get("todayProjections", []) or []):
            if p.get("date") != DATE_ISO:
                continue
            if p.get("market") != "strikeouts":
                continue
            line = p.get("line")
            if line is None:
                continue
            prop_lines.append({
                "player": p.get("player", ""),
                "market": "strikeouts",
                "line": line,
                "over_price": p.get("over_price"),
                "under_price": p.get("under_price"),
            })
    print(f"  Loaded {len(prop_lines)} prop lines from mlb-props.json")

    # Empirical std from cache
    emp_cache_path = os.path.join(
        SCRIPT_DIR, "..", "..", "data", "emp_std_cache", "mlb",
        f"emp_std_{DATE_KEY}.json"
    )
    emp_cache_path = os.path.normpath(emp_cache_path)
    runtime_emp_std = None
    if os.path.exists(emp_cache_path):
        with open(emp_cache_path, "r") as f:
            ec = json.load(f)
        runtime_emp_std = {
            k: v for k, v in ec.items()
            if not k.startswith(("n_", "source", "computed_at", "date_iso"))
            and isinstance(v, (int, float))
        }
        print(f"  Empirical std loaded: {runtime_emp_std}")

    # Project
    projections = project_pitcher_props(
        pitcher_logs,
        team_batting_stats=team_batting,
        prop_lines=prop_lines,
        kalman_state=kalman_state,
        pitcher_adv_stats=adv_stats,
        pitcher_sabermetrics=saber_stats,
        pitcher_splits=splits,
        probable_pitchers=probable,
        injury_report=None,
        weather_by_game=weather_data,
        batter_k_rates=batter_k_rates,
        lineup_data=lineup_data,
        savant_rates=savant_rates,
        empirical_std=runtime_emp_std or None,
    )
    print(f"  {len(projections)} total projections")

    # Filter to targets
    HIGH = 0.70
    MID = 0.65
    print()
    print(f"{'Target':10s} {'Player':22s} {'Team':4s} {'Opp':4s} "
          f"{'Line':>5s} {'Proj':>6s} {'Std':>5s} {'Edge':>5s} "
          f"{'pCover':>7s} {'Pick':>6s} {'Decision':>9s}")
    print("-" * 110)

    rows = []
    for (t, nm, pid, team, opp, p_hand) in target_info:
        match = None
        for p in projections:
            if p.get("market") != "strikeouts":
                continue
            if t.lower() in (p.get("player", "") or "").lower():
                match = p
                break
        if not match:
            print(f"{t:10s} {nm:22s} {team:4s} {opp:4s}  (no projection found)")
            continue
        line = match.get("line")
        proj = match.get("proj")
        std = match.get("std")
        edge = match.get("edge")
        pcov = match.get("pCover")
        pick = match.get("pick", "PASS")

        # decision based on thresholds
        if pcov is None:
            decision = "no-line"
        elif pcov >= HIGH:
            decision = "PICK"
        elif pcov >= MID:
            decision = "LEAN"
        else:
            decision = "NONE"

        line_s = f"{line:5.1f}" if line is not None else "  -  "
        proj_s = f"{proj:6.1f}" if proj is not None else "   -  "
        std_s = f"{std:5.1f}" if std is not None else "  -  "
        edge_s = f"{edge:+5.1f}" if edge is not None else "  -  "
        pcov_s = f"{pcov:7.3f}" if pcov is not None else "    -  "

        print(f"{t:10s} {nm:22s} {team:4s} {opp:4s} "
              f"{line_s} {proj_s} {std_s} {edge_s} {pcov_s} "
              f"{pick:>6s} {decision:>9s}")
        rows.append({
            "target": t, "player": nm, "team": team, "opp": opp,
            "line": line, "proj": proj, "std": std, "edge": edge,
            "pCover": pcov, "pick": pick, "decision": decision,
        })

    print()
    print("Done.")


if __name__ == "__main__":
    main()
