# MLB K's Whiff .4 — Blend-Weight A/B Variant (replaces MLB K's CSW)

**Date:** 2026-07-06
**Status:** Shipped

## Goal

Convert the daily parallel model from the CSW variant to a **whiff blend-0.4**
variant, so the two models running every day are:

- **MLB K's Whiff .2** — the live model (unchanged; `CSW_XBA_BLEND_WEIGHT = 0.2`).
- **MLB K's Whiff .4** — identical config except `CSW_XBA_BLEND_WEIGHT = 0.4`.

This makes the dashboard a clean single-knob A/B of the blend weight on live
slates, replacing the metric A/B (whiff vs csw) that the 2026-06-25/26 work
already settled as a wash.

## Why

The 2026-07-02 sweep that shipped blend 0.2 (grid {0.0, 0.2, 0.4, 0.6}) showed
0.2 winning every walk-forward window on ROI/WR/MAE. Since then (6/15–7/5),
0.4 has out-earned 0.2 on picks-only volume (e.g. 6/29–7/5: 10-3 +5.33u flat
vs 8-4 +2.52u) while 0.2 kept the better win rate, ROI per unit, and MAE in
nearly every window — i.e. 0.4's edge is extra marginal promotions landing,
not better projections. A live shadow A/B settles whether that volume edge is
real or a hot streak, without touching the live model.

## Configuration

| Setting | Whiff .2 (live) | Whiff .4 (variant) |
|---|---|---|
| `K_QUALITY_METRIC` | `whiff` | `whiff` |
| `CSW_XBA_BLEND_WEIGHT` | `0.2` | **`0.4`** |
| everything else (kcap 0.36, VAR 1.30, threshold 0.70, lineup 0.8, calib ON) | base | base (identical) |

Selection: `MLB_K_VARIANT=w04` env var (new; read at the end of `defaults.py`).
`MLB_K_METRIC=csw` still selects the old CSW profile for manual runs, but CI no
longer runs it and its outputs/tab were removed.

## Changes

- `defaults.py`: new `MLB_K_VARIANT` env var + `w04` variant profile
  (`VARIANT_SUFFIX = "_w04"`, blend 0.4, metric forced to whiff). CSW profile
  retained below it for manual use.
- `.github/workflows/mlb-run-daily.yml`: second daily step now sets
  `MLB_K_VARIANT: w04` (was `MLB_K_METRIC: csw`); artifact names/paths and the
  dashboard copy switched `_csw`/`mlb_csw` → `_w04`/`mlb_w04`; commit message
  now "(Whiff .2 + .4)".
- Dashboard: tab `mlb-props-csw` → `mlb-props-w04`; labels "MLB K's Whiff" /
  "MLB K's CSW" → "MLB K's Whiff .2" / "MLB K's Whiff .4"; the CSW-only 0.68
  bet-cutoff override in `mlb-props.js` removed (both tabs bet 0.70 — the
  variant differs from live only in blend weight).
- Removed seeded CSW outputs: `mlb-props_csw.json` (both copies),
  `kalman_state_csw.json`, `data/emp_std_cache/mlb_csw/`.
- Seeded `_w04` history via `MLB_K_VARIANT=w04 python -m scripts.props_backfill`
  (walk-forward, leak-free — same procedure as the CSW seed).

`run_daily.py` / `props_backfill.py` needed no changes: all path isolation is
already generic over `VARIANT_SUFFIX` (threaded 2026-06-26). The per-date
whiff regression-slope cache is shared by both variants by design — the fit
does not depend on the blend weight, only its application does.

## Decision rule (pre-registered)

Let the A/B run through end of July (~50–100 picks per side). Ship 0.4 to the
live model only if it leads on picks-tier ROI **and** win rate over the full
A/B window; otherwise keep 0.2. MAE tiebreaks.
