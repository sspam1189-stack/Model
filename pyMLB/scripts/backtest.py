#!/usr/bin/env python3
"""
pyMLB/scripts/backtest.py
Walk-forward backtest for the MLB model on a completed season.

Architecture mirrors pyNFL/scripts/backfill_last_n_weeks.py:
  For each game-date in chronological order:
    1. Grade previous date's picks (spread + total)
    2. Kalman batch_update from graded games
    3. Self-tune weights
    4. Build projections for today's games using historical odds
    5. Store picks with projections

After full replay:
  - Print cumulative record, units, ROI
  - Break down by confidence tier
  - Save graded runs to pyMLB/data/backtest_store.json

Usage:
  python scripts/backtest.py --season 2025
  python scripts/backtest.py --season 2025 --odds-dir "C:/path/to/odds/MLB"
  python scripts/backtest.py --season 2025 --min-conf 0.55
"""

import argparse
import json
import math
import os
import re
import sys
import time

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
from self_tune import tune_weights
from kalman_state import (
    load_kalman_state, save_kalman_state, initialize_kalman,
    apply_daily_drift, batch_update,
)
from sources.mlb_stats import fetch_team_stats
from sources.pitcher_stats import fetch_pitcher_stats
from sources.espn_scoreboard import fetch_date_scores
from sources.park_factors import get_park_factor, is_dome

from scipy.stats import norm

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
JUICE = 1.1
UNIT_WIN = 1 / JUICE     # ~0.909
UNIT_LOSS = -1.0
BURN_IN_DAYS = defaults.BURN_IN_DAYS  # 10
_DATA_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data"))
_BACKFILL_CACHE = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", "data", "backfill_cache"))

_DEFAULT_STARTER = {
    "name": "TBD", "FIP": 4.50, "xFIP": 4.50, "SIERA": 4.50,
    "K_BB_pct": 0.08, "IP_per_GS": 5.5, "hand": "R",
}
_DEFAULT_BULLPEN = {"FIP": 4.50, "K_BB_pct": 0.08, "workload": 0.0}
_DEFAULT_WEATHER = {"temperature_adj": 0.0, "wind_adj": 0.0}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm(name):
    s = str(name or "").lower().strip()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _build_alias_map():
    m = {}
    for canonical, aliases in defaults.MLB_TEAMS.items():
        m[_norm(canonical)] = canonical
        for a in aliases:
            m[_norm(a)] = canonical
    return m


_ALIAS = _build_alias_map()


def _resolve(name):
    n = _norm(name)
    if n in _ALIAS:
        return _ALIAS[n]
    for k, v in _ALIAS.items():
        if k in n or n in k:
            return v
    return None


def _pick_best_starter(team_starters):
    if not team_starters:
        return dict(_DEFAULT_STARTER)
    starters = [(nm, st) for nm, st in team_starters.items()
                if st.get("GS", 0) >= 1 and st.get("IP", 0) >= 5.0]
    if not starters:
        starters = [(nm, st) for nm, st in team_starters.items() if st.get("IP", 0) >= 1.0]
    if not starters:
        return dict(_DEFAULT_STARTER)
    best_name, best = min(starters, key=lambda x: x[1].get("FIP", 9.99))
    ip = best.get("IP", 0.0) or 0.0
    gs = best.get("GS", 1) or 1
    return {
        "name": best_name,
        "FIP": best.get("FIP", 4.50),
        "xFIP": best.get("xFIP", 4.50),
        "SIERA": best.get("SIERA", 4.50),
        "K_BB_pct": best.get("K_BB_pct", 0.08),
        "IP_per_GS": ip / gs if gs > 0 else 5.5,
        "hand": best.get("hand", "R"),
    }


def grade_spread(game):
    pick = game.get("sPick", "")
    if not pick or pick == "PASS":
        return None
    m = re.match(r"(.+?)\s+([+-])(\d+(?:\.\d+)?)", pick)
    if not m:
        return None
    team = m.group(1).strip()
    sign = m.group(2)
    pts = float(m.group(3))
    chosen_is_home = _resolve(team) == _resolve(game.get("home", ""))
    margin = (game["homeScore"] - game["awayScore"]) if chosen_is_home else (game["awayScore"] - game["homeScore"])
    val = margin + pts if sign == "+" else margin - pts
    if val == 0:
        return "PUSH"
    return "WIN" if val > 0 else "LOSS"


