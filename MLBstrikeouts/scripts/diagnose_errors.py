#!/usr/bin/env python3
"""Diagnose projection errors — find root causes, not bandaids."""

import sys, os, json, math
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import defaults
defaults.MARKET_THRESHOLDS = {
    'strikeouts': {'high': 0.50}, 'outs': {'high': 0.50, 'high_under': 0.50},
    'hits_allowed': {'high': 0.50, 'high_under': 0.50},
    'walks': {'high': 0.50}, 'game_hits': {'high': 0.50},
}
defaults.MIN_EDGE = {k: 0.0 for k in defaults.MIN_EDGE}
defaults.MIN_LINE = {k: 0.0 for k in defaults.MIN_LINE}
defaults.UNDER_ONLY_MARKETS = set()
defaults.DISABLED_MARKETS = {"walks"}

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


def run():
    season = 2026
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

    all_projs = []

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

        real_lines = None
        try:
            from sources.odds_theoddsapi import _props_cache_path, _load_cache as _lc2
            real_lines = _lc2(_props_cache_path(game_date.replace('-', '')), max_age_hours=None)
        except: pass

        lineup_hand = fetch_lineup_handedness(game_date, bat_sides=bat_sides, season=season)
        date_tb = dict(team_batting)
        for abbr, hd in lineup_hand.items():
            if abbr in date_tb: date_tb[abbr] = {**date_tb[abbr], 'PCT_LHB': hd['PCT_LHB']}

        weather_data = fetch_game_weather(game_date)
        date_lineup_data = {abbr: {'player_ids': order} for abbr, order in bo_cache.get(game_date, {}).items()}

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
                team, opp = g.get('team', ''), g.get('opp', g.get('opponent', ''))
                if g.get('is_home', False):
                    gm.update({'home_team': team, 'away_team': opp,
                               'home_pitcher_id': pid, 'home_pitcher_name': pname})
                else:
                    gm.update({'away_team': team, 'home_team': opp,
                               'away_pitcher_id': pid, 'away_pitcher_name': pname})
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

        for proj in projs:
            if proj.get('line') is None:
                continue
            player, market = proj['player'], proj['market']
            stat_key = STAT_KEYS.get(market)
            if not stat_key:
                continue
            for pid, ag in actual_games.items():
                pname = ag.get('pitcher_name', ag.get('player_name', ''))
                if pname and pname.lower() == player.lower():
                    proj['actual'] = ag.get(stat_key)
                    proj['date'] = game_date
                    n = len([g for g in pitcher_logs_all.get(pid, [])
                             if g.get('game_date', '') < game_date])
                    proj['n_starts'] = n
                    proj['proj_ip'] = ag.get('IP', ag.get('ip', 0))
                    all_projs.append(proj)
                    break

        if today_logs:
            batch_update_from_game_logs(kalman_state, today_logs)

    # === ANALYSIS ===
    print(f"\nTotal graded projections: {len(all_projs)}")

    for market in ['strikeouts', 'outs', 'hits_allowed']:
        mkt = [p for p in all_projs if p['market'] == market and p.get('actual') is not None]
        if not mkt:
            continue

        biases = [p['proj'] - p['actual'] for p in mkt]
        abs_errors = [abs(b) for b in biases]

        print(f"\n{'='*70}")
        print(f"  {market.upper()} - {len(mkt)} projections")
        print(f"{'='*70}")
        print(f"  Avg bias: {sum(biases)/len(biases):+.2f} (+ = overestimate)")
        print(f"  Avg |error|: {sum(abs_errors)/len(abs_errors):.2f}")
        print(f"  Median |error|: {sorted(abs_errors)[len(abs_errors)//2]:.1f}")

        # Bias by # prior starts
        print(f"\n  Bias by prior starts:")
        by_starts = defaultdict(list)
        for p in mkt:
            n = p.get('n_starts', 0)
            bucket = '2-3' if n <= 3 else ('4-5' if n <= 5 else '6+')
            by_starts[bucket].append(p['proj'] - p['actual'])
        for bucket in ['2-3', '4-5', '6+']:
            vals = by_starts.get(bucket, [])
            if vals:
                print(f"    {bucket} starts: bias={sum(vals)/len(vals):+.2f}, MAE={sum(abs(v) for v in vals)/len(vals):.2f}, n={len(vals)}")

        # Bias by direction
        print(f"\n  By direction:")
        for direction in ['OVER', 'UNDER', 'PASS']:
            subset = [p for p in mkt if p['pick'] == direction]
            if not subset:
                continue
            dir_biases = [p['proj'] - p['actual'] for p in subset]
            if direction != 'PASS':
                won = sum(1 for p in subset
                          if (p['actual'] > p['line'] if direction == 'OVER'
                              else p['actual'] < p['line']))
                pct = won / len(subset) * 100
                print(f"    {direction}: bias={sum(dir_biases)/len(dir_biases):+.2f}, "
                      f"n={len(subset)}, won={won}/{len(subset)} ({pct:.0f}%)")
            else:
                print(f"    {direction}: bias={sum(dir_biases)/len(dir_biases):+.2f}, n={len(subset)}")

        # Calibration
        print(f"\n  Calibration (pCover vs actual):")
        by_pcov = defaultdict(list)
        for p in mkt:
            if p['pick'] == 'PASS':
                continue
            pc = p.get('pCover', 0) or 0
            bucket = f"{int(pc * 20) * 5}-{int(pc * 20) * 5 + 5}%"
            won = ((p['actual'] > p['line']) if p['pick'] == 'OVER'
                   else (p['actual'] < p['line']))
            by_pcov[bucket].append(1 if won else 0)
        for bucket in sorted(by_pcov.keys()):
            vals = by_pcov[bucket]
            if len(vals) >= 3:
                print(f"    pCov {bucket}: actual={sum(vals)/len(vals)*100:.0f}%, n={len(vals)}")

        # Biggest misses
        print(f"\n  Biggest misses (|error| > 3):")
        big = sorted(mkt, key=lambda p: -abs(p['proj'] - p['actual']))
        for p in big[:8]:
            err = p['proj'] - p['actual']
            if abs(err) < 3:
                break
            print(f"    {p['date']} {p['player'][:22]:22s} "
                  f"proj={p['proj']:.1f} actual={p['actual']} err={err:+.1f} "
                  f"starts={p.get('n_starts', '?')} line={p['line']}")

        # Proj vs line vs actual: is the line more accurate than our model?
        line_better = 0
        model_better = 0
        for p in mkt:
            line_err = abs(p['line'] - p['actual'])
            model_err = abs(p['proj'] - p['actual'])
            if model_err < line_err:
                model_better += 1
            elif line_err < model_err:
                line_better += 1
        print(f"\n  Model vs Line accuracy:")
        print(f"    Model closer to actual: {model_better}/{len(mkt)} ({model_better/len(mkt)*100:.0f}%)")
        print(f"    Line closer to actual:  {line_better}/{len(mkt)} ({line_better/len(mkt)*100:.0f}%)")


if __name__ == "__main__":
    run()
