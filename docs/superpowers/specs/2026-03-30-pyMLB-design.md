# pyMLB Engine Design Spec

**Date:** 2026-03-30
**Status:** Draft
**Approach:** Ridge regression on MLB features with starter/bullpen split, reusing shared `core/` infrastructure

---

## 1. Architecture Overview

`pyMLB/` follows the same pattern as `pyNFL/`: a sport-specific model engine + data sources, with thin wrappers calling shared `core/` for Kalman filtering, Bayesian self-tuning, calibration, and persistence.

**Key decisions:**
- Ridge regression (not weighted stat deltas) — MLB's starter-dominated variance is analogous to NFL's QB-driven model
- Starter + bullpen IP-weighted split for pitching features
- pybaseball as primary stats source (wraps FanGraphs + Statcast)
- FanDuel primary for odds, The Odds API as fallback
- Static park factors + game-time weather (Open-Meteo)
- Spreads (run line + moneyline) + totals markets
- Daily execution cadence
- Backfill on prior season + historical odds archive

---

## 2. Directory Structure

```
pyMLB/
  scripts/
    __init__.py
    defaults.py          # MLB teams, weights, hyperparams, feature columns
    model_engine.py      # Ridge: build feature vectors, project margin/total
    run_daily.py         # Daily pipeline: grade -> fetch -> project -> report
    backfill.py          # Prior-season ridge training via pybaseball
    pitcher_layer.py     # Starter swap + bullpen workload adjustments
    self_tune.py         # Thin wrapper -> core/self_tune.py
    kalman_state.py      # Thin wrapper -> core/kalman_state.py
    calibration.py       # Thin wrapper -> core/calibration.py
    store.py             # Thin wrapper -> core/store.py
    sources/
      __init__.py
      espn_scoreboard.py  # MLB schedules + final scores
      odds_fanduel.py     # Primary: FanDuel API for run lines + totals
      odds_theoddsapi.py  # Fallback: The Odds API
      mlb_stats.py        # pybaseball: team batting + pitching aggregates
      pitcher_stats.py    # pybaseball: individual pitcher stats
      injuries.py         # ESPN injury API: IL status, probable pitchers
      park_factors.py     # Static park factor table
      weather.py          # Open-Meteo API: game-time temp + wind
  data/
    mlb_history.json     # Store: runs + weights
    kalman_state.json    # Per-team Kalman adjustments
    weights.json         # Ridge coefficients from backfill
```

---

## 3. Ridge Feature Set (20 features)

### Starter pitching (weighted by projected ~5.5 IP)

| Feature | Description |
|---|---|
| `starter_fip_diff` | Starter FIP vs opposing lineup wRC+ |
| `starter_kbb_diff` | Starter K-BB% differential |
| `starter_xfip_diff` | xFIP differential (normalizes HR/FB luck) |
| `starter_siera_diff` | SIERA differential (batted ball aware) |
| `starter_handedness` | Platoon flag (L/R vs opposing lineup splits) |

### Bullpen (weighted by projected ~3.5 IP)

| Feature | Description |
|---|---|
| `bullpen_fip_diff` | Team bullpen FIP differential |
| `bullpen_workload` | Bullpen IP last 3 days (fatigue signal) |
| `bullpen_kbb_diff` | Bullpen K-BB% differential |

### Batting

| Feature | Description |
|---|---|
| `wrc_plus_diff` | Team wRC+ differential |
| `xwoba_diff` | Team xwOBA differential (Statcast contact quality) |
| `iso_diff` | Isolated power differential |
| `kbb_bat_diff` | Batting K%-BB% differential (plate discipline) |
| `wrc_vs_hand_diff` | wRC+ vs starter's handedness (platoon matchup) |

### Environment + situational

| Feature | Description |
|---|---|
| `home_flag` | Home team indicator |
| `park_factor` | Venue park factor (runs/game multiplier) |
| `temperature_adj` | Degrees above 70F, scaled |
| `wind_adj` | Wind speed * directional component |

### Interaction features

| Feature | Description |
|---|---|
| `starter_x_lineup` | Starter FIP * opposing wRC+ interaction |
| `bullpen_x_lead` | Bullpen FIP * projected lead magnitude |

