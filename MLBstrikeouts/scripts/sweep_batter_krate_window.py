#!/usr/bin/env python3
"""A/B: rolling-45d (live) vs season-to-date primary window for the
per-batter K-rates that feed the live LINEUP_K_METHOD="pa_weighted" lineup
K% calc (sources.mlb_stats.fetch_batter_k_rates).

NOTE: a similar swap for the TEAM-level aggregate (fetch_team_batting_stats,
used only by the non-live "pa_weighted_team" method) was tried and reverted
(-27u / -8pp WR, see mlb_stats.py:30-40). This script tests the INDIVIDUAL
batter-level version that actually feeds the live model, which has not been
tested before.

Writes its season-primary cache to a *_SEASONPRIMARY_* cache filename so it
never reads or overwrites the live rolling-45d batter_k_rates_*.json cache
files under data/pitcher_cache/mlb/.

Usage:
    cd MLBstrikeouts
    python -m scripts.sweep_batter_krate_window
"""
import sys, os, glob, io, contextlib

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import numpy as np
import defaults
import props_backfill
import sources.mlb_stats as mlb_stats
from props_backfill import backfill
from scripts.sweep_lineup_blend import report, EMP_STD_DIR, WINDOWS

_live_fetch_batter_k_rates = props_backfill.fetch_batter_k_rates


def _fetch_batter_k_rates_season_primary(season=None, through_date=None):
    """Same as mlb_stats.fetch_batter_k_rates but season-to-date is PRIMARY
    and the rolling-45d window is the fallback for under-sampled batters.
    Cached separately so the live rolling-45d cache is never touched."""
    season = season or mlb_stats._current_season()
    assert through_date is not None, "season-primary test is walk-forward only"

    thru_key = through_date.replace("-", "")
    cache_path = mlb_stats.CACHE_DIR / f"batter_k_rates_SEASONPRIMARY_{season}_thru_{thru_key}.json"
    cached = mlb_stats._load_cache(cache_path, max_age_hours=None)
    if cached is not None:
        return {int(k): v for k, v in cached.items()}

    start_date, end_date = mlb_stats._date_window(season, through_date)
    window = mlb_stats._fetch_batter_k_rates_window(season, start_date, end_date)      # rolling 45d
    season_start = f"{season}-03-20"
    fallback = mlb_stats._fetch_batter_k_rates_window(season, season_start, end_date)  # season-to-date

    result = {}
    all_pids = set(window.keys()) | set(fallback.keys())
    for pid in all_pids:
        s = fallback.get(pid)   # season-to-date is now PRIMARY
        w = window.get(pid)
        if s and s.get("pa", 0) >= mlb_stats.RECENT_MIN_BATTER_PA:
            result[pid] = s
        elif w:
            result[pid] = w
        elif s:
            result[pid] = s

    print(f"  [mlb_stats] Fetched K rates (season-primary thru {through_date}) "
          f"for {len(result)} batters")
    mlb_stats._save_cache(cache_path, result)
    return result


def _wipe_emp_std():
    for p in glob.glob(os.path.join(EMP_STD_DIR, "emp_std_*.json")):
        try: os.remove(p)
        except OSError: pass


def run_config(season_primary):
    defaults.PROJ_LINEUP_WEIGHT = 0.8
    defaults.CSW_XBA_BLEND_WEIGHT = 0.2
    defaults.K_QUALITY_METRIC = "whiff"
    defaults.BF_CAP = 25.0
    defaults.VAR_MULT['strikeouts'] = 1.30
    defaults.BF_MULT = 1.00
    defaults.K_RATE_CAP_FLOOR = 0.36
    props_backfill.fetch_batter_k_rates = (
        _fetch_batter_k_rates_season_primary if season_primary else _live_fetch_batter_k_rates
    )
    _wipe_emp_std()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = backfill()
    return (r or {}).get('strikeouts', {}).get('picks', []) or []


def main():
    print("Running rolling-45d (live) ...")
    live_picks = run_config(season_primary=False)
    print("Running season-to-date primary ...")
    season_picks = run_config(season_primary=True)

    props_backfill.fetch_batter_k_rates = _live_fetch_batter_k_rates

    for label, start, end in WINDOWS:
        a = report(live_picks, start, end)
        b = report(season_picks, start, end)
        print(f"\n=== {label} ===")
        print(f'  {"":<20s} {"rolling-45d (live)":>20s}  {"season-to-date":>20s}  {"delta":>10s}')
        print(f'  {"n":<20s} {a["n"]:>20d}  {b["n"]:>20d}  {b["n"]-a["n"]:>+10d}')
        print(f'  {"W-L":<20s} {(str(a["w"])+"-"+str(a["l"])):>20s}  {(str(b["w"])+"-"+str(b["l"])):>20s}')
        print(f'  {"WR":<20s} {a["wr"]:>19.1f}%  {b["wr"]:>19.1f}%  {b["wr"]-a["wr"]:>+9.1f}p')
        print(f'  {"ROI":<20s} {a["roi"]:>+19.2f}%  {b["roi"]:>+19.2f}%  {b["roi"]-a["roi"]:>+9.2f}p')
        print(f'  {"units":<20s} {a["u"]:>+19.2f}u  {b["u"]:>+19.2f}u  {b["u"]-a["u"]:>+9.2f}u')
        print(f'  {"MAE":<20s} {a["mae"]:>20.3f}  {b["mae"]:>20.3f}  {b["mae"]-a["mae"]:>+10.3f}')


if __name__ == '__main__':
    main()
