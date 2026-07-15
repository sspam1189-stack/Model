# pyFull Dashboard: Team Picks Browser + Combined Weekly/Rolling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-team pick browser (dropdown + detail table) and merge the Weekly/Rolling spread-trend cards into one tabbed card, in the shared dashboard renderer used by both the NBA "Full Season" and "WNBA Full" tabs.

**Architecture:** All changes live in one file, `PythonDashboard/js/main.js`, a classic (non-module) script loaded by `PythonDashboard/index.html`. It's a template-string renderer: module-level state vars + setter functions that mutate state and call the top-level `render()`, which rebuilds the active tab's HTML from scratch (see existing `setSeasonFilter`/`setView`). No build step, no test framework — verification is (a) Node one-liners against pure template/data functions using fixture data, and (b) a final manual pass in the browser preview.

**Tech Stack:** Vanilla JS (ES2019, no modules), template strings for HTML, plain CSS in `PythonDashboard/styles.css`. Node.js (already on PATH) used only as a scratch harness to verify pure functions before wiring them into `main.js`.

---

## Reference: existing code this plan builds on

`PythonDashboard/js/main.js` currently has (verified by reading the file — line numbers may drift by a line or two as earlier tasks land, always re-grep before editing):

```js
// ~line 204-206 — module-level filter state
let seasonFilter = 'all';
let nflWeekFilter = 'latest';
let nflHistoryWeekFilter = 'all';

// ~line 260-266
function setSeasonFilter(val) {
  seasonFilter = val;
  nflWeekFilter = 'latest';
  nflHistoryWeekFilter = 'all';
  historyPage = 0;
  render();
}

// ~line 471-516 — shared helpers used throughout
function esc(v) { return String(v ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function fmtNum(v, d) { return Number.isFinite(v) ? v.toFixed(d) : '—'; }
function fmtProb(v) { return Number.isFinite(v) ? (v * 100).toFixed(2) + '%' : '—'; }
function confBadge(conf) {
  const c = String(conf || '').toLowerCase();
  const cls = c === 'elite' ? 'b-elite' : c === 'high' ? 'b-high' : 'b-pass';
  return `<span class="badge ${cls}">${(c || 'N/A').toUpperCase()}</span>`;
}
function resultBadge(result) {
  if (!result) return '';
  const r = result.toLowerCase();
  return `<span class="result-badge ${r}">${result}</span>`;
}
function isActionable(conf) {
  const c = String(conf || '').trim().toLowerCase();
  return c === 'elite' || c === 'high';
}
function parseSpreadPick(pick) {
  const m = pick.match(/^(.+?)\s+([+-])(\d+(?:\.\d+)?)$/);
  if (!m) return null;
  return { team: m[1], sign: m[2], spread: parseFloat(m[3]) };
}
function gradeSpread(g) {
  if (!Number.isFinite(g.homeScore) || !Number.isFinite(g.awayScore)) return null;
  if (!g.sPick || g.sPick === 'PASS') return null;
  const parsed = parseSpreadPick(g.sPick);
  if (!parsed) return g.sResult || null;
  const margin = g.homeScore - g.awayScore;
  // (full body continues in the real file — not reproduced here, unchanged by this plan)
}

// ~line 650-673 — existing aggregate-only team stats (untouched by this plan)
function computeTeamRecords(runs) {
  const teams = {};
  for (const r of runs) {
    if (r.burnIn) continue;
    for (const g of r.games || []) {
      if (g.status === 'MISSING_ODDS' || g.status === 'SKIPPED') continue;
      if (!g.sPick || g.sPick === 'PASS' || !isActionable(g.sConf)) continue;
      if (!Number.isFinite(g.homeScore) || !Number.isFinite(g.awayScore)) continue;
      const result = g.sResult || gradeSpread(g);
      if (!result) continue;
      const parsed = parseSpreadPick(g.sPick);
      if (!parsed) continue;
      const team = parsed.team;
      if (!teams[team]) teams[team] = { w: 0, l: 0, p: 0, picks: 0, fav: 0, dog: 0 };
      teams[team].picks++;
      if (result === 'WIN') teams[team].w++;
      else if (result === 'LOSS') teams[team].l++;
      else teams[team].p++;
      if (parsed.sign === '-') teams[team].fav++;
      else teams[team].dog++;
    }
  }
  return teams;
}

// ~line 1066-1099 — existing aggregate table renderer (untouched by this plan)
function renderTeamRecords(runs) {
  const teams = computeTeamRecords(runs);
  const sorted = Object.entries(teams)
    .filter(([, t]) => t.w + t.l >= 3)
    .sort((a, b) => calcUnits(b[1].w, b[1].l) - calcUnits(a[1].w, a[1].l));
  if (!sorted.length) return '';
  const rows = sorted.map(([name, t]) => {
    const total = t.w + t.l;
    const pct = total > 0 ? (100 * t.w / total) : 0;
    const units = calcUnits(t.w, t.l);
    return `<tr>
      <td>${esc(name)}</td>
      <td class="center">${t.picks}</td>
      <td class="center">${t.w}-${t.l}-${t.p}</td>
      <td class="center"><span class="${pctClass(pct)}">${fmtPct(pct)}</span></td>
      <td class="center"><span class="${unitClass(units)}">${fmtUnits(units)}</span></td>
      <td class="center">${t.fav}</td>
      <td class="center">${t.dog}</td>
    </tr>`;
  }).join('');
  return `
    <div class="card card-records">
      <div class="card-title">Team Spread Record (ATS)</div>
      <table class="data">
        <thead><tr><th>Team</th><th class="center">Picks</th><th class="center">W-L-P</th><th class="center">Win%</th><th class="center">Flat</th><th class="center">Fav</th><th class="center">Dog</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="card-subtitle">Only graded spread picks. Units at -110 juice.</div>
    </div>`;
}

// ~line 1026-1064 — the two cards being merged in Task 3
function renderWeekly(runs) {
  const weeks = computeWeekly(runs);
  if (!weeks.length) return '';
  const rows = weeks.map(r => `<tr>
    <td>${esc(r.week)}</td>
    <td class="center"><span class="win-text">${r.w}W</span>–<span class="loss-text">${r.l}L</span>–${r.p}P</td>
    <td class="center"><span class="${pctClass(r.pct)}">${fmtPct(r.pct)}</span></td>
    <td class="center"><span class="${unitClass(r.units)}">${fmtUnits(r.units)}</span></td>
  </tr>`).join('');
  return `
    <div class="card card-trends">
      <div class="card-title">Weekly Spread</div>
      <table class="data">
        <thead><tr><th>Week of</th><th class="center">W-L-P</th><th class="center">Win%</th><th class="center">Flat</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function renderRolling(runs) {
  const { rows, window } = computeRolling(runs);
  if (!rows.length) return '';
  const body = rows.map(r => `<tr>
    <td>${esc(r.label)}</td>
    <td class="center"><span class="win-text">${r.w}W</span>–<span class="loss-text">${r.l}L</span>–${r.p}P</td>
    <td class="center"><span class="${pctClass(r.pct)}">${fmtPct(r.pct)}</span></td>
    <td class="center"><span class="${unitClass(r.units)}">${fmtUnits(r.units)}</span></td>
    <td class="center" style="font-size:0.7rem;color:var(--muted)">${esc(r.startDate)} → ${esc(r.endDate)}</td>
  </tr>`).join('');
  return `
    <div class="card card-trends">
      <div class="card-title">Rolling ${window}-Pick Groups (Spread)</div>
      <div class="card-subtitle">Green = above break-even (${fmtPct(52.4)}) · Red = below · Units at -110</div>
      <table class="data">
        <thead><tr><th>Window</th><th class="center">W-L-P</th><th class="center">Win%</th><th class="center">Flat</th><th class="center">Dates</th></tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>`;
}