---

## 4. Daily Pipeline (`run_daily.py`)

### Step 1 — Grade yesterday's picks
- Fetch final scores from ESPN scoreboard
- Parse each pick (run line ATS + over/under result)
- Feed results into `core/self_tune.py` for Bayesian weight updates
- Update Kalman state from actual vs projected margins

### Step 2 — Fetch team stats
- `mlb_stats.py`: pybaseball `team_batting()` + `team_pitching()` for season aggregates
- Blend: 70% season / 30% last-30-days rolling window
- Cache to `data/stats_cache/mlb/YYYYMMDD.json` (same cache mechanic as NBA/NFL)

### Step 3 — Fetch pitcher stats
- `pitcher_stats.py`: pybaseball `pitching_stats()` for individual pitcher lines
- Separate starter pool vs bullpen pool per team
- Compute team bullpen aggregate (FIP, K-BB%) weighted by IP
- Cache to `data/pitcher_cache/mlb/YYYYMMDD.json`

### Step 4 — Fetch probable starters + injuries
- `injuries.py`: ESPN API for IL status + probable pitcher assignments
- `pitcher_layer.py`: If projected starter scratched/IL'd, swap replacement pitcher stats. Compute bullpen availability from last-3-day IP totals.

### Step 5 — Fetch odds
- `odds_fanduel.py`: Primary — FanDuel API for MLB run lines + totals
- `odds_theoddsapi.py`: Fallback if FanDuel returns no lines for a game
- Cache to `data/odds_cache/mlb/YYYYMMDD.json`

### Step 6 — Fetch weather
- `weather.py`: Open-Meteo API for game-time temperature + wind speed/direction
- Skip domed/retractable-roof stadiums (weather features zeroed)

### Step 7 — Apply Kalman drift
- Daily variance drift for all 30 teams via `core/kalman_state.py`

### Step 8 — Project games
- For each game: build 20-feature vector -> ridge prediction -> projected margin + total
- Apply park factor + weather adjustments to total
- Compute P(cover) and P(over/under) via normal CDF with variance propagation
- Generate picks where P exceeds thresholds

### Step 9 — Save + report
- Upsert run to `mlb_history.json` via `core/store.py`
- Email picks + calibration table via `core/email_report.py`

---

## 5. Defaults & Hyperparameters

### Initial ridge weight seeds (overwritten by backfill)

```python
DEFAULT_W = {
    # Starter pitching
    "wStarterFIP": -2.5,
    "wStarterKBB": 1.5,
    "wStarterXFIP": -1.5,
    "wStarterSIERA": -1.0,
    "wStarterHand": 0.3,
    # Bullpen
    "wBullpenFIP": -1.5,
    "wBullpenWorkload": -0.3,
    "wBullpenKBB": 0.8,
    # Batting
    "wWRCPlus": 2.0,
    "wXWOBA": 1.5,
    "wISO": 0.8,
    "wKBBBat": 0.5,
    "wWRCvsHand": 1.0,
    # Environment
    "hfa": 0.5,
    "wParkFactor": 1.0,
    "wTempAdj": 0.05,
    "wWindAdj": 0.1,
    # Interactions
    "wStarterXLineup": 0.5,
    "wBullpenXLead": 0.3,
    # Additive
    "constant": 0.0,
    # Pick thresholds
    "probHigh": 0.57,
    "probElite": 0.63,
    "probOUHigh": 0.58,
    "probOUElite": 0.65,
}
```

### Bayesian hyperparameters

```python
BAYES_HYPER = {
    "marginNoise": 16,      # ~4 run std dev
    "totalNoise": 20,
    "minWeightVar": 0.05,
    "maxWeightVar": 12,
    "residualVar": 12,
}
```

### Kalman filter

```python
KALMAN_DEFAULTS = {
    "initialVar": 9,        # ~3 run std dev
    "gameNoise": 16,        # ~4 run std dev per game
    "dailyDrift": 0.1,      # Low — daily games
    "minVar": 1.0,
    "maxVar": 20,
}
```

### HFA clamp

