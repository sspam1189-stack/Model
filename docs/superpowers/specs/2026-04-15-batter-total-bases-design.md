# Batter Total Bases Prop Engine — Design Spec

**Date:** 2026-04-15
**Status:** Approved
**Scope:** Add batter total bases (TB) prop projections to the MLB model

---

## Overview

Add a batter total bases projection engine to `MLBstrikeouts/`. Unlike pitcher props which model a single stat directly, TB requires a **decomposition model** — project plate appearances, then model hit-type outcome rates (HR, XBH, 1B), then sum weighted TB. A Monte Carlo simulation produces the cover probability distribution since TB is zero-inflated and right-skewed (normal/t-distribution assumptions fail here).

Both OVER and UNDER directions are supported.

---

## Architecture

### Core Idea

```
E[TB] = PA × (1×P(1B) + 2×P(XBH) + 4×P(HR))
```

Triples folded into XBH as a blended weight (~2.1 avg bases per XBH).

### Pipeline (per batter, per game)

```
1. Project PA       → lineup slot + game pace + pitcher efficiency
2. Model rates      → K%, BB%, HR%, XBH%, 1B% per PA (splits + Statcast + matchup)
3. Adjust rates     → park factor, weather, handedness
4. Compute E[TB]    → PA × tb_per_pa
5. Kalman blend     → 60% rate model, 40% Kalman-smoothed TB baseline
6. Simulate         → 5,000 iterations, multinomial sampling per PA
7. Cover prob       → P(Over) and P(Under) from simulation distribution
```

---

## Data Layer

### New functions in `scripts/sources/mlb_stats.py`

#### `fetch_batter_game_logs(season)`

Extract batter stats from the **same boxscore payload** already fetched by `fetch_pitcher_game_logs`. The boxscore `side_data["players"]` contains every batter's hitting stats. No additional API calls — second pass over cached data.

Returns:
```python
{batter_id: [{
    "game_date": str,
    "team": str,
    "opp": str,
    "pa": int,
    "ab": int,
    "h": int,
    "doubles": int,
    "triples": int,
    "hr": int,
    "bb": int,
    "k": int,
    "hbp": int,
    "tb": int,           # computed: h + 2b + 2*3b + 3*hr
    "lineup_slot": int,  # 1-9 batting order position
    "opp_pitcher_id": int,
    "opp_pitcher_hand": str,  # "L" or "R"
    "is_home": bool,
}, ...]}
```

Implementation: Separate function that re-reads the cached boxscore data (already saved by `fetch_pitcher_game_logs`). Does NOT modify the pitcher function — reads the same cached JSON and extracts batter stats instead. Cache batter logs separately at `batter_game_logs_{season}.json`. If boxscore cache doesn't exist yet, triggers the same boxscore fetch logic.

#### `fetch_savant_batter_rates(season, min_pa=50)`

Clone of existing `fetch_savant_pitcher_rates` with `type=batter` and batter-relevant selections.

URL:
```
https://baseballsavant.mlb.com/leaderboard/custom
  ?year={season}&type=batter&filter=&min={min_pa}
  &selections=barrel_batted_rate,hard_hit_percent,xslg,xwoba,iso,
              avg_hit_speed,flyballs_percent,groundballs_percent
  &chart=false&csv=true
```

Returns:
```python
{player_id_str: {
    "barrel_pct": float,      # barrel rate — best HR predictor
    "hard_hit_pct": float,    # hard-hit% — XBH predictor
    "xslg": float,            # expected slugging
    "xwoba": float,           # expected wOBA
    "iso": float,             # isolated power (SLG - BA)
    "avg_ev": float,          # average exit velocity
    "fb_pct": float,          # fly ball %
    "gb_pct": float,          # ground ball %
}}
```

One CSV download, all batters, free, no auth. Cached 24 hours.

#### `fetch_batter_splits(season)`

Batter hitting splits vs LHP and RHP. Uses MLB Stats API bulk endpoint with `stats=vsLeftRight`.

