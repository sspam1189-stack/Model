// PythonDashboard/js/nba-preview.js
// "NBA Picks" tab (default page; data-tab id nba-preview): the three full-season models side by side,
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
const NBAP_MODELS = [
  { key: 'pyFull', name: 'Full Season', tab: 'fullseason', local: 'data/fullseason.json',
    remote: 'https://raw.githubusercontent.com/sspam1189-stack/Model/main/pyFull/data/history.json' },
  { key: 'pyFullKalmanOff', name: 'KalmanOff', tab: 'fullseason-ko', local: 'data/fullseason-kalmanoff.json',
    remote: 'https://raw.githubusercontent.com/sspam1189-stack/Model/main/pyFullKalmanOff/data/history.json' },
  { key: 'pyFullKalmanEarly', name: 'KalmanEarly', tab: 'fullseason-ke', local: 'data/fullseason-kalmanearly.json',
    remote: 'https://raw.githubusercontent.com/sspam1189-stack/Model/main/pyFullKalmanEarly/data/history.json' },
];

let nbapData = null;
let nbapRange = 'season';   // season | last7 | last14 | YYYYMM
let nbapDay = '';           // YYYYMMDD; overrides the range
let nbapLogPage = 0;
let nbapOffPage = 0;
let nbapRecKind = null;   // records table: 'preview' | 'official' (default: official once it exists)
const NBAP_PAGE = 40;
const NBAP_HIST_DAYS = 3;   // play history: days per page
const NBAP_DIM = '#8b949e';
// Official picks use the same colors as preview: 1u blue, 2u green.
function nbapOffColor(units) { return units === 2 ? '#3fb950' : '#58a6ff'; }
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
  models = models.filter(m => !m.solo);
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
function nbapDaysInRange(d, all) {
  all = all || d.days;
  const graded = all.filter(x => !x.live || x.games.some(g => g.final));
  if (nbapDay) return all.filter(x => x.date === nbapDay);
  if (nbapRange === 'season') return graded;
  if (nbapRange === 'last7' || nbapRange === 'last14') {
    const t = new Date(nbapIso(d.today) + 'T12:00:00');
    t.setDate(t.getDate() - (nbapRange === 'last14' ? 14 : 7));
    const cut = t.toISOString().slice(0, 10).replace(/-/g, '');
    return graded.filter(x => x.date >= cut && x.date < d.today);
  }
  return graded.filter(x => x.date.slice(0, 6) === nbapRange);
}

function nbapSetRange(v) { nbapRange = v; nbapDay = ''; nbapLogPage = 0; nbapOffPage = 0; nbapRerender(); }
function nbapSetDay(v) { nbapDay = String(v || '').replace(/-/g, ''); nbapLogPage = 0; nbapOffPage = 0; nbapRerender(); }
function nbapOffPg(dlt) { nbapOffPage = Math.max(0, nbapOffPage + dlt); nbapRerender(); }
function nbapPage(dlt) { nbapLogPage = Math.max(0, nbapLogPage + dlt); nbapRerender(); }

// ── panels ─────────────────────────────────────────────────────────────────
function nbapBadge(text, c) {
  return `<span style="background:${c}22;color:${c};border:1px solid ${c}66;border-radius:3px;
      padding:0 5px;font-size:11px;font-weight:600;white-space:nowrap">${text}</span>`;
}
function nbapModelCell(g, m, off) {
  if (!m) return `<span style="color:${NBAP_DIM}">no projection</span>`;
  const offTag = off ? `<br>${nbapBadge('OFFICIAL ' + nbapEsc(nbapPlay(g, off.side)) + ' ' + off.units + 'u', nbapOffColor(off.units))}` : '';
  const proj = `<span style="color:${NBAP_DIM};font-size:11px">${m.aS.toFixed(1)}-${m.hS.toFixed(1)}</span>`;
  const pct = (m.p * 100).toFixed(1) + '%';
  const res = m.units && m.result
    ? ` <span class="${m.result === 'W' ? 'win-text' : m.result === 'L' ? 'loss-text' : ''}">${m.result}</span>` : '';
  if (!m.units) {
    return `<span style="color:${NBAP_DIM}">${nbapEsc(nbapNick(m.side === 'home' ? g.home : g.away))} ${pct} · ${m.previewOff ? 'preview stopped' : 'no play'}</span><br>${proj}${offTag}`;
  }
  const c = m.units === 2 ? '#3fb950' : '#58a6ff';
  return `<b>${nbapEsc(nbapPlay(g, m.side))}</b> ${pct} ${nbapBadge('preview ' + m.units + 'u', c)}${res}<br>${proj}${offTag}`;
}

