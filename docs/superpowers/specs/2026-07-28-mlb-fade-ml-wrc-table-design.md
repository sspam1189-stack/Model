# Team wRC+ Platoon Table (Fade ML tab) — Design

**Date:** 2026-07-28
**Status:** Built

## As-built note (supersedes the "Data source"/"Wiring" sections below)

During implementation we confirmed **FanGraphs' entire domain is behind a
Cloudflare bot-challenge** — every server-side request (local `requests` and,
worse, GitHub Actions) gets an HTTP 403 "Just a moment…" page, never data.
`pybaseball` breaks on the same wall. A **real browser passes the challenge**,
so the design pivoted (user-approved) to a **manual browser snapshot**:

- **True wRC+ retained** (park + league adjusted) — the metric the user wanted.
- Data captured through a browser from FanGraphs' *Splits Leaderboards*
  (`statgroup=2` Advanced carries the wRC+ column; `splitArr=1` vs LHP,
  `splitArr=2` vs RHP) and committed as a static `mlb-team-wrc.json`.
- **Not** fetched by the daily pipeline (it can't reach FanGraphs). It is a
  standalone file the dashboard loads directly, exactly like the hand-tails
  ledger — **not** stamped into `mlb-fade-ml.json`, so backfill is irrelevant.
- Refresh = re-capture via browser → update `build_team_wrc.py` → re-run. The
  script's docstring documents the exact URLs and steps.
- Season snapshot only (no Window dropdown); FanGraphs publishes season figures
  and there is no automated refresh to drive a live window control.

Files: `MLBstrikeouts/scripts/build_team_wrc.py` (snapshot + builder),
`MLBstrikeouts/data/mlb-team-wrc.json` + `PythonDashboard/data/mlb-team-wrc.json`
(generated), table render added to `PythonDashboard/js/mlb-fade-ml.js`.

---


## Purpose

Add a **reference/scouting table** at the bottom of the MLB Fade ML dashboard tab
showing each team's **wRC+ vs LHP** and **wRC+ vs RHP** (opposing-starter handedness
splits). Purely a "here's where each offense stands" lookup — **it does not feed the
fade decision or grading in any way**. It gives context for the fades on the slate:
when we fade a starter and bet the opponent's ML, this table lets the user eyeball how
that opponent's offense performs against the fade arm's hand.

## Non-goals

- **Not** an input to the fade model. No pick, gate, size, or grade reads this table.
  The fade model stays exactly as-is (rule-based flat betting; see
  `2026-07-17-mlb-fade-ml-model-design.md`).
- **Not** a self-computed / league-relative metric. We use FanGraphs' published
  **true wRC+** (park + league adjusted), not an in-house wOBA→wRC derivation.
- **Not** part of the walk-forward backfill. The table is a current snapshot; backfill
  never regenerates or depends on it.
- No auto-tie to tonight's games, no per-pick highlighting. Manual team filter only.

## Key decisions (decision log)

The design converged through discussion; recording the final calls and why:

1. **Metric: FanGraphs true wRC+ (park + league adjusted).** The user explicitly chose
   the real published stat over an in-house league-relative computation. 100 = league
   average; a team at 112 vs LHP is 12% better than average against left-handers.
2. **Source: scrape FanGraphs.** Acceptable here *because the table is view-only* — the
   usual objection to scraping FanGraphs (it can't give point-in-time values for the
   walk-forward backfill) does not apply, since nothing grades off this table.
3. **Window: season-to-date snapshot by default.** FanGraphs publishes a season figure,
   so the default is a season snapshot. **Conditional enhancement:** during
   implementation, verify whether FanGraphs' *Splits Leaderboards* endpoint accepts a
   date range. If it does, add a Window dropdown (Season / L30 / L14 / L7) on true wRC+.
   If it does not, ship the season snapshot with no window dropdown. Either way the
   metric is true wRC+.
4. **Split dimension: opposing-starter handedness (vs LHP / vs RHP), both columns shown.**
   No L/R toggle — both columns side by side so platoon gaps are scannable.

## Data source

New module: `MLBstrikeouts/scripts/sources/fangraphs_wrc.py`.

