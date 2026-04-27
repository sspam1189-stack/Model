#!/usr/bin/env python3
"""
Sweep pCover thresholds, min edge, and direction to find optimal K pick criteria.
Runs backfill with no filters, captures ALL strikeouts projections, then sweeps.

Usage:
    cd MLBstrikeouts
    python -m scripts.sweep_thresholds
"""

import sys, os, json, math
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Override thresholds to capture everything
import defaults
defaults.MARKET_THRESHOLDS = {
    'strikeouts':   {'high': 0.50},
}
defaults.MIN_EDGE = {k: 0.0 for k in defaults.MIN_EDGE}
defaults.MIN_LINE = {k: 0.0 for k in defaults.MIN_LINE}
defaults.UNDER_ONLY_MARKETS = set()
defaults.DISABLED_MARKETS = set()

from sources.mlb_stats import (
    fetch_pitcher_game_logs, fetch_pitcher_advanced_stats,
    fetch_pitcher_sabermetrics, fetch_team_batting_stats,
    fetch_player_bat_sides, fetch_lineup_handedness,
    fetch_batter_k_rates, load_pitch_hands,
    fetch_pitcher_handedness_splits, CACHE_DIR, _load_cache,
)
from sources.weather import fetch_game_weather
from props_engine import organize_pitcher_logs, project_pitcher_props, STAT_KEYS
from pitcher_kalman import new_pitcher_kalman_state, batch_update_from_game_logs


