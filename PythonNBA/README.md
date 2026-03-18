# NBA Picks Daily Bot

An automated NBA spread prediction system that runs daily, combining Bayesian statistical modeling with a Kalman filter to generate against-the-spread (ATS) picks. The bot fetches live data from multiple sources, projects game outcomes with calibrated probabilities, self-tunes its weights over time, and delivers picks via email and Discord.

---

## Features

- **Bayesian spread predictions** with calibrated P(cover) probabilities instead of raw edge thresholds
- **Kalman filter** tracking per-team strength drift throughout the season
- **Self-tuning weights** that adapt daily based on grading results (Bayesian weight update + profitability-based threshold tuning)
- **Lineup-adjusted projections** accounting for injuries, rest (back-to-back detection), and player availability
- **Stats blending** combining full-season averages, last-10-game form, and home/away splits
- **Kelly criterion bet sizing** for optimal unit allocation per pick
- **Calibration monitoring** bucketing historical P(cover) vs actual hit rates
- **Dual notifications** via Gmail and Discord webhook
- **Persistent history** with daily grading, rolling records, and unit P&L tracking
- **Playoff-aware** with automatic season type detection and regular-season/playoff stat blending

---

## How the Model Works

### Score Projection

Each team's projected score is computed from a weighted combination of:

- **Offensive/Defensive ratings** (OFF, DEF) from NBA.com advanced stats
- **True Shooting % delta** (TS) vs league average
- **Turnover % delta** (TO) vs league average
- **Offensive Rebound Rate delta** (ORR) vs league average
- **Net Rating differential** (NET) between the two teams
- **Pace adjustment** scaling projections by the matchup's expected tempo
- **Home court advantage** (HCA) as a flat league-wide points bonus

The formula produces a point estimate plus a variance for each team's score.

### Kalman Filter (Team Strength Tracking)

A per-team Kalman filter maintains an adjustment offset representing how each team is performing relative to their season-long stats:

- **State**: Each team carries `adj_mean` (expected offset in points) and `adj_var` (uncertainty)
- **Update**: After each graded game, the "innovation" (actual margin minus projected margin) is split between both teams via Kalman gains. Teams with higher uncertainty absorb more of the surprise.
- **Daily drift**: Variance increases slightly each day to prevent the filter from locking onto stale data, reflecting trades, fatigue, and chemistry shifts.
- **Integration**: The Kalman offset is added to score projections, and the variance feeds into the P(cover) calculation.

### Bayesian Pick Logic

Instead of using fixed edge thresholds, the model computes:

1. **Projected margin** = home projected score minus away projected score minus the spread line
2. **Margin variance** = sum of home and away projection variances (team uncertainty + weight uncertainty + residual noise)
3. **P(cover)** = normal CDF of (margin / sqrt(variance))

A pick fires when P(cover) exceeds a tunable probability threshold (default 57% for spreads). The model uses a single confidence tier for spreads with an sDiff cap of 7.5 points to filter out extreme projections that tend to underperform.

### Self-Tuning

Three learning signals run daily after grading:

1. **Bayesian weight update** -- A diagonal Kalman filter over the projection weights (wTS, wTO, wORR, wNET, HCA). Each weight has a mean and variance; more uncertain weights move more on new data.
2. **Probability threshold tuning** -- Adjusts the P(cover) threshold based on recent ATS profitability. Winning above 58% relaxes the threshold (more picks); below 52% tightens it.
3. **Gradient descent on constant and pace** -- These non-linear parameters are updated via gradient descent on total projection error.

### Stats Blending

Team stats are blended from multiple windows before projection:

- **Season + Last 10 games**: Default 35% weight on recent form to capture streaks, trades, and injuries
- **Home/Away splits**: 25% adjustment toward location-specific performance for the home and away teams respectively
- **Playoff blending**: In playoffs, regular-season stats are blended with playoff stats on a ramp (more playoff games = more playoff weight)

### Lineup Adjustments

When key players are out:

1. Per-player advanced stats (on-court OFF/DEF ratings, TS%, TOV%, ORB%) are fetched from NBA.com
2. Full-roster weighted averages are compared against available-roster weighted averages
3. The delta is applied to team stats with impact-aware dampening (stars with high NET ratings get a larger adjustment multiplier)

---

## Data Sources

| Source | Data | Module |
|--------|------|--------|
| **NBA.com Stats API** | Team advanced stats (OFF, DEF, TS%, TO%, ORR, PACE), player advanced stats, player minutes | `nba_stats.mjs`, `lineup_adjust.mjs`, `injuries.mjs` |
| **The Odds API** | Today's spreads and totals from US sportsbooks (DraftKings, FanDuel, BetMGM, etc.) | `odds_theoddsapi.mjs` |
| **ESPN Scoreboard API** | Final scores for grading, game schedules, event IDs | `espn_scoreboard.mjs` |
| **ESPN Game Summary API** | Per-game player availability (injuries, rest, personal reasons) | `injuries.mjs` |
| **ESPN Schedule/Header API** | Back-to-back detection via yesterday's completed games | `rest_detect.mjs` |
| **TeamRankings** | ATS and O/U trend data | `teamrankings_trends.mjs` |

