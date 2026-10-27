# NBA Full dNET

A third NBA full-season model, started 2026-10-27. It is a copy of `pyFull`
(Kalman on, same settings) with one change: the projection formula.

| | pyFull | dNET |
|---|---|---|
| Margin | wOFF x team OFF + wDEF x (opp DEF - league DEF), per team | **wNET x (home NET - away NET) x pace + HCA** |
| Tuned weights | wTS, wTO, wORR, wDEF, hca | **wDEF (as wNET), hca** |
| wDEF start | 0.4 | **1.0** |

Clean 2025-26 backfill through `run_daily` (`replay_season.py`; picks from Nov 10):

| | Margin error | 65%+ plays |
|---|---|---|
| pyFull | 11.84 | 155-147, -6.7u |
| dNET | 11.86 | 171-141, +15.9u |

Own state: `data/history.json`, `data/kalman_state.json`, recorded inputs in
`data/run_inputs/nba-dnet/`, dashboard feed `PythonDashboard/data/fullseason-dnet.json`
(tab "NBA Full dNET"). Shares the stats / odds / injury / ESPN caches with pyFull.
Does not build NBA Systems (pyFull does) and sends no email.

The scripts are a copy of `pyFull/scripts`: a fix to one usually belongs in
all three. `python replay_season.py --season 2025-26` backfills this model.
