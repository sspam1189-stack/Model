#!/usr/bin/env python3
"""
pyMLB/scripts/backfill.py
Train ridge regression on prior season MLB data.

Usage:
  python scripts/backfill.py --season 2025
  python scripts/backfill.py --season 2025 --odds-dir "C:\\Users\\Henry Pham\\Desktop\\odds historical\\MLB"
  python scripts/backfill.py --season 2025 --no-odds  # Skip odds loading
"""

import argparse
import datetime
import json
import os
import re
import sys
import time

import numpy as np

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(SCRIPT_DIR, "..", ".."))

from dotenv import load_dotenv
load_dotenv()

import defaults
import model_engine
from sources.mlb_stats import fetch_team_stats
from sources.pitcher_stats import fetch_pitcher_stats
from sources.espn_scoreboard import fetch_date_scores

# ---------------------------------------------------------------------------
# Data directories
# ---------------------------------------------------------------------------
_DATA_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data"))
_BACKFILL_CACHE_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", "data", "backfill_cache"))

# ---------------------------------------------------------------------------
# Default pitcher / bullpen / weather stubs
# ---------------------------------------------------------------------------
_DEFAULT_STARTER = {
    "name": "TBD",
    "FIP": 4.50,
    "xFIP": 4.50,
    "SIERA": 4.50,
    "K_BB_pct": 0.08,
    "IP_per_GS": 5.5,
    "hand": "R",
}

_DEFAULT_BULLPEN = {
    "FIP": 4.50,
    "K_BB_pct": 0.08,
    "workload": 0.0,
}

_DEFAULT_WEATHER = {
    "temperature_adj": 0.0,
    "wind_adj": 0.0,
}


# ===========================================================================
# Helper: pick best starter from pitcher stats data for a team
# ===========================================================================

def _pick_best_starter(team_pitcher_data):
    """
    Return the pitcher dict (lowest FIP) from a team's starters dict.

    Parameters
    ----------
    team_pitcher_data : dict or None
        {pitcher_name: {FIP, xFIP, SIERA, K_BB_pct, IP, GS, hand}} — as
        returned by fetch_pitcher_stats()["starters"][team_name].

    Returns
    -------
    dict
        Starter dict suitable for build_feature_vector().
    """
    if not team_pitcher_data:
        return dict(_DEFAULT_STARTER)

    # Filter to actual starters (GS >= 1) with meaningful IP
    starters = [
        (name, stats)
        for name, stats in team_pitcher_data.items()
        if stats.get("GS", 0) >= 1 and stats.get("IP", 0) >= 5.0
    ]

    if not starters:
        # Fall back to any pitcher with IP
        starters = [
            (name, stats)
            for name, stats in team_pitcher_data.items()
            if stats.get("IP", 0) >= 1.0
        ]

    if not starters:
        return dict(_DEFAULT_STARTER)

    # Pick lowest FIP
    best_name, best = min(starters, key=lambda x: x[1].get("FIP", 9.99))

    ip = best.get("IP", 0.0) or 0.0
    gs = best.get("GS", 1) or 1
    ip_per_gs = ip / gs if gs > 0 else 5.5

    return {
        "name": best_name,
        "FIP": best.get("FIP", _DEFAULT_STARTER["FIP"]),
        "xFIP": best.get("xFIP", _DEFAULT_STARTER["xFIP"]),
        "SIERA": best.get("SIERA", _DEFAULT_STARTER["SIERA"]),
        "K_BB_pct": best.get("K_BB_pct", _DEFAULT_STARTER["K_BB_pct"]),
        "IP_per_GS": ip_per_gs,
        "hand": best.get("hand", "R"),
    }


# ===========================================================================
# load_historical_odds
# ===========================================================================

