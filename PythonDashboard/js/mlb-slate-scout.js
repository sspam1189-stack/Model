// PythonDashboard/js/mlb-slate-scout.js
// Renderer for the "MLB Slate Scout" tab. Reads mlb-slate-scout.json
// (build_slate_scout.py): tonight's games with each starter's season /
// last-5 / last-3 form, the opposing offense's wRC+ against his hand, and the
// data flags that say when those rates cannot be read at face value.
//
// This tab shows no picks and no records, deliberately. The mismatch
// score it sorts by has real baseball signal and no market edge — backtested
// over 1818 games it returns -1.8% ROI, because the closing total already
// prices what it knows. It earns its place by surfacing WHERE the mismatches
// are and, more usefully, where the inputs are untrustworthy: an arm back from
// a two-month layoff still shows a full five-start line describing a different
// month, and a swingman's five-start line silently drops every inning he threw
// out of the bullpen. The advisory banner says so on the tab rather than in a
// doc nobody opens.

async function renderMLBSlateScout() {
  const el = document.getElementById('content');
  el.innerHTML = '<div class="loading"><div class="spinner"></div><br>Loading slate scout...</div>';

  const local = 'data/mlb-slate-scout.json';
  const remote = 'https://raw.githubusercontent.com/sspam1189-stack/Model/main/MLBstrikeouts/data/mlb-slate-scout.json';

  // Fetch both sources and keep whichever was generated last, rather than
  // taking the first one that answers. The local copy is republished by a
  // hand-edited per-file whitelist in mlb-run-daily.yml; when a new output is
  // missed there it freezes silently, and a first-wins loop then serves
  // days-old form as if it were tonight's. Newest-wins heals from the
  // canonical MLBstrikeouts copy on its own.
  const grab = async (url) => {
    try {
      const r = await fetch(url + '?t=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) return null;
      const j = await r.json();
      return (j && Array.isArray(j.slate) && j.slate.length) ? j : null;
    } catch (e) { return null; }
  };
  // `generated` is ISO-8601 UTC, so a string compare is a date compare.
  const data = (await Promise.all([grab(local), grab(remote)]))
    .filter(Boolean)
    .sort((a, b) => String(b.generated || '').localeCompare(String(a.generated || '')))[0] || null;

  el.textContent = '';
  if (!data || !Array.isArray(data.slate) || !data.slate.length) {
    const c = document.createElement('div');
    c.className = 'card card-games';
    c.innerHTML = '<div class="card-title">MLB Slate Scout</div>'
      + '<div class="no-picks" style="padding:20px 0 6px">No slate published yet today.</div>';
    el.appendChild(c);
    return;
  }

  const runEl = document.getElementById('last-run-info');
  if (runEl && data.generated) {
    const d = new Date(data.generated);
    runEl.textContent = 'Last run (CT) — MLB Slate Scout: '
      + d.toLocaleString('en-US', { timeZone: 'America/Chicago', month: '2-digit', day: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: true });
  }

  const GREEN = 'var(--green,#3fb950)', RED = 'var(--red,#f85149)', DIM = '#8b949e';
  const esc = (s) => (s == null ? '' : String(s).replace(/[&<>"]/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])));
  const num = (v, d = 2) => (v == null ? '—' : Number(v).toFixed(d));
  const odds = (o) => (o == null ? '—' : (o > 0 ? '+' + o : '' + o));
  const signed = (v, d = 1) => (v == null ? '' : (v > 0 ? '+' : '') + Number(v).toFixed(d));

  // wRC+ colouring: 100 is league average, so diverge from there rather than
  // from zero. Capped at ±25 so one extreme club doesn't flatten the rest.
  const wrcColor = (v) => {
    if (v == null) return DIM;
    const t = Math.max(-25, Math.min(25, v - 100)) / 25;
    return t > 0 ? `rgba(248,81,73,${0.25 + 0.55 * t})`
                 : `rgba(63,185,80,${0.25 + 0.55 * -t})`;
  };
  // Mismatch is centred on zero: positive means the bats outclass the arm.
  const mismatchColor = (v) => (v == null ? DIM : (v > 0 ? RED : GREEN));

  const flagChip = (f) =>
    `<span style="display:inline-block;padding:1px 5px;margin-left:4px;border-radius:3px;`
    + `background:rgba(210,153,34,.18);color:#d29922;font-size:10px;white-space:nowrap">${esc(f)}</span>`;

  // ---- Advisory banner -----------------------------------------------------
  const banner = document.createElement('div');
  banner.className = 'card';
  banner.style.cssText = 'border-left:3px solid #d29922;padding:10px 12px';
  banner.innerHTML =
    '<div style="font-weight:600;color:#d29922;font-size:12px;margin-bottom:4px">'
    + 'Scouting view — not a betting model</div>'
    + '<div style="font-size:11px;color:' + DIM + ';line-height:1.5">'
    + (data.notes || []).map(esc).join('<br>') + '</div>';
  el.appendChild(banner);

  // ---- Per-game cards ------------------------------------------------------
  const games = document.createElement('div');
  games.className = 'card card-games';
  // Say so when the published slate is not the current date. Compared in CT
  // because that is the calendar the pipeline runs on; before the ~08:00 CT
  // run this legitimately still reads yesterday, which is worth showing rather
  // than papering over.
  const todayCT = new Date().toLocaleDateString('en-CA', { timeZone: 'America/Chicago' });
  const staleChip = (data.date && data.date !== todayCT)
    ? '<span style="margin-left:8px;padding:1px 6px;border-radius:3px;'
      + 'background:rgba(210,153,34,.18);color:#d29922;font-size:10px;font-weight:600">'
      + 'showing ' + esc(data.date) + ' · today (CT) is ' + esc(todayCT) + '</span>'
    : '';

  let html = '<div class="card-title" style="padding:6px 8px">Slate — ' + esc(data.date)
    + ' (' + data.slate.length + ' games) · wRC+ vs ' + esc(data.wrc_role)
    + ' through ' + esc(data.wrc_through) + staleChip + '</div>';

  html += '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:12px">'
    + '<thead><tr style="text-align:left;color:' + DIM + ';border-bottom:1px solid #30363d">'
    + '<th style="padding:4px 6px">Team</th><th>Side</th>'
    + '<th>Starter</th><th title="Opposing offense wRC+ against this starter\'s hand">Opp wRC+</th>'
    + '<th title="Season ERA / K% / BB%">Season</th>'
    + '<th title="Last 5 STARTS, with change from season. Relief outings are '
    + 'excluded — when an arm has any, they are shown on the row beneath.">Last 5</th>'
    + '<th title="Last 3 starts ERA">L3</th>'
    + '<th title="Positive = the offense outclasses the arm">Mismatch</th>'
    + '</tr></thead><tbody>';

  for (const g of data.slate) {
    html += '<tr style="border-top:1px solid #21262d"><td colspan="8" style="padding:6px 6px 2px;font-weight:600">'
      + esc(g.matchup)
      + '<span style="color:' + DIM + ';font-weight:400;margin-left:8px">'
      + odds(g.away_ml) + ' / ' + odds(g.home_ml) + ' · O/U ' + num(g.total, 1)
      + '</span></td></tr>';

    for (const side of ['away', 'home']) {
      const s = g.sides[side];
      if (!s) continue;
      const f = s.form || {};
      const season = f.season || {}, recent = f.recent || {}, hot = f.hot || {};
      const t = s.trend || {};
      const flags = (s.flags || []).map(flagChip).join('');
      html += '<tr style="border-top:1px solid #161b22">'
        + '<td style="padding:3px 6px;color:' + DIM + '">' + esc(s.team) + '</td>'
        + '<td style="color:' + DIM + '">' + side + '</td>'
        + '<td style="padding:3px 6px">' + esc(s.pitcher || 'TBD')
        + '<span style="color:' + DIM + '"> (' + esc(s.hand || '?') + ')</span>' + flags + '</td>'
        + '<td style="padding:3px 6px"><span style="padding:1px 6px;border-radius:3px;background:'
        + wrcColor(s.opp_wrc_vs_hand) + '">' + (s.opp_wrc_vs_hand == null ? '—' : s.opp_wrc_vs_hand)
        + '</span><span style="color:' + DIM + ';font-size:10px"> vs ' + esc(s.opponent_offense) + '</span></td>'
        + '<td style="color:' + DIM + '">' + num(season.era) + ' · '
        + num(season.k_pct, 1) + '% · ' + num(season.bb_pct, 1) + '%</td>'
        + '<td>' + num(recent.era)
        + '<span style="color:' + (t.era > 0 ? RED : GREEN) + ';font-size:10px"> ' + signed(t.era, 2) + '</span>'
        + ' · ' + num(recent.k_pct, 1) + '%'
        + '<span style="color:' + (t.k_pct > 0 ? GREEN : RED) + ';font-size:10px"> ' + signed(t.k_pct) + '</span></td>'
        + '<td style="color:' + DIM + '">' + num(hot.era) + '</td>'
        + '<td style="color:' + mismatchColor(s.mismatch) + ';font-weight:600">' + signed(s.mismatch) + '</td>'
        + '</tr>';

      // Swingman sub-row. The Last 5 column counts STARTS only, so for an arm
      // that has been in the bullpen it describes a fraction of his season —
      // Mlodzinski read 6.3% K there on 2026-08-17 while throwing 17.4% in
      // relief across the same window. Only rendered when there is relief work
      // to show; a pure starter's row is unchanged.
      const relief = f.relief, recentAll = f.recent_all;
      if (relief && relief.g) {
        html += '<tr style="border-top:0"><td colspan="2"></td>'
          + '<td colspan="6" style="padding:0 6px 4px;color:' + DIM + ';font-size:11px">'
          + '+ ' + relief.g + ' relief G in the same window: '
          + num(relief.era) + ' ERA · ' + num(relief.k_pct, 1) + '% K · '
          + num(relief.ip, 1) + ' IP'
          + '<span style="color:#484f58"> (' + esc(relief.from || '') + ' → '
          + esc(relief.to || '') + ')</span>'
          + (recentAll && recentAll.g
              ? ' · last ' + recentAll.g + ' outings any role: ' + num(recentAll.era)
                + ' ERA · ' + num(recentAll.k_pct, 1) + '% K'
              : '')
          + '</td></tr>';
      }
    }
  }
  html += '</tbody></table></div>';
  games.innerHTML = html;
  el.appendChild(games);

  // ---- Mismatch ranking ----------------------------------------------------
  const ranked = document.createElement('div');
  ranked.className = 'card card-games';
  let rhtml = '<div class="card-title" style="padding:6px 8px">Mismatch — widest first '
    + '<span style="color:' + DIM + ';font-weight:400;font-size:11px">'
    + '(positive = offense outclasses the arm)</span></div>'
    + '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:12px">'
    + '<thead><tr style="text-align:left;color:' + DIM + ';border-bottom:1px solid #30363d">'
    + '<th style="padding:4px 6px">Mismatch</th><th>Starter</th><th>Team</th>'
    + '<th>Faces</th><th>Opp wRC+</th><th>Flags</th></tr></thead><tbody>';
  for (const r of (data.ranked_mismatch || [])) {
    rhtml += '<tr style="border-top:1px solid #161b22">'
      + '<td style="padding:3px 6px;color:' + mismatchColor(r.mismatch) + ';font-weight:600">'
      + signed(r.mismatch) + '</td>'
      + '<td style="padding:3px 6px">' + esc(r.pitcher) + ' <span style="color:' + DIM + '">('
      + esc(r.hand) + ')</span></td>'
      + '<td style="color:' + DIM + '">' + esc(r.team) + '</td>'
      + '<td style="color:' + DIM + '">' + esc(r.opponent_offense) + '</td>'
      + '<td><span style="padding:1px 6px;border-radius:3px;background:' + wrcColor(r.opp_wrc_vs_hand)
      + '">' + (r.opp_wrc_vs_hand == null ? '—' : r.opp_wrc_vs_hand) + '</span></td>'
      + '<td>' + (r.flags || []).map(flagChip).join('') + '</td>'
      + '</tr>';
  }
  rhtml += '</tbody></table></div>';
  ranked.innerHTML = rhtml;
  el.appendChild(ranked);
}
