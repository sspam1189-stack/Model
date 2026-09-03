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
2. **Flag Plays (scout O/U)** — REBUILT 2026-08-26 on the first real backtest
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
       SHADOW      swingman+stale-window (30-20 +14.7% p=0.059), the only
                   OVER on the grid -- SHADOWED FROM 2026-09-03 (user).
                   Tracked, still qualifying, no units. The contrarian
                   read is that swingman says the arm is current while
                   stale-window says his line is two months old; against it,
                   it never cleared the gate and it is the one cell with no
                   neighbour to corroborate it, since every other verdict
                   here is an under. Live record at the call: 0-1 (SD @ CIN
                   9/1, Vasquez swingman-2g vs Lodolo stale-window-46d, O9
                   lost). It keeps its "over" side so a tracked row still
                   means something, and can be carded again from a date via
                   VERDICT_SHADOW if 15-20 plays firm it up.
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

     Two cells have since been shadowed rather than deleted, both dated in
     `VERDICT_SHADOW` so an earlier slate still reads the way it was bet:
     **layoff alone** (2026-09-02) and **swingman+stale-window**
     (2026-09-03). A shadowed combo keeps its measured side, keeps
     qualifying and keeps writing a no-stake row, so it goes on producing
     evidence while costing nothing -- which a retired one cannot do.

   - **Form under — CARDED 2026-09-01 (user).** `m_sum <= -40` (both
     starters' mismatch scores summed: both arms outclass the bats they
     face) -> bet the UNDER. **84-52 +17.2% ROI, n=136, perm p=0.005**, all
     bands positive (-40/-60/-80), both walk-forward halves positive
     (+27.5%/+8.9%). Statistically the strongest scout result in this repo.
     Live record before carding: 0-1 (SEA/BOS 8/31, a 17-run game).
     Caveat: 45% of its qualifiers in the flag era were games the flag rule
     already cards, and the unflagged slice was 8-8 -- the ledger carries
     `flagged_overlap` on every entry so the incremental half can be judged
     separately.

   - **Bullpen L7 is the strongest descriptive field but is NOT yet
     bettable**: it separates runs cleanly (hot pens allow 3.39, leaking
     pens 5.09) yet every rule built on it is still under 25 plays, and
     leaking-pen overs lost money even while those games averaged 11 runs
     — the market prices pens. Shadow-track; do not card.

