// PythonDashboard/js/mlb-fade-ml.js
// Renderer for the "MLB Fade ML" tab. Reads mlb-fade-ml.json (produced by
// run_daily_ml.py / ml_backfill.py) and shows the moneyline record, today's
// fade plays, and the season bet log.
//
// The fade rule: when a fade-list pitcher starts, bet the OPPONENT's ML.
// Both starters on the fade list -> no bet (shown as SKIP).

async function renderMLBFadeML() {
  const el = document.getElementById('content');
  el.innerHTML = '<div class="loading"><div class="spinner"></div><br>Loading fade ML...</div>';

  const local = 'data/mlb-fade-ml.json';
  const remote = 'https://raw.githubusercontent.com/sspam1189-stack/Model/main/MLBstrikeouts/data/mlb-fade-ml.json';
  let data = null;
  for (const url of [local + '?t=' + Date.now(), remote + '?t=' + Date.now()]) {
    try {
      const resp = await fetch(url, { cache: 'no-store' });
      if (resp.ok) { data = await resp.json(); break; }
    } catch (e) { /* try next */ }
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
    const ct = d.toLocaleString('en-US', { timeZone: 'America/Chicago', month: '2-digit', day: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: true });
    runEl.textContent = 'Last run (CT) — MLB Fade ML: ' + ct;
  }

  const ORANGE = 'var(--orange,#e8a33d)';
  const s = data.summary || {};
  const fmtOdds = (o) => (o == null ? '—' : (o > 0 ? '+' + o : '' + o));
  const units = (s.units || 0);
  const roiPct = ((s.roi || 0) * 100);
  const unitColor = units > 0 ? 'var(--green,#3fb950)' : (units < 0 ? 'var(--red,#f85149)' : '#aaa');

  // ---- Record banner ----
  const banner = document.createElement('div');
  banner.className = 'card';
  banner.style.cssText = 'margin-bottom:16px;border:1px solid ' + ORANGE + ';background:rgba(232,163,61,0.08);padding:14px 18px';
  const stat = (label, val, color) =>
    '<div style="text-align:center;min-width:84px">'
    + '<div style="font-size:22px;font-weight:700;color:' + (color || '#eee') + '">' + val + '</div>'
    + '<div style="font-size:11px;color:#aaa;text-transform:uppercase;letter-spacing:.04em">' + label + '</div></div>';
  banner.innerHTML =
    '<div style="font-weight:600;color:' + ORANGE + ';margin-bottom:10px">Fade-list moneyline — fade the pitcher, take opp ML</div>'
    + '<div style="display:flex;gap:20px;flex-wrap:wrap;align-items:center">'
    + stat('Record', (s.wins || 0) + '–' + (s.losses || 0))
    + stat('Units', (units >= 0 ? '+' : '') + units.toFixed(2) + 'u', unitColor)
    + stat('ROI', (roiPct >= 0 ? '+' : '') + roiPct.toFixed(1) + '%', unitColor)
    + stat('Risked', (s.staked || 0).toFixed(1) + 'u')
    + stat('Voids', (s.voids || 0))
    + '</div>';
  el.appendChild(banner);

  // ---- Today's plays ----
  const today = (data.today || []);
  const todayCard = document.createElement('div');
  todayCard.className = 'card';
  todayCard.style.cssText = 'margin-bottom:16px;padding:12px 16px';
  let th = '<div class="card-title" style="margin-bottom:8px">Today’s plays</div>';
  const live = today.filter(t => t.result === 'pending');
  const skips = today.filter(t => t.result === 'SKIP');
  if (!today.length) {
    th += '<div class="no-picks">No fade-list starters on today’s slate.</div>';
  } else {
    live.forEach(t => {
      th += '<div style="font-size:14px;color:#ddd;padding:2px 0">• Fade <b>' + esc(t.pitcher) + '</b> ('
        + esc(t.fadeTeam || '?') + ') → take <b>' + esc(t.betTeam || '?') + '</b> ML '
        + '<span style="color:' + ORANGE + '">' + fmtOdds(t.odds) + '</span></div>';
    });
    if (skips.length) {
      th += '<div style="font-size:12px;color:#888;margin-top:6px">Skipped (both starters fade): '
        + skips.map(t => esc(t.pitcher) + ' vs ' + esc(t.oppPitcher || '?')).join('  ·  ') + '</div>';
    }
  }
  todayCard.innerHTML = th;
  el.appendChild(todayCard);

  // ---- Season bet log ----
  const bets = (data.bets || []).slice().reverse(); // newest first
  const logCard = document.createElement('div');
  logCard.className = 'card card-games';
  logCard.style.cssText = 'padding:8px 4px';
  let rows = '';
  bets.forEach(b => {
    const settled = b.result === 'WIN' || b.result === 'LOSS';
    const dim = settled ? '' : 'opacity:.5;';
    const resColor = b.result === 'WIN' ? 'var(--green,#3fb950)'
      : (b.result === 'LOSS' ? 'var(--red,#f85149)' : '#888');
    const prof = settled ? ((b.profit >= 0 ? '+' : '') + b.profit.toFixed(2) + 'u') : '—';
    const profColor = !settled ? '#888' : (b.profit >= 0 ? 'var(--green,#3fb950)' : 'var(--red,#f85149)');
    const note = b.result === 'VOID' ? (' <span style="color:#777;font-size:10px">' + esc(b.reason || 'void') + '</span>')
      : (b.result === 'SKIP' ? ' <span style="color:#777;font-size:10px">mutual fade</span>' : '');
    rows += '<tr style="' + dim + '">'
      + '<td style="padding:4px 8px;color:#999">' + esc(b.date) + '</td>'
      + '<td style="padding:4px 8px">Fade ' + esc(b.pitcher) + ' (' + esc(b.fadeTeam || '?') + ')</td>'
      + '<td style="padding:4px 8px;font-weight:600">' + esc(b.betTeam || '?') + note + '</td>'
      + '<td style="padding:4px 8px;text-align:right;color:' + ORANGE + '">' + fmtOdds(b.odds) + '</td>'
      + '<td style="padding:4px 8px;text-align:center;color:' + resColor + ';font-weight:700">' + esc(b.result) + '</td>'
      + '<td style="padding:4px 8px;text-align:right;color:' + profColor + '">' + prof + '</td>'
      + '</tr>';
  });
  logCard.innerHTML = '<div class="card-title" style="padding:6px 8px">Season bet log (' + bets.length + ')</div>'
    + '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:13px">'
    + '<thead><tr style="color:#888;text-align:left;border-bottom:1px solid #333">'
    + '<th style="padding:4px 8px">Date</th><th style="padding:4px 8px">Fade</th>'
    + '<th style="padding:4px 8px">Bet</th><th style="padding:4px 8px;text-align:right">Odds</th>'
    + '<th style="padding:4px 8px;text-align:center">Result</th><th style="padding:4px 8px;text-align:right">P/L</th>'
    + '</tr></thead><tbody>' + rows + '</tbody></table></div>';
  el.appendChild(logCard);
}

// esc() is defined in main.js; guard for standalone use.
if (typeof esc === 'undefined') {
  window.esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
