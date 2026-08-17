# MLB Fade — First 5 Innings instead of Full Game

**Date:** 2026-08-16
**Status:** Analysis — no pipeline change made
**Question:** can the fade-list model bet the opponent's **first 5 innings** (F5)
instead of the full-game moneyline, and is that better?

Reproduce with `cd MLBstrikeouts && python -m scripts.analyze_fade_f5`.

## Short answer

**Yes, it is possible** — the trigger, the grading data, and the odds transport all
extend to F5 without new infrastructure. **Whether it is better is not settled by
the numbers below**, and the current full-game ledger cannot settle it because that
ledger is inflated by retroactive fade-list construction. Run the real F5 backfill
in shadow before moving any money off the full game.

## 1. The structural case is strong

F5 is almost exactly the window the fade thesis is about. Across the 606 graded
fade bets, matched to each faded starter's actual outs recorded (`mlb-props.json`
`actual_outs`):

| faded starter | |
|---|---|
| mean / median IP | 5.16 / 5.00 |
| reached the 5th inning | 87.8% |
| completed 5 innings | 69.1% |
| gone before the 4th | 4.8% |
| **F5 outs actually covered by the fade starter** | **13.8 of 15 (92.1%)** |

Betting the full game buys 92% signal plus four innings of bullpen, defense, and
pinch-hitting that the fade list says nothing about. F5 buys the signal and
discards the noise. That is the whole argument, and it is a good one.

## 2. The full-game baseline is contaminated — do not calibrate on it

`mlb-fade-ml.json` reports 449-157 (74.1%), +302.85u, ROI +40.1%. Against the
de-vigged FanDuel closing line those same teams were 50.7% to win. A 23.4-point
edge over closing on 606 bets is **11.5 sigma**. That is not an edge, it is a
data artifact.

Splitting the ledger by how each bet was priced isolates it:

| sample | n | record | win% | de-vigged market | edge | ROI |
|---|---|---|---|---|---|---|
| **live** (`source: fanduel_api`, priced that day) | 109 | 72-37 | 66.1% | 52.8% | +13.3pp (2.8σ) | **+20.3%** |
| **backfill** (`source: oddsapi_fanduel`) | 496 | 376-120 | 75.8% | 50.2% | +25.6pp | +44.7% |
| all | 606 | 449-157 | 74.1% | 50.7% | +23.4pp | +40.1% |

By month: April +46.1%, May +43.9%, June +44.5%, July +37.5%, **August +11.6%** —
ROI decays monotonically as the sample moves from backfilled to live.

The cause is visible in git: `fade_list.py` was first committed **2026-08-10** and
the list has grown from 21 names to 69, but `ml_backfill.py` re-grades games back to
**2026-04-05** against whatever the list holds today. April games are being bet by
a list assembled in August with knowledge of how those pitchers' seasons went. The
grading itself is sound — all 605 gradeable bets match the cached final scores
exactly, and every price matches the cached closing line — the lookahead is in *which
games got bet*, not in how they settled.

**Everything below is therefore calibrated on the 109 live bets only.** Note that
even +13.3pp over closing is a big claim on n=109; treat it as an optimistic ceiling.

## 3. Projected F5 performance

Method (`scripts/analyze_fade_f5.py`): de-vig each game's closing two-way ML, solve
a Poisson run model against the closing total (λ_home + λ_away = total line) that
reproduces that win probability, then calibrate a single run-differential error
`delta` so the model's mean win probability matches the realized rate. For the live
sample `delta = 1.035` runs — the market was wrong by about a run of expected margin
per fade game. The model reproduces the ledger's full-game ROI to within 0.3pp
(20.6% modelled vs 20.3% realized), so the machinery is sound; the open question is
only where in the game that run lives.

**Ties through five settle as pushes** on the two-way F5 moneyline: the stake comes
back, so a tie is neither profit nor risk and drops out of both sides of the ROI
ratio. F5 prices are modelled as the market's own view, conditional on a decision,
plus a 4.5% two-way hold. Full-game baseline to beat: **+20.3% ROI**.

| share of edge in innings 1-5 | F5 W | push | L | win ex-push | ROI | vs full game |
|---|---|---|---|---|---|---|
| uniform across the game (null) | 53.3% | 17.7% | 29.0% | 64.8% | **+17.3%** | −3.0pp |
| 70% starter | 55.8% | 17.4% | 26.8% | 67.6% | +22.1% | +1.8pp |
| 85% starter | 58.6% | 16.9% | 24.5% | 70.5% | +27.3% | +7.0pp |
| 100% starter (thesis) | 61.3% | 16.4% | 22.3% | 73.3% | **+32.3%** | +11.9pp |

Three things fall out:

1. **The crossover is 64.8% attribution — an ex-push F5 win rate of 66.5%**
   (63.5% / 74.6% on the full sample; the attribution figures agree,
   which is reassuring since they are calibrated on very different deltas). If the
   fade edge is purely a starting-pitcher effect, F5 lifts ROI from +20.3% to +32.3%.
   If the edge is really a team-quality effect that happens to correlate with bad
   starters, F5 is slightly *worse* than the full game. The 92% outs-coverage figure
   argues for high attribution, but it does not prove it — a fade list of bad pitchers
   is also a list of bad teams. **This one number is the decision.**
