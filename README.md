# Sports Prediction Model

A Bayesian sports prediction platform that generates daily against-the-spread (ATS) picks for **NBA** and **NCAA basketball**. The system uses probabilistic modeling, Kalman filtering, and self-tuning weight optimization to produce calibrated spread and totals predictions.

## Features

- **Bayesian spread projections** with probability-of-cover (P(cover)) thresholds
- **Kalman filter tracking** of per-team strength drift throughout the season
- **Self-tuning weights** that learn from daily graded results via Bayesian updates
- **Calibration monitoring** to validate predicted probabilities against actual hit rates
- **Injury and lineup adjustments** based on player availability
- **Back-to-back fatigue detection** for scheduling edges
- **Head-to-head matchup history** tracking (Full-Season mode)
- **Automated daily execution** via GitHub Actions with result tracking

## Project Structure

```
core/              Python shared engine (model, Kalman, self-tune, calibration)
core-js/           JavaScript shared engine (ES modules, mirrors core/)
pyNBA/             Python NBA daily picks
pyNCAA/            Python NCAA daily picks
pyFull/            Python NBA full-season tracking (spreads + totals)
jsNBA/             JavaScript NBA daily picks
jsNCAA/            JavaScript NCAA daily picks
jsFull/            JavaScript NBA full-season tracking
Dashboard/         JavaScript-based web dashboard
PythonDashboard/   Python-based web dashboard with LR confirmations
scripts/           Global utilities and QA tests
.github/workflows/ Automated daily run pipelines
```

## How It Works

1. **Data collection** — Fetches team stats, scores, injuries, and odds from ESPN, NBA.com, The Odds API, and Barttorvik (NCAA)
2. **Projection** — Blends full-season averages, last-10-game form, and home/away splits with weighted advanced stats (OFF/DEF rating, TS%, TO%, ORR, NET rating)
3. **Spread modeling** — Applies home court advantage, pace normalization, and variance propagation to compute P(cover) via normal distribution
4. **Pick selection** — Filters picks by P(cover) threshold
5. **Self-tuning** — Grades previous picks and updates projection weights, Kalman states, and thresholds daily
6. **Reporting** — Sends picks via email (Gmail SMTP) and Discord webhooks with performance metrics and calibration tables

## Tech Stack

**Python:** NumPy, SciPy, scikit-learn, XGBoost, BeautifulSoup, Requests

**JavaScript:** Node.js 18+, node-fetch, Cheerio, Nodemailer (ES Modules)

**Infrastructure:** GitHub Actions, self-hosted runners, JSON persistence

## Getting Started

Each implementation directory (`pyNBA/`, `jsNBA/`, etc.) contains its own README with setup instructions, environment variables, and usage details.

**Quick start (Python NBA):**
```bash
cd pyNBA
pip install -r requirements.txt
cp .env.example .env  # configure API keys
python scripts/run_daily.py
```

**Quick start (JavaScript NBA):**
```bash
cd jsNBA
npm install
cp .env.example .env  # configure API keys
node scripts/run_daily.mjs
```

## Dashboards

The web dashboards provide a dark-themed UI with tabbed views for NBA, NCAA, and Full-Season picks. They display daily predictions, performance tracking, and model diagnostics from the JSON output files.
