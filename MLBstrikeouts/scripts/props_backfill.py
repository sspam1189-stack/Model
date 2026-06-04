#!/usr/bin/env python3
"""
MLBstrikeouts/scripts/props_backfill.py
Walk-forward backfill of MLB pitcher prop projections with Kalman filtering.

For each game date, projects pitcher stats using only prior games' data,
then compares to actual results. The Kalman filter is trained incrementally.

Usage:
    cd MLBstrikeouts
    python -m scripts.props_backfill
    python -m scripts.props_backfill --season 2026 --start-game 10
    python -m scripts.props_backfill --start-date 2026-04-01
"""

import sys, os, math, argparse, json
import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from sources.mlb_stats import (
    fetch_pitcher_game_logs, fetch_team_batting_stats,
    fetch_pitcher_advanced_stats_through,
)
from props_engine import (
    organize_pitcher_logs, project_pitcher_props, STAT_KEYS,
)
from pitcher_kalman import (
    new_pitcher_kalman_state, batch_update_from_game_logs,
    apply_drift, kalman_summary, PITCHER_KALMAN_DEFAULTS,
    save_pitcher_kalman_state, prune_inactive_pitchers,
)
from defaults import (
    ROLLING_WINDOW, MIN_GAMES, current_season,
    EMPIRICAL_STD_MIN_SAMPLE, DEFAULT_EMPIRICAL_STD,
)

_EMP_STD_CACHE_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "..", "data", "emp_std_cache", "mlb")
)