function nbapMatchTags(g, groups) {
  g = nbapCombineGame('', g, {});   // official pick where the model made one, else preview
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

// Phones get stacked cards instead of wide tables (no sideways scroll, no
// shrunken text). Re-rendered when the width crosses the breakpoint.
const NBAP_MOBILE_W = 700;
function nbapMobile() { return window.innerWidth < NBAP_MOBILE_W; }
function nbapMobStat(label, rec) {
  return `<div style="background:#161b22;border:1px solid #21262d;border-radius:6px;padding:6px 8px;min-width:0">
    <div style="color:${NBAP_DIM};font-size:11px">${label}</div>
    ${rec.w + rec.l + rec.p ? `<div style="font-size:14px;font-weight:700">${rec.w}-${rec.l}${rec.p ? '-' + rec.p : ''}</div>
    <div style="font-size:12px;white-space:nowrap"><span class="${nbapUClass(rec.u)}">${nbapUnits(rec.u)}</span>
      <span style="color:${NBAP_DIM}">${rec.w + rec.l ? Math.round(100 * rec.w / (rec.w + rec.l)) + '%' : ''}</span></div>`
      : `<div style="color:${NBAP_DIM}">—</div>`}</div>`;
}
function nbapMobGame(g, inner, sub) {
  return `<div style="border-top:1px solid #21262d;padding:10px 2px">
    <div style="display:flex;justify-content:space-between;gap:8px;align-items:baseline">
      <div style="font-size:14px">${nbapEsc(nbapNick(g.away))} @ <b>${nbapEsc(nbapNick(g.home))}</b></div>
      <div style="color:${NBAP_DIM};font-size:12px;white-space:nowrap">line ${g.line == null ? '—' : (g.line > 0 ? '+' : '') + g.line}</div>
    </div>
    <div style="color:${NBAP_DIM};font-size:12px;margin-bottom:6px">${sub}</div>${inner}</div>`;
}
let nbapWasMobile = null;
window.addEventListener('resize', () => {
  if (!nbapData || activeTab !== 'nba-preview') return;
  const m = nbapMobile();
  if (nbapWasMobile !== null && m !== nbapWasMobile) {
    const el = document.getElementById('content');
    if (el) { el.innerHTML = nbapTodayCard(nbapData) + '<div id="nbap-records"></div><div id="nbap-teams"></div><div id="nbap-log"></div>'; nbapRerender(); }
  }
  nbapWasMobile = m;
});

function nbapTodayCard(d) {
  nbapWasMobile = nbapMobile();
  const day = d.days.find(x => x.date === d.today);
  const groups = nbapGroups(d.models);
  const games = day ? day.games : [];
  const rows = games.map(g => `<tr style="border-top:1px solid #21262d${g.skipped ? ';opacity:.5' : ''}">
      <td style="padding:6px">${nbapEsc(g.away)} @ <b>${nbapEsc(g.home)}</b>
        <div style="color:${NBAP_DIM};font-size:11px">${nbapTime(g.start)}${g.final ? ` · final ${g.final[0]}-${g.final[1]}` : ''}</div></td>
      <td class="center">${g.line == null ? '—' : (g.line > 0 ? '+' : '') + g.line}</td>
      ${d.models.map(m => `<td style="padding:6px">${nbapModelCell(g, g.models[m.key], (g.official || {})[m.key])}</td>`).join('')}
      <td style="padding:6px">${nbapMatchTags(g, groups)}</td></tr>`).join('');
  const mobBody = games.map(g => nbapMobGame(g,
    d.models.map(m => `<div style="display:grid;grid-template-columns:92px 1fr;gap:6px;padding:3px 0;font-size:13px">
        <div style="color:${NBAP_DIM}">${nbapEsc(m.name)}</div>
        <div>${nbapModelCell(g, g.models[m.key], (g.official || {})[m.key])}</div></div>`).join('')
      + `<div style="margin-top:4px;display:flex;flex-wrap:wrap;gap:4px 10px">${nbapMatchTags(g, groups).replace(/<br>/g, ' ')}</div>`,
    `${nbapTime(g.start)}${g.final ? ` · final ${g.final[0]}-${g.final[1]}` : ''}`)).join('');
  const table = `<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:12px">
      <thead><tr style="text-align:left;color:${NBAP_DIM};border-bottom:1px solid #30363d">
        <th style="padding:4px 6px">Game</th><th class="center">Line (home)</th>
        ${d.models.map(m => `<th style="padding:4px 6px">${nbapEsc(m.name)}</th>`).join('')}
        <th style="padding:4px 6px">Matching</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="${3 + d.models.length}" style="padding:10px;color:${NBAP_DIM}">No games today.</td></tr>`}</tbody>
    </table></div>`;
  return `<div class="card card-games">
    <div class="card-title">Today's slate — ${nbapIso(d.today)}
      <span style="color:${NBAP_DIM};font-weight:400;font-size:11px">
        ${games.length} game${games.length === 1 ? '' : 's'} · ${d.generated ? 'last daily run ' + nbapEsc(d.generated) : ''}</span></div>
    ${nbapMobile() ? `<div style="padding:0 10px">${mobBody || `<div style="padding:10px 0;color:${NBAP_DIM}">No games today.</div>`}</div>` : table}
  </div>`;
}

function nbapFilterBar(d) {
  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const months = [...new Set(d.days.map(x => x.date.slice(0, 6)))].sort().reverse();
  const style = 'background:#1e1e1e;color:#e0e0e0;border:1px solid #444;border-radius:6px;padding:4px 10px;font-size:13px;cursor:pointer;margin-right:6px';
  const opts = [['season', 'Season to date'], ['last7', 'Last 7 days'], ['last14', 'Last 14 days']]
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

function nbapSetRecKind(v) { nbapRecKind = v; nbapRerender(); }
// Preview comes first until official picks exist; then Official leads.
function nbapRecToggle(kind, officialFirst) {
  const btn = (v, label, c, pos) => {
    const on = kind === v;
    const rad = pos === 'first' ? '6px 0 0 6px' : pos === 'last' ? '0 6px 6px 0' : '0';
    return `<button onclick="nbapSetRecKind('${v}')" style="background:${on ? c + '33' : '#1e1e1e'};
      color:${on ? c : '#c9d1d9'};border:1px solid ${on ? c : '#444'};padding:5px 14px;font-size:13px;
      font-weight:${on ? 700 : 400};cursor:pointer;border-radius:${rad};margin-left:-1px">${label}</button>`;
  };
  const prev = pos => btn('preview', 'Preview plays', '#58a6ff', pos);
  const off = pos => btn('official', 'Official plays', '#f0883e', pos);
  const comb = pos => btn('combined', 'Preview + official', '#a371f7', pos);
  return `<span role="group" aria-label="Which plays" style="display:inline-flex;margin-right:10px;margin-bottom:6px">${
    officialFirst ? off('first') + prev('mid') + comb('last') : prev('first') + off('mid') + comb('last')}</span>`;
}