// ~line 1729-1750 — the render() branch both new pieces plug into
if (viewMode === 'today' && latestRun) {
  const segRuns = filterRunsBySegment(runs);
  html += renderRecap(runs);
  html += renderTodayPicks(latestRun, runs);
  html += renderSpreadRecord(segRuns, modelSummary);
  html += '<div class="section-label">Cover Probabilities</div>';
  html += renderProbTable(latestRun);
  html += renderVetoTable(runs);
  html += renderRecentVetoes(runs);
  html += '<div class="section-label">Games</div>';
  html += renderGameCards(latestRun);
  html += '<div class="section-label">Spread Trends</div>';
  html += renderLast10(segRuns);
  html += renderWeekly(segRuns);
  html += renderRolling(segRuns);
  html += '<div class="section-label">Team Records</div>';
  html += renderTeamRecords(segRuns);
}
```

CSS reference (`PythonDashboard/styles.css`, unchanged, reused as-is):

```css
.result-badge.pending { background: rgba(107, 112, 148, 0.1); color: var(--muted); }
.view-toggle { display: flex; justify-content: center; gap: 6px; margin-bottom: 20px; }
.view-btn { padding: 8px 20px; border-radius: 999px; border: 1px solid var(--border); background: transparent; color: var(--muted); cursor: pointer; }
.view-btn:hover { border-color: var(--accent); color: var(--text); }
.view-btn.active { background: var(--card); border-color: var(--accent); color: var(--text); box-shadow: 0 0 12px var(--accent-glow); }
```

The `<select>` inline style pattern reused throughout the file (e.g. `seasonSelector`):
```
background:#1e1e1e;color:#e0e0e0;border:1px solid #444;border-radius:6px;padding:4px 10px;font-size:13px;margin-left:6px;cursor:pointer
```

---

### Task 1: `computeTeamPicks(runs)` data function

**Files:**
- Modify: `PythonDashboard/js/main.js` (insert after `computeTeamRecords`, currently ending ~line 673)
- Scratch verification: `<scratchpad>/verify_team_picks.js` (not committed)

- [ ] **Step 1: Write the scratch verification script**

Create `<scratchpad>/verify_team_picks.js` (use the scratchpad directory from your environment) with a self-contained copy of the small helpers it needs plus fixture data and assertions:

```js
const assert = require('assert');

