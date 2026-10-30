# NBA Full KalmanOff

A second NBA full-season model, started 2026-10-22. It is a copy of `pyFull`
with three settings changed, so the original model is untouched and both run
side by side every day (`py-run-daily.yml`, step "Run daily (NBA Full
KalmanOff)", right after pyFull).

| Setting | pyFull | KalmanOff |
|---|---|---|
| `KALMAN_SCALE` (share of Kalman team offsets in projections) | 1.0 | **0.0** |
| `recentWeight` (last-10 form vs season) | 0.87 | **0.35** |
| `HCA_CLAMP_LO` (lowest home court the tuner may set) | 1.5 | 1.5 (was **0.0** until 2026-10-30) |

Chosen on the clean 2025-26 backfill through `run_daily` (`replay_season.py`:
cached stats, lines and injuries, one run per date):

| | Margin error | Home bias | Regular season | Full season |
|---|---|---|---|---|
| pyFull | 12.17 | +0.80 | 176-164, -4.4u | 188-176, -5.6u |
| KalmanOff | 11.62 | +0.10 | 95-82, +4.8u | 107-91, +6.9u |
| Vegas line | 11.19 | +0.38 | | |

Own state: `data/history.json`, `data/kalman_state.json`, recorded inputs in
`data/run_inputs/nba-kalmanoff/`, dashboard feed
`PythonDashboard/data/fullseason-kalmanoff.json` (tab "NBA Full KalmanOff").
Shares the stats / odds / injury / ESPN caches with pyFull. Does not build NBA
Systems (pyFull does) and sends no email.

The scripts are a copy of `pyFull/scripts`: a fix to one usually belongs in
both. `python replay_season.py --season 2025-26` backfills this model.
