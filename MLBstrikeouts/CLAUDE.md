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
2. **Scout O/U plays**: card-grade only — BOTH offenses' ladders must align
   across all four rolling windows (L30/L20/L15/L7, role=all, from
   mlb-team-woba-splits.json) in the same direction (both <=90 -> under,
   both >=110 -> over). Card-grade totals went 3-1 that week.
   2026-08-25 (user): the named-defect/flag requirement is RETIRED for
   totals — it benched two winning unders in four days (LAA/TEX U8 on
   8/22, CLE/COL U11.5 on 8/23) while the aligned-both-cold profile went
   3-0; full four-window alignment on both offenses IS the requirement.
   One aligned ladder alone is still not a play.
3. **Scout MLs**: top conviction only — both halves of the game aligned
   across all four windows — capped at 1u, never opposing a model
   position.
4. **Leans are retired**: no half-case totals, no temperature-only reads,
   no "0.5u if you want it" tier. A read is card-grade or it is not
   bettable. If a filter on model plays ever seems attractive, define it
   in advance and shadow-track it before it touches a live bet.

The mismatch score and full-game wRC+ remain scouting context, never
signals (see build_slate_scout.py header for the backtests).

## Fade-roster curation standards (2026-08-26, user)

When auditing, adding, or removing arms on the fade list or hand-tails
roster:

1. **Venue-restricted arms are judged on the restricted venue's record
   ONLY.** Never blend home+away into one number for a venue-scoped fade —
   a "7-4 total" is meaningless if the fade only fires away. Split the
   ledger by venue first, then evaluate.
2. **Weight the latest venue-specific results over the season backfill.**
   A season record front-loaded in April-June with recent fades losing
   (the Painter/Gore shape) is a removal signal even when the cumulative
   number still looks healthy. Split pre/post a recent cutoff (e.g. 8/1)
   and check the last 4-6 fades on the qualifying venue.
3. **Both lenses must pass for adds**: a positive RECENT fade record on
   the qualifying venue AND current pitcher form that supports the thesis.
   An in-form arm whose recent fades happen to have cashed is variance,
   not an edge (the Messick/Alvarez trap). An elite arm is never added on
   price alone (Mize/Nola/Skenes-v1 pattern).
4. **Good form alone doesn't remove a winning fade** — the trigger is form
   PLUS the venue record turning (Gore, Nola), not form by itself
   (Mahle/Wacha keep cashing while pitching well).
