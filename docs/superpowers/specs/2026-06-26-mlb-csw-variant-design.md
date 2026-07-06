# MLB K's CSW — Parallel Model Variant

**Date:** 2026-06-26
**Status:** Retired 2026-07-06 — the daily parallel variant is now whiff
blend-0.4 (see [2026-07-06-mlb-whiff04-variant.md](2026-07-06-mlb-whiff04-variant.md)).
The `MLB_K_METRIC=csw` profile remains available for manual runs.

## Goal

Run a **second** MLB strikeout-props model daily, in parallel with the live model, and surface it as its own dashboard tab. The two models share all code and raw data; they differ only in configuration.

- **MLB K's Whiff** — the existing/live model (unchanged).
- **MLB K's CSW** — new parallel variant with its own history, state, caches, daily run, and dashboard tab.

## Configuration difference

The two variants differ in exactly two settings; everything else is identical.

| Setting | MLB K's Whiff (live) | MLB K's CSW (new) |
|---|---|---|
| `K_QUALITY_METRIC` | `whiff` | `csw` |
| `CSW_XBA_BLEND_WEIGHT` | `0.4` | `0.4` |
| `K_RATE_CAP_FLOOR` | `0.36` | `0.40` |
| `VAR_MULT["strikeouts"]` | `1.30` | `1.20` |
| pCover pick threshold | `0.70` | `0.70` |

(`VAR_MULT 1.20` is the pre-whiff/csw-era std value — consistent with the csw distribution; whiff moved it to 1.30. See [defaults.py:164–178](../../../MLBstrikeouts/scripts/defaults.py).)

The `csw` metric path is already fully implemented in the engine (it is the current fallback at [defaults.py:428](../../../MLBstrikeouts/scripts/defaults.py)), so no new model logic is required — only configuration wiring, output isolation, a backfill, CI, and dashboard.

## Non-goals

- No change to the live Whiff model's behavior, outputs, or files (must stay byte-for-byte identical when no variant env var is set).
- No new markets or model math. CSW is a full parallel run; only strikeout projections differ (the metric only affects K). Outs / hits-allowed / game-hits come out the same as Whiff and are kept for structural parity.
- No custom/trimmed CSW dashboard view — the CSW tab reuses the existing renderer.

## Architecture

### 1. Variant selection & config (a "variant profile")

In `defaults.py`, read one env var (replacing the constant at line 428) and apply the per-variant overrides as a **single consolidated block at the END of the file**, so it cleanly supersedes the base definitions of `K_RATE_CAP_FLOOR` (line 552) and `VAR_MULT` (line 139) regardless of their definition order:

```python
# top of file (replaces the K_QUALITY_METRIC constant at line 428)
import os
K_QUALITY_METRIC = os.environ.get("MLB_K_METRIC", "whiff")

# ... all base constants defined as today (K_RATE_CAP_FLOOR = 0.36, VAR_MULT = {"strikeouts": 1.30}, ...)

# ---- end of file: variant profile overrides ----
if K_QUALITY_METRIC == "csw":
    K_RATE_CAP_FLOOR = 0.40
    VAR_MULT = {"strikeouts": 1.20}
    VARIANT_SUFFIX  = "_csw"
else:                       # whiff — base values unchanged from today
    VARIANT_SUFFIX  = ""
```

- Default (env var unset) = `whiff` = exactly today's behavior (base constants untouched). **Lowest risk.**
- `CSW_XBA_BLEND_WEIGHT` stays `0.4` for both (no change).
- CSW overrides three knobs: metric `csw`, cap floor `0.40`, `VAR_MULT["strikeouts"] 1.20`.
- An optional `--metric {whiff,csw}` CLI flag on `run_daily` and `props_backfill` overrides the env var, for local/manual runs. CI uses the env var.

### 2. Output isolation (config-dependent paths get `VARIANT_SUFFIX`)

Only these outputs differ between variants and must be separated:

