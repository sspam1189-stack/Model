# Replace dNET with dDEF: Eliminating Offensive Double-Counting

**Date:** 2026-03-30
**Status:** Approved

## Problem

The base score formula uses four stat differentials: `dTS`, `dTO`, `dORR`, and `dNET`. Net rating (`dNET = (h_OFF - h_DEF) - (a_OFF - a_DEF)`) already incorporates offensive efficiency, turnovers, and rebounding — the same signals captured by `dTS`, `dTO`, and `dORR`. This double-counts elite teams' offensive strengths, causing the model to systematically overpredict them at home.

Symptom: OKC (best team in the league) ranked dead last (30th) in Kalman state at `adj_mean = -4.99`. The Kalman filter correctly detected that actual margins were consistently lower than predicted — but the root cause was the base model inflating predictions for high-`dNET` teams, not any real weakness in OKC.

## Solution

Replace `dNET` with `dDEF` — the defensive rating differential only. Since `dTS + dTO + dORR` already capture offensive production, `dDEF` adds the defensive component without overlap.

## Feature Change

**Before:**
```python
base = (t_off + o_def) / 2
     + (t_TS - a_TS) * wTS
     - (t_TO - a_TO) * wTO
     + (t_ORR - a_ORR) * wORR
     + 0.5 * wNET * ((t_OFF - t_DEF) - (a_OFF - a_DEF))
     + constant
```

**After:**
```python
base = (t_off + o_def) / 2
     + (t_TS - a_TS) * wTS
     - (t_TO - a_TO) * wTO
     + (t_ORR - a_ORR) * wORR
     + (a_DEF - t_DEF) * wDEF
     + constant
```

**Sign convention:** `dDEF = a_DEF - t_DEF`. Lower DEF rating = better defense. If the computing team has better defense (lower `t_DEF`), `dDEF` is positive, contributing positively to their predicted margin. Correct.

**Note:** The `0.5` multiplier is removed. `wDEF` is a direct coefficient without the internal scaling.

## Weight

| | Default | Self-Tuned (reset) |
|---|---|---|
| Old `wNET` | 1.0 | 1.431 |
| New `wDEF` | **0.4** | 0.4 (reset on backfill) |

Rationale: current effective dNET contribution was `0.5 × 1.431 × dNET ≈ 0.716 × dNET`. For a typical 10-point gap that's ~7 points of margin — inflated. With `wDEF = 0.4` and a 10-point dDEF gap, contribution is 4 points. More conservative, intentionally, since defense is already partially captured in the `(t_off + o_def)/2` baseline.

## Scope

### Feature Fix (6 model configs + 2 shared engines + 2 self-tune files)

| File | Change |
|------|--------|
| `core/model_engine.py` | (1) Replace `0.5 * W["wNET"] * dNET` with `(a_DEF - t_DEF) * W["wDEF"]` in base score formula; (2) Rename `dNET → dDEF` in `extract_margin_features` returned dict |
| `core-js/model_engine.mjs` | Same |
| `core/self_tune.py` | `wNET → wDEF` in `WEIGHT_KEYS`; `dNET → dDEF` in feature vector `x` |
| `core-js/self_tune.mjs` | Same |
| `pyNBA/scripts/defaults.py` | Rename `wNET → wDEF`, set both values to `0.4` |
| `pyFull/scripts/defaults.py` | Same |
| `pyNCAA/scripts/defaults.py` | Same |
| `jsNBA/scripts/defaults.mjs` | Same |
| `jsFull/scripts/defaults.mjs` | Same |
| `jsNCAA/scripts/defaults.mjs` | Same |

### Clean Backfill (4 NBA models only)

pyNCAA and jsNCAA — feature fix only, no backfill. NCAA season is nearly over; not worth wiping state.

For each of the 4 NBA models:
1. Archive `history.json` → `history_pre_ddef_<timestamp>.json`
2. Reset `kalman_state.json` — zero all `adj_mean`, reset `adj_var` to initial defaults
3. Run backfill:

```bash
# pyNBA — start 2025-12-28 (92 days)
python scripts/backfill_last_n_days.py 92

# pyFull — start 2025-10-22 (159 days)
python scripts/backfill_last_n_days.py 159

# jsNBA — start 2025-12-28 (92 days)
node scripts/backfill_last_n_days.mjs 92

# jsFull — start 2025-10-22 (159 days)
node scripts/backfill_last_n_days.mjs 159
```

## Validation

After backfill completes:
- OKC Kalman `adj_mean` should be positive or near zero (not -4.99)
- Top teams (BOS, CLE, OKC) should rank in the upper half of Kalman state
- Bad teams (WAS, UTA) should be near the bottom
- Inspect pyNBA prediction accuracy on recent games vs. actuals

If predictions are still off after backfill, adjust `wDEF` and re-run.

## Backward Compatibility — NCAA History Records

pyNCAA and jsNCAA are not being backfilled, so their existing `history.json` records still contain `dNET` as a key in the margin features dict. After the rename, `self_tune` will look for `dDEF` and find nothing in old records.

Handling: treat missing `dDEF` key as `0` in the self-tune feature vector. Both `core/self_tune.py` and `core-js/self_tune.mjs` should use `.get("dDEF", 0)` (Python) / `?? 0` (JS) when reading the feature from history. This means old NCAA records contribute no `dDEF` signal to weight tuning — acceptable since the season is nearly over and the key change will be consistent from this point forward.

## What Is Not Changing

- Kalman filter logic (`core/kalman_state.py`) — untouched
- `self_tune.py` / `self_tune.mjs` — pick threshold calibration logic is unchanged; only `WEIGHT_KEYS` and the feature vector are updated (see scope table). Thresholds will recalibrate naturally via backfill
- `hca` weight — untouched
- pyNFL — completely separate EPA/ridge regression engine, no `dNET`, skip entirely
