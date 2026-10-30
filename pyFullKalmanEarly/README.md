# NBA Full KalmanEarly

A fourth NBA full-season model, started 2026-10-30. A copy of `pyFull` (same
formula, Kalman on, same thresholds) that projects from game 1, so Kalman
learns and counts from game 1:

| | pyFull | KalmanEarly |
|---|---|---|
| Projections start | 20+ teams at 5 GP | **20+ teams at 1 GP** |
| Kalman learns from | first projected game (game 5) | **game 1** |
| Kalman offsets applied | from first projection (game 5) | **from game 1** |
| Official picks | 20+ teams at 10 GP | 20+ teams at 10 GP |

Clean 2025-26 backfill (`replay_season.py`, picks from Nov 10, 58%+ 1u / 65%+ 2u):
pyFull 11.84 margin error, 321-305 -23.4u; KalmanEarly 11.84, 312-299 -26.4u
(with offsets held until 10 GP, as first set up on 2026-10-30: 11.85, 308-298 -32.0u).

Its Kalman state was seeded on 2026-10-30 from this season's preview projections
(Oct 23-29, stats through the day before each game), so it starts having learned
from game 1 like the design intends; those offsets apply from today's run.

Own state: `data/history.json`, `data/kalman_state.json`, recorded inputs in
`data/run_inputs/nba-kalmanearly/`, dashboard feed
`PythonDashboard/data/fullseason-kalmanearly.json` (tab "NBA Full KalmanEarly").
Does not build NBA Systems and sends no email.
