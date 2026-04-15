# NFL Props Engine Rewrite: Plays → Volume → Efficiency → Output

## Summary

Replace the current raw-rolling-average NFL props engine with a decomposed
projection pipeline: team plays → pass/run split → player share → player
efficiency rate → stat projection. Same core principle as the NBA props fix
(rate × opportunity), adapted for NFL's team-level play allocation structure.

## Problem

Current `props_engine.py` averages raw box score stats (e.g., last 6 games of
passing yards) and applies a crude EPA-based opponent adjustment. This fails
because:

- Raw averages don't adapt when game environment changes (pace, game script)
- No separation between volume (attempts, targets) and efficiency (YPA, catch%)
- No team-level context (implied total, spread, pass rate)
- Market-specific filters/thresholds are bandaids hiding bad projections
- 17-game season means small samples amplify noise

## Design

### Pipeline Overview

```
Vegas Total + Spread + Team Pace (from PBP)
  → Stage 1: Team Game Environment
      → projected team plays, dropbacks, rush attempts
  → Stage 2: Player Volume Allocation
      → target share, rush share (Kalman-filtered)
  → Stage 3: Player Efficiency Rates
      → YPA, catch%, YPC, TD rate (Kalman-filtered)
  → Stage 4: Opponent Adjustment
      → multiplicative adjustment on rates using EPA
  → Stage 5: Projection Assembly
      → stat = volume × rate, variance = rate_std × volume
  → Stage 6: Pick Generation
      → t-distribution p(cover), uniform 0.80 threshold, no filters
```

### Stage 1 — Team Game Environment

Inputs:
- Vegas total and spread (from `data/odds/nfl_odds_SEASON_WWEEK.json`)
- Team pace: plays per game from PBP (exponentially weighted)
- Team pass rate over expectation (PROE) from PBP

Computation:
```
implied_team_total = (total / 2) + (spread / 2)   # for home team
expected_plays = (team_pace + opp_pace) / 2
  adjusted by: (implied_team_total / league_avg_total - 1) * scale

pass_pct = team_base_pass_rate
  adjusted by: game_script_factor from spread
    (trailing → +pass%, leading → -pass%)

dropbacks = expected_plays × pass_pct
rush_attempts = expected_plays × (1 - pass_pct)
```

### Stage 2 — Player Volume Allocation

From PBP game logs, compute per-player shares (exponentially weighted, decay=0.88):
- **QB**: gets all team dropbacks
- **RB**: `rush_share = player_rush_att / team_rush_att` per game
- **WR/TE**: `target_share = player_targets / team_dropbacks` per game

Kalman-filter these shares to detect role changes (injury, trade, depth chart).

Output: player-level projected attempts/targets for this game.

### Stage 3 — Player Efficiency Rates

Per-player rates from PBP (exponentially weighted, decay=0.88):
- **QB**: yards per attempt (YPA), completion %, TD rate per dropback
- **RB**: yards per carry (YPC)
- **WR/TE**: catch rate (receptions / targets), yards per reception (YPR)

Kalman-filter these rates (smoothed baseline + uncertainty).

### Stage 4 — Opponent Adjustment

Multiplicative adjustment on rates using opponent EPA:
```
opp_factor = 1 + (opp_def_epa - league_avg_def_epa) × weight
adjusted_rate = player_rate × opp_factor
```

Weight: 0.20 for all markets (uniform, same as NBA).

Pass defense EPA adjusts: YPA, completion%, TD rate, catch rate, YPR
Rush defense EPA adjusts: YPC

### Stage 5 — Projection Assembly

```
pass_yds   = dropbacks × YPA
pass_tds   = dropbacks × TD_rate
rush_att   = team_rush_att × rush_share
rush_yds   = rush_att × YPC
receptions = dropbacks × target_share × catch_rate
rec_yds    = receptions × YPR
```

Variance (rate-based):
```
stat_std = rate_std × volume × VAR_MULT
```

Where `rate_std` is the weighted std of per-play rates, and `volume` is the
projected opportunity count. Variance scales honestly with volume.

### Stage 6 — Pick Generation

- Student's t-distribution (df=4, heavier tails for NFL's higher variance)
- Uniform 0.80 confidence threshold across all markets
- No directional filters (OVER and UNDER both allowed)
- No edge windows (sanity bounds only: min 0.5, max 999)
- No market-specific threshold hacks

### Per-Player Kalman Filter

New file: `player_kalman_nfl.py`

Tracks per player:
- **Shares**: target_share, rush_share
- **Rates**: YPA, completion%, TD_rate, YPC, catch_rate, YPR

Hyperparameters (NFL-specific, higher noise than NBA):
- `gameDrift`: 0.5 (higher than NBA's 0.3 — weekly cadence, more regime changes)
- `obsNoise`: per-stat (YPA ~4.0, YPC ~3.0, catch_rate ~0.02, etc.)
- `minGamesForKalman`: 3 (NFL has fewer games, can't wait 5)
- `kalmanBlend`: 0.5 (50/50 — less Kalman trust with small samples)

Season reset: Kalman state starts fresh each season. No carryover.

### Markets

All active, no filters:
- `pass_yds` — dropbacks × YPA
- `pass_tds` — dropbacks × TD_rate
- `rush_att` — team_rush × rush_share
- `rush_yds` — rush_att × YPC
- `receptions` — dropbacks × target_share × catch_rate
- `rec_yds` — receptions × YPR

### Data Sources (all existing, no new fetches needed)

| Source | Used For |
|--------|----------|
| PBP cache (parquet) | Player game logs, team pace, pass rate, shares, rates |
| Odds cache (JSON) | Vegas total, spread per game |
| Props cache (JSON) | Market lines for backtesting (Weeks 4-22, 2023-2025) |
| Team stats (EPA) | Opponent defense adjustment |

### Files Changed

| File | Action | Scope |
|------|--------|-------|
| `pyNFL/scripts/props_engine.py` | Full rewrite | Plays→Volume→Efficiency→Output pipeline |
| `pyNFL/scripts/player_kalman_nfl.py` | New file | Per-player Kalman for shares + rates |
| `pyNFL/scripts/defaults.py` | Add section | Props-specific constants (leave spreads/totals untouched) |
| `pyNFL/scripts/props_backtest.py` | Minor update | Pass odds data to engine for team environment |

### Files NOT Changed

- `model_engine.py` — spreads/totals model untouched
- `kalman_state.py` — team-level Kalman untouched
- `sources/*` — all data sources reused as-is
- `run_weekly.py` — orchestration stays the same (calls props_engine)
- `calibration.py`, `self_tune.py` — spreads/totals calibration untouched

### Backtest Plan

Run walk-forward backtest across 2023-2025 seasons (Weeks 4-22 per season,
~57 weeks total). Each week: project using prior weeks only, compare to actuals
from PBP, grade against cached prop lines.

Target: 58%+ win rate at 0.80 threshold with 3+ picks/week.
