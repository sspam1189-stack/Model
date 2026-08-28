#!/usr/bin/env python3
"""
scripts/backfill_last_n_weeks.py
Historical replay for calibrating the NFL model.

Architecture: SAME as pyNBA's backfill_last_n_days.py but week-by-week.

For each week in chronological order:
  1. Fetch historical play-by-play for that week
  2. Compute team stats with decay (only data available at that point)
  3. Fetch historical odds for that week's games
  4. If not first week: grade previous week's picks, update Kalman, tune weights
  5. Compute residualVar from accumulated history (sequential, NOT frozen)
  6. For each game: run analyze_game, store results
  7. Save Kalman state

After full replay:
  - Train ridge regression on accumulated feature/margin pairs
  - Train LR model on accumulated pick outcomes
  - Save weights.json, thresholds.json, LR model

CRITICAL: residualVar computes and updates naturally each week. No freezing.
Each week processes, residualVar updates from that week's results, next week
uses the updated value.

Usage:
  python scripts/backfill_last_n_weeks.py --seasons 2023 2024 2025
  python scripts/backfill_last_n_weeks.py --seasons 2024 --output-dir ../data
"""

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(SCRIPT_DIR, "..", ".."))

from dotenv import load_dotenv
load_dotenv()

# ---------------------------------------------------------------------------
# Local imports
# ---------------------------------------------------------------------------
from store import load_store, save_store, upsert_run
from self_tune import tune_weights, compute_residual_var
from kalman_state import (
    load_kalman_state, save_kalman_state, initialize_kalman,
    apply_daily_drift, batch_update, prune_processed_games,
)
import defaults

# Source modules
from sources.nflfastr import fetch_pbp
from sources.nfl_stats import (
    compute_team_stats, compute_team_stats_through_week,
    compute_player_stats, compute_player_stats_through_week,
)
from sources.odds_theoddsapi import fetch_historical_odds
from sources.espn_scoreboard import fetch_week_scores

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BURN_IN_WEEKS = defaults.BURN_IN_WEEKS   # warm-up weeks; picks not evaluated
MIN_WEEKS_FOR_STATS = 1 # Minimum weeks of data before producing picks
NFL_REGULAR_WEEKS = 18  # 18 regular season weeks (17 games per team since 2021)
NFL_PLAYOFF_WEEKS = [19, 20, 21, 22]  # Wild Card, Divisional, Conference Championship, Super Bowl
ODDS_API_DELAY = 1.5    # Seconds between historical odds API calls (rate limit)

UNIT_WIN = 1.0
UNIT_LOSS = -1.1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fmt_num(x, digits=1):
    if isinstance(x, (int, float)) and math.isfinite(x):
        return f"{x:.{digits}f}"
    return "n/a"


def _norm_team(name):
    import re
    s = str(name or "").lower().strip()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# Build canonical team resolver from defaults.NFL_TEAMS alias map
_TEAM_CANONICAL = {}
try:
    from defaults import NFL_TEAMS
    for canonical, aliases in NFL_TEAMS.items():
        key = _norm_team(canonical)
        _TEAM_CANONICAL[key] = key
        for alias in aliases:
            _TEAM_CANONICAL[_norm_team(alias)] = key
except ImportError:
    pass


def _canonical(name):
    """Resolve any team name/abbreviation to a canonical normalized form."""
    n = _norm_team(name)
    if n in _TEAM_CANONICAL:
        return _TEAM_CANONICAL[n]
    # Fuzzy: check if any alias is a substring
    for alias, canon in _TEAM_CANONICAL.items():
        if alias in n or n in alias:
            return canon
    return n


def _match_team(a, b):
    if not a or not b:
        return False
    if a == b:
        return True
    return _canonical(a) == _canonical(b)


def grade_spread_pick(g):
    """Grade a spread pick against final scores. Returns WIN/LOSS/PUSH."""
    import re
    pick = g.get("sPick")
    if not pick or pick == "PASS":
        return None
    m = re.match(r"(.+?)\s+([+-])(\d+(?:\.\d+)?)", pick)
    if not m:
        return None
    team = m.group(1).strip()
    sign = m.group(2)
    pts = float(m.group(3))
    # Resolve the picked side canonically: sPick holds full names while
    # g["home"]/g["away"] hold nflfastR abbrs — exact string equality
    # graded every home pick as an away pick (inverted margins).
    c_team = _canonical(team)
    c_home = _canonical(g.get("home", ""))
    c_away = _canonical(g.get("away", ""))
    if c_team == c_home:
        chosen_is_home = True
    elif c_team == c_away:
        chosen_is_home = False
    else:
        return None
    margin = (g["homeScore"] - g["awayScore"]) if chosen_is_home else (g["awayScore"] - g["homeScore"])
    val = margin + pts if sign == "+" else margin - pts
    if val == 0:
        return "PUSH"
    return "WIN" if val > 0 else "LOSS"


def grade_ou_pick(g):
    """Grade an over/under pick against final scores. Returns WIN/LOSS/PUSH."""
    pick = g.get("oPick")
    if not pick or pick == "PASS":
        return None
    total_line = g.get("total", 0)
    if total_line <= 0:
        return None
    hs = g.get("homeScore")
    aws = g.get("awayScore")
    if hs is None or aws is None:
        return None
    actual_total = hs + aws
    if actual_total == total_line:
        return "PUSH"
    is_over = "OVER" in pick.upper()
    if is_over:
        return "WIN" if actual_total > total_line else "LOSS"
    else:
        return "WIN" if actual_total < total_line else "LOSS"


