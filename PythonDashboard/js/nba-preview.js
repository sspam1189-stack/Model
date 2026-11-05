// PythonDashboard/js/nba-preview.js
// "NBA Preview" tab (default page): the three full-season models side by side,
// built from the same daily-run files the individual model tabs read
// (fullseason / -kalmanoff / -kalmanearly). Nothing extra runs: every daily
// run refreshes this page.
//
// A play is P(cover) >= 58% (1u) or >= 65% (2u) with |line| < 18 -- the same
// rule as the preview. A win pays +stake, a loss costs 1.1 x stake. Each day
// is graded at that day's last daily-run line. Matching plays (two or three
// models on the same side) are staked at the smallest stake; "split" means
// they agree on the side but not the stake.

const NBAP_SEASON_START = '20261023';
const NBAP_FIRE = 0.58, NBAP_ELITE = 0.65, NBAP_MAX_LINE = 18;
// Games left out of the records by hand: shown dimmed, never counted.
const NBAP_SKIP = new Set([
  '20261102|Atlanta Hawks|Indiana Pacers',   // Siakam + Nembhard ruled out after a 5-pt line move
]);
const NBAP_MODELS = [
  { key: 'pyFull', name: 'Full Season', local: 'data/fullseason.json',
    remote: 'https://raw.githubusercontent.com/sspam1189-stack/Model/main/pyFull/data/history.json' },
  { key: 'pyFullKalmanOff', name: 'KalmanOff', local: 'data/fullseason-kalmanoff.json',
    remote: 'https://raw.githubusercontent.com/sspam1189-stack/Model/main/pyFullKalmanOff/data/history.json' },
  { key: 'pyFullKalmanEarly', name: 'KalmanEarly', local: 'data/fullseason-kalmanearly.json',
    remote: 'https://raw.githubusercontent.com/sspam1189-stack/Model/main/pyFullKalmanEarly/data/history.json' },
];

let nbapData = null;
let nbapRange = 'season';   // season | last7 | YYYYMM
let nbapDay = '';           // YYYYMMDD; overrides the range
let nbapLogPage = 0;
const NBAP_PAGE = 40;
const NBAP_DIM = '#8b949e';
const NBAP_SHORT = { pyFull: 'FS', pyFullKalmanOff: 'KO', pyFullKalmanEarly: 'KE' };

function nbapEsc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function nbapIso(d8) { return d8 ? `${d8.slice(0, 4)}-${d8.slice(4, 6)}-${d8.slice(6, 8)}` : ''; }
function nbapShortDate(d8) { return d8 ? `${+d8.slice(4, 6)}/${+d8.slice(6, 8)}` : ''; }
function nbapUnits(u) { return (u >= 0 ? '+' : '') + u.toFixed(1) + 'u'; }
function nbapUClass(u) { return u > 0.001 ? 'win-text' : (u < -0.001 ? 'loss-text' : ''); }
function nbapNick(team) { const p = String(team || '').split(' '); return p[p.length - 1]; }
function nbapTime(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleTimeString('en-US',
      { timeZone: 'America/Chicago', hour: 'numeric', minute: '2-digit' });
  } catch (e) { return ''; }
}
// The side a model leans, as "Suns -1.5"
function nbapPlay(g, side) {
  const team = side === 'home' ? g.home : g.away;
  if (g.line == null) return nbapNick(team);
  const v = side === 'home' ? g.line : -g.line;
  return `${nbapNick(team)} ${v === 0 ? 'PK' : (v > 0 ? '+' : '') + v}`;
}

// ── records ────────────────────────────────────────────────────────────────
function nbapNewRec() { return { w: 0, l: 0, p: 0, u: 0 }; }
function nbapAdd(r, res, units) {
  if (res === 'W') { r.w++; r.u += units; }
  else if (res === 'L') { r.l++; r.u -= 1.1 * units; }
  else if (res === 'P') { r.p++; }
}
function nbapRecCell(r) {
  const n = r.w + r.l;
  if (!n && !r.p) return `<span style="color:${NBAP_DIM}">—</span>`;
  const pct = n ? ` <span style="color:${NBAP_DIM};font-size:11px">${Math.round(100 * r.w / n)}%</span>` : '';
  return `<b>${r.w}-${r.l}${r.p ? '-' + r.p : ''}</b> <span class="${nbapUClass(r.u)}">${nbapUnits(r.u)}</span>${pct}`;
}

