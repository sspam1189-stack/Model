# NBA KalmanOff + Injuries (pyFullInj)

A copy of `pyFullKalmanOff` (Kalman offsets not applied) with one addition, in
`scripts/sources/prior.py` (`adjust_team_stats_prior`):

**Injuries from game 1.** The stock lineup adjustment needs this season's
player ratings, which only exist once a player has 10 games. Here rosters and
player ratings fall back to last season's until this season's exist. Team
ratings are this season's only (`PRIOR_TEAM` is off), so every absence of a
known player counts in full unless it is a long-term one.

Split out of `pyFullPrior` on 2026-11-13 so each addition keeps its own record.

Static input: `data/priors/nba/players_2025-26.json`.

Own data: `pyFullInj/data/`, recorded inputs `data/run_inputs/nba-inj/`,
dashboard feed `PythonDashboard/data/fullseason-inj.json` (tab "NBA KO +
Injuries", and the same column on NBA Picks). Runs in `py-run-daily.yml`, step
"Run daily (NBA Full Inj)".

Walk-forward Oct 23 - Nov 12, 2026 (58% / 65% rule): 59-44 (+21.5u), against
KalmanOff's 59-45 (+22.5u): over that span it made nearly the same picks.