# ---------------------------------------------------------------------------
# Core backfill function
# ---------------------------------------------------------------------------

def backfill(seasons, output_dir=None):
    """
    Replay N seasons of NFL data sequentially for model calibration.

    Parameters
    ----------
    seasons : list[int]
        List of NFL season years to replay (e.g. [2023, 2024, 2025]).
    output_dir : str or None
        Directory to save trained artifacts. Defaults to pyNFL/data/.
    """
    if output_dir is None:
        output_dir = os.path.join(SCRIPT_DIR, "..", "data")
    os.makedirs(output_dir, exist_ok=True)

    store = load_store()

    # Clear previous backfill runs so we get clean history
    store["runs"] = [
        r for r in store.get("runs", [])
        if not r.get("backfill", False)
    ]

    # Tracking for ridge regression training after full replay
    all_features = []   # list of feature dicts per game
    all_margins = []    # actual score margins (home - away)
    all_pick_outcomes = []  # for LR training: (features, WIN/LOSS)

    # Kalman state — initialized on first season with stats, evolves forward
    kalman_state = None
    prev_graded = []    # games with projections from previous week -> Kalman + self-tune

    total_weeks = 0
    total_games = 0

    for season in sorted(seasons):
        print(f"\n{'='*70}")
        print(f"  BACKFILL — Season {season}")
        print(f"{'='*70}\n")

        # 1. Load full season play-by-play
        print(f"[{season}] Loading play-by-play...")
        try:
            pbp = fetch_pbp(season)
            print(f"  {len(pbp):,} plays loaded")
        except Exception as e:
            print(f"  ERROR: PBP load failed for {season}: {e} — skipping season")
            continue

        # Determine available weeks
        if "week" not in pbp.columns or pbp.empty:
            print(f"  ERROR: No week column or empty PBP for {season}")
            continue
        available_weeks = sorted(pbp["week"].dropna().unique().astype(int))
        if not available_weeks:
            print(f"  No weeks found in PBP for {season}")
            continue
        max_week = max(available_weeks)  # Includes playoffs (weeks 19-22) if available
        print(f"  Available weeks: {available_weeks[0]}-{max_week}")

        # Reset weights at season boundaries (but keep Kalman with decay)
        base_w = store.get("weights") or {**defaults.DEFAULT_W}
        base_w_var = store.get("weightsVar") or {**defaults.DEFAULT_W_VAR}
        base_w = {**base_w}
        base_w_var = {**base_w_var}

        # Decay Kalman states at season boundary (prior-year info is still useful)
        if kalman_state and kalman_state.get("teams"):
            print(f"  Decaying Kalman states for season transition...")
            for team, ts in kalman_state.get("teams", {}).items():
                if isinstance(ts.get("adj_var"), (int, float)):
                    # Increase variance substantially at season boundary
                    ts["adj_var"] = min(ts["adj_var"] * 3.0, defaults.KALMAN_DEFAULTS.get("maxVar", 40))
            # Set lastDriftDate to Aug 1 of new season (YYYYMMDD format required by core)
            kalman_state["lastDriftDate"] = f"{season}0801"

        # Historical weather (temp/wind/roof) per home team-week, so the
        # weather adjustment is actually exercised in the backtest
        _hist_weather = {}
        try:
            from sources.qb_starts import fetch_schedules, norm_abbr as _qna
            _sch = fetch_schedules(season)
            for _, _r in _sch.iterrows():
                _roof = str(_r.get("roof") or "")
                _w, _t = _r.get("wind"), _r.get("temp")
                if _roof in ("dome", "closed"):
                    continue    # dome handled by is_dome_game()
                if _w is None and _t is None:
                    continue
                _hist_weather[(int(_r["week"]), _qna(_r["home_team"]))] = {
                    "wind_mph": float(_w) if _w == _w and _w is not None else 9.0,
                    "temp_f": float(_t) if _t == _t and _t is not None else 60.0,
                    "precip_mm": 0.0,   # not in the schedules feed
                }
            print(f"  Historical weather for {len(_hist_weather)} home team-weeks")
        except Exception as e:
            print(f"  WARNING: historical weather unavailable: {e}")

        # Per team-game EPA for joint power ratings (refit each week below)
        season_game_epa = []
        try:
            from power_ratings import compute_game_epa
            season_game_epa = compute_game_epa(pbp)
        except Exception as e:
            print(f"  WARNING: game EPA for power ratings unavailable: {e}")

        # Backup-QB start flags from nflverse schedules (starters are
        # announced pregame, so this is walk-forward legitimate)
        # Two flags: strict drives the validated totals system, loose drives
        # the margin penalty (it also covers weeks 2+ of an absence, where
        # the offensive rating is still stale).
        backup_qb_flags = {}
        qb_change_flags = {}
        try:
            from sources.qb_starts import fetch_schedules, build_backup_qb_flags
            _sched_qb = fetch_schedules(season)
            backup_qb_flags = build_backup_qb_flags(_sched_qb, strict=True)
            qb_change_flags = build_backup_qb_flags(_sched_qb, strict=False)
            print(f"  Backup-QB flags: {len(backup_qb_flags)} strict (totals), "
                  f"{len(qb_change_flags)} loose (margin)")
        except Exception as e:
            print(f"  WARNING: backup-QB flags unavailable: {e}")

        # Situational-system context per (week, home_abbr): broadcast window,
        # roof/temperature, and divisional-rematch history.
        _sit_ctx = {}
        try:
            from sources.qb_starts import norm_abbr as _qna2
            import collections as _c
            _prev_meet = _c.defaultdict(list)
            _sch2 = _sched_qb.sort_values("week")
            for _, _r in _sch2.iterrows():
                _hh, _aa = _qna2(_r["home_team"]), _qna2(_r["away_team"])
                _key = tuple(sorted([_hh, _aa]))
                _hist = _prev_meet[_key]
                try:
                    _hour = int(str(_r.get("gametime", "")).split(":")[0])
                except (ValueError, TypeError):
                    _hour = None
                _wd = str(_r.get("weekday", ""))
                _prime = (_wd in ("Thursday", "Monday")
                          or (_wd == "Sunday" and _hour is not None and _hour >= 20))
                _roof = str(_r.get("roof") or "")
                _t = _r.get("temp")
                _sit_ctx[(int(_r["week"]), _hh)] = dict(
                    week=int(_r["week"]), primetime=_prime,
                    dome=_roof in ("dome", "closed"),
                    temp=(float(_t) if _t is not None and _t == _t else None),
                    rematch=bool(_hist),
                    home_lost_meeting1=bool(_hist) and _hist[0] != _hh,
                )
                if _r.get("home_score") is not None and _r.get("home_score") == _r.get("home_score"):
                    _prev_meet[_key].append(_hh if _r["home_score"] > _r["away_score"] else _aa)
            print(f"  Situational context built for {len(_sit_ctx)} games")
        except Exception as e:
            print(f"  WARNING: situational context unavailable: {e}")

        # 2. Replay week-by-week
        for week in range(1, max_week + 1):
            week_key = f"{season}_W{week}"
            is_burn_in = (week <= BURN_IN_WEEKS)
            burn_tag = " [BURN-IN]" if is_burn_in else ""

            # --- Step A: Grade previous week's results (Kalman + self-tune) ---
            if prev_graded and week > 1:
                # Kalman batch update from last week's completed games
                if kalman_state:
                    batch_update(kalman_state, prev_graded)

                # Self-tune weights (skip during burn-in)
                if not is_burn_in and len(prev_graded) > 0:
                    try:
                        tuned = tune_weights(base_w, base_w_var, prev_graded)
                        base_w = tuned["W"]
                        base_w_var = tuned["W_var"]
                        store["weights"] = tuned["W"]
                        store["weightsVar"] = tuned["W_var"]
                    except Exception as e:
                        print(f"    [tune] Warning: self-tune failed for {week_key}: {e}")

            prev_graded = []

            # --- Step B: Compute team stats through this week ---
            # We use stats through (week - 1) for projections, then after grading
            # week's games, the stats for *this* week become available.
            # STRICTLY prior weeks. The old `max(1, week-1) if week > 1 else 1`
            # made week 1 compute stats with `week <= 1` -- i.e. from its own
            # results. That look-ahead is why week 1 graded 22-9 (71%) with
            # projection corr 0.677 vs ~0.38 everywhere else. week 1 now
            # yields no stats and therefore no picks, which is correct.
            through_week = week - 1
            team_stats = compute_team_stats_through_week(pbp, through_week, decay=0.85)
            if not team_stats:
                print(f"  {week_key}{burn_tag}: No stats available — skipping")
                continue

            # Compute player stats for injury deltas
            try:
                player_stats = compute_player_stats_through_week(pbp, through_week)
            except Exception:
                player_stats = {}

            # Convert week key to YYYYMMDD date string for Kalman drift.
            # NFL Week 1 starts ~first Thursday of September; each week is +7 days.
            from datetime import datetime as _dt, timedelta as _td
            _sept1 = _dt(season, 9, 1)
            # First Thursday on or after Sep 1
            _first_thu = _sept1 + _td(days=(3 - _sept1.weekday()) % 7)
            _week_date = _first_thu + _td(weeks=week - 1)
            _date_str = _week_date.strftime("%Y%m%d")

            # --- Step C: Initialize Kalman on first week with data ---
            if kalman_state is None:
                kalman_state = initialize_kalman(team_stats)
                kalman_state["lastDriftDate"] = _date_str
                print(f"  Kalman initialized from {week_key} ({_date_str})")

            # Apply drift to account for time passing
            apply_daily_drift(kalman_state, _date_str)

            # --- Step D: Compute residualVar from history so far ---
            # CRITICAL: Sequential, not frozen. Updates naturally each week.
            dynamic_residual_var = compute_residual_var(store.get("runs", []))
            store["residualVar"] = dynamic_residual_var

            # Empirical probability calibration — walk-forward safe: the
            # store only contains weeks already replayed at this point.
            try:
                from prob_calib import build_prob_calibration
                week_prob_calib = build_prob_calibration(store.get("runs", []))
            except Exception:
                week_prob_calib = None

            # v2 engine scale calibration (same walk-forward guarantee)
            try:
                from engine_v2 import build_scale_calibration
                week_scale_calib = build_scale_calibration(store.get("runs", []))
            except Exception:
                week_scale_calib = None

            # Joint power ratings from games BEFORE this week (walk-forward)
            week_power_ratings = None
            if season_game_epa:
                try:
                    from power_ratings import fit_epa_ratings
                    week_power_ratings = fit_epa_ratings(season_game_epa, week - 1)
                except Exception:
                    week_power_ratings = None

            # --- Step E: Fetch final scores for this week (for grading later) ---
            is_playoff = week > NFL_REGULAR_WEEKS
            season_type = 3 if is_playoff else 2
            # ESPN postseason uses week 1-5 under seasontype=3, not 19-22
            # nflfastR: 19=Wild Card, 20=Divisional, 21=Conf Champ, 22=Super Bowl
            # ESPN:     1=Wild Card, 2=Divisional, 3=Conf Champ, 5=Super Bowl
            _ESPN_PLAYOFF_WEEK = {19: 1, 20: 2, 21: 3, 22: 5}
            espn_week = _ESPN_PLAYOFF_WEEK.get(week, week) if is_playoff else week
            try:
                finals = fetch_week_scores(espn_week, season=season, season_type=season_type)
            except Exception as e:
                print(f"  {week_key}{burn_tag}: Score fetch failed: {e}")
                finals = []

            if not finals:
                print(f"  {week_key}{burn_tag}: No final scores — skipping")
                continue

            # --- Step F: Fetch historical odds ---
            week_odds = []
            try:
                week_odds = fetch_historical_odds(season=season, week=week)
                if ODDS_API_DELAY > 0:
                    time.sleep(ODDS_API_DELAY)
            except Exception as e:
                print(f"    [odds] Historical odds failed for {week_key}: {e}")

            # Build odds lookup by canonical team names
            odds_lookup = {}
            for o in week_odds:
                key = (_canonical(o.get("away")), _canonical(o.get("home")))
                odds_lookup[key] = o
                # Super Bowl (week 22): neutral site, so home/away may be
                # flipped between ESPN and The Odds API.  Add a reverse key
                # with a negated line so the match works either way.
                if week == 22:
                    rev = dict(o)
                    if isinstance(rev.get("line"), (int, float)):
                        rev["line"] = -rev["line"]
                    rev_key = (_canonical(o.get("home")), _canonical(o.get("away")))
                    if rev_key not in odds_lookup:
                        odds_lookup[rev_key] = rev

            # --- Step G: Match games with odds and analyze ---
            games = []

            for f in finals:
                away = f["away"]
                home = f["home"]
                away_score = f["awayScore"]
                home_score = f["homeScore"]

                # Find matching odds
                odds_match = None
                c_away = _canonical(away)
                c_home = _canonical(home)

                # Match by canonical name
                odds_match = odds_lookup.get((c_away, c_home))
                if not odds_match:
                    # Fallback: try fuzzy match
                    for (oa, oh), o in odds_lookup.items():
                        if _match_team(oa, away) and _match_team(oh, home):
                            odds_match = o
                            break

                g = {
                    "away": away,
                    "home": home,
                    "line": odds_match.get("line") if odds_match else None,
                    "total": odds_match.get("total") if odds_match else None,
                    "_book": odds_match.get("_book") if odds_match else None,
                }

                if not isinstance(g["line"], (int, float)) or not isinstance(g["total"], (int, float)):
                    games.append({
                        **g,
                        "awayScore": away_score,
                        "homeScore": home_score,
                        "status": "MISSING_ODDS",
                        "note": "Historical odds not available",
                    })
                    continue

                # Enrich with schedule/weather data
                try:
                    from sources.weather import compute_weather_adjustment
                    from sources.schedule import compute_rest_adjustment
                    from defaults import NFL_TEAMS
                    _n2a = {}
                    for _fn, _al in NFL_TEAMS.items():
                        if _al:
                            _n2a[_fn] = _al[0]
                    g["_homeAbbr"] = _n2a.get(home, "")
                    g["_awayAbbr"] = _n2a.get(away, "")
                    # Historical weather from nflverse schedules. Previously
                    # this passed None, so the weather adjustment was inert
                    # in every backtest and could never be validated.
                    _wx = _hist_weather.get((week, g["_homeAbbr"]))
                    g["_weatherAdj"] = compute_weather_adjustment(
                        _wx, g["_homeAbbr"])
                    # Rest/schedule from previous week games
                    g["_restAdj"] = compute_rest_adjustment(g, prev_graded)
                except Exception:
                    pass  # Non-critical — proceed without enrichment

                # Run model engine (v2 structural)
                try:
                    from engine_v2 import analyze_game
                    _situational = None
                    if backup_qb_flags or qb_change_flags:
                        from sources.qb_starts import norm_abbr as _qna
                        _h, _a = _qna(g.get("_homeAbbr")), _qna(g.get("_awayAbbr"))
                        _hb = backup_qb_flags.get((week, _h), False)
                        _ab = backup_qb_flags.get((week, _a), False)
                        _hc = qb_change_flags.get((week, _h), False)
                        _ac = qb_change_flags.get((week, _a), False)
                        if _hb or _ab or _hc or _ac:
                            _situational = {"home_backup_qb": _hb,
                                            "away_backup_qb": _ab,
                                            "home_qb_change": _hc,
                                            "away_qb_change": _ac}
                    # merge in broadcast window / weather / rematch context
                    if _sit_ctx:
                        from sources.qb_starts import norm_abbr as _qna_ctx
                        _extra = _sit_ctx.get((week, _qna_ctx(g.get("_homeAbbr"))))
                        if _extra:
                            _situational = {**(_situational or {}), **_extra}
                    r = analyze_game(
                        game_data=g,
                        team_stats=team_stats,
                        weights=base_w,
                        kalman_states=kalman_state,
                        injury_deltas=None,    # No historical injury data in backfill
                        residual_var=dynamic_residual_var,
                        thresholds={},
                        prob_calib=week_prob_calib,
                        situational=_situational,
                        scale_calib=week_scale_calib,
                        power_ratings=week_power_ratings,
                    )
                except ImportError:
                    # model_engine.py not yet available — create stub result
                    r = _stub_analyze(g, team_stats, base_w, kalman_state, dynamic_residual_var)
                except Exception as e:
                    print(f"    [engine] Warning: analyze_game failed for {away}@{home}: {e}")
                    r = None

                if not r:
                    games.append({
                        **g,
                        "awayScore": away_score,
                        "homeScore": home_score,
                        "status": "SKIPPED",
                        "note": "analyze_game returned None",
                    })
                    continue

                r["awayScore"] = away_score
                r["homeScore"] = home_score

                # Grade immediately since we have scores
                if r.get("sPick") and r["sPick"] != "PASS":
                    r["sResult"] = grade_spread_pick(r)
                if r.get("oPick") and r["oPick"] != "PASS":
                    r["oResult"] = grade_ou_pick(r)

                # Collect features for ridge regression training
                # _features is a list of floats (numpy feature vector from
                # model_engine.build_feature_vector) when using the real engine,
                # or a dict of named features from the stub fallback.
                feat = r.get("_features")
                if feat is not None:
                    actual_margin = home_score - away_score
                    all_features.append(feat)
                    all_margins.append(actual_margin)

                    # Collect for LR training
                    if r.get("sResult") in ("WIN", "LOSS"):
                        all_pick_outcomes.append({
                            "features": feat,
                            "result": r["sResult"],
                        })

                games.append(r)

            # --- Step H: Collect completed games for next week's Kalman + tune ---
            completed = [
                x for x in games
                if x.get("status") not in ("MISSING_ODDS", "SKIPPED")
                and isinstance(x.get("homeScore"), (int, float)) and math.isfinite(x["homeScore"])
                and isinstance(x.get("awayScore"), (int, float)) and math.isfinite(x["awayScore"])
            ]

            for g in completed:
                g["_kalmanDate"] = week_key
            prev_graded = completed

            # --- Step I: Save run to store ---
            _playoff_names = {19: "Wild Card", 20: "Divisional", 21: "Conference Championship", 22: "Super Bowl"}
            _week_label = _playoff_names.get(week, f"Week {week}")
            run = {
                "weekKey": week_key,
                "season": season,
                "week": week,
                "date": week_key,
                "dateDisplay": f"{season} {_week_label}",
                "burnIn": is_burn_in,
                "backfill": True,
                "playoff": is_playoff,
                "playoffRound": _playoff_names.get(week) if is_playoff else None,
                "weightsUsed": {**base_w},
                "games": games,
                "summaryText": "",
            }

            upsert_run(store, run)
            save_store(store)

            # Save Kalman state every week
            if kalman_state:
                save_kalman_state(kalman_state)

            # Print progress
            n_ok = len(completed)
            n_miss = sum(1 for x in games if x.get("status") == "MISSING_ODDS")
            n_skip = sum(1 for x in games if x.get("status") == "SKIPPED")
            wins = sum(1 for x in completed if x.get("sResult") == "WIN")
            losses = sum(1 for x in completed if x.get("sResult") == "LOSS")
            units = UNIT_WIN * wins + UNIT_LOSS * losses
            ou_wins = sum(1 for x in completed if x.get("oResult") == "WIN")
            ou_losses = sum(1 for x in completed if x.get("oResult") == "LOSS")
            ou_units = UNIT_WIN * ou_wins + UNIT_LOSS * ou_losses

            ou_tag = f" | OU={ou_wins}-{ou_losses} ({'+' if ou_units >= 0 else ''}{ou_units:.1f}u)" if (ou_wins + ou_losses) > 0 else ""
            print(
                f"  {week_key}{burn_tag}: "
                f"games={len(games)} completed={n_ok} missing_odds={n_miss} skipped={n_skip} "
                f"W-L={wins}-{losses} ({'+' if units >= 0 else ''}{units:.1f}u){ou_tag}"
            )

            total_weeks += 1
            total_games += len(games)

        # --- End of season: grade final week's games through Kalman ---
        # prev_graded from the last week would otherwise be lost because
        # the next season starts at week=1 where the `week > 1` guard skips it.
        if prev_graded and kalman_state:
            batch_update(kalman_state, prev_graded)
            # Self-tune on final week as well
            if len(prev_graded) > 0:
                try:
                    tuned = tune_weights(base_w, base_w_var, prev_graded)
                    base_w = tuned["W"]
                    base_w_var = tuned["W_var"]
                    store["weights"] = tuned["W"]
                    store["weightsVar"] = tuned["W_var"]
                except Exception:
                    pass
            prev_graded = []

    # -----------------------------------------------------------------------
    # Post-replay: Train ridge regression + LR model
    # -----------------------------------------------------------------------

    print(f"\n{'='*70}")
    print(f"  POST-REPLAY TRAINING")
    print(f"{'='*70}\n")
    print(f"  Total weeks replayed: {total_weeks}")
    print(f"  Total games: {total_games}")
    print(f"  Feature samples: {len(all_features)}")
    print(f"  LR samples: {len(all_pick_outcomes)}")

    # --- Train ridge regression via model_engine.train_ridge ---
    trained_weights = {}
    trained_thresholds = {}

    if len(all_features) >= 30:
        print("\n[train] Training ridge regression on accumulated data...")
        try:
            import numpy as np

            # all_features may be lists of floats (from real engine) or dicts
            # (from stub). Convert to numpy array accordingly.
            sample = all_features[0]
            if isinstance(sample, dict):
                # Stub path: features are dicts -> use backfill's own _train_ridge
                print("  (Using backfill dict-based ridge training — stub features)")
                trained_weights, trained_thresholds = _train_ridge(
                    all_features, all_margins, output_dir
                )
            else:
                # Real engine path: features are lists of floats -> numpy array
                X = np.array(all_features, dtype=np.float64)
                y = np.array(all_margins, dtype=np.float64)

                # Remove rows with NaN/Inf
                mask = np.all(np.isfinite(X), axis=1) & np.isfinite(y)
                X = X[mask]
                y = y[mask]

                if len(X) >= 20:
                    from model_engine import train_ridge
                    trained_weights = train_ridge(X, y)
                    # Build thresholds from residuals
                    trained_thresholds = {
                        "residual_var": trained_weights.get("residual_var", 0),
                        "r_squared": trained_weights.get("r_squared", 0),
                        "n_train": trained_weights.get("n_train", len(X)),
                        "source": "backfill_ridge",
                    }
                else:
                    print(f"  [ridge] Only {len(X)} valid samples after NaN filter — too few")

            print(f"  Ridge trained on {len(all_features)} games")
        except Exception as e:
            print(f"  WARNING: Ridge training failed: {e}")
            import traceback
            traceback.print_exc()
    else:
        print(f"\n[train] Insufficient data for ridge regression ({len(all_features)} < 30)")


    # --- Save final artifacts ---
    weights_path = os.path.join(output_dir, "weights.json")
    thresholds_path = os.path.join(output_dir, "thresholds.json")

    if trained_weights:
        with open(weights_path, "w") as f:
            json.dump(trained_weights, f, indent=2)
        print(f"\n  Saved weights.json ({len(trained_weights)} keys)")

    if trained_thresholds:
        with open(thresholds_path, "w") as f:
            json.dump(trained_thresholds, f, indent=2)
        print(f"  Saved thresholds.json")

    # Save final Kalman state
    if kalman_state:
        prune_processed_games(kalman_state, 60)
        save_kalman_state(kalman_state)
        print("  Saved final Kalman state")

    # Sort runs by season + week before final save
    store["runs"].sort(key=lambda r: (r.get("season", 0), r.get("week", 0)))
    save_store(store)

    # --- Print final summary ---
    _print_backfill_summary(store, seasons)