// A group of models "matches" on a game when every member plays the same side.
// kind: both2 (all 2u), both1 (all 1u), split (same side, mixed stakes).
function nbapMatch(g, keys) {
  const ms = keys.map(k => g.models[k]);
  if (ms.some(m => !m || !m.units)) return null;
  if (new Set(ms.map(m => m.side)).size !== 1) return null;
  const stakes = ms.map(m => m.units);
  const lo = Math.min(...stakes), hi = Math.max(...stakes);
  return { side: ms[0].side, units: lo, kind: lo === hi ? (lo === 2 ? 'both2' : 'both1') : 'split',
           result: ms[0].result };
}

function nbapGroups(models) {
  const k = models.map(m => m.key);
  const name = key => (models.find(m => m.key === key) || {}).name || key;
  const out = [];
  for (let i = 0; i < k.length; i++)
    for (let j = i + 1; j < k.length; j++)
      out.push({ keys: [k[i], k[j]], name: `${name(k[i])} + ${name(k[j])}` });
  if (k.length >= 3) out.push({ keys: k.slice(), name: `All ${k.length} agree` });
  return out;
}

// ── day selection ──────────────────────────────────────────────────────────
function nbapDaysInRange(d) {
  const graded = d.days.filter(x => !x.live || x.games.some(g => g.final));
  if (nbapDay) return d.days.filter(x => x.date === nbapDay);
  if (nbapRange === 'season') return graded;
  if (nbapRange === 'last7') {
    const t = new Date(nbapIso(d.today) + 'T12:00:00');
    t.setDate(t.getDate() - 7);
    const cut = t.toISOString().slice(0, 10).replace(/-/g, '');
    return graded.filter(x => x.date >= cut && x.date < d.today);
  }
  return graded.filter(x => x.date.slice(0, 6) === nbapRange);
}

function nbapSetRange(v) { nbapRange = v; nbapDay = ''; nbapLogPage = 0; nbapRerender(); }
function nbapSetDay(v) { nbapDay = String(v || '').replace(/-/g, ''); nbapLogPage = 0; nbapRerender(); }
function nbapPage(dlt) { nbapLogPage = Math.max(0, nbapLogPage + dlt); nbapRerender(); }

// ── panels ─────────────────────────────────────────────────────────────────
function nbapModelCell(g, m) {
  if (!m) return `<span style="color:${NBAP_DIM}">no projection</span>`;
  const proj = `<span style="color:${NBAP_DIM};font-size:11px">${m.aS.toFixed(1)}-${m.hS.toFixed(1)}</span>`;
  const pct = (m.p * 100).toFixed(1) + '%';
  const res = m.units && m.result
    ? ` <span class="${m.result === 'W' ? 'win-text' : m.result === 'L' ? 'loss-text' : ''}">${m.result}</span>` : '';
  if (!m.units) {
    return `<span style="color:${NBAP_DIM}">${nbapEsc(nbapNick(m.side === 'home' ? g.home : g.away))} ${pct} · no play</span><br>${proj}`;
  }
  const c = m.units === 2 ? '#3fb950' : '#58a6ff';
  return `<b>${nbapEsc(nbapPlay(g, m.side))}</b> ${pct}
    <span style="background:${c}22;color:${c};border:1px solid ${c}66;border-radius:3px;
      padding:0 5px;font-size:11px;font-weight:600">${m.units}u</span>${res}<br>${proj}`;
}

function nbapMatchTags(g, groups) {
  const tags = [];
  for (const grp of groups) {
    const mt = nbapMatch(g, grp.keys);
    if (!mt) continue;
    const label = mt.kind === 'both2' ? 'both 2u' : mt.kind === 'both1' ? 'both 1u' : 'split';
    const c = mt.kind === 'both2' ? '#3fb950' : mt.kind === 'both1' ? '#58a6ff' : '#d29922';
    const who = grp.keys.length > 2 ? 'All 3' : grp.keys.map(k => NBAP_SHORT[k] || k).join('+');
    tags.push(`<span style="color:${c};font-size:11px;white-space:nowrap">${who} ${label}</span>`);
  }
  return tags.join('<br>') || `<span style="color:${NBAP_DIM}">—</span>`;
}

