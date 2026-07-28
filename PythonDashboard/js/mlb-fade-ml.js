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

  // Also pull the handedness-tails ledger (own file) so the bet log can show
  // Fade vs Tail picks side by side. Missing/failed load just means no tails.
  const tailLocal = 'data/mlb-hand-tails.json';
  const tailRemote = 'https://raw.githubusercontent.com/sspam1189-stack/Model/main/MLBstrikeouts/data/mlb-hand-tails.json';
  let tailData = null;
  for (const url of [tailLocal + '?t=' + Date.now(), tailRemote + '?t=' + Date.now()]) {
    try { const r = await fetch(url, { cache: 'no-store' }); if (r.ok) { tailData = await r.json(); break; } }
    catch (e) { /* next */ }
  }

  // Shadow watchlist (arms not on the active hand-tails list showing an edge).
  const watchLocal = 'data/mlb-hand-tails-watch.json';
  const watchRemote = 'https://raw.githubusercontent.com/sspam1189-stack/Model/main/MLBstrikeouts/data/mlb-hand-tails-watch.json';
  let watchData = null;
  for (const url of [watchLocal + '?t=' + Date.now(), watchRemote + '?t=' + Date.now()]) {
    try { const r = await fetch(url, { cache: 'no-store' }); if (r.ok) { watchData = await r.json(); break; } }
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

  // ---- Tail ML banner (hand-tails "take" side, own ledger) ----
  const tk = (((tailData || {}).summary || {}).byAction || {}).take;
  if (tk) {
    const TEAL = 'var(--green,#3fb950)';
    const tb = document.createElement('div');
    tb.className = 'card';
    tb.style.cssText = 'margin-bottom:14px;border:1px solid ' + TEAL + ';background:rgba(63,185,80,0.07);padding:14px 18px';
    tb.innerHTML =
      '<div style="font-weight:600;color:' + TEAL + ';margin-bottom:10px">Tail ML — back the arm’s team on 6+ opposite-hand lineups</div>'
      + '<div style="display:flex;gap:20px;flex-wrap:wrap;align-items:center">'
      + bigStat('Record', (tk.wins || 0) + '–' + (tk.losses || 0))
      + bigStat('Units', uStr(tk.units), uColor(tk.units))
      + bigStat('ROI', ((tk.roi >= 0 ? '+' : '') + ((tk.roi || 0) * 100).toFixed(1)) + '%', uColor(tk.units))
      + bigStat('Risked', (tk.staked || 0).toFixed(1) + 'u')
      + '</div>';
    el.appendChild(tb);
  }

  // ---- Today's plays ----
  // Fade-list ML picks plus the handedness-driven hand-tails picks, each tagged
  // with WHY it fired so all four reasons are distinguishable at a glance:
  //   Fade X (team) @ away/home/all  — fade-list arm (reason = venue split)
  //   Fade X (team) @ handedness     — hand-tails fade (6+ opposite-hand lineup)
  //   Tail X (team) handedness       — hand-tails take (6+ opposite-hand lineup)
  const TEAL = 'var(--green,#3fb950)';
  const tCard = document.createElement('div');
  tCard.className = 'card';
  tCard.style.cssText = 'margin-bottom:16px;padding:12px 16px';
  const PURPLE = 'var(--purple,#b083f0)';
  const REAL = new Set(['ml', 'ml_dog']);
  const pend = (data.today || []).filter(t => t.result === 'pending' && REAL.has(t.betType));
  const tailPend = ((tailData && tailData.today) || []).filter(t => t.result === 'pending');
  // Watchlist plays for today (review only, never bet) — surfaced with a WATCH tag.
  const watchPend = ((watchData && watchData.today) || []).filter(t => t.result === 'pending');

  const atStr = (t) => (t.away && t.home)
    ? ' <span style="color:#888">· ' + esc(t.away) + ' @ ' + esc(t.home) + '</span>' : '';
  const oddsStr = (o) => ' ML <span style="color:' + ORANGE + '">' + fmtOdds(o) + '</span>';
  // Reason tag next to the pick. `prefix` is '@ ' for a fade, '' for a tail.
  const tag = (txt, color, prefix) =>
    ' <span style="color:' + (color || '#888') + ';font-size:12px">' + (prefix || '') + esc(txt) + '</span>';

  // Fade-list picks — reason is the venue split (fadeReason: away/home/all).
  const fadeLabel = (t) => {
    const who = (t.pitchers || []).join(' / ');
    if (t.betType === 'ml_dog')
      return '• Mutual (' + esc(who) + ')' + tag(t.fadeReason || 'all', null, '@ ')
        + ' → dog <b>' + esc(t.selection || '?') + '</b>' + oddsStr(t.odds) + atStr(t);
    return '• Fade <b>' + esc(who) + '</b> (' + esc(t.fadeTeam || '?') + ')'
      + tag(t.fadeReason || 'all', null, '@ ')
      + ' → <b>' + esc(t.selection || '?') + '</b>' + oddsStr(t.odds) + atStr(t);
  };
  // Hand-tails picks — reason is handedness; take backs the arm's own team.
  const tailLabel = (t) => {
    const arm = esc(t.pitcher || (t.pitchers || []).join(' / '));
    if (t.action === 'take')
      return '• <span style="color:' + TEAL + ';font-weight:600">Tail</span> <b>' + arm + '</b> ('
        + esc(t.arm_team || '?') + ')' + tag('handedness', TEAL, '')
        + ' → <b>' + esc(t.selection || '?') + '</b>' + oddsStr(t.odds) + atStr(t);
    return '• Fade <b>' + arm + '</b> (' + esc(t.arm_team || '?') + ')'
      + tag('handedness', null, '@ ')
      + ' → <b>' + esc(t.selection || '?') + '</b>' + oddsStr(t.odds) + atStr(t);
  };
  // WATCH badge for watchlist plays — review only, not part of the bet record.
  const watchBadge = '<span style="background:rgba(176,131,240,0.16);color:' + PURPLE
    + ';font-size:10px;font-weight:700;letter-spacing:.05em;padding:1px 5px;border-radius:4px;margin-right:4px">WATCH</span>';
  const watchLabel = (t) => {
    const arm = esc(t.pitcher || '?');
    const verb = t.action === 'take'
      ? '<span style="color:' + TEAL + ';font-weight:600">Tail</span>' : 'Fade';
    const reasonTag = tag('handedness', t.action === 'take' ? TEAL : null, t.action === 'take' ? '' : '@ ');
    const gs = t.games ? ' <span style="color:#777;font-size:11px">(' + t.games + 'gs)</span>' : '';
    return '• ' + watchBadge + verb + ' <b>' + arm + '</b> (' + esc(t.arm_team || '?') + ')'
      + reasonTag + ' → <b>' + esc(t.selection || '?') + '</b>' + oddsStr(t.odds) + gs + atStr(t);
  };

  let th = '<div class="card-title" style="margin-bottom:8px">Today’s plays</div>';
  if (!pend.length && !tailPend.length && !watchPend.length) {
    th += '<div class="no-picks">No fade-list or hand-tails moneyline plays on today’s slate.</div>';
  } else {
    th += pend.map(t => '<div style="font-size:14px;color:#ddd;padding:2px 0">' + fadeLabel(t) + '</div>').join('');
    th += tailPend.map(t => '<div style="font-size:14px;color:#ddd;padding:2px 0">' + tailLabel(t) + '</div>').join('');
    if (watchPend.length) {
      th += watchPend.map(t => '<div style="font-size:14px;color:#cbb8e6;padding:2px 0">' + watchLabel(t) + '</div>').join('');
      th += '<div style="font-size:11px;color:#777;margin-top:6px">WATCH = handedness watchlist candidate (review only, not bet).</div>';
    }
  }
  tCard.innerHTML = th;
  el.appendChild(tCard);

  // ---- Season bet log (filterable by Fade/Tail + pick + pitcher + venue) ----
  const typeTag = { ml: 'Fade', ml_dog: 'DOG', hand_fade: 'Fade', hand_take: 'TAIL' };
  // Fade-list bets (kind=fade) merged with hand-tails bets (kind=tail when the
  // pick backs the arm's own team, else fade). Normalize hand-tails fields to
  // what the log renderer expects (pitchers[], fadeTeam for the Venue filter).
  const fadeBets = (data.bets || []).map(b => ({ ...b, kind: 'fade' }));
  const tailBets = ((tailData && tailData.bets) || []).map(b => ({
    ...b, kind: b.action === 'take' ? 'tail' : 'fade',
    pitchers: b.pitchers || [b.pitcher], fadeTeam: b.arm_team,
  }));
  const bets = [...fadeBets, ...tailBets].sort((a, b) => {
    const ka = (a.date || '') + (a.commence || ''), kb = (b.date || '') + (b.commence || '');
    return ka < kb ? 1 : ka > kb ? -1 : 0;  // newest first
  });

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

  // Distinct bet dates, newest first, for the date dropdown.
  const dates = [...new Set(bets.map(b => b.date).filter(Boolean))].sort().reverse();
  const MON = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const dateLabel = (iso) => {
    const [y, m, d] = iso.split('-');
    return MON[+m] + ' ' + (+d) + ', ' + y;
  };

  // Distinct weeks (Monday–Sunday), newest first, for the week dropdown.
  // Compute in UTC from the plain ISO date so it's timezone-independent.
  const weekStartOf = (iso) => {
    const [y, m, d] = iso.split('-').map(Number);
    const dt = new Date(Date.UTC(y, m - 1, d));
    const dow = dt.getUTCDay();                    // 0=Sun … 6=Sat
    dt.setUTCDate(dt.getUTCDate() + (dow === 0 ? -6 : 1 - dow)); // back to Monday
    return dt.toISOString().slice(0, 10);
  };
  const weekLabel = (mondayIso) => {
    const [y, m, d] = mondayIso.split('-').map(Number);
    const sun = new Date(Date.UTC(y, m - 1, d + 6));
    return MON[m] + ' ' + d + ' – ' + MON[sun.getUTCMonth() + 1] + ' ' + sun.getUTCDate() + ', ' + sun.getUTCFullYear();
  };
  const weeks = [...new Set(bets.map(b => b.date).filter(Boolean).map(weekStartOf))].sort().reverse();

  const log = document.createElement('div');
  log.className = 'card card-games';
  log.style.cssText = 'padding:8px 4px';
  const selCss = 'background:#1b1b1b;color:#ddd;border:1px solid #333;border-radius:6px;padding:4px 8px;font-size:12px';
  log.innerHTML =
    '<div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:6px 8px">'
    + '<div class="card-title" style="padding:0">Season bet log</div>'
    + '<span id="fadeLogRec" style="font-size:13px;font-weight:700"></span>'
    + '</div>'
    // Row 0: Fade / Tail (bet direction)
    + '<div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:0 8px 6px">'
    + '<label style="font-size:11px;color:#888">Type '
    + '<select id="fadeKindSel" style="' + selCss + '"><option value="">All</option>'
    + '<option value="fade">Fade</option><option value="tail">Tail</option>'
    + '</select></label>'
    + '</div>'
    // Row 1: Pick / Pitcher / Venue
    + '<div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:0 8px 6px">'
    + '<label style="font-size:11px;color:#888">Pick '
    + '<select id="fadePickSel" style="' + selCss + '"><option value="">All</option>'
    + pickTeams.map(t => '<option value="' + esc(t) + '">' + esc(t) + '</option>').join('')
    + '</select></label>'
    + '<label style="font-size:11px;color:#888">Pitcher '
    + '<select id="fadePitcherSel" style="' + selCss + '"><option value="">All</option>'
    + pitchers.map(p => '<option value="' + esc(p) + '">' + esc(p) + '</option>').join('')
    + '</select></label>'
    + '<label style="font-size:11px;color:#888">Venue '
    + '<select id="fadeVenueSel" style="' + selCss + '"><option value="">All</option>'
    + '<option value="home">Home</option><option value="away">Away</option>'
    + '</select></label>'
    + '</div>'
    // Row 2: Month / Week / Date
    + '<div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:0 8px 6px">'
    + '<label style="font-size:11px;color:#888">Month '
    + '<select id="fadeMonthSel" style="' + selCss + '"><option value="">All</option>'
    + months.map(m => '<option value="' + m + '">' + monthLabel(m) + '</option>').join('')
    + '</select></label>'
    + '<label style="font-size:11px;color:#888">Week '
    + '<select id="fadeWeekSel" style="' + selCss + '"><option value="">All</option>'
    + weeks.map(w => '<option value="' + w + '">' + weekLabel(w) + '</option>').join('')
    + '</select></label>'
    + '<label style="font-size:11px;color:#888">Date '
    + '<select id="fadeDateSel" style="' + selCss + '"><option value="">All</option>'
    + dates.map(dt => '<option value="' + dt + '">' + dateLabel(dt) + '</option>').join('')
    + '</select></label>'
    + '</div>'
    + '<div id="fadeLogWrap" style="overflow-x:auto"></div>';
  el.appendChild(log);

  const wrap = log.querySelector('#fadeLogWrap');
  const pickSel = log.querySelector('#fadePickSel');
  const monthSel = log.querySelector('#fadeMonthSel');
  const weekSel = log.querySelector('#fadeWeekSel');
  const dateSel = log.querySelector('#fadeDateSel');
  const pitcherSel = log.querySelector('#fadePitcherSel');
  const venueSel = log.querySelector('#fadeVenueSel');
  const kindSel = log.querySelector('#fadeKindSel');
  const recEl = log.querySelector('#fadeLogRec');

  // Venue of a fade bet = the faded arm's-team side (home if his team is home).
  const venueOf = (b) => b.fadeTeam === b.home ? 'home' : (b.fadeTeam === b.away ? 'away' : '');

  // Rebuild the Pitcher dropdown to only list arms that have bets of the
  // currently selected Type (Fade/Tail). Keeps the current pick if still valid.
  function refreshPitcherOptions() {
    const tv = kindSel.value;
    const ps = [...new Set(bets.filter(b => !tv || b.kind === tv)
      .flatMap(b => b.pitchers || []))].sort();
    const cur = pitcherSel.value;
    pitcherSel.innerHTML = '<option value="">All</option>'
      + ps.map(p => '<option value="' + esc(p) + '">' + esc(p) + '</option>').join('');
    pitcherSel.value = ps.includes(cur) ? cur : '';
  }

  function drawRows() {
    const kv = pickSel.value, mv = monthSel.value, wv = weekSel.value, dv = dateSel.value, pv = pitcherSel.value, vv = venueSel.value, tv = kindSel.value;
    const view = bets.filter(b =>
      (!tv || b.kind === tv) &&
      (!kv || b.selection === kv) &&
      (!mv || (b.date || '').slice(0, 7) === mv) &&
      (!wv || (b.date && weekStartOf(b.date) === wv)) &&
      (!dv || b.date === dv) &&
      (!pv || (b.pitchers || []).includes(pv)) &&
      (!vv || venueOf(b) === vv));
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
  weekSel.addEventListener('change', drawRows);
  dateSel.addEventListener('change', drawRows);
  pitcherSel.addEventListener('change', drawRows);
  venueSel.addEventListener('change', drawRows);
  kindSel.addEventListener('change', () => { refreshPitcherOptions(); drawRows(); });
  drawRows();

  // ---- Shadow watchlist (review-only candidates, not bet) ----
  if (watchData && (watchData.candidates || []).length) {
    const wc = document.createElement('div');
    wc.className = 'card card-games';
    wc.style.cssText = 'padding:8px 4px;margin-top:16px';
    const wrows = watchData.candidates.map(c => {
      const b = c.suggest === 'fade' ? c.fade : c.take;
      const sugColor = c.suggest === 'take' ? 'var(--green,#3fb950)' : ORANGE;
      return '<tr>'
        + '<td style="padding:4px 8px">' + esc(c.pitcher) + '</td>'
        + '<td style="padding:4px 6px;text-align:center;color:#999">' + esc(c.hand) + 'HP</td>'
        + '<td style="padding:4px 8px;text-align:center;color:' + sugColor + ';font-weight:700;text-transform:uppercase">' + esc(c.suggest) + '</td>'
        + '<td style="padding:4px 8px;text-align:center">' + b.w + '–' + b.l + '</td>'
        + '<td style="padding:4px 8px;text-align:right;color:' + uColor(b.u) + '">' + (b.u >= 0 ? '+' : '') + b.u.toFixed(2) + 'u</td>'
        + '<td style="padding:4px 8px;text-align:center;color:#999">' + c.games + '</td>'
        + '<td style="padding:4px 8px;text-align:right;color:#999">' + (c.era != null ? c.era.toFixed(2) : '—') + '</td>'
        + '</tr>';
    }).join('');
    wc.innerHTML =
      '<div style="padding:6px 8px"><span class="card-title" style="padding:0">Tail watchlist</span>'
      + '<span style="font-size:11px;color:#888;margin-left:8px">arms not on the list with a 6+ opposite-hand edge · review only, not bet · in-sample</span></div>'
      + '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:13px">'
      + '<thead><tr style="color:#888;text-align:left;border-bottom:1px solid #333">'
      + '<th style="padding:4px 8px">Pitcher</th><th style="padding:4px 6px;text-align:center">Hand</th>'
      + '<th style="padding:4px 8px;text-align:center">Suggest</th><th style="padding:4px 8px;text-align:center">W–L</th>'
      + '<th style="padding:4px 8px;text-align:right">Units</th><th style="padding:4px 8px;text-align:center">GS</th>'
      + '<th style="padding:4px 8px;text-align:right">ERA</th></tr></thead><tbody>' + wrows + '</tbody></table></div>';
    el.appendChild(wc);
  }
}

if (typeof esc === 'undefined') {
  window.esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