function isActionable(conf) {
  const c = String(conf || '').trim().toLowerCase();
  return c === 'elite' || c === 'high';
}
function parseSpreadPick(pick) {
  const m = pick.match(/^(.+?)\s+([+-])(\d+(?:\.\d+)?)$/);
  if (!m) return null;
  return { team: m[1], sign: m[2], spread: parseFloat(m[3]) };
}
function gradeSpread(g) {
  if (!Number.isFinite(g.homeScore) || !Number.isFinite(g.awayScore)) return null;
  if (!g.sPick || g.sPick === 'PASS') return null;
  const parsed = parseSpreadPick(g.sPick);
  if (!parsed) return g.sResult || null;
  const margin = g.homeScore - g.awayScore;
  const chosenIsHome = parsed.team === g.home;
  const relMargin = chosenIsHome ? margin : -margin;
  const val = parsed.sign === '+' ? relMargin + parsed.spread : relMargin - parsed.spread;
  if (val === 0) return 'PUSH';
  return val > 0 ? 'WIN' : 'LOSS';
}

// --- function under test (will be pasted into main.js verbatim in Step 3) ---
function computeTeamPicks(runs) {
  const teams = {};
  for (const r of runs) {
    if (r.burnIn) continue;
    for (const g of r.games || []) {
      if (g.status === 'MISSING_ODDS' || g.status === 'SKIPPED') continue;
      if (!g.sPick || g.sPick === 'PASS' || !isActionable(g.sConf)) continue;
      const parsed = parseSpreadPick(g.sPick);
      if (!parsed) continue;
      const hasScore = Number.isFinite(g.homeScore) && Number.isFinite(g.awayScore);
      const result = hasScore ? (g.sResult || gradeSpread(g)) : null;
      const team = parsed.team;
      if (!teams[team]) teams[team] = [];
      teams[team].push({
        date: g.startTimeUTC || r.date || '',
        matchup: `${g.away} @ ${g.home}`,
        pick: g.sPick,
        conf: g.sConf,
        line: g.line,
        projMargin: g.margin,
        pCover: g.pCover != null ? g.pCover : null,
        result,
        pending: !hasScore,
        final: hasScore ? `${g.awayScore}-${g.homeScore}` : null,
      });
    }
  }
  return teams;
}
// --- end function under test ---

const fixtureRuns = [
  { date: '20260601', games: [
    { startTimeUTC: '2026-06-01T23:00:00Z', away: 'Chicago Sky', home: 'Indiana Fever',
      sPick: 'Indiana Fever -3.5', sConf: 'elite', line: -3.5, margin: 4.2, pCover: 0.61,
      homeScore: 80, awayScore: 74, status: 'OK' },
    { startTimeUTC: '2026-06-01T20:00:00Z', away: 'Atlanta Dream', home: 'Chicago Sky',
      sPick: 'PASS', sConf: 'low', status: 'OK' },
  ]},
  { date: '20260602', games: [
    { startTimeUTC: '2026-06-02T23:00:00Z', away: 'Chicago Sky', home: 'Indiana Fever',
      sPick: 'Chicago Sky +2.5', sConf: 'high', line: 2.5, margin: -1.0, pCover: 0.58,
      status: 'OK' }, // no homeScore/awayScore -> pending
  ]},
];

const result = computeTeamPicks(fixtureRuns);

assert.strictEqual(Object.keys(result).length, 2, 'expected 2 teams with picks (Indiana Fever, Chicago Sky)');
assert.strictEqual(result['Indiana Fever'].length, 1);
assert.strictEqual(result['Indiana Fever'][0].result, 'WIN');
assert.strictEqual(result['Indiana Fever'][0].pending, false);
assert.strictEqual(result['Indiana Fever'][0].final, '74-80');
assert.strictEqual(result['Chicago Sky'].length, 1);
assert.strictEqual(result['Chicago Sky'][0].pending, true);
assert.strictEqual(result['Chicago Sky'][0].result, null);
assert.strictEqual(result['Atlanta Dream'], undefined, 'PASS picks must not create a team entry');

