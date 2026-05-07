// Shared utilities, data fetching, NBA/NFL game rendering

// ─── Dense-table auto-fitter ──────────────────────────────────────────────
// Many of our cards render tables with 10–18 columns; on phone widths the
// natural minimum width exceeds the card width and content gets cut off
// (see e.g. NBA Player Props "Result" column). This module shrinks the
// table's font until it fits its container — no horizontal scroll, no
// hidden columns. Fits on insert (via MutationObserver) and re-fits on
// viewport resize / orientation change.
(function() {
  const tracked = new Set();
  const minFont = 6;
  const startFont = 13;

  // Fit one table in isolation: returns the font size at which it fits
  // its container. Temporarily switches the table to natural sizing for
  // measurement (a width:100% inline style hides true overflow because
  // the browser shrinks columns to fit and lets cell content overflow
  // visually instead).
  function measureFontFor(tbl) {
    if (!tbl || !tbl.parentElement || !document.body.contains(tbl)) return startFont;
    const parent = tbl.parentElement;
    const available = parent.clientWidth;
    if (available <= 0) return startFont;
    const cells = tbl.querySelectorAll('th, td');
    if (!cells.length) return startFont;

    const origWidth = tbl.style.width;
    const origMaxWidth = tbl.style.maxWidth;
    const origTableLayout = tbl.style.tableLayout;
    tbl.style.width = 'max-content';
    tbl.style.maxWidth = 'none';
    tbl.style.tableLayout = 'auto';

    const setSize = (px) => cells.forEach(c => c.style.setProperty('font-size', px + 'px', 'important'));
    let fontSize = startFont;
    setSize(fontSize);
    let guard = 0;
    while (tbl.scrollWidth > available + 1 && fontSize > minFont && guard < 60) {
      fontSize -= 0.5;
      setSize(fontSize);
      guard++;
    }

    tbl.style.width = origWidth;
    tbl.style.maxWidth = origMaxWidth;
    tbl.style.tableLayout = origTableLayout;
    return fontSize;
  }

  // Apply a font size to every cell in a table.
  function applyFont(tbl, px) {
    if (!tbl) return;
    tbl.querySelectorAll('th, td').forEach(c => c.style.setProperty('font-size', px + 'px', 'important'));
  }

  // Fit a table AND all of its sibling tables in the same card to a
  // single font size — the smallest needed across the group. Two tables
  // sharing the same headers should render with identical typography even
  // if their column data differs in width.
  function fitTable(tbl) {
    if (!tbl || !tbl.parentElement || !document.body.contains(tbl)) return;
    const card = tbl.closest('.card, .card-games, .card-picks, .card-recap') || tbl.parentElement;
    const group = Array.from(card.querySelectorAll('table'));
    let minNeeded = startFont;
    for (const t of group) {
      const fs = measureFontFor(t);
      if (fs < minNeeded) minNeeded = fs;
    }
    for (const t of group) applyFont(t, minNeeded);
  }

  function track(tbl) {
    if (!tbl || tracked.has(tbl)) return;
    tracked.add(tbl);
    requestAnimationFrame(() => fitTable(tbl));
  }

  function inDenseCard(node) {
    return node && node.closest && node.closest('.card, .card-games, .card-picks, .card-recap');
  }

  function sweep(root) {
    (root || document).querySelectorAll('.card table, .card-games table, .card-picks table, .card-recap table').forEach(track);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => sweep());
  } else {
    sweep();
  }

  const observer = new MutationObserver(records => {
    for (const r of records) {
      for (const node of r.addedNodes) {
        if (node.nodeType !== 1) continue;
        if (node.tagName === 'TABLE' && inDenseCard(node)) {
          track(node);
        } else if (node.querySelectorAll) {
          node.querySelectorAll('table').forEach(t => {
            if (inDenseCard(t)) track(t);
          });
        }
      }
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });

  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      for (const t of [...tracked]) {
        if (!document.body.contains(t)) { tracked.delete(t); continue; }
        fitTable(t);
      }
    }, 150);
  });

  window.fitTableToContainer = fitTable;
})();

const SOURCES = {
  nba: {
    name: 'pyNBA',
    local: 'data/nba.json',
    remote: 'https://raw.githubusercontent.com/sspam1189-stack/Model/main/pyNBA/data/history.json',
    repo: 'https://github.com/sspam1189-stack/Model'
  },
  fullseason: {
    name: 'pyFull',
    local: 'data/fullseason.json',
    remote: 'https://raw.githubusercontent.com/sspam1189-stack/Model/main/pyFull/data/history.json',
    repo: 'https://github.com/sspam1189-stack/Model'
  },
  ncaa: {
    name: 'pyNCAA',
    local: 'data/ncaa.json',
    remote: 'https://raw.githubusercontent.com/sspam1189-stack/Model/main/pyNCAA/data/history.json',
    repo: 'https://github.com/sspam1189-stack/Model'
  },
  nfl: {
    name: 'pyNFL',
    local: 'data/nfl.json',
    remote: 'https://raw.githubusercontent.com/sspam1189-stack/Model/main/pyNFL/data/nfl.json',
    repo: 'https://github.com/sspam1189-stack/Model'
  },
  'nba-props': {
    name: 'NBA Props',
    local: 'data/nba-props.json',
    remote: 'https://raw.githubusercontent.com/sspam1189-stack/Model/main/pyNBAPROPS/data/nba-props.json',
    repo: 'https://github.com/sspam1189-stack/Model'
  },
  'nfl-props': {
    name: 'NFL Props',
    local: 'data/nfl-props.json',
    remote: 'https://raw.githubusercontent.com/sspam1189-stack/Model/main/pyNFL/data/nfl-props.json',
    repo: 'https://github.com/sspam1189-stack/Model'
  },
  'mlb-props': {
    name: 'MLB Props',
    local: 'data/mlb-props.json',
    remote: 'https://raw.githubusercontent.com/sspam1189-stack/Model/main/MLBstrikeouts/data/mlb-props.json',
    repo: 'https://github.com/sspam1189-stack/Model'
  },
  'mlb-batter-props': {
    name: 'MLB Batter Props',
    local: 'data/mlb-props.json',
    remote: 'https://raw.githubusercontent.com/sspam1189-stack/Model/main/MLBstrikeouts/data/mlb-props.json',
    repo: 'https://github.com/sspam1189-stack/Model'
  }
};

const SUMMARY_SOURCES = {};

let cache = {};
let sourceMeta = {};
let summaryCache = {};
let summaryMeta = {};
let activeTab = 'fullseason';
let viewMode = 'today';
let historyPage = 0;
const DAYS_PER_PAGE = 7;

// ─── Generic Season Filter (shared across ALL tabs) ───
let seasonFilter = 'all';
let nflWeekFilter = 'latest';
let nflHistoryWeekFilter = 'all';

function setSeasonFilter(val) {
  seasonFilter = val;
  nflWeekFilter = 'latest';
  nflHistoryWeekFilter = 'all';
  historyPage = 0;
  render();
}

// Parse a week filter value that may be "3" or "2023_3" (season_week)
function parseNflWeekFilter(val) {
  if (val.includes('_')) {
    const [s, w] = val.split('_');
    return { season: parseInt(s), week: parseInt(w) };
  }
  return { season: null, week: parseInt(val) };
}

function setNflWeekFilter(val) {
  nflWeekFilter = val;
  render();
}

function setNflHistoryWeekFilter(val) {
  nflHistoryWeekFilter = val;
  historyPage = 0;
  render();
}

function nflWeekLabel(weekNum, run) {
  // For playoff weeks use round name; for regular season use "Week N"
  if (run && run.playoffRound) return run.playoffRound;
  if (weekNum > 18) {
    const names = { 19: 'Wild Card', 20: 'Divisional', 21: 'Conference Championship', 22: 'Super Bowl' };
    return names[weekNum] || `Week ${weekNum}`;
  }
  return `Week ${weekNum}`;
}

function nflWeekSelector(runs) {
  if (activeTab !== 'nfl') return '';
  const showSeason = seasonFilter === 'all';
  const weeks = runs
    .filter(r => !r.burnIn)
    .map(r => {
      const base = nflWeekLabel(r.week, r);
      const label = showSeason && r.season ? `${r.season} ${base}` : base;
      const val = showSeason && r.season ? `${r.season}_${r.week}` : String(r.week);
      return { week: r.week, season: r.season || 0, label, val, playoff: r.playoff };
    })
    .sort((a, b) => a.season !== b.season ? a.season - b.season : a.week - b.week);
  if (weeks.length <= 1) return '';
  let opts = `<option value="latest" ${nflWeekFilter === 'latest' ? 'selected' : ''}>Latest</option>`;
  opts += `<option value="all" ${nflWeekFilter === 'all' ? 'selected' : ''}>All Weeks</option>`;
  for (const w of weeks) {
    opts += `<option value="${w.val}" ${nflWeekFilter === w.val ? 'selected' : ''}>${w.label}</option>`;
  }
  return `<select onchange="setNflWeekFilter(this.value)" style="background:#1e1e1e;color:#e0e0e0;border:1px solid #444;border-radius:6px;padding:4px 10px;font-size:13px;margin-left:6px;cursor:pointer">${opts}</select>`;
}

