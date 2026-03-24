# pyNFL — NFL Score Projection Pipeline Design Spec

**Date:** 2026-03-24
**Status:** Draft
**Author:** Claude (Staff Engineer)
**Stakeholder:** Henry Pham (CEO/CFO)

---

## 1. Goal

Build `pyNFL/`, an NFL score projection pipeline that fits into the existing monorepo alongside pyNBA, pyNCAA, and pyFull. The pipeline ingests play-by-play EPA data via nfl_data_py, applies injury-conditional adjustments, and produces spread projections for weekly NFL matchups. Output is a Python dashboard tab — no email, no Discord.

## 2. Constraints

- **Do not modify** any existing pyNBA, pyNCAA, pyFull, jsNBA, jsNCAA, or jsFull code (except deleting recalculate files repo-wide)
- **Reuse** `core/` for Kalman filter, self-tune, calibration, store
- **No PFF** — no paid data subscriptions
- **No recalculate files** — delete all existing `recalculate.py` and `recalculate.mjs` across the repo
- **The Odds API** — reuse existing API key, NFL sport key
- **Dashboard only** — no email, no Discord distribution

## 3. Architecture

Hybrid approach: reuse `core/` for sport-agnostic infrastructure, build NFL-specific model engine, injury layer, and data sources.

### 3.1 New Files

```
core/engines/nfl.py                  # NFL config — sets weight keys, Kalman hyperparams, Bayes hyper for core/self_tune.py

pyNFL/
  ├─ __init__.py
  ├─ scripts/
  │   ├─ __init__.py
  │   ├─ run_weekly.py               # Orchestrator (--stage flag: fetch, injuries, project, grade, all)
  │   ├─ defaults.py                 # NFL weight keys (wPassOff, wRushOff, wPassDef, wRushDef, wPace, wRZ, hfa), hyperparams, thresholds
  │   ├─ model_engine.py             # NFL-specific engine (NOT a thin wrapper — standalone ridge regression on EPA features)
  │   ├─ injury_layer.py             # Positional injury deltas
  │   ├─ lr_model.py                 # Logistic regression confirmation layer
  │   ├─ lr_backtest.py              # LR backtesting (mirrors pyNBA/scripts/lr_backtest.py)
  │   ├─ kalman_state.py             # Thin wrapper over core/kalman_state.py
  │   ├─ self_tune.py                # Wrapper over core/self_tune.py — maps NFL weight keys to Bayesian updater
  │   ├─ calibration.py              # Thin wrapper over core/calibration.py
  │   ├─ store.py                    # Thin wrapper over core/store.py — outputs to data/nfl.json for dashboard
  │   ├─ backfill_last_n_weeks.py    # Historical replay, sequential residualVar
  │   └─ sources/
  │       ├─ __init__.py
  │       ├─ nflfastr.py             # nfl_data_py: play-by-play EPA, CPOE, air yards
  │       ├─ nfl_stats.py            # Aggregate team-level stats from play-by-play (+ derived pressure rate, coverage EPA)
  │       ├─ injuries.py             # ESPN injury API: practice designations (DNP/Limited/Full)
  │       ├─ odds_theoddsapi.py      # The Odds API, NFL sport key
  │       ├─ pfr_stats.py            # Pro Football Reference: snap counts, target shares (optional — graceful fallback to nflfastR-only if blocked)
  │       ├─ nextgenstats.py         # NFL.com Next Gen Stats: tracking data (optional — graceful fallback if unavailable)
  │       └─ espn_scoreboard.py      # Final scores for grading
  ├─ data/
  │   ├─ team_states/                # Kalman state JSON per team (saved after every game window)
  │   ├─ player_epa/                 # Per-player EPA snapshots (starters + backups)
  │   ├─ injuries/                   # Weekly injury designations
  │   ├─ odds/                       # Market lines per matchup
  │   ├─ lr_models/                  # Trained LR model persistence
  │   ├─ weights.json                # Trained ridge + Bayesian weights from backfill
  │   ├─ thresholds.json             # Calibrated sDiff thresholds from backfill
  │   └─ nfl.json                    # Dashboard output (read by PythonDashboard)
  ├─ requirements.txt
  ├─ .env.example
  └─ README.md
```

### 3.2 Modified Files

- `PythonDashboard/` — Add NFL tab reading from `pyNFL/data/nfl.json`. Weekly picks, P&L, injury adjustments, model vs market, rolling hit rate.
- `core/engines/__init__.py` — Register nfl engine (if needed)

