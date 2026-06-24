# MAE Gate — measure signal over the 0.65+ universe (shadow only)

**Date:** 2026-06-24
**Status:** Design — approved for planning
**Scope:** `PythonDashboard/js/mlb-props.js` only. Shadow-only; no change to live
staking, picks, conf, or locks. Live bet cutoff stays flat 0.70.

## Problem

The MAE Gate (`renderMaeGate`, ~L1322) is a shadow monitor for the question
"on days my recent form trails the closing line, how does the 0.65–0.70 band I
no longer bet actually do?" Its signal is a trailing 3-day gap of model MAE
(`|proj-actual|`) minus line MAE (`|line-actual|`) over a population of graded
picks.

That signal population (`g`, ~L1327) currently filters `p.pick === 'OVER' ||
'UNDER'` — **actual bets only**. After the 2026-06-17 threshold move to 0.70,
the 0.65–0.70 band is demoted to `would_be_pick` (conf=watch), so it is
excluded from the signal. The gate is therefore measuring MAE on the **0.70+
book**, not the 0.65+ universe it is meant to reason about.

## Desired behavior

The gate should measure trailing model-MAE vs line-MAE across the **full 0.65+
universe** (the 0.70+ bets *and* the 0.65–0.70 would-be band), and drive the
band toggle from that signal:

- **Behind** (gap = model MAE − line MAE ≥ 0) → close the band off → effective
  **0.70** cutoff.
- **Ahead** (gap < 0) → reopen the band → **0.65+**.

This is the dynamic 0.65+ policy. It remains **shadow only** — it changes no
live pick. Live staking stays flat 0.70.

## Changes

All edits are in `PythonDashboard/js/mlb-props.js`.

### 1. Widen the signal population in `renderMaeGate` (~L1327)

Change the `g` filter from "bet picks only" to "all graded directional 0.65+
plays" by accepting `would_be_pick` alongside `pick`:

```js
const g=(data.props||[]).filter(p =>
  (p.pick==='OVER'||p.pick==='UNDER'||
   p.would_be_pick==='OVER'||p.would_be_pick==='UNDER') &&
  (p.result==='WIN'||p.result==='LOSS') &&
  p.pCover!=null && p.line!=null && p.proj!=null && p.actual!=null &&
  p.pCover>=LOWT);   // LOWT = MLB_LEAN_FLOOR = 0.65
```

`LOWT` is already `MLB_LEAN_FLOOR` (0.65), so the `pCover>=LOWT` floor is
correct as-is; only the direction predicate changes. The 3-day gap now reflects
the whole 0.65+ universe.

The `tighten` rule (`ga >= 0`) and `GWIN=3` / `GMIN=8` window parameters are
unchanged — `behind → 0.70`, `ahead → 0.65+` already maps onto `gap >= 0 →
tighten → cut band`.

### 2. Mirror the population in `_maeTightenDates` (~L512)

The Recent Record "MAE" toggle (`_maeGateKeep`, ~L532) derives its tighten days
from `_maeTightenDates` (~L512), which builds the same `g` population with the
**bet-picks-only** filter. Widen it identically so the toggle and the card
compute the *same* tighten days from the *same* 0.65+ population:

```js
const g = (data.props || []).filter(p =>
  (p.pick === 'OVER' || p.pick === 'UNDER' ||
   p.would_be_pick === 'OVER' || p.would_be_pick === 'UNDER') &&
  (p.result === 'WIN' || p.result === 'LOSS') &&
  p.pCover != null && p.line != null && p.proj != null && p.actual != null &&
  p.pCover >= LOWT);   // LOWT = MLB_LEAN_FLOOR
```

### 3. Add a Flat-0.65 band baseline to the card's shadow ledger (~L1409)

The `band` array (~L1336) is already the 0.65–0.70 tier, graded, unioned across
both eras (`pick` pre-move, `would_be_pick` post-move). Add a single reduce over
**all** of `band` (every day, not only tighten days) to get the Flat-0.65
band-only record, and render it above the existing saved-u line:

- New line: *Flat 0.65 (band always bet, all days): `W`-`L` (`±U`u).*
- Existing headline (unchanged): *On tighten days the band went `W`-`L`
  (`±u`). Skipping it … worth **`+saved`u**.*
- Interpretive clause: *Dynamic 0.65+ rule (band on normal days, off on tighten
  days) nets `flat065_u + saved`u on the band vs `flat065_u`u always-on.*

Use the existing `calcMLBPropsUnits` helper for units and the existing empty
guard so the line no-ops cleanly when `band` is empty.

## Out of scope / untouched

- Live staking, `MARKET_THRESHOLDS`, `MLB_PICK_STRONG`, `MLB_LEAN_FLOOR`,
  `defaults.py`, `run_daily.py`.
- EV Gate, Read card, `track_threshold_shadow.py`.
- The `band` population itself (already correct — 0.65–0.70, both eras).

## Testing

Load the dashboard (`PythonDashboard`) and confirm:

1. The MAE Gate card renders, including the new Flat-0.65 baseline line.
2. **Population check:** the signal now counts 0.65–0.70 would-be plays —
   tighten-day determination can differ from the pre-change card on days where
   the band swings the MAE gap. Spot-check one date by hand.
3. **Arithmetic:** `tighten-day band record + normal-day band record =
   Flat-0.65 band record` (the two partitions sum to the all-days total).
4. **Identity:** `dynamic_band_u = flat065_u + saved` holds (saved = −tighten-day
   band units).
5. The Recent Record "MAE" toggle and the card agree on tighten days (both now
   use the 0.65+ population).