function nflHistoryWeekSelector(runs) {
  if (activeTab !== 'nfl') return '';
  const showSeason = seasonFilter === 'all';
  const seen = new Map();
  for (const r of runs) {
    if (r.burnIn) continue;
    const base = nflWeekLabel(r.week, r);
    const label = showSeason && r.season ? `${r.season} ${base}` : base;
    const val = showSeason && r.season ? `${r.season}_${r.week}` : String(r.week);
    if (!seen.has(val)) {
      seen.set(val, { label, season: r.season || 0, week: r.week });
    }
  }
  const weeks = [...seen.entries()].sort((a, b) =>
    a[1].season !== b[1].season ? a[1].season - b[1].season : a[1].week - b[1].week
  );
  if (weeks.length <= 1) return '';
  let opts = `<option value="all" ${nflHistoryWeekFilter === 'all' ? 'selected' : ''}>All Weeks</option>`;
  for (const [val, info] of weeks) {
    opts += `<option value="${val}" ${nflHistoryWeekFilter === val ? 'selected' : ''}>${info.label}</option>`;
  }
  return `<select onchange="setNflHistoryWeekFilter(this.value)" style="background:#1e1e1e;color:#e0e0e0;border:1px solid #444;border-radius:6px;padding:4px 10px;font-size:13px;margin-left:6px;cursor:pointer">${opts}</select>`;
}

// Derive NBA/NCAA season label from a date string like "20260324"
// NBA season runs Oct-Jun: Oct-Dec = first year, Jan-Sep = second year
// e.g. "20251015" -> "2025-26", "20260324" -> "2025-26"
function nbaSeason(dateStr) {
  if (!dateStr || dateStr.length < 8) return null;
  const y = parseInt(dateStr.slice(0, 4));
  const m = parseInt(dateStr.slice(4, 6));
  // Oct (10) through Dec (12) = start of season year Y
  // Jan (1) through Sep (9) = end of season year Y-1
  if (m >= 10) {
    return `${y}-${String(y + 1).slice(2)}`;
  } else {
    return `${y - 1}-${String(y).slice(2)}`;
  }
}

// For NFL, the season field is already on the run object
function getRunSeason(run) {
  if (activeTab === 'nfl') {
    return run.season ? String(run.season) : null;
  }
  // NBA / NCAA / fullseason: derive from date
  return nbaSeason(run.date);
}

function filterBySeason(runs) {
  if (seasonFilter === 'all') return runs;
  return runs.filter(r => getRunSeason(r) === String(seasonFilter));
}

function seasonSelector(runs) {
  const seasons = [...new Set(runs.map(r => getRunSeason(r)).filter(Boolean))].sort();
  if (seasons.length <= 1) return '';
  let opts = `<option value="all" ${seasonFilter === 'all' ? 'selected' : ''}>All Seasons</option>`;
  for (const s of seasons) {
    opts += `<option value="${s}" ${String(seasonFilter) === String(s) ? 'selected' : ''}>${s}</option>`;
  }
  return `<select onchange="setSeasonFilter(this.value)" style="background:#1e1e1e;color:#e0e0e0;border:1px solid #444;border-radius:6px;padding:4px 10px;font-size:13px;margin-left:10px;cursor:pointer">${opts}</select>`;
}

// Tab switching
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => {
      t.classList.remove('active');
      t.removeAttribute('aria-current');
    });
    tab.classList.add('active');
    tab.setAttribute('aria-current', 'page');
    activeTab = tab.dataset.tab;
    historyPage = 0;
    viewMode = 'today';
    seasonFilter = 'all';
    nflWeekFilter = 'latest';
    nflHistoryWeekFilter = 'all';
    render();
  });
});

async function fetchData(key) {
  if (cache[key]) return cache[key];
  const cfg = SOURCES[key];
  if (!cfg) return null;
  const bust = `?t=${Date.now()}`;
  const isLocal = location.hostname === 'localhost' || location.hostname === '127.0.0.1';
  const attempts = isLocal ? [
    { mode: 'local', url: cfg.local + bust, label: 'Local snapshot' },
    { mode: 'remote', url: cfg.remote + bust, label: 'GitHub raw fallback' },
  ] : [
    { mode: 'local', url: cfg.local + bust, label: 'Local snapshot' },
    { mode: 'remote', url: cfg.remote + bust, label: 'GitHub raw fallback' },
  ];
  let lastErr = '';
  try {
    for (const attempt of attempts) {
      try {
        const resp = await fetch(attempt.url, { cache: 'no-store' });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        // Props feeds have data.props, game feeds have data.runs
        const isProps = key.includes('props');
        if (!data || (!isProps && !Array.isArray(data.runs)) || (isProps && !Array.isArray(data.props))) throw new Error('Invalid feed shape');
        cache[key] = data;
        sourceMeta[key] = {
          mode: attempt.mode,
          label: attempt.label,
          url: attempt.url,
          fetchedAt: new Date().toISOString(),
        };
        return data;
      } catch (err) {
        lastErr = `${attempt.label}: ${err.message || String(err)}`;
      }
    }
  } catch (e) {
    lastErr = e.message || String(e);
  }
  sourceMeta[key] = {
    mode: 'error',
    label: 'Unavailable',
    url: cfg.remote,
    error: lastErr || 'Unknown fetch error'
  };
  console.error(`Failed to load ${key}:`, lastErr);
  return null;
}

async function fetchModelSummary(key) {
  if (summaryCache[key]) return summaryCache[key];
  const cfg = SUMMARY_SOURCES[key];
  if (!cfg) return null;
  try {
    const resp = await fetch(cfg.remote + `?t=${Date.now()}`, { cache: 'no-store' });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    if (!data || !Array.isArray(data.results) || !data.results.length) {
      throw new Error('Invalid summary shape');
    }
    summaryCache[key] = data;
    summaryMeta[key] = { ok: true, fetchedAt: new Date().toISOString() };
    return data;
  } catch (e) {
    summaryMeta[key] = { ok: false, error: e.message || String(e) };
    return null;
  }
}

