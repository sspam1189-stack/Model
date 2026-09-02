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
  const grabTableAny = async (url) => {
    try {
      const r = await fetch(url + '?t=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) return null;
      const j = await r.json();
      return (j && j.splits) ? j : null;
    } catch (e) { return null; }
  };
  const grabTable = async (url) => {
    try {
      const r = await fetch(url + '?t=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) return null;
      const j = await r.json();
      return (j && Array.isArray(j.combos) && j.combos.length) ? j : null;
    } catch (e) { return null; }
  };
  const grabStatus = async (url) => {
    try {
      const r = await fetch(url + '?t=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) return null;
      const j = await r.json();
      return (j && j.rules) ? j : null;
    } catch (e) { return null; }
  };
  // Single source of truth for card/shadow (MLBstrikeouts/scripts/rule_status.py).
  // The daily logger imports the same table, so the ledger and this tab cannot
  // disagree the way they did on 2026-09-01.
  const ruleStatus = (await Promise.all([
    grabStatus('data/rule-status.json'),
    grabStatus('https://raw.githubusercontent.com/sspam1189-stack/Model/main/'
      + 'MLBstrikeouts/data/rule-status.json'),
  ])).filter(Boolean)
    .sort((a, b) => String(b.generated || '').localeCompare(String(a.generated || '')))[0] || null;
  const isCard = (rule, fallback) => (ruleStatus && ruleStatus.rules[rule])
    ? ruleStatus.rules[rule].status === 'card'
    : fallback;

  const msumTable = (await Promise.all([
    grabTableAny('data/msum-ml-table.json'),
    grabTableAny('https://raw.githubusercontent.com/sspam1189-stack/Model/main/'
      + 'MLBstrikeouts/data/msum-ml-table.json'),
  ])).filter(Boolean)
    .sort((a, b) => String(b.generated || '').localeCompare(String(a.generated || '')))[0] || null;

  // The non-scout systems (MLBstrikeouts/scripts/allml_systems.py): eight
  // rules read off mlb-all-ml.json alone, with no input from the mismatch
  // model. Their own panel, because an agreement between one of these and a
  // scout rule is a second opinion rather than the same inputs twice.
  const grabSystems = async (url) => {
    try {
      const r = await fetch(url + '?t=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) return null;
      const j = await r.json();
      return (j && Array.isArray(j.systems) && j.systems.length) ? j : null;
    } catch (e) { return null; }
  };
  const sysTable = (await Promise.all([
    grabSystems('data/allml-systems-table.json'),
    grabSystems('https://raw.githubusercontent.com/sspam1189-stack/Model/main/'
      + 'MLBstrikeouts/data/allml-systems-table.json'),
  ])).filter(Boolean)
    .sort((a, b) => String(b.generated || '').localeCompare(String(a.generated || '')))[0] || null;

  // Recent logged plays and how they graded (scripts/build_plays_feed.py), a
  // projection of the ledger the auto-grader settles. Tonight comes from the
  // live payloads; anything earlier is read from here.
  const grabFeed = async (url) => {
    try {
      const r = await fetch(url + '?t=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) return null;
      const j = await r.json();
      return (j && Array.isArray(j.days) && j.days.length) ? j : null;
    } catch (e) { return null; }
  };
  const playsFeed = (await Promise.all([
    grabFeed('data/plays-feed.json'),
    grabFeed('https://raw.githubusercontent.com/sspam1189-stack/Model/main/'
      + 'MLBstrikeouts/data/plays-feed.json'),
  ])).filter(Boolean)
    .sort((a, b) => String(b.generated || '').localeCompare(String(a.generated || '')))[0] || null;

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
  // Carded 8/29 without a shadow period, pulled 8/30 at 1-3, REVIVED AS
  // SHADOW 2026-09-01 -- tracked by scripts/mismatch_shadow.py --log, never
  // bet. MM_LIVE=false keeps the Play
  // column rendering as watch-only labels instead of bets; flip it back only
  // after the rule shadow-trades 15-20 plays (MLBstrikeouts/CLAUDE.md).
  const MM_LIVE = false;   // still not a card play
  const MM_SHADOW = true;  // revived as SHADOW 2026-09-01 (user)

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
  // CARDED 2026-09-01 (user). form-under: m_sum <= -40 -> under, 84-52 +17.2%
  // full-season as-of, perm p=0.005, coherent bands, both walk-forward halves
  // positive -- the strongest number in the repo's scout work. aligned-ML:
  // carded on the user's call at n=4 lifetime, on a ladder the backtest
  // measured inert for runs (cold offenses 4.54 r/g, hot 4.52); it has no
  // statistical case, only a structural one.
  // Fallbacks only -- rule-status.json wins when it loads.
  const FORM_UNDER_LIVE = isCard('form-under', true);
  const ALIGNED_ML_LIVE = isCard('aligned-ml', true);
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
      // Flag count in the chip: "CARD · 2 · swingman+stale-window". The count
      // is the tier the combo grid groups by, so a row can be placed at a
      // glance without counting plus signs.
      const nFlags = combo ? combo.split('+').length : 0;
      // PER-COMBO VERDICTS, live from 2026-09-01 (user). The bet side comes
      // from the daily-rebuilt table's `verdicts` map -- swingman alone and
      // swingman+opener and the thin stacks play the under, swingman+
      // stale-window plays the OVER, swingman+layoff / swingman+layoff+opener
      // / opener / stale-window are no plays. Falls back to the old
      // swingman-present rule if the table is unavailable.
      const verdicts = comboTable && comboTable.verdicts;
      const side = verdicts
        ? (Object.prototype.hasOwnProperty.call(verdicts, combo)
          ? verdicts[combo]
          : (combo.split('+').includes('swingman') ? 'under' : null))
        : (combo.split('+').includes('swingman') ? 'under' : null);
      const label = nFlags + ' · ' + combo;
      if (side === 'under' || side === 'over') {
        underPlays.push({ s, kind: 'card', side: side === 'over' ? 'O' : 'U',
          rule: 'Flag Plays · ' + label, why });
      } else {
        underPlays.push({ s, kind: 'dead', side: 'U',
          rule: 'Flag Plays · ' + label,
          why: why + ' · this configuration measured flat or negative, not bet' });
      }
    }
    if (msum != null && msum <= FORM_UNDER_AT) {
      underPlays.push({ s, kind: FORM_UNDER_LIVE ? 'card' : 'shadow', side: 'U',
        rule: 'Form under',
        why: 'm_sum ' + msum.toFixed(1)
          + (defSides.length ? ' · also flagged' : ' · unflagged') });
    }
    // The over sides exist only so the panel answers the question; neither is
    // bet. Flags have no over rule at all (a data defect backtests as an
    // under edge, not an over). Form over (m_sum >= +40) was backtested and
    // MEASURED NEGATIVE: -0.6% at +40, -8.5% at +60, vs blind-over -6.4% --
    // the market overprices hot bats. Shown dimmed as no-plays.
    if (msum != null && msum >= -FORM_UNDER_AT) {
      underPlays.push({ s, kind: 'dead', side: 'O', rule: 'Form over',
        why: 'm_sum +' + msum.toFixed(1) + ' · over side measured -0.6% ROI, not bet' });
    }
    // scout-ml-both-halves-aligned at its own 75-PA floor (everything else
    // stays at 150). Emitted by the builder; shadow until 25 graded plays.
    if (s.aligned_ml) {
      const am = s.aligned_ml;
      underPlays.push({ s, kind: ALIGNED_ML_LIVE ? 'card' : 'shadow', side: 'ML',
        ml: am,
        rule: 'Aligned ML',
        why: 'away ' + (am.away_offense || []).join('/') + ' vs home '
          + (am.home_offense || []).join('/') + ' @75pa' });
    }
  }
  // ---- date filter shared by both plays panels ----------------------------
  // Each panel keeps its OWN selector so one can stay on tonight while the
  // other shows how yesterday finished. Tonight is rendered from the live
  // payloads exactly as before; past dates come from plays-feed.json, whose
  // results and profit are the ledger's own -- nothing is regraded here.
  const feedDays = (playsFeed && playsFeed.days) ? playsFeed.days : [];
  const slateDate = data.date || (feedDays[0] && feedDays[0].date) || '';
  const feedFor = (d) => feedDays.filter((x) => x.date === d)[0] || null;
  const pastDates = feedDays.map((x) => x.date).filter((d) => d !== slateDate);
  // ONE selector drives both plays panels. They are read together as a single
  // card, so two independent dropdowns just made it possible to compare
  // tonight's systems against yesterday's scout plays by accident. Option
  // labels carry the combined record for the day; each panel still prints its
  // own record in its body, and a panel with nothing logged says so.
  const dayLabel = (d) => {
    const day = feedFor(d);
    const t = day && day.all;
    if (!t || !t.n) return 'no plays';
    if (t.pending && !t.w && !t.l) return t.pending + ' pending';
    return t.w + '-' + t.l + (t.push ? '-' + t.push : '')
      + (t.pending ? ' +' + t.pending + 'p' : '');
  };
  const yesterdayOf = (d) => {
    const t = Date.parse(d + 'T12:00:00Z');
    return isNaN(t) ? null
      : new Date(t - 86400000).toISOString().slice(0, 10);
  };
  const RESULT_STYLE = {
    WIN: ['#3fb950', 'rgba(63,185,80,.18)'],
    LOSS: ['#f85149', 'rgba(248,81,73,.14)'],
    PUSH: [DIM, 'rgba(139,148,158,.14)'],
  };
  const resultChip = (r) => {
    const c = RESULT_STYLE[r] || [DIM, 'rgba(139,148,158,.10)'];
    return '<span style="display:inline-block;padding:1px 6px;border-radius:3px;'
      + 'font-weight:600;white-space:nowrap;color:' + c[0] + ';background:'
      + c[1] + '">' + esc(r === 'pending' ? 'PENDING' : r) + '</span>';
  };
  const unitStr = (u) => '<span style="color:'
    + (u > 0 ? '#3fb950' : u < 0 ? '#f85149' : DIM) + ';font-weight:600">'
    + (u > 0 ? '+' : '') + Number(u).toFixed(2) + 'u</span>';

  // One past day for one group, sectioned the way the live table is: what was
  // bet, then what was only tracked, then what the rule called and nobody
  // wagered. Sorting the three together by first pitch reads as one card and
  // hides that a losing row never cost anything.
  const PAST_SECTIONS = [
    ['card', 'CARD — bet these', '#3fb950'],
    ['shadow', 'SHADOW — tracked, not bet', DIM],
    ['not_bet', 'NOT BET — rule fired, no wager', DIM],
    ['backfilled', 'BACKFILLED — replayed after the fact, never wagered', DIM],
  ];
  // Backfilled is checked FIRST. These rows were written by replaying the
  // season once the rule was carded, so treating one as a card play would put
  // hindsight money in the day's units.
  const pastKind = (r) => (r.backfilled ? 'backfilled'
    : r.not_bet ? 'not_bet' : (r.shadow ? 'shadow' : 'card'));
  const pastPlaysHtml = (dateStr, group, emptyMsg) => {
    const day = feedFor(dateStr);
    const rows = day ? day.rows.filter((r) => r.group === group) : [];
    if (!rows.length) {
      return '<div style="padding:8px 10px;font-size:12px;color:' + DIM + '">'
        + esc(emptyMsg) + '</div>';
    }
    const t = group === 'scout' ? day.scout : day.non_scout;
    const bf = t.backfilled || { n: 0, w: 0, l: 0, push: 0 };
    const liveN = t.live_n == null ? t.n : t.live_n;
    // A day with nothing but backfilled rows has no record and no money, so
    // it says what it actually is instead of reporting 0-0 and +0.00u.
    const headline = liveN
      ? (t.w + '-' + t.l + (t.push ? '-' + t.push : '')
        + (t.pending ? ' (' + t.pending + ' pending)' : '') + ' · '
        + unitStr(t.units) + ' · graded from the finals by the ledger'
        + (bf.n ? ' · plus ' + bf.n + ' backfilled' : ''))
      : (bf.w + '-' + bf.l + (bf.push ? '-' + bf.push : '')
        + ' · backfilled only — replayed after the fact, never wagered');
    let h = '<div style="padding:5px 8px;font-size:11px;color:' + DIM + '">'
      + esc(dateStr) + ' — ' + headline + '</div>'
      + '<div class="scout-scroll"><table style="width:100%;'
      + 'border-collapse:collapse;font-size:12px">'
      + '<thead><tr style="text-align:left;color:' + DIM
      + ';border-bottom:1px solid #30363d">'
      + '<th style="padding:4px 6px">CT</th><th>Game</th><th>Play</th>'
      + '<th>Rule</th><th>Result</th><th>Unit</th></tr></thead><tbody>';
    for (const [kind, label, colour] of PAST_SECTIONS) {
      const group_ = rows.filter((r) => pastKind(r) === kind)
        .sort((a, b) => String(a.commence || '~').localeCompare(
          String(b.commence || '~')));
      if (!group_.length) continue;
      const gr = group_.filter((r) => r.result === 'WIN' || r.result === 'LOSS');
      const gw = gr.filter((r) => r.result === 'WIN').length;
      const gp = group_.filter((r) => r.result === 'pending').length;
      const gu = kind === 'card'
        ? group_.reduce((a, r) => a + (r.profit || 0), 0) : null;
      h += '<tr><td colspan="6" style="padding:5px 6px 2px;font-size:11px;'
        + 'font-weight:600;border-top:1px solid #30363d;color:' + colour + '">'
        + label + ' — ' + gw + '-' + (gr.length - gw)
        + (gp ? ' (' + gp + ' pending)' : '')
        + (gu === null ? ' · no units by design' : ' · ' + unitStr(gu))
        + '</td></tr>';
      for (const r of group_) {
        const held = kind !== 'card';
        h += '<tr style="border-top:1px solid #161b22'
          + (held ? ';opacity:.6' : '') + '">'
          + '<td style="padding:3px 6px;color:' + DIM + '">'
          + (r.commence ? ctTime(r.commence) : '—') + '</td>'
          + '<td style="padding:3px 6px">' + esc(r.game || '') + '</td>'
          + '<td style="padding:3px 6px;font-weight:600;white-space:nowrap">'
          + esc(r.play || '') + ' <span style="color:' + DIM
          + ';font-weight:400">' + mlStr(r.price) + '</span></td>'
          + '<td style="white-space:nowrap">' + esc(r.name || r.rule || '') + '</td>'
          + '<td>' + resultChip(r.result) + '</td>'
          + '<td style="white-space:nowrap">'
          + (r.result === 'pending' || held
            ? '<span style="color:' + DIM + '">—</span>'
            : unitStr(r.profit || 0)) + '</td>'
          + '</tr>';
      }
    }
    return h + '</tbody></table></div>';
  };

  // Each plays panel registers its swappable body; the selector is mounted
  // once, after both exist, and drives all of them together.
  const datePanels = [];
  const registerPlaysPanel = (cardEl, group, todayHtml, emptyMsg) => {
    const body = cardEl.querySelector('[data-plays-body]');
    if (body) datePanels.push({ cardEl, group, todayHtml, emptyMsg, body });
  };
  const mountDateFilter = () => {
    if (!datePanels.length || !pastDates.length) return;
    const wrap = datePanels[0].cardEl.querySelector('[data-date-filter]');
    if (!wrap) return;
    const yday = yesterdayOf(slateDate);
    const opts = ['<option value="__today">Today'
      + (slateDate ? ' · ' + slateDate : '') + '</option>']
      .concat(pastDates.map((d) => '<option value="' + d + '">'
        + (d === yday ? 'Yesterday · ' : '') + d
        + ' · ' + dayLabel(d) + '</option>'));
    wrap.innerHTML = '<span style="color:' + DIM + ';font-size:11px">'
      + 'plays date </span><select style="background:#0d1117;color:#c9d1d9;'
      + 'border:1px solid #30363d;border-radius:4px;font-size:11px;'
      + 'padding:1px 4px;font-weight:400">' + opts.join('') + '</select>';
    const sel = wrap.querySelector('select');
    sel.addEventListener('change', () => {
      const today = sel.value === '__today';
      for (const p of datePanels) {
        p.body.innerHTML = today
          ? p.todayHtml : pastPlaysHtml(sel.value, p.group, p.emptyMsg);
        // A panel showing an earlier day must not still be headed "today's
        // plays" -- on a phone the title is the only thing above the fold.
        const when = p.cardEl.querySelector('[data-plays-when]');
        if (when) when.textContent = today ? "today's plays" : sel.value;
      }
    });
  };

  const playsCard = document.createElement('div');
  playsCard.className = 'card card-games';
  let phtml = '<div class="card-title" style="padding:6px 8px">Flagged &amp; form O/U — '
    + '<span data-plays-when>today\'s plays</span> '
    + '<span class="scout-note" style="color:' + DIM + ';font-weight:400;font-size:11px">'
    + '(CARD is bet · SHADOW is tracked, not bet · NO PLAY is a measured dead '
    + 'side. Side comes from the combo\'s verdict — most play the under, '
    + 'swingman+stale-window plays the over.)</span>'
    + '<span data-date-filter style="float:right;font-weight:400"></span>'
    + '</div>';
  const scoutTitleHtml = phtml;
  phtml = '';
  if (!underPlays.length) {
    phtml += '<div style="padding:8px 10px;font-size:12px;color:' + DIM
      + '">No qualifying plays on this slate.</div>';
  } else {
    phtml += '<div class="scout-scroll"><table style="width:100%;border-collapse:collapse;font-size:12px">'
      + '<thead><tr style="text-align:left;color:' + DIM + ';border-bottom:1px solid #30363d">'
      + '<th style="padding:4px 6px">CT</th><th>Game</th><th>Play</th><th>Rule</th>'
      + '<th data-col="why">Why</th>'
      + '</tr></thead><tbody>';
    const RANK = { card: 0, shadow: 1, dead: 2 };
    const SECTION = {
      card: 'CARD — bet these',
      shadow: 'SHADOW — tracked, not bet',
      dead: 'NO PLAY — measured dead, shown so the slate is complete',
    };
    // Section first, then first pitch inside each: the card is what gets
    // acted on, so it should not be interleaved with rows that are only
    // there for the record.
    underPlays.sort((a, b) => (RANK[a.kind] - RANK[b.kind])
      || String(a.s.commence || '').localeCompare(String(b.s.commence || '')));
    let pSection = null;
    for (const p of underPlays) {
      if (p.kind !== pSection) {
        pSection = p.kind;
        phtml += '<tr><td colspan="5" style="padding:5px 6px 2px;font-size:11px;'
          + 'font-weight:600;border-top:1px solid #30363d;color:'
          + (p.kind === 'card' ? '#3fb950' : DIM) + '">'
          + SECTION[p.kind] + '</td></tr>';
      }
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
        + '<td data-col="why" style="color:' + DIM + ';font-size:11px">'
        + p.why + '</td>'
        + '</tr>';
    }
    phtml += '</tbody></table></div>';
  }
  const scoutTodayHtml = phtml;
  playsCard.innerHTML = scoutTitleHtml
    + '<div data-plays-body>' + scoutTodayHtml + '</div>';
  el.appendChild(playsCard);
  registerPlaysPanel(playsCard, 'scout', scoutTodayHtml,
    'Nothing was logged for the scout rules that day.');
  // The two "today's plays" panels sit together at the top, scout first then
  // non-scout, so the whole actionable card is read in one place before the
  // reference tables below it. Both are built further down (the non-scout one
  // needs its table fetched first), so they are inserted against this anchor
  // rather than appended in code order.
  let playsAnchor = playsCard;

  // ---- Better-arm ML (m_sum >= +40) ----------------------------------------
  // Found 2026-09-01 inside the pool the dead form-over rule was sitting on:
  // when both starters are outclassed by the bats (m_sum >= +40), back the
  // team whose starter has the LOWER mismatch. Backing the FAVORITE in the
  // same games loses (-1.9%) and the rule is flat outside the pool (-0.2%),
  // so the filter is doing the selecting. SHADOW until 20 live plays.
  if (msumTable) {
    const MSUM_ML_AT = msumTable.threshold != null ? msumTable.threshold : 40;
    const live = isCard('better-arm-ml', msumTable.status === 'card');
    const picks = [];
    for (const g of (data.slate || [])) {
      const a = (g.sides || {}).away || {}, h = (g.sides || {}).home || {};
      if (a.mismatch == null || h.mismatch == null) continue;
      const msum = a.mismatch + h.mismatch;
      if (msum < MSUM_ML_AT) continue;
      const betterAway = a.mismatch < h.mismatch;
      const team = betterAway ? g.away : g.home;
      const ml = betterAway ? g.away_ml : g.home_ml;
      picks.push({ g, msum, team, ml, dog: ml != null && ml > 0,
        arm: (betterAway ? a : h).pitcher,
        armM: betterAway ? a.mismatch : h.mismatch,
        oppM: betterAway ? h.mismatch : a.mismatch });
    }
    // Dogs first — that is where the edge concentrates — then by first pitch.
    picks.sort((x, y) => (y.dog - x.dog)
      || String(x.g.commence || '').localeCompare(String(y.g.commence || '')));
    const sp = msumTable.splits || {};
    const rec = (k) => sp[k]
      ? sp[k].w + '-' + sp[k].l + ' ' + (sp[k].roi > 0 ? '+' : '') + sp[k].roi + '%'
      : '—';
    const card = document.createElement('div');
    card.className = 'card card-games';
    let m = '<div class="card-title" style="padding:6px 8px">'
      + 'Better arm ML — m_sum ≥ +' + MSUM_ML_AT + ' '
      + '<span style="color:' + DIM + ';font-weight:400;font-size:11px">('
      + (live ? 'CARD' : 'SHADOW — tracked, not bet')
      + (msumTable.require_dog ? ' · dogs only' : '')
      + ' · rule ' + rec('rule')
      + ' · whole pool ' + rec('pool_all')
      + ' · backing the favorite instead ' + rec('control_back_favorite')
      + ' · outside the pool ' + rec('outside_pool')
      + ')</span></div>';
    if (!picks.length) {
      m += '<div style="padding:8px 10px;font-size:12px;color:' + DIM
        + '">No game reaches m_sum +' + MSUM_ML_AT + ' on this slate.</div>';
    } else {
      m += '<div class="scout-scroll"><table style="width:100%;border-collapse:collapse;font-size:12px">'
        + '<thead><tr style="text-align:left;color:' + DIM + ';border-bottom:1px solid #30363d">'
        + '<th style="padding:4px 6px">CT</th><th>Game</th><th>Play</th>'
        + '<th>m_sum</th><th>Better arm</th></tr></thead><tbody>';
      let seenFav = false;
      for (const p of picks) {
        if (!p.dog && !seenFav) {
          seenFav = true;
          m += '<tr><td colspan="5" style="padding:5px 6px 2px;color:' + DIM
            + ';font-size:11px;border-top:1px solid #30363d">'
            + (msumTable.require_dog
              ? 'better arm is the FAVORITE — OUT OF SCOPE, not a play ('
                + rec('favorite') + ', still measured)'
              : 'better arm is the FAVORITE — ' + rec('favorite')
                + ' (weaker half of the rule)') + '</td></tr>';
        } else if (p.dog && !seenFav && picks.indexOf(p) === 0) {
          m += '<tr><td colspan="5" style="padding:5px 6px 2px;color:#3fb950'
            + ';font-size:11px;font-weight:600;border-top:1px solid #30363d">'
            + 'better arm is the DOG — THE RULE — ' + rec('dog')
            + '</td></tr>';
        }
        m += '<tr style="border-top:1px solid #161b22' + (p.dog ? '' : ';opacity:.7') + '">'
          + '<td style="padding:3px 6px;color:' + DIM + '">' + ctTime(p.g.commence) + '</td>'
          + '<td style="padding:3px 6px">' + esc(p.g.matchup) + '</td>'
          + '<td style="padding:3px 6px;font-weight:600;white-space:nowrap;color:'
          + (p.dog ? '#3fb950' : DIM) + '">' + esc(p.team) + ' ML '
          + mlStr(p.ml) + '</td>'
          + '<td style="color:' + DIM + '">+' + p.msum.toFixed(1) + '</td>'
          + '<td style="color:' + DIM + ';font-size:11px">' + esc(p.arm || '?')
          + ' (' + signed(p.armM) + ' vs ' + signed(p.oppM) + ')</td></tr>';
      }
      m += '</tbody></table></div>';
    }
    m += '<div style="padding:6px 8px;font-size:11px;color:' + DIM + ';line-height:1.5">'
      + 'Both starters outclassed by the bats they face (m_sum ≥ +' + MSUM_ML_AT
      + '); back the side whose arm grades better'
      + (msumTable.require_dog ? ', and only at plus money' : '')
      + '. Not a favorite bias — backing '
      + 'the favorite in these same games returns ' + rec('control_back_favorite')
      + ', and the rule is ' + rec('outside_pool') + ' outside the pool. '
      + (live ? '' : 'Shadow until ' + (msumTable.shadow_target || 20)
        + ' live plays: p=0.043 out of a heavily scanned session, and April ran −31%.')
      + '</div>';
    card.innerHTML = m;
    el.appendChild(card);
  }

  // ---- Non-scout systems ---------------------------------------------------
  // Eight rules found 2026-09-01 by scanning the 2,066 settled games in
  // mlb-all-ml.json using only what that file carries. Season records are
  // replayed as-of each game date by scripts/build_allml_systems_table.py on
  // every daily run, so nothing here is a number somebody typed once.
  if (sysTable) {
    const byKey = {};
    for (const sy of sysTable.systems) byKey[sy.key] = sy;
    const plays = (sysTable.today || []).filter(
      p => !sysTable.today_date || p.date === sysTable.today_date);
    const recOf = (k) => {
      const r = byKey[k] && byKey[k].record;
      return r ? r.w + '-' + r.l + ' ' + (r.roi > 0 ? '+' : '') + r.roi + '%' : '—';
    };
    const sc = document.createElement('div');
    sc.className = 'card card-games';
    let t = '<div class="card-title" style="padding:6px 8px">'
      + 'Non-scout systems — <span data-plays-when>today\'s plays</span> '
      + '<span class="scout-note" style="color:' + DIM + ';font-weight:400;font-size:11px">'
      + '(CARD — all of these are bet. Derived from the all-ML game file '
      + 'alone: prices, totals, probables, scores. No mismatch-model input, '
      + 'so an agreement with a scout rule is a second opinion.)</span>'
      + '</div>';
    const sysTitleHtml = t;
    t = '';
    if (!plays.length) {
      t += '<div style="padding:8px 10px;font-size:12px;color:' + DIM
        + '">No system qualifies on this slate.</div>';
    } else {
      t += '<div class="scout-scroll"><table style="width:100%;border-collapse:collapse;font-size:12px">'
        + '<thead><tr style="text-align:left;color:' + DIM + ';border-bottom:1px solid #30363d">'
        + '<th style="padding:4px 6px">CT</th><th>Game</th><th>Play</th>'
        + '<th>System</th><th data-col="season">Season</th>'
        + '<th data-col="why">Why</th></tr></thead><tbody>'
        + '<tr><td colspan="6" style="padding:5px 6px 2px;font-size:11px;'
        + 'font-weight:600;border-top:1px solid #30363d;color:#3fb950">'
        + 'CARD — bet these</td></tr>';
      for (const p of plays) {
        const sy = byKey[p.rule] || {};
        const playCell = p.market === 'h2h'
          ? esc(p.pick) + ' ML <span style="color:' + DIM + ';font-weight:400">'
            + mlStr(p.price) + '</span>'
          : (p.pick === 'under' ? 'U' : 'O') + (p.total == null ? '?' : p.total)
            + ' <span style="color:' + DIM + ';font-weight:400">'
            + mlStr(p.price) + '</span>';
        t += '<tr style="border-top:1px solid #161b22">'
          + '<td style="padding:3px 6px;color:' + DIM + '">' + ctTime(p.commence) + '</td>'
          + '<td style="padding:3px 6px">' + esc(p.matchup) + '</td>'
          + '<td style="padding:3px 6px;font-weight:600;white-space:nowrap">' + playCell + '</td>'
          + '<td><span style="display:inline-block;padding:1px 6px;border-radius:3px;'
          + 'font-weight:600;white-space:nowrap;background:rgba(63,185,80,.18);color:#3fb950">'
          + esc(sy.name || p.rule) + '</span>'
          + (sy.ladder_fails ? ' <span title="carded on request; the winning '
            + 'bucket has losing neighbours" style="color:#d29922">△</span>' : '')
          + '</td>'
          + '<td data-col="season" style="color:' + DIM
          + ';white-space:nowrap">' + recOf(p.rule) + '</td>'
          + '<td data-col="why" style="color:' + DIM + ';font-size:11px">'
          + esc(p.why) + '</td>'
          + '</tr>';
      }
      t += '</tbody></table></div>';
    }
    // Season table for all eight, whether or not they fired tonight.
    const sysTodayHtml = t;
    t = '';
    const b = sysTable.baselines || {};
    // The builder stores each system's own blind benchmark, so this does not
    // have to guess one from the rule name.
    const baseFor = (sy) => (sy.baseline != null ? sy.baseline : null);
    t += '<div class="scout-scroll" style="border-top:1px solid #30363d">'
      + '<table style="width:100%;border-collapse:collapse;font-size:12px">'
      + '<thead><tr style="text-align:left;color:' + DIM + '">'
      + '<th style="padding:4px 6px">System</th>'
      + '<th data-col="rule-desc">Rule</th><th>Record</th>'
      + '<th>ROI</th><th>vs blind</th><th>Halves</th>'
      + '<th data-col="perday">Plays/day</th>'
      + '</tr></thead><tbody>';
    for (const sy of sysTable.systems) {
      const r = sy.record || {};
      const base = baseFor(sy);
      const edge = (base == null || r.roi == null) ? null : r.roi - base;
      const col = r.roi > 0 ? '#3fb950' : r.roi < 0 ? '#f85149' : DIM;
      const hs = (sy.halves || []).map(
        x => (x == null ? '—' : (x > 0 ? '+' : '') + x.toFixed(0))).join(' / ');
      const bad = (sy.halves || []).some(x => x != null && x <= 0);
      t += '<tr style="border-top:1px solid #161b22">'
        + '<td style="padding:3px 6px;font-weight:600;white-space:nowrap">'
        + esc(sy.name)
        + (sy.ladder_fails ? ' <span title="carded on request; the winning '
          + 'bucket has losing neighbours" style="color:#d29922">△</span>' : '')
        + '</td>'
        + '<td data-col="rule-desc" style="color:' + DIM
        + ';font-size:11px">' + esc(sy.rule) + '</td>'
        + '<td style="color:' + DIM + ';white-space:nowrap">' + r.w + '-' + r.l + '</td>'
        + '<td style="color:' + col + ';font-weight:600;white-space:nowrap">'
        + (r.roi > 0 ? '+' : '') + (r.roi == null ? '—' : r.roi.toFixed(1)) + '%</td>'
        + '<td style="color:' + DIM + ';white-space:nowrap">'
        + (edge == null ? '—' : (edge > 0 ? '+' : '') + edge.toFixed(1)) + '</td>'
        + '<td style="color:' + (bad ? '#d29922' : DIM) + ';white-space:nowrap">'
        + hs + '</td>'
        + '<td data-col="perday" style="color:' + DIM + '">'
        + (sy.per_day == null ? '—' : sy.per_day) + '</td>'
        + '</tr>';
    }
    t += '</tbody></table></div>'
      + '<div class="scout-foot" style="padding:6px 8px;font-size:11px;color:'
      + DIM + ';line-height:1.5">'
      + 'Replayed as-of each game date over ' + (sysTable.games || 0)
      + ' settled games — a game\'s own result is never in the features that '
      + 'select it. Blind baselines over the same games: backing every side '
      + b.side + '%, blind over ' + b.over + '%, blind under ' + b.under + '%. '
      + '<span style="color:#d29922">△</span> marks a system whose winning '
      + 'bucket has losing neighbours in its own ladder — carded on request, '
      + 'and the record is what settles it. The scan that produced these tested '
      + 'thousands of cells, so treat the p-values as screening, not proof.'
      + '</div>';
    // Only the plays half swaps with the date; the season table underneath is
    // full-season and the same whichever day is being looked at.
    sc.innerHTML = sysTitleHtml
      + '<div data-plays-body>' + sysTodayHtml + '</div>' + t;
    el.insertBefore(sc, playsAnchor.nextSibling);
    playsAnchor = sc;
    registerPlaysPanel(sc, 'non-scout', sysTodayHtml,
      'No non-scout system logged a play that day.');
  }

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
      + '<div class="scout-scroll"><table style="width:100%;border-collapse:collapse;font-size:12px">'
      + '<thead><tr style="text-align:left;color:' + DIM + ';border-bottom:1px solid #30363d">'
      + '<th style="padding:4px 6px">Combo</th><th>n</th><th>UNDER</th><th>OVER</th>'
      + '</tr></thead><tbody>';
    // Two section headers only -- carded vs rust. Within a section the file is
    // already ordered lexicographically over the flag list (A, A+B, A+B+C,
    // A+B+C+D, A+B+D, A+C, ...), so the blocks are self-evident and per-tier
    // headers would just eat rows.
    let section = null;
    for (const c of comboTable.combos) {
      const mine = c.carded ? 'card' : 'rust';
      const rustEdge = mine === 'rust' && section !== 'rust';
      if (mine !== section) {
        section = mine;
        t += '<tr><td colspan="4" style="padding:5px 6px 2px;font-size:11px;'
          + 'font-weight:600;border-top:1px solid #30363d;color:'
          + (mine === 'card' ? '#3fb950' : DIM) + '">'
          + (mine === 'card'
            ? 'CARDED — these configurations are bet'
            : 'NOT CARDED — measured flat, negative, or too thin')
          + '</td></tr>';
      }
      // Below 25 plays the harness refuses to call anything an edge, so the
      // row still says "thin" -- but only DIM it when it is also not carded.
      // Dimming a live play made the carded stacks look retired.
      const thin = (c.under?.n || 0) < 25;
      t += '<tr style="border-top:1px solid '
        + (rustEdge ? '#30363d' : '#161b22')
        + (thin && !c.carded ? ';opacity:.6' : '') + '">'
        + '<td style="padding:3px 6px">'
        + (c.carded ? '<span style="color:#3fb950">●</span> ' : '<span style="color:'
          + DIM + '">○</span> ')
        + esc(c.combo) + (thin ? ' <span style="color:' + DIM
          + ';font-size:10px">thin</span>' : '') + '</td>'
        + '<td style="color:' + DIM + '">' + (c.under?.n ?? 0) + '</td>'
        + roiCell(c.under, c.verdict === 'under')
        + roiCell(c.over, c.verdict === 'over') + '</tr>';
    }
    t += '</tbody></table></div>'
      + '<div style="padding:6px 8px;font-size:11px;color:' + DIM + ';line-height:1.5">'
      + '● = carded, with the bet side shown in bold; ○ = no play. '
      + 'Combo rows count each game once under its exact flag set. '
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

  html += '<div class="scout-scroll"><table style="width:100%;border-collapse:collapse;font-size:12px">'
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
    + '<div class="scout-scroll"><table style="width:100%;border-collapse:collapse;font-size:12px">'
    + '<thead><tr style="text-align:left;color:' + DIM + ';border-bottom:1px solid #30363d">'
    + '<th style="padding:4px 6px">Mismatch</th><th>Starter</th><th>Team</th>'
    + '<th>Faces</th><th>Opp wRC+</th>'
    + '<th title="mismatch-ML: tail at m<=-45, fade at m>=+55 (L20 window). '
    + 'Carded 8/29, pulled 8/30 at 1-3, revived as SHADOW 9/1 -- tracked, '
    + 'never bet, until 15-20 plays at August\'s +9.4% expectation.">Play '
    + '<span style="color:' + DIM + ';font-weight:400;font-size:10px">'
    + '(mismatch-ML · ' + (MM_SHADOW ? 'shadow' : 'retired') + ')</span></th>'
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
  // Order at the top of the tab: banner, then both plays panels (the actionable
  // part), then the mismatch ranking, then the per-game table.
  el.insertBefore(ranked, playsAnchor.nextSibling);
  // Both plays panels exist by now, so the shared selector can be wired.
  mountDateFilter();
}