```python
HCA_CLAMP_LO = 0.2
HCA_CLAMP_HI = 1.0
HCA_VAR_FLOOR = 0.1
```

### Burn-in: 14 days

---

## 6. Backfill & Ridge Training

**Data sources for backfill:**
- pybaseball: prior season team + pitcher stats
- ESPN scoreboard: prior season final scores
- Historical odds: `C:\Users\Henry Pham\Desktop\odds historical\MLB`
- FanGraphs park factors: prior season

**Training process:**
1. For each prior-season game (~2,430):
   - Look up that day's probable starter stats per team
   - Build 20-feature vector from team + pitcher + park stats
   - Target = actual margin (home - away)
2. Apply exponential recency decay (lambda ~0.998/game)
3. Fit `sklearn.linear_model.RidgeCV` with alphas [0.1, 0.5, 1.0, 5.0, 10.0]
4. Save coefficients to `pyMLB/data/weights.json`
5. Fit separate ridge for totals (target = home + away runs)

**Historical odds integration:**
- Parse local odds archive for closing lines
- Validate: compare model projected spread vs historical closing line
- Seed calibration buckets from historical ATS results

**Warm start:**
- Backfill coefficients populate DEFAULT_W in the store
- Kalman state initialized fresh (roster turnover between seasons)
- Self-tune refines from game 1 of new season

---

## 7. MLB-Specific Considerations

### Domed stadiums (skip weather fetch)

```python
DOMED_STADIUMS = {
    "Tropicana Field",        # Tampa Bay
    "Rogers Centre",          # Toronto
    "loanDepot Park",         # Miami
    "American Family Field",  # Milwaukee (retractable)
    "Minute Maid Park",       # Houston (retractable)
    "Globe Life Field",       # Texas
    "T-Mobile Park",          # Seattle (retractable)
    "Chase Field",            # Arizona (retractable)
}
```

### Run line vs moneyline
- Standard MLB run line is fixed -1.5/+1.5
- Model projects margin -> converts to P(cover -1.5) and P(cover +1.5)
- Also computes moneyline implied probability from projected margin + variance
- Picks target run line OR moneyline depending on where edge is larger

### Day games vs night games
- Temperature adjustment uses game-time forecast, not daily high

### Doubleheaders
- Treated as independent games — both appear on ESPN scoreboard with separate game IDs
- No special adjustment needed

### All-Star break
- ~4 day gap mid-July. Kalman drift accumulates naturally. No special handling.

### Universal DH
- In effect since 2022. No AL/NL lineup split needed.

---

## 8. Shared Core Integration

| Shared module | MLB usage |
|---|---|
| `core/self_tune.py` | Bayesian weight updates after grading |
| `core/kalman_state.py` | Per-team strength drift tracking |
| `core/calibration.py` | P(cover) bucket analysis + dashboard output |
| `core/store.py` | JSON persistence for runs + weights |
| `core/email_report.py` | Daily pick email delivery |

MLB model engine (`pyMLB/scripts/model_engine.py`) is fully self-contained — it builds feature vectors and runs ridge projection without depending on `core/model_engine.py` (which is NBA stat-delta specific). Same pattern as `pyNFL/scripts/model_engine.py`.

---

## 9. 30 MLB Teams

```
AL East:  Baltimore Orioles, Boston Red Sox, New York Yankees, Tampa Bay Rays, Toronto Blue Jays
AL Central: Chicago White Sox, Cleveland Guardians, Detroit Tigers, Kansas City Royals, Minnesota Twins
AL West: Houston Astros, Los Angeles Angels, Oakland Athletics, Seattle Mariners, Texas Rangers
NL East: Atlanta Braves, Miami Marlins, New York Mets, Philadelphia Phillies, Washington Nationals
NL Central: Chicago Cubs, Cincinnati Reds, Milwaukee Brewers, Pittsburgh Pirates, St. Louis Cardinals
NL West: Arizona Diamondbacks, Colorado Rockies, Los Angeles Dodgers, San Diego Padres, San Francisco Giants
```

Full alias map (abbreviations, city names, nicknames) built in `defaults.py` following the NFL pattern.
