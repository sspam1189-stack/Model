# NCAA Picks Daily Bot

An automated NCAA men's basketball spread-picking system that uses a Bayesian model engine with Kalman filtering to generate daily against-the-spread (ATS) picks. The bot fetches team stats, odds, and scores from multiple sources, analyzes every game, and delivers picks via email and Discord.

---

## Features

- **Bayesian spread model** with probability-of-cover (P(cover)) as the primary decision signal
- **Kalman filter** tracking per-team strength adjustments that evolve over time
- **Self-tuning weights** via diagonal Bayesian linear regression updated after each graded game
- **Probability threshold tuning** that automatically tightens or relaxes pick criteria based on ATS profitability
- **P(cover) calibration table** to verify that model probabilities match real-world hit rates
- **Kelly criterion bet sizing** (quarter-Kelly) for unit recommendations
- **Back-to-back detection** with offensive/defensive stat adjustments for fatigued teams
- **Stats blending** combining season averages with recent form and home/away splits
- **Backfill mode** to replay historical games and train the model on past data
- **Dual notifications** via Gmail (HTML-formatted) and Discord webhook
- **Automatic grading** of yesterday's picks against final scores before generating new ones
- **Rolling window records** (last 7, 14, 30 days) included in daily reports

---

## How the Model Works

### Projection Engine

Each team's expected score is projected using a weighted combination of:

- **Adjusted Offensive/Defensive Efficiency** (from Barttorvik T-Rank)
- **True Shooting / eFG% delta** relative to league average
- **Turnover rate delta** relative to league average
- **Offensive rebound rate delta** relative to league average
- **Net rating differential** between the two teams
- **Pace adjustment** based on both teams' adjusted tempo
- **Home court advantage** (default 4.0 points, range 2.0-6.0 for college)

The projected margin is: `home_projected_score - away_projected_score - spread_line`.

### Bayesian Probability of Cover

Rather than using raw edge thresholds, the model computes a full probability distribution:

1. **Variance propagation** combines Kalman team uncertainty, weight uncertainty, and residual game noise
2. **P(cover)** is computed via the normal CDF: `P = phi(margin / sqrt(variance))`
3. A pick is made when P(cover) exceeds 0.60, with a favorite line cap of 8 points (no cap on dogs)

### Kalman Filter

Each team carries an adjustment offset (how many points better or worse they are performing vs. season stats):

- **State**: `adj_mean` (offset) and `adj_var` (uncertainty) per team
- **Daily drift**: variance increases slightly each day to reflect that teams change over time
- **Game updates**: after each graded game, the innovation (actual minus projected margin) is split between both teams via Kalman gains proportional to their uncertainty
- Higher uncertainty teams absorb more of the surprise; well-known teams absorb less

### Self-Tuning

Three learning signals update the model daily:

1. **Bayesian weight update** (diagonal Kalman filter on projection weights: wTS, wTO, wORR, wNET, HCA)
2. **Probability threshold tuning** adjusts the P(cover) threshold based on recent ATS win rate
3. **Gradient descent** on constant and pace adjustment (non-linear parameters)

---

## Data Sources

| Source | Data | URL |
|--------|------|-----|
| **Barttorvik (T-Rank)** | Adjusted offensive/defensive efficiency, eFG%, TO%, OR%, adjusted tempo, games played | `barttorvik.com` |
| **The Odds API** | Live and historical spreads and totals from DraftKings, FanDuel, BetMGM, etc. | `the-odds-api.com` |
| **ESPN Scoreboard** | Final scores for grading, back-to-back detection | `site.api.espn.com` |
| **TeamRankings** | ATS trends and over/under trends for context | `teamrankings.com` |

---

## Setup and Installation

### Prerequisites

- **Node.js** (v18 or later recommended)
- **curl** (used internally for Barttorvik requests with cookie handling)
- API keys for The Odds API and Gmail (see Environment Variables below)

### Install Dependencies

```bash
cd ncaa_picks_daily_bot
npm install
```

This installs:
- `cheerio` -- HTML parsing for web scraping
- `dotenv` -- environment variable loading from `.env`
- `node-fetch` -- HTTP client
- `nodemailer` -- email delivery via Gmail SMTP

### Environment Variables

Create a `.env` file in the project root (see `.env.example`):

```
GMAIL_USER=
GMAIL_APP_PASSWORD=
TO_EMAIL=
ODDS_API_KEY=
DISCORD_WEBHOOK_URL=
```