// Mean absolute error of the projected home margin vs the final margin, over
// every final game in the range (plays or not). Vegas = the closing home line.
function nbapMAE(games, key) {
  let n = 0, sum = 0;
  games.forEach(g => {
    if (!g.final) return;
    const actual = g.final[1] - g.final[0];
    if (key === 'vegas') {
      if (g.line == null) return;
      sum += Math.abs(-g.line - actual); n++;
      return;
    }
    const m = g.models[key];
    if (!m || !Number.isFinite(m.aS) || !Number.isFinite(m.hS)) return;
    sum += Math.abs((m.hS - m.aS) - actual); n++;
  });
  return n ? { mae: sum / n, n } : null;
}
function nbapMAECell(x, best) {
  if (!x) return `<span style="color:${NBAP_DIM}">—</span>`;
  return `<b style="${best ? 'color:#3fb950' : ''}">${x.mae.toFixed(2)}</b> <span style="color:${NBAP_DIM};font-size:11px">${x.n} g</span>`;
}

function nbapRecordsCard(d, kind) {
  const official = kind === 'official', combined = kind === 'combined';
  const days = nbapDaysInRange(d, official ? d.offDays : combined ? d.combDays : d.days);
  const games = days.flatMap(x => x.games).filter(g => !g.skipped);
  const label = nbapDay ? nbapIso(nbapDay)
    : nbapRange === 'season' ? 'season to date' : nbapRange === 'last7' ? 'last 7 days' : nbapRange === 'last14' ? 'last 14 days' : nbapRange;

  // each model: all / 2u / 1u
  const mobModels = [], mobGroups = [];
  // MAE over every final game in range, split by era to match the toggle:
  // Preview = days before the model's official picks began, Official = from
  // that day on, Preview + official = all of it.
  const projDays = nbapDaysInRange(d, d.days);
  const offStarts = Object.values(d.offStart || {}).sort();
  const eraGames = start => projDays.filter(x =>
    combined || !start ? (official ? !!start : true) : (official ? x.date >= start : x.date < start))
    .flatMap(x => x.games);
  const maes = {};
  d.models.forEach(m => { maes[m.key] = nbapMAE(eraGames((d.offStart || {})[m.key]), m.key); });
  const vegasMAE = nbapMAE(eraGames(offStarts[0]), 'vegas');
  const bestMAE = Math.min(...Object.values(maes).filter(Boolean).map(x => x.mae));
  const modelRows = d.models.map(m => {
    const all = nbapNewRec(), two = nbapNewRec(), one = nbapNewRec();
    mobModels.push({ name: m.name, all, two, one, mae: maes[m.key] });
    games.forEach(g => {
      const x = g.models[m.key];
      if (!x || !x.units || !x.result) return;
      nbapAdd(all, x.result, x.units);
      nbapAdd(x.units === 2 ? two : one, x.result, x.units);
    });
    return `<tr style="border-top:1px solid #21262d"><td style="padding:6px"><b>${nbapEsc(m.name)}</b></td>
      <td class="center">${nbapMAECell(maes[m.key], maes[m.key] && maes[m.key].mae === bestMAE)}</td>
      <td class="center">${nbapRecCell(all)}</td><td class="center">${nbapRecCell(two)}</td>
      <td class="center">${nbapRecCell(one)}</td></tr>`;
  }).join('');

  // matching groups: both 2u / both 1u / split / any
  const groupRows = nbapGroups(d.models).map(grp => {
    const r = { both2: nbapNewRec(), both1: nbapNewRec(), split: nbapNewRec(), any: nbapNewRec() };
    mobGroups.push({ name: grp.name, r });
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
  const noneYet = official && !d.anyOfficial;
  const skipped = days.flatMap(x => x.games).filter(g => g.skipped).length;
  const head = `<div class="card card-games">
    <div class="card-title">Records — ${official ? '<span style="color:#f0883e">official plays</span>' : combined ? '<span style="color:#a371f7">preview + official</span>' : 'preview plays'}, ${nbapEsc(label)}
      <span style="color:${NBAP_DIM};font-weight:400;font-size:11px">${days.length} day${days.length === 1 ? '' : 's'}</span></div>
    <div style="padding:8px 10px 0;display:flex;flex-wrap:wrap;align-items:center">${nbapRecToggle(kind, d.anyOfficial)}${nbapFilterBar(d)}</div>`;
  if (noneYet) {
    return head + `<div style="padding:6px 10px 14px;color:${NBAP_DIM};font-size:12px">No official plays yet. The models are
      still in burn-in, so every daily-run game is a PASS and every play so far is a preview play. Official picks start
      once 20+ teams have played 10 games; this table then shows the models' own record, kept separate from the
      preview record, and preview plays stop.</div></div>`;
  }
  const grid = cols => `display:grid;grid-template-columns:repeat(${cols},minmax(0,1fr));gap:6px`;
  const mobile = `<div style="padding:0 10px">
      <div style="color:${NBAP_DIM};font-size:12px;margin:4px 0 6px">Each model</div>
      ${mobModels.map(x => `<div style="margin-bottom:10px"><div style="font-weight:700;margin-bottom:4px">${nbapEsc(x.name)}
          <span style="font-weight:400;font-size:12px;color:${NBAP_DIM};margin-left:6px">MAE ${nbapMAECell(x.mae, x.mae && x.mae.mae === bestMAE)}</span></div>
        <div style="${grid(3)}">${nbapMobStat('All plays', x.all)}${nbapMobStat('2u only', x.two)}${nbapMobStat('1u only', x.one)}</div></div>`).join('')}
      <div style="color:${NBAP_DIM};font-size:12px;margin:-2px 0 10px">Vegas line MAE ${nbapMAECell(vegasMAE, false)}</div>
      <div style="color:${NBAP_DIM};font-size:12px;margin:12px 0 6px">When they match (same side)</div>
      ${mobGroups.map(x => `<div style="margin-bottom:10px"><div style="font-weight:700;margin-bottom:4px">${nbapEsc(x.name)}</div>
        <div style="${grid(2)}">${nbapMobStat('Both 2u', x.r.both2)}${nbapMobStat('Both 1u', x.r.both1)}${nbapMobStat('Split 1u/2u', x.r.split)}${nbapMobStat('Any', x.r.any)}</div></div>`).join('')}
    </div>`;
  return head + (nbapMobile() ? mobile : `
    <div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:12px">
      <thead><tr style="text-align:left;color:${NBAP_DIM};border-bottom:1px solid #30363d">
        <th style="padding:4px 6px">Model</th>${th('MAE')}${th('All plays')}${th('2u only')}${th('1u only')}</tr></thead>
      <tbody>${modelRows}
        <tr style="border-top:1px solid #21262d"><td style="padding:6px;color:${NBAP_DIM}">Vegas line</td>
          <td class="center">${nbapMAECell(vegasMAE, false)}</td><td colspan="3"></td></tr></tbody></table></div>
    <div style="overflow-x:auto;margin-top:14px"><table style="width:100%;border-collapse:collapse;font-size:12px">
      <thead><tr style="text-align:left;color:${NBAP_DIM};border-bottom:1px solid #30363d">
        <th style="padding:4px 6px">When they match (same side)</th>${th('Both 2u')}${th('Both 1u')}${th('Split 1u/2u')}${th('Any')}</tr></thead>
      <tbody>${groupRows}</tbody></table></div>`) + `
  </div>`;
}

function nbapLogCard(d, kind) {
  const official = kind === 'official';
  const groups = nbapGroups(d.models);
  const days = nbapDaysInRange(d, official ? d.offDays : d.days).slice().sort((a, b) => b.date.localeCompare(a.date));
  const curPage = official ? nbapOffPage : nbapLogPage, pgFn = official ? 'nbapOffPg' : 'nbapPage';
  const rows = [];
  days.forEach(x => x.games.forEach(g => {
    if (d.models.some(m => g.models[m.key] && g.models[m.key].units)) rows.push({ date: x.date, g });
  }));
  const pages = Math.max(1, Math.ceil(rows.length / NBAP_PAGE));
  const page = Math.min(curPage, pages - 1);
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
    <div class="card-title">${official ? '<span style="color:#f0883e">Official plays</span>' : 'Preview plays'} — log <span style="color:${NBAP_DIM};font-weight:400;font-size:11px">${rows.length} games with a play</span></div>
    <div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:12px">
      <thead><tr style="text-align:left;color:${NBAP_DIM};border-bottom:1px solid #30363d">
        <th style="padding:4px 6px">Date</th><th>Game</th><th class="center">Final</th>
        ${d.models.map(m => `<th class="center">${nbapEsc(m.name)}</th>`).join('')}<th>Matching</th></tr></thead>
      <tbody>${body || `<tr><td colspan="${4 + d.models.length}" style="padding:10px;color:${NBAP_DIM}">No plays in this range.</td></tr>`}</tbody>
    </table></div>
    <div class="date-nav" style="padding:8px 10px">
      <button onclick="${pgFn}(-1)" ${page === 0 ? 'disabled' : ''}>Newer</button>
      <span class="current-view">Page ${page + 1} / ${pages}</span>
      <button onclick="${pgFn}(1)" ${page >= pages - 1 ? 'disabled' : ''}>Older</button>
    </div>
  </div>`;
}

// Play-history filters (own row, like the other logs).
let nbapHF = { kind: 'all', month: 'all', day: '', team: 'all', model: 'all',
               stake: 'all', result: 'all', side: 'all', match: 'all' };
function nbapSetHF(k, v, focusId) {
  if (k === 'day') { v = String(v || '').replace(/-/g, ''); if (v) nbapHF.month = 'all'; }
  if (k === 'month') nbapHF.day = '';
  nbapHF[k] = v; nbapLogPage = 0; nbapRerender();
  if (focusId) { const f = document.getElementById(focusId); if (f) f.focus(); }
}
function nbapResetHF() {
  nbapHF = { kind: 'all', month: 'all', day: '', team: 'all', model: 'all',
             stake: 'all', result: 'all', side: 'all', match: 'all' };
  nbapLogPage = 0; nbapRerender();
}

// Every play on the page, preview and official, one row per game, newest
// first, today's pending plays included.
function nbapHistoryCard(d) {
  const f = nbapHF;
  const rows = {};
  const key = (dt, g) => dt + '|' + g.away + '|' + g.home;
  d.days.forEach(x => x.games.forEach(g => {
    rows[key(x.date, g)] = { date: x.date, g, off: Object.assign({}, g.official || {}) };
  }));
  (d.offDays || []).forEach(x => x.games.forEach(g => {
    const k = key(x.date, g);
    if (rows[k]) Object.assign(rows[k].off, g.models);
    else rows[k] = { date: x.date, g: Object.assign({}, g, { models: {} }), off: Object.assign({}, g.models) };
  }));
  const groups = nbapGroups(d.models);
  const teamLine = (g, side) => g.line == null ? null : (side === 'home' ? g.line : -g.line);
  // the plays in a row that pass the play-level filters
  const playsOf = r => {
    const out = [];
    d.models.forEach(m => {
      if (f.model !== 'all' && f.model !== m.key) return;
      const x = r.g.models[m.key], o = r.off[m.key];
      if (x && x.units && f.kind !== 'official') out.push({ m: m.key, type: 'preview', ...x });
      if (o && f.kind !== 'preview') out.push({ m: m.key, type: 'official', ...o });
    });
    return out.filter(p =>
      (f.stake === 'all' || String(p.units) === f.stake) &&
      (f.result === 'all' || (f.result === 'pending' ? !p.result : p.result === f.result)) &&
      (f.side === 'all' || (() => { const t = teamLine(r.g, p.side); return t != null && (f.side === 'fav' ? t < 0 : t > 0); })()));
  };
  let list = Object.values(rows).filter(r =>
    (f.month === 'all' || r.date.slice(0, 6) === f.month) &&
    (!f.day || r.date === f.day) &&
    (f.team === 'all' || r.g.away === f.team || r.g.home === f.team) &&
    (f.match === 'all' || (() => {
      const grp = groups[+f.match.split(':')[0]], kind = f.match.split(':')[1];
      const mt = grp && nbapMatch(nbapCombineGame('', r.g, {}), grp.keys);
      return mt && (kind === 'any' || mt.kind === kind);
    })()));
  list = list.map(r => ({ ...r, plays: playsOf(r) })).filter(r => r.plays.length);
  list.sort((a, b) => b.date.localeCompare(a.date) || String(b.g.start || '').localeCompare(String(a.g.start || '')));

  // record of the plays shown, preview and official kept apart
  const recP = nbapNewRec(), recO = nbapNewRec();
  let pend = 0;
  list.forEach(r => r.plays.forEach(p => {
    if (!p.result) { pend++; return; }
    nbapAdd(p.type === 'official' ? recO : recP, p.result, p.units);
  }));

  const resTag = res => res ? ` <span class="${res === 'W' ? 'win-text' : res === 'L' ? 'loss-text' : ''}">${res}</span>`
    : ` <span style="color:${NBAP_DIM};font-size:11px">pending</span>`;
  // Pages of NBAP_HIST_DAYS days each (newest first), with a header row per day.
  const pageDates = [...new Set(list.map(r => r.date))];
  const pages = Math.max(1, Math.ceil(pageDates.length / NBAP_HIST_DAYS));
  const page = Math.min(nbapLogPage, pages - 1);
  const showDates = new Set(pageDates.slice(page * NBAP_HIST_DAYS, (page + 1) * NBAP_HIST_DAYS));
  let lastDate = null;
  const body = list.filter(r => showDates.has(r.date)).map(({ date, g, plays }) => {
    const dayHdr = date !== lastDate ? `<tr><td colspan="${4 + d.models.length}" style="padding:8px 6px 4px;
      font-weight:600;color:#c9d1d9;border-top:1px solid #30363d">${nbapIso(date)}${date === d.today ? ' · today' : ''}</td></tr>` : '';
    lastDate = date;
    const cells = d.models.map(m => {
      const parts = plays.filter(p => p.m === m.key).map(p => `<span style="white-space:nowrap">${
        p.type === 'official' ? nbapBadge('OFFICIAL ' + p.units + 'u', nbapOffColor(p.units))
          : nbapBadge('preview ' + p.units + 'u', p.units === 2 ? '#3fb950' : '#58a6ff')}
        ${nbapEsc(nbapPlay(g, p.side))}${resTag(p.result)}</span>`);
      return `<td style="padding:4px 6px">${parts.join('<br>') || `<span style="color:${NBAP_DIM}">—</span>`}</td>`;
    }).join('');
    return dayHdr + `<tr style="border-top:1px solid #21262d">
      <td style="padding:4px 6px;color:${NBAP_DIM};white-space:nowrap">${nbapShortDate(date)}</td>
      <td style="padding:4px 6px">${nbapEsc(nbapNick(g.away))} @ ${nbapEsc(nbapNick(g.home))}
        <div style="color:${NBAP_DIM};font-size:11px">line ${g.line == null ? '—' : (g.line > 0 ? '+' : '') + g.line}</div></td>
      <td class="center">${g.final ? `${g.final[0]}-${g.final[1]}` : `<span style="color:${NBAP_DIM}">—</span>`}</td>
      ${cells}<td style="padding:4px 6px">${nbapMatchTags(g, groups)}</td></tr>`;
  }).join('');

  // phone layout: a header per day, one stacked block per game
  let mobLast = null;
  const mobBody = list.filter(r => showDates.has(r.date)).map(({ date, g, plays }) => {
    const hdr = date !== mobLast ? `<div style="padding:12px 2px 2px;font-weight:700;color:#c9d1d9;border-top:1px solid #30363d">
      ${nbapIso(date)}${date === d.today ? ' · today' : ''}</div>` : '';
    mobLast = date;
    const lines = d.models.map(m => {
      const ps = plays.filter(p => p.m === m.key);
      if (!ps.length) return '';
      return `<div style="display:grid;grid-template-columns:92px 1fr;gap:6px;padding:3px 0;font-size:13px">
        <div style="color:${NBAP_DIM}">${nbapEsc(m.name)}</div><div>${ps.map(p => `${
          p.type === 'official' ? nbapBadge('OFFICIAL ' + p.units + 'u', nbapOffColor(p.units))
            : nbapBadge('preview ' + p.units + 'u', p.units === 2 ? '#3fb950' : '#58a6ff')}
          ${nbapEsc(nbapPlay(g, p.side))}${resTag(p.result)}`).join('<br>')}</div></div>`;
    }).join('');
    const tags = nbapMatchTags(g, groups);
    return hdr + nbapMobGame(g, lines + (tags.includes('—') ? '' : `<div style="margin-top:4px;display:flex;flex-wrap:wrap;gap:4px 10px">${tags.replace(/<br>/g, ' ')}</div>`),
      g.final ? `final ${g.final[0]}-${g.final[1]}` : 'pending');
  }).join('');

  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const allRows = Object.values(rows);
  const months = [...new Set(allRows.map(r => r.date.slice(0, 6)))].sort().reverse();
  const teams = [...new Set(allRows.flatMap(r => [r.g.away, r.g.home]))].sort();
  const dates = allRows.map(r => r.date).sort();
  const st = 'background:#1e1e1e;color:#e0e0e0;border:1px solid #444;border-radius:6px;padding:3px 8px;font-size:12px;margin:0 6px 6px 0;cursor:pointer';
  const sel = (k, opts) => `<select id="nbap-hf-${k}" onchange="nbapSetHF('${k}', this.value, this.id)" style="${st}">`
    + opts.map(([v, l]) => `<option value="${nbapEsc(v)}" ${f[k] === v ? 'selected' : ''}>${nbapEsc(l)}</option>`).join('') + '</select>';
  const matchOpts = [['all', 'Any matching']];
  groups.forEach((g, i) => {
    const n = g.keys.length > 2 ? 'All 3' : g.keys.map(k => NBAP_SHORT[k] || k).join('+');
    matchOpts.push([i + ':any', n + ' agree'], [i + ':both2', n + ' both 2u'], [i + ':both1', n + ' both 1u'], [i + ':split', n + ' split']);
  });
  const active = Object.entries(f).some(([k, v]) => (k === 'day' ? v !== '' : v !== 'all'));
  return `<div class="card card-games">
    <div class="card-title">Play history — preview and official
      <span style="color:${NBAP_DIM};font-weight:400;font-size:11px">${list.length} games · ${pageDates.length} days · 3 days per page</span></div>
    <div style="padding:4px 10px 2px">
      ${sel('kind', [['all', 'Preview + official'], ['preview', 'Preview only'], ['official', 'Official only']])}
      ${sel('month', [['all', 'All months']].concat(months.map(m => [m, MON[+m.slice(4) - 1] + ' ' + m.slice(0, 4)])))}
      <input type="date" id="nbap-hf-day" aria-label="Filter by day" value="${nbapIso(f.day)}"
        min="${nbapIso(dates[0])}" max="${nbapIso(dates[dates.length - 1])}"
        onchange="nbapSetHF('day', this.value, this.id)" style="${st};color-scheme:dark">
      ${sel('team', [['all', 'All teams']].concat(teams.map(t => [t, t])))}
      ${sel('model', [['all', 'All models']].concat(d.models.map(m => [m.key, m.name])))}
      ${sel('stake', [['all', 'All stakes'], ['2', '2u'], ['1', '1u']])}
      ${sel('result', [['all', 'All results'], ['W', 'Wins'], ['L', 'Losses'], ['P', 'Pushes'], ['pending', 'Pending']])}
      ${sel('side', [['all', 'Fav + dog'], ['fav', 'Favorites'], ['dog', 'Underdogs']])}
      ${sel('match', matchOpts)}
      ${active ? `<button onclick="nbapResetHF()" style="${st}">Clear filters</button>` : ''}
    </div>
    <div style="padding:0 10px 8px;font-size:12px">
      <span style="color:#c9d1d9">Preview plays shown: ${nbapRecCell(recP)}</span>
      <span style="color:#c9d1d9;margin-left:14px">Official: ${nbapRecCell(recO)}</span>
      ${pend ? `<span style="color:${NBAP_DIM};margin-left:14px">${pend} pending</span>` : ''}
    </div>
    ${nbapMobile() ? `<div style="padding:0 10px">${mobBody || `<div style="padding:10px 0;color:${NBAP_DIM}">No plays match these filters.</div>`}</div>`
    : `<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:12px">
      <thead><tr style="text-align:left;color:${NBAP_DIM};border-bottom:1px solid #30363d">
        <th style="padding:4px 6px">Date</th><th>Game</th><th class="center">Final</th>
        ${d.models.map(m => `<th style="padding:4px 6px">${nbapEsc(m.name)}</th>`).join('')}<th>Matching (preview)</th></tr></thead>
      <tbody>${body || `<tr><td colspan="${4 + d.models.length}" style="padding:10px;color:${NBAP_DIM}">No plays match these filters.</td></tr>`}</tbody>
    </table></div>`}
    <div class="date-nav" style="padding:8px 10px">
      <button onclick="nbapPage(-1)" ${page === 0 ? 'disabled' : ''}>Newer</button>
      <span class="current-view">${pageDates.length ? nbapShortDate([...showDates].pop()) + ' – ' + nbapShortDate([...showDates][0]) : ''} · ${page + 1}/${pages}</span>
      <button onclick="nbapPage(1)" ${page >= pages - 1 ? 'disabled' : ''}>Older</button>
    </div>
  </div>`;
}