def run_sweep(season=None):
    season = season or defaults.current_season()
    print(f"\n{'='*60}")
    print(f"  THRESHOLD SWEEP — Season {season}")
    print(f"{'='*60}")

    # Load data
    all_logs = fetch_pitcher_game_logs(season=season)
    for g in all_logs:
        if 'pitcher_id' not in g and 'player_id' in g: g['pitcher_id'] = g['player_id']
        if 'pitcher_name' not in g and 'player_name' in g: g['pitcher_name'] = g['player_name']
        if 'IP' not in g and 'ip' in g: g['IP'] = g['ip']
        if 'opp' not in g and 'opponent' in g: g['opp'] = g['opponent']

    pitcher_logs_all = organize_pitcher_logs(all_logs)
    team_batting = fetch_team_batting_stats(season=season)
    adv_stats = fetch_pitcher_advanced_stats(season=season)
    saber_stats = fetch_pitcher_sabermetrics(season=season)
    bat_sides = fetch_player_bat_sides(season=season)
    batter_k_rates = fetch_batter_k_rates(season=season)
    pitch_hands = load_pitch_hands(season=season)
    for pid_str, adv in adv_stats.items():
        try: adv['pitch_hand'] = pitch_hands.get(int(pid_str), 'R')
        except: pass

    bo_cache = _load_cache(CACHE_DIR / f'batting_orders_{season}.json', max_age_hours=None) or {}
    all_dates = sorted(set(g.get('game_date', '') for g in all_logs if g.get('game_date')))
    kalman_state = new_pitcher_kalman_state()

    all_projections = []

    print(f"  Walking forward through {len(all_dates)} dates...")

    for date_idx, game_date in enumerate(all_dates):
        today_logs = [g for g in all_logs if g.get('game_date', '') == game_date and g.get('IP', 0) >= 3.0]

        if date_idx < 10:
            if today_logs: batch_update_from_game_logs(kalman_state, today_logs)
            continue

        prior_logs = {}
        actual_games = {}
        for pid, games in pitcher_logs_all.items():
            prior = [g for g in games if g.get('game_date', '') < game_date]
            today = [g for g in games if g.get('game_date', '') == game_date]
            if prior: prior_logs[pid] = prior
            if today: actual_games[pid] = today[0]

        if not prior_logs or not actual_games:
            if today_logs: batch_update_from_game_logs(kalman_state, today_logs)
            continue

        # Odds
        real_lines = None
        try:
            from sources.odds_theoddsapi import _props_cache_path, _load_cache as _lc2
            cp = _props_cache_path(game_date.replace('-', ''))
            real_lines = _lc2(cp, max_age_hours=None)
        except: pass

        # Lineup handedness
        lineup_hand = fetch_lineup_handedness(game_date, bat_sides=bat_sides, season=season)
        date_tb = dict(team_batting)
        for abbr, hd in lineup_hand.items():
            if abbr in date_tb: date_tb[abbr] = {**date_tb[abbr], 'PCT_LHB': hd['PCT_LHB']}
            else: date_tb[abbr] = {'PCT_LHB': hd['PCT_LHB']}

        weather_data = fetch_game_weather(game_date)

        date_lineup_data = {}
        for abbr, order in bo_cache.get(game_date, {}).items():
            date_lineup_data[abbr] = {'player_ids': order}

        # Probable pitchers from actual starters
        games_by_id = defaultdict(list)
        for g in today_logs:
            gid = g.get('game_id', '')
            if gid: games_by_id[gid].append(g)
        date_probable = []
        for gid, starters in games_by_id.items():
            gm = {'game_id': gid}
            for g in starters:
                pid = g.get('pitcher_id') or g.get('player_id')
                pname = g.get('pitcher_name') or g.get('player_name', '')
                team = g.get('team', '')
                opp = g.get('opp', g.get('opponent', ''))
                if g.get('is_home', False):
                    gm.update({'home_team': team, 'away_team': opp, 'home_pitcher_id': pid, 'home_pitcher_name': pname})
                else:
                    gm.update({'away_team': team, 'home_team': opp, 'away_pitcher_id': pid, 'away_pitcher_name': pname})
            date_probable.append(gm)

        date_splits = {}
        for g in today_logs:
            pid = g.get('pitcher_id') or g.get('player_id')
            if pid and str(pid) not in date_splits:
                s = fetch_pitcher_handedness_splits(pid, season=season)
                if s: date_splits[str(pid)] = s

        projs = project_pitcher_props(
            prior_logs, team_batting_stats=date_tb, prop_lines=real_lines,
            kalman_state=kalman_state, pitcher_adv_stats=adv_stats,
            pitcher_sabermetrics=saber_stats, pitcher_splits=date_splits,
            probable_pitchers=date_probable, weather_by_game=weather_data,
            batter_k_rates=batter_k_rates, lineup_data=date_lineup_data,
        )

        # Grade
        for proj in projs:
            if proj.get('line') is None: continue
            player = proj['player']
            market = proj['market']
            stat_key = STAT_KEYS.get(market)
            if not stat_key: continue

            actual_val = None
            for pid, ag in actual_games.items():
                pname = ag.get('pitcher_name', ag.get('player_name', ''))
                if pname and pname.lower() == player.lower():
                    actual_val = ag.get(stat_key, None)
                    break

            if actual_val is not None:
                proj['actual'] = actual_val
                proj['date'] = game_date
                if proj['pick'] == 'OVER':
                    proj['won'] = actual_val > proj['line']
                elif proj['pick'] == 'UNDER':
                    proj['won'] = actual_val < proj['line']
                else:
                    proj['won'] = None
                all_projections.append(proj)

        if today_logs: batch_update_from_game_logs(kalman_state, today_logs)

    # Sweep
    graded = [p for p in all_projections if p.get('won') is not None]
    print(f"\n  Total graded projections: {len(graded)}")

    for market in ['strikeouts']:
        mkt = [p for p in graded if p['market'] == market]
        print(f"\n{'='*65}")
        print(f"  {market.upper()} — {len(mkt)} graded projections")
        print(f"{'='*65}")
        print(f"  {'pCov':>5s} {'Edge':>5s} {'Dir':>6s} {'N':>4s} {'W':>3s} {'L':>3s} {'Win%':>6s} {'Units':>7s} {'ROI':>7s}")
        print(f"  {'-'*50}")

        best = {'pct': 0, 'line': ''}
        for direction in ['UNDER', 'OVER', 'BOTH']:
            for thresh in [0.55, 0.58, 0.60, 0.62, 0.65, 0.68, 0.70, 0.72, 0.75, 0.80]:
                for min_edge in [0.0, 0.5, 1.0, 1.5]:
                    filtered = []
                    for p in mkt:
                        pc = p.get('pCover', 0) or 0
                        edge = abs(p.get('edge', 0) or 0)
                        pk = p['pick']
                        if pk == 'PASS': continue
                        if pc < thresh: continue
                        if edge < min_edge: continue
                        if direction != 'BOTH' and pk != direction: continue
                        filtered.append(p)

                    if len(filtered) < 3: continue
                    w = sum(1 for p in filtered if p['won'])
                    l = len(filtered) - w
                    pct = w / (w + l) * 100
                    def _u(p):
                        o = p.get('odds')
                        if o is None: return 0.0
                        o = int(o)
                        won = p['won']
                        if o > 0:
                            return o/100.0 if won else -1.0
                        return 1.0 if won else -abs(o)/100.0
                    units = sum(_u(p) for p in filtered)
                    risked = sum((1.0 if (p.get('odds') or 0) > 0 else abs(int(p.get('odds') or -110))/100.0) for p in filtered)
                    roi = (units / risked * 100) if risked else 0
                    flag = ' ***' if pct >= 65 else (' **' if pct >= 60 else (' *' if pct >= 55 else ''))
                    if units > 0:
                        print(f"  {thresh:5.2f} {min_edge:5.1f} {direction:>6s} {len(filtered):4d} {w:3d} {l:3d} {pct:5.1f}% {units:+6.1f}u {roi:+6.1f}%{flag}")
                    if pct > best['pct'] and len(filtered) >= 5:
                        best = {'pct': pct, 'line': f"{thresh}/{min_edge}/{direction}", 'n': len(filtered), 'units': units}

        print(f"\n  Best: {best['line']} -> {best['pct']:.1f}% ({best.get('n',0)} picks, {best.get('units',0):+.1f}u)")


if __name__ == "__main__":
    run_sweep()
