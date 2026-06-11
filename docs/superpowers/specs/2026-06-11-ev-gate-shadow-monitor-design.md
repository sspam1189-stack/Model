# EV Gate — shadow monitor (not live) — Design

**Date:** 2026-06-11
**Status:** Approved (pending spec review)
**Scope:** MLB strikeouts props model + dashboard

## Summary

Add a second, **shadow-only** gate on top of the existing pCover threshold.
Today a pick becomes actionable (`conf="high"`) purely on `pCover >= MARKET_THRESHOLDS[market]["high"]` (0.64 for strikeouts) plus edge/line filters — **price is never consulted**. So a -200 favorite at pCover 0.64 (needs 66.7% to break even, badly -EV) is treated identically to a +100 dog at 0.64 (needs 50%, strongly +EV).

The EV Gate converts the offered price to a breakeven probability and compares it to `pCover`. It stamps a TAKE/PASS verdict per pick **without changing the actual pick, conf, or lock** — purely for monitoring, exactly like the existing "Read" verdict.

This feature mirrors the existing "Read" gate's architecture in all three of its sites (run_daily, props_backfill, dashboard JS). As part of this work the existing "Read Record (backtest)" card is **renamed** to "Read Model History Gate — shadow monitor (not live)" and **relocated** so the two shadow gates sit together as a group.

Non-goal: this never alters which picks are bet. It is a measurement layer only.

## Verdict logic (the core)

For each actionable pick (`pick ∈ {OVER, UNDER}`) carrying integer American `odds`:

- **breakeven** (implied prob of the offered price), computed sign-aware **from `odds` directly**:
  - `odds < 0` → `breakeven = |odds| / (|odds| + 100)`  (e.g. -158 → 0.612, -200 → 0.667)
  - `odds > 0` → `breakeven = 100 / (odds + 100)`        (e.g. +120 → 0.455, +100 → 0.500)
- **Verdict** (default-TAKE, only PASS on a clear negative signal, mirroring Read):
  - `evVerdict = "PASS"` if `pCover < breakeven - EV_GATE_MARGIN`
  - else `evVerdict = "TAKE"`
- Pick missing `odds` → `evVerdict = "TAKE"` (no price to gate on).
- Only OVER/UNDER actionable picks get a verdict; PASS/WATCH rows are skipped (same as Read).

The gate is `pCover` vs `breakeven`, which is **staking-independent** — breakeven probability does
not depend on stake size, so the verdict is unaffected by the project's asymmetric unit convention.
`PASS` means the pick is -EV at the offered price; `TAKE` means +EV (within the margin).

**Do NOT use `to_win_1u` to derive breakeven.** Per `_to_win_1u()` in props_engine.py, that field is
*stake-to-win-1u* (= 1/b, the reciprocal of the net payout per 1u), not the payout multiplier:
-158 → 1.58, +120 → 0.83. Computing breakeven from `odds` directly avoids that trap. (For reference,
`breakeven == to_win_1u / (1 + to_win_1u)`, but the sign-aware `odds` form above is canonical.)

`EV_GATE_MARGIN` is a new constant in `defaults.py`, **default `0.0`** (pure EV>0). A small
buffer (e.g. 0.01–0.02) can be set later to absorb model-calibration noise; it is a single
tunable knob consistent with the project's other thresholds.

Note on de-vig: the breakeven uses the **raw offered price** (vig included), because that is the
price the bet is actually placed at. This is the same convention as Read's `_units` helper, which
also uses raw `odds`. No no-vig / fair-market adjustment is applied.

Walk-forward note: unlike Read, the EV verdict is **self-contained per pick** — it depends only on
that pick's own `odds` and `pCover` at pick time, so there is no historical accumulation and no
leak risk. The backtest card simply re-applies the same per-pick rule to each graded pick.

## Data field

- New per-prop field: **`evVerdict`** with value `"TAKE"` or `"PASS"`.
- Stamped on actionable picks only; absent on PASS/WATCH rows.