console.log('OK: computeTeamPicks fixture assertions passed');
```

- [ ] **Step 2: Run it to confirm the harness itself is sound**

Run: `node <scratchpad>/verify_team_picks.js`
Expected: `OK: computeTeamPicks fixture assertions passed`

(This script is self-contained — the function is defined in the same file, so there's no separate red/green step here; the "test" is the assertion block. If it doesn't print OK, fix `computeTeamPicks` in the scratch file until it does, before moving on.)

- [ ] **Step 3: Paste the verified function into `main.js`**

Open `PythonDashboard/js/main.js`, find the end of `computeTeamRecords` (the closing `}` and `return teams;` around line 673, right before the `// ─── Yesterday's Recap ───` comment), and insert immediately after it:

```js

// Per-team list of individual picks (graded + pending) for the Team Picks
// browser. Unlike computeTeamRecords, does NOT require a final score.
function computeTeamPicks(runs) {
  const teams = {};
  for (const r of runs) {
    if (r.burnIn) continue;
    for (const g of r.games || []) {
      if (g.status === 'MISSING_ODDS' || g.status === 'SKIPPED') continue;
      if (!g.sPick || g.sPick === 'PASS' || !isActionable(g.sConf)) continue;
      const parsed = parseSpreadPick(g.sPick);
      if (!parsed) continue;
      const hasScore = Number.isFinite(g.homeScore) && Number.isFinite(g.awayScore);
      const result = hasScore ? (g.sResult || gradeSpread(g)) : null;
      const team = parsed.team;
      if (!teams[team]) teams[team] = [];
      teams[team].push({
        date: g.startTimeUTC || r.date || '',
        matchup: `${g.away} @ ${g.home}`,
        pick: g.sPick,
        conf: g.sConf,
        line: g.line,
        projMargin: g.margin,
        pCover: g.pCover != null ? g.pCover : null,
        result,
        pending: !hasScore,
        final: hasScore ? `${g.awayScore}-${g.homeScore}` : null,
      });
    }
  }
  return teams;
}
```

- [ ] **Step 4: Sanity-check the file still parses**

Run: `node --check PythonDashboard/js/main.js`
Expected: no output (exit code 0 means valid syntax — this checks parse-ability only, `main.js` still isn't executable standalone under Node because it uses browser globals like `fetch`/`document`).

- [ ] **Step 5: Commit**

```bash
git add PythonDashboard/js/main.js
git commit -m "Add computeTeamPicks: per-team pick list for dashboard team browser"
```

---

### Task 2: Team Picks browser UI + state wiring

**Files:**
- Modify: `PythonDashboard/js/main.js`
  - State vars near line 206
  - New setter near `setSeasonFilter` (~line 266)
  - New `renderTeamPicksSection` function near `renderTeamRecords` (~line 1099, right after it)
  - Wire into `render()` (~line 1749, right after the `renderTeamRecords(segRuns)` line)
- Scratch verification: `<scratchpad>/verify_team_picks_render.js` (not committed)

- [ ] **Step 1: Write the scratch verification script**

Create `<scratchpad>/verify_team_picks_render.js`:

```js
const assert = require('assert');

function esc(v) { return String(v ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function fmtNum(v, d) { return Number.isFinite(v) ? v.toFixed(d) : '—'; }
function fmtProb(v) { return Number.isFinite(v) ? (v * 100).toFixed(2) + '%' : '—'; }
function confBadge(conf) {
  const c = String(conf || '').toLowerCase();
  const cls = c === 'elite' ? 'b-elite' : c === 'high' ? 'b-high' : 'b-pass';
  return `<span class="badge ${cls}">${(c || 'N/A').toUpperCase()}</span>`;
}
function resultBadge(result) {
  if (!result) return '';
  const r = result.toLowerCase();
  return `<span class="result-badge ${r}">${result}</span>`;
}

// --- function under test (will be pasted into main.js verbatim in Step 3) ---
function renderTeamPicksSection(teamPicksMap, selectedTeam) {
  const teamNames = Object.keys(teamPicksMap).sort();
  if (!teamNames.length) return '';
  const activeTeam = teamPicksMap[selectedTeam] ? selectedTeam : teamNames[0];
  const picks = [...teamPicksMap[activeTeam]].sort((a, b) => String(b.date).localeCompare(String(a.date)));

  const opts = teamNames.map(name =>
    `<option value="${esc(name)}" ${name === activeTeam ? 'selected' : ''}>${esc(name)}</option>`
  ).join('');

  const rows = picks.length ? picks.map(p => {
    const resultCell = p.pending
      ? '<span class="result-badge pending">PENDING</span>'
      : (p.result ? resultBadge(p.result) : '—');
    return `<tr>
      <td>${esc(p.matchup)}</td>
      <td><span class="pick-team">${esc(p.pick)}</span> ${confBadge(p.conf)}</td>
      <td class="center">${fmtNum(p.line, 1)}</td>
      <td class="center">${fmtNum(p.projMargin, 1)}</td>
      <td class="center">${fmtProb(p.pCover)}</td>
      <td class="center">${resultCell}</td>
      <td class="center">${p.final ? esc(p.final) : '—'}</td>
    </tr>`;
  }).join('') : `<tr><td colspan="7" class="no-picks">No picks yet for this team.</td></tr>`;

  return `
    <div class="card card-records">
      <div class="card-title">Team Picks</div>
      <select onchange="setTeamPicksFilter(this.value)" style="background:#1e1e1e;color:#e0e0e0;border:1px solid #444;border-radius:6px;padding:4px 10px;font-size:13px;margin-bottom:10px;cursor:pointer">${opts}</select>
      <table class="data">
        <thead><tr><th>Matchup</th><th>Pick</th><th class="center">Line</th><th class="center">Proj Margin</th><th class="center">P(Cover)</th><th class="center">Result</th><th class="center">Final</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}