def _compute_and_cache_emp_std(date_key, date_iso, results_so_far):
    """
    Walk-forward emp_std for the backfill loop. results_so_far is the
    accumulating `results` dict from the backfill — its `projections`
    and `actuals` lists already only contain data from earlier dates
    (we append AFTER projecting each date).

    If data/emp_std_cache/mlb/emp_std_<date_key>.json already exists,
    reuse it so walk-forward stays consistent across reruns. Otherwise
    compute, write, and return. Returns dict like {"strikeouts": 2.23}
    or {} when sample is too small (engine then falls back to
    DEFAULT_EMPIRICAL_STD).
    """
    cache_path = os.path.join(_EMP_STD_CACHE_DIR, f"emp_std_{date_key}.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r") as f:
                payload = json.load(f)
            return {k: v for k, v in payload.items()
                    if k not in ("source", "computed_at", "date_iso")}
        except Exception as e:
            print(f"  [empirical_std] cache read failed ({e}), recomputing")

    out = {}
    for market, bundle in results_so_far.items():
        projs = bundle.get("projections") or []
        actuals = bundle.get("actuals") or []
        n = min(len(projs), len(actuals))
        if n < EMPIRICAL_STD_MIN_SAMPLE:
            continue
        residuals = []
        for p, a in zip(projs[:n], actuals[:n]):
            try:
                residuals.append(float(a) - float(p))
            except (TypeError, ValueError):
                continue
        if len(residuals) < EMPIRICAL_STD_MIN_SAMPLE:
            continue
        mean = sum(residuals) / len(residuals)
        var = sum((r - mean) ** 2 for r in residuals) / len(residuals)
        out[market] = math.sqrt(var)
        out[f"n_{market}"] = len(residuals)

    payload = dict(out)
    payload["source"] = "backfill"
    payload["computed_at"] = datetime.now().isoformat(timespec="seconds")
    payload["date_iso"] = date_iso
    try:
        os.makedirs(_EMP_STD_CACHE_DIR, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(payload, f, indent=2)
    except Exception as e:
        print(f"  [empirical_std] cache write failed: {e}")
    return out
from sources.weather import fetch_game_weather
from sources.mlb_stats import (
    fetch_pitcher_handedness_splits,
    fetch_pitcher_handedness_splits_season,
    fetch_player_bat_sides, fetch_lineup_handedness,
    fetch_batter_k_rates, fetch_batter_career_k_rates, load_pitch_hands,
    CACHE_DIR,
)


# ---------------------------------------------------------------------------
# Field normalization
# ---------------------------------------------------------------------------

def _adv_stats_through(date_iso, season, pitch_hands):
    """
    Walk-forward starter-only adv stats AS OF a specific date.

    Hits the MLB Stats API `byDateRange` endpoint with `sitCodes=sp` so
    we get exactly what the daily pipeline pulls — just bounded by the
    end_date. Results cache per-date, so historical days never refetch.
    Pitch-hand is merged in from the static lookup since the API split
    response doesn't carry it.
    """
    adv = fetch_pitcher_advanced_stats_through(season, date_iso)
    for pid_str, row in adv.items():
        try:
            row["pitch_hand"] = pitch_hands.get(int(pid_str), "R")
        except (ValueError, TypeError):
            row["pitch_hand"] = "R"
    return adv


def _normalize_game_log(g):
    """
    Ensure game log has both player_* and pitcher_* field names.

    MLB Stats API returns player_id / player_name, but
    organize_pitcher_logs and pitcher_kalman expect pitcher_id / pitcher_name.
    Add aliases so both consumers work.
    """
    if "pitcher_id" not in g and "player_id" in g:
        g["pitcher_id"] = g["player_id"]
    if "pitcher_name" not in g and "player_name" in g:
        g["pitcher_name"] = g["player_name"]
    # Also keep IP as float for organize_pitcher_logs filter
    if "IP" not in g and "ip" in g:
        g["IP"] = g["ip"]
    # Opponent field: props_engine uses "opp", mlb_stats uses "opponent"
    if "opp" not in g and "opponent" in g:
        g["opp"] = g["opponent"]
    return g


def _calc_pick_units(odds, won):
    """
    Calculate units for a single pick using risk-to-win-1u convention.

      + odds:  risk 1u to win (odds/100)u
      - odds:  risk (|odds|/100)u to win 1u

    If odds is None, returns 0.0 (pick contributes nothing to units). Every
    real pick has a recorded price (FanDuel / Odds API); a None price means
    the odds fetch broke. Silently pricing at -110 would misreport P/L, so
    we surface the gap instead by contributing zero.
    """
    if odds is None:
        return 0.0

    odds = int(odds)
    if odds > 0:
        return odds / 100.0 if won else -1.0
    else:
        return 1.0 if won else -abs(odds) / 100.0


def _calc_units(picks):
    """Calculate total units from backfill picks (have 'won' and 'odds')."""
    return sum(_calc_pick_units(p.get("odds"), p["won"]) for p in picks)


def _calc_units_from_dashboard(picks):
    """Calculate total units from dashboard picks (have 'result' and 'odds')."""
    return sum(
        _calc_pick_units(p.get("odds"), p["result"] == "WIN")
        for p in picks if p.get("result") in ("WIN", "LOSS")
    )


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------

def backfill(season=None, start_game=10, start_date=None):
    """
    Walk-forward backfill with Kalman filter trained incrementally.
    Always resets Kalman state from scratch (no prior history).

    For each game date:
      1. Project using prior data + current Kalman state (no lookahead)
      2. Compare projections to actuals
      3. Update Kalman state with today's actual results
      4. Move to next date

    Parameters
    ----------
    season : int or None
        MLB season year (default: current_season()).
    start_game : int
        Minimum game dates before projecting (default: 10).
    start_date : str or None
        If set (YYYY-MM-DD), start projecting from this date instead of
        using start_game. Games before this date still train the Kalman.
    """
    season = season or current_season()
    date_label = f"from {start_date}" if start_date else f"after game #{start_game}"
    print(f"\n{'='*60}")
    print(f"  MLB PITCHER PROPS BACKTEST (Bayesian Kalman)")
    print(f"  Season: {season}  |  Start {date_label}")
    print(f"{'='*60}")

    # Load pitcher game logs (from cache if available)
    print(f"\n  Loading pitcher game logs...")
    all_logs = fetch_pitcher_game_logs(season=season)
    if not all_logs:
        print("  No pitcher game logs found. Run fetch_stats first.")
        return None

    # Normalize field names (player_id -> pitcher_id, etc.)
    for g in all_logs:
        _normalize_game_log(g)

    # Team batting is now fetched per-date inside the walk-forward loop
    # (see Phase 2b) to avoid season-to-today leakage.
    team_batting_by_date = {}

    # adv_stats / sabermetrics / savant_rates are NOT loaded as season-to-today
    # snapshots — that leaks future stats into early-season projections.  Instead
    # adv_stats is built per-date from prior game logs inside the walk-forward
    # loop (see _adv_stats_through).  saber_stats isn't read by props_engine and
    # savant_rates is only a fallback that the derived adv_stats supersedes.
    #
    # EXCEPTION: when CSW_XBA_BLEND_WEIGHT > 0, props_engine reads savant
    # whiff_pct and xba to regress pitcher_k_rate toward underlying pitch
    # quality. Savant's leaderboard can't be date-bounded at fetch, so backfill
    # replays the per-date daily snapshots run_daily cached (as-of prior_date,
    # via load_savant_rates_as_of inside the loop). Dates before daily caching
    # began get {} -> no whiff/xBA blend. No future-data leakage either way.
    from defaults import CSW_XBA_BLEND_WEIGHT, K_QUALITY_METRIC
    if CSW_XBA_BLEND_WEIGHT > 0:
        from sources.mlb_stats import (fetch_savant_pitcher_rates,
                                       compute_whiff_xba_regression,
                                       save_whiff_xba_regression,
                                       load_whiff_xba_regression_as_of,
                                       load_savant_rates_as_of,
                                       fetch_statcast_pitch_csw,
                                       load_csw_as_of)
        if K_QUALITY_METRIC == "csw":
            # Build immutable per-date CSW tallies up to yesterday so the
            # walk-forward loop can reconstruct leak-free season-to-date CSW.
            # Default start (season-03-15) covers openers; one-time cost,
            # cached days are skipped on re-run.
            fetch_statcast_pitch_csw(season=season)
            print(f"  [backfill] K_QUALITY_METRIC=csw -> CSW tallies primed")
        # Today's snapshot is used ONLY to prime today's slope cache (for the
        # next live run). It is NOT passed to historical projections — each
        # backfill date loads its own as-of snapshot inside the loop
        # (load_savant_rates_as_of) so no future whiff/xBA leaks in.
        _savant_today = fetch_savant_pitcher_rates(season=season) or {}
        print(f"  [backfill] whiff/xBA blend enabled -> loaded "
              f"{len(_savant_today)} savant entries (today's snapshot, "
              f"slope-priming only)")
        # Slope refit is WALK-FORWARD — applied inside the date loop using
        # per-date cached regression files. Today's regression file is still
        # written below for tomorrow.
        _reg_today = compute_whiff_xba_regression(_savant_today)
        if _reg_today:
            save_whiff_xba_regression(_reg_today, season=season)
            print(f"  [backfill] today's slopes cached "
                  f"(whiff={_reg_today['whiff_slope']:+.4f} "
                  f"xBA={_reg_today['xba_slope']:+.4f}  R^2={_reg_today['r2']:.3f})")

    # Load player bat-side lookup (for per-game lineup handedness)
    print(f"  Loading player bat sides...")
    bat_sides = fetch_player_bat_sides(season=season)
    print(f"  {len(bat_sides)} players with bat-side data")

    print(f"  Batter K rates will be fetched per-date (walk-forward mode)...")
    batter_k_rates_by_date = {}
    # Career handed K% (prior completed seasons, leak-free) — fetched once,
    # used as the tier-3 thin-sample fallback in compute_lineup_k_pct.
    batter_career_k_rates = fetch_batter_career_k_rates(season=season)
    pitch_hands = load_pitch_hands(season=season)

    # Load batting orders for lineup K% lookup
    from sources.mlb_stats import _load_cache as _lc
    bo_cache_path = CACHE_DIR / f"batting_orders_{season}.json"
    batting_orders_all = _lc(bo_cache_path, max_age_hours=None) or {}

    # Organize by pitcher
    pitcher_logs = organize_pitcher_logs(all_logs)
    print(f"  {len(pitcher_logs)} pitchers, {len(all_logs)} total game logs")

    # Get all unique game dates
    all_dates = sorted(set(
        g.get("game_date", "") for g in all_logs if g.get("game_date")
    ))
    print(f"  {len(all_dates)} game dates in season")

    # Always reset Kalman state from scratch
    kalman_state_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "data", "kalman_state.json"
    )
    kalman_state_path = os.path.normpath(kalman_state_path)
    if os.path.exists(kalman_state_path):
        os.remove(kalman_state_path)
        print(f"  [kalman] Reset: deleted {kalman_state_path}")
    kalman_state = new_pitcher_kalman_state()

    results = {"strikeouts": {"projections": [], "actuals": [], "picks": []}}
    total_projected = 0

    # Save baseline whiff/xBA slopes so we can restore at function end.
    import defaults as _d
    _baseline_slopes = (
        _d.CSW_K_SLOPE, _d.XBA_K_SLOPE,
        _d.CSW_LEAGUE_AVG, _d.XBA_LEAGUE_AVG,
    )
    _last_logged_slopes = None

    # --- Walk-forward loop ---
    for date_idx, game_date in enumerate(all_dates):

        # Collect today's logs (for Kalman update after projecting)
        today_date_logs = [
            g for g in all_logs
            if g.get("game_date", "") == game_date and g.get("IP", 0) >= 3.0
        ]

        # Determine if we should start projecting yet
        should_skip = False
        if start_date:
            should_skip = game_date < start_date
        else:
            should_skip = date_idx < start_game

        if should_skip:
            # Not projecting yet, but still update Kalman with this date's games
            if today_date_logs:
                batch_update_from_game_logs(kalman_state, today_date_logs)
            continue

        # Walk-forward cutoff for ALL "as-of" external stats. Games played ON
        # game_date are already complete in the historical API, and the MLB
        # Stats API `byDateRange`/`statSplits` endDate is INCLUSIVE — so any
        # fetch bounded at game_date would pull the very outcome we're trying
        # to project (the pitcher's own start, the opponent's K's tonight).
        # Bounding at game_date - 1 mirrors the adv_stats treatment below and
        # keeps backfill faithful to what run_daily sees pre-game.
        prior_date = (datetime.strptime(game_date, "%Y-%m-%d")
                      - timedelta(days=1)).strftime("%Y-%m-%d")

        # Walk-forward slope loading. Both metrics use the SAME contract:
        # replay the most recent run_daily regression cached on-or-before
        # game_date-1 (so backfill reproduces the exact slopes live locked).
        # csw additionally rebuilds the leak-free as-of merged dict for the
        # per-pitcher csw values, and inline-refits as a fallback for dates
        # before CSW daily caching began (no cache yet). Both fall back to
        # baseline (defaults.py) if data is too thin.
        _csw_merged_asof = None
        if CSW_XBA_BLEND_WEIGHT > 0 and K_QUALITY_METRIC == "csw":
            _xba_asof = load_savant_rates_as_of(prior_date, season) or {}
            _csw_asof = load_csw_as_of(prior_date, season) or {}
            _merged = {}
            for _pid, _csw in _csw_asof.items():
                _base = _xba_asof.get(_pid)
                if not _base:
                    continue
                _merged[_pid] = {**_base, "csw": _csw["csw"]}
            _csw_merged_asof = _merged
            # Slopes: replay run_daily's cached CSW regression (faithful match
            # to live); else inline-refit on the as-of merged snapshot.
            _wfr = load_whiff_xba_regression_as_of(prior_date, season=season,
                                                   metric="csw")
            _reg = _wfr or compute_whiff_xba_regression(_merged, x1_key="csw")
            if _reg:
                _d.CSW_K_SLOPE    = _reg["whiff_slope"]
                _d.XBA_K_SLOPE      = _reg["xba_slope"]
                _d.CSW_LEAGUE_AVG = _reg["whiff_mean"]
                _d.XBA_LEAGUE_AVG   = _reg["xba_mean"]
                _key = (round(_reg['whiff_slope'], 4),
                        round(_reg['xba_slope'], 4))
                if _key != _last_logged_slopes:
                    _src = (f"cached {_wfr['date_iso']}" if _wfr
                            else f"refit n={_reg['n']} R^2={_reg['r2']:.3f}")
                    print(f"  [walk-forward {game_date}] CSW slopes ({_src}): "
                          f"csw={_reg['whiff_slope']:+.4f} "
                          f"xBA={_reg['xba_slope']:+.4f}")
                    _last_logged_slopes = _key
            else:
                (_d.CSW_K_SLOPE, _d.XBA_K_SLOPE,
                 _d.CSW_LEAGUE_AVG, _d.XBA_LEAGUE_AVG) = _baseline_slopes
        elif CSW_XBA_BLEND_WEIGHT > 0:
            import datetime as _dt
            _prior_iso = (_dt.date.fromisoformat(game_date)
                          - _dt.timedelta(days=1)).isoformat()
            _wfr = load_whiff_xba_regression_as_of(_prior_iso, season=season)
            if _wfr:
                _d.CSW_K_SLOPE    = _wfr["whiff_slope"]
                _d.XBA_K_SLOPE      = _wfr["xba_slope"]
                _d.CSW_LEAGUE_AVG = _wfr["whiff_mean"]
                _d.XBA_LEAGUE_AVG   = _wfr["xba_mean"]
                _key = (round(_wfr['whiff_slope'], 4),
                        round(_wfr['xba_slope'], 4))
                if _key != _last_logged_slopes:
                    print(f"  [walk-forward {game_date}] using slopes from "
                          f"{_wfr['date_iso']}: whiff={_wfr['whiff_slope']:+.4f} "
                          f"xBA={_wfr['xba_slope']:+.4f} (n={_wfr['n']})")
                    _last_logged_slopes = _key
            else:
                # No cache yet — fall back to baseline
                (_d.CSW_K_SLOPE, _d.XBA_K_SLOPE,
                 _d.CSW_LEAGUE_AVG, _d.XBA_LEAGUE_AVG) = _baseline_slopes

        # Phase 1: Build prior-only pitcher logs for projection
        prior_logs = {}
        actual_games = {}

        for pid, games in pitcher_logs.items():
            prior = [g for g in games if g.get("game_date", "") < game_date]
            today = [g for g in games if g.get("game_date", "") == game_date]

            if prior:  # let the engine's own MIN_GAMES handle per-market filtering
                prior_logs[pid] = prior
            if today:
                actual_games[pid] = today[0]

        if not prior_logs or not actual_games:
            # Still update Kalman with today's games
            if today_date_logs:
                batch_update_from_game_logs(kalman_state, today_date_logs)
            continue

        # Phase 2: Load real prop lines — FanDuel-first, OddsAPI fallback.
        # Mirrors run_daily.py merge (FanDuel primary; OddsAPI fills gaps only).
        # OddsAPI is emergency fallback for dates where FanDuel cache is missing
        # or when FD didn't capture a particular (player, market) pair.
        real_lines = None
        try:
            from sources.odds_fanduel     import _props_cache_path as _fd_cp,  _load_cache as _fd_load
            from sources.rotowire_lineups import _norm_name
            date_key = game_date.replace("-", "")

            # Both _props_cache_path's resolve to the same mlb_props_<date>.json,
            # which co-mingles rows from every source (rotowire / fanduel /
            # oddsapi). Split them back out by source tag and merge in the EXACT
            # same order run_daily.py uses, so backfill picks the same line live
            # locked (otherwise the engine's last-write-wins can land on a stray
            # OddsAPI row, e.g. a 4.5 overwriting the Rotowire 5.5 live used).
            cached = _fd_load(_fd_cp(date_key), max_age_hours=None) or []

            def _src(p):
                return str(p.get("source", "")).lower()

            rw_props = [p for p in cached if _src(p).startswith("rotowire")]
            fd_props = [p for p in cached if "fanduel" in _src(p)]
            oa_props = [p for p in cached
                        if not _src(p).startswith("rotowire")
                        and "fanduel" not in _src(p)]

            # 1. Rotowire (priority) + 2. FanDuel (fallback; Rotowire wins for
            #    strikeouts by normalized pitcher name) — mirrors run_daily.
            rw_strikeout_names = {
                _norm_name(p.get("player")) for p in rw_props
                if p.get("market") == "strikeouts"
            }
            combined = list(rw_props)
            for p in fd_props:
                if p.get("market") == "strikeouts":
                    if _norm_name(p.get("player")) in rw_strikeout_names:
                        continue  # Rotowire wins
                combined.append(p)

            # 3. OddsAPI last-resort fallback — only (player, market) pairs not
            #    already covered by Rotowire + FanDuel.
            combined_keys = {(p.get("player", ""), p.get("market", "")) for p in combined}
            merged = list(combined)
            for p in oa_props:
                if (p.get("player", ""), p.get("market", "")) not in combined_keys:
                    merged.append(p)

            if merged:
                real_lines = merged
        except Exception:
            pass  # No props available for this date — project without lines

        # Phase 2b: Fetch lineup handedness + weather for this date
        lineup_hand = fetch_lineup_handedness(
            game_date, bat_sides=bat_sides, season=season
        )

        # Fetch per-date team batting (walk-forward: 45-day window ending at
        # prior_date, with season-to-prior_date fallback for small samples).
        # Bounded at prior_date (not game_date) so tonight's games don't leak.
        if game_date not in team_batting_by_date:
            team_batting_by_date[game_date] = fetch_team_batting_stats(
                season=season, through_date=prior_date
            )
        team_batting = team_batting_by_date[game_date]

        # Fetch per-date batter K rates the same way (bounded at prior_date).
        if game_date not in batter_k_rates_by_date:
            batter_k_rates_by_date[game_date] = fetch_batter_k_rates(
                season=season, through_date=prior_date
            )
        batter_k_rates = batter_k_rates_by_date[game_date]

        # Merge PCT_LHB into team_batting for this date's lineups
        date_team_batting = dict(team_batting)  # shallow copy
        for abbr, hand_data in lineup_hand.items():
            if abbr in date_team_batting:
                date_team_batting[abbr] = {
                    **date_team_batting[abbr],
                    "PCT_LHB": hand_data["PCT_LHB"],
                }
            else:
                date_team_batting[abbr] = {"PCT_LHB": hand_data["PCT_LHB"]}

        weather_data = fetch_game_weather(game_date)

        # Build lineup_data for this date.
        # Priority: live cache (lineups_YYYYMMDD.json — what run_daily actually saw
        # at projection time) → fall back to batting_orders_{season}.json
        # (post-game actual lineups). This makes backfill replay live's experience
        # rather than getting hindsight on late scratches/lineup card changes.
        date_lineup_data = {}
        live_cache_path = CACHE_DIR / f"lineups_{game_date.replace('-', '')}.json"
        live_lineup = _lc(live_cache_path, max_age_hours=None) or {}
        date_bo = batting_orders_all.get(game_date, {})
        for abbr in set(live_lineup) | set(date_bo):
            live = live_lineup.get(abbr) or {}
            live_ids = live.get("player_ids") if isinstance(live, dict) else None
            if live_ids:
                date_lineup_data[abbr] = {"player_ids": live_ids}
            elif date_bo.get(abbr):
                date_lineup_data[abbr] = {"player_ids": date_bo[abbr]}

        # Recompute PCT_LHB from final lineup_data so the pitcher vs-L/vs-R
        # weighting uses the same 9 batters that drive lineup K%. Mirrors
        # run_daily.py — propagates Rotowire/recent fallback lineups into the
        # PCT_LHB the pitcher hand-platoon math uses (instead of letting it
        # collapse to fetch_lineup_handedness's MLB-only result or the 0.40
        # default downstream).
        from sources.mlb_stats import _compute_pct_lhb as _pct_lhb_fn
        for abbr, ldata in date_lineup_data.items():
            pids = ldata.get("player_ids") or []
            if len(pids) < 5:
                continue
            pct = _pct_lhb_fn(pids, bat_sides)
            if abbr in date_team_batting:
                date_team_batting[abbr] = {
                    **date_team_batting[abbr], "PCT_LHB": pct,
                }
            else:
                date_team_batting[abbr] = {"PCT_LHB": pct}

        # Build probable_pitchers from today's actual game logs
        # (who actually started — mirrors what run_daily gets from schedule)
        # Use is_start flag when present; fall back to IP>=3.0 only for legacy
        # rows missing the flag. The previous IP>=3.0 gate dropped pitchers
        # who got chased early (e.g. Glasnow 5/6: 1.0 IP), silently excluding
        # them from the backfill even though they were scheduled to start.
        date_probable = []
        def _is_starter_row(g):
            if g.get("game_date", "") != game_date:
                return False
            if g.get("is_start") is True:
                return True
            if "is_start" in g:
                return False
            return g.get("IP", g.get("ip", 0)) >= 3.0
        today_starters = [g for g in all_logs if _is_starter_row(g)]
        # Group by game_id to pair home/away
        from collections import defaultdict
        games_by_id = defaultdict(list)
        for g in today_starters:
            gid = g.get("game_id", "")
            if gid:
                games_by_id[gid].append(g)
        for gid, starters in games_by_id.items():
            gm = {"game_id": gid}
            for g in starters:
                pid = g.get("pitcher_id") or g.get("player_id")
                pname = g.get("pitcher_name") or g.get("player_name", "")
                team = g.get("team", "")
                opp = g.get("opp", g.get("opponent", ""))
                is_home = g.get("is_home", False)
                if is_home:
                    gm["home_team"] = team
                    gm["away_team"] = opp
                    gm["home_pitcher_id"] = pid
                    gm["home_pitcher_name"] = pname
                else:
                    gm["away_team"] = team
                    gm["home_team"] = opp
                    gm["away_pitcher_id"] = pid
                    gm["away_pitcher_name"] = pname
            date_probable.append(gm)

        # Add game-specific lineup entries for doubleheader support
        for _gm in date_probable:
            _gid = _gm.get("game_id")
            if not _gid:
                continue
            for _tk in ("home_team", "away_team"):
                _t = _gm.get(_tk)
                if _t and _t in date_lineup_data:
                    date_lineup_data[f"{_t}|{_gid}"] = date_lineup_data[_t]

        # Build pitcher_splits for today's starters
        date_splits = {}
        for g in today_starters:
            pid = g.get("pitcher_id") or g.get("player_id")
            if pid and str(pid) not in date_splits:
                s = fetch_pitcher_handedness_splits(
                    pid, season=season, through_date=prior_date
                )
                # Also fetch season-to-prior_date splits and stash in the
                # same dict under _season suffix so props_engine can blend.
                # Bounded at prior_date so the pitcher's own start tonight
                # doesn't leak into their vs-L/vs-R K rate (same-game leak).
                s_season = fetch_pitcher_handedness_splits_season(
                    pid, season=season, through_date=prior_date
                )
                if s and s_season:
                    s["vs_left_season"] = s_season.get("vs_left", {})
                    s["vs_right_season"] = s_season.get("vs_right", {})
                if s:
                    date_splits[str(pid)] = s

        # Pull starter-only adv_stats through the prior date from the
        # MLB API (cached per-date, so historical days hit network once).
        # Backfill uses the same upstream the daily pipeline does — just
        # bounded by `prior_date` (computed at loop top) so no future leakage.
        date_adv = _adv_stats_through(prior_date, season, pitch_hands)

        # Phase 3a: walk-forward empirical_std for this date (uses only
        # graded residuals from prior dates — results_so_far has not yet
        # been appended for the current game_date). Always rewrites the
        # per-day cache file so subsequent live runs see the backfill's
        # walk-forward value (vs whatever live run had at the time).
        date_key = game_date.replace("-", "")
        runtime_emp_std = _compute_and_cache_emp_std(date_key, game_date, results)
        # Mirror run_daily's log line so backfill output is comparable.
        if runtime_emp_std:
            for _mkt in sorted(k for k in runtime_emp_std if not k.startswith("n_")):
                _val = runtime_emp_std[_mkt]
                _n = runtime_emp_std.get(f"n_{_mkt}", "?")
                _dflt = DEFAULT_EMPIRICAL_STD.get(_mkt, "n/a")
                print(f"  [empirical_std {game_date}] {_mkt}: {_val:.3f} "
                      f"(backfill, n={_n}, default {_dflt})")

        # Walk-forward savant: replay the season-to-date snapshot that was
        # cached on/before prior_date. {} when none exists (pre-caching early
        # dates) -> props_engine skips the whiff/xBA adjustment. Never uses a
        # future snapshot, so no whiff/xBA leakage.
        if CSW_XBA_BLEND_WEIGHT <= 0:
            _savant_asof = {}
        elif K_QUALITY_METRIC == "csw":
            # Merged xBA+CSW snapshot built above for the inline slope fit;
            # props_engine reads "csw" + "xba" from it (leak-free as-of).
            _savant_asof = _csw_merged_asof or {}
        else:
            _savant_asof = load_savant_rates_as_of(prior_date, season)

        # Phase 3: Project props using prior data + current Kalman state
        projections = project_pitcher_props(
            prior_logs,
            team_batting_stats=date_team_batting,
            prop_lines=real_lines,
            kalman_state=kalman_state,
            pitcher_adv_stats=date_adv,
            pitcher_sabermetrics={},
            pitcher_splits=date_splits,
            probable_pitchers=date_probable,
            weather_by_game=weather_data,
            batter_k_rates=batter_k_rates,
            lineup_data=date_lineup_data,
            savant_rates=_savant_asof,
            empirical_std=runtime_emp_std or None,
            career_k_rates=batter_career_k_rates,
        )

        # Save ALL projections for this date (for Games Explorer)
        latest_all_projections = [
            p for p in projections
            if p.get("line") is not None and p.get("proj") is not None
        ]
        for p in latest_all_projections:
            p["date"] = game_date

        # Phase 4: Grade projections against actuals
        date_picks = 0
        for proj in projections:
            player = proj["player"]
            market = proj["market"]

            if market != "strikeouts":
                continue

            actual_val = _find_actual(player, market, actual_games, pitcher_logs)
            if actual_val is None:
                continue

            actual_game = _find_actual_game(player, actual_games) or {}
            actual_outs = actual_game.get("outs")
            actual_bf = actual_game.get("bf")
            actual_pitches = actual_game.get("pitches")

            proj_val = proj["proj"]
            std = proj["std"]

            results[market]["projections"].append(proj_val)
            results[market]["actuals"].append(actual_val)

            pick = proj.get("pick")
            pick_line = proj.get("line")
            pcover = proj.get("pCover") or 0

            # Watchlist: capture sub-threshold projections (>= 0.50) so we
            # have a projection on essentially every probable start. Picks
            # remain >= 0.68 (set via MARKET_THRESHOLDS); 0.60-0.68 is the
            # "watch" band the dashboard surfaces in the All Pass tab;
            # 0.50-0.60 stays in JSON for analysis but is sub-watch noise.
            if pick in ("PASS", None):
                if pick_line is None or pcover < 0.50:
                    total_projected += 1
                    continue
                # Derive direction from edge sign
                derived_dir = "OVER" if proj_val > pick_line else "UNDER"
                if actual_val == pick_line:
                    total_projected += 1
                    continue
                would_be_won = (derived_dir == "OVER" and actual_val > pick_line) or \
                               (derived_dir == "UNDER" and actual_val < pick_line)
                results[market]["picks"].append({
                    "date": game_date,
                    "player": player,
                    "team": proj.get("team", ""),
                    "opp": proj.get("opp", ""),
                    "proj": proj_val,
                    "std": std,
                    "line": pick_line,
                    "actual": actual_val,
                    "pick": "PASS",  # dashboard hides
                    "would_be_pick": derived_dir,
                    "pCover": pcover,
                    "conf": "watch",
                    "odds": proj.get("odds"),
                    "won": would_be_won,
                    "proj_ip": proj.get("proj_ip"),
                    "proj_bf": proj.get("proj_bf"),
                    "proj_bf_capped": proj.get("proj_bf_capped"),
                    "proj_pc": proj.get("proj_pc"),
                    "opp_team_k_pct": proj.get("opp_team_k_pct"),
                    "lineup_k_pct": proj.get("lineup_k_pct"),
                    "actual_outs": actual_outs,
                    "actual_bf": actual_bf,
                    "actual_pitches": actual_pitches,
                })
                total_projected += 1
                continue

            if pick_line is None:
                total_projected += 1
                continue

            if actual_val == pick_line:
                total_projected += 1
                continue  # Push

            won = (pick == "OVER" and actual_val > pick_line) or \
                  (pick == "UNDER" and actual_val < pick_line)

            results[market]["picks"].append({
                "date": game_date,
                "player": player,
                "team": proj.get("team", ""),
                "opp": proj.get("opp", ""),
                "proj": proj_val,
                "std": std,
                "line": pick_line,
                "actual": actual_val,
                "pick": pick,
                "pCover": pcover,
                "conf": proj.get("conf"),
                "odds": proj.get("odds"),
                "won": won,
                "proj_ip": proj.get("proj_ip"),
                "proj_bf": proj.get("proj_bf"),
                "proj_bf_capped": proj.get("proj_bf_capped"),
                "proj_pc": proj.get("proj_pc"),
                "opp_team_k_pct": proj.get("opp_team_k_pct"),
                "lineup_k_pct": proj.get("lineup_k_pct"),
                "actual_outs": actual_outs,
                "actual_bf": actual_bf,
                "actual_pitches": actual_pitches,
            })
            date_picks += 1
            total_projected += 1

        # Phase 5: UPDATE Kalman with today's actual games (after projecting)
        if today_date_logs:
            batch_update_from_game_logs(kalman_state, today_date_logs)

        if date_picks > 0:
            wins_today = sum(1 for m in results for p in results[m]["picks"] if p.get("date") == game_date and p.get("won"))
            losses_today = sum(1 for m in results for p in results[m]["picks"] if p.get("date") == game_date and not p.get("won"))
            print(f"  {game_date}: {date_picks} picks ({wins_today}W-{losses_today}L)")

    # --- Save Kalman state so run_daily can pick up from here ---
    prune_inactive_pitchers(kalman_state)
    save_pitcher_kalman_state(kalman_state, kalman_state_path)
    print(f"  Saved Kalman state ({len(kalman_state['pitchers'])} pitchers) "
          f"to {kalman_state_path}")


    # Restore baseline slopes so subsequent calls in the same process
    # don't see the last-iterated date's mutated defaults.
    if CSW_XBA_BLEND_WEIGHT > 0:
        (_d.CSW_K_SLOPE, _d.XBA_K_SLOPE,
         _d.CSW_LEAGUE_AVG, _d.XBA_LEAGUE_AVG) = _baseline_slopes

    # --- Summary ---
    print(kalman_summary(kalman_state, top_n=10, stat_key="k"))
    _print_summary(results, total_projected, season)

    return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_actual(pitcher_name, market, actual_games, pitcher_logs):
    """
    Find a pitcher's actual stat value from today's games.

    Searches actual_games by matching pitcher_name (from projection) to
    the pitcher_name field in the game log entry.

    Parameters
    ----------
    pitcher_name : str
        Pitcher display name from the projection.
    market : str
        Market name (strikeouts).
    actual_games : dict
        {pitcher_id: game_log_dict} for today's games.
    pitcher_logs : dict
        Full pitcher logs (unused, kept for API compatibility).

    Returns
    -------
    float or None
        Actual stat value, or None if not found.
    """
    stat_key = STAT_KEYS.get(market)
    if not stat_key:
        return None

    from props_engine import _name_key
    target_nk = _name_key(pitcher_name)

    for pid, g in actual_games.items():
        name = g.get("pitcher_name") or g.get("player_name", "")
        if _name_key(name) == target_nk:
            val = g.get(stat_key)
            if val is not None:
                return float(val)
    return None