function nbapTodayCard(d) {
  const day = d.days.find(x => x.date === d.today);
  const groups = nbapGroups(d.models);
  const games = day ? day.games : [];
  const rows = games.map(g => `<tr style="border-top:1px solid #21262d${g.skipped ? ';opacity:.5' : ''}">
      <td style="padding:6px">${nbapEsc(g.away)} @ <b>${nbapEsc(g.home)}</b>
        <div style="color:${NBAP_DIM};font-size:11px">${nbapTime(g.start)}${g.final ? ` · final ${g.final[0]}-${g.final[1]}` : ''}</div></td>
      <td class="center">${g.line == null ? '—' : (g.line > 0 ? '+' : '') + g.line}</td>
      ${d.models.map(m => `<td style="padding:6px">${nbapModelCell(g, g.models[m.key])}</td>`).join('')}
      <td style="padding:6px">${nbapMatchTags(g, groups)}</td></tr>`).join('');
  return `<div class="card card-games">
    <div class="card-title">Today's preview — ${nbapIso(d.today)}
      <span style="color:${NBAP_DIM};font-weight:400;font-size:11px">
        ${games.length} game${games.length === 1 ? '' : 's'} · ${d.generated ? 'last daily run ' + nbapEsc(d.generated) : ''}</span></div>
    <div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:12px">
      <thead><tr style="text-align:left;color:${NBAP_DIM};border-bottom:1px solid #30363d">
        <th style="padding:4px 6px">Game</th><th class="center">Line (home)</th>
        ${d.models.map(m => `<th style="padding:4px 6px">${nbapEsc(m.name)}</th>`).join('')}
        <th style="padding:4px 6px">Matching</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="${3 + d.models.length}" style="padding:10px;color:${NBAP_DIM}">No games today.</td></tr>`}</tbody>
    </table></div>
    <div style="padding:6px 10px;color:${NBAP_DIM};font-size:11px">
      Under 58% is no play, 58–65% is 1u, 65%+ is 2u. Scores are each model's projected away-home score, from the latest daily run.</div>
  </div>`;
}

function nbapFilterBar(d) {
  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const months = [...new Set(d.days.map(x => x.date.slice(0, 6)))].sort().reverse();
  const style = 'background:#1e1e1e;color:#e0e0e0;border:1px solid #444;border-radius:6px;padding:4px 10px;font-size:13px;cursor:pointer;margin-right:6px';
  const opts = [['season', 'Season to date'], ['last7', 'Last 7 days']]
    .concat(months.map(m => [m, MON[+m.slice(4) - 1] + ' ' + m.slice(0, 4)]));
  const days = d.days.map(x => x.date);
  return `<div style="padding:8px 0 10px">
    <select id="nbap-range" onchange="nbapSetRange(this.value)" style="${style}">
      ${opts.map(([v, l]) => `<option value="${v}" ${!nbapDay && nbapRange === v ? 'selected' : ''}>${l}</option>`).join('')}
    </select>
    <input type="date" id="nbap-day" aria-label="Filter by day" value="${nbapIso(nbapDay)}"
      min="${nbapIso(days[0])}" max="${nbapIso(days[days.length - 1])}"
      onchange="nbapSetDay(this.value)" style="${style};color-scheme:dark">
    ${nbapDay ? `<button onclick="nbapSetDay('')" style="${style}">Clear day</button>` : ''}
  </div>`;
}

function nbapRecordsCard(d) {
  const days = nbapDaysInRange(d);
  const games = days.flatMap(x => x.games).filter(g => !g.skipped);
  const label = nbapDay ? nbapIso(nbapDay)
    : nbapRange === 'season' ? 'season to date' : nbapRange === 'last7' ? 'last 7 days' : nbapRange;

  // each model: all / 2u / 1u
  const modelRows = d.models.map(m => {
    const all = nbapNewRec(), two = nbapNewRec(), one = nbapNewRec();
    games.forEach(g => {
      const x = g.models[m.key];
      if (!x || !x.units || !x.result) return;
      nbapAdd(all, x.result, x.units);
      nbapAdd(x.units === 2 ? two : one, x.result, x.units);
    });
    return `<tr style="border-top:1px solid #21262d"><td style="padding:6px"><b>${nbapEsc(m.name)}</b></td>
      <td class="center">${nbapRecCell(all)}</td><td class="center">${nbapRecCell(two)}</td>
      <td class="center">${nbapRecCell(one)}</td></tr>`;
  }).join('');

  // matching groups: both 2u / both 1u / split / any
  const groupRows = nbapGroups(d.models).map(grp => {
    const r = { both2: nbapNewRec(), both1: nbapNewRec(), split: nbapNewRec(), any: nbapNewRec() };
    games.forEach(g => {
      const mt = nbapMatch(g, grp.keys);
      if (!mt || !mt.result) return;
      nbapAdd(r[mt.kind], mt.result, mt.units);
      nbapAdd(r.any, mt.result, mt.units);
    });
    return `<tr style="border-top:1px solid #21262d"><td style="padding:6px"><b>${nbapEsc(grp.name)}</b></td>
      <td class="center">${nbapRecCell(r.both2)}</td><td class="center">${nbapRecCell(r.both1)}</td>
      <td class="center">${nbapRecCell(r.split)}</td><td class="center">${nbapRecCell(r.any)}</td></tr>`;
  }).join('');

  const th = s => `<th class="center" style="padding:4px 6px">${s}</th>`;
  const skipped = days.flatMap(x => x.games).filter(g => g.skipped).length;
  return `<div class="card card-games">
    <div class="card-title">Records — ${nbapEsc(label)}
      <span style="color:${NBAP_DIM};font-weight:400;font-size:11px">${days.length} day${days.length === 1 ? '' : 's'}</span></div>
    <div style="padding:0 10px">${nbapFilterBar(d)}</div>
    <div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:12px">
      <thead><tr style="text-align:left;color:${NBAP_DIM};border-bottom:1px solid #30363d">
        <th style="padding:4px 6px">Model</th>${th('All plays')}${th('2u only')}${th('1u only')}</tr></thead>
      <tbody>${modelRows}</tbody></table></div>
    <div style="overflow-x:auto;margin-top:14px"><table style="width:100%;border-collapse:collapse;font-size:12px">
      <thead><tr style="text-align:left;color:${NBAP_DIM};border-bottom:1px solid #30363d">
        <th style="padding:4px 6px">When they match (same side)</th>${th('Both 2u')}${th('Both 1u')}${th('Split 1u/2u')}${th('Any')}</tr></thead>
      <tbody>${groupRows}</tbody></table></div>
    <div style="padding:8px 10px;color:${NBAP_DIM};font-size:11px">
      Win = +stake, loss = −1.1 × stake. Matching plays use the smallest stake; "split" means same side, different stakes.
      Graded at each day's last daily-run line.${skipped ? ` ${skipped} game${skipped === 1 ? '' : 's'} skipped by hand (shown dimmed, not counted).` : ''}</div>
  </div>`;
}

function nbapLogCard(d) {
  const groups = nbapGroups(d.models);
  const days = nbapDaysInRange(d).slice().sort((a, b) => b.date.localeCompare(a.date));
  const rows = [];
  days.forEach(x => x.games.forEach(g => {
    if (d.models.some(m => g.models[m.key] && g.models[m.key].units)) rows.push({ date: x.date, g });
  }));
  const pages = Math.max(1, Math.ceil(rows.length / NBAP_PAGE));
  const page = Math.min(nbapLogPage, pages - 1);
  const body = rows.slice(page * NBAP_PAGE, (page + 1) * NBAP_PAGE).map(({ date, g }) => {
    const cells = d.models.map(m => {
      const x = g.models[m.key];
      if (!x || !x.units) return `<td class="center" style="color:${NBAP_DIM}">—</td>`;
      const rc = x.result === 'W' ? 'win-text' : x.result === 'L' ? 'loss-text' : '';
      return `<td class="center"><span style="white-space:nowrap">${nbapEsc(nbapPlay(g, x.side))}</span>
        <span style="color:${NBAP_DIM}">${x.units}u</span> <span class="${rc}">${x.result || ''}</span></td>`;
    }).join('');
    return `<tr style="border-top:1px solid #21262d${g.skipped ? ';opacity:.45;text-decoration:line-through' : ''}">
      <td style="padding:4px 6px;color:${NBAP_DIM}">${nbapShortDate(date)}</td>
      <td style="padding:4px 6px">${nbapEsc(nbapNick(g.away))} @ ${nbapEsc(nbapNick(g.home))}</td>
      <td class="center">${g.final ? `${g.final[0]}-${g.final[1]}` : '<span style="color:' + NBAP_DIM + '">—</span>'}</td>
      ${cells}<td style="padding:4px 6px">${nbapMatchTags(g, groups)}</td></tr>`;
  }).join('');
  return `<div class="card card-games">
    <div class="card-title">Plays log <span style="color:${NBAP_DIM};font-weight:400;font-size:11px">${rows.length} games with a play</span></div>
    <div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:12px">
      <thead><tr style="text-align:left;color:${NBAP_DIM};border-bottom:1px solid #30363d">
        <th style="padding:4px 6px">Date</th><th>Game</th><th class="center">Final</th>
        ${d.models.map(m => `<th class="center">${nbapEsc(m.name)}</th>`).join('')}<th>Matching</th></tr></thead>
      <tbody>${body || `<tr><td colspan="${4 + d.models.length}" style="padding:10px;color:${NBAP_DIM}">No plays in this range.</td></tr>`}</tbody>
    </table></div>
    <div class="date-nav" style="padding:8px 10px">
      <button onclick="nbapPage(-1)" ${page === 0 ? 'disabled' : ''}>Newer</button>
      <span class="current-view">Page ${page + 1} / ${pages}</span>
      <button onclick="nbapPage(1)" ${page >= pages - 1 ? 'disabled' : ''}>Older</button>
    </div>
  </div>`;
}

function nbapRerender() {
  if (!nbapData) return;
  const set = (id, html) => { const el = document.getElementById(id); if (el) el.innerHTML = html; };
  set('nbap-records', nbapRecordsCard(nbapData));
  set('nbap-log', nbapLogCard(nbapData));
}

// One model's daily-run game -> lean side, P(cover), stake, ATS result.
function nbapFromRun(g) {
  const ph = g.pHomeCover, pa = g.pAwayCover;
  if (!Number.isFinite(ph) || !Number.isFinite(pa) || !Number.isFinite(g.aS) || !Number.isFinite(g.hS)) return null;
  const side = ph >= pa ? 'home' : 'away';
  const p = Math.max(ph, pa);
  const line = Number.isFinite(g.line) ? g.line : null;
  const units = (line == null || Math.abs(line) >= NBAP_MAX_LINE) ? 0
    : (p >= NBAP_ELITE ? 2 : (p >= NBAP_FIRE ? 1 : 0));
  let result = null;
  if (line != null && Number.isFinite(g.homeScore) && Number.isFinite(g.awayScore)) {
    const cover = g.homeScore - g.awayScore + line;
    result = cover === 0 ? 'P' : ((cover > 0) === (side === 'home') ? 'W' : 'L');
  }
  return { aS: g.aS, hS: g.hS, line, side, p, units, result };
}

function nbapBuild(histories) {
  const byDate = {};
  let generated = '', genDate = '';
  NBAP_MODELS.forEach((m, i) => {
    const runs = (histories[i] && histories[i].runs) || [];
    runs.forEach(r => {
      if (r.burnIn || !r.date || r.date < NBAP_SEASON_START) return;
      if (r.ranAt && r.date >= genDate) { genDate = r.date; generated = String(r.ranAt); }
      const day = byDate[r.date] || (byDate[r.date] = {});
      (r.games || []).forEach(g => {
        if (['MISSING_ODDS', 'SKIPPED', 'POSTPONED'].includes(g.status)) return;
        const k = g.away + '|' + g.home;
        const row = day[k] || (day[k] = {
          away: g.away, home: g.home, line: null, start: g.startTimeUTC || null, final: null,
          skipped: NBAP_SKIP.has(r.date + '|' + k), models: {},
        });
        const x = nbapFromRun(g);
        if (x) {
          row.models[m.key] = x;
          if (x.line != null) row.line = x.line;
        }
        if (Number.isFinite(g.homeScore) && Number.isFinite(g.awayScore)) row.final = [g.awayScore, g.homeScore];
      });
    });
  });
  const dates = Object.keys(byDate).sort();
  const today = dates[dates.length - 1] || '';
  return {
    generated, today,
    models: NBAP_MODELS.map(m => ({ key: m.key, name: m.name })),
    days: dates.map(d => ({
      date: d, live: d === today,
      games: Object.values(byDate[d]).sort((a, b) => String(a.start || '').localeCompare(String(b.start || ''))),
    })),
  };
}

async function renderNBAPreview() {
  const el = document.getElementById('content');
  el.innerHTML = '<div class="loading"><div class="spinner"></div><br>Loading NBA preview...</div>';
  const grab = async (url) => {
    try {
      const r = await fetch(url + '?t=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) return null;
      const j = await r.json();
      return (j && Array.isArray(j.runs)) ? j : null;
    } catch (e) { return null; }
  };
  const lastDate = h => { const r = h && h.runs; return r && r.length ? String(r[r.length - 1].date || '') : ''; };
  // Newest of the dashboard copy and the model's own history wins.
  const histories = await Promise.all(NBAP_MODELS.map(async m => {
    const [a, b] = await Promise.all([grab(m.local), grab(m.remote)]);
    if (!a || !b) return a || b;
    return lastDate(b) > lastDate(a) ? b : a;
  }));
  if (!histories.some(Boolean)) {
    el.innerHTML = `<div class="card card-games"><div class="card-title">NBA Preview — No Data</div>
      <div class="no-picks" style="padding:20px 0 6px">The daily-run files could not be loaded.</div></div>`;
    return;
  }
  const data = nbapBuild(histories);
  nbapData = data;
  el.innerHTML = nbapTodayCard(data) + '<div id="nbap-records"></div><div id="nbap-log"></div>';
  nbapRerender();
}
