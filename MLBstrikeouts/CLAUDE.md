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
2. **Scout O/U plays** — REBUILT 2026-08-26 on the first real backtest
   (`scripts/backtest_scout.py`, 121 games / 10 slates, graded at the
   payload's own prices against a bet-every-under baseline):

   - **The named-defect flag is the trigger, and it is the ONLY scout rule
     that has ever cleared both bars**: a layoff, stale window, opener, or
     swingman flag on either starter, played to the under, went **22-17
     (n=39, +8.1% ROI, +5.4 points over baseline)** — the sole rule in the
     battery to clear 25 plays AND beat its baseline. Retiring this clause
     on 8/25 was exactly backwards and the amendment went 0-2 immediately.
     It is restored as the primary condition.
   - **AMENDED 2026-09-01 (user), live the same day: PER-COMBO VERDICTS.**
     The single "swingman present -> under" rule is replaced by an explicit
     decision per flag configuration, read off the full-season grid
     (`scripts/build_flag_combo_table.py`, 510 flagged of 2,010 gradeable,
     flags replayed as-of from the pitcher logs). The verdicts live in
     `COMBO_VERDICTS` in that script and ride into the payload, so the
     dashboard and the card cannot disagree:

       PLAY UNDER  swingman (38-23 +19.0% p=0.030) · swingman+opener
                   (23-16 +13.3% p=0.118) · layoff alone (41-34 +4.8%
                   p=0.199) · the three thin swingman stacks, which run
                   +29% to +73% and are played as the swingman rule until
                   they reach n=25 · swingman+layoff+opener (27-23 +3.4%
                   p=0.296, carded on the user's call over the gate)
       PLAY OVER   swingman+stale-window (30-20 +14.7% p=0.059) -- the
                   contrarian cell: swingman says the arm is current,
                   stale-window says his line is two months old
       NO PLAY     swingman+layoff (-0.7%) · opener alone (+3.0%) ·
                   stale-window alone (-6.5%) · every rust combo at n<=5

     **The honest caveat, recorded because it is the whole risk:** this is a
     post-hoc carve of a validated aggregate. "Swingman present" was 166-124
     +9.1% at p=0.0047 over 290 games; keeping the cells that looked good and
     dropping the ones that did not is the same move that produced the
     rust-over mirage, done here on the full season instead of a 15-slate
     window. 16 tests were scanned, so ~0.8 cells should look significant by
     chance, and only ONE of the eight PLAY cells (swingman alone, p=0.030)
     actually cleared the gate -- the rest are carded on the user's call at
     p=0.06 to 0.30, or on samples too thin to test at all.
     Verdicts are stored as constants, never derived from live numbers, so a
     cell cannot silently flip on a week of variance; revisit if a PLAY cell
     runs clearly negative over its next 15-20.

   - **Bullpen L7 is the strongest descriptive field but is NOT yet
     bettable**: it separates runs cleanly (hot pens allow 3.39, leaking
     pens 5.09) yet every rule built on it is still under 25 plays, and
     leaking-pen overs lost money even while those games averaged 11 runs
     — the market prices pens. Shadow-track; do not card.

3. **Mismatch ML — RETIRED 2026-08-30 (user), after one day at 1-3.**
   Kept here because the reasoning is the point, not the result. Was: off the live scout payload's
   L20 mismatch, one play per qualifying SIDE --

   - `m <= -45` the arm outclasses the offense -> **TAIL him**, back his team
   - `m >= +55` the offense outclasses the arm -> **FADE him**, back the opponent

   Logged by `scripts/mismatch_shadow.py --log`. Full season 66-33 (+17.2%),
   positive in all five months, and it clears a permutation test -- 1,000
   shuffles of the mismatch values re-run through the same rule returned
   -3.5% on average and beat the real rule 22/1000 (**p = 0.022**). That is
   the first scout rule to show the column is doing real work rather than a
   threshold cutting noise attractively.

   Two things to hold onto. **The month series decays monotonically** (Apr
   +29.1% / May +33.8% / Jun +14.6% / Jul +13.1% / Aug +9.4%) and every
   split point drifts negative, so **August's +9.4% is the live expectation,
   not +17.2%**. And this was carded WITHOUT the 15-20 shadow plays the gate
   below requires -- a deliberate exception, not a precedent. If it runs
   clearly negative over its first 20 live plays, it comes off.

   The 99 historical plays are backfilled into the ledger with
   `"backfilled": true` and reported on their own line. They were never
   wagered, so they never touch the card record.

   **Why it was pulled, and what it does and does not prove.** Four plays
   cannot overturn a permutation test at p=0.022 -- and four wins would not
   have confirmed it either. What the episode settles is the PROCESS
   question. The rule went from backtest to full stake in a single day, so
   there was no live record to size against when it started losing, and the
   only honest response to 1-3 was to pull it entirely. That is the failure
   mode the shadow period exists to prevent: it is not there to prove a rule
   works, it is there so a bad opening run costs a fraction of a unit
   instead of a tier. **Do not card another rule without it.** If this one
   is revived, it shadow-trades 15-20 plays at the +9.4% expectation, not
   the +17.2% season figure.

4. **Scout MLs**: top conviction only — both halves of the game aligned
   across all four windows — capped at 1u, never opposing a model
   position. NOTE: this tier rests on the same ladder the backtest just
   found inert (it is 3-1 lifetime, n=4, which is nothing). Treat every
   scout ML as shadow until it has 25 graded plays.
5. **Leans are retired**: no half-case totals, no temperature-only reads,
   no "0.5u if you want it" tier. A read is card-grade or it is not
   bettable. If a filter on model plays ever seems attractive, define it
   in advance and shadow-track it before it touches a live bet.

The mismatch score and full-game wRC+ remain scouting context, never
signals (see build_slate_scout.py header for the backtests).

## Changing a scout rule (2026-08-26)

Every scout rule change before this date was made same-day off two or
three games, and the two that went live both lost immediately (the 8/25
flag-clause retirement went 0-2; the direction-neutral over half went
0-1). The gate now:

1. **Backtest first.** `python3 scripts/backtest_scout.py report` — add
   the proposed rule to `RULES` and grade it. No rule is discussed
   without its row.
2. **Clear both bars**: at least 25 graded plays AND a positive edge
   against the baseline the harness prints. Raw ROI is not evidence — in
   the 8/17-8/26 window blind unders returned +2.7% and blind overs
   −12.1%, so direction alone moves a rule 15 points.
3. **Shadow first, card later.** A rule that clears the backtest still
   logs to `scout-card-log.json` as shadow (`"shadow": true`) for 15-20
   live plays before it can produce a real bet.
4. **Grade closing-line value, not just W/L.** Both 8/25 losses BEAT the
   close (CLE/LAA 7.5→7, PHI/SEA 7.5→8). Beating the number while losing
   the game is process working plus variance; losing both is a broken
   read, and W/L alone cannot tell them apart. Record `close_line` when
   grading.

Where the scout can actually add value, per the backtest: **defects the
market prices lazily** (layoffs, stale windows, openers/swingmen — the
one measured edge) and **confirmed-lineup information**, which posts
after the number is set and is what makes hand-tails work. Offense
temperature is priced. Stop rediscovering that weekly.

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