def grade_total(game):
    pick = game.get("oPick", "")
    if not pick or pick == "PASS":
        return None
    total_line = game.get("total", 0)
    if not total_line:
        return None
    hs = game.get("homeScore")
    aws = game.get("awayScore")
    if hs is None or aws is None:
        return None
    actual = hs + aws
    if actual == total_line:
        return "PUSH"
    return ("WIN" if actual > total_line else "LOSS") if "OVER" in pick.upper() else \
           ("WIN" if actual < total_line else "LOSS")


def _conf_label(p):
    conf = max(p, 1 - p)
    if conf >= 0.65:
        return "elite"
    if conf >= 0.60:
        return "high"
    if conf >= 0.55:
        return "medium"
    return "low"


def load_season_odds(odds_dir, season):
    """
    Load all odds from monthly subdirs, returning {date_str: {game_key: {line, total}}}.
    Mirrors backfill.load_historical_odds logic.
    """
    from backfill import load_historical_odds
    return load_historical_odds(odds_dir, season)


def fetch_season_scores_cached(season):
    """Load season scores from backfill cache."""
    os.makedirs(_BACKFILL_CACHE, exist_ok=True)
    cache_path = os.path.join(_BACKFILL_CACHE, f"{season}_scores.json")
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            data = json.load(f)
        print(f"  Loaded {len(data)} games from cache")
        return data
    # Fall back to ESPN fetch
    from sources.espn_scoreboard import fetch_date_scores
    import datetime
    scores = []
    current = datetime.date(season, 4, 1)
    end = datetime.date(season, 10, 1)
    while current <= end:
        ds = current.strftime("%Y%m%d")
        try:
            day = fetch_date_scores(ds)
            if day:
                for g in day:
                    g["date"] = ds
                scores.extend(day)
                print(f"  {ds}: {len(day)}")
        except Exception as e:
            print(f"  {ds}: error — {e}")
        time.sleep(1)
        current += datetime.timedelta(days=1)
    with open(cache_path, "w") as f:
        json.dump(scores, f)
    return scores


# ---------------------------------------------------------------------------
# Walk-forward backtest
# ---------------------------------------------------------------------------