# ---------------------------------------------------------------------------
# Ridge regression training
# ---------------------------------------------------------------------------

def _train_ridge(features_list, margins, output_dir):
    """
    Train ridge regression on accumulated (feature_dict, margin) pairs.
    Uses 5-fold CV to select alpha. Returns (weights_dict, thresholds_dict).
    """
    import numpy as np
    from sklearn.linear_model import RidgeCV
    from sklearn.preprocessing import StandardScaler

    # Build feature matrix
    # Determine all feature keys from the union
    all_keys = set()
    for fd in features_list:
        all_keys.update(fd.keys())
    feature_keys = sorted(all_keys)

    X = np.zeros((len(features_list), len(feature_keys)))
    y = np.array(margins, dtype=float)

    for i, fd in enumerate(features_list):
        for j, key in enumerate(feature_keys):
            val = fd.get(key, 0)
            if isinstance(val, (int, float)) and math.isfinite(val):
                X[i, j] = val

    # Remove rows with NaN/Inf in y
    mask = np.isfinite(y)
    X = X[mask]
    y = y[mask]

    if len(X) < 20:
        print(f"  [ridge] Only {len(X)} valid samples — too few for training")
        return {}, {}

    # Standardize features
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # 5-fold CV to select alpha
    alphas = [0.01, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 50.0, 100.0]
    ridge = RidgeCV(alphas=alphas, cv=5, scoring="neg_mean_squared_error")
    ridge.fit(X_scaled, y)

    best_alpha = ridge.alpha_
    coefs = ridge.coef_
    intercept = ridge.intercept_
    r2 = ridge.score(X_scaled, y)

    print(f"  [ridge] Best alpha={best_alpha}, R²={r2:.4f}, intercept={intercept:.3f}")

    # Build weights dict: map feature keys to ridge coefficients
    weights = {"_type": "ridge", "_alpha": float(best_alpha), "_intercept": float(intercept)}
    for j, key in enumerate(feature_keys):
        weights[key] = float(coefs[j])

    # Store scaler params for inference
    weights["_scaler_mean"] = scaler.mean_.tolist()
    weights["_scaler_scale"] = scaler.scale_.tolist()
    weights["_feature_keys"] = feature_keys

    # Compute sDiff thresholds from residuals
    y_pred = ridge.predict(X_scaled)
    residuals = y - y_pred
    rmse = float(np.sqrt(np.mean(residuals ** 2)))
    residual_std = float(np.std(residuals))

    # Calibrate thresholds from historical sDiff distribution
    # These define "high" and "elite" confidence buckets
    thresholds = {
        "rmse": rmse,
        "residual_std": residual_std,
        "ridgeAlpha": float(best_alpha),
        "r2": float(r2),
        "n_train": len(X),
        "feature_keys": feature_keys,
    }

    print(f"  [ridge] RMSE={rmse:.2f}, residual_std={residual_std:.2f}")

    return weights, thresholds




