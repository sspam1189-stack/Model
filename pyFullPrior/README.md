# NBA Full Prior

A fifth NBA full-season model, started 2026-11-12. It is a copy of
`pyFullKalmanOff` (Kalman offsets not applied) with two additions, both in
`scripts/sources/prior.py`:

1. **Last season as the starting point.** Each team's season stats are blended
   with last season's final regular-season numbers. The prior counts as
   `PRIOR_GAMES` (10) games and is regressed toward the league average by
   `PRIOR_REGRESS` (0.8), so it fades as games are played.
2. **Injuries from game 1.** Rosters and player ratings fall back to last
   season's until this season's exist, and each absence is scaled by how much
   of the team's blended rating actually includes that player.

Static inputs: `data/priors/nba/team_2025-26.json` and
`data/priors/nba/players_2025-26.json`. Rebuild both for the next season from
the final regular-season stats cache and the props player cache.

Own data: `pyFullPrior/data/`, recorded inputs `data/run_inputs/nba-prior/`,
dashboard feed `PythonDashboard/data/fullseason-prior.json` (tab "NBA Full
Prior", and the "Prior" column on NBA Picks). Runs in `py-run-daily.yml`, step
"Run daily (NBA Full Prior)".

## What was checked before launch (2026-27, through Nov 11)

- Blending the prior into KalmanOff's saved inputs (154 games): average margin
  miss 12.82 -> about 11.2 (Vegas 10.85). Nearly all of the gain is in the first
  week (14.8 -> about 10.6); from Oct 30 the two are level.
- Full replay through `run_daily`, Oct 30 - Nov 11 (89 games): margin miss 11.76
  vs KalmanOff 11.77; plays 23-21 (-2.2u) vs KalmanOff 34-28 (+6.9u). It sits
  closer to the line (average gap 2.9 vs 4.8 points), so it makes fewer plays.
- No against-the-spread edge has been shown. This model is more accurate early
  in a season; whether that turns into wins is what running it live will tell.