3. **Better arm ML — CARDED 2026-09-01 (user), dogs only.** In games where
   both starters are outclassed by the bats they face (`m_sum` = the two
   sides' mismatch scores summed, `>= +40`), back the team whose starter has
   the LOWER mismatch — but only when that team is priced at plus money.

       rule (dogs only)     19-15  +26.0%   n=34
       favorite half        63-41   +3.8%   n=104   measured, out of scope
       whole pool           82-56   +9.3%   n=138   perm p=0.043
       CONTROL favorite     79-59   -1.9%   n=138
       same rule outside    677-560 -0.2%   n=1237
       mirror pool (<= -40) 73-67   -6.1%   p=0.698  DEAD

   The controls are the case, not the ROI: backing the favorite in the same
   games loses, the identical rule outside the pool is flat, and the mirror
   pool is dead. So the m_sum filter selects the games, and the arm
   comparison is not a favorite proxy. Table rebuilt every run by
   `scripts/build_msum_ml_table.py`; the panel and the ledger read its
   `status` field, so flipping card/shadow is a one-line change there.

   **Carded without a shadow period, and that is the risk.** The mismatch ML
   below was carded the same way at a BETTER p-value (0.022) and was pulled
   at 1-3 the next day. This rule is n=34 in scope, April ran -31%, and it
   surfaced during a session that scanned a great many cells. It fires about
   once a week, so a bad run takes months to read; if it is clearly negative
   over its first 10-15 plays it comes off. The favorite half stays measured
   so the rule can be WIDENED rather than scrapped if the dog cut is the
   overfit part.

4. **Mismatch ML — REVIVED AS SHADOW 2026-09-01 (user).** Carded 8/29
   without a shadow period, pulled 8/30 at 1-3, and now doing the shadow
   period the gate asked for in the first place: `scripts/mismatch_shadow.py
   --log` writes `shadow-mismatch-ml` entries that are tracked and never bet,
   15-20 plays at August's **+9.4%** expectation, not the +17.2% season
   figure. It reads off the live scout payload's
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

   **Why it was pulled the first time, and what that does and does not
   prove.** Four plays
   cannot overturn a permutation test at p=0.022 -- and four wins would not
   have confirmed it either. What the episode settles is the PROCESS
   question. The rule went from backtest to full stake in a single day, so
   there was no live record to size against when it started losing, and the
   only honest response to 1-3 was to pull it entirely. That is the failure
   mode the shadow period exists to prevent: it is not there to prove a rule
   works, it is there so a bad opening run costs a fraction of a unit
   instead of a tier. **Do not card another rule without it.** If this one
   is revived, it shadow-trades 15-20 plays at the +9.4% expectation, not
   the +17.2% season figure. **That revival happened 2026-09-01** — this is
   the one rule on the board that is doing the process in the right order.

5. **Aligned ML — RETIRED 2026-09-02 (user).** One offense hot-aligned
   across all four windows (>= 110) while the other is cold-aligned (<= 90),
   at its own 75-PA floor (`ALIGNED_ML_MIN_PA`) -> back the hot side's team.

   Carded 2026-09-01 on a 3-1 lifetime record (n=4) with no statistical case,
   on a four-window ladder that had been measured INERT for runs (cold-aligned
   offenses scored 4.54 a game, hot-aligned 4.52). The full-season replay
   published the next day, when Form under and Mismatch ML finally got a
   season table, put it at **6-7, -12.8% over 13 plays, negative in both
   halves (-8/-30)** against a -3.3% blind baseline. That is the opposite of
   the 3-1 that justified carding it, on three times the sample, so it came
   off.

   The lesson is the one the rust-over rule taught in August and this repeats:
   a rule carded on four games is carded on nothing. The reason it took a day
   to catch is that Aligned ML had no season grid -- its record lived only in
   this file as prose. Every rule now has a table (`scout-rules-table.json`
   covers the three that did not), so the next one cannot hide.

6. **Non-scout systems — EIGHT CARDED (five 2026-09-01, three 2026-09-02; user).** A separate
   family, defined in `scripts/allml_systems.py` and grouped apart on the
   tab and in the ledger. They read `mlb-all-ml.json` alone -- prices,
   totals, probables, scores, plus what can be derived as-of from its own
   history -- and take no input from the mismatch model, so when one agrees
   with a scout rule it is a second opinion rather than the same inputs
   counted twice. Season records, replayed as-of each game date:

   | system | rule | record | ROI | blind |
   |---|---|---|---|---|
   | Away dog ML | away dog, total >= 9.5 | 87-76 | +23.4% | -3.2% |
   | Home slide ML | home on L4+, new opponent | 33-17 | +25.0% | -3.2% |
   | Pickem under | favorite -115 or shorter, total >= 8.5 | 98-69 | +11.5% | -3.5% |
   | Starter over run | a starter 75%+ overs in his last 8, total >= 8.0 (8.5 before 2026-09-03) | 120-87 | +10.8% | -6.1% |
   | Cold arms under | both starters <= 40% overs in last 8, total <= 8.0 (was <=35% ungated before 2026-09-03) | 27-14 | +23.5% | -3.5% |
   | Division home dog | home to a division rival, priced +115 to +149 | 46-27 | +43.0% | -3.2% |
   | Home dog getaway | day game, visitors played last night, home is a plus-money dog | 26-19 | +26.0% | -3.2% |
   | Home dog + under | home dog +115 to +149, parlay the ML with the under | 83-165 | +43.6% | 0.0% |

   Three more were carded the same day and removed before any of them
   settled a play. **Hot arm dog ML** (back a plus-money side whose starter's
   team is +40% or better over his last 8 starts, 91-90 +12.7%) because at
   91-90 the win rate is a coin flip and the whole return is the plus-money
   prices holding -- a dog system has to beat its price, and that one only
   matched it. **Low line over** (total <= 7 -> over, 72-52 +8.9%) because the 7.5
   bucket beside it runs -9.4% over 457 games, so it was the 7-and-under cell
   rather than a low-total trend; and **Under juice** (under at -120 or
   shorter, 234-171 +5.1%) because it was the most volume of the eight at 2.6
   plays a day for the least edge per play, and close to just following the
   book. All three keep their numbers in `allml_systems.RETIRED` so they are not
   rediscovered later and mistaken for something new.

   **How they were found bears on how much to trust them.** The scan tested
   every single and pairwise cell across ~30 derived features, roughly 4,000
   cells per market. About 2,000 beat baseline per market and ~100 cleared
   p<0.05 on chance alone; NONE survived Benjamini-Hochberg (best q ~= 0.18).
   The p-values here are screening statistics, not proof. Every rule above
   additionally beats its blind baseline, holds in both walk-forward halves,
   and has a mechanism.

   **Division home dog (added 2026-09-02, user)** is the one find from the
   sixth scanning pass, which otherwise killed head-to-head history,
   interleague, trip length, rest differential, repeat pitcher-vs-opponent
   and scoring form against the line. It survives leave-one-division-out on
   all six divisions and leave-one-team-out, and its controls are the right
   shape (non-division same band +11.5%/-9.4%, division home favourites
   -0.3%, the away mirror -10.6%, blind home -3.5%). **The case against it is
   the calibration gap**: at +115-129 these teams won 66.7% against a 45.4%
   price, a 21.3-point miss, where the L4 cell was rejected for a 15.9-point
   one. The effect is also a hump in price, dead below +115 and above +150.
   Carded anyway on the user's call at the +115..+149 band.

   **Home dog getaway (added 2026-09-02, user)** has the cleanest mechanism
   of the seven: after a night game both clubs are short on sleep, but one
   sleeps at home and the other packs for the airport, and the market prices
   team quality rather than that. It shows up only where the home side is the
   weaker team (the favourite half is -0.2%). Controls behave -- the same day
   games without the night-before condition return -4.9%, home dogs in day
   games not following one -1.4%, leave-one-team-out holds at all five most
   frequent hosts, and a 2/3-1/3 holdout tested +34.0%. Its calibration gap
   is +11.6 points and the record has probability 0.060 against an
   exactly-right market, which is unremarkable -- the return comes from dog
   prices, not from an implausible win rate. Against it: the price ladder
   inside the cell is not clean, July runs -14%, and n=45 is small.

   **A note on the mirror test that nearly killed it.** The first read
   rejected this because "home club night-then-day -> back away" measured
   -13.2%, which looked like the fatigue only affecting visitors. That test
   was wrong: 172 of the 176 games have BOTH clubs coming off a night game,
   because they played each other, so the mirror was the same games bet the
   other way -- the inverse of the result, not a control. Check what a
   control actually holds constant before trusting it.

   **Home dog + under (added 2026-09-02, user)** is the first PARLAY on the
   board and the only rule whose mirror fails for the right reason. A home
   win means the bottom of the ninth is never played, so home wins skew
   under (league total 8.77 when the home side wins, 9.15 when it loses).
   An away win carries no such property, and away dog +115..+149 parlayed
   with the under is duly dead at -11.1% over 517 games. The edge is not the
   moneyline leg repeating Division home dog: in non-division games that leg
   alone is flat at +1.8% while the parlay returns +40.5%, because these home
   dogs go under 72.7% of the time when they win against 51.6% league-wide.

   Against it: the no-bottom-ninth effect is worth ~2 points league-wide and
   here it is 20, only inside one price band, and the mechanism does not
   explain why +125 differs from +110. Variance is severe -- 13 straight
   losses and a -13.0u drawdown inside a recent 8-week run that finished
   +40u -- and plays cluster by series. **It also assumes the book prices a
   same-game ML+total parlay at multiplied odds; a correlation-priced parlay
   removes exactly the edge measured here.**

   `parlay` is a market type across the whole tier now: the ledger stores the
   combined American price plus each leg, and a pushed total drops that leg
   and reduces the bet to its moneyline at its own price, which is the
   standard book rule and is why the recorded n (248) exceeds the scan's
   push-excluded 243.

   **Three fail their ladder and are carded anyway, on the user's call.** Away
   dog ML is entirely the 9.5 bucket: away dogs run -15.4% at a 9.0 total
   (n=168, more games than the winning cell) and +6.1% at 10+. Home slide ML
   is entirely "exactly L4": L3 is -9.4% (n=118) and L5 -6.4% (n=29), and
   the market prices L2/L3/L5 teams within a few points of their true win
   rate while missing L4 teams by 15.9 points. Both survive every other test
   -- trimming the biggest wins, park exclusion, monthly splits -- which is
   why they are arguable rather than dismissed. They are marked with a
   warning glyph on the tab and `LADDER FAILS` in the module docstring. The
   live record settles it.

   Adding or retiring one of these is a `rule_status.py` edit; the logger,
   the ledger and the dashboard all follow from that one file.

   The season is in the ledger too: `scripts/backfill_allml_systems.py`
   replayed all 636 historical plays as `backfilled` rows, so the tab's date
   filter can show them for past days instead of saying "no plays" while the
   season table underneath claimed 628. Backfilled rows are hindsight and are
   held out of the record entirely -- no units, their own section on the tab,
   their own line in the report -- exactly as the 99 mismatch-ML rows written
   the same way are. That script is a ONE-SHOT tool and is deliberately not in
   the daily workflow: it is idempotent, but running it after a rule changes
   would rewrite history to match the current definition.
7. **Leans are retired**: no half-case totals, no temperature-only reads,
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

## Stacked positions across non-scout systems (2026-09-02)

A "position" is a side two systems can land on together: a team's
moneyline, or one side of a game's total. A parlay counts toward both —
its ML leg to that team, its under leg to the under.

Measured over the settled season, carded non-scout systems only:

| position | depth | games | bets | record | ROI/bet | units/game |
|---|---|---|---|---|---|---|
| moneyline | 1 | 385 | 385 | 179-206 | +29.1% | +0.291 |
| moneyline | 2 | 88 | 176 | 86-90 | +47.6% | +0.953 |
| moneyline | 3 | 8 | 24 | 15-9 | +63.3% | +1.899 |
| over | 1 | 208 | 208 | 121-87 | +11.2% | +0.112 |
| under | 1 | 445 | 445 | 203-242 | +31.4% | +0.314 |
| under | 2 | 8 | 16 | 8-8 | +20.2% | +0.403 |

The moneyline ladder is monotone (29 → 48 → 63%), both walk-forward
halves hold (+47.3% / +51.9%) and all three thirds are positive
(+49.6 / +20.6 / +72.7%). So stacking is not a totals-only effect: it
is strongest on the ML.

Caveat that belongs next to the number: every depth-2 ML stack contains
home-dog-under-parlay, and 66 of 96 are that plus division-home-dog —
the same home dog priced +115 to +149 in a division game. The stack is
therefore mostly one trigger family agreeing with itself, not two
independent reads confirming each other. Treat it as a sharper filter on
the home-dog band, not as evidence that unrelated systems corroborate.

Doubling a stacked position was not tested and is not implied: every
number above is flat stakes on each system's own bet.

## Pickem under → SHADOW from 2026-09-04 (2026-09-03, user)

Not bet from tomorrow's slate on: tracked and logged, no units, until 15-20
plays say card it or drop it. `pickem-under` moved from `CARD_ORDER` to
`SHADOW_ORDER` in allml_systems.py, which is where `rule_status.RULE_STATUS`
derives the non-scout statuses from, so the tab, the logger and the ledger
all follow from that one edit.

**Demoted from a healthy record, not a broken one** — 99-70 +11.3% (n=169),
halves +13.0/+9.5, every month positive but April (-15%, n=22). Nothing
measured turned against it. What is true: it was carded 2026-09-01 WITHOUT
the shadow period this file's own gate requires, exactly as the mismatch ML
was before it went 1-3 and came off. This is that gate applied late rather
than skipped, and it costs almost nothing — its live record is 4 plays and
-0.09u. The one measured mark against it is the conflict split, +13.9%
unopposed (n=125) against +3.7% opposed (n=44).

**Dated, unlike the conflict rewrite.** `SHADOW_FROM` in allml_systems.py
holds `{"pickem-under": "2026-09-04"}`; the logger asks it before writing a
row, so slates through 9/3 still log as card bets and the two pending 9/2
rows settle as the bets they were. Everything else — the tab, the season
table, RULE_STATUS — reads the destination status immediately, because that
is what the rule is from here; only the ledger waits. Delete the entry once
the date has passed; it is inert after that.

## Conflicting total positions — BOTH sides passed (2026-09-03, user)

The mirror of the stacking table above. When two carded systems land on
opposite sides of the same total, neither side is taken. A parlay's under
leg counts as an under for detection, and **the parlay stands down with
them**.

**Applied to the WHOLE SEASON (user, 2026-09-03), history rewritten.** The
parlay half briefly carried a `CONFLICT_PARLAY_FROM = "2026-09-04"` start
date so the ledger would keep the bets actually made; the user asked for
one policy and one record with no date seam in the middle, so the constant
was deleted and `scripts/backfill_conflict_skips.py` marked the history to
match. 110 rows across 54 conflicted games now carry `not_bet` +
`conflict_skip` (was 54, all overs): 53 starter-over-run, 31 pickem-under,
22 home-dog-under-parlay, 4 flag-plays. **+5.71u came out of the live
record**; the rest were backfilled rows, which carry no units either way.
Results and profits are untouched — the rows keep their W-L and lose only
the units, which is what `not_bet` has always meant here.

That script is a ONE-SHOT and is deliberately not in the daily workflow. It
ignores `_locked` on purpose, which is the one thing the daily path must
never do. The pre-rewrite ledger is recoverable from git.

**Detection is NON-SCOUT ONLY (user, 2026-09-03).** It briefly spanned every
carded rule with a total side, which let a scout rule pass a non-scout bet
while the tab's own detector — which only ever sees `SYS.today_plays()` —
showed no conflict at all. SD @ CIN on 9/1 is the row that surfaced it:
flag-plays took the over, so the parlay's under leg was marked not_bet in
the ledger and a +3.28u winner left the record with nothing on the panel to
explain it.

The scoping is not just consistency between two surfaces. The families are
built to be independent — allml_systems reads none of the mismatch model, so
agreement between them is a *second opinion*. Two independent reads
disagreeing is not the correlated cancellation the pass rule measured, and
the -4.1%/-3.8% that justified passing both sides came entirely from inside
the non-scout family. Cross-family disagreement was never measured.

Releasing the 4 cross-family games put **+2.05u back** into the live record
(6 rows: 4 flag-plays, 1 parlay, 1 starter-over-run). The backfill is
two-way now — narrowing the rule gives rows back, or the ledger keeps bets
passed under a definition that no longer exists — and it reads each rule's
status AS OF THE ROW'S DATE, honouring `SHADOW_FROM`. Without that, shadowing
pickem-under would have retroactively released 63 rows it had legitimately
been half of.

Before this the over was passed and the under was kept. Passing the under
too **costs 17-12 +12.6% (+3.66u over 29 settled plays)** — the over was
already being passed, so this is a cost, not a saving, and the card tier's
ROI only rises +27.6% -> +28.0% because that cell sits below the tier
average. Units go down. Recorded plainly because the change was made on
judgement, not on a number that favoured it.

What it buys, measured across carded and shadow systems together:

| on a 1-over-vs-1-under game | n | ROI |
|---|---|---|
| take the under | 150 | -4.1% |
| take the over | 150 | -3.8% |
| take the ML dog instead | 148 | +2.6% (p=0.44) |
| pass | — | 0.0% |

Two systems disagreeing cancel to the vig, and there is no side to be on.
The moneyline dog was tested as the alternative and is worse: **-12.8% on
the carded conflicts** against a -3.1% blind dog, and -1.9% over the wider
set at p=0.44 — no better than the -1.5% that games with an UNOPPOSED total
rule return, so the disagreement carries no moneyline information either.

Every system is worse when contradicted, which is the finding underneath
all of this — unopposed vs opposed: starter-over-run +23.4% -> -9.7%,
low-line-over +15.2% -> -13.7%, monday-over +12.4% -> -9.2%, pickem-under
+13.9% -> +3.7%, under-juice +6.6% -> -1.9%. In aggregate over unopposed
+17.3% (n=361) against -10.8% opposed; under unopposed +10.7% (n=585)
against +1.8% opposed.

**The parlay was exempt for one day and no longer is (2026-09-04, user).**
The exemption rested on the under leg graded STRAIGHT returning +27.7%
(n=57) opposed against +9.9% (n=192) unopposed — the one position on the
board that wanted the disagreement, and only measurable once the ML leg was
stripped. But that is a different bet from the parlay, and the parlay
itself measures the ordinary way round: **+32.1% (n=20) on conflicted games
against +46.6% (n=234) unopposed.** Passing it costs +6.42u over those 20
plays and takes the card tier from +271.87u to +265.45u.

Worth keeping straight, because the two numbers describe the same games and
point opposite ways: the under leg alone beats its own baseline when
opposed; the parlay as priced does not beat its own. The leg's baseline is
a straight under (+9.9%), the parlay's is a two-leg payout (+46.6%).

Reverting is a two-line change (`_mark_conflicts` in
build_allml_systems_table.py, `drop_conflicting_totals` in
log_daily_qualifiers.py); the ledger flag is `conflict_skip` on both rows.

## Ledger rows track the market until first pitch (2026-09-02, user)

A logged row is not a snapshot of the first quote of the morning. It
follows the book and then freezes:

- **Re-price, don't duplicate.** The idempotency key `sig_of` is
  `(date, rule, market, game, side)`. The total is deliberately NOT in
  it. It used to be, so a line move between two runs of the workflow
  read as a different bet — on 9/2 the book moved DET/MIN from 9 to 8.5
  and the ledger carried two pickem-under rows for one play, staking 2u
  where 1u was made. Six rows that day across five rules.
- **Overwrite with the latest quote.** A re-run updates the row to what
  the market shows now. The number you would actually get is the latest
  one, not whichever the first run happened to catch.
- **Lock at first pitch.** Once `commence` has passed, the row is frozen
  — price, line and play text — however many times the workflow runs
  after it. A graded row is locked too, and a row with no `commence` is
  treated as locked rather than risk rewriting a game in progress.
- **Only market fields move** (`PRICE_FIELDS`). Result, profit, stake,
  `not_bet` and anything hand-edited belong to the ledger, not the book,
  and a re-price never touches them.

Known exception, left alone deliberately: 2026-07-19 WSH @ OAK carries
two backfilled mismatch-ml rows on the same side (WSH ML −146), one from
the tail arm and one from the fade arm, both WIN +0.68. Whether a rule
firing twice on one side is one position or two is a rules question, not
a bug, so it stands until someone decides it. It is the only such pair
in the season.
