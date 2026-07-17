# MLB Fade-List Moneyline Model — Design

**Date:** 2026-07-17
**Status:** Approved-pending-review

## Purpose

Bet the moneyline *against* pitchers on the manually-curated MLB "fade list." For each
game, grab **both** teams' FanDuel moneyline; when exactly one starter is a fade-list
pitcher, bet the **opponent** (the non-fade team). Produce a season backfill, live daily
picks, and a dashboard tab — using the **same caching, `run_daily`, and backfill mechanics
as the MLB K-props model**.

## Core Rules

1. **Odds capture:** for every game, store **both** teams' FanDuel h2h (home + away) in a
   per-date cache. The bet universe is only games with a fade starter, but odds are kept
   for both sides (needed to price the opponent and for symmetry with the K cache).
2. **Trigger:** a fade-list pitcher is a game's starter → bet that game's **opponent** ML.
3. **Mutual-fade skip:** if *both* starters are fade-list, **no bet**. Skip only when the
   opponent's fade status is **confirmed** (opponent starter known from data); if unknown,
   still bet.
4. **One bet per game**, deduped by team/opp/date and by commence_time (doubleheaders).
5. **Staking (house convention):**
   - Negative odds (opp favored): **risk-to-win-1u** — stake `|odds|/100` u, win `+1.00` u,
     lose `-(|odds|/100)` u.
   - Positive odds (opp underdog): **risk-1u** — stake `1` u, win `+odds/100` u, lose `-1.00` u.
   - **ROI = total profit / total risked.**
6. **Price = closing line**, achieved the same way the K model does it:
   - **Live:** `run_daily_ml` runs on the existing MLB daily cadence; it refetches odds for
     upcoming games (overwrite) and **freezes started games**, so the last pre-first-pitch
     run captures the near-closing number.
   - **Backfill:** true closing lines pulled from The Odds API **historical** snapshots at
     `commence − ~5 min`, prefetched into the same per-date cache.
7. **Voids:** postponed / suspended / no-price games are **VOID** — excluded from units and
   ROI, shown in the bet log with a reason.

## Mirrors of the K model (mechanics to copy)

| K-props file | Fade-ML analogue | Role |
|---|---|---|
| `sources/odds_fanduel.py` (live FanDuel API) | `odds_fanduel.py` + `fetch_fanduel_mlb_ml` | Live FanDuel ML (free) |
| `sources/odds_theoddsapi.py` (per-date cache, freeze) | `sources/odds_ml_theoddsapi.py` | Historical FanDuel ML + cache |
| `fetch_odds.py` (`fetch_historical_mlb_props_batch`) | `fetch_ml_odds.py` (`fetch_historical_mlb_ml_batch`) | Prefetch historical ML into cache |
| `run_daily.py` (Stage 0 grade → fetch → write ×2) | `run_daily_ml.py` | Live daily picks + grading |
| `props_backfill.py` (walk-forward, cache-only) | `ml_backfill.py` | Season backfill from cache |
| `data/props_cache/mlb/mlb_props_<date>.json` | `data/odds_cache/mlb_ml/mlb_ml_<date>.json` | Per-date permanent cache |
| `mlb-props.json` (×2 locations) | `mlb-fade-ml.json` (×2 locations) | Dashboard data |

## Data Sources (already integrated)