// ── team spread record ─────────────────────────────────────────────────────
// Record by the team each model played, over the page's date range. Its own
// filters: which plays (preview / official / both), model, team. Models are
// never added together: "All models" shows a column (or a row) per model.
let nbapTF = { kind: 'combined', model: 'all', team: 'all' };
function nbapSetTF(k, v, focusId) {
  nbapTF[k] = v; nbapRerender();
  if (focusId) { const f = document.getElementById(focusId); if (f) f.focus(); }
}
function nbapTeamCard(d) {
  const f = nbapTF;
  const src = f.kind === 'official' ? (d.offDays || []) : f.kind === 'preview' ? d.days : d.combDays;
  const days = nbapDaysInRange(d, src);
  const label = nbapDay ? nbapIso(nbapDay)
    : nbapRange === 'season' ? 'season to date' : nbapRange === 'last7' ? 'last 7 days' : nbapRange === 'last14' ? 'last 14 days' : nbapRange;
  const models = d.models.filter(m => f.model === 'all' || f.model === m.key);
  const blank = () => ({ all: nbapNewRec(), two: nbapNewRec(), one: nbapNewRec(), fav: 0, dog: 0, n: 0, by: {} });
  // All models + all teams: one column per model, never added together.
  const split = f.model === 'all' && f.team === 'all';
  const rows = {};            // keyed by team, or by model when one team is selected
  const teams = new Set();
  let pending = 0;
  days.forEach(x => x.games.forEach(g => {
    if (g.skipped) return;
    models.forEach(m => {
      const p = g.models[m.key];
      if (!p || !p.units) return;
      const team = p.side === 'home' ? g.home : g.away;
      if (f.team !== 'all' && team !== f.team) return;
      if (!p.result) { pending++; return; }
      const key = f.team === 'all' ? team : m.name;
      const r = rows[key] || (rows[key] = blank());
      r.n++;
      nbapAdd(r.all, p.result, p.units);
      nbapAdd(r.by[m.key] || (r.by[m.key] = nbapNewRec()), p.result, p.units);
      nbapAdd(p.units === 2 ? r.two : r.one, p.result, p.units);
      const line = p.line != null ? p.line : g.line;
      if (line != null) { const t = p.side === 'home' ? line : -line; if (t < 0) r.fav++; else if (t > 0) r.dog++; }
    });
  }));
  // every team seen on the page stays in the dropdown, whatever the filters
  d.combDays.forEach(x => x.games.forEach(g => { teams.add(g.away); teams.add(g.home); }));
  if (f.team !== 'all') teams.add(f.team);
  const list = Object.entries(rows).sort((a, b) => split ? a[0].localeCompare(b[0])
    : (b[1].all.u - a[1].all.u || a[0].localeCompare(b[0])));
  const tot = blank();
  list.forEach(([, r]) => {
    ['all', 'two', 'one'].forEach(k => { tot[k].w += r[k].w; tot[k].l += r[k].l; tot[k].p += r[k].p; tot[k].u += r[k].u; });
    tot.fav += r.fav; tot.dog += r.dog; tot.n += r.n;
    Object.keys(r.by).forEach(k => { const t = tot.by[k] || (tot.by[k] = nbapNewRec()), x = r.by[k];
      t.w += x.w; t.l += x.l; t.p += x.p; t.u += x.u; });
  });
  const none = nbapNewRec();

  const st = 'background:#1e1e1e;color:#e0e0e0;border:1px solid #444;border-radius:6px;padding:3px 8px;font-size:12px;margin:0 6px 6px 0;cursor:pointer';
  const sel = (k, opts) => `<select id="nbap-tf-${k}" onchange="nbapSetTF('${k}', this.value, this.id)" style="${st}">`
    + opts.map(([v, l]) => `<option value="${nbapEsc(v)}" ${f[k] === v ? 'selected' : ''}>${nbapEsc(l)}</option>`).join('') + '</select>';
  const kindName = f.kind === 'official' ? 'official plays' : f.kind === 'preview' ? 'preview plays' : 'preview + official';
  const first = f.team === 'all' ? 'Team played' : 'Model';
  const th = t => `<th class="center" style="padding:4px 6px">${t}</th>`;
  const tr = (name, r, bold) => split ? `<tr style="border-top:1px solid #21262d${bold ? ';background:#161b22' : ''}">
      <td style="padding:6px">${bold ? `<b>${nbapEsc(name)}</b>` : nbapEsc(name)}</td>
      ${d.models.map(m => `<td class="center">${nbapRecCell(r.by[m.key] || none)}</td>`).join('')}</tr>`
    : `<tr style="border-top:1px solid #21262d${bold ? ';background:#161b22' : ''}">
      <td style="padding:6px">${bold ? `<b>${nbapEsc(name)}</b>` : nbapEsc(name)}</td>
      <td class="center">${r.n}</td><td class="center">${nbapRecCell(r.all)}</td>
      <td class="center">${nbapRecCell(r.two)}</td><td class="center">${nbapRecCell(r.one)}</td>
      <td class="center">${r.fav}</td><td class="center">${r.dog}</td></tr>`;
  const empty = `<div style="padding:8px 10px 12px;color:${NBAP_DIM};font-size:12px">No graded plays match these filters.</div>`;
  const mob = (list.length > 1 && (split || f.model !== 'all') ? [['Total', tot]] : []).concat(list).map(([name, r]) => `<div style="margin-bottom:10px"><div style="font-weight:700;margin-bottom:4px">${nbapEsc(name)}
        ${split ? '' : `<span style="font-weight:400;font-size:12px;color:${NBAP_DIM};margin-left:6px">${r.n} play${r.n === 1 ? '' : 's'} · ${r.fav} fav · ${r.dog} dog</span>`}</div>
      <div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px">${split
        ? d.models.map(m => nbapMobStat(m.name, r.by[m.key] || none)).join('')
        : nbapMobStat('All plays', r.all) + nbapMobStat('2u only', r.two) + nbapMobStat('1u only', r.one)}</div></div>`).join('');
  return `<div class="card card-games">
    <div class="card-title">Team spread record (ATS) — ${nbapEsc(kindName)}, ${nbapEsc(label)}
      <span style="color:${NBAP_DIM};font-weight:400;font-size:11px">${f.model === 'all' ? 'each model separately' : `${tot.n} graded play${tot.n === 1 ? '' : 's'}`}${pending ? ` · ${pending} pending` : ''}</span></div>
    <div style="padding:8px 10px 2px">
      ${sel('kind', [['combined', 'Preview + official'], ['official', 'Official plays'], ['preview', 'Preview plays']])}
      ${sel('model', [['all', 'All models']].concat(d.models.map(m => [m.key, m.name])))}
      ${sel('team', [['all', 'All teams']].concat([...teams].sort().map(t => [t, t])))}
      ${f.kind !== 'combined' || f.model !== 'all' || f.team !== 'all'
        ? `<button onclick="nbapTF={kind:'combined',model:'all',team:'all'};nbapRerender()" style="${st}">Clear filters</button>` : ''}
    </div>
    ${!list.length ? empty : nbapMobile() ? `<div style="padding:0 10px">${mob}</div>` : `
    <div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:12px">
      <thead><tr style="text-align:left;color:${NBAP_DIM};border-bottom:1px solid #30363d">
        <th style="padding:4px 6px">${first}</th>${split ? d.models.map(m => th(nbapEsc(m.name))).join('')
          : th('Plays') + th('All plays') + th('2u only') + th('1u only') + th('Fav') + th('Dog')}</tr></thead>
      <tbody>${list.map(([name, r]) => tr(name, r, false)).join('')}${list.length > 1 && (split || f.model !== 'all') ? tr('Total', tot, true) : ''}</tbody>
    </table></div>`}
  </div>`;
}