## Mirror sites (mirroring the existing "Read" gate)

### 1. `MLBstrikeouts/scripts/run_daily.py`
- Add `_stamp_ev_verdicts(merged_props)` — walks `merged_props`, applies the per-pick rule above,
  sets `p["evVerdict"]` on each OVER/UNDER strikeouts pick. Self-contained (no cohort maps).
- Call it immediately after the existing `_stamp_read_verdicts(merged_props)` (~line 1541).
- Add `"evVerdict"` to the `_VOID_STRIP_FIELDS` tuple (~line 1555) so voided pitchers' verdicts
  are blanked alongside `readVerdict`.

### 2. `MLBstrikeouts/scripts/props_backfill.py`
- Import and call `_stamp_ev_verdicts` next to the existing `_stamp_read_verdicts` import/call
  (~line 1073), so a clean backfill populates `evVerdict` across history.
- Extend the summary print (~line 1084) to also report an "EV TAKE" count, e.g.
  `... ({read_takes} Read TAKE, {ev_takes} EV TAKE) to {path}`.

### 3. `MLBstrikeouts/scripts/defaults.py`
- Add `EV_GATE_MARGIN = 0.0` with an explanatory comment block in the threshold/config region.

### 4. `PythonDashboard/js/mlb-props.js`
- Add a shared `evVerdictFor(p)` helper (mirrors `readVerdictFor`) implementing the per-pick rule
  in JS, so the live chip and the backtest card share one rule.
- Add an **"EV"** option to `recentModeOptions` (~line 369):
  `{ label: 'EV', filter: p => p.evVerdict === 'TAKE', title: 'Recent EV Record' }`.
- Add a new **EV Gate backtest card** (`_evRecordCard`) built like `buildReadRecord()` (~line 3184):
  walk every graded pick, compute `evVerdictFor`, compare to actual result, tally W-L-units for the
  TAKE subset. (No walk-forward accumulation needed — verdict is per-pick.) The W-L-**units** tally
  must reuse the existing units helper (the asymmetric risk-to-win-1u / risk-1u convention already
  shared by Read's record cards), so EV-card units are comparable to every other unit figure on the
  page — do not recompute units with a naive symmetric formula.

## Dashboard layout change

Group the two shadow gates together at the bottom of the page (where Read Record currently sits,
appended around line 4213). New order within the group:

1. **Read Model History Gate — shadow monitor (not live)** — the existing `_readRecordCard`,
   renamed (card title/header text updated from "Read Record (backtest)") and relocated into the
   group.
2. **EV Gate — shadow monitor (not live)** — the new `_evRecordCard`.

The "Recent Record" container and All-history table stay below the group, preserving the rest of
the page flow.

## Decisions locked

- `EV_GATE_MARGIN` default **0.0**.
- Field name **`evVerdict`**.
- Card order: **Read Model History Gate first, EV Gate second.**
- Raw offered price for breakeven (no de-vig).

## Testing / verification

- After implementation, run `run_daily` (or a dry equivalent) and confirm `evVerdict` is stamped
  on actionable strikeouts picks and absent on PASS/WATCH.
- Confirm voided pitchers have `evVerdict` blanked along with `readVerdict`.
- Spot-check a known favorite below breakeven (e.g. a -200 line at pCover ~0.64) stamps `PASS`, and
  a +EV pick stamps `TAKE`.
- Load the dashboard: confirm the "EV" recent-record chip filters, and the two shadow-gate cards
  render in order with the renamed Read card.
- Confirm the live pick set (which picks are `conf="high"`) is **unchanged** — shadow only.

## Out of scope

- Making the gate live (flipping picks based on EV) — explicitly not now.
- Tuning `EV_GATE_MARGIN` away from 0.0 — left as a future sweep.
- The Today's Games + Pitcher History merge — separate spec.