// --- end function under test ---

const fixture = {
  'Indiana Fever': [
    { date: '2026-06-01T23:00:00Z', matchup: 'Chicago Sky @ Indiana Fever', pick: 'Indiana Fever -3.5', conf: 'elite', line: -3.5, projMargin: 4.2, pCover: 0.61, result: 'WIN', pending: false, final: '74-80' },
  ],
  'Chicago Sky': [
    { date: '2026-06-02T23:00:00Z', matchup: 'Chicago Sky @ Indiana Fever', pick: 'Chicago Sky +2.5', conf: 'high', line: 2.5, projMargin: -1.0, pCover: 0.58, result: null, pending: true, final: null },
  ],
};

// No selection yet -> falls back to alphabetically-first team ("Chicago Sky")
let html = renderTeamPicksSection(fixture, null);
assert.ok(html.includes('value="Chicago Sky" selected'), 'should default-select first team alphabetically');
assert.ok(html.includes('PENDING'), 'pending pick should render a PENDING badge');
assert.ok(!html.includes('74-80'), 'Indiana Fever row should not appear when Chicago Sky is selected');

// Explicit selection
html = renderTeamPicksSection(fixture, 'Indiana Fever');
assert.ok(html.includes('value="Indiana Fever" selected'));
assert.ok(html.includes('74-80'));
assert.ok(!html.includes('PENDING'));

// Selection no longer present in map (e.g. season filter changed) -> falls back
html = renderTeamPicksSection(fixture, 'Some Team Not Present');
assert.ok(html.includes('value="Chicago Sky" selected'), 'unknown selection should fall back to first team');

// Empty map -> empty string, no card rendered
assert.strictEqual(renderTeamPicksSection({}, null), '');

console.log('OK: renderTeamPicksSection fixture assertions passed');
```

- [ ] **Step 2: Run it**

Run: `node <scratchpad>/verify_team_picks_render.js`
Expected: `OK: renderTeamPicksSection fixture assertions passed`
If any assertion throws, fix `renderTeamPicksSection` in the scratch file until it passes.

- [ ] **Step 3: Add state + setter to `main.js`**

Find the block around line 204-206:

```js
let seasonFilter = 'all';
let nflWeekFilter = 'latest';
let nflHistoryWeekFilter = 'all';
```

Change to:

```js
let seasonFilter = 'all';
let nflWeekFilter = 'latest';
let nflHistoryWeekFilter = 'all';
let teamPicksFilter = null; // selected team for the Team Picks browser; null = not yet chosen
```

Find `setSeasonFilter` (~line 260-266):

```js
function setSeasonFilter(val) {
  seasonFilter = val;
  nflWeekFilter = 'latest';
  nflHistoryWeekFilter = 'all';
  historyPage = 0;
  render();
}
```

Add immediately after its closing `}`:

```js
function setTeamPicksFilter(team) {
  teamPicksFilter = team;
  render();
}
```

- [ ] **Step 4: Add `renderTeamPicksSection` to `main.js`**

Find the end of `renderTeamRecords` (~line 1099, the closing ``</div>\`;`` and `}` right before the `// ─── Qualified Under Check (Full Season) ───` comment) and insert immediately after it the exact function verified in Step 2 (copy verbatim from the scratch script's "function under test" block — same body, no changes).

- [ ] **Step 5: Wire it into `render()`**

Find (~line 1748-1749):

```js
    html += '<div class="section-label">Team Records</div>';
    html += renderTeamRecords(segRuns);
```

Change to:

```js
    html += '<div class="section-label">Team Records</div>';
    html += renderTeamRecords(segRuns);
    html += renderTeamPicksSection(computeTeamPicks(segRuns), teamPicksFilter);
```

- [ ] **Step 6: Sanity-check the file still parses**

Run: `node --check PythonDashboard/js/main.js`
Expected: no output.

- [ ] **Step 7: Commit**

```bash
git add PythonDashboard/js/main.js
git commit -m "Add Team Picks browser: dropdown + per-team pick table to dashboard"
```