### 3.3 Deleted Files

All `recalculate.py` and `recalculate.mjs` files across the repo:
- `pyNBA/scripts/recalculate.py`
- `pyFull/scripts/recalculate.py`
- `jsNBA/scripts/recalculate.mjs`
- `jsFull/scripts/recalculate.mjs`

### 3.4 Architecture Clarification: What Comes From core/ vs pyNFL/

`core/` is reused for **sport-agnostic infrastructure only**:
- `core/kalman_state.py` — Kalman filter state management (initialize, drift, batch update, save/load). Sport-agnostic.
- `core/self_tune.py` — Bayesian weight updater + gradient descent. Sport-agnostic — weight keys are configured by the wrapper, not hardcoded.
- `core/calibration.py` — Calibration table builder. Sport-agnostic.
- `core/store.py` — JSON persistence for runs/history. Sport-agnostic.

`pyNFL/scripts/model_engine.py` is **NOT** a wrapper around `core/model_engine.py`. The NBA model engine uses weighted stat deltas (dTS, dTO, dORR, dNET); the NFL engine uses ridge regression on EPA features. These are fundamentally different projection approaches. `pyNFL/scripts/model_engine.py` is a standalone NFL-specific engine.

`core/engines/nfl.py` configures the core modules with NFL-specific weight keys (`wPassOff`, `wRushOff`, `wPassDef`, `wRushDef`, `wPace`, `wRZ`, `hfa`), Kalman hyperparameters, and Bayesian hyper settings. It does NOT configure `core/model_engine.py`'s `proj_score`.

## 4. Model Engine

### 4.1 Feature Engineering

All features derived from nfl_data_py play-by-play with exponential decay weighting (~0.85 decay factor per week). A Week 12 team profile is dominated by the last 4-5 weeks, not the full season average.

**Tier 1 — Efficiency (highest signal):**
- Offensive EPA/play (pass split)
- Offensive EPA/play (rush split)
- Defensive EPA/play (pass split)
- Defensive EPA/play (rush split)
- Success rate (offense and defense)

**Tier 2 — Passing game (drives ~70-80% of scoring variance):**
- CPOE (Completion % Over Expected)
- Air yards / aDOT (average depth of target)
- Pressure rate allowed (O-line)
- QB EPA/dropback

**Tier 3 — Defensive detail:**
- Pressure rate generated
- Red zone defense efficiency (TD% allowed)
- Coverage EPA allowed (derived from play-by-play targeting data)

**Tier 4 — Situational:**
- Third down conversion rate (both sides)
- Turnover-adjusted efficiency (strip out fluky turnover luck)
- Red zone TD% (offense)
- Pace (plays/game)

**Supplementary sources (free, optional with graceful fallback):**
- Pro Football Reference: snap counts, target shares. **Risk:** PFR actively blocks scrapers. If blocked, derive snap counts and target shares from nflfastR play-by-play alone (sufficient for Tier 1-2 features).
- Next Gen Stats (NFL.com): completion probability, separation, speed metrics. **Risk:** No stable public API. Marked as optional Tier 2 enrichment — pipeline runs without it.

### 4.2 Projection Formula

Ridge regression trained on 2-3 seasons of historical data. Features are Kalman-filtered team states, not raw weekly stats.

The regression learns coefficients empirically from score margins rather than using hand-tuned weights. The feature set includes:
- Offensive passing/rushing EPA
- Defensive passing/rushing EPA
- Pace interaction terms
- Home field advantage
- Injury deltas (post-projection additive)

**Ridge model persistence:** Trained model saved to `pyNFL/data/weights.json` during backfill. In-season, the self-tuner adjusts these weights incrementally — the ridge regression provides the initial starting point, and the Bayesian updater refines from there. The ridge model is NOT retrained in-season; only the Bayesian posterior evolves.

**Cross-validation:** Ridge regularization parameter (alpha) selected via 5-fold CV during backfill training. Stored in `thresholds.json`.

Self-tuner refines coefficients in-season via all three signals from `core/self_tune.py`:
- **Signal 1:** Bayesian posterior weight updates on NFL weight keys (`wPassOff`, `wRushOff`, `wPassDef`, `wRushDef`, `wPace`, `wRZ`, `hfa`)
- **Signal 2:** Profitability-driven probability threshold tuning
- **Signal 3:** Gradient descent on constant and key coefficients (passOff weight, pace adjustment)