- **Primary fetch:** FanGraphs *Splits Leaderboards* (the undocumented POST endpoint
  used by <https://www.fangraphs.com/leaders/splits-leaderboards>), team grouping,
  split = "vs L" and "vs R", current season. Returns wRC+ per team per split.
  - **Verify at build time:** exact endpoint URL, request body/params, that team-level
    grouping returns aggregates (not per-player rows), and whether a `startDate`/`endDate`
    range is honored (decides the Window dropdown per decision 3).
- **Fallback:** if team-level platoon splits aren't cleanly available from that endpoint,
  aggregate **player** splits (vs L / vs R) to the team level, **PA-weighted**. Still
  FanGraphs, still true wRC+.
- **Caching:** dated cache file under the existing `CACHE_DIR`, same pattern as other
  `sources/*` fetchers (e.g. `fangraphs_wrc_<season>_<YYYYMMDD>.json`) → at most one
  FanGraphs request per day. Cache-hit path returns immediately.
- **Return shape:** `{ "LAD": {"vsLHP": 118, "vsRHP": 124}, ... }` keyed by the same team
  abbreviations used elsewhere in the repo (map FanGraphs team names/IDs → repo abbrs).

## Wiring

- **Live daily run** (`run_daily_ml.py`): after building the fade payload, call
  `fangraphs_wrc` and stamp the result into the Fade ML payload under a new top-level
  key, `teamWrc`. Shape:
  ```json
  "teamWrc": {
    "generated": "2026-07-28T12:00:00Z",
    "source": "fangraphs",
    "window": "season",            // or per-window object if the date range pans out
    "teams": { "LAD": {"vsLHP": 118, "vsRHP": 124}, ... }
  }
  ```
  If the date-range endpoint works, `teams` becomes per-window
  (`{"season": {...}, "L30": {...}, ...}`) and the dashboard exposes the Window dropdown.
- **Payload builder** (`fade_ml_common.py` `build_payload`): accept an optional
  `team_wrc` argument and include it in the payload when present. Absent → key omitted.
- **Backfill** (`ml_backfill.py`): does **not** fetch FanGraphs. It preserves the existing
  `teamWrc` block from the current JSON if one is present (read-existing → carry forward),
  so a re-grade never wipes the last live snapshot. (Consistent with the backfill-
  preservation rule for pending picks.)
- **Failure is non-fatal (fail-open):** any error fetching/parsing FanGraphs → skip the
  `teamWrc` block entirely; the fade model and the rest of the payload are unaffected.
  The dashboard hides the table when the block is missing.

## Display

Bottom of the Fade ML tab, `PythonDashboard/js/mlb-fade-ml.js`:

- **Section title:** "Team wRC+ by Opposing Starter Hand".
- **Controls:**
  - `[Team filter ▾]` — dropdown to isolate one team (default: all 30).
  - `[Window ▾]` — **only rendered if** the payload carries per-window data (decision 3).
- **Table:** rows = teams (all 30, or the filtered one), columns = **wRC+ vs LHP** and
  **wRC+ vs RHP**. Cells colored above / below 100 (green above, red below), same visual
  language as the rest of the dashboard. Sortable by either column.
- **Empty/missing:** if `teamWrc` is absent from the payload, render nothing (no error,
  no empty shell).
- **Small-sample guard (fallback/date-range case only):** if a split's underlying PA is
  below a threshold (thin windows), show "—" rather than a noisy value. Not needed for
  the full-season snapshot.

## Error handling

- FanGraphs unreachable / shape changed / empty → fail-open, omit `teamWrc`, log a warning.
  The daily run and fade grading proceed normally.
- Team-name → abbr mapping miss → drop that team from the table (don't crash), log it.
- Cache corruption → treat as cache-miss and refetch.

## Testing

- **Unit:** parser turns a captured FanGraphs splits response into the
  `{abbr: {vsLHP, vsRHP}}` shape; team-name→abbr mapping covers all 30; fallback
  aggregation is PA-weighted correctly.
- **Fail-open:** simulate a fetch error → `teamWrc` omitted, payload otherwise identical.
- **Wiring:** `build_payload` includes `teamWrc` when passed, omits it when not; backfill
  carries an existing block forward untouched.
- **Manual/dashboard:** table renders, team filter isolates a team, coloring keys off 100,
  sort works; table hidden when the block is absent.

## Open items (resolve during implementation)

1. Exact FanGraphs Splits Leaderboards endpoint (URL, POST body, team grouping) and
   whether it honors a date range — this single check decides snapshot-only vs the
   Window dropdown.
2. FanGraphs team identifier → repo abbreviation mapping (reuse `MLB_TEAM_ID_TO_ABBR` /
   name map if a suitable one already exists in `sources/mlb_stats.py`).