| Variable | Required | Description |
|----------|----------|-------------|
| `GMAIL_USER` | Yes | Gmail address used to send pick emails |
| `GMAIL_APP_PASSWORD` | Yes | Gmail App Password (not your regular password -- generate one in Google Account settings) |
| `TO_EMAIL` | Yes | Recipient email address for daily picks |
| `ODDS_API_KEY` | Yes | API key from [The Odds API](https://the-odds-api.com/) (sport: `basketball_ncaab`) |
| `DISCORD_WEBHOOK_URL` | No | Discord webhook URL for posting picks to a channel |

---

## How to Run

### Daily Picks Pipeline

```bash
npm run run:daily
```

This executes the full daily pipeline:

1. Grades yesterday's picks against ESPN final scores
2. Updates the Kalman filter and self-tunes weights from graded results
3. Fetches today's team stats from Barttorvik
4. Fetches today's odds from The Odds API
5. Fetches ATS/OU trends from TeamRankings
6. Detects back-to-back situations and applies fatigue adjustments
7. Runs every game through the model engine
8. Builds an HTML email with picks, records, rolling windows, and calibration data
9. Sends the report via email and Discord

### Backfill Historical Data

```bash
npm run backfill
# or with custom date range:
node scripts/backfill.mjs 20260201
node scripts/backfill.mjs 20260101 20260301
```

Replays historical games day-by-day using historical odds from The Odds API. This trains the Kalman filter and self-tuning weights on past results. Requires a paid Odds API tier with historical access.

---

## Project Structure

```
ncaa_picks_daily_bot/
├── package.json                  # Project metadata and npm scripts
├── .env                          # Environment variables (git-ignored)
├── .env.example                  # Template for environment variables
├── .gitignore                    # Ignores node_modules, .env, logs
│
├── scripts/
│   ├── run_daily.mjs             # Main daily pipeline (grade, fetch, analyze, send)
│   ├── model_engine.mjs          # Bayesian projection + P(cover) + pick logic
│   ├── defaults.mjs              # Default weights, variances, and hyperparameters
│   ├── self_tune.mjs             # Bayesian weight update + threshold tuning
│   ├── kalman_state.mjs          # Kalman filter for per-team strength tracking
│   ├── calibration.mjs           # P(cover) calibration table + Kelly criterion sizing
│   ├── store.mjs                 # JSON-based persistence (history.json)
│   ├── backfill.mjs              # Historical replay with Odds API historical data
│   ├── email.mjs                 # Gmail SMTP email sender (nodemailer)
│   ├── discord.mjs               # Discord webhook sender (auto-chunks long messages)
│   ├── update.mjs                # Stats update utility
│   ├── regrade_picks.mjs         # Re-grade historical picks
│   ├── check_volume.mjs          # Volume analysis
│   ├── test_resolve.mjs          # Team name resolution tests
│   │
│   └── sources/
│       ├── ncaa_stats.mjs        # Barttorvik T-Rank scraper (curl + cookie auth)
│       ├── odds_theoddsapi.mjs   # The Odds API client (live NCAAB spreads/totals)
│       ├── espn_scoreboard.mjs   # ESPN scoreboard API (final scores)
│       ├── blend_stats.mjs       # Stats blending (season + recent + location splits)
│       ├── rest_detect.mjs       # Back-to-back detection + fatigue adjustments
│       ├── teamrankings_trends.mjs # ATS and O/U trends scraper
│       ├── season_type.mjs       # Regular season vs. NCAA Tournament detection
│       └── http.mjs              # Shared HTTP fetch + Cheerio HTML parser
│
├── data/
│   ├── history.json              # Persistent store: runs, weights, graded picks
│   ├── kalman_state.json         # Kalman filter state per team
│   ├── barttorvik_cache.json     # Cached Barttorvik stats (20-hour TTL)
│   └── stats_cache/              # Additional stats caches
│
└── logs/                         # Runtime logs (git-ignored)
```

---

## Notification Methods

### Email (Gmail SMTP)

The bot sends a formatted HTML email containing:

- Today's spread picks with P(cover) probabilities and Kelly unit sizing
- Season-to-date record and unit profit/loss
- Rolling window records (7-day, 14-day, 30-day)
- Yesterday's grading results
- P(cover) calibration table
- Current model weights and Kalman filter summary

Requires `GMAIL_USER`, `GMAIL_APP_PASSWORD`, and `TO_EMAIL` in your `.env` file.

### Discord Webhook

The bot also posts a plain-text summary to a Discord channel via webhook. Long messages are automatically chunked to respect Discord's 2000-character limit.

Set `DISCORD_WEBHOOK_URL` in your `.env` file. If the variable is not set, Discord notifications are silently skipped.

---

## Key Model Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `hca` | 4.0 | Home court advantage (points) |
| `wTS` | 1.0 | True shooting delta weight |
| `wTO` | 1.0 | Turnover rate delta weight |
| `wORR` | 1.0 | Offensive rebound rate delta weight |
| `wNET` | 1.0 | Net rating delta weight |
| `paceAdj` | 1.0 | Pace multiplier |
| `residualVar` | 130 | Irreducible per-team game noise (points squared) |
| `gameNoise` | 196 | Kalman game outcome noise (~14 pt std dev squared) |
| `initialVar` | 20 | Kalman initial team uncertainty |
| `dailyDrift` | 0.15 | Kalman daily variance drift |

All weights are self-tuned over time as games are graded.

---

## License

Private project. Not intended for public distribution.
