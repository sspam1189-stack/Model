# NBA full-season backtests

Replays of past seasons on a later version of the model. These are
**simulations, not the record of picks that were actually posted**; the real
record lives in `../archive/` (e.g. `history-2025-26.json`, 412-227-1).
Nothing in this folder is read by the dashboard or the daily pipeline.

## history-2025-26-replay.json

2025-26 (2025-10-22 to 2026-06-15) replayed offline on the code at commit
`0d58be81b` (2026-10-20), i.e. the setup the 2026-27 season starts with:

- `MIN_GP` 10 (stats burn-in), `KALMAN_SCALE` 0.0 (Kalman tracked, not applied)
- Kalman double-count and playoff weight-leak fixes (21975ec19)
- weights start from `DEFAULT_W` and self-tune through the season

Inputs: the cached daily stats (`data/stats_cache/nba/`) and the lines, scores
and games stored in the real 2025-26 history. Not reproduced: live injury
adjustments and live odds timing, so treat the record as optimistic.

| | Record | Units (-110, 1u) |
|---|---|---|
| All spread picks | 445-246 | +158.5u |
| 2u band (P(cover) >= 0.65) | 269-116 | +128.5u |

Use it as the "how would the current model have done" baseline for 2026-27,
not as a replacement for the real 2025-26 record.