| Output | Whiff path (unchanged) | CSW path |
|---|---|---|
| Kalman state | `MLBstrikeouts/data/kalman_state.json` | `…/kalman_state_csw.json` |
| Picks (model copy) | `MLBstrikeouts/data/mlb-props.json` | `…/mlb-props_csw.json` |
| Picks (dashboard copy) | `PythonDashboard/data/mlb-props.json` | `…/mlb-props_csw.json` |
| Empirical-std cache dir | `data/emp_std_cache/mlb/` | `data/emp_std_cache/mlb_csw/` |

Touch points to thread `VARIANT_SUFFIX` through:
- `run_daily.py`: `KALMAN_STATE_PATH` (~line 71), `_EMP_STD_CACHE_DIR` (~line 110), `output_paths` (~lines 1307–1310).
- `props_backfill.py`: `_EMP_STD_CACHE_DIR` (~line 42), kalman_state path (~lines 312–318), `write_dashboard_json` output `paths` (~lines 1058–1061).

The per-date regression-slope cache is **already metric-named** (`whiff_xba_regression_*` vs `csw_xba_regression_*`), so it self-isolates. **Verify during implementation**; if not metric-named, apply the same suffix treatment.

### 3. Shared (config-independent) — left exactly as-is

All raw-data caches are identical regardless of metric and are shared (no extra API load, no duplication): pitcher game logs, team batting, pitcher advanced stats, handedness splits, batter K%, batting orders, pitch hands, weather, probable pitchers, savant raw rates, lineups. The CSW-only `csw_tallies_*` (statcast pitch-level) live in the shared `pitcher_cache/mlb/` dir with distinct names and are only produced by the CSW run.

### 4. Daily run (CI)

`.github/workflows/mlb-run-daily.yml` is triggered externally at 6 times/day and runs `python -m scripts.run_daily` on a self-hosted runner, then a separate job commits with `git add -A`.

Change: add a **second step in the same `mlb-props` job**, after the Whiff step, that runs the CSW variant on the same runner (raw caches already warm):

```yaml
- name: Run daily MLB props (CSW variant)
  working-directory: MLBstrikeouts
  env:
    PYTHONUTF8: "1"
    PYTHONUNBUFFERED: "1"
    PYTHONIOENCODING: "utf-8"
    ODDS_API_KEY: ${{ secrets.ODDS_API_KEY }}
    MLB_K_METRIC: csw
  run: python -m scripts.run_daily
```

- Whiff step unchanged.
- `git add -A` already picks up the new `_csw` files; extend the commit message to note both variants (e.g. `Run daily MLB Pitcher Props (Whiff + CSW) + Game Hits [skip ci]`).
- Serial execution on one runner avoids races on shared caches; both variants reflect the same slate.

### 5. Backfill (seed CSW history)

Run once to produce the CSW season history, mirroring the Whiff ship pattern:

```bash
cd MLBstrikeouts && MLB_K_METRIC=csw python -m scripts.props_backfill
```

Produces `mlb-props_csw.json` (both copies) + `kalman_state_csw.json` + `emp_std_cache/mlb_csw/*` under the CSW config (csw metric, cap floor 0.40). Back up existing files first per standard practice. Whiff's files are not touched (different paths).

### 6. Dashboard

- `PythonDashboard/index.html` (~line 23): rename existing tab label `MLB Strikeouts` → **`MLB K's Whiff`**; add a sibling button `data-tab="mlb-props-csw"` labeled **`MLB K's CSW`**.
- `PythonDashboard/js/main.js`:
  - SOURCES (~lines 160–165): add `mlb-props-csw` entry → local `data/mlb-props_csw.json`, remote GitHub raw `…/MLBstrikeouts/data/mlb-props_csw.json`.
  - labels map (~line 1129): `mlb-props` → `MLB K's Whiff`; add `mlb-props-csw` → `MLB K's CSW`.
  - render dispatch (~lines 1649–1656): route `activeTab === 'mlb-props-csw'` to the renderer.