Returns:
```python
{batter_id: {
    "vs_lhp": {"ba": float, "slg": float, "iso": float, "hr_rate": float, "k_pct": float, "pa": int},
    "vs_rhp": {"ba": float, "slg": float, "iso": float, "hr_rate": float, "k_pct": float, "pa": int},
}}
```

Bulk fetch (2 API calls — overall + splits). Cached 24 hours.

### New file: `scripts/sources/park_factors.py`

Self-derived rolling park factors computed from batter game log data. No external API calls.

**Approach:** For each ballpark, aggregate batter stats (HR, XBH, 1B, TB) from game logs played at that park vs. the league average. Rolling calculation that updates as season progresses.

```python
def compute_park_factors(batter_game_logs, min_games=20):
    """
    Compute park factors from game log data.

    Returns:
        {team_abbr: {"hr": float, "xbh": float, "single": float, "tb": float}}

    Factors are ratios vs league average (1.0 = neutral).
    Falls back to 1.0 for parks with < min_games.
    """
```

**Early season handling:** Until a park has 20+ games in the current season, fall back to a hardcoded prior-year baseline (static dict in the same file). Blend current-year data in as sample grows: `factor = (n × current + 20 × prior) / (n + 20)` (Bayesian shrinkage toward prior).

---

## Projection Engine: `scripts/batter_props_engine.py`

### Step 1 — Project PA

```python
LINEUP_SLOT_PA = {
    1: 4.5, 2: 4.4, 3: 4.3, 4: 4.2, 5: 4.1,
    6: 4.0, 7: 3.9, 8: 3.8, 9: 3.7,
}
```

```
adjusted_pa = base_pa
    × game_pace_factor       # team implied runs / league avg runs (~4.5)
    × pitcher_efficiency     # high-K pitcher = fewer baserunners = fewer PA for later slots
```

If lineup not yet posted, use batter's rolling 15-game average PA.

### Step 2 — Model outcome rates per PA

For each PA, mutually exclusive outcomes with probabilities:

| Outcome | How modeled |
|---------|-------------|
| **K** | Batter K% vs pitcher hand, anchored to Savant whiff tendency |
| **BB** | Batter BB% vs pitcher hand |
| **HBP** | Flat ~1% (stable, not worth modeling) |
| **HR** | `f(batter_barrel%, batter_fb%, pitcher_hr_fb%, park_hr_factor, weather)` |
| **XBH (2B+3B)** | `f(batter_hard_hit%, batter_iso - hr_component, park_xbh_factor)` |
| **1B** | `contact_rate × BABIP_adj - HR_rate - XBH_rate` |
| **Out (in play)** | Remainder: `1 - K - BB - HBP - HR - XBH - 1B` |

**Rate adjustments applied:**
- **Pitcher matchup:** Pitcher's barrel% allowed, hard-hit% allowed, K% from existing Savant pitcher data
- **Park factor:** Self-derived rolling factor per component (HR, XBH, 1B)
- **Weather:** Reuse existing `compute_weather_multiplier`, extended for power (wind out → HR boost, cold → contact suppression)
- **Handedness:** Batter's split rates vs LHP/RHP matched to today's starter

### Step 3 — Compute E[TB]

```python
tb_per_pa = 1 * p_single + 2.1 * p_xbh + 4 * p_hr
# 2.1 accounts for ~10% of XBH being triples (3 TB) vs 90% doubles (2 TB)

e_tb = projected_pa * tb_per_pa
```

### Step 4 — Kalman blend

Kalman tracks two state variables per batter (see batter_kalman.py section):
- **ISO** (isolated power) — stabilizes power projection
- **Contact rate** (1 - K%) — stabilizes opportunity projection

Kalman-smoothed TB = `kalman_iso × PA × weight + kalman_contact × base_hit_rate × PA × weight`

Final projection:
```
proj_tb = 0.60 × rate_model_tb + 0.40 × kalman_smoothed_tb
```

### Step 5 — Simulation (5,000 iterations)