2. **Pushes are free, but they cost volume.** ~17% of these games are level after
   five. With ties refunded that is not a tax on ROI — it is why the ex-push win rate
   (64.8-73.3%) lands near the full game's 66.1% rather than below it — but it does
   mean roughly one bet in six returns no action, so the same edge needs ~20% more
   bets to produce the same units. Variance drops accordingly.
3. **Market form barely matters.** Pricing the same slate as a three-way (tie loses,
   6% hold) gives +17.6% / +35.4% across the same attribution range — within a few
   points of the push market at every row. The two are economically near-equivalent,
   so this conclusion survives if FanDuel's F5 turns out to be three-way after all.

Sensitivity, 100% attribution, live sample: ROI stays in **+29.3% to +34.6%** across
run-share 0.54-0.59 and hold 3.5-6.0%. Under the null it stays near or below the
full-game baseline throughout. The conclusion is not sensitive to those two knobs —
it is sensitive to attribution, which only real F5 results can measure. Results are
also stable across staking conventions (house risk-to-win-1u +32.3%, flat 1u +34.3%),
so the choice of stake plan does not drive the answer.

**Caveat on the price model.** Real F5 lines are not the full-game line scaled — books
weight the starter more heavily over five innings. If the market overrates the fade
starter, the real F5 price on our side should be *longer* than modelled here, which
makes these projections conservative. But that is an argument, not a measurement.
Only real `h2h_1st_5_innings` snapshots settle it.

## 4. Feasibility — what an F5 build actually needs

Nothing structural blocks it. Item by item:

- **Trigger** — unchanged. Same fade list, same `mlb-props.json` starter slate, same
  mutual-fade skip.
- **Grading — free.** The schedule call in `sources/mlb_schedule.py` already hydrates
  `linescore`; `linescore.innings[]` carries per-inning `home.runs` / `away.runs`.
  Sum innings 1-5 and compare. No new endpoint, no new credits.
- **F5 settlement rules** — two settlement paths the full-game model does not have:
  - *Tie through five is a PUSH* — stake refunded. This is a third result alongside
    WIN/LOSS, distinct from VOID: a push is a real settled game that happens to
    return the stake, and it belongs in the bet log and the displayed record
    (`72-37-15` style) even though it contributes nothing to units or to the ROI
    denominator. Expect roughly one bet in six.
  - *Shortened games are VOID* — a bet has action once 5 innings are complete, or 4.5
    with the home team ahead (home not batting in the bottom of the 5th). Otherwise
    void, keyed off `linescore.currentInning` and `innings[4].home` being absent.
- **Odds — the one real unknown.** Two transports, matching the existing ones:
  - *Live:* FanDuel's public API already backs `fetch_fanduel_mlb_ml` off the
    `MONEY_LINE` marketType. The same event page carries the first-5-innings
    markets, so live capture is likely free like the full-game one — **the exact
    marketType string must be confirmed against a live payload**, which could not be
    done here (no egress in this session).
  - *Historical:* The Odds API `h2h_1st_5_innings` for `baseball_mlb`. This is an
    *additional* market, which on this provider means the per-event odds endpoint —
    the same path `odds_theoddsapi.py` already uses for props, costed by that file's
    own accounting at 1 (events list) + N_games credits per date. A season backfill
    is in the low thousands of credits, in line with what the full-game backfill cost.
  - Ties push, so the market is two-way and the existing both-side ML cache shape
    carries over unchanged — one `f5_home_ml` / `f5_away_ml` pair per game.
- **Staking** — the house convention carries over unchanged. F5 prices are shorter
  than the full game's (−82 average for this slate vs −128), so the risk-to-win-1u
  branch still dominates, just at smaller stakes per bet.

Suggested shape, mirroring the existing model rather than replacing it:
`sources/odds_f5_theoddsapi.py` + an `fetch_fanduel_mlb_f5` extension →
`data/odds_cache/mlb_f5/` → `fade_f5_backfill.py` → `mlb-fade-f5.json` → a dashboard
tab. Additive; the full-game model keeps running.

## 5. Recommendation

1. **Fix the lookahead first.** Stamp each fade-list entry with the date it was added
   and have `ml_backfill.py` only bet games on or after that date. Without this, no
   backfill — full game or F5 — measures anything real, and the two cannot be
   compared to each other.
2. **Backfill F5 grading on the existing bet log** (free — linescores only). That
   measures attribution directly: how often did the opponent lead after five, and how
   often was it level, on the same games?

   **The target: an ex-push F5 win rate above 66.5%** (on the live sample; 74.6% on
   the contaminated full sample, which is why step 1 comes first). That is the win
   rate at the crossover — clear it and F5 beats the full game, miss it and the full
   game is the better bet. No odds purchase required to measure it, because the
   crossover can be expressed as a win rate rather than an ROI.
3. **Only then buy F5 odds** for the dates that survive step 1, and run the real ROI
   comparison.
4. **Shadow before switching.** Run F5 alongside the full game for a few weeks the
   way `track_threshold_shadow.py` does. On the live sample the honest full-game
   number is +20.3% on 109 bets, not +40% on 606 — the bar F5 must clear is lower
   than the dashboard suggests, but so is the confidence in either figure.

## Out of scope

- Building the F5 pipeline (this is analysis only).
- F5 totals / F5 run line as separate products.
- Fixing the fade-list lookahead — flagged here, worth its own change.