---

### Task 3: Combine Weekly + Rolling into one tabbed card

**Files:**
- Modify: `PythonDashboard/js/main.js`
  - State var near line 206
  - New setter near `setTeamPicksFilter` (added in Task 2)
  - Replace `renderWeekly` + `renderRolling` (~line 1026-1064) with `renderWeeklyRolling`
  - Wire into `render()` (~line 1743-1746)
- Scratch verification: `<scratchpad>/verify_weekly_rolling.js` (not committed)

- [ ] **Step 1: Write the scratch verification script**

Create `<scratchpad>/verify_weekly_rolling.js`:

```js
const assert = require('assert');

function esc(v) { return String(v ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function fmtPct(v) { return Number.isFinite(v) ? v.toFixed(2) + '%' : '—'; }
function fmtUnits(u) { return (u >= 0 ? '+' : '') + u.toFixed(2) + 'u'; }
function pctClass(p) { return p >= 55 ? 'win-text' : p <= 50 ? 'loss-text' : ''; }
function unitClass(u) { return u > 0 ? 'win-text' : u < 0 ? 'loss-text' : ''; }

// --- function under test (will be pasted into main.js verbatim in Step 3) ---
function renderWeeklyRolling(weeks, rollingRows, rollingWindow, activeTab) {
  if (!weeks.length && !rollingRows.length) return '';
  const tab = activeTab === 'rolling' ? 'rolling' : 'weekly';

  const tabsHtml = `
    <div style="display:flex;gap:6px;margin-bottom:12px">
      <button class="view-btn ${tab === 'weekly' ? 'active' : ''}" onclick="setSpreadTrendsTab('weekly')">Weekly</button>
      <button class="view-btn ${tab === 'rolling' ? 'active' : ''}" onclick="setSpreadTrendsTab('rolling')">Rolling</button>
    </div>`;

  let title, subtitle, thead, body;
  if (tab === 'weekly') {
    title = 'Weekly Spread';
    subtitle = '';
    thead = '<th>Week of</th><th class="center">W-L-P</th><th class="center">Win%</th><th class="center">Flat</th>';
    body = weeks.map(r => `<tr>
      <td>${esc(r.week)}</td>
      <td class="center"><span class="win-text">${r.w}W</span>–<span class="loss-text">${r.l}L</span>–${r.p}P</td>
      <td class="center"><span class="${pctClass(r.pct)}">${fmtPct(r.pct)}</span></td>
      <td class="center"><span class="${unitClass(r.units)}">${fmtUnits(r.units)}</span></td>
    </tr>`).join('');
  } else {
    title = `Rolling ${rollingWindow}-Pick Groups (Spread)`;
    subtitle = `<div class="card-subtitle">Green = above break-even (${fmtPct(52.4)}) · Red = below · Units at -110</div>`;
    thead = '<th>Window</th><th class="center">W-L-P</th><th class="center">Win%</th><th class="center">Flat</th><th class="center">Dates</th>';
    body = rollingRows.map(r => `<tr>
      <td>${esc(r.label)}</td>
      <td class="center"><span class="win-text">${r.w}W</span>–<span class="loss-text">${r.l}L</span>–${r.p}P</td>
      <td class="center"><span class="${pctClass(r.pct)}">${fmtPct(r.pct)}</span></td>
      <td class="center"><span class="${unitClass(r.units)}">${fmtUnits(r.units)}</span></td>
      <td class="center" style="font-size:0.7rem;color:var(--muted)">${esc(r.startDate)} → ${esc(r.endDate)}</td>
    </tr>`).join('');
  }

  return `
    <div class="card card-trends">
      <div class="card-title">${title}</div>
      ${tabsHtml}
      ${subtitle}
      <table class="data">
        <thead><tr>${thead}</tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>`;
}
// --- end function under test ---

const weeks = [{ week: '2026-06-01', w: 3, l: 1, p: 0, pct: 75, units: 1.9 }];
const rollingRows = [{ label: '1-10', w: 6, l: 4, p: 0, pct: 60, units: 1.6, startDate: '20260601', endDate: '20260610' }];

let html = renderWeeklyRolling(weeks, rollingRows, 10, 'weekly');
assert.ok(html.includes('Weekly Spread'));
assert.ok(html.includes('2026-06-01'));
assert.ok(!html.includes('Rolling 10-Pick'));
assert.ok(html.match(/class="view-btn active"[^>]*>Weekly/), 'Weekly tab should be marked active');

html = renderWeeklyRolling(weeks, rollingRows, 10, 'rolling');
assert.ok(html.includes('Rolling 10-Pick Groups (Spread)'));
assert.ok(html.includes('20260601'));
assert.ok(html.match(/class="view-btn active"[^>]*>Rolling/), 'Rolling tab should be marked active');
assert.ok(html.includes('Green = above break-even'));

assert.strictEqual(renderWeeklyRolling([], [], 10, 'weekly'), '', 'empty inputs should render nothing');

console.log('OK: renderWeeklyRolling fixture assertions passed');
```

