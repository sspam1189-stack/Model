# NBA KalmanOff + Last Season (pyFullPrior)

A copy of `pyFullKalmanOff` (Kalman offsets not applied) with one addition, in
`scripts/sources/prior.py`:

**Last season as the starting point.** Each team's season stats are blended
with last season's final regular-season numbers. The prior counts as
`PRIOR_GAMES` (10) games and is regressed toward the league average by
`PRIOR_REGRESS` (0.8), so it fades as games are played.

Until 2026-11-13 this model also carried "injuries from game 1". The two
additions were split so each keeps its own record: the injury addition now runs
alone as `pyFullInj`. The switches are `PRIOR_TEAM` / `PRIOR_INJURIES` in
`scripts/defaults.py`.

Walk-forward Oct 23 - Nov 12, 2026 (58% / 65% rule): KalmanOff 59-45 (+22.5u),
injuries only 59-44 (+21.5u), last season only 42-28 (+12.0u).

Static inputs: `data/priors/nba/team_2025-26.json` and
`data/priors/nba/players_2025-26.json`. Rebuild both for the next season from
the final regular-season stats cache and the props player cache.

Own data: `pyFullPrior/data/`, recorded inputs `data/run_inputs/nba-prior/`,
dashboard feed `PythonDashboard/data/fullseason-prior.json` (tab "NBA KO +
Last Season", and the same column on NBA Picks). Runs in `py-run-daily.yml`, step
"Run daily (NBA Full Prior)".

## What was checked before launch, with both additions on (2026-27, through Nov 11)

- Blending the prior into KalmanOff's saved inputs (154 games): average margin
  miss 12.82 -> about 11.2 (Vegas 10.85). Nearly all of the gain is in the first
  week (14.8 -> about 10.6); from Oct 30 the two are level.
- Full replay through `run_daily`, Oct 30 - Nov 11 (89 games): margin miss 11.76
  vs KalmanOff 11.77; plays 23-21 (-2.2u) vs KalmanOff 34-28 (+6.9u). It sits
  closer to the line (average gap 2.9 vs 4.8 points), so it makes fewer plays.
- No against-the-spread edge has been shown. This model is more accurate early
  in a season; whether that turns into wins is what running it live will tell.