# ---------------------------------------------------------------------------
# Stub analyze (fallback when model_engine.py is not yet available)
# ---------------------------------------------------------------------------

def _stub_analyze(g, team_stats, weights, kalman_state, residual_var):
    """
    Minimal projection stub used when model_engine.py is not yet available.
    Produces a basic spread projection from team EPA differentials + HFA.
    """
    away = g.get("away", "")
    home = g.get("home", "")

    away_stats = team_stats.get(away)
    home_stats = team_stats.get(home)

    if not away_stats or not home_stats:
        # Try fuzzy match
        for k, v in team_stats.items():
            if _match_team(k, away):
                away_stats = v
            if _match_team(k, home):
                home_stats = v

    if not away_stats or not home_stats:
        return None

    # Simple projection: EPA differential + HFA
    hfa = weights.get("hfa", 2.5)
    home_off = home_stats.get("off_pass_epa", 0) * 0.65 + home_stats.get("off_rush_epa", 0) * 0.35
    away_off = away_stats.get("off_pass_epa", 0) * 0.65 + away_stats.get("off_rush_epa", 0) * 0.35
    home_def = home_stats.get("def_pass_epa", 0) * 0.65 + home_stats.get("def_rush_epa", 0) * 0.35
    away_def = away_stats.get("def_pass_epa", 0) * 0.65 + away_stats.get("def_rush_epa", 0) * 0.35

    # EPA differential -> points (rough scaling: 1 EPA ~ 7 points per game)
    epa_scale = 7.0
    home_advantage = (home_off - away_def) * epa_scale
    away_advantage = (away_off - home_def) * epa_scale
    proj_spread = (home_advantage - away_advantage) + hfa  # positive = home favored

    line = g.get("line", 0) or 0
    s_diff = proj_spread - line  # positive = model says home covers more

    # Compute P(cover) using normal CDF with residualVar
    p_cover = 0.5
    try:
        from scipy.stats import norm
        sigma = math.sqrt(max(residual_var, 50))
        p_cover = float(norm.cdf(abs(s_diff) / sigma))
    except ImportError:
        if abs(s_diff) > 5:
            p_cover = 0.65
        elif abs(s_diff) > 3:
            p_cover = 0.58

    # Determine pick
    prob_high = weights.get("probHigh", 0.57)
    prob_elite = weights.get("probElite", 0.63)

    pick = "PASS"
    conf = "low"
    if p_cover >= prob_elite:
        conf = "elite"
    elif p_cover >= prob_high:
        conf = "high"

    if conf in ("high", "elite") and abs(s_diff) > 1.5:
        if s_diff > 0:
            # Home covers: pick home at the spread
            if line >= 0:
                pick = f"{home} +{abs(line)}"
            else:
                pick = f"{home} {'-' if line < 0 else '+'}{abs(line)}"
        else:
            # Away covers
            if line >= 0:
                pick = f"{away} -{abs(line)}"
            else:
                pick = f"{away} +{abs(line)}"

    # Feature dict for ridge regression training
    features = {
        "home_off_pass_epa": home_stats.get("off_pass_epa", 0),
        "home_off_rush_epa": home_stats.get("off_rush_epa", 0),
        "home_def_pass_epa": home_stats.get("def_pass_epa", 0),
        "home_def_rush_epa": home_stats.get("def_rush_epa", 0),
        "away_off_pass_epa": away_stats.get("off_pass_epa", 0),
        "away_off_rush_epa": away_stats.get("off_rush_epa", 0),
        "away_def_pass_epa": away_stats.get("def_pass_epa", 0),
        "away_def_rush_epa": away_stats.get("def_rush_epa", 0),
        "home_pace": home_stats.get("pace", 65),
        "away_pace": away_stats.get("pace", 65),
        "home_rz": home_stats.get("rz_td_pct_off", 0.55),
        "away_rz": away_stats.get("rz_td_pct_off", 0.55),
        "home_success_off": home_stats.get("success_rate_off", 0.45),
        "away_success_off": away_stats.get("success_rate_off", 0.45),
        "hfa": hfa,
        "line": line,
    }

    return {
        "away": away,
        "home": home,
        "line": line,
        "total": g.get("total"),
        "_book": g.get("_book"),
        "projSpread": round(proj_spread, 2),
        "sDiff": round(s_diff, 2),
        "pCover": round(p_cover, 4),
        "sPick": pick,
        "sConf": conf,
        "_features": features,
    }