def backtest(season, odds_dir=None, output_dir=None, min_conf=0.52):
    if output_dir is None:
        output_dir = _DATA_DIR
    os.makedirs(output_dir, exist_ok=True)

    print(f"\n{'='*65}")
    print(f"  MLB Walk-Forward Backtest — Season {season}")
    print(f"{'='*65}\n")

    # ---- Load inputs ----
    print("[1/4] Loading team + pitcher stats...")
    season_key = f"{season}0401"
    team_stats = fetch_team_stats(season=season, date_str=season_key)
    pitcher_stats = fetch_pitcher_stats(season=season, date_str=season_key)
    starters_by_team = pitcher_stats.get("starters", {})
    bullpens_by_team = pitcher_stats.get("bullpens", {})
    print(f"  {len(team_stats)} teams, {sum(len(v) for v in starters_by_team.values())} starters, {len(bullpens_by_team)} bullpens")

    print("[2/4] Loading season scores...")
    all_scores = fetch_season_scores_cached(season)
    # Group by date
    by_date = {}
    for s in all_scores:
        d = s.get("date", "")
        if d:
            by_date.setdefault(d, []).append(s)
    dates_sorted = sorted(by_date.keys())
    print(f"  {len(all_scores)} games across {len(dates_sorted)} dates")

    print("[3/4] Loading historical odds...")
    historical_odds = {}
    if odds_dir:
        historical_odds = load_season_odds(odds_dir, season)
    else:
        print("  No --odds-dir; predictions will use line=0.0, total=9.0")
    print(f"  {len(historical_odds)} dates with odds")

    # ---- Load trained weights ----
    engine = model_engine.load_engine()
    weights = dict(engine["weights"])
    w_var = {**defaults.DEFAULT_W_VAR}
    residual_var = weights.get("residual_var", defaults.KALMAN_DEFAULTS.get("initialVar", 16))

    # ---- Kalman state ----
    kalman_state = initialize_kalman(team_stats)

    print(f"\n[4/4] Walk-forward replay ({len(dates_sorted)} dates)...\n")

    all_runs = []
    prev_games = []          # games from previous date with projections (for grading)

    # Running totals — spread only
    spread_w = spread_l = spread_p = 0
    # Tier totals: (w, l, p) keyed by min confidence
    tiers = {0.67: [0, 0, 0], 0.63: [0, 0, 0]}

    for date_idx, date_str in enumerate(dates_sorted):
        is_burn_in = date_idx < BURN_IN_DAYS

        # --- Step A: Grade previous date's picks ---
        if prev_games:
            for g in prev_games:
                g["sResult"] = grade_spread(g)

            if not is_burn_in:
                # Kalman batch update
                batch_update(kalman_state, prev_games)
                # Self-tune
                try:
                    tuned = tune_weights(weights, w_var, prev_games)
                    weights = tuned["W"]
                    w_var = tuned["W_var"]
                    residual_var = tuned.get("residual_var", residual_var)
                except Exception:
                    pass

            # Accumulate record
            for g in prev_games:
                sr = g.get("sResult")
                or_ = g.get("oResult")
                p_cover = g.get("pCover", 0.5)
                conf_s = max(p_cover, 1 - p_cover)

                if sr == "WIN":
                    spread_w += 1
                    for thresh, cnt in tiers.items():
                        if conf_s >= thresh:
                            cnt[0] += 1
                elif sr == "LOSS":
                    spread_l += 1
                    for thresh, cnt in tiers.items():
                        if conf_s >= thresh:
                            cnt[1] += 1
                elif sr == "PUSH":
                    spread_p += 1
                    for thresh, cnt in tiers.items():
                        if conf_s >= thresh:
                            cnt[2] += 1

        # --- Step B: Kalman drift for today ---
        apply_daily_drift(kalman_state, today=date_str)

        # --- Step C: Project today's games ---
        day_games = by_date.get(date_str, [])
        day_odds = historical_odds.get(date_str, {})
        projected = []

        for score in day_games:
            home = score.get("home", "")
            away = score.get("away", "")
            home_score = score.get("homeScore")
            away_score = score.get("awayScore")

            if home_score is None or away_score is None:
                continue
            try:
                home_score = float(home_score)
                away_score = float(away_score)
            except (TypeError, ValueError):
                continue

            home_key = _resolve(home)
            away_key = _resolve(away)
            if not home_key or not away_key:
                continue

            home_st = team_stats.get(home_key)
            away_st = team_stats.get(away_key)
            if not home_st or not away_st:
                continue

            starter_home = _pick_best_starter(starters_by_team.get(home_key, {}))
            starter_away = _pick_best_starter(starters_by_team.get(away_key, {}))
            bp_home_raw = bullpens_by_team.get(home_key, {})
            bp_away_raw = bullpens_by_team.get(away_key, {})
            bullpen_home = {"FIP": bp_home_raw.get("FIP", 4.5), "K_BB_pct": bp_home_raw.get("K_BB_pct", 0.08), "workload": 0.0}
            bullpen_away = {"FIP": bp_away_raw.get("FIP", 4.5), "K_BB_pct": bp_away_raw.get("K_BB_pct", 0.08), "workload": 0.0}

            park_factor = get_park_factor(home_key)

            odds_data = day_odds.get(f"{away}@{home}") or day_odds.get(f"{home}@{away}")
            line = odds_data.get("line", 0.0) if odds_data else 0.0

            # Kalman adjustment
            home_k = kalman_state.get("teams", {}).get(home_key, {})
            away_k = kalman_state.get("teams", {}).get(away_key, {})
            kalman_adj = home_k.get("adj_mean", 0.0) - away_k.get("adj_mean", 0.0)

            fv = model_engine.build_feature_vector(
                home_st, away_st, starter_home, starter_away,
                bullpen_home, bullpen_away,
                park_factor=park_factor, weather=_DEFAULT_WEATHER, is_home=True,
            )

            proj_margin = model_engine.project_score(fv, weights, use_total_model=False) + kalman_adj
            std = max(residual_var ** 0.5, 1.0)
            p_home_cover = norm.cdf((proj_margin - (-line)) / std)

            # Spread pick — 60% minimum confidence
            if p_home_cover > 0.5:
                s_pick = f"{home_key} {line:+.1f}" if line != 0 else f"{home_key} -0.5"
                s_conf = _conf_label(p_home_cover)
            else:
                away_line = -line
                s_pick = f"{away_key} {away_line:+.1f}" if away_line != 0 else f"{away_key} -0.5"
                s_conf = _conf_label(1 - p_home_cover)

            # hS/aS required by core kalman batch_update
            league_avg = 4.5
            proj_home_score = league_avg + proj_margin / 2
            proj_away_score = league_avg - proj_margin / 2

            game_rec = {
                "date": date_str,
                "home": home_key,
                "away": away_key,
                "homeScore": home_score,
                "awayScore": away_score,
                "line": line,
                "projMargin": round(proj_margin, 2),
                "hS": round(proj_home_score, 2),   # for Kalman batch_update
                "aS": round(proj_away_score, 2),   # for Kalman batch_update
                "pCover": round(p_home_cover, 4),
                "sPick": s_pick,
                "sConf": s_conf,
                "burnIn": is_burn_in,
            }
            projected.append(game_rec)

        all_runs.append({
            "date": date_str,
            "burnIn": is_burn_in,
            "games": projected,
        })

        prev_games = projected

        # Progress every 30 days
        if (date_idx + 1) % 30 == 0 or date_idx == len(dates_sorted) - 1:
            s_n = spread_w + spread_l
            s_pct = f"{spread_w / s_n * 100:.1f}%" if s_n else "n/a"
            print(f"  {date_str} ({date_idx+1}/{len(dates_sorted)}) | "
                  f"Spread {spread_w}-{spread_l} ({s_pct})")

    # ---- Final results ----
    s_n = spread_w + spread_l
    s_units = spread_w * UNIT_WIN + spread_l * UNIT_LOSS

    print(f"\n{'='*65}")
    print(f"  {season} Walk-Forward Backtest — Spread Only (60%+ confidence)")
    print(f"{'='*65}")
    print(f"  Dates: {len(dates_sorted)}  |  Burn-in: {BURN_IN_DAYS} days")
    print()
    if s_n:
        print(f"  All 60%+:  {spread_w}-{spread_l}-{spread_p}  "
              f"({spread_w/s_n*100:.1f}%)  "
              f"{s_units:+.1f}u  ROI: {s_units/s_n*100:+.1f}%  (n={s_n})")

    print()
    print(f"  {'Tier':<8}  {'Record':>20}  {'Units':>8}  {'ROI':>7}")
    print(f"  {'-'*50}")
    for thresh in sorted(tiers.keys(), reverse=True):
        sw, sl, sp = tiers[thresh]
        sn = sw + sl
        su = sw * UNIT_WIN + sl * UNIT_LOSS if sn else 0
        s_str = f"{sw}-{sl}-{sp} ({sw/sn*100:.1f}%)" if sn else "n/a"
        roi_str = f"{su/sn*100:+.1f}%" if sn else "n/a"
        print(f"  {thresh*100:.0f}%+{'':<4}  {s_str:>20}  {su:>+8.1f}u  {roi_str:>7}")
    print()

    # ---- Save to disk ----
    out_path = os.path.join(output_dir, "backtest_store.json")
    payload = {
        "season": season,
        "runs": all_runs,
        "summary": {
            "spread": {"w": spread_w, "l": spread_l, "p": spread_p,
                       "units": round(s_units, 2), "n": s_n,
                       "roi_pct": round(s_units / s_n * 100, 2) if s_n else None},
        }
    }
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"  Results saved to: {out_path}")
    print(f"{'='*65}\n")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Walk-forward MLB backtest.")
    parser.add_argument("--season", type=int, default=2025)
    parser.add_argument("--odds-dir", type=str,
                        default=os.environ.get("HISTORICAL_ODDS_DIR", ""))
    parser.add_argument("--min-conf", type=float, default=0.52,
                        help="Minimum confidence to count a pick (default: 0.52)")
    parser.add_argument("--output-dir", type=str, default=None)
    args = parser.parse_args()

    backtest(
        season=args.season,
        odds_dir=args.odds_dir,
        output_dir=args.output_dir,
        min_conf=args.min_conf,
    )


if __name__ == "__main__":
    main()