- **Fade trigger + who-started:** `mlb-props.json` `props[]` (`player`, `team`, `opp`,
  `date`, `market=="strikeouts"`). Season window: 2026-04-05 → today. Mutual-fade uses the
  reverse row (opponent's starter same date).
- **ML odds — FanDuel only, two transports** (mirrors the K model's "FanDuel primary,
  Odds API fallback"):
  - **Live (today):** FanDuel's own public API via `sources/odds_fanduel.py` — free, no
    key, no credits. Extend it with a game-moneyline fetch (the `MONEYLINE` marketType on
    the MLB event page). Fallback to The Odds API (FanDuel bookmaker) if the FD API fails.
  - **Historical (backfill):** FanDuel has **no** historical feed, so use The Odds API
    historical endpoint `/v4/historical/sports/baseball_mlb/odds/?regions=us&markets=h2h&oddsFormat=american&date=<ISO>`
    and keep **only the FanDuel bookmaker** (≈10 credits/snapshot). Key via `ODDS_API_KEY`
    env (as in the K workflow), never hardcoded.
  - Both transports write the same both-team rows into the per-date cache; a `source`
    field (`"fanduel_api"` / `"oddsapi_fanduel"`) records provenance.
- **Commence time + final score:** MLB Stats API schedule
  `schedule?sportId=1&date=<iso>&hydrate=linescore`, reusing `run_daily.py`'s schedule
  cache + void-status logic.
- **Team name ↔ abbrev:** reuse `_TEAM_ABBR` (odds_theoddsapi.py) / `MLB_NAME_TO_ABBREV`
  (sources/game_context.py). Props abbreviations (PHI, CHC, …) already match.

## Per-date ML cache (`data/odds_cache/mlb_ml/mlb_ml_<YYYYMMDD>.json`)

Same freeze/permanence rules as the K props cache:
- **Historical dates:** permanently cached — once written, never refetched (backfill reads
  cache only, 0 API calls).
- **Today, upcoming games:** refetch + overwrite each run (line moves toward closing).
- **Today, started games:** frozen (never overwritten) — captures the closing number.

Shape (one entry per game, both sides):
```json
[
  {"date":"2026-05-15","commence":"2026-05-15T22:41:00Z",
   "home":"PHI","away":"CHC",
   "home_ml":-134,"away_ml":116,"book":"fanduel","source":"oddsapi_fanduel",
   "started":true,"snapshot_ts":"2026-05-15T22:36:00Z"}
]
```

## Components

### 1. `MLBstrikeouts/scripts/fade_list.py`
Single source of truth: `FADE_LIST` (the 21 entries in `PythonDashboard/js/mlb-props.js`) +
`is_fade(name)` / `matched_entry(name)` using the same normalize + all-tokens-present
matching as the JS. The generated JSON also embeds the list so the tab renders from data.
(The dashboard's existing K-tab callout keeps its inline copy; DRYing the two is out of scope.)

### 2. ML-odds clients (both write the same per-date cache)
**`sources/odds_fanduel.py` (extend) — live FanDuel API:**
- `fetch_fanduel_mlb_ml(date_key)` — parse the `MONEYLINE` marketType off the MLB event
  page (same endpoints the file already uses for props), returning both-team ML per game.
  Free, no credits.

**`sources/odds_ml_theoddsapi.py` (new) — historical + fallback via The Odds API:**
- `fetch_historical_mlb_ml_batch(start, end, dry_run, delay)` — for each date, for each
  fade-relevant game (commence from schedule/first snapshot), pull the closing snapshot at
  `commence − 5min`, keep the FanDuel bookmaker's both-team ML, permanently cache. Cached
  dates skipped.
- `fetch_mlb_ml_live(date_key)` — live fallback (FanDuel bookmaker) when the FD API fails.

**Shared cache helpers** (in `odds_ml_theoddsapi.py`, used by both):
- `save_ml_cache(date_key, rows)` — applies the freeze/overwrite rule, writes the per-date file.
- `load_ml_cache(date_key)` — read the per-date cache (backfill, 0 API calls).

`run_daily_ml` prefers `fetch_fanduel_mlb_ml`, falling back to `fetch_mlb_ml_live`.

### 3. `MLBstrikeouts/scripts/fetch_ml_odds.py`
CLI mirroring `fetch_odds.py`: `--start --end [--dry-run --delay]` → calls
`fetch_historical_mlb_ml_batch`. Run before the backfill to populate the cache.

### 4. `MLBstrikeouts/scripts/run_daily_ml.py`
Mirrors `run_daily.py`:
- **Stage 0 — grade** prior `today[]` bets whose games are final (MLB schedule result →
  opp win = WIN / loss = LOSS / void), move them into `bets[]`.
- **Stage 1 — fetch** today's ML via `fetch_fanduel_mlb_ml` (free), falling back to
  `fetch_mlb_ml_live` (Odds API / FanDuel bookmaker); freeze rule applies on save.
- **Stage 2 — pick:** read today's fade starters from `mlb-props.json` slate; for each,
  bet opp ML from the cache; apply mutual-fade skip; compute stake.
- **Stage 3 — write** `mlb-fade-ml.json` to `MLBstrikeouts/data/` **and**
  `PythonDashboard/data/`, preserving already-graded `bets[]` and any still-pending
  `today[]` (backfill-preservation rule).
- Uses one shared `serialize_bet()` for both live and backfill paths so no field is dropped
  (avoids the K model's write-whitelist footgun).

### 5. `MLBstrikeouts/scripts/ml_backfill.py`
Mirrors `props_backfill.py`: walk dates from season start → today; for each date read the
cached ML + schedule results + fade triggers (from the props cache / `mlb-props.json`);
build graded `bets[]`; write the same JSON. **Reads cache only.** Preserves today's pending
picks. `--start-date` / `--season` CLI like the K backfill.

### 6. Dashboard tab "MLB Fade ML"
`PythonDashboard/js/mlb-fade-ml.js` (new) + tab registration + `index.html` wiring, styled
like existing tabs: record banner (W-L, units, ROI), "Today's plays"
(`• Fade <pitcher> (<fadeTeam>) → <betTeam> ML <odds>`), and a season bet log (VOID rows
greyed). Reuses existing table/banner CSS.

### 7. Workflow (`.github/workflows/mlb-run-daily.yml`)
Add, after the existing K steps (same runner, warm caches, same `ODDS_API_KEY`):
- a step running `python -m scripts.fetch_ml_odds --start <yesterday> --end <yesterday>`
  to freeze yesterday's closing lines for grading, then `python -m scripts.run_daily_ml`;
- upload/commit `data/odds_cache/mlb_ml` and copy `mlb-fade-ml.json` into
  `PythonDashboard/data/` in the commit-and-push job.
(The K steps are untouched — additive only.)

## Data Flow

```
The Odds API h2h ──► per-date ML cache (both teams, freeze rule)
mlb-props.json (props[]) ──► fade games ──► mutual-fade filter
MLB Stats API schedule ──► commence + result
        └──────────────► grade + stake (fade_list.py) ──► mlb-fade-ml.json (×2) ──► dashboard tab
live: run_daily_ml  |  history: fetch_ml_odds → ml_backfill (cache-only)
```

## Error Handling

- No FanDuel price after one nearest-snapshot retry → `VOID reason:"no_price"`.
- Schedule/result unavailable or void status → `VOID reason:"postponed"` / `"no_result"`.
- Odds API non-200 in backfill/prefetch → raise (cache protects credits); in the daily run,
  catch + log + continue so the K pipeline is unaffected.
- Team-name match failure → log + VOID (never silently mis-grade).

## Testing / Verification

- Unit: `is_fade` (surnames/full names, rejects other "Jones"); staking math for −134 and
  +116; mutual-fade skip fires only when both starters are fade.
- Unit: cache freeze rule — started game not overwritten, upcoming game overwritten.
- Integration: `fetch_ml_odds` one week → `ml_backfill` same week; grades match known final
  scores; inspect JSON.
- End-to-end: full-season backfill, then the dashboard tab renders banner + log; a live
  `run_daily_ml` dry pass on today's slate produces sane picks.

## Out of Scope (YAGNI)

- Priced/edge model — this is rule-based flat betting.
- Multi-book line shopping — FanDuel only.
- DRYing the dashboard's inline K-tab fade list into `fade_list.py`.
- Auto-tuning the fade roster.

## Cost Note

Historical prefetch ≈ (fade-relevant games) snapshots × 10 credits; full-season ballpark a
few thousand of ~64,700 remaining. Permanent caching makes re-runs free.