# ---------------------------------------------------------------------------
# Print final summary
# ---------------------------------------------------------------------------

def _print_backfill_summary(store, seasons):
    """Print end-of-backfill summary statistics."""
    print(f"\n{'='*70}")
    print(f"  BACKFILL SUMMARY")
    print(f"{'='*70}\n")

    total_w = total_l = total_p = 0
    total_ow = total_ol = total_op = 0
    season_stats = {}

    for r in store.get("runs", []):
        if not r.get("backfill"):
            continue
        if r.get("burnIn"):
            continue

        s = r.get("season", 0)
        if s not in season_stats:
            season_stats[s] = {"w": 0, "l": 0, "p": 0,
                               "ow": 0, "ol": 0, "op": 0,
                               "games": 0, "weeks": 0}
        season_stats[s]["weeks"] += 1

        for g in r.get("games", []):
            if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
                continue
            season_stats[s]["games"] += 1

            result = g.get("sResult")
            if result == "WIN":
                season_stats[s]["w"] += 1
                total_w += 1
            elif result == "LOSS":
                season_stats[s]["l"] += 1
                total_l += 1
            elif result == "PUSH":
                season_stats[s]["p"] += 1
                total_p += 1

            o_result = g.get("oResult")
            if o_result == "WIN":
                season_stats[s]["ow"] += 1
                total_ow += 1
            elif o_result == "LOSS":
                season_stats[s]["ol"] += 1
                total_ol += 1
            elif o_result == "PUSH":
                season_stats[s]["op"] += 1
                total_op += 1

    for s in sorted(season_stats.keys()):
        ss = season_stats[s]
        units = UNIT_WIN * ss["w"] + UNIT_LOSS * ss["l"]
        total = ss["w"] + ss["l"]
        pct = f"{ss['w'] / total * 100:.1f}%" if total > 0 else "n/a"
        ou_units = UNIT_WIN * ss["ow"] + UNIT_LOSS * ss["ol"]
        ou_total = ss["ow"] + ss["ol"]
        ou_pct = f"{ss['ow'] / ou_total * 100:.1f}%" if ou_total > 0 else "n/a"
        ou_tag = f" | OU: {ss['ow']}W-{ss['ol']}L ({ou_pct}) {'+' if ou_units >= 0 else ''}{ou_units:.1f}u" if ou_total > 0 else ""
        print(
            f"  {s}: {ss['weeks']} weeks, {ss['games']} games, "
            f"ATS: {ss['w']}W-{ss['l']}L-{ss['p']}P ({pct}) "
            f"{'+'if units >= 0 else ''}{units:.1f}u{ou_tag}"
        )

    total_units = UNIT_WIN * total_w + UNIT_LOSS * total_l
    grand_total = total_w + total_l
    grand_pct = f"{total_w / grand_total * 100:.1f}%" if grand_total > 0 else "n/a"
    total_ou_units = UNIT_WIN * total_ow + UNIT_LOSS * total_ol
    ou_grand = total_ow + total_ol
    ou_grand_pct = f"{total_ow / ou_grand * 100:.1f}%" if ou_grand > 0 else "n/a"

    print(f"\n  ATS TOTAL:  {total_w}W-{total_l}L-{total_p}P ({grand_pct}) "
          f"{'+'if total_units >= 0 else ''}{total_units:.1f}u")
    if ou_grand > 0:
        print(f"  O/U TOTAL:  {total_ow}W-{total_ol}L-{total_op}P ({ou_grand_pct}) "
              f"{'+'if total_ou_units >= 0 else ''}{total_ou_units:.1f}u")
        combined = total_units + total_ou_units
        print(f"  COMBINED:   {'+'if combined >= 0 else ''}{combined:.1f}u")
    print()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="NFL historical backfill — replay seasons week-by-week for model calibration"
    )
    parser.add_argument(
        "--seasons",
        nargs="+",
        type=int,
        default=None,
        help="Season years to replay (e.g. --seasons 2023 2024 2025). Defaults to last 2 seasons.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory to save trained artifacts (default: pyNFL/data/)",
    )
    args = parser.parse_args()

    if args.seasons:
        seasons = sorted(args.seasons)
    else:
        from sources.nflfastr import current_season
        cur = current_season()
        seasons = [cur - 1, cur]

    print(f"\n{'='*60}")
    print(f"  NFL BACKFILL — Seasons: {seasons}")
    print(f"{'='*60}\n")

    start_time = time.time()
    backfill(seasons, output_dir=args.output_dir)
    elapsed = time.time() - start_time

    print(f"\nBackfill completed in {elapsed:.1f}s ({elapsed / 60:.1f}m)")


if __name__ == "__main__":
    try:
        main()
    except Exception as err:
        import traceback
        traceback.print_exc()
        sys.exit(1)