// ─── Helpers ───
function esc(v) { return String(v ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function fmtNum(v, d) { return Number.isFinite(v) ? v.toFixed(d) : '\u2014'; }
function winPct(w, l) { return (w + l) > 0 ? (100 * w / (w + l)) : 0; }
function fmtPct(v) { return Number.isFinite(v) ? v.toFixed(2) + '%' : '\u2014'; }
function fmtProb(v) { return Number.isFinite(v) ? (v * 100).toFixed(2) + '%' : '\u2014'; }
// Per-pick unit calculation using risk-to-win-1u convention (industry std).
//   + odds: risk 1u to win (odds/100)u   → WIN +odds/100, LOSS -1
//   - odds: risk (|odds|/100)u to win 1u → WIN +1, LOSS -|odds|/100
function pickUnit(p) {
  if (!p || !p.result || (p.result !== 'WIN' && p.result !== 'LOSS')) return 0;
  const o = (p.odds !== null && p.odds !== undefined) ? Number(p.odds) : -110;
  const win = p.result === 'WIN';
  if (o > 0) return win ? (o / 100) : -1;
  return win ? 1 : -(Math.abs(o) / 100);
}
function calcUnits(w, l, picks) {
  if (Array.isArray(picks) && picks.length) {
    const u = picks.reduce((a, p) => a + pickUnit(p), 0);
    return Math.round(u * 100) / 100;
  }
  // Fallback: -110 assumption (risk-to-win-1u): WIN=+1u, LOSS=-1.1u
  return Math.round((w - l * 1.1) * 100) / 100;
}
function fmtUnits(u) { return (u >= 0 ? '+' : '') + u.toFixed(2) + 'u'; }
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
function unitClass(u) { return u > 0 ? 'win-text' : u < 0 ? 'loss-text' : ''; }
function pctClass(p) { return p >= 55 ? 'win-text' : p <= 50 ? 'loss-text' : ''; }
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
  const isHome = parsed.team === g.home;
  const spreadVal = parsed.sign === '-' ? -parsed.spread : parsed.spread;
  const covered = isHome ? margin + spreadVal : -margin + spreadVal;
  if (covered > 0) return 'WIN';
  if (covered < 0) return 'LOSS';
  return 'PUSH';
}


// ─── Data Computations ───

function getGradedPicks(runs) {
  const picks = [];
  for (const r of runs) {
    if (r.burnIn) continue;
    for (const g of r.games || []) {
      if (g.status === 'MISSING_ODDS' || g.status === 'SKIPPED') continue;
      if (!Number.isFinite(g.homeScore) || !Number.isFinite(g.awayScore)) continue;
      picks.push({ date: r.date, dateDisplay: r.dateDisplay, ...g });
    }
  }
  return picks;
}

function tallyPicks(picks, { conf = null, side = null } = {}) {
  const confKey = conf ? String(conf).trim().toLowerCase() : null;
  let w = 0, l = 0, p = 0;
  const matched = [];
  for (const g of picks) {
    if (!g.sPick || g.sPick === 'PASS') continue;
    if (!isActionable(g.sConf)) continue;
    if (confKey && String(g.sConf || '').trim().toLowerCase() !== confKey) continue;
    if (side) {
      const parsed = parseSpreadPick(g.sPick);
      if (!parsed) continue;
      const pickSide = parsed.sign === '-' ? 'fav' : 'dog';
      if (pickSide !== side) continue;
    }
    const result = g.sResult || gradeSpread(g);
    if (!result) continue;
    if (result === 'WIN') w++;
    else if (result === 'LOSS') l++;
    else p++;
    matched.push({ result, odds: g.sOdds ?? g.odds });
  }
  return { w, l, p, pct: winPct(w, l), units: calcUnits(w, l, matched), played: w + l + p };
}

function computeSummary(runs) {
  const picks = getGradedPicks(runs);
  return {
    all: tallyPicks(picks),
    elite: tallyPicks(picks, { conf: 'elite' }),
    fav: tallyPicks(picks, { side: 'fav' }),
    dog: tallyPicks(picks, { side: 'dog' }),
  };
}

function getActionablePicks(runs) {
  const results = [];
  for (const r of runs) {
    if (r.burnIn) continue;
    for (const g of r.games || []) {
      if (g.status === 'MISSING_ODDS' || g.status === 'SKIPPED') continue;
      if (!g.sPick || g.sPick === 'PASS' || !isActionable(g.sConf)) continue;
      if (!Number.isFinite(g.homeScore) || !Number.isFinite(g.awayScore)) continue;
      const result = g.sResult || gradeSpread(g);
      if (!result) continue;
      results.push({
        date: r.date, dateDisplay: r.dateDisplay,
        matchup: `${g.away} @ ${g.home}`,
        pick: g.sPick, conf: g.sConf, result,
        final: `${g.awayScore}-${g.homeScore}`,
        sDiff: g.sDiff, pCover: g.pCover,
      });
    }
  }
  return results;
}

function computeLast10(runs) {
  return getActionablePicks(runs).slice(-10);
}

function computeWeekly(runs) {
  const picks = getActionablePicks(runs);
  const weeks = {};
  for (const p of picks) {
    const d = p.dateDisplay || p.date;
    const iso = d.replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3');
    const dt = new Date(iso + 'T12:00:00');
    const day = dt.getDay();
    const diff = day === 0 ? -6 : 1 - day;
    const mon = new Date(dt); mon.setDate(dt.getDate() + diff);
    const key = mon.getFullYear() + '-' + String(mon.getMonth() + 1).padStart(2, '0') + '-' + String(mon.getDate()).padStart(2, '0');
    if (!weeks[key]) weeks[key] = { week: key, w: 0, l: 0, p: 0 };
    if (p.result === 'WIN') weeks[key].w++;
    else if (p.result === 'LOSS') weeks[key].l++;
    else weeks[key].p++;
  }
  return Object.values(weeks).sort((a, b) => a.week.localeCompare(b.week)).map(r => ({
    ...r, pct: winPct(r.w, r.l), units: calcUnits(r.w, r.l)
  }));
}

function computeRolling(runs) {
  const picks = getActionablePicks(runs);
  const window = picks.length < 100 ? 10 : 20;
  const rows = [];
  for (let i = 0; i + window <= picks.length; i += window) {
    const chunk = picks.slice(i, i + window);
    const w = chunk.filter(x => x.result === 'WIN').length;
    const l = chunk.filter(x => x.result === 'LOSS').length;
    const p = chunk.filter(x => x.result === 'PUSH').length;
    rows.push({
      label: `#${i + 1}\u2013${i + window}`,
      w, l, p, pct: winPct(w, l), units: calcUnits(w, l),
      startDate: chunk[0].dateDisplay || chunk[0].date,
      endDate: chunk[chunk.length - 1].dateDisplay || chunk[chunk.length - 1].date,
    });
  }
  return { rows, window };
}

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

// ─── Yesterday's Recap ───
function getYesterdayRecap(runs) {
  const nonBurnIn = runs.filter(r => !r.burnIn);
  if (nonBurnIn.length < 2) return null;
  // Search backwards from second-to-last for the most recent run with graded picks
  for (let i = nonBurnIn.length - 2; i >= Math.max(0, nonBurnIn.length - 4); i--) {
    const day = nonBurnIn[i];
    const picks = [];
    for (const g of day.games || []) {
      if (g.status === 'MISSING_ODDS' || g.status === 'SKIPPED') continue;
      if (!g.sPick || g.sPick === 'PASS' || !isActionable(g.sConf)) continue;
      if (!Number.isFinite(g.homeScore) || !Number.isFinite(g.awayScore)) continue;
      const result = g.sResult || gradeSpread(g);
      if (!result) continue;
      picks.push({
        matchup: `${g.away} @ ${g.home}`,
        pick: g.sPick, conf: g.sConf, result,
        final: `${g.awayScore}-${g.homeScore}`,
        sDiff: g.sDiff,
      });
    }
    if (!picks.length) continue;
    const w = picks.filter(x => x.result === 'WIN').length;
    const l = picks.filter(x => x.result === 'LOSS').length;
    const p = picks.filter(x => x.result === 'PUSH').length;
    return {
      dateDisplay: day.dateDisplay || day.date,
      picks, tally: { w, l, p },
      units: calcUnits(w, l),
    };
  }
  return null;
}

// ─── Render Sections ───

function getModelBucket(modelSummary) {
  if (!modelSummary || !Array.isArray(modelSummary.results) || !modelSummary.results.length) return null;
  const best = modelSummary.results[0];
  return {
    name: String(best.name || 'Model'),
    w: Number(best.wins || 0),
    l: Number(best.losses || 0),
    p: Number(best.pushes || 0),
    units: Number(best.units || 0),
    played: Number(best.picks || 0),
    pct: Number(best.win_pct || 0),
    allSideAcc: Number(best.all_side_acc || 0),
    avgT: Number(best.avg_t || 0),
  };
}

function renderRecordBanner(runs, modelSummary = null) {
  const s = computeSummary(runs);
  let e = s.all;
  let label = 'Elite Record';
  const total = e.w + e.l;
  const pct = total > 0 ? (e.w / total * 100) : 0;
  const uClass = e.units > 0 ? 'positive' : e.units < 0 ? 'negative' : 'neutral';
  const pClass = pct > 52.4 ? 'positive' : pct < 50 ? 'negative' : 'neutral';
  let html = `
    <div class="record-banner">
      <div class="record-item">
        <div class="label">${label}</div>
        <div class="value">${e.w}-${e.l}${e.p > 0 ? `-${e.p}` : ''}</div>
      </div>
      <div class="record-item">
        <div class="label">Win %</div>
        <div class="value ${pClass}">${fmtPct(pct)}</div>
      </div>
      <div class="record-item">
        <div class="label">Units</div>
        <div class="value ${uClass}">${fmtUnits(e.units)}</div>
      </div>
      <div class="record-item">
        <div class="label">Graded</div>
        <div class="value">${e.played}</div>
      </div>
    </div>`;

  // Since 3/2 recent record (filter by pick date, not run date)
  const recentPicks = getActionablePicks(runs).filter(p => (p.date || '') >= '20260302');
  if (recentPicks.length > 0) {
    const rw = recentPicks.filter(p => p.result === 'WIN').length;
    const rl = recentPicks.filter(p => p.result === 'LOSS').length;
    const rp = recentPicks.filter(p => p.result === 'PUSH').length;
    const re = { w: rw, l: rl, p: rp, pct: winPct(rw, rl), units: calcUnits(rw, rl), played: rw + rl + rp };
    const rTotal = re.w + re.l;
    const rPct = rTotal > 0 ? (re.w / rTotal * 100) : 0;
    const rUClass = re.units > 0 ? 'positive' : re.units < 0 ? 'negative' : 'neutral';
    const rPClass = rPct > 52.4 ? 'positive' : rPct < 50 ? 'negative' : 'neutral';
    html += `
    <div class="record-banner" style="margin-top:8px;opacity:0.85">
      <div class="record-item">
        <div class="label">Since 3/2</div>
        <div class="value">${re.w}-${re.l}${re.p > 0 ? `-${re.p}` : ''}</div>
      </div>
      <div class="record-item">
        <div class="label">Win %</div>
        <div class="value ${rPClass}">${fmtPct(rPct)}</div>
      </div>
      <div class="record-item">
        <div class="label">Units</div>
        <div class="value ${rUClass}">${fmtUnits(re.units)}</div>
      </div>
      <div class="record-item">
        <div class="label">Graded</div>
        <div class="value">${re.played}</div>
      </div>
    </div>`;
  }

  return html;
}

function renderRecap(runs) {
  const recap = getYesterdayRecap(runs);
  if (!recap) return '';
  const rows = recap.picks.map(p => `
    <tr>
      <td>${esc(p.matchup)}</td>
      <td><span class="pick-team">${esc(p.pick)}</span> ${confBadge(p.conf)}</td>
      <td class="center">${resultBadge(p.result)}</td>
      <td class="center">${esc(p.final)}</td>
    </tr>`).join('');
  const uColor = recap.units >= 0 ? 'win-text' : 'loss-text';
  return `
    <div class="card card-recap">
      <div class="card-title">Yesterday's Recap (${esc(recap.dateDisplay)})</div>
      <table class="data">
        <thead><tr><th>Game</th><th>Pick</th><th class="center">Result</th><th class="center">Final</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="l10-tally">Spread: <b>${recap.tally.w}-${recap.tally.l}${recap.tally.p ? `-${recap.tally.p}` : ''}</b> &middot; <span class="${uColor}">${fmtUnits(recap.units)}</span></div>
    </div>`;
}

function renderTodayPicks(run, runs) {
  const games = (run.games || [])
    .filter(g => g.status !== 'MISSING_ODDS' && g.status !== 'SKIPPED')
    .filter(g => g.sPick && g.sPick !== 'PASS' && isActionable(g.sConf))
    .sort((a, b) => (b.pCover || 0) - (a.pCover || 0));

  let spreadItems = games.map(g => {
    const projMargin = Math.round((g.hS - g.aS) * 10) / 10;
    const favTeam = projMargin >= 0 ? g.home : g.away;
    const pStr = g.pCover != null ? ` \u00b7 P=${fmtProb(g.pCover)}` : '';
    return `<div class="pick-item">
      <span class="pick-team">${esc(g.sPick)}</span>
      ${confBadge(g.sConf)}
      <span class="pick-meta">proj ${esc(favTeam)} by ${fmtNum(Math.abs(projMargin), 1)} \u00b7 sDiff ${fmtNum(g.sDiff, 1)}${pStr}</span>
    </div>`;
  }).join('');

  // Include UNDER picks as official picks if they match any filter criteria (fullseason only)
  let underItems = '';
  if (activeTab === 'fullseason') {
    const todayUnders = (run.games || []).filter(g => {
      if (g.status === 'MISSING_ODDS' || g.status === 'SKIPPED') return false;
      if (!g.oPick || g.oPick !== 'UNDER') return false;
      if (!Number.isFinite(g.tDiff)) return false;
      return isQualifiedUnder(g);
    });

    underItems = todayUnders.map(g => {
      const tDiffAbs = Math.abs(g.tDiff);
      const pU = g.pUnder != null ? ` \u00b7 P(U)=${fmtProb(g.pUnder)}` : '';
      return `<div class="pick-item" style="border-left: 2px solid var(--blue);">
        <span class="pick-team">UNDER ${fmtNum(g.total, 1)}</span>
        ${g.oConf ? confBadge(g.oConf) : ''}
        <span class="pick-meta">${esc(g.away)} @ ${esc(g.home)} \u00b7 proj ${fmtNum(g.pT, 1)} \u00b7 tDiff ${fmtNum(tDiffAbs, 1)}${pU}</span>
      </div>`;
    }).join('');
  }

  const hasAny = games.length || underItems;
  if (!hasAny) return `<div class="card card-picks"><div class="card-title">Today's Picks</div><div class="no-picks">No actionable picks today.</div></div>`;

  let html = `<div class="card card-picks"><div class="card-title">Today's Picks (Actionable)</div>`;
  if (spreadItems) {
    html += `<div class="card-subtitle" style="margin-bottom:8px;font-weight:600;color:var(--text)">Spreads</div>`;
    html += spreadItems;
  }
  if (underItems) {
    html += `<div class="card-subtitle" style="margin:12px 0 8px;font-weight:600;color:var(--blue)">Totals</div>`;
    html += underItems;
  }
  html += `</div>`;
  return html;
}

function renderSpreadRecord(runs, modelSummary = null) {
  const s = computeSummary(runs);
  const modelBucket = s.all;
  const row = (label, b) => `<tr><td>${label}</td><td>${b.w}-${b.l}-${b.p}</td>
    <td class="center"><span class="${pctClass(b.pct)}">${fmtPct(Number(b.pct || 0))}</span></td>
    <td class="center"><span class="${unitClass(b.units)}">${fmtUnits(b.units)}</span></td>
    <td class="center">${b.played}</td></tr>`;
  return `
    <div class="card card-records">
      <div class="card-title">Spread Record (ATS)</div>
      <table class="data">
        <thead><tr><th>Bucket</th><th>W-L-P</th><th class="center">Win%</th><th class="center">Flat</th><th class="center">Graded</th></tr></thead>
        <tbody>
          ${row('Model', modelBucket)}
          ${row('Favorites', s.fav)}
          ${row('Underdogs', s.dog)}
        </tbody>
      </table>
      <div class="card-subtitle">Only graded picks (games with final scores + a non-PASS pick).</div>
    </div>`;
}

function renderProbTable(run) {
  const games = (run.games || []).filter(g => g.status !== 'MISSING_ODDS' && g.status !== 'SKIPPED')
    .sort((a, b) => (a.startTimeUTC || '').localeCompare(b.startTimeUTC || '') || (a.home || '').localeCompare(b.home || ''));
  if (!games.length) return '';
  const rows = games.map(g => {
    const sPick = g.sPick && g.sPick !== 'PASS'
      ? `<span class="pick-team">${esc(g.sPick)}</span> ${confBadge(g.sConf)}`
      : `<span style="color:var(--muted)">PASS</span>`;
    const pCover = g.pCover != null ? `<b>${fmtProb(g.pCover)}</b>` : '<span style="color:var(--muted)">\u2014</span>';
    // Show pCover_bayes as secondary when it differs from pCover
    const bayesSecondary = (g.pCover_bayes != null && g.pCover_bayes !== g.pCover)
      ? `<div class="card-subtitle" style="margin-top:2px;font-size:0.63rem;color:var(--yellow)">Bayes: ${fmtProb(g.pCover_bayes)}</div>` : '';
    const pHome = g.pHomeCover != null ? fmtProb(g.pHomeCover) : '\u2014';
    const pAway = g.pAwayCover != null ? fmtProb(g.pAwayCover) : '\u2014';
    const margin = Number.isFinite(g.margin) ? (g.margin >= 0 ? '+' : '') + fmtNum(g.margin, 1) : '\u2014';
    return `<tr>
      <td style="font-weight:700">${esc(g.away)} @ ${esc(g.home)}</td>
      <td>${sPick}<div class="card-subtitle" style="margin:2px 0 0">Line ${fmtNum(g.line,1)} \u00b7 proj ${margin} \u00b7 sDiff ${fmtNum(g.sDiff,1)}</div></td>
      <td class="center">${pCover}${bayesSecondary}<div class="card-subtitle">${pAway} away / ${pHome} home</div></td>
    </tr>`;
  }).join('');
  return `
    <div class="card card-probs">
      <div class="card-title">Cover Probabilities \u2014 All Games</div>
      <table class="data">
        <thead><tr><th>Game</th><th>Spread Pick</th><th class="center">P(Cover)</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="card-subtitle" style="margin-top:8px">P(Cover) = Bayesian probability the picked side covers the spread.</div>
    </div>`;
}

function renderVetoTable() { return ''; }
function renderRecentVetoes() { return ''; }

function renderGameCards(run) {
  const games = [...(run.games || [])].sort((a, b) =>
    (a.startTimeUTC || '').localeCompare(b.startTimeUTC || '') || (a.home || '').localeCompare(b.home || ''));
  if (!games.length) return '';
  const cards = games.map(g => {
    const isSkipped = g.status === 'MISSING_ODDS' || g.status === 'SKIPPED';

    let spreadHtml;
    if (!isSkipped && g.sPick && g.sPick !== 'PASS') {
      const projMargin = Math.round((g.hS - g.aS) * 10) / 10;
      const favTeam = projMargin >= 0 ? g.home : g.away;
      spreadHtml = `<div class="game-detail"><span class="pick-team">${esc(g.sPick)}</span> ${confBadge(g.sConf)} <span class="label">proj ${esc(favTeam)} by ${fmtNum(Math.abs(projMargin), 1)} \u00b7 sDiff ${fmtNum(g.sDiff, 1)}${g.pCover != null ? ` \u00b7 P=${fmtProb(g.pCover)}` : ''}</span></div>`;
    } else {
      spreadHtml = `<div class="game-detail"><span class="label">Spread: ${isSkipped ? esc(g.status) : 'PASS'}</span></div>`;
    }

    let projHtml = '';
    if (!isSkipped && Number.isFinite(g.aS) && Number.isFinite(g.hS)) {
      let projLine = `<span class="label">Proj</span> <b>${esc(g.away)} ${fmtNum(g.aS, 1)}</b> \u2013 <b>${esc(g.home)} ${fmtNum(g.hS, 1)}</b>`;
      projHtml = `<div class="game-detail">${projLine}</div>`;
    }

    const injuryHtml = g.injuryNote ? g.injuryNote.split(' | ').map(s => `<div class="injury">${esc(s)}</div>`).join('') : '';
    const b2bHtml = g.b2bNote ? `<div class="b2b">${esc(g.b2bNote)}</div>` : '';
    const scoreHtml = '';

    let trendsHtml = '';
    if (g.trends) {
      function fmtTrend(t) {
        if (!t) return '';
        if (typeof t === 'string') return t;
        if (typeof t === 'object') {
          const parts = [];
          if (t.atsPct != null) parts.push(`ATS ${t.atsPct.toFixed(0)}%`);
          if (t.atsPlusMinus != null) parts.push(`ATS ${t.atsPlusMinus >= 0 ? '+' : ''}${t.atsPlusMinus.toFixed(1)}`);
          if (t.overPct != null) parts.push(`O ${t.overPct.toFixed(0)}%`);
          if (t.underPct != null) parts.push(`U ${t.underPct.toFixed(0)}%`);
          return parts.join(' · ') || '';
        }
        return '';
      }
      const awayTrend = fmtTrend(g.trends.away);
      const homeTrend = fmtTrend(g.trends.home);
      if (awayTrend || homeTrend) {
        trendsHtml = `<div class="trends-info">`;
        if (awayTrend) trendsHtml += `<div><b>${esc(g.away)}:</b> ${esc(awayTrend)}</div>`;
        if (homeTrend) trendsHtml += `<div><b>${esc(g.home)}:</b> ${esc(homeTrend)}</div>`;
        trendsHtml += `</div>`;
      }
    }

    return `<div class="game-card">
      <div class="game-title">${esc(g.away)} @ ${esc(g.home)}</div>
      <div class="game-line">Line ${fmtNum(g.line, 1)} \u00b7 Total ${fmtNum(g.total, 1)}</div>
      ${spreadHtml}${projHtml}${injuryHtml}${b2bHtml}${scoreHtml}${trendsHtml}
    </div>`;
  });
  return `<div class="games-grid">${cards.join('')}</div>`;
}

function renderLast10(runs) {
  const picks = computeLast10(runs);
  if (!picks.length) return '';
  const rows = picks.map(p => `<tr>
    <td>${esc(p.dateDisplay || p.date)}</td>
    <td>${esc(p.matchup)}</td>
    <td><span class="pick-team">${esc(p.pick)}</span></td>
    <td class="center">${confBadge(p.conf)}</td>
    <td class="center">${resultBadge(p.result)}</td>
    <td class="center">${esc(p.final)}</td>
  </tr>`).join('');
  const t = { w: picks.filter(x => x.result === 'WIN').length, l: picks.filter(x => x.result === 'LOSS').length, p: picks.filter(x => x.result === 'PUSH').length };
  t.pct = winPct(t.w, t.l); t.units = calcUnits(t.w, t.l);
  return `
    <div class="card card-trends">
      <div class="card-title">Last 10 Spread</div>
      <table class="data">
        <thead><tr><th>Date</th><th>Matchup</th><th>Pick</th><th class="center">Conf</th><th class="center">Result</th><th class="center">Final</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="l10-tally">Last 10: <span class="win-text">${t.w}W</span>\u2013<span class="loss-text">${t.l}L</span>\u2013${t.p}P \u00b7 <span class="${pctClass(t.pct)}">${fmtPct(t.pct)}</span> \u00b7 <span class="${unitClass(t.units)}">${fmtUnits(t.units)}</span></div>
    </div>`;
}

function renderWeekly(runs) {
  const weeks = computeWeekly(runs);
  if (!weeks.length) return '';
  const rows = weeks.map(r => `<tr>
    <td>${esc(r.week)}</td>
    <td class="center"><span class="win-text">${r.w}W</span>\u2013<span class="loss-text">${r.l}L</span>\u2013${r.p}P</td>
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
    <td class="center"><span class="win-text">${r.w}W</span>\u2013<span class="loss-text">${r.l}L</span>\u2013${r.p}P</td>
    <td class="center"><span class="${pctClass(r.pct)}">${fmtPct(r.pct)}</span></td>
    <td class="center"><span class="${unitClass(r.units)}">${fmtUnits(r.units)}</span></td>
    <td class="center" style="font-size:0.7rem;color:var(--muted)">${esc(r.startDate)} \u2192 ${esc(r.endDate)}</td>
  </tr>`).join('');
  return `
    <div class="card card-trends">
      <div class="card-title">Rolling ${window}-Pick Groups (Spread)</div>
      <div class="card-subtitle">Green = above break-even (${fmtPct(52.4)}) \u00b7 Red = below \u00b7 Units at -110</div>
      <table class="data">
        <thead><tr><th>Window</th><th class="center">W-L-P</th><th class="center">Win%</th><th class="center">Flat</th><th class="center">Dates</th></tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>`;
}

function renderTeamRecords(runs) {
  const teams = computeTeamRecords(runs);
  const sorted = Object.entries(teams)
    .filter(([, t]) => t.w + t.l >= 3)
    .sort((a, b) => {
      const uA = calcUnits(a[1].w, a[1].l);
      const uB = calcUnits(b[1].w, b[1].l);
      return uB - uA;
    });
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

// ─── Qualified Under Check (Full Season) ───
// An UNDER play is an official pick if tDiff >= 10
// (covers all filter criteria: tDiff>=10, tDiff>=10+total<235/240, tDiff>=12, tDiff 11-13, tDiff 13-15)
function isQualifiedUnder(g) {
  return Math.abs(g.tDiff) >= 10;
}

// ─── History View ───

function renderHistoryDay(run) {
  const games = run.games || [];
  const picks = games.filter(g => g.sPick && g.sPick !== 'PASS' && isActionable(g.sConf));
  const dayW = picks.filter(g => (g.sResult || gradeSpread(g)) === 'WIN').length;
  const dayL = picks.filter(g => (g.sResult || gradeSpread(g)) === 'LOSS').length;
  const dayP = picks.filter(g => (g.sResult || gradeSpread(g)) === 'PUSH').length;
  const hasResults = dayW + dayL + dayP > 0;
  const recordStr = hasResults ? `${dayW}-${dayL}${dayP ? `-${dayP}` : ''}` : `${picks.length} pick${picks.length !== 1 ? 's' : ''}`;

  const sorted = [...games].sort((a, b) => {
    return (a.startTimeUTC || '').localeCompare(b.startTimeUTC || '') || (a.home || '').localeCompare(b.home || '');
  });

  const cards = sorted.map(g => {
    const isSkipped = g.status === 'MISSING_ODDS' || g.status === 'SKIPPED';
    const isPick = g.sPick && g.sPick !== 'PASS';
    const result = isPick ? (g.sResult || gradeSpread(g)) : null;
    const hasScore = Number.isFinite(g.homeScore) && Number.isFinite(g.awayScore);
    return `<div class="game-card">
      <div class="game-title">${esc(g.away)} @ ${esc(g.home)} <span style="font-weight:400;font-size:0.72rem;color:var(--muted)">Line ${fmtNum(g.line,1)}</span></div>
      <div class="game-detail">
        ${isPick ? `<span class="pick-team">${esc(g.sPick)}</span> ${confBadge(g.sConf)}` : `<span style="color:var(--muted)">${isSkipped ? esc(g.status) : 'PASS'}</span>`}
        ${result ? resultBadge(result) : (isPick && !hasScore ? '<span class="result-badge pending">PENDING</span>' : '')}
      </div>
      ${isPick ? lrInfo(g) : ''}
      ${hasScore ? `<div class="game-detail"><span class="label">Final:</span> ${g.awayScore}-${g.homeScore}</div>` : ''}
    </div>`;
  });

  return `
    <div style="margin-bottom:24px">
      <div class="date-header">
        <span>${run.dateDisplay || run.date}</span>
        <span class="date-record">${recordStr}</span>
      </div>
      <div class="games-grid">${cards.join('')}</div>
    </div>`;
}

// ─── Last Run Info ───

function updateLastRunInfo() {
  const labels = {
    nba: 'NBA', fullseason: 'Full Season', ncaa: 'NCAA', nfl: 'NFL',
    'nba-props': 'NBA Props', 'nfl-props': 'NFL Props',
    'mlb-props': 'MLB Strikeouts', 'mlb-batter-props': 'MLB Batter Props',
  };
  const data = cache[activeTab];
  const el = document.getElementById('last-run-info');
  if (!el) return;

  // MLB/props tabs use data.generated instead of data.runs
  if (data && data.generated) {
    const d = new Date(data.generated);
    const ts = d.toLocaleString('en-US', {dateStyle: 'short', timeStyle: 'medium'});
    el.textContent = `Last run (CT) \u2014 ${labels[activeTab] || activeTab}: ${ts}`;
    return;
  }

  if (!data || !data.runs) { el.textContent = ''; return; }
  const nonBurn = data.runs.filter(r => !r.burnIn);
  const last = nonBurn.length ? nonBurn[nonBurn.length - 1] : null;
  if (!last) { el.textContent = ''; return; }
  const ts = last.ranAt || last.dateDisplay || `${last.date.slice(0,4)}-${last.date.slice(4,6)}-${last.date.slice(6,8)}`;
  el.textContent = `Last run (CT) \u2014 ${labels[activeTab] || activeTab}: ${ts}`;
}

// ─── Last Sync Info ───

async function updateLastSyncInfo() {
  const el = document.getElementById('last-sync-info');
  if (!el) return;
  try {
    const res = await fetch('https://api.github.com/repos/sspam1189-stack/Model/actions/workflows/sync-dashboard-data.yml/runs?status=success&per_page=1');
    if (!res.ok) { el.textContent = ''; return; }
    const json = await res.json();
    const run = json.workflow_runs && json.workflow_runs[0];
    if (!run) { el.textContent = ''; return; }
    const d = new Date(run.updated_at);
    const ct = d.toLocaleString('en-US', { timeZone: 'America/Chicago', month: '2-digit', day: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true });
    el.textContent = `Last data sync: ${ct}`;
  } catch { el.textContent = ''; }
}

// ═══════════════════════════════════════════════════════════════
// ─── NFL-Specific Rendering ───
// ═══════════════════════════════════════════════════════════════

// NFL uses weekly cadence. Each run = one NFL week.
// Game fields: away, home, line (marketSpread), projSpread, projHome,
// injuries (array), injuryDelta, homeScore, awayScore, sResult, week

function nflGetWeekLabel(run) {
  if (run.dateDisplay) return run.dateDisplay;
  const season = run.season ? `${run.season} ` : '';
  if (run.playoffRound) return `${season}${run.playoffRound}`;
  if (run.week != null) return `${season}Week ${run.week}`;
  return run.date || '—';
}

function nflGetActionablePicks(runs) {
  const results = [];
  for (const r of runs) {
    if (r.burnIn) continue;
    for (const g of r.games || []) {
      if (g.status === 'MISSING_ODDS' || g.status === 'SKIPPED') continue;
      if (!g.sPick || g.sPick === 'PASS' || !isActionable(g.sConf)) continue;
      if (!Number.isFinite(g.homeScore) || !Number.isFinite(g.awayScore)) continue;
      const result = g.sResult || gradeSpread(g);
      if (!result) continue;
      results.push({
        week: r.week, date: r.date, dateDisplay: r.dateDisplay,
        matchup: `${g.away} @ ${g.home}`,
        pick: g.sPick, conf: g.sConf, result,
        final: `${g.awayScore}-${g.homeScore}`,
        sDiff: g.sDiff, pCover: g.pCover,
        projSpread: g.projSpread ?? (Number.isFinite(g.hS) && Number.isFinite(g.aS) ? Math.round((g.hS - g.aS) * 10) / 10 : null),
        marketSpread: g.line,
      });
    }
  }
  return results;
}

// ─── NFL Record Banner ───
function nflRenderRecordBanner(runs) {
  const picks = nflGetActionablePicks(runs);
  let w = 0, l = 0, p = 0;
  for (const pk of picks) {
    if (pk.result === 'WIN') w++;
    else if (pk.result === 'LOSS') l++;
    else p++;
  }
  const total = w + l;
  const pct = total > 0 ? (100 * w / total) : 0;
  const units = calcUnits(w, l);
  const nonBurn = runs.filter(r => !r.burnIn);
  const weeksPlayed = nonBurn.length;
  const uClass = units > 0 ? 'positive' : units < 0 ? 'negative' : 'neutral';
  const pClass = pct > 52.4 ? 'positive' : pct < 50 ? 'negative' : 'neutral';
  return `
    <div class="record-banner">
      <div class="record-item">
        <div class="label">ATS Record</div>
        <div class="value">${w}-${l}${p > 0 ? `-${p}` : ''}</div>
      </div>
      <div class="record-item">
        <div class="label">Win %</div>
        <div class="value ${pClass}">${fmtPct(pct)}</div>
      </div>
      <div class="record-item">
        <div class="label">Units (1u flat)</div>
        <div class="value ${uClass}">${fmtUnits(units)}</div>
      </div>
      <div class="record-item">
        <div class="label">Weeks</div>
        <div class="value">${weeksPlayed}</div>
      </div>
    </div>`;
}

// ─── NFL Weekly Picks Table ───
function nflRenderWeeklyPicks(run) {
  const games = (run.games || []).filter(g => g.status !== 'MISSING_ODDS' && g.status !== 'SKIPPED');
  if (!games.length) return '<div class="no-picks">No games this week.</div>';
  const rows = games.map(g => {
    const projSpr = g.projSpread ?? (Number.isFinite(g.hS) && Number.isFinite(g.aS) ? Math.round((g.aS - g.hS) * 10) / 10 : null);
    const mktSpr = g.line;
    const sDiff = g.sDiff;
    const pickHtml = g.sPick && g.sPick !== 'PASS'
      ? `<span class="pick-team">${esc(g.sPick)}</span> ${confBadge(g.sConf)}`
      : `<span style="color:var(--muted)">PASS</span>`;
    const result = g.sResult || gradeSpread(g);
    const resultHtml = result ? resultBadge(result) : (Number.isFinite(g.homeScore) ? '' : '<span class="result-badge pending">PENDING</span>');
    return `<tr>
      <td style="font-weight:700;white-space:nowrap">${esc(g.away)} @ ${esc(g.home)}</td>
      <td class="center">${fmtNum(projSpr, 1)}</td>
      <td class="center">${fmtNum(mktSpr, 1)}</td>
      <td class="center" style="font-weight:700;${Math.abs(sDiff) >= 3 ? 'color:var(--green)' : ''}">${fmtNum(sDiff, 1)}</td>
      <td>${pickHtml}</td>
      <td class="center">${resultHtml}</td>
    </tr>`;
  }).join('');
  return `
    <div class="card card-picks">
      <div class="card-title">Weekly Picks — ${nflGetWeekLabel(run)}</div>
      <table class="data">
        <thead><tr>
          <th>Matchup</th><th class="center">Proj Spr</th><th class="center">Mkt Spr</th>
          <th class="center">sDiff</th><th>Pick</th><th class="center">Result</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

// ─── NFL Season P&L + Cumulative Units Chart ───
function nflRenderPnL(runs) {
  const picks = nflGetActionablePicks(runs);
  if (!picks.length) return '';
  // Group by week
  const weekMap = {};
  for (const pk of picks) {
    const wk = pk.week ?? pk.date;
    if (!weekMap[wk]) weekMap[wk] = { week: wk, w: 0, l: 0, p: 0 };
    if (pk.result === 'WIN') weekMap[wk].w++;
    else if (pk.result === 'LOSS') weekMap[wk].l++;
    else weekMap[wk].p++;
  }
  const weeks = Object.values(weekMap).sort((a, b) => {
    const na = typeof a.week === 'number' ? a.week : parseInt(a.week) || 0;
    const nb = typeof b.week === 'number' ? b.week : parseInt(b.week) || 0;
    return na - nb;
  });

  let cumUnits = 0;
  const weekRows = weeks.map(wk => {
    const weekUnits = calcUnits(wk.w, wk.l);
    cumUnits += weekUnits;
    return `<tr>
      <td>Wk ${wk.week}</td>
      <td class="center">${wk.w}-${wk.l}${wk.p ? `-${wk.p}` : ''}</td>
      <td class="center"><span class="${unitClass(weekUnits)}">${fmtUnits(weekUnits)}</span></td>
      <td class="center" style="font-weight:700"><span class="${unitClass(cumUnits)}">${fmtUnits(cumUnits)}</span></td>
    </tr>`;
  }).join('');

  // SVG cumulative chart
  let cum = 0;
  const points = [{ x: 0, y: 0 }];
  weeks.forEach((wk, i) => {
    cum += calcUnits(wk.w, wk.l);
    points.push({ x: i + 1, y: cum });
  });
  const maxX = points.length - 1 || 1;
  const yVals = points.map(p => p.y);
  const minY = Math.min(0, ...yVals) - 1;
  const maxY = Math.max(0, ...yVals) + 1;
  const rangeY = maxY - minY || 1;
  const chartW = 600, chartH = 160, padL = 40, padR = 10, padT = 10, padB = 25;
  const plotW = chartW - padL - padR, plotH = chartH - padT - padB;
  const sx = x => padL + (x / maxX) * plotW;
  const sy = y => padT + plotH - ((y - minY) / rangeY) * plotH;
  const pathD = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join(' ');
  const zeroY = sy(0);
  const finalColor = cum >= 0 ? 'var(--green)' : 'var(--red)';
  const chartSvg = `
    <svg viewBox="0 0 ${chartW} ${chartH}" style="width:100%;max-width:${chartW}px;height:auto;display:block;margin:12px auto 0">
      <line x1="${padL}" y1="${zeroY}" x2="${chartW-padR}" y2="${zeroY}" stroke="var(--border-light)" stroke-dasharray="4,3"/>
      <text x="${padL-4}" y="${zeroY+4}" fill="var(--muted)" font-size="10" text-anchor="end">0</text>
      <text x="${padL-4}" y="${sy(maxY)+4}" fill="var(--muted)" font-size="10" text-anchor="end">${maxY.toFixed(1)}</text>
      <text x="${padL-4}" y="${sy(minY)+4}" fill="var(--muted)" font-size="10" text-anchor="end">${minY.toFixed(1)}</text>
      <polyline points="${points.map(p => `${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join(' ')}" fill="none" stroke="${finalColor}" stroke-width="2.5" stroke-linejoin="round"/>
      ${points.slice(1).map(p => `<circle cx="${sx(p.x).toFixed(1)}" cy="${sy(p.y).toFixed(1)}" r="3" fill="${finalColor}"/>`).join('')}
      <text x="${chartW/2}" y="${chartH-2}" fill="var(--muted)" font-size="10" text-anchor="middle">NFL Week</text>
    </svg>`;

  return `
    <div class="card card-records">
      <div class="card-title">Season P&L — 1u Flat</div>
      ${chartSvg}
      <table class="data" style="margin-top:12px">
        <thead><tr><th>Week</th><th class="center">W-L-P</th><th class="center">Week Units</th><th class="center">Cumulative</th></tr></thead>
        <tbody>${weekRows}</tbody>
      </table>
    </div>`;
}

// ─── NFL Injury Adjustments ───
function nflRenderInjuries(run) {
  const games = (run.games || []).filter(g => {
    if (g.status === 'MISSING_ODDS' || g.status === 'SKIPPED') return false;
    // Check multiple possible injury field names
    return (g.injuries && g.injuries.length) || g.injuryNote || Number.isFinite(g.injuryDelta);
  });
  if (!games.length) return '';
  const rows = games.map(g => {
    // Handle injuries as array of objects or as injuryNote string
    let injList = '';
    if (g.injuries && Array.isArray(g.injuries)) {
      injList = g.injuries.map(inj => {
        const name = inj.player || inj.name || '?';
        const pos = inj.position || inj.pos || '';
        const status = inj.status || inj.designation || '';
        const delta = Number.isFinite(inj.delta) ? ` (${inj.delta > 0 ? '+' : ''}${inj.delta.toFixed(2)})` : '';
        return `<span class="injury" style="margin:2px 4px 2px 0">${esc(name)}${pos ? ' ' + esc(pos) : ''}: ${esc(status)}${delta}</span>`;
      }).join(' ');
    } else if (g.injuryNote) {
      injList = g.injuryNote.split(' | ').map(s => `<span class="injury" style="margin:2px 4px 2px 0">${esc(s)}</span>`).join(' ');
    }
    const totalDelta = Number.isFinite(g.injuryDelta) ? (g.injuryDelta > 0 ? '+' : '') + g.injuryDelta.toFixed(2) : '—';
    const deltaColor = g.injuryDelta > 0 ? 'var(--green)' : g.injuryDelta < 0 ? 'var(--red)' : 'var(--muted)';
    return `<tr>
      <td style="font-weight:700;white-space:nowrap">${esc(g.away)} @ ${esc(g.home)}</td>
      <td>${injList || '<span style="color:var(--muted)">None</span>'}</td>
      <td class="center" style="font-weight:700;color:${deltaColor}">${totalDelta}</td>
    </tr>`;
  }).join('');
  return `
    <div class="card" style="border-left:3px solid var(--red);background:linear-gradient(135deg,var(--card) 0%,rgba(248,113,113,0.03) 100%)">
      <div class="card-title">Injury Adjustments — ${nflGetWeekLabel(run)}</div>
      <table class="data">
        <thead><tr><th>Matchup</th><th>Players Out / Limited</th><th class="center">Net Delta</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="card-subtitle" style="margin-top:8px">Delta = projected point adjustment from injuries. Positive = team benefits (opponent injuries).</div>
    </div>`;
}

// ─── NFL Model vs Market Scatter (SVG) ───
function nflRenderScatter(runs) {
  const picks = nflGetActionablePicks(runs);
  if (picks.length < 3) return '';
  const pts = picks.filter(p => Number.isFinite(p.projSpread) && Number.isFinite(p.marketSpread));
  if (pts.length < 3) return '';

  const chartW = 500, chartH = 400, pad = 50;
  const projVals = pts.map(p => p.projSpread);
  const mktVals = pts.map(p => p.marketSpread);
  const allVals = [...projVals, ...mktVals];
  const lo = Math.floor(Math.min(...allVals)) - 2;
  const hi = Math.ceil(Math.max(...allVals)) + 2;
  const range = hi - lo || 1;
  const plotW = chartW - 2 * pad, plotH = chartH - 2 * pad;
  const sx = v => pad + ((v - lo) / range) * plotW;
  const sy = v => pad + plotH - ((v - lo) / range) * plotH;

  const dots = pts.map(p => {
    const color = p.result === 'WIN' ? 'var(--green)' : p.result === 'LOSS' ? 'var(--red)' : 'var(--yellow)';
    return `<circle cx="${sx(p.projSpread).toFixed(1)}" cy="${sy(p.marketSpread).toFixed(1)}" r="5" fill="${color}" opacity="0.8"><title>${esc(p.matchup)} proj=${fmtNum(p.projSpread,1)} mkt=${fmtNum(p.marketSpread,1)} ${p.result}</title></circle>`;
  }).join('');

  // Diagonal line (perfect agreement)
  const diagLine = `<line x1="${sx(lo)}" y1="${sy(lo)}" x2="${sx(hi)}" y2="${sy(hi)}" stroke="var(--border-light)" stroke-dasharray="4,3"/>`;

  // Grid lines
  const step = range > 20 ? 5 : range > 10 ? 3 : 1;
  let gridLines = '';
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
    gridLines += `<line x1="${sx(v)}" y1="${pad}" x2="${sx(v)}" y2="${pad+plotH}" stroke="var(--border)" stroke-width="0.5"/>`;
    gridLines += `<line x1="${pad}" y1="${sy(v)}" x2="${pad+plotW}" y2="${sy(v)}" stroke="var(--border)" stroke-width="0.5"/>`;
    gridLines += `<text x="${sx(v)}" y="${pad+plotH+15}" fill="var(--muted)" font-size="10" text-anchor="middle">${v}</text>`;
    gridLines += `<text x="${pad-8}" y="${sy(v)+4}" fill="var(--muted)" font-size="10" text-anchor="end">${v}</text>`;
  }

  return `
    <div class="card card-probs">
      <div class="card-title">Model vs Market Spread</div>
      <svg viewBox="0 0 ${chartW} ${chartH}" style="width:100%;max-width:${chartW}px;height:auto;display:block;margin:0 auto">
        ${gridLines}${diagLine}${dots}
        <text x="${chartW/2}" y="${chartH-5}" fill="var(--muted)" font-size="11" text-anchor="middle">Model Projected Spread</text>
        <text x="12" y="${chartH/2}" fill="var(--muted)" font-size="11" text-anchor="middle" transform="rotate(-90,12,${chartH/2})">Market Spread</text>
      </svg>
      <div class="card-subtitle" style="margin-top:8px;text-align:center">
        <span style="color:var(--green)">&#9679;</span> WIN &nbsp;
        <span style="color:var(--red)">&#9679;</span> LOSS &nbsp;
        <span style="color:var(--yellow)">&#9679;</span> PUSH &nbsp;
        Dashed = perfect agreement
      </div>
    </div>`;
}

// ─── NFL Rolling Hit Rate by Week ───
function nflRenderRollingRate(runs) {
  const picks = nflGetActionablePicks(runs);
  if (picks.length < 5) return '';
  // Group by week
  const weekMap = {};
  for (const pk of picks) {
    const wk = pk.week ?? pk.date;
    if (!weekMap[wk]) weekMap[wk] = { week: wk, w: 0, l: 0, total: 0 };
    weekMap[wk].total++;
    if (pk.result === 'WIN') weekMap[wk].w++;
    else if (pk.result === 'LOSS') weekMap[wk].l++;
  }
  const weeks = Object.values(weekMap).sort((a, b) => {
    const na = typeof a.week === 'number' ? a.week : parseInt(a.week) || 0;
    const nb = typeof b.week === 'number' ? b.week : parseInt(b.week) || 0;
    return na - nb;
  });
  if (weeks.length < 2) return '';

  // Compute rolling 3-week hit rate
  const windowSize = Math.min(3, weeks.length);
  const rollingPts = [];
  for (let i = windowSize - 1; i < weeks.length; i++) {
    let rw = 0, rl = 0;
    for (let j = i - windowSize + 1; j <= i; j++) {
      rw += weeks[j].w;
      rl += weeks[j].l;
    }
    const rate = (rw + rl) > 0 ? (100 * rw / (rw + rl)) : 0;
    rollingPts.push({ week: weeks[i].week, rate, w: rw, l: rl });
  }

  const rows = weeks.map(wk => {
    const pct = wk.total > 0 ? (100 * wk.w / (wk.w + wk.l || 1)) : 0;
    return `<tr>
      <td>Wk ${wk.week}</td>
      <td class="center">${wk.w}-${wk.l}</td>
      <td class="center"><span class="${pctClass(pct)}">${fmtPct(pct)}</span></td>
    </tr>`;
  }).join('');

  // Rolling chart SVG
  const chartW = 600, chartH = 140, padL = 40, padR = 10, padT = 10, padB = 25;
  const plotW = chartW - padL - padR, plotH = chartH - padT - padB;
  const maxX = rollingPts.length - 1 || 1;
  const sxr = i => padL + (i / maxX) * plotW;
  const syr = v => padT + plotH - ((v - 30) / 50) * plotH; // 30% to 80% range
  const breakEvenY = syr(52.4);

  const pathD = rollingPts.map((p, i) => `${i === 0 ? 'M' : 'L'}${sxr(i).toFixed(1)},${syr(p.rate).toFixed(1)}`).join(' ');

  const chartSvg = `
    <svg viewBox="0 0 ${chartW} ${chartH}" style="width:100%;max-width:${chartW}px;height:auto;display:block;margin:8px auto">
      <line x1="${padL}" y1="${breakEvenY}" x2="${chartW-padR}" y2="${breakEvenY}" stroke="var(--yellow)" stroke-dasharray="4,3" opacity="0.5"/>
      <text x="${padL-4}" y="${breakEvenY+4}" fill="var(--yellow)" font-size="9" text-anchor="end">${fmtPct(52.4)}</text>
      <polyline points="${rollingPts.map((p,i) => `${sxr(i).toFixed(1)},${syr(p.rate).toFixed(1)}`).join(' ')}" fill="none" stroke="var(--accent)" stroke-width="2.5" stroke-linejoin="round"/>
      ${rollingPts.map((p, i) => `<circle cx="${sxr(i).toFixed(1)}" cy="${syr(p.rate).toFixed(1)}" r="3" fill="var(--accent)"><title>Wk ${p.week}: ${p.rate.toFixed(2)}% (${p.w}-${p.l})</title></circle>`).join('')}
      <text x="${chartW/2}" y="${chartH-2}" fill="var(--muted)" font-size="10" text-anchor="middle">Rolling ${windowSize}-Week Hit Rate</text>
    </svg>`;

  return `
    <div class="card card-trends">
      <div class="card-title">Rolling Hit Rate by Week</div>
      ${chartSvg}
      <table class="data">
        <thead><tr><th>Week</th><th class="center">W-L</th><th class="center">Hit Rate</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

// ─── NFL Calibration Table ───
function nflRenderCalibration(runs) {
  const picks = nflGetActionablePicks(runs);
  if (picks.length < 10) return '';
  // Bucket by pCover confidence
  const buckets = [
    { lo: 0.50, hi: 0.55, label: '50-55%' },
    { lo: 0.55, hi: 0.60, label: '55-60%' },
    { lo: 0.60, hi: 0.65, label: '60-65%' },
    { lo: 0.65, hi: 0.70, label: '65-70%' },
    { lo: 0.70, hi: 1.01, label: '70%+' },
  ];
  const results = buckets.map(b => {
    const inBucket = picks.filter(p => {
      const pc = p.pCover ?? 0;
      return pc >= b.lo && pc < b.hi;
    });
    const w = inBucket.filter(p => p.result === 'WIN').length;
    const l = inBucket.filter(p => p.result === 'LOSS').length;
    const total = w + l;
    const actualPct = total > 0 ? (100 * w / total) : null;
    const midPred = ((b.lo + Math.min(b.hi, 1.0)) / 2 * 100);
    return { ...b, w, l, total, actualPct, midPred };
  });

  // Filter buckets with data
  const filled = results.filter(r => r.total > 0);
  if (!filled.length) return '';

  const rows = filled.map(r => {
    const diff = r.actualPct !== null ? r.actualPct - r.midPred : null;
    const diffStr = diff !== null ? `${diff >= 0 ? '+' : ''}${diff.toFixed(2)}%` : '—';
    const diffColor = diff !== null ? (diff >= -3 ? 'var(--green)' : diff >= -8 ? 'var(--yellow)' : 'var(--red)') : '';
    return `<tr>
      <td class="center">${esc(r.label)}</td>
      <td class="center">${fmtPct(r.midPred)}</td>
      <td class="center" style="font-weight:700">${r.actualPct !== null ? fmtPct(r.actualPct) : '—'}</td>
      <td class="center" style="color:${diffColor}">${diffStr}</td>
      <td class="center">${r.total}</td>
    </tr>`;
  }).join('');

  return `
    <div class="card card-probs">
      <div class="card-title">Calibration Table</div>
      <table class="data">
        <thead><tr>
          <th class="center">P(Cover) Bucket</th><th class="center">Predicted</th>
          <th class="center">Actual</th><th class="center">Diff</th><th class="center">N</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="card-subtitle" style="margin-top:8px">Predicted = bucket midpoint. Actual = observed hit rate. Well-calibrated model has Diff near 0.</div>
    </div>`;
}

// ─── NFL: Today's Actionable Picks ───
function nflRenderTodayPicks(run) {
  const games = (run.games || [])
    .filter(g => g.status !== 'MISSING_ODDS' && g.status !== 'SKIPPED')
    .filter(g => g.sPick && g.sPick !== 'PASS' && isActionable(g.sConf))
    .sort((a, b) => (b.pCover || 0) - (a.pCover || 0));
  if (!games.length) return `<div class="card card-picks"><div class="card-title">Picks — ${nflGetWeekLabel(run)}</div><div class="no-picks">No actionable picks this week.</div></div>`;
  const items = games.map(g => {
    const projMargin = g.projSpread ?? (Number.isFinite(g.hS) && Number.isFinite(g.aS) ? Math.round((g.hS - g.aS) * 10) / 10 : null);
    const pStr = g.pCover != null ? ` \u00b7 P=${fmtProb(g.pCover)}` : '';
    return `<div class="pick-item">
      <span class="pick-team">${esc(g.sPick)}</span>
      ${confBadge(g.sConf)}
      <span class="pick-meta">sDiff ${fmtNum(g.sDiff, 1)}${pStr}</span>
    </div>`;
  }).join('');
  return `<div class="card card-picks"><div class="card-title">Picks — ${nflGetWeekLabel(run)} (Actionable)</div>${items}</div>`;
}

// ─── NFL History View ───
function nflRenderHistoryWeek(run) {
  const games = run.games || [];
  const picks = games.filter(g => g.sPick && g.sPick !== 'PASS' && isActionable(g.sConf));
  const dayW = picks.filter(g => (g.sResult || gradeSpread(g)) === 'WIN').length;
  const dayL = picks.filter(g => (g.sResult || gradeSpread(g)) === 'LOSS').length;
  const dayP = picks.filter(g => (g.sResult || gradeSpread(g)) === 'PUSH').length;
  const hasResults = dayW + dayL + dayP > 0;
  const recordStr = hasResults ? `${dayW}-${dayL}${dayP ? `-${dayP}` : ''}` : `${picks.length} pick${picks.length !== 1 ? 's' : ''}`;
  const cards = games.map(g => {
    const isSkipped = g.status === 'MISSING_ODDS' || g.status === 'SKIPPED';
    const isPick = g.sPick && g.sPick !== 'PASS';
    const result = isPick ? (g.sResult || gradeSpread(g)) : null;
    const hasScore = Number.isFinite(g.homeScore) && Number.isFinite(g.awayScore);
    return `<div class="game-card">
      <div class="game-title">${esc(g.away)} @ ${esc(g.home)} <span style="font-weight:400;font-size:0.72rem;color:var(--muted)">Line ${fmtNum(g.line,1)}</span></div>
      <div class="game-detail">
        ${isPick ? `<span class="pick-team">${esc(g.sPick)}</span> ${confBadge(g.sConf)}` : `<span style="color:var(--muted)">${isSkipped ? esc(g.status) : 'PASS'}</span>`}
        ${result ? resultBadge(result) : (isPick && !hasScore ? '<span class="result-badge pending">PENDING</span>' : '')}
      </div>
      ${hasScore ? `<div class="game-detail"><span class="label">Final:</span> ${g.awayScore}-${g.homeScore}</div>` : ''}
    </div>`;
  });
  return `
    <div style="margin-bottom:24px">
      <div class="date-header">
        <span>${nflGetWeekLabel(run)}</span>
        <span class="date-record">${recordStr}</span>
      </div>
      <div class="games-grid">${cards.join('')}</div>
    </div>`;
}

// ─── NFL Player Props Render ───


async function render() {
  const el = document.getElementById('content');
  el.innerHTML = '<div class="loading"><div class="spinner"></div><br>Loading picks...</div>';

  // Clear/update last-run info for every tab switch
  updateLastRunInfo();
  updateLastSyncInfo();

  // NFL has its own render pipeline
  if (activeTab === 'nfl') {
    return renderNFL();
  }
  // NBA Player Props
  if (activeTab === 'nba-props') {
    return renderNBAProps();
  }
  // NFL Player Props
  if (activeTab === 'nfl-props') {
    return renderNFLProps();
  }
  // MLB Pitcher Props
  if (activeTab === 'mlb-props') {
    return renderMLBProps();
  }
  // MLB Batter Props
  if (activeTab === 'mlb-batter-props') {
    return renderMLBBatterProps();
  }

  const data = await fetchData(activeTab);
  if (!data || !data.runs) {
    const cfg = SOURCES[activeTab];
    const meta = sourceMeta[activeTab];
    const err = meta?.error ? `<div class="card-subtitle" style="margin-top:8px;color:var(--red)">Error: ${esc(meta.error)}</div>` : '';
    el.innerHTML = `
      <div class="card card-games">
        <div class="card-title">No Data Available</div>
        <div class="no-picks" style="padding:20px 0 6px">Unable to load ${esc(cfg?.name || activeTab)} feed.</div>
        <div class="card-subtitle">Attempted local snapshot and GitHub raw fallback.</div>
        ${err}
      </div>`;
    return;
  }

  const allRuns = data.runs;
  const runs = filterBySeason(allRuns);
  const nonBurnIn = runs.filter(r => !r.burnIn);
  const latestRun = nonBurnIn.length ? nonBurnIn[nonBurnIn.length - 1] : null;
  const totalPages = Math.ceil(nonBurnIn.length / DAYS_PER_PAGE);
  const modelSummary = await fetchModelSummary(activeTab);

  // Update last run timestamps per model
  updateLastRunInfo();
  updateLastSyncInfo();

  let html = renderRecordBanner(runs, modelSummary);

  // View toggle
  html += `
    <div class="view-toggle">
      <button class="view-btn ${viewMode === 'today' ? 'active' : ''}" onclick="setView('today')">Latest</button>
      <button class="view-btn ${viewMode === 'history' ? 'active' : ''}" onclick="setView('history')">History</button>
      ${seasonSelector(allRuns)}
    </div>`;

  if (viewMode === 'today' && latestRun) {
    html += renderRecap(runs);
    html += renderTodayPicks(latestRun, runs);
    html += renderSpreadRecord(runs, modelSummary);
    html += '<div class="section-label">Cover Probabilities</div>';
    html += renderProbTable(latestRun);
    html += renderVetoTable(runs);
    html += renderRecentVetoes(runs);

    html += '<div class="section-label">Games</div>';
    html += renderGameCards(latestRun);

    html += '<div class="section-label">Spread Trends</div>';
    html += renderLast10(runs);
    html += renderWeekly(runs);
    html += renderRolling(runs);

    html += '<div class="section-label">Team Records</div>';
    html += renderTeamRecords(runs);

  } else if (viewMode === 'history') {
    html += `
      <div class="date-nav">
        <button onclick="prevPage()" ${historyPage === 0 ? 'disabled' : ''}>Newer</button>
        <span class="current-view">Page ${historyPage + 1} / ${totalPages}</span>
        <button onclick="nextPage()" ${historyPage >= totalPages - 1 ? 'disabled' : ''}>Older</button>
      </div>`;
    const reversed = [...nonBurnIn].reverse();
    const start = historyPage * DAYS_PER_PAGE;
    const pageRuns = reversed.slice(start, start + DAYS_PER_PAGE);
    if (!pageRuns.length) {
      html += '<div class="no-picks">No picks available yet.</div>';
    } else {
      html += pageRuns.map(renderHistoryDay).join('');
    }
  } else {
    html += '<div class="no-picks">No picks available yet.</div>';
  }

  el.innerHTML = html;
}

function setView(mode) { viewMode = mode; historyPage = 0; render(); }
function prevPage() { if (historyPage > 0) { historyPage--; render(); } }
function nextPage() { historyPage++; render(); }