function nbapRerender() {
  if (!nbapData) return;
  const set = (id, html) => { const el = document.getElementById(id); if (el) el.innerHTML = html; };
  set('nbap-records', nbapRecordsCard(nbapData, nbapRecKind || (nbapData.anyOfficial ? 'official' : 'preview')));
  set('nbap-teams', nbapTeamCard(nbapData));
  set('nbap-log', nbapHistoryCard(nbapData));
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

// The model's own (engine-fired) pick in a daily run, or null on a PASS.
function nbapOfficial(g) {
  const sp = g.sPick;
  if (!sp || sp === 'PASS') return null;
  const t = String(sp);
  const side = t.startsWith(g.home) ? 'home' : t.startsWith(g.away) ? 'away'
    : (t.includes(nbapNick(g.home)) ? 'home' : (t.includes(nbapNick(g.away)) ? 'away' : null));
  if (!side) return null;
  const pc = Number.isFinite(g.pCover) ? g.pCover : null;
  const units = Number.isFinite(g.stakeUnits) ? g.stakeUnits : (pc != null && pc >= NBAP_ELITE ? 2 : 1);
  const line = Number.isFinite(g.line) ? g.line : null;
  let result = null;
  if (line != null && Number.isFinite(g.homeScore) && Number.isFinite(g.awayScore)) {
    const cover = g.homeScore - g.awayScore + line;
    result = cover === 0 ? 'P' : ((cover > 0) === (side === 'home') ? 'W' : 'L');
  }
  return { side, units, result, p: pc, line };
}

function nbapCombineGame(date, g, offByDate) {
  const off = Object.assign({}, ((offByDate[date] || {})[g.away + '|' + g.home] || {}).models || {}, g.official || {});
  if (!Object.keys(off).length) return g;
  const models = Object.assign({}, g.models);
  Object.keys(off).forEach(k => {
    models[k] = Object.assign({}, g.models[k] || {}, { side: off[k].side, units: off[k].units, result: off[k].result, official: true });
  });
  return Object.assign({}, g, { models });
}

function nbapBuild(histories, replay) {
  const offByDate = {};
  let anyOfficial = false;
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
          models: {},
        });
        const off = nbapOfficial(g);
        if (off) {
          anyOfficial = true;
          (row.official || (row.official = {}))[m.key] = off;
          const od = offByDate[r.date] || (offByDate[r.date] = {});
          const orow = od[k] || (od[k] = { away: g.away, home: g.home, line: off.line,
            start: g.startTimeUTC || null, final: null, models: {} });
          orow.models[m.key] = off;
          if (Number.isFinite(g.homeScore) && Number.isFinite(g.awayScore)) orow.final = [g.awayScore, g.homeScore];
        }
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
  // Days the replay covers are graded the replay's way (each game at its last
  // run before tip, same as the preview); later days come from the daily run.
  const through = (replay && replay.through) || '';
  const days = (replay && Array.isArray(replay.days) ? replay.days : [])
    .filter(x => x.date >= NBAP_SEASON_START && x.date <= through)
    .map(x => ({ date: x.date, live: false, replay: true, games: x.games }));
  dates.filter(d => d > through).forEach(d => days.push({
    date: d, live: d === today,
    games: Object.values(byDate[d]).sort((a, b) => String(a.start || '').localeCompare(String(b.start || ''))),
  }));
  // Preview plays stop once the model makes official picks: from each model's
  // first official-pick day on, its 58/65 preview play is dropped (the lean is
  // still shown) and only the official pick counts. Earlier days keep their
  // preview record.
  const offStart = {};
  Object.keys(offByDate).sort().forEach(d => Object.values(offByDate[d]).forEach(g =>
    Object.keys(g.models).forEach(k => { if (!offStart[k]) offStart[k] = d; })));
  days.forEach(x => x.games.forEach(g => Object.keys(offStart).forEach(k => {
    const m = g.models[k];
    if (m && x.date >= offStart[k] && m.units) { g.models[k] = Object.assign({}, m, { units: 0, previewOff: true }); }
  })));
  // Preview + official: each model's official pick where it made one, else its
  // preview play (preview plays already stop on official days).
  const combDays = days.map(x => ({ ...x, games: x.games.map(g => nbapCombineGame(x.date, g, offByDate)) }));
  const offDays = Object.keys(offByDate).sort().map(d => ({
    date: d, live: d === today, games: Object.values(offByDate[d]),
  }));
  return {
    generated, today, through, anyOfficial, offDays, offStart, combDays,
    models: NBAP_MODELS.map(m => ({ key: m.key, name: m.name, solo: !!m.solo })),
    days,
  };
}