- [ ] **Step 2: Run it**

Run: `node <scratchpad>/verify_weekly_rolling.js`
Expected: `OK: renderWeeklyRolling fixture assertions passed`

- [ ] **Step 3: Add state + setter to `main.js`**

In the state block edited in Task 2 Step 3, add one more line:

```js
let teamPicksFilter = null; // selected team for the Team Picks browser; null = not yet chosen
let spreadTrendsTab = 'weekly'; // active tab for the combined Weekly/Rolling card
```

Immediately after `setTeamPicksFilter` (added in Task 2 Step 3), add:

```js
function setSpreadTrendsTab(tab) {
  spreadTrendsTab = tab;
  render();
}
```

- [ ] **Step 4: Replace `renderWeekly` + `renderRolling` with `renderWeeklyRolling` in `main.js`**

Find (~line 1026-1064):

```js
function renderWeekly(runs) {
  const weeks = computeWeekly(runs);
  if (!weeks.length) return '';
  const rows = weeks.map(r => `<tr>
    <td>${esc(r.week)}</td>
    <td class="center"><span class="win-text">${r.w}W</span>–<span class="loss-text">${r.l}L</span>–${r.p}P</td>
    <td class="center"><span class="${pctClass(r.pct)}">${fmtPct(r.pct)}</span></td>
    <td class="center"><span class="${unitClass(r.units)}">${fmtUnits(r.units)}</span></td>
  </tr>`).join('');
  return `
    <div class="card card-trends">
      <div class="card-title">Weekly Spread</div>
      <table class="data">
        <thead><tr><th>Week of</th><th class="center">W-L-P</th><th class="center">Win%</th><th class="center">Flat</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function renderRolling(runs) {
  const { rows, window } = computeRolling(runs);
  if (!rows.length) return '';
  const body = rows.map(r => `<tr>
    <td>${esc(r.label)}</td>
    <td class="center"><span class="win-text">${r.w}W</span>–<span class="loss-text">${r.l}L</span>–${r.p}P</td>
    <td class="center"><span class="${pctClass(r.pct)}">${fmtPct(r.pct)}</span></td>
    <td class="center"><span class="${unitClass(r.units)}">${fmtUnits(r.units)}</span></td>
    <td class="center" style="font-size:0.7rem;color:var(--muted)">${esc(r.startDate)} → ${esc(r.endDate)}</td>
  </tr>`).join('');
  return `
    <div class="card card-trends">
      <div class="card-title">Rolling ${window}-Pick Groups (Spread)</div>
      <div class="card-subtitle">Green = above break-even (${fmtPct(52.4)}) · Red = below · Units at -110</div>
      <table class="data">
        <thead><tr><th>Window</th><th class="center">W-L-P</th><th class="center">Win%</th><th class="center">Flat</th><th class="center">Dates</th></tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>`;
}
```

Replace both with (same body as the verified scratch function, taking `runs` directly rather than pre-split fixtures, matching how the original two functions took `runs`):

```js
function renderWeeklyRolling(runs) {
  const weeks = computeWeekly(runs);
  const { rows: rollingRows, window: rollingWindow } = computeRolling(runs);
  if (!weeks.length && !rollingRows.length) return '';
  const tab = spreadTrendsTab === 'rolling' ? 'rolling' : 'weekly';

  const tabsHtml = `
    <div style="display:flex;gap:6px;margin-bottom:12px">
      <button class="view-btn ${tab === 'weekly' ? 'active' : ''}" onclick="setSpreadTrendsTab('weekly')">Weekly</button>
      <button class="view-btn ${tab === 'rolling' ? 'active' : ''}" onclick="setSpreadTrendsTab('rolling')">Rolling</button>
    </div>`;

  let title, subtitle, thead, body;
  if (tab === 'weekly') {
    title = 'Weekly Spread';
    subtitle = '';
    thead = '<th>Week of</th><th class="center">W-L-P</th><th class="center">Win%</th><th class="center">Flat</th>';
    body = weeks.map(r => `<tr>
      <td>${esc(r.week)}</td>
      <td class="center"><span class="win-text">${r.w}W</span>–<span class="loss-text">${r.l}L</span>–${r.p}P</td>
      <td class="center"><span class="${pctClass(r.pct)}">${fmtPct(r.pct)}</span></td>
      <td class="center"><span class="${unitClass(r.units)}">${fmtUnits(r.units)}</span></td>
    </tr>`).join('');
  } else {
    title = `Rolling ${rollingWindow}-Pick Groups (Spread)`;
    subtitle = `<div class="card-subtitle">Green = above break-even (${fmtPct(52.4)}) · Red = below · Units at -110</div>`;
    thead = '<th>Window</th><th class="center">W-L-P</th><th class="center">Win%</th><th class="center">Flat</th><th class="center">Dates</th>';
    body = rollingRows.map(r => `<tr>
      <td>${esc(r.label)}</td>
      <td class="center"><span class="win-text">${r.w}W</span>–<span class="loss-text">${r.l}L</span>–${r.p}P</td>
      <td class="center"><span class="${pctClass(r.pct)}">${fmtPct(r.pct)}</span></td>
      <td class="center"><span class="${unitClass(r.units)}">${fmtUnits(r.units)}</span></td>
      <td class="center" style="font-size:0.7rem;color:var(--muted)">${esc(r.startDate)} → ${esc(r.endDate)}</td>
    </tr>`).join('');
  }

  return `
    <div class="card card-trends">
      <div class="card-title">${title}</div>
      ${tabsHtml}
      ${subtitle}
      <table class="data">
        <thead><tr>${thead}</tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>`;
}
```

- [ ] **Step 5: Update the call site in `render()`**

Find (~line 1743-1746):

```js
    html += '<div class="section-label">Spread Trends</div>';
    html += renderLast10(segRuns);
    html += renderWeekly(segRuns);
    html += renderRolling(segRuns);