---

## Setup and Installation

### Prerequisites

- **Node.js** v18 or later
- A Gmail account with an App Password (for email notifications)
- An API key from [The Odds API](https://the-odds-api.com/) (free tier available)
- A Discord webhook URL (optional, for Discord notifications)

### Install

```bash
cd nba_picks_daily_bot
npm install
```

### Environment Variables

Create a `.env` file in the project root or set these as environment variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `GMAIL_USER` | Yes | Gmail address used to send pick emails |
| `GMAIL_APP_PASSWORD` | Yes | Gmail App Password (not your regular password; generate at Google Account > Security > App Passwords) |
| `TO_EMAIL` | Yes | Recipient email address for daily picks |
| `ODDS_API_KEY` | Yes | API key from The Odds API for fetching spreads and totals |
| `DISCORD_WEBHOOK_URL` | No | Discord webhook URL for posting picks to a channel |

---

## How to Run

### Daily Script (Node.js)

```bash
npm run run:daily
```

This executes the full pipeline:
1. Load history and Kalman state from `data/`
2. Grade yesterday's picks against ESPN final scores
3. Run self-tune on the graded results (Bayesian weight update + threshold tuning)
4. Update the Kalman filter with graded game innovations
5. Fetch today's stats, odds, injuries, and back-to-back status
6. Blend stats and apply lineup adjustments
7. Analyze each game and generate picks with P(cover)
8. Build HTML and plain-text reports with records, trends, and calibration
9. Send via email and Discord
10. Save updated history and Kalman state

### Windows Batch File

The included `run_daily.bat` runs the pipeline and logs output:

```bash
run_daily.bat
```

Logs are written to `logs/daily.log`.

### Other Scripts

```bash
# Print a summary report of all-time pick performance
npm run report

# Backfill the last 8 days of history
npm run backfill
```

---

## Project Structure

```
nba_picks_daily_bot/
├── package.json                  # Dependencies and npm scripts
├── run_daily.bat                 # Windows batch launcher
├── .env                          # Environment variables (not committed)
├── data/
│   ├── history.json              # Persistent store: daily runs, picks, results, weights
│   └── kalman_state.json         # Kalman filter state per team
├── logs/
│   └── daily.log                 # Pipeline output log
├── scripts/
│   ├── run_daily.mjs             # Main daily pipeline orchestrator
│   ├── model_engine.mjs          # Score projection, P(cover), game analysis
│   ├── defaults.mjs              # Default weights, variances, Bayesian hyperparameters
│   ├── kalman_state.mjs          # Kalman filter: load/save/init/drift/update
│   ├── self_tune.mjs             # Bayesian weight update, threshold tuning, residual variance
│   ├── calibration.mjs           # P(cover) calibration table, Kelly criterion sizing
│   ├── store.mjs                 # History persistence (load/save/upsert)
│   ├── email.mjs                 # Gmail notification via nodemailer
│   ├── discord.mjs               # Discord webhook notification (auto-chunking)
│   ├── print_report.mjs          # CLI performance report
│   ├── backfill_last_n_days.mjs  # Historical backfill utility
│   └── sources/
│       ├── nba_stats.mjs              # NBA.com team advanced stats (season, L10, splits)
│       ├── blend_stats.mjs            # Stats blending (season + recent + location)
│       ├── odds_theoddsapi.mjs        # The Odds API: live spreads and totals
│       ├── odds_theoddsapi_historical.mjs  # Historical/closing odds for started games
│       ├── espn_scoreboard.mjs        # ESPN scoreboard: final scores and schedules
│       ├── injuries.mjs               # ESPN game-specific injury/availability data
│       ├── lineup_adjust.mjs          # Lineup-adjusted team stats from player impact
│       ├── rest_detect.mjs            # Back-to-back detection and stat adjustments
│       ├── season_type.mjs            # Auto-detect regular season vs playoffs
│       ├── teamrankings_trends.mjs    # ATS and O/U trends from TeamRankings
│       └── http.mjs                   # Shared HTTP fetch utilities
```

---

## Notification Methods

### Email

Daily picks are sent as a formatted HTML email via Gmail's SMTP service using `nodemailer`. The email includes:

- Today's spread picks with P(cover), projected scores, and Kelly unit sizing
- Season-to-date record (W-L-P) with win percentage and unit P&L
- Rolling 7-day and 30-day performance windows
- Injury notes for key players (starters and stars who are out)
- Back-to-back flags
- Calibration table showing predicted vs actual hit rates by probability bucket
- Kalman filter summary (top team adjustments)

### Discord

Picks are posted to a Discord channel via webhook. Messages exceeding Discord's 2000-character limit are automatically split into multiple chunks at line boundaries. The Discord message contains a plain-text version of the picks and records.

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `node-fetch` | HTTP requests to NBA.com, ESPN, The Odds API |
| `dotenv` | Load environment variables from `.env` |
| `nodemailer` | Send HTML emails via Gmail SMTP |
| `cheerio` | HTML parsing for web-scraped data sources |
| `pdf-parse` | PDF parsing utility |
