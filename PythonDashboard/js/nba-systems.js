// PythonDashboard/js/nba-systems.js
// Renderer for the "NBA Systems" tab. Reads nba-systems.json
// (pyFull/scripts/build_nba_systems.py).
//
// Seven candidates are CARDED (bet); the model reference stays SHADOW (graded,
// never bet). The carding decision was taken at registry freeze on in-sample
// evidence, so the page leads with what that evidence is worth rather than with
// the numbers: the systems come from ~3,600 conditions screened against ONE
// season, a permutation test returns 13 winners where noise returns 7.1, and
// systems picked this way lost 4.2% out of sample in a walk-forward test.
//
// Five known-junk controls used to run alongside these as a null and were
// dropped 2026-09-02. The banner says so, because without them the forward
// record can only be read against break-even rather than against what this
// screen returns on nothing -- and the reader deserves to know which of those
// two comparisons they are looking at.
//
// Four panels: today's card (sectioned CARD / SHADOW, because that is what
// gets acted on), the per-system record with live and backtest kept strictly
// apart, a definitions card stating each system's literal trigger, and the
// filterable season log.

const NBAS_DIM = '#8b949e';
const NBAS_GREEN = '#3fb950';
const NBAS_RED = '#f85149';
const NBAS_AMBER = '#d29922';

// Tier drives colour everywhere: a chip, a record row and a log row all read
// as the same thing. Controls are amber -- not red, which means "loss" on the
// rest of the page, and not green, which would read as endorsement.
const NBAS_TIER_COLOR = {
  candidate: '#7c5cff',
  control: NBAS_AMBER,
  reference: NBAS_DIM,
};
const NBAS_TIER_LABEL = {
  candidate: 'CANDIDATE',
  control: 'CONTROL — known junk, same bar',
  reference: 'REFERENCE — the model itself',
};

let nbasFilters = {
  system: 'all', tier: 'all', market: 'all', result: 'all', season: 'all',
};
let nbasLogPage = 0;
let nbasShowMechanism = null;   // system id whose mechanism is expanded
// Last feed, kept so a filter change is a pure re-render rather than two more
// 1.4MB fetches. Refreshed whenever the tab is entered.
let nbasData = null;
const NBAS_PAGE = 25;

// Filters re-render IN PLACE. Calling the global render() here used to rebuild
// #content from scratch, which threw the reader back to the top of a 3,700px
// page on every filter change and page turn -- and re-fetched the 1.4MB feed
// twice (local + remote) each time. Only the three filter-dependent panels are
// rewritten now; the banner and the definitions card never change, so the page
// above the fold does not move and scroll position survives untouched.
function nbasRerender(focusId) {
  if (!nbasData) { render(); return; }
  const set = (id, html) => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = html;
  };
  set('nbas-today', nbasTodayCard(nbasData));
  set('nbas-record', nbasRecordTable(nbasData));
  set('nbas-log', nbasLogTable(nbasData));
  // The <select> that triggered this was just destroyed and rebuilt, so hand
  // focus back to its replacement or the next keyboard change goes nowhere.
  if (focusId) {
    const f = document.getElementById(focusId);
    if (f) f.focus();
  }
}

function nbasSetFilter(k, v, focusId) {
  nbasFilters[k] = v;
  nbasLogPage = 0;
  nbasRerender(focusId);
}
function nbasSetSystem(id) {
  nbasFilters.system = (nbasFilters.system === id) ? 'all' : id;
  nbasLogPage = 0;
  nbasRerender();
}
function nbasPage(d) { nbasLogPage = Math.max(0, nbasLogPage + d); nbasRerender(); }
function nbasToggleMech(id) {
  nbasShowMechanism = (nbasShowMechanism === id) ? null : id;
  nbasRerender();
}

// Season label from a YYYYMMDD date. The NBA year straddles the calendar, so
// anything from September on belongs to the season that starts that year.
function nbasSeason(d8) {
  if (!d8 || d8.length < 6) return '?';
  const y = +d8.slice(0, 4), m = +d8.slice(4, 6);
  const start = m >= 9 ? y : y - 1;
  return start + '-' + String((start + 1) % 100).padStart(2, '0');
}

function nbasDate(d8) {
  const s = String(d8 || '');
  return s.length === 8 ? s.slice(0, 4) + '-' + s.slice(4, 6) + '-' + s.slice(6) : s;
}

