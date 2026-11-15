# NBA full-season model as of 2026-04-19 (test copy)

`pyFull/scripts/` and `core/` are verbatim copies of those folders at commit
`fca4833db` (2026-04-19; the model code itself last changed on April 9 and 18),
minus `info.txt`, a notes file that is not code. Here for walk-forward testing
only: not in the daily run, no dashboard tab, no live data.

How it differs from today's models:

- **Same projection formula** as today: `OFF + wDEF x (oppDEF - league DEF)`,
  times pace, plus home court.
- **Weights are tuned every day** (60-day window), including the pick
  threshold (`probHigh`, start 0.58). Today's models are frozen.
- **Recent form counts far more:** last-10 blend 0.87 (0.35 today).
- **Kalman team corrections on.**
- **Picks:** one tier, flat 1u, when P(cover) >= `probHigh` and the line is
  under 18.
- **No projection until both teams have 15 games.**

Run from the repo root, or through the "NBA Old-Model Walk-Forward" workflow
with version `apr19`:

    python pyFullApr19/walk_forward.py --from 20261023 --to 20261112

The driver is shared with the March copy (`pyFullMar19/walk_forward.py`).
`--min-gp` (default 1) lowers the 15-game rule so early-season dates can be
compared; `--min-gp 15` is the model exactly as it was.
