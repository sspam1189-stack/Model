# NFL Weekly Spread Projection Pipeline

An automated NFL spread prediction system that runs weekly, combining ridge regression on EPA features with a Kalman filter to generate against-the-spread (ATS) picks. The pipeline ingests play-by-play data via nfl_data_py, applies injury-conditional adjustments, and delivers projections to the Python dashboard.

---

## Features

- **Ridge regression on EPA features** with Kalman-filtered team states as inputs
- **Kalman filter** tracking per-team strength drift throughout the season
- **Self-tuning weights** that adapt weekly based on grading results (Bayesian weight update + profitability-based threshold tuning)
- **Injury-adjusted projections** with positional EPA deltas for QB, WR1, pass rusher, CB1, and offensive line
- **Exponential decay weighting** (~0.85/week) emphasizing recent performance over season-long averages
- **LR confirmation layer** as an ensemble check on Bayesian projections
- **Calibration monitoring** bucketing historical P(cover) vs actual hit rates
- **Persistent history** with weekly grading, rolling records, and unit P&L tracking
- **Dashboard output** to PythonDashboard NFL tab

---

## How the Model Works

### Score Projection

Each matchup's projected spread is computed via ridge regression on Kalman-filtered team EPA features:

- **Offensive EPA/play** (pass and rush splits)
- **Defensive EPA/play** (pass and rush splits)
- **Pace** (plays/game) interaction terms
- **Red zone efficiency** (TD% on both sides)
- **Home field advantage** as a flat points bonus
- **Injury deltas** applied additively post-projection

### Kalman Filter (Team Strength Tracking)

A per-team Kalman filter maintains state vectors for offensive EPA, defensive EPA, pace, and red zone TD%:

- **State**: Each team carries filtered estimates with uncertainty
- **Update**: After each game window (Thursday/Sunday/Monday), innovations are applied
- **Process noise**: Higher than NBA to account for weekly variance and injury regime changes
- **Measurement noise**: Scaled by games played (early season = high noise)

### Injury Adjustment Layer

The pipeline's primary edge over public EPA models:

- **QB**: Replace team passing EPA with backup's historical EPA/dropback
- **WR1**: Redistribute target share, apply passing efficiency haircut
- **Pass Rusher**: Reduce team pressure rate, increase opponent QB EPA
- **CB1/Safety**: Substitute coverage EPA, degrade pass defense
- **O-Line**: Increase pressure rate allowed, reduce QB efficiency
- **Compounding**: Multi-injury multiplier (1.1-1.2x) when 2+ starters on same unit are out

### Self-Tuning

Three learning signals run weekly after grading:

1. **Bayesian weight update** on NFL weight keys (wPassOff, wRushOff, wPassDef, wRushDef, wPace, wRZ, hfa)
2. **Probability threshold tuning** based on recent ATS profitability
3. **Gradient descent** on constant and key coefficients

---

## Data Sources

| Source | Data | Module |
|--------|------|--------|
| **nfl_data_py (nflfastR)** | Play-by-play EPA, CPOE, air yards, WPA, player IDs | `nflfastr.py` |
| **The Odds API** | Opening/closing lines, NFL sport key | `odds_theoddsapi.py` |
| **ESPN Scoreboard** | Final scores for grading | `espn_scoreboard.py` |
| **ESPN Injury API** | Wed-Fri practice designations (DNP/Limited/Full) | `injuries.py` |
| **Pro Football Reference** | Snap counts, target shares (optional, graceful fallback) | `pfr_stats.py` |
| **Next Gen Stats** | Completion probability, separation metrics (optional) | `nextgenstats.py` |

---

## Setup and Installation

### Prerequisites

- **Python 3.10+**
- An API key from [The Odds API](https://the-odds-api.com/) (free tier available)

### Install

```bash
cd pyNFL
pip install -r requirements.txt
```

### Environment Variables

Copy `.env.example` to `.env` and fill in:

| Variable | Required | Description |
|----------|----------|-------------|
| `ODDS_API_KEY` | Yes | API key from The Odds API for fetching spreads |

---

## How to Run

### Weekly Pipeline

```bash
python -m scripts.run_weekly --stage all
```

Pipeline stages (can be run individually via `--stage` flag):

1. **fetch** -- Pull play-by-play EPA data and team stats via nfl_data_py
2. **injuries** -- Fetch injury designations from ESPN, compute positional deltas
3. **project** -- Build feature matrix, run ridge projection, apply injury adjustments
4. **grade** -- Grade previous week's picks against final scores, run self-tune

### Backfill

```bash
python -m scripts.backfill_last_n_weeks --weeks 51
```

Replays 2-3 seasons sequentially to train ridge coefficients, calibrate thresholds, and train the LR model.

---

## Project Structure

```
pyNFL/
├── requirements.txt
├── .env.example
├── README.md
├── __init__.py
├── scripts/
│   ├── __init__.py
│   ├── run_weekly.py            # Orchestrator (--stage flag)
│   ├── defaults.py              # NFL weight keys, hyperparams, thresholds
│   ├── model_engine.py          # Ridge regression on EPA features
│   ├── injury_layer.py          # Positional injury deltas
│   ├── lr_model.py              # Logistic regression confirmation
│   ├── lr_backtest.py           # LR backtesting
│   ├── kalman_state.py          # Wrapper over core/kalman_state.py
│   ├── self_tune.py             # Wrapper over core/self_tune.py
│   ├── calibration.py           # Wrapper over core/calibration.py
│   ├── store.py                 # Outputs to data/nfl.json
│   ├── backfill_last_n_weeks.py # Historical replay
│   └── sources/
│       ├── __init__.py
│       ├── nflfastr.py          # nfl_data_py play-by-play
│       ├── nfl_stats.py         # Team-level stat aggregation
│       ├── injuries.py          # ESPN injury API
│       ├── odds_theoddsapi.py   # The Odds API
│       ├── pfr_stats.py         # Pro Football Reference (optional)
│       ├── nextgenstats.py      # NFL.com Next Gen Stats (optional)
│       └── espn_scoreboard.py   # Final scores for grading
├── data/
│   ├── nfl.json                 # Dashboard output
│   ├── weights.json             # Trained ridge + Bayesian weights
│   ├── thresholds.json          # Calibrated sDiff thresholds
│   ├── team_states/             # Kalman state per team
│   ├── player_epa/              # Per-player EPA snapshots
│   ├── injuries/                # Weekly injury designations
│   ├── odds/                    # Market lines per matchup
│   └── lr_models/               # Trained LR model persistence
```

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `nfl-data-py` | nflfastR Python wrapper for play-by-play data |
| `numpy` | Numerical computation |
| `scipy` | Statistical functions |
| `scikit-learn` | Ridge regression, logistic regression |
| `beautifulsoup4` | HTML parsing for PFR/NGS scraping |
| `requests` | HTTP requests to ESPN, PFR, NGS |
| `python-dotenv` | Load environment variables from `.env` |
| `pandas` | Data manipulation and aggregation |
