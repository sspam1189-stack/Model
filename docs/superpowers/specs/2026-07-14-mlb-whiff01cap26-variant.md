# MLB K's Whiff .1/Cap26 — Blend/Cap Variant (replaces MLB K's Whiff .4)

**Date:** 2026-07-14
**Status:** Shipped

## Goal

Replace the Whiff .4 daily parallel model with a **whiff blend-0.1 / BF_CAP-26**
variant, so the two models running every day are:

- **MLB K's Whiff .2** — the live model (unchanged; `CSW_XBA_BLEND_WEIGHT = 0.2`,
  `BF_CAP = 25.0`).
- **MLB K's Whiff .1/Cap26** — identical config except `CSW_XBA_BLEND_WEIGHT =
  0.1` and `BF_CAP = 26.0`.

## Why

A narrow walk-forward sweep (`scripts/sweep_lineup_blend.py`, extended
ad hoc for BF_CAP/VAR) around the shipped lineup 0.8 / blend 0.2 / cap 25 /
var 1.3 config showed:

- Blend 0.4 (the outgoing variant) is dominated at every lineup weight and in
  every window tested — season, last-3-weeks, and the 7/6+ shadow stretch all
  degrade monotonically as blend rises past 0.2, bottoming out badly at 0.5/0.6
  (season units down ~20%, MAE degrades from ~1.74 to ~1.79+, shadow window as
  low as -54% ROI). This confirms the standing `defaults.py` note that "0.5+
  overshoots" and extends it: 0.4 itself is already past the optimum.
- At lineup 0.8, blend 0.1 / cap 26 / var 1.3 was the single best cell in the
  full blend x cap x var grid on season ROI (+35.97% vs shipped's +34.97%) and
  the most resilient cell in the recent slump (7/6-7/14 shadow window: -10.83%
  ROI vs shipped's -25.02% at blend 0.1/cap25, better still at cap 26).
- Var 1.3 (already shipped) and cap 25-26 remain confirmed as the right
  neighborhood; cap 23 is clearly worse everywhere.
- Shipped (blend 0.2/cap 25) still wins full-season units and June outright,
  and wins last-3-weeks/July on raw units when compared cell-for-cell against
  blend 0.1/cap 26 — the tradeoff is quality/resilience (blend 0.1) vs raw
  volume (blend 0.2). Since the *outgoing* variant (blend 0.4) was already the
  loser on both axes, blend 0.1/cap 26 is the more informative shadow to run
  next: if it doesn't beat live on ROI/WR through the rest of the A/B window,
  0.2 stays shipped, same decision rule as before.

## Configuration

| Setting | Whiff .2 (live) | Whiff .1/Cap26 (variant) |
|---|---|---|
| `K_QUALITY_METRIC` | `whiff` | `whiff` |
| `CSW_XBA_BLEND_WEIGHT` | `0.2` | **`0.1`** |
| `BF_CAP` | `25.0` | **`26.0`** |
| everything else (lineup 0.8, VAR 1.30, threshold 0.70, kcap 0.36, calib ON) | base | base (identical) |

Selection: `MLB_K_VARIANT=w01c26` env var (was `w04`). `MLB_K_METRIC=csw`
still selects the old CSW profile for manual runs.

## Changes

- `defaults.py`: variant profile block now checks `MLB_K_VARIANT == "w01c26"`
  and sets `CSW_XBA_BLEND_WEIGHT = 0.1`, `BF_CAP = 26.0`,
  `VARIANT_SUFFIX = "_w01c26"` (was blend 0.4 / suffix `_w04`).
- `.github/workflows/mlb-run-daily.yml`: second daily step now sets
  `MLB_K_VARIANT: w01c26`; artifact names/paths and the dashboard copy
  switched `_w04`/`mlb_w04` → `_w01c26`/`mlb_w01c26`; commit message now
  "(Whiff .2 + .1/Cap26)".
- Dashboard: tab `mlb-props-w04` → `mlb-props-w01c26`; labels "MLB K's Whiff
  .4" → "MLB K's Whiff .1/Cap26" (`index.html`, `main.js`, `mlb-props.js`
  comments). Both tabs still bet the same 0.70 cutoff — the variant differs
  from live only in blend weight + BF_CAP.
- Removed seeded Whiff .4 outputs: `mlb-props_w04.json` (both copies),
  `kalman_state_w04.json`, `data/emp_std_cache/mlb_w04/`, and their
  `artifacts/` mirrors.
- Seeded `_w01c26` history via `MLB_K_VARIANT=w01c26 python -m
  scripts.props_backfill` (walk-forward, leak-free — same procedure as the
  w04 seed).

`run_daily.py` / `props_backfill.py` needed no changes: all path isolation is
already generic over `VARIANT_SUFFIX`.

## Decision rule (pre-registered)

Let the A/B run through end of July (~50–100 picks per side). Ship 0.1/cap26
to the live model only if it leads on picks-tier ROI **and** win rate over the
full A/B window; otherwise keep 0.2/cap25. MAE tiebreaks.