function nbasCtTime(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleTimeString('en-US', {
      timeZone: 'America/Chicago', hour: 'numeric', minute: '2-digit',
    });
  } catch (e) { return ''; }
}

// id -> display name, so a play row reads "Home dog over" rather than an id
function nbasName(d, id) {
  const s = (d.systems || []).find(x => x.id === id);
  return (s && s.name) || id;
}

function nbasPrice(p) {
  if (p === null || p === undefined) return '';
  return p > 0 ? '+' + p : String(p);
}

function nbasPct(x) {
  return Number.isFinite(x) ? (x * 100).toFixed(1) + '%' : '—';
}

function nbasUnits(u) {
  if (!Number.isFinite(u)) return '—';
  return (u >= 0 ? '+' : '') + u.toFixed(1) + 'u';
}

function nbasUClass(u) {
  return Number.isFinite(u) && u !== 0 ? (u > 0 ? 'win-text' : 'loss-text') : '';
}

// ---------------------------------------------------------------------------
async function renderNBASystems() {
  const el = document.getElementById('content');
  el.innerHTML = '<div class="loading"><div class="spinner"></div><br>Loading NBA systems...</div>';

  const local = 'data/nba-systems.json';
  const remote = 'https://raw.githubusercontent.com/sspam1189-stack/Model/main/pyFull/data/nba-systems.json';
  // Newest-wins, same as the slate-scout tab: the local copy is republished by
  // a hand-maintained whitelist in the workflow, and when a new output is
  // missed there it freezes silently. Taking the newer `generated` heals from
  // the canonical pyFull copy on its own.
  const grab = async (url) => {
    try {
      const r = await fetch(url + '?t=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) return null;
      const j = await r.json();
      return (j && Array.isArray(j.systems)) ? j : null;
    } catch (e) { return null; }
  };
  const data = (await Promise.all([grab(local), grab(remote)]))
    .filter(Boolean)
    .sort((a, b) => String(b.generated || '').localeCompare(String(a.generated || '')))[0];

  if (!data) {
    el.innerHTML = `<div class="card card-games">
      <div class="card-title">NBA Systems — No Data</div>
      <div class="no-picks" style="padding:20px 0 6px">
        No systems feed yet. It is written by pyFull/scripts/build_nba_systems.py
        on each daily run.</div></div>`;
    return;
  }

  nbasData = data;
  // Stable wrappers so nbasRerender() can replace just the filter-dependent
  // panels instead of the whole page.
  el.innerHTML = nbasBanner(data)
    + '<div id="nbas-today">' + nbasTodayCard(data) + '</div>'
    + '<div id="nbas-record">' + nbasRecordTable(data) + '</div>'
    + nbasDefinitions(data)
    + '<div id="nbas-log">' + nbasLogTable(data) + '</div>';
}

// ---------------------------------------------------------------------------
function nbasBanner(d) {
  const nCard = d.systems.filter(s => s.status === 'card').length;
  const live = (d.totals && d.totals.live) || {};
  const card = (d.totals && d.totals.card) || {};
  const graded = (live.n || 0) + (live.p || 0);
  return `
    <div class="card card-games" style="border-left:3px solid ${NBAS_GREEN}">
      <div class="card-title">NBA Systems
        <span style="color:${NBAS_DIM};font-weight:400;font-size:11px">
          registry frozen ${esc(d.registry_frozen || '?')} ·
          <b style="color:${NBAS_GREEN}">${nCard} carded</b> ·
          ${d.systems.length - nCard} shadow</span>
      </div>
      <div style="padding:2px 10px 10px;font-size:12px;color:#c9d1d9;line-height:1.55">
        <b style="color:${NBAS_GREEN}">${nCard} candidates are bet.</b> That was
        decided at registry freeze, on in-sample evidence, without waiting for
        the ${d.promotion_min_plays || 25}-play out-of-sample gate — so the
        backtest below is a claim these plays are testing, not a result they
        have earned. The numbers behind that caveat: the systems come from one
        season screened over ~3,600 conditions; a permutation test returns 7.1
        winners from pure noise where the screen found 13; its best system
        (+30.9u) is what noise produces one time in five; and 22 systems picked
        this way on the first half of 2025-26 lost 4.2% on the second, with 5 of
        22 staying positive where chance predicts 11.
        <br><br>
        <b style="color:${NBAS_AMBER}">The controls were dropped.</b> Five
        known-junk conditions that cleared the same ROI &gt; 10% bar used to run
        alongside these unbet, so the season could show whether the carded
        systems were distinguishable from noise. Without them this record can
        only be read against break-even, not against what the screen returns on
        nothing. They are recoverable from git if that comparison is wanted.
        <br><br>
        <span style="color:${NBAS_DIM}">Live graded so far: <b>${graded}</b> plays
        ${graded ? '· card ' + nbasUnits(card.units) : '· the season has not started'}.
        Seeded 2025-26 plays appear in the log below but are held out of every
        live record.</span>
      </div>
    </div>`;
}

// ---------------------------------------------------------------------------
// Panel 1 — tonight's card. Sectioned by tier, first pitch inside each.
function nbasTodayCard(d) {
  const plays = (d.today || []).filter(p =>
    (nbasFilters.system === 'all' || p.system === nbasFilters.system));
  const title = `Today's card — ${esc(d.dateDisplay || d.date || '')}`
    + `<span style="color:${NBAS_DIM};font-weight:400;font-size:11px">`
    + ` (${d.games_today || 0} game${d.games_today === 1 ? '' : 's'} on the slate`
    + ` · CARD is bet · SHADOW is graded but never bet)</span>`;

  if (!plays.length) {
    const why = (d.games_today || 0) === 0
      ? 'No games on the slate — the season is not running.'
      : 'No system fired on this slate.';
    return `<div class="card card-games"><div class="card-title">${title}</div>
      <div style="padding:10px;font-size:12px;color:${NBAS_DIM}">${why}</div></div>`;
  }

  let h = `<div class="card card-games"><div class="card-title">${title}</div>`;
  h += '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:12px">'
    + `<thead><tr style="text-align:left;color:${NBAS_DIM};border-bottom:1px solid #30363d">`
    + '<th style="padding:4px 6px">CT</th><th>Game</th><th>Play</th>'
    + '<th>Price</th><th>System</th></tr></thead><tbody>';

  // Sectioned by what gets ACTED ON, not by tier: the card is the thing you
  // bet, and interleaving it with rows kept only for the record is how a
  // shadow play ends up on a bet slip. Tier stays visible as the chip colour.
  const SECTION = {
    card: 'CARD — bet these',
    shadow: 'SHADOW — graded, never bet',
  };
  let status = null;
  for (const p of plays) {
    if (p.status !== status) {
      status = p.status;
      const sc = status === 'card' ? NBAS_GREEN : NBAS_DIM;
      h += `<tr><td colspan="5" style="padding:7px 6px 3px;font-size:11px;font-weight:600;`
        + `border-top:1px solid #30363d;color:${sc}">${esc(SECTION[status] || status)}</td></tr>`;
    }
    const c = NBAS_TIER_COLOR[p.tier] || NBAS_DIM;
    const dim = p.status !== 'card' ? ';opacity:.68' : '';
    // A converted moneyline price is not a real quote. Marking it on the row is
    // the only place a reader would otherwise assume it was.
    const conv = p.market === 'h2h'
      ? `<span style="color:${NBAS_AMBER}" title="frozen spread-to-ML conversion, not a book quote">*</span>` : '';
    h += `<tr style="border-top:1px solid #161b22${dim}">`
      + `<td style="padding:3px 6px;color:${NBAS_DIM}">${esc(nbasCtTime(p.startTimeUTC))}</td>`
      + `<td style="padding:3px 6px">${esc(p.game)}</td>`
      + `<td style="padding:3px 6px;font-weight:600;white-space:nowrap">${esc(p.play)}</td>`
      + `<td style="padding:3px 6px;color:${NBAS_DIM};white-space:nowrap">${esc(nbasPrice(p.price))}${conv}</td>`
      + `<td><span style="display:inline-block;padding:1px 6px;border-radius:3px;font-weight:600;`
      + `white-space:nowrap;background:${c}22;color:${c};cursor:pointer" `
      + `onclick="nbasSetSystem('${p.system}')" title="${esc(p.label || '')}">`
      + `${esc(nbasName(d, p.system))}</span></td></tr>`;
  }
  h += '</tbody></table></div>';
  if (plays.some(p => p.market === 'h2h')) {
    h += `<div style="padding:6px 10px;font-size:11px;color:${NBAS_DIM}">`
      + `<span style="color:${NBAS_AMBER}">*</span> moneyline price is the frozen `
      + `spread-to-ML conversion, which is what grades the play. The real book `
      + `price is recorded alongside it; the gap between them is the measurement `
      + `the backtest could never make.</div>`;
  }
  return h + '</div>';
}

// ---------------------------------------------------------------------------
// Panel 2 — per-system record. LIVE and BACKTEST never share a column.
function nbasRecordTable(d) {
  const rows = d.systems.filter(s =>
    (nbasFilters.tier === 'all' || s.tier === nbasFilters.tier)).map(s => {
    const rec = (d.records || {})[s.id] || {};
    const live = rec.live || {};
    const b = s.backtest || {};
    const on = nbasFilters.system === s.id;
    const c = NBAS_TIER_COLOR[s.tier] || NBAS_DIM;
    const liveRec = (live.n || live.p)
      ? `${live.w}-${live.l}${live.p ? '-' + live.p : ''}` : '—';
    const bUnits = Number.isFinite(b.units) ? nbasUnits(b.units) : '—';
    const conv = b.priced === 'converted'
      ? `<span style="color:${NBAS_AMBER}" title="units from the frozen conversion, not observed prices">*</span>` : '';
    const mech = nbasShowMechanism === s.id
      ? `<tr><td colspan="9" style="padding:6px 10px 10px;font-size:11px;color:${NBAS_DIM};`
        + `line-height:1.5;background:#0d1117">${esc(s.mechanism)}</td></tr>` : '';
    const stat = s.status === 'card'
      ? `<span style="color:${NBAS_GREEN};font-weight:600;font-size:11px">CARD</span>`
      : `<span style="color:${NBAS_DIM};font-size:11px">shadow</span>`;
    return `<tr style="cursor:pointer${on ? ';background:rgba(124,92,255,0.15)' : ''}">
        <td onclick="nbasSetSystem('${s.id}')">${on ? '▸ ' : ''}
          <span style="display:inline-block;width:8px;height:8px;border-radius:2px;
            background:${c};margin-right:7px"></span>${esc(s.name || s.id)}
          <span style="color:${NBAS_DIM};font-size:10px">${esc(s.id)}</span></td>
        <td>${stat}</td>
        <td style="color:${c};font-size:11px">${esc(s.tier)}</td>
        <td style="color:${NBAS_DIM};font-size:11px">${esc(s.market)}</td>
        <td class="center">${liveRec}</td>
        <td class="center"><span class="${nbasUClass(live.units)}">${live.n || live.p ? nbasUnits(live.units) : '—'}</span></td>
        <td class="center">${live.pending ? `<span style="color:${NBAS_DIM}">${live.pending} pend</span>` : '—'}</td>
        <td class="center" style="color:${NBAS_DIM}">${b.w}-${b.l} · ${bUnits}${conv}</td>
        <td class="center" style="color:${NBAS_DIM}">${nbasPct(b.roi)}</td>
        <td class="center"><span onclick="nbasToggleMech('${s.id}')"
          style="cursor:pointer;color:${NBAS_DIM}" title="why this is in the registry">
          ${nbasShowMechanism === s.id ? '▾' : '▸'} why</span></td>
      </tr>${mech}`;
  }).join('');

  const tierSel = ['all', 'candidate', 'control', 'reference']
    .map(t => `<option value="${t}" ${nbasFilters.tier === t ? 'selected' : ''}>`
      + `${t === 'all' ? 'All tiers' : t}</option>`).join('');

  return `
    <div class="card card-records">
      <div class="card-title">System record
        <select id="nbas-sel-tier" onchange="nbasSetFilter('tier', this.value, this.id)"
          style="background:#1e1e1e;color:#e0e0e0;border:1px solid #444;border-radius:6px;
                 padding:4px 10px;font-size:13px;margin-left:10px;cursor:pointer">${tierSel}</select>
      </div>
      <div style="overflow-x:auto"><table class="data">
        <thead><tr>
          <th>System</th><th>Status</th><th>Tier</th><th>Market</th>
          <th class="center">Live W-L</th><th class="center">Live units</th>
          <th class="center">Pending</th>
          <th class="center">2025-26 (in-sample)</th><th class="center">ROI</th>
          <th class="center"></th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table></div>
      <div class="card-subtitle">
        Live and 2025-26 are kept in separate columns and never summed — the
        backtest is the claim being tested, not evidence for it. Click a system
        to filter the whole tab.
        <span style="color:${NBAS_AMBER}">*</span> units from the frozen
        spread-to-moneyline conversion rather than observed prices.
      </div>
    </div>`;
}

// ---------------------------------------------------------------------------
// Panel 3 — what each system actually IS. Always visible, never behind a
// toggle: a page that shows a play and a record without stating the trigger
// leaves the reader guessing at what was bet and why. Same shape as the MLB
// rule-status card — display name, the literal rule, then a one-line why
// carrying the record and the carding date.
function nbasDefinitions(d) {
  const rows = d.systems.map(s => {
    const c = NBAS_TIER_COLOR[s.tier] || NBAS_DIM;
    const carded = s.status === 'card';
    const chip = carded
      ? `<span style="background:rgba(63,185,80,.18);color:${NBAS_GREEN};
           padding:1px 6px;border-radius:3px;font-weight:600;font-size:10px">CARD</span>`
      : `<span style="background:rgba(139,148,158,.14);color:${NBAS_DIM};
           padding:1px 6px;border-radius:3px;font-weight:600;font-size:10px">SHADOW</span>`;
    return `<div style="padding:9px 10px;border-top:1px solid #161b22">
      <div style="font-size:12px;font-weight:600;color:${c}">
        ${esc(s.name || s.id)} ${chip}
        <span style="color:${NBAS_DIM};font-weight:400;font-size:11px">
          · ${esc(s.id)} · ${esc(s.market)}</span>
      </div>
      <div style="font-size:12px;color:#c9d1d9;margin-top:3px">${esc(s.rule || s.label)}</div>
      <div style="font-size:11px;color:${NBAS_DIM};margin-top:3px;line-height:1.5">${esc(s.why || '')}</div>
    </div>`;
  }).join('');
  return `<div class="card card-games">
    <div class="card-title">What these systems are
      <span style="color:${NBAS_DIM};font-weight:400;font-size:11px">
        (the literal trigger, then the evidence behind it — every record quoted
        here is in-sample 2025-26)</span></div>
    ${rows}</div>`;
}

// ---------------------------------------------------------------------------
// Panel 4 — the season bet log, filterable.
function nbasLogTable(d) {
  const all = (d.log || []).filter(e => !e.no_play);
  const f = nbasFilters;
  const rows = all.filter(e =>
    (f.system === 'all' || e.system === f.system) &&
    (f.tier === 'all' || e.tier === f.tier) &&
    (f.market === 'all' || e.market === f.market) &&
    (f.result === 'all' || (f.result === 'pending'
      ? e.result === 'pending' : e.result === f.result)) &&
    (f.season === 'all' || nbasSeason(e.date) === f.season)
  ).sort((a, b) => String(b.date).localeCompare(String(a.date))
    || String(a.system).localeCompare(String(b.system)));

  const seasons = [...new Set(all.map(e => nbasSeason(e.date)))].sort().reverse();
  const sel = (key, opts, labels) => `<select id="nbas-sel-${key}"
      onchange="nbasSetFilter('${key}', this.value, this.id)"
      style="background:#1e1e1e;color:#e0e0e0;border:1px solid #444;border-radius:6px;
             padding:3px 8px;font-size:12px;margin-right:6px;cursor:pointer">`
    + opts.map((o, i) => `<option value="${o}" ${f[key] === o ? 'selected' : ''}>`
      + `${esc((labels && labels[i]) || o)}</option>`).join('') + '</select>';

  // Totals over the FILTERED slice, split so a seeded backtest row can never
  // be added to a live one.
  const sum = (list) => {
    let w = 0, l = 0, p = 0, u = 0, pend = 0;
    list.forEach(e => {
      if (e.result === 'WIN') { w++; u += e.profit || 0; }
      else if (e.result === 'LOSS') { l++; u += e.profit || 0; }
      else if (e.result === 'PUSH') { p++; }
      else pend++;
    });
    const n = w + l + p;
    return { w, l, p, u, pend, n, roi: n ? u / n : null };
  };
  const liveSum = sum(rows.filter(e => !e.backfilled));
  const seedSum = sum(rows.filter(e => e.backfilled));

  const totalPages = Math.max(1, Math.ceil(rows.length / NBAS_PAGE));
  const page = Math.min(nbasLogPage, totalPages - 1);
  const slice = rows.slice(page * NBAS_PAGE, page * NBAS_PAGE + NBAS_PAGE);

  const body = slice.map(e => {
    const c = NBAS_TIER_COLOR[e.tier] || NBAS_DIM;
    const res = e.result === 'WIN' ? `<span class="win-text">WIN</span>`
      : e.result === 'LOSS' ? `<span class="loss-text">LOSS</span>`
        : e.result === 'PUSH' ? 'PUSH'
          : `<span style="color:${NBAS_DIM}">pending</span>`;
    const prof = (e.result === 'WIN' || e.result === 'LOSS')
      ? `<span class="${nbasUClass(e.profit)}">${nbasUnits(e.profit)}</span>` : '—';
    const bp = (e.book_price !== null && e.book_price !== undefined
      && e.market === 'h2h' && e.book_price !== e.price)
      ? `<span style="color:${NBAS_DIM}" title="real book price">(${esc(nbasPrice(e.book_price))})</span>` : '';
    return `<tr style="border-top:1px solid #161b22${e.backfilled ? ';opacity:.6' : ''}">
      <td style="padding:3px 6px;color:${NBAS_DIM};white-space:nowrap">${esc(nbasDate(e.date))}
        ${e.backfilled ? `<span style="font-size:10px;color:${NBAS_AMBER}" title="replayed 2025-26 backtest, held out of the live record">seed</span>` : ''}</td>
      <td style="padding:3px 6px">${esc(e.game || '')}</td>
      <td style="padding:3px 6px;font-weight:600;white-space:nowrap">${esc(e.play || '')}</td>
      <td style="padding:3px 6px;color:${NBAS_DIM};white-space:nowrap">${esc(nbasPrice(e.price))} ${bp}</td>
      <td style="padding:3px 6px"><span style="color:${c};font-size:11px">${esc(e.system || '')}</span>
        ${e.status === 'card' ? `<span style="color:${NBAS_GREEN};font-size:10px;font-weight:600"> CARD</span>` : ''}</td>
      <td class="center">${res}</td>
      <td class="center">${prof}</td></tr>`;
  }).join('');

  return `
    <div class="card card-games">
      <div class="card-title">Season bet log
        <span style="color:${NBAS_DIM};font-weight:400;font-size:11px">
          ${rows.length} of ${all.length} plays</span>
      </div>
      <div style="padding:6px 10px 8px">
        ${sel('season', ['all'].concat(seasons), ['All seasons'].concat(seasons))}
        ${sel('system', ['all'].concat(d.systems.map(s => s.id)),
              ['All systems'].concat(d.systems.map(s => s.id)))}
        ${sel('market', ['all', 'spread', 'total', 'h2h'],
              ['All markets', 'Spread', 'Total', 'Moneyline'])}
        ${sel('result', ['all', 'WIN', 'LOSS', 'PUSH', 'pending'],
              ['All results', 'Wins', 'Losses', 'Pushes', 'Pending'])}
      </div>
      <div style="padding:0 10px 8px;font-size:12px">
        <span style="color:#c9d1d9">Live: <b>${liveSum.w}-${liveSum.l}${liveSum.p ? '-' + liveSum.p : ''}</b>
          <span class="${nbasUClass(liveSum.u)}">${nbasUnits(liveSum.u)}</span>
          ${liveSum.roi !== null ? '· ROI ' + nbasPct(liveSum.roi) : ''}
          ${liveSum.pend ? `<span style="color:${NBAS_DIM}"> · ${liveSum.pend} pending</span>` : ''}</span>
        ${seedSum.n ? `<span style="color:${NBAS_DIM};margin-left:14px">Seeded 2025-26 (in-sample, not live):
          ${seedSum.w}-${seedSum.l}${seedSum.p ? '-' + seedSum.p : ''} ${nbasUnits(seedSum.u)}
          · ROI ${nbasPct(seedSum.roi)}</span>` : ''}
      </div>
      <div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:12px">
        <thead><tr style="text-align:left;color:${NBAS_DIM};border-bottom:1px solid #30363d">
          <th style="padding:4px 6px">Date</th><th>Game</th><th>Play</th>
          <th>Price</th><th>System</th>
          <th class="center">Result</th><th class="center">P/L</th>
        </tr></thead><tbody>${body || `<tr><td colspan="7" style="padding:10px;color:${NBAS_DIM}">No plays match these filters.</td></tr>`}</tbody>
      </table></div>
      <div class="date-nav" style="padding:8px 10px">
        <button onclick="nbasPage(-1)" ${page === 0 ? 'disabled' : ''}>Newer</button>
        <span class="current-view">Page ${page + 1} / ${totalPages}</span>
        <button onclick="nbasPage(1)" ${page >= totalPages - 1 ? 'disabled' : ''}>Older</button>
      </div>
    </div>`;
}