def _find_actual_game(pitcher_name, actual_games):
    """Return today's full game-log dict for a pitcher (for actual outs/pitches)."""
    from props_engine import _name_key
    target_nk = _name_key(pitcher_name)
    for pid, g in actual_games.items():
        name = g.get("pitcher_name") or g.get("player_name", "")
        if _name_key(name) == target_nk:
            return g
    return None


def _print_summary(results, total_projected, season):
    """Print formatted backfill summary per market + grand total."""
    print(f"\n{'='*60}")
    print(f"  MLB PITCHER PROPS BACKTEST SUMMARY (Kalman) -- {season}")
    print(f"{'='*60}")
    print(f"  Total pitcher-games projected: {total_projected}\n")

    grand_w = grand_l = 0
    grand_units = 0.0

    for market in results:
        data = results[market]
        projs = np.array(data["projections"])
        acts = np.array(data["actuals"])
        all_picks = data["picks"]
        # Actionable picks only — exclude watchlist (pick=PASS) from W-L/units.
        picks = [p for p in all_picks if p.get("pick") in ("OVER", "UNDER")]
        watch = [p for p in all_picks if p.get("pick") == "PASS"]

        if len(projs) == 0:
            continue

        mae = np.mean(np.abs(projs - acts))
        corr = np.corrcoef(projs, acts)[0, 1] if len(projs) > 1 else 0

        wins = sum(1 for p in picks if p["won"])
        losses = len(picks) - wins
        units = _calc_units(picks)
        pct = wins / max(1, wins + losses) * 100

        watch_w = sum(1 for p in watch if p["won"])
        watch_l = len(watch) - watch_w
        watch_pct = watch_w / max(1, watch_w + watch_l) * 100

        grand_w += wins
        grand_l += losses
        grand_units += units

        print(f"  {market:14s}: MAE={mae:6.1f}  corr={corr:.3f}  "
              f"picks={wins}W-{losses}L ({pct:.1f}%) "
              f"{'+'if units >= 0 else ''}{units:.1f}u  "
              f"watch={watch_w}W-{watch_l}L ({watch_pct:.1f}%)")

    print(f"\n  TOTAL: {grand_w}W-{grand_l}L "
          f"({grand_w / max(1, grand_w + grand_l) * 100:.1f}%) "
          f"{'+'if grand_units >= 0 else ''}{grand_units:.1f}u")