- `PythonDashboard/js/mlb-props.js`: **parameterize** `renderMLBProps(sourceKey = 'mlb-props', title)` so the CSW tab reuses the identical renderer and all downstream cards (today's picks, season W-L/units, calibration, P&L, matchup history) work off whichever data file is active. Make the hardcoded `MLB Strikeouts` title (~line 178) and last-run string (~line 189) dynamic from the labels map. `renderMLBPropsCSW()` is a one-line wrapper calling `renderMLBProps('mlb-props-csw', "MLB K's CSW")`.

### 7. Data files (new)

`MLBstrikeouts/data/mlb-props_csw.json`, `PythonDashboard/data/mlb-props_csw.json`, `MLBstrikeouts/data/kalman_state_csw.json`, `data/emp_std_cache/mlb_csw/*` — all generated by the CSW backfill/daily run; same JSON schema as the Whiff files.

## Ship sequence

1. Code: `defaults.py` variant profile + `VARIANT_SUFFIX` threading in `run_daily.py` / `props_backfill.py` + optional `--metric` flag.
2. Verify Whiff is unchanged: run `python -m scripts.run_daily --date <recent>` (no env var) and confirm it writes the same un-suffixed paths and matches prior output.
3. CSW clean backfill (`MLB_K_METRIC=csw`) → seeds CSW history + state.
4. Dashboard wiring (rename + second tab + parameterized renderer).
5. Verify both tabs locally (Whiff unchanged, CSW populated with its own record).
6. CI: add the second daily step.
7. Commit. Push only when the user asks.

## Risks & mitigations

- **Live model regression.** Mitigated by env-var default = whiff = current behavior, and by step-2 verification that un-suffixed outputs are unchanged.
- **Regression-cache collision.** Verify metric-named (likely already isolated); suffix if not.
- **CI race on shared caches.** Avoided by serial steps in one job on one runner.
- **Backfill drops today's pending CSW picks.** Same accepted behavior as Whiff backfill; next daily CSW run refills.
- **`git add -A` scope.** Confirm it captures `_csw` files and the new emp_std dir; no `.gitignore` excludes them.

## Verification / testing

- Whiff parity: diff un-suffixed outputs before/after the code change for a fixed `--date` — must be identical.
- CSW backfill: sanity-check summary (W-L / units / picks) is plausible and distinct from Whiff.
- Dashboard: both tabs load, switch, and render records from their respective files; Whiff tab still labeled and populated correctly.

## Implementation notes (as-built, 2026-06-26)

Deviations from the design discovered/decided during implementation:

- **Filename separator.** `VARIANT_SUFFIX = "_csw"` yields `mlb-props_csw.json` / `kalman_state_csw.json` (underscore, consistent across all outputs). The dashboard SOURCES key stays the hyphen form `mlb-props-csw` (internal tab id); only the file path uses the underscore.
- **`--metric` CLI flag dropped.** `defaults` reads `MLB_K_METRIC` at import time, so a `__main__` flag would need an `importlib.reload` footgun. The env var is the sole mechanism (CI sets it; local: `MLB_K_METRIC=csw python -m scripts.run_daily`).
- **More read-sites than estimated in `run_daily.py`.** Beyond the three write paths, the prior-props *reads* also needed the suffix: `grade_previous_picks` (~486), `compute_empirical_std_from_graded` (~134), `compute_calib_coefs_from_graded` (~230), and the two lock-state `prior_path` reads (~1106, ~1137) — all threaded so CSW reads its own history.
- **Backfill priming made metric-aware.** `props_backfill.py`'s "today's slope priming" block previously always wrote the *whiff* regression cache; in csw mode it now builds the merged csw snapshot and writes only the `csw_xba_regression` cache, so a CSW backfill leaves all whiff files byte-for-byte unchanged (verified via git diff: only timestamps changed on whiff re-run).
- **Renderer wrapper.** Instead of a separate `renderMLBPropsCSW()`, the dispatch calls the parameterized `renderMLBProps('mlb-props-csw', "MLB K's CSW")` directly.
- **Results.** Whiff backfill 190-61 (75.7%) +113.8u / 251 picks; CSW backfill 196-66 (74.8%) +113.8u / 262 picks. Both tabs verified in-browser rendering their own records.
