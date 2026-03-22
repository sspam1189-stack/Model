# NBA Full-Season Picks Bot

An automated NBA spread and totals prediction system that runs daily, grades its own picks against final scores, and continuously improves its model weights using Bayesian learning. Unlike a simple daily picks tool, this bot is designed to operate across an entire NBA season -- it maintains a persistent history of every prediction, tracks cumulative accuracy, self-tunes its model weights based on results, and uses a Kalman filter to track team strength drift over time.

---

## Table of Contents

- [Features](#features)
- [How the Model Works](#how-the-model-works)
- [Data Sources](#data-sources)
- [Project Structure](#project-structure)
- [Setup and Installation](#setup-and-installation)
- [Environment Variables](#environment-variables)
- [How to Run](#how-to-run)
- [Notification Methods](#notification-methods)
- [Scheduling](#scheduling)
- [Notes and Limitations](#notes-and-limitations)

---

## Features

- **Daily automated pipeline** -- Fetches stats, odds, injuries, and trends; generates spread and totals picks; grades yesterday's results; sends notifications.
- **Bayesian self-tuning model** -- Projection weights (TS%, TO%, ORR, NET rating, HCA) are updated daily using a diagonal Kalman filter (Bayesian linear regression). The model learns from its own prediction errors.
- **Kalman filter team tracker** -- Each team carries an adjustment offset that tracks how many points better or worse they are performing relative to season-long stats. Uncertainty shrinks with more data and grows with daily drift.
- **Probability-based pick logic** -- Picks are triggered by P(cover) thresholds rather than raw point edges. The model computes the full probability distribution of the projected margin and uses the normal CDF to estimate cover probability.
- **Kelly criterion bet sizing** -- Each pick is assigned a unit size (0.5u to 3u) based on quarter-Kelly optimal sizing derived from the estimated edge.
- **Calibration tracking** -- Buckets historical picks by their predicted P(cover) and compares against actual hit rates to measure model calibration.
- **Stats blending** -- Merges full-season stats with last-10-game stats and home/away splits to capture recent form and location-specific performance.
- **Injury-aware projections** -- Fetches per-game player availability from ESPN and adjusts team efficiency ratings based on which players are out, using minutes-weighted lineup impact analysis.
- **Back-to-back detection** -- Identifies teams on the second night of a back-to-back and applies a fatigue adjustment.
- **Head-to-head matchup history** -- Pulls season game logs and computes a recency-weighted H2H margin adjustment for each matchup pair.
- **Playoff mode** -- Automatically detects regular season vs. playoffs and blends playoff stats with regular-season stats proportionally.
- **Backfill utility** -- Can retroactively process past days to build up history and train the model from scratch.
- **Dual notifications** -- Sends formatted HTML emails via Gmail and plain-text messages to Discord via webhook.
- **Full season persistence** -- All picks, results, weights, Kalman state, and calibration data are stored in `data/history.json` and `data/kalman_state.json`, accumulating across the entire season.

---

## How the Model Works

### Projection Formula

For each team in a game, the model computes a projected score:

```
score = base * pace_factor + home_court_advantage + kalman_adjustment
```

Where `base` is derived from:
- Offensive and defensive ratings (OFF, DEF) of both teams
- True shooting percentage delta (TS%) weighted by `wTS`
- Turnover rate delta (TO%) weighted by `wTO`
- Offensive rebound rate delta (ORR) weighted by `wORR`
- Net rating differential weighted by `wNET`
- An additive constant

The pace factor normalizes possessions: `(team_pace + opp_pace) / 2 * paceAdj / 100`.

### Bayesian Framework

Each projection weight carries both a **mean** (the weight value) and a **variance** (how uncertain the model is about that weight). After each graded game:

1. The model computes the prediction error (actual margin minus projected margin).
2. A diagonal Kalman update adjusts each weight's mean and shrinks its variance.
3. Weights with high uncertainty move more; confident weights move less.

The total projection variance for a game combines:
- Kalman team uncertainty (per-team adjustment variance)
- Weight uncertainty propagated through features
- Residual game noise (irreducible randomness)

### Pick Logic

The projected margin distribution is assumed normal. The model computes:
- `P(home covers) = CDF(margin_mean / margin_std)`
- `P(over) = CDF(total_diff / total_std)`

A spread pick fires when `P(cover)` exceeds the `probHigh` threshold (default 0.57). A totals pick fires only at the `probOUElite` threshold (default 0.64) since totals are noisier. These thresholds are themselves tuned based on ATS profitability: if the model is winning above 58%, thresholds relax to generate more picks; if below 52%, they tighten.

### Self-Tuning Signals

The model uses three learning signals daily:

| Signal | Method | What It Adjusts |
|--------|--------|-----------------|
| Weight update | Bayesian (diagonal Kalman) | wTS, wTO, wORR, wNET, hca |
| Threshold tuning | Profitability feedback | probHigh, probOUElite |
| Constant + pace | Gradient descent | constant, paceAdj |

### Kalman Team Tracker

Each team has an `adj_mean` (offset in points) and `adj_var` (uncertainty). After grading a game, the innovation (surprise) is split between both teams via Kalman gains proportional to their respective uncertainties. A daily drift term prevents overconfidence by slowly increasing variance.

---

## Data Sources

| Source | Data | Module |
|--------|------|--------|
| **NBA.com** (stats.nba.com) | Team advanced stats (OFF/DEF rating, TS%, TO%, ORR, PACE), last-10-game stats, home/away splits, per-player advanced stats, season game logs | `nba_stats.mjs`, `lineup_adjust.mjs`, `h2h_matchup.mjs` |
| **The Odds API** (api.the-odds-api.com) | Live spreads and totals from major US sportsbooks (DraftKings, FanDuel, BetMGM, etc.), historical/closing lines | `odds_theoddsapi.mjs`, `odds_theoddsapi_historical.mjs` |
| **ESPN** (site.api.espn.com) | Scoreboard and final scores, per-game injury/availability reports, player minutes per game (fallback) | `espn_scoreboard.mjs`, `injuries.mjs` |
| **TeamRankings** (teamrankings.com) | ATS trends and over/under trends | `teamrankings_trends.mjs` |

---

## Project Structure

```
nba_picks_daily_botfullseason/
|-- package.json                    # Dependencies and npm scripts
|-- run_daily.bat                   # Windows batch launcher for daily run
|-- RECAPrun_daily.bat              # Windows batch launcher for recap/grading run
|-- .github/
|   `-- workflows/
|       `-- nba-picks.yml           # GitHub Actions cron schedule (daily at 10:10 AM CT)
|-- data/
|   |-- history.json                # Full season picks, results, weights, records
|   |-- kalman_state.json           # Per-team Kalman filter state
|   |-- odds_cache/                 # Cached odds data
|   `-- stats_cache/                # Cached stats for backfill
|-- logs/
|   `-- daily.log                   # Runtime output log
`-- scripts/
    |-- run_daily.mjs               # Main daily pipeline (grade + predict + notify)
    |-- RECAPrun_daily.mjs          # Recap/grading-only pipeline
    |-- model_engine.mjs            # Score projection, P(cover), game analysis
    |-- defaults.mjs                # Default weights, variances, Bayesian hyperparameters
    |-- self_tune.mjs               # Bayesian weight update + threshold tuning
    |-- kalman_state.mjs            # Kalman filter for team strength tracking
    |-- calibration.mjs             # P(cover) calibration table + Kelly sizing
    |-- store.mjs                   # history.json read/write
    |-- email.mjs                   # Gmail SMTP sender (HTML + plain text)
    |-- discord.mjs                 # Discord webhook sender (auto-chunking)
    |-- backfill_last_n_days.mjs    # Retroactive processing of past days
    |-- recalculate.mjs             # Recalculate picks with updated weights
    |-- grade_only.mjs              # Grade-only utility
    |-- update.mjs                  # Update utility
    `-- sources/
        |-- nba_stats.mjs           # NBA.com team stats (season, last 10, splits)
        |-- nba_recent_stats.mjs    # Recent game stats
        |-- blend_stats.mjs         # Blends season + recent + location stats
        |-- odds_theoddsapi.mjs     # Live odds from The Odds API
        |-- odds_theoddsapi_historical.mjs  # Historical/closing odds
        |-- odds_batch_historical.mjs       # Batch historical odds for backfill
        |-- espn_scoreboard.mjs     # ESPN scores and game status
        |-- injuries.mjs            # Per-game injury reports + player MPG
        |-- lineup_adjust.mjs       # Lineup-adjusted team efficiency
        |-- rest_detect.mjs         # Back-to-back detection and adjustments
        |-- h2h_matchup.mjs         # Head-to-head matchup history
        |-- teamrankings_trends.mjs # ATS and O/U trends scraping
        |-- season_type.mjs         # Auto-detect regular season vs. playoffs
        `-- http.mjs                # Shared HTTP fetch utilities
```

---

## Setup and Installation

### Prerequisites

- **Node.js** v20 or later
- **npm** (included with Node.js)
- A **Gmail account** with an App Password for email notifications (optional)
- A **Discord webhook URL** for Discord notifications (optional)
- An **Odds API key** from [the-odds-api.com](https://the-odds-api.com/) (required)

### Install

```bash
cd nba_picks_daily_botfullseason
npm install
```

### Configuration

Create a `.env` file in the project root (or set environment variables directly):

```env
GMAIL_USER=your-email@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
TO_EMAIL=recipient@example.com
ODDS_API_KEY=your_odds_api_key_here
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ODDS_API_KEY` | Yes | API key for The Odds API (spreads and totals) |
| `GMAIL_USER` | For email | Gmail address to send from |
| `GMAIL_APP_PASSWORD` | For email | Gmail App Password (not your regular password) |
| `TO_EMAIL` | For email | Recipient email address |
| `DISCORD_WEBHOOK_URL` | For Discord | Discord channel webhook URL |
| `TZ` | Recommended | Timezone (default: `America/Chicago`) |

### Gmail App Password

Use a Gmail **App Password**, not your regular password. Generate one at:
Google Account > Security > 2-Step Verification > App Passwords.

---

## How to Run

### Daily Run (full pipeline)

Fetches stats and odds, grades yesterday's picks, generates today's picks, self-tunes weights, and sends notifications:

```bash
npm run run:daily
```

### Backfill Historical Data

Retroactively processes past days to build up history and train the model:

```bash
npm run backfill
```

### Windows Batch Files

For Windows Task Scheduler or manual execution:

- `run_daily.bat` -- Runs the full daily pipeline with environment variables pre-configured.
- `RECAPrun_daily.bat` -- Runs the recap/grading pipeline only.

### Manual Execution

```bash
node scripts/run_daily.mjs
```

---

## Notification Methods

### Email (Gmail SMTP)

The bot sends a formatted HTML email containing:
- Today's picks with projected scores, margins, P(cover), and Kelly unit sizing
- Yesterday's graded results (WIN/LOSS/PUSH)
- Cumulative season record with rolling 7-day and 14-day windows
- Calibration table showing predicted vs. actual hit rates
- Injury notes for key players

Requires `GMAIL_USER`, `GMAIL_APP_PASSWORD`, and `TO_EMAIL` environment variables.

### Discord (Webhook)

The bot sends a plain-text summary to a Discord channel via webhook. Messages over 2000 characters are automatically split into multiple chunks on newline boundaries.

Requires the `DISCORD_WEBHOOK_URL` environment variable.

---

## Scheduling

### GitHub Actions

A workflow is included at `.github/workflows/nba-picks.yml` that runs daily at 10:10 AM Central Time:

```yaml
on:
  schedule:
    - cron: "10 16 * * *"   # 10:10 AM America/Chicago (UTC-6)
  workflow_dispatch:          # Manual trigger
```

Required GitHub Secrets:
- `GMAIL_USER`
- `GMAIL_APP_PASSWORD`
- `TO_EMAIL`

### Windows Task Scheduler

Use the included `run_daily.bat` file as the action in a Windows Task Scheduler task. Set it to run once daily, roughly 30 minutes before the first NBA game of the day.

---

## Notes and Limitations

- **API rate limits** -- NBA.com and ESPN endpoints may rate-limit or change response formats. The bot includes multiple fallback strategies (e.g., three different MPG sources) and degrades gracefully when a source is unavailable.
- **The Odds API quota** -- Free tier has limited monthly requests. The bot makes 1-2 API calls per daily run for live odds, plus additional calls for historical odds on already-started games.
- **Minimum games played** -- Teams with fewer than 15 games played are excluded from projections (the `MIN_GP` threshold) to avoid small-sample projections.
- **Stats are point-in-time for live runs only** -- When running live each day, stats reflect the current state. Backfill uses cached stats where available, but projections for past dates may use stats from the time of backfill, not the actual date.
- **Model is spread-focused** -- The Kalman filter tracks margin drift, not total scoring. Totals picks use a separate clean projection formula that avoids double-counting offensive/defensive adjustments.
- **Break-even at -110 juice is 52.4%** -- The model requires meaningful edge above this before firing a pick. Default spread threshold is P(cover) >= 57%.
