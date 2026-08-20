# MLBstrikeouts — notes for Claude

## Bet sizing in ad-hoc sweep/backfill scripts

Use **flat 1u** staking when computing units/ROI in any sweep, A/B, or
backfill analysis script (`scripts/sweep_*.py`, one-off report helpers,
etc.) — matches the live dashboard's actual staking plan (`MLB_PICK_STAKE =
1` in `PythonDashboard/js/mlb-props.js`, picks-only flat 1u, no lean tier).

`scripts/compare_configs_AB.py` predates that staking plan and still uses
2.5u picks / 1.5u leans — leave it as-is unless asked to update it, but
don't copy its sizing convention into new scripts. `scripts/sweep_lineup_blend.py`'s
`units()` defaults to `sz=1.0` for this reason; new sweep scripts should do
the same.

ROI% and win rate are scale-invariant to flat stake size, so this only
affects the raw units figures reported, not which config wins.

## Daily betting-card policy ("trust the model", 2026-08-20)

When building a daily card from this repo's outputs, the tiers are:

1. **Model plays** (fade-ML, K-model picks): take every one, standard
   sizing (risk-to-win-1u at negative odds, risk-1u at positive), with NO
   discretionary overlays in either direction — no scout-based vetoes, no
   price-bucket cuts, no "backed-side blind spot" skips. Both overlay
   attempts in the week of 8/17 were wrong within 24h (NYM +122 skipped,
   won; CLE −205 cut, and the >−200 fade bucket is itself 21-4 +19.5%).
2. **Scout O/U plays**: card-grade only — the offense ladder must align
   across all four rolling windows (L30/L20/L15/L7, role=all, from
   mlb-team-woba-splits.json) AND the read must be keyed to a named
   defect/flag (layoff, stale window, opener/swingman, collapsed IP/GS),
   not temperature alone. Card-grade totals went 3-1 that week.
3. **Scout MLs**: top conviction only — both halves of the game aligned
   across all four windows — capped at 1u, never opposing a model
   position.
4. **Leans are retired**: no half-case totals, no temperature-only reads,
   no "0.5u if you want it" tier. A read is card-grade or it is not
   bettable. If a filter on model plays ever seems attractive, define it
   in advance and shadow-track it before it touches a live bet.

The mismatch score and full-game wRC+ remain scouting context, never
signals (see build_slate_scout.py header for the backtests).