**Feature matrix construction:** `model_engine.py` builds the X matrix by pulling Kalman-filtered team states from `kalman_state.py`, applying injury deltas from `injury_layer.py`, and assembling the feature vector per matchup. The ridge coefficients (from `weights.json`) produce projected scores. Edge detection (sDiff) and P(cover) are computed within `model_engine.py`.

### 4.3 Recency Weighting

The Kalman filter provides natural recency weighting through process noise decay. On top of that, stat aggregations use exponential decay (~0.85/week):

- **Weeks 1-2:** Projections lean heavily on prior-year data as priors, wide confidence intervals
- **Weeks 4-6:** In-season data dominates but with wide error bars
- **Week 8+:** Model driven by current-season performance with tight Kalman estimates

### 4.4 Kalman State

**State vector per team:** offensive EPA/play, defensive EPA/play, pace (plays/game), red zone TD%

- Process noise: higher than NBA to account for weekly variance and injury regime changes
- Measurement noise: scaled by games played (early season = high noise)
- State saved after every game window (not just weekly — Thursday/Sunday/Monday games each trigger a save)

### 4.5 Edge Detection

Model projected spread vs market spread. An edge is flagged when sDiff exceeds a threshold calibrated via backfill.

- **Sizing:** 1u flat per pick, to-win-1u at -110 (WIN = +1u, LOSS = -1.1u)
- **Burn-in:** 1 week (backfill provides trained starting point)

## 5. Injury Adjustment Layer

The pipeline's primary edge. Most public EPA models use season-long averages with no injury adjustment.

### 5.1 Delta Framework

```
adjustedTeamEPA = baseTeamEPA + sum(injuryDeltas)
injuryDelta_i = (backupEPA - starterEPA) * positionalWeight * snapShareFactor
```

Positional weights calibrated from backfill variance decomposition. Directional ordering: QB >>> Edge/WR1 > CB1 > OL.

### 5.2 Position-Specific Logic

**QB (largest lever):**
- Replace team passing EPA with backup QB's historical EPA/dropback
- If backup has <30 dropbacks, blend with league-average replacement-level prior
- Downgrade entire offense — defenses stack the box against weak passers
- Increase projected rush rate in game script

**WR1 / Pass Catcher:**
- Redistribute target share to remaining weapons
- 10-20% passing efficiency haircut when WR2 steps into WR1 role
- High-concentration offenses (>30% target share to one player) suffer more

**Pass Rusher:**
- Estimate team pressure rate drop from individual's contribution %
- Reduced pressure increases opponent QB EPA/dropback

**CB1 / Secondary:**
- Substitute CB2's coverage EPA and passer rating allowed when targeted
- Safety injuries degrade entire coverage shell (~10-15% team pass defense EPA)

**Offensive Line:**
- Bump defensive pressure rate input upward
- Reduce QB efficiency proportionally
- Interior vs edge distinction: interior affects pass + run, tackle primarily affects passing

### 5.3 Sample Size Handling

If a backup has insufficient sample (<30 dropbacks for QB, <100 snaps for others), blend their EPA with a positional league-average replacement-level prior. Blend ratio scales with sample size — more data = more trust in individual estimate.

### 5.4 Multi-Injury Compounding

When 2+ starters on the same unit are out, apply compounding multiplier (1.1-1.2x). A backup QB behind a backup tackle is worse than the sum of individual deltas.

### 5.5 Questionable Players

Probability-weighted deltas. Questionable = ~50% of full delta applied.

### 5.6 Data Flow

1. `injuries.py` fetches from ESPN injury API (`site.api.espn.com/apis/site/v2/sports/football/nfl/injuries`), classifies: IN / QUESTIONABLE / OUT
2. `injury_layer.py` maps designations to EPA deltas using player snapshots from nflfastr (primary) + PFR snap counts (optional enrichment, graceful fallback)
3. Deltas applied additively on Kalman-filtered team baseline

## 6. LR Confirmation Layer

Logistic regression model trained on backfill features as an ensemble check on the Bayesian projection.

- Same architecture as pyNBA/pyFull `lr_model.py`
- Features: team EPA differentials, injury delta magnitude, market line, sDiff, pace differential, home/away
- Trained on historical pick outcomes from backfill
- Acts as confirm/reject gate — picks agreed on by both models historically hit at higher rate
- Retrained periodically as new game results accumulate

## 7. Data Sources