```python
for each iteration:
    sim_pa = poisson.rvs(projected_pa)
    sim_tb = 0
    for each PA:
        outcome = multinomial_sample([p_k, p_bb, p_hbp, p_hr, p_xbh, p_1b, p_out])
        sim_tb += tb_value[outcome]  # {hr: 4, xbh: 2, 1b: 1, else: 0}
    results.append(sim_tb)

p_over = mean(results > line)
p_under = mean(results < line)
# Handle push (results == line) by splitting evenly
```

This correctly captures:
- PA variance (Poisson)
- Outcome variance (multinomial)
- Zero-inflation (many 0-TB games)
- Right skew (HR games = 4+ TB spikes)

---

## Kalman Filter: `scripts/batter_kalman.py`

Adapted from `pitcher_kalman.py` with batter-specific parameters.

### Hyperparameters

```python
BATTER_KALMAN_DEFAULTS = {
    "gameDrift": 0.15,        # per day (batters play daily, less drift than pitchers' 0.5)
    "obsNoise": {
        "iso": 0.05,          # ISO is noisy per game but stable over 15+ games
        "contact": 0.03,      # contact rate is more stable
    },
    "initialVar": 4.0,        # lower than pitcher (25.0) — more games = faster convergence
    "minVar": 0.01,
    "maxVar": 10.0,
    "minGamesForKalman": 10,  # need ~2 weeks of daily play
    "kalmanBlend": 0.4,       # 40% Kalman, 60% rate model (rate model is primary here)
}
```

### State per batter

```python
{
    "mean_iso": float,
    "var_iso": float,
    "mean_contact": float,
    "var_contact": float,
    "last_game_date": str,
    "games_tracked": int,
}
```

### Update logic

Same Kalman math as pitcher version:
```
prior_var += days_since_last_game × gameDrift
kalman_gain = prior_var / (prior_var + obsNoise)
mean += gain × (observed - mean)
var = (1 - gain) × prior_var
```

Observed ISO per game = (TB - H) / AB (or 0 if AB = 0).
Observed contact = 1 - (K / PA).

---

## Odds Integration

### FanDuel (`odds_fanduel.py`)

Add to `FD_PROP_TABS`:
```python
"batter-total-bases",
"batter-props",
"batter-hits",        # TB lines sometimes appear here
```

Add `FD_BATTER_MARKET_MAP`:
```python
FD_BATTER_MARKET_MAP = {
    "TOTAL_BASES": "total_bases",
    "BATTER_TOTAL_BASES": "total_bases",
    "PLAYER_TOTAL_BASES": "total_bases",
    "TOTAL_BATTER_TOTAL_BASES": "total_bases",
}
```

Extend `_match_fd_market_type` to check batter market map alongside pitcher map.

### Odds API (`odds_theoddsapi.py`)

Add market key `batter_total_bases` to a new `BATTER_PROP_MARKETS_API` list. Same per-event fetch pattern. Used as fallback to FanDuel and for historical data in backfill.

### Merge logic

Same pattern as pitcher props: FanDuel primary (free), Odds API fallback (saves credits). Match batter by name normalization using existing `_name_key` pattern.

---

## Filtering and Thresholds

### New constants in `defaults.py`

```python
# Batter props — total bases
BATTER_MIN_GAMES = 10          # ~2 weeks of daily play
BATTER_MIN_PA = 30             # minimum season PA to qualify
BATTER_MARKET_THRESHOLDS = {
    "total_bases": {"high": 0.58},  # both directions
}
BATTER_MIN_EDGE = {"total_bases": 0.0}
BATTER_CONFIDENCE_FLOOR = 0.52  # don't pick if simulation too noisy

# Lineup confirmation required — no lineup = no pick
BATTER_REQUIRE_LINEUP = True

# Paired teammate filter — keep best pCover per team
BATTER_PAIRED_FILTER = True
```

---

## Output Format

Integrate into existing `mlb-props.json`:

```json
{
    "sport": "mlb",
    "type": "pitcher_props",
    "date": "2026-04-15",
    "props": [...],
    "todayProjections": [...],
    "batterProps": [
        {
            "player": "Aaron Judge",
            "player_id": 592450,
            "team": "NYY",
            "opp": "BOS",
            "opp_pitcher": "Brayan Bello",
            "opp_pitcher_hand": "R",
            "market": "total_bases",
            "line": 1.5,
            "proj": 1.82,
            "pCover": 0.63,
            "pick": "OVER",
            "edge": 0.32,
            "lineup_slot": 2,
            "proj_pa": 4.3,
            "hr_rate": 0.068,
            "xbh_rate": 0.052,
            "single_rate": 0.142,
            "iso": 0.295,
            "park_tb_factor": 1.02,
            "date": "2026-04-15"
        }
    ],
    "batterProjections": [...]
}
```

---

## Daily Pipeline Integration (`run_daily.py`)

Add 3 new stages after pitcher projection stages:

```
Stage 15b: Fetch batter data
  - fetch_batter_game_logs (from cached boxscores, no new API calls)
  - fetch_savant_batter_rates (1 CSV download)
  - fetch_batter_splits (2 API calls)
  - compute_park_factors (from game logs, no API calls)

Stage 15c: Project batter TB
  - Load batter Kalman state
  - Update Kalman with new games
  - Run decomposition + simulation for all confirmed lineup batters with lines
  - Filter picks by thresholds

Stage 15d: Merge output
  - Add batterProps and batterProjections to mlb-props.json
  - Save batter Kalman state to data/batter_kalman_state.json
```

---

## Backfill: `scripts/batter_props_backfill.py`

Replay the season day by day to bootstrap Kalman state and validate the model.

### Pipeline

```
1. Load all boxscore game logs (already cached)
2. For each date with completed games:
   a. Build batter Kalman state up to that date
   b. Fetch historical odds (Odds API cache or props_cache)
   c. Run projection engine as if it were that date
   d. Grade against actual TB from game logs
3. Output results CSV: hit rate, calibration, ROI by threshold
4. Save fully warmed Kalman state
```

### Output

```
data/backfill/batter_tb_backfill_2026.csv
```

Columns: `date, player, team, opp, line, proj, pCover, pick, actual_tb, hit, edge`

This also enables threshold sweeping — find optimal pCover cutoff for OVER vs UNDER separately.

### Historical odds

Extend `odds_theoddsapi.py` to fetch historical `batter_total_bases` market data. Same `fetch_mlb_pitcher_props` pattern with the batter market key. Results cached in `data/props_cache/mlb/`.

---

## File Structure

### New files

| File | Purpose |
|------|---------|
| `scripts/batter_props_engine.py` | Core: PA projection, outcome rates, TB decomposition, simulation |
| `scripts/batter_kalman.py` | Kalman filter for ISO + contact rate per batter |
| `scripts/sources/park_factors.py` | Self-derived rolling park factors from game log data |
| `scripts/batter_props_backfill.py` | Season replay: backfill Kalman + grade projections vs actuals |

### Modified files

| File | Changes |
|------|---------|
| `scripts/sources/mlb_stats.py` | Add `fetch_batter_game_logs`, `fetch_savant_batter_rates`, `fetch_batter_splits` |
| `scripts/sources/odds_fanduel.py` | Add batter prop tabs + `FD_BATTER_MARKET_MAP` |
| `scripts/sources/odds_theoddsapi.py` | Add `batter_total_bases` market key + historical fetch |
| `scripts/defaults.py` | Add batter constants (thresholds, Kalman params, PA table) |
| `scripts/run_daily.py` | Add batter data fetch + projection + output merge stages |

### Shared infrastructure (unchanged)

- Boxscore cache — batter logs extracted from same cache
- `sources/weather.py` — reuse `compute_weather_multiplier`
- `sources/game_context.py` — reuse lineup slot helpers
- Name normalization (`_name_key`) — reuse from `props_engine.py`
