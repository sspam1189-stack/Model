# WNBA Player Props + Full-Season Models — Design

**Date:** 2026-07-01
**Status:** Approved (design), pending implementation plan
**Author:** Claude + Henry

## Goal

Port the two existing NBA betting models to the WNBA, as exact-methodology
clones:

- `pyNBAPROPS/` (player props: points, rebounds, assists, threes, PRA) →
  **`pyWNBAPROPS/`**
- `pyFull/` (full-season spreads + totals) → **`pyWNBAFull/`**

Same pipeline, same Kalman methodology, same odds sources, same dashboard +
GitHub Actions automation shape — retuned for WNBA.

## Key Decisions (locked)

1. **Fork, don't parameterize.** Copy each NBA system into a new folder. NBA
   code stays byte-for-byte untouched. Each league tunes independently. (Matches
   the "keep whiff byte-for-byte unchanged" precedent from the MLB CSW variant.)
2. **Two parallel agents.** One builds `pyWNBAPROPS`, the other builds
   `pyWNBAFull`. Separate folders → no file conflicts.
3. **Backtest-calibrate before go-live.** Pull WNBA season history, run the
   existing sweep/calibration scripts to derive WNBA-specific constants, then
   ship. Do not ship NBA-tuned numbers as live values.

## Data Layer Verification (done 2026-07-01)

`nba_api` 1.11.4 returns WNBA data with the same schema as NBA. Confirmed
working with `LeagueID='10'` and plain-year season strings:

| Endpoint | NBA usage | WNBA result |
|---|---|---|
| `LeagueGameLog` (player) | basic box scores | ✓ 2025: 5,407 rows, 2026: 2,861 rows, identical columns |
| `LeagueDashPlayerStats` (Advanced) | USG%, TS%, PACE, OFF/DEF_RATING | ✓ 2026: 208 players, all fields present |
| `LeagueDashTeamStats` (Opponent) | team defense OPP_* | ✓ 2026: team rows, all OPP_* columns present |

**Conclusion:** the entire `nba_api` data layer ports with only a league-id and
season-string change. Odds layer needs only a sport-key swap.

## Architecture

Two standalone forks under the repo root, mirroring the existing NBA folders:

```
pyWNBAPROPS/          (clone of pyNBAPROPS/)
  scripts/
    run_daily.py
    defaults.py         # WNBA-tuned constants (see Calibration)
    props_engine.py
    player_kalman.py
    calibrate_threshold.py, fit_calibration.py, sweeps...
    sources/
      wnba_player_stats.py   # was nba_player_stats.py
      odds_theoddsapi.py     # sport key -> basketball_wnba
      odds_fanduel.py        # -> WNBA
      game_context.py, season_type.py
  data/
    kalman_state.json
    wnba-props.json
    player_cache/wnba/

pyWNBAFull/           (clone of pyFull/)
  scripts/... (same swap pattern)
  data/
    wnba-full.json
```

NBA folders (`pyNBAPROPS/`, `pyFull/`) are not modified.

## Data-Layer Changes (identical pattern in both forks)

1. **nba_api calls:** add `league_id='10'` (or `league_id_nullable='10'`) to
   every `LeagueGameLog` / `LeagueDashPlayerStats` / `LeagueDashTeamStats` /
   related call.
2. **Season strings:** WNBA seasons are a single calendar year (`'2026'`), not
   `'2025-26'`. Rewrite `current_season()` to return the current year and drop
   the October-crossing logic (`start_year = year if month >= 10 ...`). WNBA
   runs ~May–September, so the season year is simply the current year during the
   season.
3. **Caches:** repoint `PLAYER_CACHE_DIR` to `data/player_cache/wnba/`.
4. **Odds sources:** swap The Odds API sport key `basketball_nba` →
   `basketball_wnba`, and the FanDuel league identifier to WNBA, in both
   `odds_theoddsapi.py` and `odds_fanduel.py`. Prop market keys
   (`player_points`, `player_rebounds`, ...) are unchanged on The Odds API.

## WNBA-Specific Realities (absorbed by calibration)

- **40-minute games** (NBA is 48). Per-game volumes are lower; minute-based
  thresholds (`MIN_MINUTES`, `MIN_LINE`) and per-36 anchors need re-derivation.
- **~44-game season, ~15 teams (2026, post-expansion).** Much less data per player than NBA's 82.
  `MIN_GAMES` and Kalman process-noise/drift likely need loosening so real
  players aren't filtered out early-season. Start from NBA priors, retune.
- **Markets offered vs. shipped.** Props pipeline computes points, rebounds,
  assists, threes, PRA; full pipeline computes spread + total. Which
  markets/directions actually go live (`DISABLED_MARKETS`, directional filters)
  is decided by the WNBA backtest — NBA conclusions (e.g. "rebounds UNDER only")
  are NOT inherited.

## Calibration Plan (backtest-first)

For each fork:

1. Pull 2024 + 2025 full WNBA seasons via the WNBA data layer as backtest data
   (2025 alone = 5,407 player-games, confirmed available). Use 2026-to-date as a
   holdout / live-forward check.
2. Run the existing calibration + sweep scripts (`calibrate_threshold.py`,
   `fit_calibration.py`, `sweep_*.py`) against WNBA data to derive WNBA-specific:
   - `MARKET_THRESHOLDS` (pCover cutoffs)
   - `VAR_MULT` (per-market variance multipliers)
   - `MIN_EDGE` / `MAX_EDGE` / `MIN_LINE` bounds
   - `DISABLED_MARKETS` and directional filters (data-driven, not copied)
   - Kalman init/drift params where the short season demands it
3. Ship only markets/directions that show real WNBA edge in backtest, with the
   evidence recorded in `defaults.py` comments (same style as the NBA files).

## Dashboard + Automation

- **Outputs:** `pyWNBAPROPS/data/wnba-props.json` and
  `pyWNBAFull/data/wnba-full.json`, mirroring the NBA JSON shape (so grading /
  backfill logic ports unchanged). Also written to the `PythonDashboard/data/`
  location like the NBA files.
- **Dashboard:** new WNBA tabs in PythonDashboard, cloned from the NBA tabs.
- **GitHub Actions:** new workflows cloned from `.github/workflows/py-run-daily.yml`
  (e.g. `wnba-props-daily.yml`, `wnba-full-daily.yml`), scheduled for WNBA game
  days (May–Sept). Follow the existing daily-commit `[skip ci]` convention.

## Verification (per agent, gate for "done")

Following the project's preview/verify conventions:

1. Run the pipeline end-to-end on a **past WNBA date** — confirm picks generate
   and grade correctly against actual box scores.
2. Run for **today** — confirm today's lines fetch, projections produce, picks
   write to the dashboard JSON.
3. Confirm the daily workflow file is valid and the dashboard tab renders.

## Out of Scope

- Modifying any NBA / MLB / NFL / NCAA code.
- New prop markets not present in the NBA model.
- Live deployment scheduling changes beyond adding the two WNBA workflows.

## Build Split (two parallel agents)

- **Agent A — Props:** fork `pyNBAPROPS` → `pyWNBAPROPS`, swap data layer,
  backtest-calibrate props constants, wire dashboard JSON + workflow, verify.
- **Agent B — Full:** fork `pyFull` → `pyWNBAFull`, swap data layer,
  backtest-calibrate spread/total constants, wire dashboard JSON + workflow,
  verify.

Shared conventions (league_id='10', year seasons, `basketball_wnba`) are fixed
in this spec so both agents apply them identically.