async function renderNBAPreview() {
  const el = document.getElementById('content');
  el.innerHTML = '<div class="loading"><div class="spinner"></div><br>Loading NBA picks...</div>';
  const grab = async (url) => {
    try {
      const r = await fetch(url + '?t=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) return null;
      const j = await r.json();
      return (j && Array.isArray(j.runs)) ? j : null;
    } catch (e) { return null; }
  };
  const grab2 = async (url) => {
    try {
      const r = await fetch(url + '?t=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) return null;
      const j = await r.json();
      return (j && Array.isArray(j.days)) ? j : null;
    } catch (e) { return null; }
  };
  // Same loader and cache as the model tabs (fetchData in main.js), so this
  // page always shows exactly the run the Full Season / KalmanOff /
  // KalmanEarly tabs show.
  const histories = await Promise.all(NBAP_MODELS.map(m =>
    (typeof fetchData === 'function' && typeof SOURCES !== 'undefined' && SOURCES[m.tab])
      ? fetchData(m.tab) : grab(m.local).then(a => a || grab(m.remote))));
  const replay = (await Promise.all([
    grab2('data/nba-preview.json'),
    grab2('https://raw.githubusercontent.com/sspam1189-stack/Model/main/PythonDashboard/data/nba-preview.json'),
  ])).filter(Boolean).sort((a, b) => String(b.through || '').localeCompare(String(a.through || '')))[0] || null;
  if (!histories.some(Boolean)) {
    el.innerHTML = `<div class="card card-games"><div class="card-title">NBA Picks — No Data</div>
      <div class="no-picks" style="padding:20px 0 6px">The daily-run files could not be loaded.</div></div>`;
    return;
  }
  const data = nbapBuild(histories, replay);
  nbapData = data;
  if (typeof updateLastRunInfo === 'function') updateLastRunInfo();   // data is loaded now
  el.innerHTML = nbapTodayCard(data) + '<div id="nbap-records"></div><div id="nbap-teams"></div><div id="nbap-log"></div>';
  nbapRerender();
}