def load_historical_odds(odds_dir, season):
    """
    Scan odds_dir for JSON files matching {YYYYMMDD}.json or {season}-*.json.

    JSON format: {"AwayTeam@HomeTeam": {"line": float, "total": float}, ...}

    Returns
    -------
    dict
        {date_str: {game_key: {"line": float, "total": float}}}
    """
    if not odds_dir:
        return {}

    if not os.path.isdir(odds_dir):
        print(f"  [backfill] odds-dir not found: {odds_dir}")
        return {}

    result = {}
    season_str = str(season)
    yyyymmdd_re = re.compile(r"^(\d{8})\.json$")
    season_re = re.compile(rf"^{re.escape(season_str)}-(.+)\.json$")

    try:
        entries = os.listdir(odds_dir)
    except OSError as exc:
        print(f"  [backfill] Cannot list odds_dir: {exc}")
        return {}

    files_loaded = 0
    games_loaded = 0

    for fname in sorted(entries):
        date_str = None

        m = yyyymmdd_re.match(fname)
        if m:
            date_str = m.group(1)
        else:
            m2 = season_re.match(fname)
            if m2:
                # Convert "2025-04-15" -> "20250415"
                raw = m2.group(1)
                cleaned = raw.replace("-", "")
                if len(cleaned) == 8 and cleaned.isdigit():
                    date_str = cleaned

        if date_str is None:
            continue

        # Only include dates in the season range
        try:
            dt = datetime.datetime.strptime(date_str, "%Y%m%d")
        except ValueError:
            continue
        if dt.year != season:
            continue

        fpath = os.path.join(odds_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue  # silently skip non-JSON or malformed files

        if not isinstance(data, dict):
            continue

        day_odds = {}
        for game_key, odds_data in data.items():
            if not isinstance(odds_data, dict):
                continue
            try:
                line = float(odds_data.get("line", 0.0))
                total = float(odds_data.get("total", 9.0))
            except (TypeError, ValueError):
                continue
            day_odds[game_key] = {"line": line, "total": total}

        if day_odds:
            result[date_str] = day_odds
            files_loaded += 1
            games_loaded += len(day_odds)

    print(f"  [backfill] Historical odds: {files_loaded} dates, {games_loaded} games loaded")
    return result


# ===========================================================================
# fetch_season_scores
# ===========================================================================

def fetch_season_scores(season, limit_dates=None):
    """
    Fetch all final scores for the season by iterating dates April 1 through
    October 1 of `season` year.

    Caches to data/backfill_cache/{season}_scores.json.

    Parameters
    ----------
    season : int
    limit_dates : int or None
        Stop after this many dates (for testing).

    Returns
    -------
    list of dict
        Each dict: {home, away, homeScore, awayScore, date}
    """
    os.makedirs(_BACKFILL_CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(_BACKFILL_CACHE_DIR, f"{season}_scores.json")

    if os.path.exists(cache_path) and limit_dates is None:
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            print(f"  [backfill] Loaded {len(cached)} games from cache: {cache_path}")
            return cached
        except Exception as exc:
            print(f"  [backfill] Cache read failed ({exc}), re-fetching")

    start = datetime.date(season, 4, 1)
    end = datetime.date(season, 10, 1)

    all_scores = []
    current = start
    date_count = 0

    while current <= end:
        if limit_dates is not None and date_count >= limit_dates:
            print(f"  [backfill] --limit-dates {limit_dates} reached, stopping early")
            break

        date_str = current.strftime("%Y%m%d")
        try:
            scores = fetch_date_scores(date_str)
            if scores:
                for s in scores:
                    s["date"] = date_str
                all_scores.extend(scores)
                print(f"    {date_str}: {len(scores)} games")
        except Exception as exc:
            print(f"    {date_str}: ERROR — {exc}")

        time.sleep(1)
        current += datetime.timedelta(days=1)
        date_count += 1

    print(f"  [backfill] Fetched {len(all_scores)} total games for {season}")

    # Only cache full season fetches
    if limit_dates is None:
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(all_scores, f)
            print(f"  [backfill] Saved season scores cache: {cache_path}")
        except Exception as exc:
            print(f"  [backfill] Failed to save cache: {exc}")

    return all_scores


# ===========================================================================
# build_training_data
# ===========================================================================

def build_training_data(season, team_stats_by_date, pitcher_stats, final_scores, historical_odds):
    """
    Build ridge training matrix from historical game data.

    Parameters
    ----------
    season : int
    team_stats_by_date : dict
        {date_str: {team_name: stats_dict}} — pre-loaded for each date.
        (If only one date's worth of stats is available, pass as a single-key dict.)
    pitcher_stats : dict
        Output from fetch_pitcher_stats(): {"starters": {...}, "bullpens": {...}}
    final_scores : list of dict
        Each: {date, home, away, homeScore, awayScore}
    historical_odds : dict
        {date_str: {game_key: {line, total}}}

    Returns
    -------
    dict
        {"X": np.ndarray, "y_margin": np.ndarray, "y_total": np.ndarray,
         "weights": np.ndarray, "n_games": int}
    """
    starters_by_team = pitcher_stats.get("starters", {})
    bullpens_by_team = pitcher_stats.get("bullpens", {})

    # Sort scores chronologically to compute recency decay
    sorted_scores = sorted(final_scores, key=lambda s: s.get("date", ""))
    n_total = len(sorted_scores)

    # Get sorted date keys for nearest-date lookup
    stats_dates = sorted(team_stats_by_date.keys())

    X_rows = []
    y_margin_list = []
    y_total_list = []
    weights_list = []
    skipped = 0

    for game_idx, score in enumerate(sorted_scores):
        date_str = score.get("date", "")
        home = score.get("home", "")
        away = score.get("away", "")
        home_score = score.get("homeScore")
        away_score = score.get("awayScore")

        # Validate scores
        if home_score is None or away_score is None:
            skipped += 1
            continue
        try:
            home_score = float(home_score)
            away_score = float(away_score)
        except (TypeError, ValueError):
            skipped += 1
            continue

        # Find closest prior team stats date
        team_stats = _find_closest_stats(team_stats_by_date, stats_dates, date_str)
        if team_stats is None:
            skipped += 1
            continue

        # Resolve team names in stats
        home_key = _resolve_team_name(home, team_stats)
        away_key = _resolve_team_name(away, team_stats)

        if not home_key or not away_key:
            skipped += 1
            continue

        home_st = team_stats.get(home_key)
        away_st = team_stats.get(away_key)

        if not home_st or not away_st:
            skipped += 1
            continue

        # Pick starters (best by FIP for historical games)
        home_pitchers = starters_by_team.get(home_key, {})
        away_pitchers = starters_by_team.get(away_key, {})
        starter_home = _pick_best_starter(home_pitchers)
        starter_away = _pick_best_starter(away_pitchers)

        # Bullpen
        home_bp = bullpens_by_team.get(home_key, {})
        away_bp = bullpens_by_team.get(away_key, {})
        bullpen_home = {
            "FIP": home_bp.get("FIP", _DEFAULT_BULLPEN["FIP"]),
            "K_BB_pct": home_bp.get("K_BB_pct", _DEFAULT_BULLPEN["K_BB_pct"]),
            "workload": 0.0,  # No live workload data in backfill
        }
        bullpen_away = {
            "FIP": away_bp.get("FIP", _DEFAULT_BULLPEN["FIP"]),
            "K_BB_pct": away_bp.get("K_BB_pct", _DEFAULT_BULLPEN["K_BB_pct"]),
            "workload": 0.0,
        }

        # Odds lookup
        game_key_fwd = f"{away}@{home}"
        game_key_rev = f"{home}@{away}"
        day_odds = historical_odds.get(date_str, {})
        odds_data = (
            day_odds.get(game_key_fwd)
            or day_odds.get(game_key_rev)
            or {"line": 0.0, "total": 9.0}
        )

        # Build feature vector
        try:
            fv = model_engine.build_feature_vector(
                home_st, away_st,
                starter_home, starter_away,
                bullpen_home, bullpen_away,
                park_factor=1.0,          # No historical park factor in backfill
                weather=_DEFAULT_WEATHER,
                is_home=True,
            )
        except Exception as exc:
            print(f"    [backfill] Feature build error for {away}@{home} {date_str}: {exc}")
            skipped += 1
            continue

        # Recency decay: games at end of season get weight ~1.0
        n_games_from_end = n_total - 1 - game_idx
        weight = 0.998 ** n_games_from_end

        margin = home_score - away_score
        total = home_score + away_score

        X_rows.append(fv)
        y_margin_list.append(margin)
        y_total_list.append(total)
        weights_list.append(weight)

    n_built = len(X_rows)
    print(f"  [backfill] Built {n_built} training samples ({skipped} skipped)")

    if n_built == 0:
        return {
            "X": np.empty((0, len(defaults.FEATURE_COLUMNS))),
            "y_margin": np.array([]),
            "y_total": np.array([]),
            "weights": np.array([]),
            "n_games": 0,
        }

    return {
        "X": np.array(X_rows, dtype=np.float64),
        "y_margin": np.array(y_margin_list, dtype=np.float64),
        "y_total": np.array(y_total_list, dtype=np.float64),
        "weights": np.array(weights_list, dtype=np.float64),
        "n_games": n_built,
    }


# ===========================================================================
# Helpers
# ===========================================================================

def _find_closest_stats(team_stats_by_date, sorted_dates, date_str):
    """
    Find team stats for the closest date <= date_str.
    Returns None if no prior date is available.
    """
    if not sorted_dates:
        return None

    # Exact match
    if date_str in team_stats_by_date:
        return team_stats_by_date[date_str]

    # Find closest prior date
    prior = None
    for d in sorted_dates:
        if d <= date_str:
            prior = d
        else:
            break

    if prior is not None:
        return team_stats_by_date[prior]

    # No prior date; use the earliest available
    return team_stats_by_date[sorted_dates[0]]


def _resolve_team_name(display_name, team_stats):
    """
    Try to match a display name (e.g. "New York Yankees") to a key in team_stats.
    Tries exact match, then case-insensitive substring.
    """
    if not display_name or not team_stats:
        return None

    # Exact
    if display_name in team_stats:
        return display_name

    # Case-insensitive exact
    dn_lower = display_name.lower()
    for key in team_stats:
        if key.lower() == dn_lower:
            return key

    # Substring (e.g. "Yankees" in "New York Yankees")
    for key in team_stats:
        if dn_lower in key.lower() or key.lower() in dn_lower:
            return key

    return None


# ===========================================================================
# main
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(description="Train MLB ridge regression on prior season data.")
    parser.add_argument("--season", type=int, default=2025,
                        help="Season year to train on (default: 2025)")
    parser.add_argument("--odds-dir", type=str,
                        default=os.environ.get("HISTORICAL_ODDS_DIR", ""),
                        help="Directory containing historical odds JSON files")
    parser.add_argument("--no-odds", action="store_true",
                        help="Skip odds loading entirely")
    parser.add_argument("--output", type=str, default=None,
                        help="Output weights.json path (default: pyMLB/data/weights.json)")
    parser.add_argument("--limit-dates", type=int, default=None,
                        help="Limit to first N dates (for testing)")
    args = parser.parse_args()

    season = args.season
    print(f"\n{'='*60}")
    print(f"MLB Backfill — Season {season}")
    print(f"{'='*60}\n")

    # -------------------------------------------------------------------------
    # 1. Fetch team stats (uses cache if available)
    # -------------------------------------------------------------------------
    print(f"[1/5] Fetching team stats for {season}...")
    # Use a season-level date key so all fetches for this season share one cache
    season_date_key = f"{season}0401"  # Start of season approximation
    team_stats = fetch_team_stats(season=season, date_str=season_date_key)
    print(f"  Got stats for {len(team_stats)} teams")

    if not team_stats:
        print("  WARNING: No team stats available. Training will use defaults only.")

    # Wrap in a date-keyed dict for build_training_data
    team_stats_by_date = {season_date_key: team_stats}

    # -------------------------------------------------------------------------
    # 2. Fetch pitcher stats (uses cache if available)
    # -------------------------------------------------------------------------
    print(f"\n[2/5] Fetching pitcher stats for {season}...")
    pitcher_stats = fetch_pitcher_stats(season=season, date_str=season_date_key)
    n_starters = sum(len(v) for v in pitcher_stats.get("starters", {}).values())
    n_bullpen_teams = len(pitcher_stats.get("bullpens", {}))
    print(f"  Got {n_starters} individual starters, {n_bullpen_teams} team bullpens")

    # -------------------------------------------------------------------------
    # 3. Fetch season scores
    # -------------------------------------------------------------------------
    print(f"\n[3/5] Fetching season scores for {season}...")
    final_scores = fetch_season_scores(season, limit_dates=args.limit_dates)
    print(f"  {len(final_scores)} games loaded")

    # -------------------------------------------------------------------------
    # 4. Load historical odds
    # -------------------------------------------------------------------------
    print(f"\n[4/5] Loading historical odds...")
    historical_odds = {}
    if args.no_odds:
        print("  --no-odds flag set, skipping odds")
    elif args.odds_dir:
        historical_odds = load_historical_odds(args.odds_dir, season)
    else:
        print("  No --odds-dir specified. Use HISTORICAL_ODDS_DIR env var or --odds-dir to include odds.")

    # Count games with odds coverage
    odds_game_keys = set()
    for day_odds in historical_odds.values():
        odds_game_keys.update(day_odds.keys())
    n_with_odds = sum(
        1 for s in final_scores
        if f"{s.get('away','')}@{s.get('home','')}" in odds_game_keys
        or f"{s.get('home','')}@{s.get('away','')}" in odds_game_keys
    )
    print(f"  {len(final_scores)} games loaded, {n_with_odds} with odds coverage")

    # -------------------------------------------------------------------------
    # 5. Build training data
    # -------------------------------------------------------------------------
    print(f"\n[5/5] Building training data...")
    training = build_training_data(
        season=season,
        team_stats_by_date=team_stats_by_date,
        pitcher_stats=pitcher_stats,
        final_scores=final_scores,
        historical_odds=historical_odds,
    )

    n_games = training["n_games"]
    if n_games < 50:
        print(f"\n  WARNING: Only {n_games} training samples (minimum 50 required).")
        print("  Cannot train ridge regression reliably. Exiting.")
        return

    X = training["X"]
    y_margin = training["y_margin"]
    y_total = training["y_total"]

    print(f"\n  Training ridge regression on {n_games} games...")
    print(f"  Note: Recency decay (0.998^n_from_end) applied via data ordering.")
    print(f"        Most recent games have weight ~1.0, earliest ~{0.998**(n_games-1):.3f}")

    # -------------------------------------------------------------------------
    # Train
    # -------------------------------------------------------------------------
    weights = model_engine.train_ridge(X, y_margin, y_total=y_total)

    # -------------------------------------------------------------------------
    # Print results
    # -------------------------------------------------------------------------
    print(f"\n{'='*60}")
    print(f"Training Results")
    print(f"{'='*60}")
    print(f"  Alpha:        {weights.get('alpha', '?'):.4f}")
    print(f"  R²:           {weights.get('r_squared', 0.0):.4f}")
    print(f"  Residual Var: {weights.get('residual_var', 0.0):.2f}")
    print(f"  N train:      {weights.get('n_train', 0)}")

    feat_cols = weights.get("feature_columns", defaults.FEATURE_COLUMNS)
    coefs = weights.get("coefficients", [])
    print(f"\n  Top 5 features by coefficient magnitude:")
    pairs = sorted(zip(feat_cols, coefs), key=lambda x: abs(x[1]), reverse=True)
    for fname, coef in pairs[:5]:
        print(f"    {fname:30s}: {coef:+.4f}")

    # -------------------------------------------------------------------------
    # Save weights
    # -------------------------------------------------------------------------
    if args.output:
        output_path = args.output
    else:
        output_path = os.path.join(_DATA_DIR, "weights.json")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    weights["backfill_season"] = season
    weights["backfill_date"] = datetime.date.today().isoformat()
    model_engine.save_weights(weights, output_path)
    print(f"\n  Weights saved to: {output_path}")

    # -------------------------------------------------------------------------
    # Optional: seed calibration
    # -------------------------------------------------------------------------
    store_path = os.path.join(_DATA_DIR, "store.json")
    if os.path.exists(store_path):
        print(f"\n  Seeding calibration from {store_path}...")
        try:
            from calibration import build_calibration_table
            from store import load_store
            store = load_store(store_path)
            runs = store.get("runs", [])
            graded = [r for r in runs if r.get("result") is not None]
            if graded:
                calib = build_calibration_table(graded)
                calib_path = os.path.join(_DATA_DIR, "calibration.json")
                with open(calib_path, "w", encoding="utf-8") as f:
                    json.dump(calib, f, indent=2)
                print(f"  Calibration table built ({len(graded)} graded picks) -> {calib_path}")
            else:
                print(f"  No graded picks found in store, skipping calibration.")
        except Exception as exc:
            print(f"  Calibration seeding failed: {exc}")

    print(f"\n{'='*60}")
    print(f"Backfill complete.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
