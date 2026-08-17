# MLB Fade — First 5 Innings instead of Full Game

**Date:** 2026-08-16
**Status:** Analysis — no pipeline change made
**Question:** can the fade-list model bet the opponent's **first 5 innings** (F5)
instead of the full-game moneyline, and is that better?

Reproduce with `cd MLBstrikeouts && python -m scripts.analyze_fade_f5` and
`python -m scripts.fade_f5_attribution`.

## Short answer

**Possible: yes.** The trigger, the grading data, and the odds transport all extend
to F5 without new infrastructure.

**Better: no — don't switch.** F5 only wins if roughly **65%** of the fade edge is
produced while the faded starter is on the mound. Measured, that share is **59.8%**
(90% CI 51.8-67.8%) on the full sample and **44.8%** on the clean live sample. Both
sit below the crossover, so F5 projects to give up roughly 2-7pp of ROI. A second,
independent proxy — who won the head-to-head starter duel — agrees, falling 6-7pp
short of what the crossover requires.

The reason is the finding underneath: **the fade edge is not a starting-pitcher
effect.** It is spread across the game roughly in proportion to innings, which is
what a team-quality signal looks like. Fade-list teams concede +0.72 runs over the
market's expectation *after* the faded starter leaves. Their bullpens are bad too,
and the full game collects on that; F5 throws it away.

## 1. The structural case looks strong (and turns out to be wrong)

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
pinch-hitting that the fade list says nothing about. F5 buys the signal and discards
the noise. That is the argument for F5, and on workload grounds it is a good one.

It fails anyway, because those extra four innings turn out not to be noise — §4
measures real edge in them. Coverage of the *starter* was never the binding
constraint; where the *edge* lives is.

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

## 4. Measured attribution — the crossover is not cleared

`scripts/fade_f5_attribution.py` estimates attribution instead of assuming it, using
every start's line from `data/pitcher_cache/mlb/game_logs_2026.json` (starters *and*
relievers, 15,659 appearances, complete staffs). For each fade game it decomposes the
run differential against the closing line into the span the faded starter pitched and
everything after, using the market's own λ as the per-out expectation:

```
starter window  S_sp = (runs_off_fadeSP  - lam_bet*o_f/27)
                     - (runs_off_ourSP   - lam_opp*o_b/27)
rest of game    S_bp = same, over the remaining outs
identity        S_sp + S_bp == realized margin - market expected margin
```

The identity is asserted per game, so the split cannot drift. Unearned runs (~8.2%
league-wide, confirmed against final scores) are allocated to the starter by his share
of the staff's outs rather than dumped into the post-starter bucket.

| | live (n=104) | full season (n=588) |
|---|---|---|
| total edge over market | +0.744 runs/game | +1.793 runs/game |
| **while the faded starter pitched** | **+0.333** | **+1.072** |
| **after he left** | **+0.411** | **+0.721** |
| measured attribution | **44.8%** | **59.8%** |
| 90% bootstrap CI | −27.9% .. 117.5% | 51.8% .. 67.8% |
| crossover required | 65.1% | 63.5% |
| projected F5 ROI at measured | +12.4% | +38.1% |
| full-game ROI | +19.5% | +40.4% |
| **verdict** | **full game, −7.2pp** | **full game, −2.3pp** |

Three reasons this is a real answer and not just a noisy one:

1. **The measured share is statistically indistinguishable from the null.** Uniform
   attribution — the edge spread evenly across the game — is 56.5%. The full sample
   measures 59.8% ± 8. There is no evidence the edge concentrates in the starter's
   innings, which is precisely the premise F5 needs.
2. **The contamination biases attribution *upward*, not down.** Retroactive fade-list
   selection picks pitchers whose seasons went badly, and their bad outcomes occur in
   their own innings. So 59.8% is best read as a *ceiling*, and it is already below
   the 63.5% crossover. Only the upper CI bound (67.8%) clears it, and only by
   +2.5pp.
3. **An independent proxy agrees.** Scoring each game by the head-to-head starter duel
   — did our offense out-score theirs against the opposing starter — gives an ex-push
   record of 344-161-83, a **68.1%** win rate where the crossover needs **74.6%**. On
   the live sample it is 59.3% against 66.5% needed. Restricting to games where both
   starters completed five innings, the cleanest available analogue of the actual F5
   window, barely moves it (67.4%, n=297). Two methods built on different arithmetic
   miss the bar by the same 6-7pp.

**What would still change the answer.** This measures "while the faded starter
pitched", not literally innings 1-5, and it uses run attribution rather than a
linescore. The definitive test is grading the existing bet log on actual runs through
five — free, since `linescore.innings[]` needs no odds — and it could not be run here
because egress to `statsapi.mlb.com` is blocked in this session. Given two
independent proxies landing 6-7pp short, expect it to confirm rather than overturn.

The one caveat that points the other way is the price model: real F5 lines are not the
full-game line scaled, since books weight the starter more heavily over five innings.
But that cuts against F5 here — if the market's starter view is roughly *right*, which
low attribution implies, then a starter-weighted F5 line is more accurate than the
scaled proxy, and our F5 price would be worse than modelled, not better.

## 5. Feasibility — what an F5 build would need

Recorded for completeness; the analysis above says don't build it yet.

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

## 6. Recommendation

**Keep betting the full game.** F5 is buildable and would work — it clears break-even
comfortably at every attribution level — it just works *less well* than what is
already running, because the fade signal collects on bad bullpens as well as bad
starters and F5 discards half of that.

Two things are worth doing anyway:

1. **Fix the lookahead.** Stamp each fade-list entry with the date it was added and
   have `ml_backfill.py` only bet games on or after that date. The dashboard is
   currently advertising +40.1% ROI where the honest, prospective number is +20.3%.
   That matters independently of F5: it is the figure any future comparison —
   thresholds, staking, a new market — gets measured against, and right now every such
   comparison starts from a number inflated by ~20pp.
2. **Confirm with the free linescore regrade** when egress allows. Grade the existing
   bet log on runs through five and check the ex-push win rate against **66.5%** (live)
   / **74.6%** (full sample). Costs nothing — no odds required, because the crossover
   is expressible as a win rate. Both proxies here predict it lands 6-7pp short.

If F5 gets revisited later, the thing to look for is a *sub-list*: the measured
attribution is an average, and it is plausible that some faded arms (short-outing,
high-walk types on teams with decent bullpens) carry most of their edge in the first
five while others do not. Splitting attribution by pitcher needs more games per arm
than this season provides, but it is the version of the idea the data does not rule
out.

## Out of scope

- Building the F5 pipeline — the analysis says don't, for now.
- F5 totals as a separate product.
- Fixing the fade-list lookahead — flagged here, worth its own change.
- Per-pitcher attribution splits (not enough games per arm this season).
