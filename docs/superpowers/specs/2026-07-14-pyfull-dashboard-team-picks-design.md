# pyFull Dashboard: Team Picks Browser + Combined Weekly/Rolling — Design

**Date:** 2026-07-14
**Status:** Approved (design), pending implementation plan
**Author:** Claude + Henry

## Goal

Two additions to the shared full-season dashboard renderer in
`PythonDashboard/js/main.js`. Since this file already drives both the NBA
"Full Season" tab and the "WNBA Full" tab from the same functions (same
`runs`/game shape), both changes apply to both sports automatically — no
per-sport work needed.

1. **Team Picks browser** — let the user pick a team from a dropdown and see
   every pick the model has made for that team (graded + pending), not just
   the aggregate W-L-P record.
2. **Combine Weekly + Rolling** — merge the two separate "Weekly Spread" and
   "Rolling N-Pick Groups" cards into one card with a two-tab filter.

## Non-goals

- No changes to the Python model/engine code — this is dashboard-only.
- No changes to "Last 10 Spread" (`renderLast10`) — stays a separate card.
- No totals/props changes — spread (ATS) picks only, matching the existing
  "Team Spread Record (ATS)" table's scope.

## 1. Team Picks Browser

### Data layer

Add `computeTeamPicks(runs)` alongside the existing `computeTeamRecords(runs)`
in `main.js`. Walks the same `runs` → `run.games` structure, using the same
actionable-pick filter already used by `computeTeamRecords`:

```js
if (g.status === 'MISSING_ODDS' || g.status === 'SKIPPED') continue;
if (!g.sPick || g.sPick === 'PASS' || !isActionable(g.sConf)) continue;
```

Unlike `computeTeamRecords`, this does **not** require a final score — picks
without `homeScore`/`awayScore` are included and marked `pending: true`, so
today's/upcoming picks show up alongside history.

For each qualifying game, resolve the picked team via the existing
`parseSpreadPick(g.sPick)` helper and push a record onto that team's array:

```js
{
  date, dateDisplay, matchup,       // "{away} @ {home}"
  pick, conf,                       // g.sPick, g.sConf
  line, projMargin, pCover,
  result,                           // g.sResult || gradeSpread(g), or null if pending
  pending,                          // true if no final score yet
  final,                            // "{awayScore}-{homeScore}" or null
}
```

Returns `{ [teamName]: PickRecord[] }`, unsorted (sort happens at render time).

### UI

New card rendered directly after the existing `renderTeamRecords` output
(same "Team Records" section):

- `<select>` populated with every team key present in the `computeTeamPicks`
  map, alphabetically sorted, using the same inline-styled `<select>` pattern
  as `seasonSelector`/`nflWeekFilter` selects already in this file.
- Below it, a detail table with columns: Matchup, Pick (+ confidence badge),
  Line, Proj Margin, P(Cover), Result (badge, or a `PENDING` badge matching
  the style already used in `renderHistoryDay`), Final. Sorted newest pick
  first.
- Empty state: "No picks yet for this team" if the array is empty (shouldn't
  normally happen since the dropdown only lists teams with ≥1 pick).

### State wiring

Follows the existing `setSeasonFilter`/`setView` convention exactly — a
module-level variable plus a setter that mutates it and calls `render()`:

```js
let teamPicksFilter = null; // team name, or null = "not yet initialized"

function setTeamPicksFilter(team) {
  teamPicksFilter = team;
  render();
}
```

At render time, if `teamPicksFilter` is `null` or refers to a team with no
picks in the current `computeTeamPicks` result (e.g. after a season-filter
change), fall back to the alphabetically-first team key in the map instead of
rendering an empty table.

## 2. Combine Weekly + Rolling

Replace the two separate calls:

```js
html += renderWeekly(segRuns);
html += renderRolling(segRuns);
```

with a single `renderWeeklyRolling(segRuns)` that renders one
`card card-trends` containing:

- A two-button tab row at the top: **Weekly** / **Rolling** (same visual
  treatment as the existing `.view-btn`/`view-toggle` buttons used for
  Latest/History).
- Below the tabs, the body of whichever existing table is active — reuses
  the row-building logic currently inside `renderWeekly`/`renderRolling`
  unchanged (just relocated into the combined function), including the
  Rolling-only subtitle ("Green = above break-even...") which only shows
  when the Rolling tab is active.

### State wiring

```js
let spreadTrendsTab = 'weekly'; // default per design decision

function setSpreadTrendsTab(tab) {
  spreadTrendsTab = tab;
  render();
}
```

`renderWeekly` and `renderRolling` as standalone functions are removed (or
kept as internal helpers called by `renderWeeklyRolling` — implementation's
call) once merged; no other call sites reference them outside the one
replaced in the main render path.

## Testing

- No Python/model tests affected (dashboard-only change).
- Manual verification in the browser preview: load both the "Full Season"
  and "WNBA Full" tabs, confirm:
  - Team Picks dropdown lists teams, defaults to first alphabetically, shows
    graded + pending picks newest-first, switching teams updates the table.
  - Weekly/Rolling tabs toggle correctly, default to Weekly on load, Rolling
    subtitle only shows on the Rolling tab.
  - Existing widgets (Team Spread Record, Last 10, Cover Probabilities, etc.)
    are unaffected.