```

Change to:

```js
    html += '<div class="section-label">Spread Trends</div>';
    html += renderLast10(segRuns);
    html += renderWeeklyRolling(segRuns);
```

- [ ] **Step 6: Search for any other call sites of the removed functions**

Run: `grep -n "renderWeekly(\|renderRolling(" PythonDashboard/js/main.js`
Expected: no matches (both were only called from the one spot just edited). If there are other matches, update them to call `renderWeeklyRolling` the same way before continuing.

- [ ] **Step 7: Sanity-check the file still parses**

Run: `node --check PythonDashboard/js/main.js`
Expected: no output.

- [ ] **Step 8: Commit**

```bash
git add PythonDashboard/js/main.js
git commit -m "Merge Weekly + Rolling spread-trend cards into one tabbed card"
```

---

### Task 4: Cache-bust and manual browser verification

**Files:**
- Modify: `PythonDashboard/index.html:40`

- [ ] **Step 1: Bump the script version query param**

Find (line 40):
```html
  <script src="js/main.js?v=7"></script>
```
Change to:
```html
  <script src="js/main.js?v=8"></script>
```

- [ ] **Step 2: Start the dashboard in the browser preview**

Use the preview tooling to open `PythonDashboard/index.html` (static file — a simple static server or direct file open both work depending on how `fetchData` resolves relative paths; if it fetches via `fetch('data/...')` a `file://` origin will fail CORS, so serve it, e.g. `python -m http.server 8000` from `PythonDashboard/` and open `http://localhost:8000/index.html`).

- [ ] **Step 3: Verify the NBA "Full Season" tab**

- Click the "Full Season" tab.
- Scroll to "Team Records" — confirm the existing "Team Spread Record (ATS)" table still renders, and a new "Team Picks" card appears below it with a team dropdown.
- Change the dropdown selection — confirm the table below updates to that team's picks, newest first, with a mix of result badges and (if any upcoming games exist) a PENDING badge.
- Scroll to "Spread Trends" — confirm "Weekly Spread" and "Rolling N-Pick Groups" are now one card with Weekly/Rolling tab buttons, defaulting to Weekly. Click "Rolling" — confirm the table swaps and the break-even subtitle appears; click back to "Weekly" — confirm it swaps back and the subtitle disappears.

- [ ] **Step 4: Verify the "WNBA Full" tab**

Repeat the same checks as Step 3 on the "WNBA Full" tab — since all changes are in shared `main.js` code, this should work without any additional changes; if it doesn't, that's a bug to fix before proceeding (most likely cause: a WNBA-specific data shape gap — check the browser console for errors first).

- [ ] **Step 5: Check the browser console**

Confirm no new JS errors appear in the console on either tab (some pre-existing warnings from other widgets are fine — only new errors introduced by this change are blocking).

- [ ] **Step 6: Commit**

```bash
git add PythonDashboard/index.html
git commit -m "Bump dashboard script cache-buster for Team Picks + Weekly/Rolling merge"
```

---

## Self-review notes (for whoever executes this plan)

- Every step that changes code shows the exact code, not a description.
- `computeTeamPicks`, `renderTeamPicksSection`, and `renderWeeklyRolling` are each verified standalone via a Node fixture script *before* being pasted into `main.js`, so by the time they're wired into the real file their logic is already proven — the remaining risk is purely integration (correct call sites, correct existing helper names), which Task 4's manual browser pass catches.
- `computeTeamRecords`, `renderTeamRecords`, `renderLast10`, and all other existing cards are untouched — only the two new pieces are added and the two merged functions are replaced.
