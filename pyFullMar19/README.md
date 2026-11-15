# NBA full-season model as of 2026-03-19 (test copy)

`scripts/` is a verbatim copy of `PythonFull/scripts` at commit `088596411`
(minus `info.txt`, a notes file that is not code)
(2026-03-19 18:23 CT, the last code change that day). It is here for
walk-forward testing only: it is not in the daily run, has no dashboard tab and
writes no live data.

How it differs from today's models:

- **Projection:** `(OFF + oppDEF) / 2 + TS, TO and ORR terms + 0.5 x wNET x
  net-rating gap`, times pace, plus home court. Today's models use
  `OFF + wDEF x (oppDEF - league DEF)`.
- **Weights are tuned every day** from the previous day's games, including the
  pick threshold (`probHigh`, start 0.57).
- **Picks:** one tier, flat 1u, when P(cover) >= `probHigh`, the edge is at
  most 10 points and the line is under 12. A logistic-regression check can
  veto a pick once 80 graded games exist.
- **Kalman team corrections on**, head-to-head adjustment on (weight 0.15),
  home/away split blend 0.25, last-10 blend 0.35.
- **No projection until both teams have 15 games** (hard-coded).

`walk_forward.py` runs the old `run_daily.main()` unchanged, one date at a
time, on this season's saved inputs (see the header of that file). Run it from
the repo root, or through the "NBA March-19 Model Walk-Forward" workflow:

    python pyFullMar19/walk_forward.py --from 20261023 --to 20261112

`--min-gp` (default 1) lowers the 15-game rule so early-season dates can be
compared with the current models' preview; `--min-gp 15` is the model exactly
as it was.