# ---------------------------------------------------------------------------
# Dashboard JSON
# ---------------------------------------------------------------------------

class _NumpyEncoder(json.JSONEncoder):
    """JSON encoder that handles numpy types."""
    def default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        return super().default(obj)


def write_dashboard_json(results, season):
    """
    Write graded backfill picks to dashboard JSON files.

    Merges with existing live picks (preserves picks from dates not
    in the backfill range).

    Output paths:
      - MLBstrikeouts/data/mlb-props.json
      - PythonDashboard/data/mlb-props.json
    """
    all_picks = []
    for market, data in results.items():
        for p in data["picks"]:
            entry = {
                "player": p["player"],
                "team": p.get("team", ""),
                "opp": p.get("opp", ""),
                "market": market,
                "proj": p["proj"],
                "std": p.get("std", 0),
                "line": p["line"],
                "pick": p["pick"],
                "edge": round(p["proj"] - p["line"], 1),
                "pCover": p["pCover"],
                "conf": p["conf"],
                "odds": p.get("odds"),
                "to_win_1u": p.get("to_win_1u"),
                "actual": p["actual"],
                "result": "WIN" if p["won"] else "LOSS",
                "date": p.get("date", ""),
                "proj_ip": p.get("proj_ip"),
                "proj_bf": p.get("proj_bf"),
                "proj_bf_capped": p.get("proj_bf_capped"),
                "proj_pc": p.get("proj_pc"),
                "opp_team_k_pct": p.get("opp_team_k_pct"),
                "lineup_k_pct": p.get("lineup_k_pct"),
                "actual_outs": p.get("actual_outs"),
                "actual_bf": p.get("actual_bf"),
                "actual_pitches": p.get("actual_pitches"),
            }
            if p.get("would_be_pick"):
                entry["would_be_pick"] = p["would_be_pick"]
            all_picks.append(entry)

    all_picks.sort(key=lambda x: (-(x.get("pCover") or 0), x.get("date", "")))

    # Summary excludes watchlist entries (pick=PASS) so units/W-L only
    # reflect actionable picks that would have actually been bet.
    actionable = [p for p in all_picks if p.get("pick") in ("OVER", "UNDER")]
    total_w = sum(1 for p in actionable if p["result"] == "WIN")
    total_l = sum(1 for p in actionable if p["result"] == "LOSS")
    units = _calc_units_from_dashboard(actionable)

    dashboard = {
        "sport": "mlb",
        "type": "pitcher_props",
        "season": str(season),
        "mode": "backfill",
        "model": "kalman_blend",
        "generated": datetime.now().isoformat(),
        "totalProjections": sum(len(d["projections"]) for d in results.values()),
        "totalPicks": len(actionable),
        "totalWatchlist": len(all_picks) - len(actionable),
        "props": all_picks,
        "summary": (
            f"{len(actionable)} actionable picks for {season}: "
            f"{total_w}W-{total_l}L "
            f"({total_w / max(1, total_w + total_l) * 100:.1f}%) "
            f"{'+'if units >= 0 else ''}{units:.1f}u "
            f"(+{len(all_picks) - len(actionable)} watchlist)"
        ),
    }

    script_dir = os.path.dirname(os.path.abspath(__file__))
    paths = [
        os.path.join(script_dir, "..", "data", "mlb-props.json"),
        os.path.join(script_dir, "..", "..", "PythonDashboard", "data", "mlb-props.json"),
    ]

    # Collect all backfill dates so we know which are "historical"
    backfill_dates = set(p.get("date", "") for p in all_picks)

    for path in paths:
        path = os.path.normpath(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)

        # Backfill fully overwrites — no preservation of live/today entries.
        # Previously we preserved out-of-range dates (e.g. tonight's projections),
        # but that risks carrying over corrupted entries from a poisoned daily run
        # (e.g. a stub-row contamination). The daily pipeline (run_daily) will
        # rewrite today's picks on the next run.
        merged_props = list(all_picks)
        # Stamp readVerdict ("TAKE" / "PASS") using the same walk-forward
        # logic as run_daily so the dashboard's Read filter has data after
        # a clean backfill. Without this, all picks lack readVerdict and the
        # "Recent Read Record" card shows empty until the next daily run.
        from run_daily import _stamp_read_verdicts
        _stamp_read_verdicts(merged_props)
        dashboard["props"] = merged_props
        # totalPicks reflects only actionable (non-PASS) entries; watchlist
        # is tracked separately so it doesn't inflate the headline pick count.
        merged_actionable = [p for p in merged_props if p.get("pick") in ("OVER", "UNDER")]
        merged_watch = [p for p in merged_props if p.get("pick") == "PASS"]
        dashboard["totalPicks"] = len(merged_actionable)
        dashboard["totalWatchlist"] = len(merged_watch)

        with open(path, "w") as f:
            json.dump(dashboard, f, indent=2, cls=_NumpyEncoder)
        takes = sum(1 for p in merged_props if p.get("readVerdict") == "TAKE")
        print(f"  Wrote {len(merged_actionable)} picks (+{len(merged_watch)} watch, {takes} Read TAKE) to {path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backtest MLB pitcher prop projections (Kalman)"
    )
    parser.add_argument("--season", type=int, default=None,
                        help="MLB season year (e.g. 2026)")
    parser.add_argument("--start-game", type=int, default=10,
                        help="Min game dates before projecting (default: 10)")
    parser.add_argument("--start-date", type=str, default=None,
                        help="Start projecting from this date (YYYY-MM-DD)")
    args = parser.parse_args()

    results = backfill(args.season, args.start_game, start_date=args.start_date)
    if results:
        write_dashboard_json(results, args.season or current_season())