| Source | Data | Cost |
|--------|------|------|
| nfl_data_py (nflfastR) | Play-by-play EPA, CPOE, air yards, WPA, passer/receiver/rusher IDs | Free |
| The Odds API | Opening/closing lines, NFL sport key (existing API key) | Existing |
| Pro Football Reference | Snap counts, pressure %, target shares | Free |
| Next Gen Stats (NFL.com) | Completion probability, separation, speed metrics | Free |
| ESPN Scoreboard | Final scores for grading picks | Free |
| ESPN/NFL.com injury reports | Wed-Fri practice designations (DNP/Limited/Full) | Free |

## 8. Backfill

Same architecture as NBA's `backfill_last_n_days.py`, adapted to weekly NFL cadence.

- Pull 2-3 seasons of play-by-play via nfl_data_py
- Historical closing lines via The Odds API batch endpoint
- Replay week-by-week sequentially
- **ResidualVar computes and updates naturally each week** — no freezing, no lookahead. Each week processes, residualVar updates from that week's results, next week uses the updated value
- Calibrate: sDiff thresholds, positional injury weights, ridge regression coefficients
- Train LR model on backfill outcomes
- Output: trained `weights.json`, `thresholds.json`, LR model as starting point for live season

## 9. Dashboard

Extend `PythonDashboard/` with an NFL tab. Same dark theme as existing NBA views.

**Views:**
- Weekly picks table: projected spread, market spread, sDiff, LR confirmation, pick, result
- Season-to-date P&L tracking: 1u flat, cumulative units chart
- Injury adjustments applied per game (which players out, delta applied)
- Model vs market comparison scatter
- Rolling hit rate by week
- Calibration table (predicted vs actual hit rates by confidence bucket)

## 10. Scheduling

*Deferred — to be designed after core pipeline is validated.*

GitHub Actions will be the target (matching existing repo pattern). Weekly cycle with multiple update windows aligned to NFL practice reports and game windows.

## 11. Cleanup

Delete all recalculate files across the repo:
- `pyNBA/scripts/recalculate.py`
- `pyFull/scripts/recalculate.py`
- `jsNBA/scripts/recalculate.mjs` (if exists)
- `jsFull/scripts/recalculate.mjs` (if exists)
- `pyNCAA/scripts/recalculate.py` (if exists)
- `jsNCAA/scripts/recalculate.mjs` (if exists)

## 12. Tech Stack

- **Python 3.10+**
- **nfl_data_py** — nflfastR Python wrapper
- **NumPy, SciPy, scikit-learn** — statistical modeling, ridge regression, logistic regression
- **BeautifulSoup, Requests** — web scraping (PFR, Next Gen Stats, injuries)
- **python-dotenv** — environment variable management
- **Existing core/ modules** — Kalman filter, Bayesian self-tune, calibration, store

## 13. Environment Variables

```
ODDS_API_KEY=           # Existing The Odds API key
```

## 14. Offseason Handling

Between seasons, Kalman states are decayed heavily toward league-average priors (not fully reset — prior-year performance has predictive value for Weeks 1-2). Decay factor calibrated during backfill across season boundaries. Player EPA snapshots are cleared and rebuilt from new season's play-by-play.

## 15. Error Handling & Data Source Fallbacks

The pipeline must not fail if a supplementary data source is unavailable:

| Source | Fallback if unavailable |
|--------|------------------------|
| nfl_data_py | **Hard dependency** — pipeline cannot run without it |
| The Odds API | **Hard dependency** — no odds = no edge detection |
| ESPN Scoreboard | **Hard dependency** — no scores = no grading |
| ESPN Injury API | Proceed without injury deltas (all deltas = 0). Log warning. |
| Pro Football Reference | Skip PFR enrichment, use nflfastR-derived snap/target data only |
| Next Gen Stats | Skip NGS enrichment entirely, no impact on Tier 1-2 features |

## 16. Open Questions (Future Work)

- **Weather integration:** Outdoor games in extreme conditions affect passing efficiency. Worth a weather adjustment layer post-v1.
- **Bye week effects:** Teams off byes may perform differently. Evaluate whether already priced by market.
- **Divisional familiarity:** Teams play division opponents twice. Possible repeated-game signal.
- **Shrinkage:** Early-season regression toward prior-year metrics. Currently handled by Kalman priors but could be explicit.
- **Historical odds depth:** The Odds API free tier may have limited NFL historical data. If <2 seasons available, supplement with archived closing lines from other free sources or reduce backfill window.
