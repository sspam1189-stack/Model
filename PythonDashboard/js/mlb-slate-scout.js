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

  // The flag-combo grid, rebuilt full-season by every daily run
  // (scripts/build_flag_combo_table.py). Same newest-wins fetch; a missing
  // table just hides its panel rather than breaking the tab.
  const grabTable = async (url) => {
    try {
      const r = await fetch(url + '?t=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) return null;
      const j = await r.json();
      return (j && Array.isArray(j.combos) && j.combos.length) ? j : null;
    } catch (e) { return null; }
  };
  const comboTable = (await Promise.all([
    grabTable('data/flag-combo-table.json'),
    grabTable('https://raw.githubusercontent.com/sspam1189-stack/Model/main/'
      + 'MLBstrikeouts/data/flag-combo-table.json'),
  ])).filter(Boolean)
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
  // RETIRED 2026-08-30 after one day at 1-3. MM_LIVE=false keeps the Play
  // column rendering as watch-only labels instead of bets; flip it back only
  // after the rule shadow-trades 15-20 plays (MLBstrikeouts/CLAUDE.md).
  const MM_LIVE = false;

  // Mismatch ML rule (carded 2026-08-29, retired 2026-08-30). Thresholds are
  // calibrated to the
  // L20 window the payload publishes -- they are NOT portable to L30, where
  // the same numbers select a broader, weaker set of games (see
  // MLBstrikeouts/scripts/backtest_mismatch.py).
  //   m <= -45  the arm outclasses the offense -> TAIL him, back his team
  //   m >= +55  the offense outclasses the arm -> FADE him, back the opponent
  const MM_TAIL = -45, MM_FADE = 55;

  // Mismatch is centred on zero: positive means the bats outclass the arm.
  const mismatchColor = (v) => (v == null ? DIM : (v > 0 ? RED : GREEN));

  // Rolling-window ladder (L30/L20/L15/L7). Value-suppressed thin cells
  // (<75 PA) render as their sample size so a 27-PA week can't print an 18.
  const wrcLadder = (wins) => {
    if (!wins) return '<span style="color:' + DIM + '">—</span>';
    return ['last30', 'last20', 'last15', 'last7'].map((k) => {
      const c = wins[k];
      if (!c) return '<span style="color:' + DIM + '">—</span>';
      if (c.wrcplus == null) return '<span style="color:' + DIM + ';font-size:10px">' + (c.pa || 0) + 'pa</span>';
      return '<span style="padding:0 3px;border-radius:2px;background:' + wrcColor(c.wrcplus) + '">' + c.wrcplus + '</span>';
    }).join('<span style="color:#30363d">·</span>');
  };

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

  // ---- Today's plays: the two under systems --------------------------------
  // 1) flagged under (CARD, live): a named data defect on either starter.
  //    Backtested 34-27 +6.7% ROI, +8.3pts over baseline (n=61) -- the one
  //    rule in the battery that clears the 25-play bar.
  // 2) form under (SHADOW, not bet): both arms in form -- sum of the two
  //    mismatch scores <= -40. Full-season as-of backtest 84-52 +17.2% ROI
  //    (perm p=0.005) but Aug ran 20-20 and 45% of its live-window plays
  //    were games the flags already card, so it shadows until the unflagged
  //    slice proves out. Entries say whether the game is also flagged.
  const DEFECTS = ['layoff', 'stale-window', 'opener', 'swingman'];
  const isDefect = (f) => DEFECTS.some((d) => f.startsWith(d));
  // Canonical combo naming, matching build_flag_combo_table.COMBO_ORDER: the
  // card requirement leads, then the rest alphabetically. One spelling per
  // combo across the chips, the grid and the ledger.
  const COMBO_ORDER = ['swingman', 'layoff', 'opener', 'stale-window'];
  const comboName = (kinds) => COMBO_ORDER.filter((k) => kinds.includes(k)).join('+');
  // The defect kinds present across a game's flagged sides, canonically named.
  const gameCombo = (defSides) => comboName(COMBO_ORDER.filter((d) =>
    defSides.some((x) => (x.flags || []).some((f) => f.startsWith(d)))));
  // A single pitcher's defect flags in the same order, so the Why column reads
  // "swingman-26g, opener, stale-window-108d" everywhere, never payload order.
  const canonFlags = (flags) => COMBO_ORDER
    .map((d) => (flags || []).filter((f) => f.startsWith(d)))
    .flat().join(', ');
  const FORM_UNDER_AT = -40;
  const ctTime = (iso) => {
    try {
      return new Date(iso).toLocaleTimeString('en-US',
        { timeZone: 'America/Chicago', hour: 'numeric', minute: '2-digit' });
    } catch (e) { return ''; }
  };
  const mlStr = (v) => (v == null ? '' : (v > 0 ? '+' + v : String(v)));
  const underPlays = [];
  for (const s of (data.slate || [])) {
    const sides = ['away', 'home'].map((k) => (s.sides || {})[k] || {});
    const defSides = sides.filter((x) => (x.flags || []).some(isDefect));
    const ms = sides.map((x) => x.mismatch);
    const msum = (ms[0] != null && ms[1] != null) ? ms[0] + ms[1] : null;
    if (defSides.length) {
      // 2026-09-02 amendment: the card requires a SWINGMAN flag. The 9/1
      // decomposition (200 games) put swingman-present at 27-5 +61.6%
      // (stable both walk-forward halves, perm p<0.0003) and rust-only
      // (layoff/stale-window/opener with no swingman) at 9-24 -47.7% --
      // genuine absence means real rust, and those games score. Rust-only
      // qualifiers render as SHADOW. Date-gated so slates before 9/2 show
      // the rule as it was carded then.
      const why = defSides.map((x) => esc(x.pitcher || '?') + ' '
        + canonFlags(x.flags)).join(' · ');
      const combo = gameCombo(defSides);
      const amended = (data.date || '') >= '2026-09-02';
      const hasSwing = combo.startsWith('swingman');
      if (!amended || hasSwing) {
        // Name the combo in the chip itself, spelled exactly as the combo
        // grid and the ledger spell it; the Why column carries the
        // per-pitcher detail (who, and the exact flag with its count).
        underPlays.push({ s, kind: 'card', side: 'U',
          rule: 'CARD · ' + combo, why });
      } else {
        // Rust-only is DEAD both ways per the 2,064-game as-of replay
        // (9/1): under -1.7%, over -7.1% (perm p=0.57). The cache-window
        // 24-9 over that briefly earned a shadow was a two-week mirage.
        underPlays.push({ s, kind: 'dead', side: 'U',
          rule: 'NO PLAY · ' + combo,
          why: why + ' · rust-only measured dead both ways (full season), not bet' });
      }
    }
    if (msum != null && msum <= FORM_UNDER_AT) {
      underPlays.push({ s, kind: 'shadow', side: 'U', rule: 'SHADOW · form-under',
        why: 'm_sum ' + msum.toFixed(1)
          + (defSides.length ? ' · also flagged' : ' · unflagged') });
    }
    // The over sides exist only so the panel answers the question; neither is
    // bet. Flags have no over rule at all (a data defect backtests as an
    // under edge, not an over). Form over (m_sum >= +40) was backtested and
    // MEASURED NEGATIVE: -0.6% at +40, -8.5% at +60, vs blind-over -6.4% --
    // the market overprices hot bats. Shown dimmed as no-plays.
    if (msum != null && msum >= -FORM_UNDER_AT) {
      underPlays.push({ s, kind: 'dead', side: 'O', rule: 'NO PLAY · form-over',
        why: 'm_sum +' + msum.toFixed(1) + ' · over side measured -0.6% ROI, not bet' });
    }
    // scout-ml-both-halves-aligned at its own 75-PA floor (everything else
    // stays at 150). Emitted by the builder; shadow until 25 graded plays.
    if (s.aligned_ml) {
      const am = s.aligned_ml;
      underPlays.push({ s, kind: 'shadow', side: 'ML', ml: am,
        rule: 'SHADOW · aligned ML',
        why: 'away ' + (am.away_offense || []).join('/') + ' vs home '
          + (am.home_offense || []).join('/') + ' @75pa' });
    }
  }
  const playsCard = document.createElement('div');
  playsCard.className = 'card card-games';
  let phtml = '<div class="card-title" style="padding:6px 8px">Flagged &amp; form O/U — today\'s plays '
    + '<span style="color:' + DIM + ';font-weight:400;font-size:11px">'
    + '(CARD is bet · SHADOW is tracked, not bet · NO PLAY is a measured dead side. '
    + 'Flags have no over rule: a defect is an under edge only.)</span></div>';
  if (!underPlays.length) {
    phtml += '<div style="padding:8px 10px;font-size:12px;color:' + DIM
      + '">No qualifying plays on this slate.</div>';
  } else {
    phtml += '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:12px">'
      + '<thead><tr style="text-align:left;color:' + DIM + ';border-bottom:1px solid #30363d">'
      + '<th style="padding:4px 6px">CT</th><th>Game</th><th>Play</th><th>Rule</th><th>Why</th>'
      + '</tr></thead><tbody>';
    for (const p of underPlays) {
      const chip = p.kind === 'card'
        ? 'background:rgba(63,185,80,.18);color:#3fb950'
        : p.kind === 'shadow'
          ? 'background:rgba(139,148,158,.14);color:' + DIM
          : 'background:rgba(248,81,73,.12);color:#8b949e';
      const playCell = p.side === 'ML'
        ? esc(p.ml.pick) + ' ML <span style="color:' + DIM + ';font-weight:400">'
          + mlStr(p.ml.ml) + '</span>'
        : p.side + (p.s.total == null ? '?' : p.s.total)
          + ' <span style="color:' + DIM + ';font-weight:400">'
          + mlStr(p.side === 'U' ? p.s.under_ml : p.s.over_ml) + '</span>';
      phtml += '<tr style="border-top:1px solid #161b22'
        + (p.kind === 'dead' ? ';opacity:.55' : '') + '">'
        + '<td style="padding:3px 6px;color:' + DIM + '">' + ctTime(p.s.commence) + '</td>'
        + '<td style="padding:3px 6px">' + esc(p.s.matchup) + '</td>'
        + '<td style="padding:3px 6px;font-weight:600;white-space:nowrap">' + playCell + '</td>'
        + '<td><span style="display:inline-block;padding:1px 6px;border-radius:3px;'
        + 'font-weight:600;white-space:nowrap;' + chip + '">' + p.rule + '</span></td>'
        + '<td style="color:' + DIM + ';font-size:11px">' + p.why + '</td>'
        + '</tr>';
    }
    phtml += '</tbody></table></div>';
  }
  playsCard.innerHTML = phtml;
  el.appendChild(playsCard);

  // ---- Flag-combo performance grid -----------------------------------------
  // Rebuilt full-season on every daily run. This panel exists because the
  // rust-only OVER rule cleared a 33-game backtest, a walk-forward split and
  // a permutation test on the 15-slate snapshot window, and died the same day
  // the whole season was replayed. Numbers here are never remembered, always
  // recomputed.
  if (comboTable) {
    const roiCell = (s, live) => {
      if (!s) return '<td style="color:' + DIM + '">—</td>';
      const strong = live && Math.abs(s.roi) >= 10 && s.n >= 25;
      const col = s.roi > 0 ? '#3fb950' : s.roi < 0 ? '#f85149' : DIM;
      return '<td style="white-space:nowrap;padding:3px 6px">'
        + '<span style="color:' + DIM + '">' + s.w + '-' + s.l + '</span> '
        + '<span style="color:' + col + (strong ? ';font-weight:600' : '') + '">'
        + (s.roi > 0 ? '+' : '') + s.roi.toFixed(1) + '%</span></td>';
    };
    const tbl = document.createElement('div');
    tbl.className = 'card card-games';
    const b = comboTable.baselines || {};
    let t = '<div class="card-title" style="padding:6px 8px">Flag combos — full season '
      + '<span style="color:' + DIM + ';font-weight:400;font-size:11px">('
      + esc(comboTable.span?.from || '') + '..' + esc(comboTable.span?.to || '') + ' · '
      + (comboTable.games?.flagged || 0) + ' flagged of ' + (comboTable.games?.gradeable || 0)
      + ' · baseline U ' + (b.under ? b.under.roi.toFixed(1) : '?') + '% / O '
      + (b.over ? b.over.roi.toFixed(1) : '?') + '% · rebuilt each run)</span></div>'
      + '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:12px">'
      + '<thead><tr style="text-align:left;color:' + DIM + ';border-bottom:1px solid #30363d">'
      + '<th style="padding:4px 6px">Combo</th><th>n</th><th>UNDER</th><th>OVER</th>'
      + '</tr></thead><tbody>';
    // Group headers: card side by how many defects stack (the dose-response
    // reads top to bottom), then the rust side once.
    let lastGroup = null;
    for (const c of comboTable.combos) {
      const n = (c.kinds || []).length;
      const group = c.carded ? 'card:' + n : 'rust';
      if (group !== lastGroup) {
        lastGroup = group;
        const label = c.carded
          ? (n === 1 ? 'swingman alone'
            : n === 2 ? 'swingman + 1 other defect'
              : n === 3 ? 'swingman + 2 others' : 'all four defects')
          : 'rust side — no swingman, not carded (measured dead both ways)';
        t += '<tr><td colspan="4" style="padding:6px 6px 2px;color:' + DIM
          + ';font-size:11px;border-top:1px solid #30363d">' + label + '</td></tr>';
      }
      // Below 25 plays the harness refuses to call anything an edge; dim those.
      const thin = (c.under?.n || 0) < 25;
      t += '<tr style="border-top:1px solid #161b22' + (thin ? ';opacity:.6' : '') + '">'
        + '<td style="padding:3px 6px">'
        + (c.carded ? '<span style="color:#3fb950">●</span> ' : '<span style="color:'
          + DIM + '">○</span> ')
        + esc(c.combo) + (thin ? ' <span style="color:' + DIM
          + ';font-size:10px">thin</span>' : '') + '</td>'
        + '<td style="color:' + DIM + '">' + (c.under?.n ?? 0) + '</td>'
        + roiCell(c.under, c.carded) + roiCell(c.over, false) + '</tr>';
    }
    t += '</tbody></table></div>'
      + '<div style="padding:6px 8px;font-size:11px;color:' + DIM + ';line-height:1.5">'
      + '● = on the card (requires <b>' + esc(comboTable.card_requires || 'swingman')
      + '</b>). Combo rows count each game once under its exact flag set. '
      + 'Rows under 25 plays are dimmed — the harness never calls those an edge. '
      + 'Flags are replayed as-of each game date from the pitcher logs, graded at '
      + 'the real payload prices.</div>';
    tbl.innerHTML = t;
    el.appendChild(tbl);
  }

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
    + ' (' + data.slate.length + ' games) · wRC+ ' + esc(data.wrc_primary_window || 'season')
    + ' vs ' + esc(data.wrc_role)
    + ' through ' + esc(data.wrc_through) + staleChip + '</div>';

  html += '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:12px">'
    + '<thead><tr style="text-align:left;color:' + DIM + ';border-bottom:1px solid #30363d">'
    + '<th style="padding:4px 6px">Team</th><th>Side</th>'
    + '<th>Starter</th>'
    + '<th title="Opposing offense wRC+ against this starter\'s hand, primary window">Opp wRC+</th>'
    + '<th title="Rolling wRC+ ladder vs this hand: L30 / L20 / L15 / L7. One window '
    + 'alone reverses reads; the ladder shows the trend. Thin cells (<75 PA) show PA only.">L30/20/15/7</th>'
    + '<th title="Season ERA / K% / BB%">Season</th>'
    + '<th title="Last 5 STARTS, with change from season. Relief outings are '
    + 'excluded — when an arm has any, they are shown on the row beneath.">Last 5</th>'
    + '<th title="Last 3 starts ERA">L3</th>'
    + '<th title="Positive = the offense outclasses the arm">Mismatch</th>'
    + '</tr></thead><tbody>';

  for (const g of data.slate) {
    html += '<tr style="border-top:1px solid #21262d"><td colspan="9" style="padding:6px 6px 2px;font-weight:600">'
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
        + '<td style="padding:3px 6px;white-space:nowrap">' + wrcLadder(s.opp_wrc_windows) + '</td>'
        + '<td style="color:' + DIM + '">' + num(season.era) + ' · '
        + num(season.k_pct, 1) + '% · ' + num(season.bb_pct, 1) + '%</td>'
        + '<td>' + num(recent.era)
        + '<span style="color:' + (t.era > 0 ? RED : GREEN) + ';font-size:10px"> ' + signed(t.era, 2) + '</span>'
        + ' · ' + num(recent.k_pct, 1) + '%'
        + '<span style="color:' + (t.k_pct > 0 ? GREEN : RED) + ';font-size:10px"> ' + signed(t.k_pct) + '</span></td>'
        + '<td style="color:' + DIM + '">' + num(hot.era) + '</td>'
        + '<td style="color:' + mismatchColor(s.mismatch) + ';font-weight:600">' + signed(s.mismatch) + '</td>'
        + '</tr>';

      // Team bullpen sub-row: the pen that inherits this starter's game, as
      // calendar-day windows of every relief line in the game logs, with the
      // league ERA rank for that window. Totals lean on 3-4 relief innings a
      // night, and the ladder above says nothing about who throws them.
      const pen = s.pen;
      if (pen && (pen.last30 || pen.last7)) {
        const p30 = pen.last30 || {}, p7 = pen.last7 || {};
        const l7Hot = (p7.era != null && p30.era != null)
          ? (p7.era < p30.era ? GREEN : RED) : DIM;
        html += '<tr style="border-top:0"><td colspan="2"></td>'
          + '<td colspan="7" style="padding:0 6px 4px;color:' + DIM + ';font-size:11px">'
          + esc(s.team) + ' pen: L30 ' + num(p30.era) + ' ERA'
          + (p30.rank != null ? ' (#' + p30.rank + ')' : '')
          + ' · ' + num(p30.whip) + ' WHIP · ' + num(p30.hr9) + ' HR9'
          + ' → L7 <span style="color:' + l7Hot + '">' + num(p7.era) + '</span> ERA'
          + (p7.ip != null ? ' (' + num(p7.ip, 1) + ' IP)' : '')
          + '</td></tr>';
      }

      // Swingman sub-row. The Last 5 column counts STARTS only, so for an arm
      // that has been in the bullpen it describes a fraction of his season —
      // Mlodzinski read 6.3% K there on 2026-08-17 while throwing 17.4% in
      // relief across the same window. Only rendered when there is relief work
      // to show; a pure starter's row is unchanged.
      const relief = f.relief, recentAll = f.recent_all;
      if (relief && relief.g) {
        html += '<tr style="border-top:0"><td colspan="2"></td>'
          + '<td colspan="7" style="padding:0 6px 4px;color:' + DIM + ';font-size:11px">'
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

  // Returns the carded play for a row, or null. `team` is the starter's own
  // team, `faces` the offense he is up against -- a FADE backs the latter.
  function mismatchPlay(m, team, faces) {
    if (m == null) return null;
    const dim = { bg: 'rgba(139,148,158,.14)', fg: DIM };
    if (m <= MM_TAIL) return MM_LIVE
      ? { act: 'TAIL', pick: team, bg: 'rgba(63,185,80,.18)', fg: '#3fb950' }
      : { act: 'tail', pick: team, ...dim };
    if (m >= MM_FADE) return MM_LIVE
      ? { act: 'FADE', pick: faces, bg: 'rgba(248,81,73,.18)', fg: '#f85149' }
      : { act: 'fade', pick: faces, ...dim };
    return null;
  }

  // ---- Mismatch ranking ----------------------------------------------------
  const ranked = document.createElement('div');
  ranked.className = 'card card-games';
  let rhtml = '<div class="card-title" style="padding:6px 8px">Mismatch — widest first '
    + '<span style="color:' + DIM + ';font-weight:400;font-size:11px">'
    + '(positive = offense outclasses the arm)</span></div>'
    + '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:12px">'
    + '<thead><tr style="text-align:left;color:' + DIM + ';border-bottom:1px solid #30363d">'
    + '<th style="padding:4px 6px">Mismatch</th><th>Starter</th><th>Team</th>'
    + '<th>Faces</th><th>Opp wRC+</th>'
    + '<th title="mismatch-ML, RETIRED 2026-08-30 at 1-3: tail at m<=-45, '
    + 'fade at m>=+55 (L20 window). Labels are watch-only.">Play '
    + '<span style="color:' + DIM + ';font-weight:400;font-size:10px">'
    + '(mismatch-ML · retired)</span></th>'
    + '<th>Flags</th></tr></thead><tbody>';
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
      + '<td>' + (function () {
          const p = mismatchPlay(r.mismatch, r.team, r.opponent_offense);
          if (!p) return '<span style="color:' + DIM + '">—</span>';
          return '<span style="display:inline-block;padding:1px 6px;border-radius:3px;'
            + 'font-weight:600;white-space:nowrap;background:' + p.bg + ';color:' + p.fg
            + '">' + p.act + ' ' + esc(p.pick) + ' ML</span>';
        })() + '</td>'
      + '<td>' + (r.flags || []).map(flagChip).join('') + '</td>'
      + '</tr>';
  }
  rhtml += '</tbody></table></div>';
  ranked.innerHTML = rhtml;
  // Order at the top of the tab: banner, then the plays panel (the actionable
  // part), then the mismatch ranking, then the per-game table.
  el.insertBefore(ranked, playsCard.nextSibling);
}
