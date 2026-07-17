// PythonDashboard/js/mlb-fade-ml.js
// Renderer for the "MLB Fade ML" tab. Reads mlb-fade-ml.json (run_daily_ml.py
// / ml_backfill.py). Three bet types on fade-list games:
//   ml      - single fade arm -> bet the OPPONENT moneyline
//   ml_dog  - mutual fade (both starters fade) -> bet the UNDERDOG moneyline
//   total   - OVER on the game total (fade arm -> more runs)

async function renderMLBFadeML() {
  const el = document.getElementById('content');
  el.innerHTML = '<div class="loading"><div class="spinner"></div><br>Loading fade ML...</div>';

  const local = 'data/mlb-fade-ml.json';
  const remote = 'https://raw.githubusercontent.com/sspam1189-stack/Model/main/MLBstrikeouts/data/mlb-fade-ml.json';
  let data = null;
  for (const url of [local + '?t=' + Date.now(), remote + '?t=' + Date.now()]) {
    try { const r = await fetch(url, { cache: 'no-store' }); if (r.ok) { data = await r.json(); break; } }
    catch (e) { /* next */ }
  }

  el.textContent = '';
  if (!data) {
    const c = document.createElement('div');
    c.className = 'card card-games';
    c.innerHTML = '<div class="card-title">MLB Fade ML</div>'
      + '<div class="no-picks" style="padding:20px 0 6px">Unable to load the fade-ML feed.</div>';
    el.appendChild(c);
    return;
  }

  const runEl = document.getElementById('last-run-info');
  if (runEl && data.generated) {
    const d = new Date(data.generated);
    runEl.textContent = 'Last run (CT) — MLB Fade ML: '
      + d.toLocaleString('en-US', { timeZone: 'America/Chicago', month: '2-digit', day: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: true });
  }

  const ORANGE = 'var(--orange,#e8a33d)';
  const GREEN = 'var(--green,#3fb950)', RED = 'var(--red,#f85149)';
  const fmtOdds = (o) => (o == null ? '—' : (o > 0 ? '+' + o : '' + o));
  const uColor = (u) => (u > 0 ? GREEN : (u < 0 ? RED : '#aaa'));
  const s = data.summary || {};
  const bt = s.byType || {};

  // ---- Overall banner ----
  const banner = document.createElement('div');
  banner.className = 'card';
  banner.style.cssText = 'margin-bottom:14px;border:1px solid ' + ORANGE + ';background:rgba(232,163,61,0.08);padding:14px 18px';
  const bigStat = (label, val, color) =>
    '<div style="text-align:center;min-width:80px">'
    + '<div style="font-size:22px;font-weight:700;color:' + (color || '#eee') + '">' + val + '</div>'
    + '<div style="font-size:11px;color:#aaa;text-transform:uppercase;letter-spacing:.04em">' + label + '</div></div>';
  const uStr = (u) => (u >= 0 ? '+' : '') + (u || 0).toFixed(2) + 'u';
  banner.innerHTML =
    '<div style="font-weight:600;color:' + ORANGE + ';margin-bottom:10px">Fade-list moneyline — fade the pitcher, underdog on mutual</div>'
    + '<div style="display:flex;gap:20px;flex-wrap:wrap;align-items:center">'
    + bigStat('Record', (s.wins || 0) + '–' + (s.losses || 0))
    + bigStat('Units', uStr(s.units), uColor(s.units))
    + bigStat('ROI', ((s.roi >= 0 ? '+' : '') + ((s.roi || 0) * 100).toFixed(1)) + '%', uColor(s.units))
    + bigStat('Risked', (s.staked || 0).toFixed(1) + 'u')
    + bigStat('Voids', (s.voids || 0))
    + '</div>';
  el.appendChild(banner);

  // ---- Per-type sub-records ----
  const typeMeta = [
    ['ml', 'Fade ML (opp)', 'Single fade arm → opponent moneyline'],
    ['ml_dog', 'Mutual → dog', 'Both starters fade → underdog ML'],
  ];
  const chips = document.createElement('div');
  chips.style.cssText = 'display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px';
  typeMeta.forEach(([k, lbl, sub]) => {
    const r = bt[k] || {};
    const card = document.createElement('div');
    card.className = 'card';
    card.style.cssText = 'flex:1;min-width:180px;padding:10px 14px';
    card.innerHTML =
      '<div style="font-weight:600;color:#ddd">' + lbl + '</div>'
      + '<div style="font-size:11px;color:#888;margin-bottom:6px">' + sub + '</div>'
      + '<div style="font-size:18px;font-weight:700">' + (r.wins || 0) + '–' + (r.losses || 0)
      + ' <span style="font-size:14px;color:' + uColor(r.units) + '">' + uStr(r.units)
      + ' · ' + (((r.roi || 0) * 100 >= 0 ? '+' : '') + ((r.roi || 0) * 100).toFixed(1)) + '%</span></div>';
    chips.appendChild(card);
  });
  el.appendChild(chips);

  // ---- Today's plays ----
  const today = (data.today || []);
  const tCard = document.createElement('div');
  tCard.className = 'card';
  tCard.style.cssText = 'margin-bottom:16px;padding:12px 16px';
  const REAL = new Set(['ml', 'ml_dog']);
  const pend = today.filter(t => t.result === 'pending' && REAL.has(t.betType));
  const label = (t) => {
    const who = (t.pitchers || []).join(' / ');
    if (t.betType === 'ml_dog') return '• Mutual (' + esc(who) + ') → dog <b>' + esc(t.selection || '?') + '</b> ML <span style="color:' + ORANGE + '">' + fmtOdds(t.odds) + '</span>';
    return '• Fade <b>' + esc(who) + '</b> (' + esc(t.fadeTeam || '?') + ') → <b>' + esc(t.selection || '?') + '</b> ML <span style="color:' + ORANGE + '">' + fmtOdds(t.odds) + '</span>';
  };
  let th = '<div class="card-title" style="margin-bottom:8px">Today’s plays</div>';
  if (!pend.length) th += '<div class="no-picks">No fade-list moneyline plays on today’s slate.</div>';
  else th += pend.map(t => '<div style="font-size:14px;color:#ddd;padding:2px 0">' + label(t) + '</div>').join('');
  tCard.innerHTML = th;
  el.appendChild(tCard);

  // ---- Season bet log (filterable by bet type + month + pitcher) ----
  const typeTag = { ml: 'ML', ml_dog: 'DOG' };
  const bets = (data.bets || []).slice().reverse(); // newest first

  // Distinct months (latest first) and pitchers (alphabetical).
  const months = [...new Set(bets.map(b => (b.date || '').slice(0, 7)).filter(Boolean))]
    .sort().reverse();
  const monthLabel = (ym) => {
    const [y, m] = ym.split('-');
    return ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][+m] + ' ' + y;
  };
  const pitchers = [...new Set(bets.flatMap(b => b.pitchers || []))].sort();

  // Distinct pick teams (the team you bet on), alphabetical.
  const pickTeams = [...new Set(bets.map(b => b.selection).filter(Boolean))].sort();

  const log = document.createElement('div');
  log.className = 'card card-games';
  log.style.cssText = 'padding:8px 4px';
  const selCss = 'background:#1b1b1b;color:#ddd;border:1px solid #333;border-radius:6px;padding:4px 8px;font-size:12px';
  log.innerHTML =
    '<div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:6px 8px">'
    + '<div class="card-title" style="padding:0">Season bet log</div>'
    + '<span id="fadeLogRec" style="font-size:13px;font-weight:700"></span>'
    + '<span style="flex:1"></span>'
    + '<label style="font-size:11px;color:#888">Pick '
    + '<select id="fadePickSel" style="' + selCss + '"><option value="">All</option>'
    + pickTeams.map(t => '<option value="' + esc(t) + '">' + esc(t) + '</option>').join('')
    + '</select></label>'
    + '<label style="font-size:11px;color:#888">Month '
    + '<select id="fadeMonthSel" style="' + selCss + '"><option value="">All</option>'
    + months.map(m => '<option value="' + m + '">' + monthLabel(m) + '</option>').join('')
    + '</select></label>'
    + '<label style="font-size:11px;color:#888">Pitcher '
    + '<select id="fadePitcherSel" style="' + selCss + '"><option value="">All</option>'
    + pitchers.map(p => '<option value="' + esc(p) + '">' + esc(p) + '</option>').join('')
    + '</select></label>'
    + '</div>'
    + '<div id="fadeLogWrap" style="overflow-x:auto"></div>';
  el.appendChild(log);

  const wrap = log.querySelector('#fadeLogWrap');
  const pickSel = log.querySelector('#fadePickSel');
  const monthSel = log.querySelector('#fadeMonthSel');
  const pitcherSel = log.querySelector('#fadePitcherSel');
  const recEl = log.querySelector('#fadeLogRec');

  function drawRows() {
    const kv = pickSel.value, mv = monthSel.value, pv = pitcherSel.value;
    const view = bets.filter(b =>
      (!kv || b.selection === kv) &&
      (!mv || (b.date || '').slice(0, 7) === mv) &&
      (!pv || (b.pitchers || []).includes(pv)));
    // W-L / units for the current filter (settled only).
    let w = 0, l = 0, u = 0, stk = 0;
    view.forEach(b => {
      if (b.result === 'WIN' || b.result === 'LOSS') { w += b.result === 'WIN'; l += b.result === 'LOSS'; u += b.profit; stk += (b.stake || 0); }
    });
    const roi = stk ? (u / stk * 100) : 0;
    recEl.innerHTML = w + '–' + l
      + ' <span style="color:' + uColor(u) + '">' + (u >= 0 ? '+' : '') + u.toFixed(2) + 'u · '
      + (roi >= 0 ? '+' : '') + roi.toFixed(1) + '%</span>';
    let rows = '';
    view.forEach(b => {
      const settled = b.result === 'WIN' || b.result === 'LOSS';
      const dim = settled ? '' : 'opacity:.5;';
      const resColor = b.result === 'WIN' ? GREEN : (b.result === 'LOSS' ? RED : '#888');
      const prof = settled ? ((b.profit >= 0 ? '+' : '') + b.profit.toFixed(2) + 'u') : '—';
      const profColor = !settled ? '#888' : (b.profit >= 0 ? GREEN : RED);
      const pick = esc(b.selection || '?');
      const note = b.result === 'VOID' ? (' <span style="color:#777;font-size:10px">' + esc(b.reason || 'void') + '</span>')
        : (b.result === 'SKIP' ? ' <span style="color:#777;font-size:10px">skip</span>' : '');
      rows += '<tr style="' + dim + '">'
        + '<td style="padding:4px 8px;color:#999">' + esc(b.date) + '</td>'
        + '<td style="padding:4px 6px;color:' + ORANGE + ';font-size:11px;font-weight:700">' + (typeTag[b.betType] || '') + '</td>'
        + '<td style="padding:4px 8px">' + esc((b.pitchers || []).join(' / ')) + '</td>'
        + '<td style="padding:4px 8px;font-weight:600">' + pick + note + '</td>'
        + '<td style="padding:4px 8px;text-align:right;color:' + ORANGE + '">' + fmtOdds(b.odds) + '</td>'
        + '<td style="padding:4px 8px;text-align:center;color:' + resColor + ';font-weight:700">' + esc(b.result) + '</td>'
        + '<td style="padding:4px 8px;text-align:right;color:' + profColor + '">' + prof + '</td>'
        + '</tr>';
    });
    wrap.innerHTML =
      '<div style="font-size:12px;color:#888;padding:2px 8px 8px">' + view.length + ' bets shown</div>'
      + '<table style="width:100%;border-collapse:collapse;font-size:13px">'
      + '<thead><tr style="color:#888;text-align:left;border-bottom:1px solid #333">'
      + '<th style="padding:4px 8px">Date</th><th style="padding:4px 6px">Type</th>'
      + '<th style="padding:4px 8px">Fade arm(s)</th><th style="padding:4px 8px">Pick</th>'
      + '<th style="padding:4px 8px;text-align:right">Odds</th>'
      + '<th style="padding:4px 8px;text-align:center">Result</th><th style="padding:4px 8px;text-align:right">P/L</th>'
      + '</tr></thead><tbody>' + rows + '</tbody></table>';
  }
  pickSel.addEventListener('change', drawRows);
  monthSel.addEventListener('change', drawRows);
  pitcherSel.addEventListener('change', drawRows);
  drawRows();
}

if (typeof esc === 'undefined') {
  window.esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
