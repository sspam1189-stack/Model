// PythonDashboard/js/mlb-fade-ml.js
// Renderer for the "MLB Fade ML" tab. Reads mlb-fade-ml.json (run_daily_ml.py
// / ml_backfill.py). Three bet types on fade-list games:
//   ml      - single fade arm -> bet the OPPONENT moneyline
//   ml_dog  - mutual fade (both starters fade) -> bet the UNDERDOG moneyline
//             (retired 2026-08-07: mutual games are skipped going forward)
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

  // Fade watchlist (auto-screen: arms not on the active fade list whose home or
  // away fade clears the bar). Own file (fade_watch.py); like the tail watchlist.
  const fwatchLocal = 'data/mlb-fade-watch.json';
  const fwatchRemote = 'https://raw.githubusercontent.com/sspam1189-stack/Model/main/MLBstrikeouts/data/mlb-fade-watch.json';
  let fwatchData = null;
  for (const url of [fwatchLocal + '?t=' + Date.now(), fwatchRemote + '?t=' + Date.now()]) {
    try { const r = await fetch(url, { cache: 'no-store' }); if (r.ok) { fwatchData = await r.json(); break; } }
    catch (e) { /* next */ }
  }

  // Pitcher-vs-team fade ledger (fade_vs_team.py) — a separate "fade this arm
  // vs this specific team" angle. Its graded starts feed the bet log under the
  // Reason = "Vs team" filter. Own file; missing = no vs-team rows.
  const vtLocal = 'data/mlb-fade-vs-team.json';
  const vtRemote = 'https://raw.githubusercontent.com/sspam1189-stack/Model/main/MLBstrikeouts/data/mlb-fade-vs-team.json';
  let vtData = null;
  for (const url of [vtLocal + '?t=' + Date.now(), vtRemote + '?t=' + Date.now()]) {
    try { const r = await fetch(url, { cache: 'no-store' }); if (r.ok) { vtData = await r.json(); break; } }
    catch (e) { /* next */ }
  }

  // Pitcher-vs-team WATCH list (fade_vs_team_watch.py) — the review tier
  // (ERA 6–8 vs the team), shown as a shadow watchlist card below. Data only.
  const vtwLocal = 'data/mlb-fade-vs-team-watch.json';
  const vtwRemote = 'https://raw.githubusercontent.com/sspam1189-stack/Model/main/MLBstrikeouts/data/mlb-fade-vs-team-watch.json';
  let vtwData = null;
  for (const url of [vtwLocal + '?t=' + Date.now(), vtwRemote + '?t=' + Date.now()]) {
    try { const r = await fetch(url, { cache: 'no-store' }); if (r.ok) { vtwData = await r.json(); break; } }
    catch (e) { /* next */ }
  }

  // Self-computed team wOBA/wRC+ splits by hand, per window (build_team_woba_
  // splits.py) — park-adjusted wRC+ approximation, sliceable by
  // month/recent. Own file; missing = no wRC+ table.
  const wobaLocal = 'data/mlb-team-woba-splits.json';
  const wobaRemote = 'https://raw.githubusercontent.com/sspam1189-stack/Model/main/MLBstrikeouts/data/mlb-team-woba-splits.json';
  let wobaData = null;
  for (const url of [wobaLocal + '?t=' + Date.now(), wobaRemote + '?t=' + Date.now()]) {
    try { const r = await fetch(url, { cache: 'no-store' }); if (r.ok) { wobaData = await r.json(); break; } }
    catch (e) { /* next */ }
  }

  // Normalize a computed window into {team:{vsLHP,vsRHP,paL,paR}} (ints). Shared
  // by the Today's-plays wRC+ chips and the wRC+ table so both track the same
  // selected window.
  const _wobaWindows = (wobaData && wobaData.windows) || {};
  // venue: '' (all) | 'home' | 'road'; role: '' (all) | 'sp' | 'rp' (starters /
  // relievers faced); roster: '' (whole team) | 'lineup' (tonight's confirmed 9).
  const teamsForWindow = (winKey, venue, role, roster) => {
    const t = (_wobaWindows[winKey] || {}).teams || {}; const o = {};
    Object.keys(t).forEach(k => {
      // Under the confirmed-lineup roster, only teams whose lineup has posted
      // have a 'lineup' node -- others are omitted (not shown blank).
      if (roster === 'lineup' && !t[k].lineup) return;
      const rosterBase = (roster === 'lineup') ? t[k].lineup : t[k];
      const roleBase = (role === 'sp' || role === 'rp') ? (rosterBase[role] || {}) : rosterBase;
      const src = (venue === 'home' || venue === 'road') ? (roleBase[venue] || {}) : roleBase;
      o[k] = {
        vsLHP: (src.vsLHP || {}).wrcplus, vsRHP: (src.vsRHP || {}).wrcplus,
        paL: (src.vsLHP || {}).pa, paR: (src.vsRHP || {}).pa,
      };
    });
    return o;
  };

  // Today's full slate (all games) — for the wRC+ table's matchup filter buttons.
  const allmlLocal = 'data/mlb-all-ml.json';
  const allmlRemote = 'https://raw.githubusercontent.com/sspam1189-stack/Model/main/MLBstrikeouts/data/mlb-all-ml.json';
  let allmlData = null;
  for (const url of [allmlLocal + '?t=' + Date.now(), allmlRemote + '?t=' + Date.now()]) {
    try { const r = await fetch(url, { cache: 'no-store' }); if (r.ok) { allmlData = await r.json(); break; } }
    catch (e) { /* next */ }
  }

  // Today's probable-pitcher hands ({normName: 'L'|'R'}), to tag Today's plays
  // as RHP/LHP. Own file (build_pitch_hands_today.py); missing = no tags.
  const handLocal = 'data/mlb-pitch-hands.json';
  const handRemote = 'https://raw.githubusercontent.com/sspam1189-stack/Model/main/MLBstrikeouts/data/mlb-pitch-hands.json';
  let handData = null;
  for (const url of [handLocal + '?t=' + Date.now(), handRemote + '?t=' + Date.now()]) {
    try { const r = await fetch(url, { cache: 'no-store' }); if (r.ok) { handData = await r.json(); break; } }
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

  // ---- Combined banner: fade-list ML + hand-fade ML ----
  const TEAL = 'var(--green,#3fb950)';
  const hf = (((tailData || {}).summary || {}).byAction || {}).fade || null;
  const banner = document.createElement('div');
  banner.className = 'card';
  banner.style.cssText = 'margin-bottom:14px;border:1px solid ' + ORANGE + ';background:rgba(232,163,61,0.08);padding:14px 18px';
  const bigStat = (label, val, color) =>
    '<div style="text-align:center;min-width:80px">'
    + '<div style="font-size:22px;font-weight:700;color:' + (color || '#eee') + '">' + val + '</div>'
    + '<div style="font-size:11px;color:#aaa;text-transform:uppercase;letter-spacing:.04em">' + label + '</div></div>';
  const uStr = (u) => (u >= 0 ? '+' : '') + (u || 0).toFixed(2) + 'u';
  const roiPct = (r) => ((r >= 0 ? '+' : '') + ((r || 0) * 100).toFixed(1)) + '%';
  // Combined total = venue fade-list + hand-fade.
  const cW = (s.wins || 0) + ((hf && hf.wins) || 0);
  const cL = (s.losses || 0) + ((hf && hf.losses) || 0);
  const cU = (s.units || 0) + ((hf && hf.units) || 0);
  const cStk = (s.staked || 0) + ((hf && hf.staked) || 0);
  const cRoi = cStk ? cU / cStk : 0;
  const subLine = (color, title, r, extra) =>
    '<div style="border-top:1px solid rgba(255,255,255,0.08);padding-top:8px;margin-top:8px">'
    + '<div style="font-size:12px;color:' + color + ';font-weight:600">' + title + '</div>'
    + '<div style="font-size:15px;font-weight:700;margin-top:2px">' + (r.wins || 0) + '–' + (r.losses || 0)
    + ' <span style="font-size:13px;color:' + uColor(r.units) + '">' + uStr(r.units) + ' · ' + roiPct(r.roi) + '</span>'
    + ' <span style="font-size:11px;color:#888;font-weight:400">· ' + (r.staked || 0).toFixed(1) + 'u risked' + (extra || '') + '</span></div></div>';
  banner.innerHTML =
    '<div style="font-weight:600;color:' + ORANGE + ';margin-bottom:10px">Fade ML — combined (venue + hand)</div>'
    + '<div style="display:flex;gap:20px;flex-wrap:wrap;align-items:center">'
    + bigStat('Record', cW + '–' + cL)
    + bigStat('Units', uStr(cU), uColor(cU))
    + bigStat('ROI', roiPct(cRoi), uColor(cU))
    + bigStat('Risked', cStk.toFixed(1) + 'u')
    + '</div>'
    + subLine(ORANGE, 'Fade-list moneyline — fade the pitcher, mutual games skipped since 8/7', s, ' · ' + (s.voids || 0) + ' voids')
    + (hf && (hf.wins || hf.losses) ? subLine(TEAL, 'Hand-fade ML — fade the arm on 6+ opposite-hand lineups', hf, '') : '');
  el.appendChild(banner);

  // ---- Hand-tails NOTIFICATIONS: manual list vs walk-forward bar ----
  // The hand-tails bet list is manually curated (hand-tails-manual.json). The
  // daily run computes who currently clears 4 starts & +3.0u walk-forward and
  // flags any qualifier NOT on the manual list as a PROMOTION candidate (badge
  // below), and any manual-list arm that has slipped under the bar as a
  // "consider removing" note. Nothing is auto-added or auto-removed.
  const promos = (tailData && tailData.promotions) || [];
  const demos = (tailData && tailData.demotions) || [];
  if (promos.length || demos.length) {
    const note = document.createElement('div');
    note.className = 'card';
    note.style.cssText = 'margin-bottom:14px;padding:12px 16px;border:1px solid '
      + (promos.length ? TEAL : ORANGE) + ';background:rgba(63,185,80,0.08)';
    let html = '';
    if (promos.length) {
      html += '<div style="font-weight:700;color:' + TEAL + ';margin-bottom:6px">'
        + '🔔 Hand-tails promotion candidate' + (promos.length > 1 ? 's' : '')
        + ' (' + promos.length + ')</div>'
        + '<div style="font-size:12px;color:#aaa;margin-bottom:8px">Cleared 4 starts &amp; '
        + '+3.0u walk-forward but not on your manual fade list. Review and add to '
        + '<code>hand-tails-manual.json</code> to bet.</div>';
      promos.forEach(p => {
        const uc = ((p.units || 0) >= 0 ? GREEN : RED);
        html += '<div style="font-size:14px;margin:2px 0">+ <strong>' + p.name + '</strong> '
          + '<span style="color:#888">(' + p.hand + 'HP)</span> — ' + (p.games || 0) + 'gs '
          + '<span style="color:' + uc + '">' + ((p.units || 0) >= 0 ? '+' : '')
          + (p.units || 0).toFixed(2) + 'u</span> '
          + '<span style="font-size:11px;color:#888">qualified since ' + (p.since || '—')
          + '</span></div>';
      });
    }
    if (demos.length) {
      html += '<div style="font-weight:600;color:' + ORANGE + ';margin-top:'
        + (promos.length ? '10px' : '0') + ';margin-bottom:4px">Consider removing ('
        + demos.length + ') — on your list but under the bar</div>';
      demos.forEach(d => {
        const uc = ((d.units || 0) >= 0 ? GREEN : RED);
        html += '<div style="font-size:13px;margin:2px 0;color:#bbb">− <strong>' + d.name
          + '</strong> <span style="color:#888">(' + (d.hand || '') + 'HP)</span> — '
          + (d.games || 0) + 'gs <span style="color:' + uc + '">' + ((d.units || 0) >= 0 ? '+' : '')
          + (d.units || 0).toFixed(2) + 'u</span></div>';
      });
    }
    note.innerHTML = html;
    el.appendChild(note);
  }

  // ---- Per-type sub-records ----
  const typeMeta = [
    ['ml', 'Fade ML (opp)', 'Single fade arm → opponent moneyline'],
    ['ml_dog', 'Mutual → dog', 'Both starters fade → underdog ML (retired 8/7)'],
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
  // Fade-list ML picks plus the handedness-driven hand-tails picks, each tagged
  // with WHY it fired so all four reasons are distinguishable at a glance:
  //   Fade X (team) @ away/home/all  — fade-list arm (reason = venue split)
  //   Fade X (team) @ handedness     — hand-tails fade (6+ opposite-hand lineup)
  //   Tail X (team) handedness       — hand-tails take (6+ opposite-hand lineup)
  const tCard = document.createElement('div');
  tCard.className = 'card';
  tCard.style.cssText = 'margin-bottom:16px;padding:12px 16px';
  const PURPLE = 'var(--purple,#b083f0)';
  const REAL = new Set(['ml', 'ml_dog']);
  const pend = (data.today || []).filter(t => t.result === 'pending' && REAL.has(t.betType));
  // Fade-WATCH pending (auto-screen candidates starting today) — WATCH badge.
  const fadeWatchPend = ((fwatchData && fwatchData.today) || []).filter(t => t.result === 'pending');
  const tailPend = ((tailData && tailData.today) || []).filter(t => t.result === 'pending');
  // Watchlist plays for today (review only, never bet) — surfaced with a WATCH tag.
  const watchPend = ((watchData && watchData.today) || []).filter(t => t.result === 'pending');
  // Pitcher-vs-team pending picks (fade_vs_team.py) — bettable, but previously
  // only shown in the Season bet log. Surface them in Today's plays too, tagged
  // "vs team", so a live vs-team pick isn't hidden from the today view.
  const vtPend = ((vtData && vtData.today) || []).filter(t => t.result === 'pending');
  // Hand-tails plays whose opponent lineup is only PROJECTED so far — a fade
  // qualifies on a projected card but no pick is made until the real lineup
  // posts. Shown in their own "awaiting lineup" group, odds withheld, not bet.
  const tailUnconf = ((tailData && tailData.today) || []).filter(t => t.result === 'unconfirmed');
  const watchUnconf = ((watchData && watchData.today) || []).filter(t => t.result === 'unconfirmed');

  const atStr = (t) => (t.away && t.home)
    ? ' <span style="color:#888">· ' + esc(t.away) + ' @ ' + esc(t.home) + '</span>' : '';
  // Odds are withheld (null) on unconfirmed rows — the lineup hasn't posted, so
  // the pick isn't final and the price can drift. Render nothing in that case.
  const oddsStr = (o) => (o == null) ? ''
    : ' ML <span style="color:' + ORANGE + '">' + fmtOdds(o) + '</span>';
  // Reason tag next to the pick. `prefix` is '@ ' for a fade, '' for a tail.
  const tag = (txt, color, prefix) =>
    ' <span style="color:' + (color || '#888') + ';font-size:12px">' + (prefix || '') + esc(txt) + '</span>';

  // Pitcher RHP/LHP tag from today's probable-pitcher map (build_pitch_hands_
  // today.py). Normalizes the name the same way the Python builder does before
  // lookup; `fb` is a fallback hand ('L'/'R') carried on the record itself.
  // Placed right after the Fade/Tail verb: "Fade (RHP) Slade Cecconi (CLE) …".
  const handMap = (handData && handData.hands) || {};
  const _hn = (s) => String(s == null ? '' : s).normalize('NFD')
    .replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z ]/g, ' ')
    .replace(/\s+/g, ' ').trim();
  const handOf = (name, fb) => handMap[_hn(name)] || (fb === 'L' || fb === 'R' ? fb : null);
  const handTag = (name, fb) => {
    const h = handOf(name, fb);
    return h ? ' <span style="color:#7aa2d6;font-size:11px;font-weight:600">(' + h + 'HP)</span>' : '';
  };
  // wRC+ of the OFFENSE we're buying (the bet team) vs the faded pitcher's hand
  // — the platoon number that actually matters for the fade. Green above 100,
  // red below. Needs both the pitcher's hand and a wRC+ row for the bet team.
  // Today's per-team probable starter ({abbr: {name, hand}}) — used to show the
  // starting pitcher of the TAKE (the team we're backing) before its name.
  const starters = (handData && handData.starters) || {};
  // Only trust the starter map when it's for the CURRENT slate. If the pitch-
  // hands file lagged (built before today's probables posted), its date won't
  // match today's picks — show no take-SP (TBD) rather than yesterday's arm.
  const _slateDate = ((data.today || []).find(t => t.result === 'pending') || {}).date;
  const _startersFresh = !!(handData && handData.date && _slateDate && handData.date === _slateDate);
  const takeSpTag = (team) => {
    if (!_startersFresh) return '';
    const sp = starters[team];
    return (sp && sp.name)
      ? '<span style="color:#9aa2ad;font-size:12px">' + esc(sp.name) + '</span> ' : '';
  };
  // Today's-plays wRC+ chips track the wRC+ table's selected window (default
  // last30). `wrcTeams` is reassigned when the window changes (see the table's
  // Window select), and the plays are repainted. Falls back to the computed
  // season if the default window is empty.
  const DEFAULT_WRC_WIN = 'last30';
  let wrcTeams = teamsForWindow(DEFAULT_WRC_WIN);
  if (!Object.keys(wrcTeams).length) wrcTeams = teamsForWindow('season');
  // A "TEAM NN vs XHP" chip (green above 100, red below). `label` adds "wRC+".
  const wrcChip = (team, v, h, label) => {
    const col = v >= 100 ? GREEN : RED;
    return ' <span style="color:#888;font-size:11px">· ' + esc(team) + ' '
      + '<span style="color:' + col + ';font-weight:600">' + v + '</span>'
      + (label ? ' wRC+' : '') + ' vs ' + h + 'HP</span>';
  };
  // The OFFENSE WE'RE BUYING (bet team) vs the faded pitcher's hand.
  const betWrcTag = (selection, pitcherName, fbHand) => {
    const h = handOf(pitcherName, fbHand);
    const tm = wrcTeams[selection];
    if (!h || !tm) return '';
    const v = h === 'L' ? tm.vsLHP : tm.vsRHP;
    return v == null ? '' : wrcChip(selection, v, h, true);
  };
  // The OFFENSE WE'RE FADING (fade team) vs the take starter's hand — falls back
  // to the faded arm's hand when the take SP is still TBD. Shows both sides.
  const fadeWrcTag = (fadeTeam, takeTeam, fadedHand) => {
    const tm = wrcTeams[fadeTeam];
    if (!tm) return '';
    const sp = starters[takeTeam];
    const h = (sp && sp.hand) || fadedHand;
    if (!h) return '';
    const v = h === 'L' ? tm.vsLHP : tm.vsRHP;
    return v == null ? '' : wrcChip(fadeTeam, v, h, false);
  };
  // A team's wRC+ vs a given pitcher hand (for hand-tails picks, both sides).
  const teamVsHandTag = (team, hand, label) => {
    const tm = wrcTeams[team];
    if (!tm || !hand) return '';
    const v = hand === 'L' ? tm.vsLHP : tm.vsRHP;
    return v == null ? '' : wrcChip(team, v, hand, label);
  };
  // Opposing starter's hand for a team (only when the starter map is fresh).
  const oppSpHandOf = (team) => (_startersFresh && (starters[team] || {}).hand) || null;

  // Fade-list picks — reason is the venue split (fadeReason: away/home/all).
  const fadeLabel = (t) => {
    const ps = t.pitchers || [];
    if (t.betType === 'ml_dog') {
      // Mutual: two arms, tag each name individually. (No single-hand wRC+ tag
      // — the dog's offense faces the opposing fade arm, ambiguous to map here.)
      const arms = ps.map(p => esc(p) + handTag(p)).join(' / ');
      return '• Mutual (' + arms + ')' + tag(t.fadeReason || 'all', null, '@ ')
        + ' → dog <b>' + esc(t.selection || '?') + '</b>' + oddsStr(t.odds) + atStr(t);
    }
    const who = ps[0] || ps.join(' / ');
    return '• Fade' + handTag(who) + ' <b>' + esc(who) + '</b> (' + esc(t.fadeTeam || '?') + ')'
      + tag(t.fadeReason || 'all', null, '@ ')
      + fadeWrcTag(t.fadeTeam, t.selection, handOf(who))   // fade team's bat up front
      + ' → ' + takeSpTag(t.selection) + '<b>' + esc(t.selection || '?') + '</b>' + oddsStr(t.odds) + atStr(t)
      + betWrcTag(t.selection, who);                        // take team's bat at the end
  };
  // Fade-WATCH picks (shadow WATCH_LIST arms) — same shape as a fade with a
  // WATCH badge; tracked, never bet. Rendered in Today's plays for visibility.
  const fadeWatchLabel = (t) => {
    const who = t.pitcher || (t.pitchers || [])[0] || '';
    const fadeTeam = t.fadeTeam || t.arm_team;
    const gs = t.games ? ' <span style="color:#777;font-size:11px">(' + t.games + 'gs)</span>' : '';
    return '• ' + watchBadge + 'Fade' + handTag(who) + ' <b>' + esc(who) + '</b> ('
      + esc(fadeTeam || '?') + ')'
      + tag(t.suggest || 'all', null, '@ ')
      + fadeWrcTag(fadeTeam, t.selection, handOf(who))
      + ' → ' + takeSpTag(t.selection) + '<b>' + esc(t.selection || '?') + '</b>'
      + oddsStr(t.odds) + atStr(t) + betWrcTag(t.selection, who) + gs;
  };
  // Hand-tails picks — reason is handedness; take backs the arm's own team.
  const tailLabel = (t) => {
    const name = t.pitcher || (t.pitchers || []).join(' / ');
    const htag = handTag(name, t.hand);
    const armHand = handOf(name, t.hand);       // the arm's own hand
    const armTeam = t.arm_team, opp = t.opp_team;
    if (t.action === 'take')
      // Back the arm's team: opp bat vs the arm up front, our bat vs opp SP at end.
      return '• <span style="color:' + TEAL + ';font-weight:600">Tail</span>' + htag
        + ' <b>' + esc(name) + '</b> (' + esc(armTeam || '?') + ')' + tag('handedness', TEAL, '')
        + teamVsHandTag(opp, armHand, false)
        + ' → <b>' + esc(t.selection || '?') + '</b>' + oddsStr(t.odds) + atStr(t)
        + teamVsHandTag(armTeam, oppSpHandOf(opp), true);
    // Hand-tails fade: bet the opponent — arm's-team bat vs opp SP up front,
    // opp bat vs the arm at the end.
    return '• Fade' + htag + ' <b>' + esc(name) + '</b> (' + esc(armTeam || '?') + ')'
      + tag('handedness', null, '@ ')
      + teamVsHandTag(armTeam, oppSpHandOf(opp), false)
      + ' → <b>' + esc(t.selection || '?') + '</b>' + oddsStr(t.odds) + atStr(t)
      + teamVsHandTag(opp, armHand, true);
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
    return '• ' + watchBadge + verb + handTag(t.pitcher, t.hand) + ' <b>' + arm + '</b> (' + esc(t.arm_team || '?') + ')'
      + reasonTag + ' → <b>' + esc(t.selection || '?') + '</b>' + oddsStr(t.odds) + gs + atStr(t);
  };
  // UNCONFIRMED badge + wrapper for hand-tails plays that qualify on a projected
  // lineup only. The pick isn't placed until the real card posts (odds already
  // withheld upstream); this surfaces the brewing fade with its projected count.
  const AMBER = '#c99a3a';
  const unconfBadge = '<span style="background:rgba(201,154,58,0.16);color:' + AMBER
    + ';font-size:10px;font-weight:700;letter-spacing:.05em;padding:1px 5px;border-radius:4px;margin-right:4px">UNCONFIRMED</span>';
  const projNote = (t) => (t.oppRighty != null && t.oppLefty != null)
    ? ' <span style="color:' + AMBER + ';font-size:11px">· proj ' + t.oppRighty + 'R/'
      + t.oppLefty + 'L — awaiting lineup</span>'
    : ' <span style="color:' + AMBER + ';font-size:11px">· awaiting lineup</span>';
  // Wrap an existing label fn: inject the UNCONFIRMED badge after the bullet and
  // append the projected-count note. Reuses the same label so wRC+ chips etc.
  // render identically to a confirmed play.
  const withUnconf = (fn) => (t) => fn(t).replace(/^• /, '• ' + unconfBadge) + projNote(t);

  // Re-callable so the wRC+ chips inside the play labels refresh when the wRC+
  // table's Window selector changes (the labels read the mutable `wrcTeams`).
  // Game start time (CT) from the commence ISO, e.g. "1:10p". Also returns a
  // sortable key so plays order by first pitch.
  const gameTime = (iso) => {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d)) return '';
    return d.toLocaleTimeString('en-US', { timeZone: 'America/Chicago', hour: 'numeric', minute: '2-digit' })
      .replace(' AM', 'a').replace(' PM', 'p');
  };
  const timeChip = (iso) => '<span style="color:#888;font-size:11px;min-width:52px;display:inline-block">'
    + (gameTime(iso) || '—') + '</span> ';

  // Pitcher-vs-team pick label — fade the arm, take the opponent's ML; reason
  // tag is "vs team". Shows the take team's wRC+ vs the faded arm's hand at the
  // end, matching fadeLabel.
  const vtLabel = (t) => {
    const who = t.pitcher || '';
    return '• Fade' + handTag(who) + ' <b>' + esc(who) + '</b> (' + esc(t.arm_team || '?') + ')'
      + tag('vs team', null, '@ ')
      + ' → ' + takeSpTag(t.selection) + '<b>' + esc(t.selection || '?') + '</b>' + oddsStr(t.odds)
      + (t.matchup ? ' <span style="color:#888">· ' + esc(t.matchup) + '</span>' : '')
      + betWrcTag(t.selection, who);
  };

  function paintTodayPlays() {
    let th = '<div class="card-title" style="margin-bottom:8px">Today’s plays</div>';
    if (!pend.length && !fadeWatchPend.length && !tailPend.length && !watchPend.length
        && !vtPend.length && !tailUnconf.length && !watchUnconf.length) {
      th += '<div class="no-picks">No fade-list or hand-tails moneyline plays on today’s slate.</div>';
    } else {
      // Sort by first pitch (commence) but don't display the time.
      const byTime = (a, b) => (a.c < b.c ? -1 : a.c > b.c ? 1 : 0);
      const mk = (list, fn, color) => list.map(t => ({
        c: t.commence || '',
        html: '<div style="font-size:14px;color:' + color + ';padding:2px 0">' + fn(t) + '</div>',
      }));
      // Real bettable plays (fade-list + hand-tails), sorted by first pitch.
      const real = [...mk(pend, fadeLabel, '#ddd'), ...mk(tailPend, tailLabel, '#ddd'), ...mk(vtPend, vtLabel, '#ddd')].sort(byTime);
      // Hand-tails plays awaiting lineup confirmation — qualify on a projected
      // card, not yet a pick. Own group between real bets and the watchlist.
      const unconf = [...mk(tailUnconf, withUnconf(tailLabel), '#d8c08a'),
                      ...mk(watchUnconf, withUnconf(watchLabel), '#d8c08a')].sort(byTime);
      // WATCH plays (review-only candidates) in their OWN sorted group below.
      const watch = [...mk(fadeWatchPend, fadeWatchLabel, '#cbb8e6'), ...mk(watchPend, watchLabel, '#cbb8e6')].sort(byTime);
      th += real.length ? real.map(i => i.html).join('')
        : '<div class="no-picks">No confirmed hand-tails or fade-list plays yet.</div>';
      if (unconf.length) {
        th += '<div style="font-size:11px;color:' + AMBER + ';font-weight:700;letter-spacing:.05em;text-transform:uppercase;margin:10px 0 2px;border-top:1px solid rgba(255,255,255,0.07);padding-top:8px">Awaiting lineup confirmation — not yet a pick</div>';
        th += unconf.map(i => i.html).join('');
      }
      if (watch.length) {
        th += '<div style="font-size:11px;color:#8a7bb0;font-weight:700;letter-spacing:.05em;text-transform:uppercase;margin:10px 0 2px;border-top:1px solid rgba(255,255,255,0.07);padding-top:8px">Watch — review only, not bet</div>';
        th += watch.map(i => i.html).join('');
      }
    }
    th += '<div id="playsWrcWin" style="font-size:11px;color:#777;margin-top:6px"></div>';
    tCard.innerHTML = th;
    const w = tCard.querySelector('#playsWrcWin');
    if (w) w.textContent = 'wRC+ chips: ' + (wrcTeamsWindowLabel || 'Season') + ' (change via the wRC+ table below)';
  }
  let wrcTeamsWindowLabel = 'Last 30 days';
  paintTodayPlays();
  el.appendChild(tCard);

  // ---- Season bet log (filterable by Fade/Tail + pick + pitcher + venue) ----
  const typeTag = { ml: 'Fade', ml_dog: 'DOG', hand_fade: 'Fade', hand_take: 'TAIL' };
  // Fade-list bets (kind=fade) merged with hand-tails bets (kind=tail when the
  // pick backs the arm's own team, else fade). Normalize hand-tails fields to
  // what the log renderer expects (pitchers[], fadeTeam for the Venue filter).
  // `source` distinguishes WHY it fired: 'venue' (fade-list) vs 'handedness'
  // (hand-tails) -- a single arm can be on both lists, so the same game can
  // appear twice (one venue fade, one handedness fade).
  const fadeBets = (data.bets || []).map(b => ({ ...b, kind: 'fade', source: 'venue' }));
  const tailBets = ((tailData && tailData.bets) || []).map(b => ({
    ...b, kind: b.action === 'take' ? 'tail' : 'fade', source: 'handedness',
    pitchers: b.pitchers || [b.pitcher], fadeTeam: b.arm_team,
  }));
  // Pitcher-vs-team fades -> bet rows (source 'vs_team'). Each entry's graded
  // starts are settled bets; the today[] list are pending. Odds->stake/profit
  // uses flat 1u American-odds math (matches the fade grader).
  const vtStake = (o) => (o == null ? 0 : (o < 0 ? Math.abs(o) / 100 : 1));
  const vtProfit = (o, won) => (o == null ? 0 : (won ? (o < 0 ? 1 : o / 100) : -vtStake(o)));
  const vtSplit = (m) => { const p = (m || '').split(' @ '); return { away: p[0] || '', home: p[1] || '' }; };
  const vtSettled = ((vtData && vtData.entries) || []).flatMap(e => (e.starts || []).map(s => {
    const { away, home } = vtSplit(s.matchup);
    const won = s.result === 'win';
    return {
      date: s.date, commence: '', betType: 'ml', kind: 'fade', source: 'vs_team',
      pitchers: [e.pitcher], fadeTeam: e.arm_team, home, away,
      selection: s.selection, odds: s.opp_ml,
      result: won ? 'WIN' : 'LOSS', stake: vtStake(s.opp_ml), profit: vtProfit(s.opp_ml, won),
    };
  }));
  const vtToday = ((vtData && vtData.today) || []).map(t => {
    const { away, home } = vtSplit(t.matchup);
    return {
      date: t.date, commence: t.commence || '', betType: 'ml', kind: 'fade', source: 'vs_team',
      pitchers: [t.pitcher], fadeTeam: t.arm_team, home, away,
      selection: t.selection, odds: t.odds, result: 'PENDING', stake: 0, profit: 0,
    };
  });
  const bets = [...fadeBets, ...tailBets, ...vtSettled, ...vtToday].sort((a, b) => {
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
    + '<label style="font-size:11px;color:#888">Reason '
    + '<select id="fadeSourceSel" style="' + selCss + '"><option value="">All</option>'
    + '<option value="venue">Venue</option><option value="handedness">Handedness</option>'
    + '<option value="vs_team">Vs team</option>'
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
  const sourceSel = log.querySelector('#fadeSourceSel');
  const recEl = log.querySelector('#fadeLogRec');

  // Venue of a fade bet = the faded arm's-team side (home if his team is home).
  const venueOf = (b) => b.fadeTeam === b.home ? 'home' : (b.fadeTeam === b.away ? 'away' : '');

  // Rebuild the Pitcher dropdown to only list arms that have bets of the
  // currently selected Type (Fade/Tail). Keeps the current pick if still valid.
  function refreshPitcherOptions() {
    const tv = kindSel.value, sv = sourceSel.value;
    const ps = [...new Set(bets.filter(b => (!tv || b.kind === tv) && (!sv || b.source === sv))
      .flatMap(b => b.pitchers || []))].sort();
    const cur = pitcherSel.value;
    pitcherSel.innerHTML = '<option value="">All</option>'
      + ps.map(p => '<option value="' + esc(p) + '">' + esc(p) + '</option>').join('');
    pitcherSel.value = ps.includes(cur) ? cur : '';
  }

  const LOG_PAGE_SIZE = 50;
  let logPage = 0;   // 0-based page index into the current filtered view
  function drawRows() {
    const kv = pickSel.value, mv = monthSel.value, wv = weekSel.value, dv = dateSel.value, pv = pitcherSel.value, vv = venueSel.value, tv = kindSel.value, sv = sourceSel.value;
    const view = bets.filter(b =>
      (!tv || b.kind === tv) &&
      (!sv || b.source === sv) &&
      (!kv || b.selection === kv) &&
      (!mv || (b.date || '').slice(0, 7) === mv) &&
      (!wv || (b.date && weekStartOf(b.date) === wv)) &&
      (!dv || b.date === dv) &&
      (!pv || (b.pitchers || []).includes(pv)) &&
      (!vv || venueOf(b) === vv));
    // W-L / units for the current filter (settled only), plus home/away split
    // (by the faded arm's-team venue).
    let w = 0, l = 0, u = 0, stk = 0;
    const hs = [0, 0, 0.0], as = [0, 0, 0.0];   // [wins, losses, units]
    view.forEach(b => {
      if (b.result === 'WIN' || b.result === 'LOSS') {
        w += b.result === 'WIN'; l += b.result === 'LOSS'; u += b.profit; stk += (b.stake || 0);
        const s = venueOf(b) === 'home' ? hs : (venueOf(b) === 'away' ? as : null);
        if (s) { s[0] += b.result === 'WIN'; s[1] += b.result === 'LOSS'; s[2] += b.profit; }
      }
    });
    const roi = stk ? (u / stk * 100) : 0;
    const mini = (lbl, r) => ' <span style="color:#888;font-weight:400;font-size:12px">· ' + lbl + ' '
      + r[0] + '–' + r[1] + ' <span style="color:' + uColor(r[2]) + '">'
      + (r[2] >= 0 ? '+' : '') + r[2].toFixed(2) + 'u</span></span>';
    recEl.innerHTML = w + '–' + l
      + ' <span style="color:' + uColor(u) + '">' + (u >= 0 ? '+' : '') + u.toFixed(2) + 'u · '
      + (roi >= 0 ? '+' : '') + roi.toFixed(1) + '%</span>'
      + mini('home', hs) + mini('away', as);
    // Paginate the filtered view by 50 (the W-L/units summary above still
    // covers the WHOLE filter, not just the visible page).
    const total = view.length;
    const pages = Math.max(1, Math.ceil(total / LOG_PAGE_SIZE));
    if (logPage >= pages) logPage = pages - 1;
    if (logPage < 0) logPage = 0;
    const start = logPage * LOG_PAGE_SIZE;
    const pageView = view.slice(start, start + LOG_PAGE_SIZE);
    let rows = '';
    pageView.forEach(b => {
      const settled = b.result === 'WIN' || b.result === 'LOSS';
      const dim = settled ? '' : 'opacity:.5;';
      const resColor = b.result === 'WIN' ? GREEN : (b.result === 'LOSS' ? RED : '#888');
      const prof = settled ? ((b.profit >= 0 ? '+' : '') + b.profit.toFixed(2) + 'u') : '—';
      const profColor = !settled ? '#888' : (b.profit >= 0 ? GREEN : RED);
      // Pick shows the bet team with an "@" prefix when it's the HOME side
      // ("away @ home" notation — the @ marks the host). So "@PIT" = betting PIT
      // at home, "PIT" = betting PIT on the road.
      const sel = b.selection || '?';
      const pick = (sel === b.home ? '@' : '') + esc(sel);
      // Tail (hand-tails take) backs the arm's own team — show who it faces.
      const oppTag = (b.kind === 'tail' && b.opp_team)
        ? ' <span style="color:#888;font-weight:400;font-size:11px">vs ' + esc(b.opp_team) + '</span>' : '';
      const note = b.result === 'VOID' ? (' <span style="color:#777;font-size:10px">' + esc(b.reason || 'void') + '</span>')
        : (b.result === 'SKIP' ? ' <span style="color:#777;font-size:10px">skip</span>' : '');
      // Reason: WHY the fade fired — 'venue' (fade list), 'hand' (hand-tails),
      // or 'vs team' (pitcher-vs-team fade).
      const reason = b.source === 'handedness' ? 'hand'
        : (b.source === 'venue' ? 'venue' : (b.source === 'vs_team' ? 'vs team' : ''));
      rows += '<tr style="' + dim + '">'
        + '<td style="padding:4px 8px;color:#999">' + esc(b.date) + '</td>'
        + '<td style="padding:4px 6px;color:' + ORANGE + ';font-size:11px;font-weight:700">' + (typeTag[b.betType] || '') + '</td>'
        + '<td style="padding:4px 8px;color:#999;font-size:11px">' + reason + '</td>'
        + '<td style="padding:4px 8px">' + esc((b.pitchers || []).join(' / ')) + '</td>'
        + '<td style="padding:4px 8px;font-weight:600">' + pick + oppTag + note + '</td>'
        + '<td style="padding:4px 8px;text-align:right;color:' + ORANGE + '">' + fmtOdds(b.odds) + '</td>'
        + '<td style="padding:4px 8px;text-align:center;color:' + resColor + ';font-weight:700">' + esc(b.result) + '</td>'
        + '<td style="padding:4px 8px;text-align:right;color:' + profColor + '">' + prof + '</td>'
        + '</tr>';
    });
    const shownFrom = total ? start + 1 : 0;
    const shownTo = Math.min(start + LOG_PAGE_SIZE, total);
    const btnCss = selCss + ';cursor:pointer';
    const btnOff = selCss + ';opacity:.4;cursor:default';
    const pager = total > LOG_PAGE_SIZE
      ? '<div style="display:flex;gap:8px;align-items:center;font-size:12px;color:#888">'
        + '<button id="fadeLogPrev" ' + (logPage <= 0 ? 'disabled' : '')
        + ' style="' + (logPage <= 0 ? btnOff : btnCss) + '">‹ Prev</button>'
        + '<span>page ' + (logPage + 1) + ' / ' + pages + '</span>'
        + '<button id="fadeLogNext" ' + (logPage >= pages - 1 ? 'disabled' : '')
        + ' style="' + (logPage >= pages - 1 ? btnOff : btnCss) + '">Next ›</button>'
        + '</div>'
      : '';
    wrap.innerHTML =
      '<div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;padding:2px 8px 8px">'
      + '<span style="font-size:12px;color:#888">' + (total ? (shownFrom + '–' + shownTo + ' of ' + total) : '0') + ' bets</span>'
      + pager + '</div>'
      + '<table style="width:100%;border-collapse:collapse;font-size:13px">'
      + '<thead><tr style="color:#888;text-align:left;border-bottom:1px solid #333">'
      + '<th style="padding:4px 8px">Date</th><th style="padding:4px 6px">Type</th>'
      + '<th style="padding:4px 8px">Reason</th>'
      + '<th style="padding:4px 8px">Fade arm(s)</th><th style="padding:4px 8px">Pick</th>'
      + '<th style="padding:4px 8px;text-align:right">Odds</th>'
      + '<th style="padding:4px 8px;text-align:center">Result</th><th style="padding:4px 8px;text-align:right">P/L</th>'
      + '</tr></thead><tbody>' + rows + '</tbody></table>';
    const prevBtn = wrap.querySelector('#fadeLogPrev');
    const nextBtn = wrap.querySelector('#fadeLogNext');
    if (prevBtn) prevBtn.addEventListener('click', () => { if (logPage > 0) { logPage--; drawRows(); } });
    if (nextBtn) nextBtn.addEventListener('click', () => { if (logPage < pages - 1) { logPage++; drawRows(); } });
  }
  // Changing any filter resets to the first page before redrawing.
  const repage = () => { logPage = 0; drawRows(); };
  pickSel.addEventListener('change', repage);
  monthSel.addEventListener('change', repage);
  weekSel.addEventListener('change', repage);
  dateSel.addEventListener('change', repage);
  pitcherSel.addEventListener('change', repage);
  venueSel.addEventListener('change', repage);
  sourceSel.addEventListener('change', () => { refreshPitcherOptions(); repage(); });
  kindSel.addEventListener('change', () => { refreshPitcherOptions(); repage(); });
  drawRows();

  // ---- Fade watchlist (auto-screen: home/away fade edge, not bet) ----
  // Arms NOT on the active fade list whose home or away fade clears the bar
  // (fade_watch.py: >= minGames, qualifying side >= +minUnits). Season replay,
  // in-sample — a leaderboard to watch, not bet. Sits by the Tail watchlist.
  if (fwatchData && (fwatchData.candidates || []).length) {
    const fwc = document.createElement('div');
    fwc.className = 'card card-games';
    fwc.style.cssText = 'padding:8px 4px;margin-top:16px';
    const splitCell = (r, dim) => {
      const u = (r && r.u) || 0, n = (r && r.n) || 0;
      if (!n) return '<td style="padding:4px 8px;text-align:center;color:#666">—</td>';
      return '<td style="padding:4px 8px;text-align:center' + (dim ? ';opacity:.55' : '') + '">'
        + (r.w || 0) + '–' + (r.l || 0)
        + ' <span style="color:' + uColor(u) + '">' + (u >= 0 ? '+' : '') + u.toFixed(2) + 'u</span></td>';
    };
    const sugColor = 'var(--orange,#e8a33d)';
    const frows = (fwatchData.candidates || []).map(c => {
      // Dim the side(s) that didn't qualify (suggest names the qualifying side).
      const homeDim = c.suggest === 'away', awayDim = c.suggest === 'home';
      return '<tr><td style="padding:4px 8px;font-weight:600">' + esc(c.pitcher) + '</td>'
        + '<td style="padding:4px 6px;color:#999">' + esc(c.team || '') + '</td>'
        + '<td style="padding:4px 6px;text-align:center;color:' + sugColor + ';font-weight:700;text-transform:uppercase">' + esc(c.suggest) + '</td>'
        + splitCell(c.home, homeDim) + splitCell(c.away, awayDim) + splitCell(c.all) + '</tr>';
    }).join('');
    const mg = fwatchData.minGames, mu = fwatchData.minUnits;
    fwc.innerHTML =
      '<div style="padding:6px 8px"><span class="card-title" style="padding:0">Fade watchlist</span>'
      + '<span style="font-size:11px;color:#888;margin-left:8px">arms NOT on the fade list whose home or away fade clears ≥' + mg + ' games & +' + mu + 'u · review only, not bet · in-sample</span></div>'
      + '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:13px">'
      + '<thead><tr style="color:#888;text-align:left;border-bottom:1px solid #333">'
      + '<th style="padding:4px 8px">Arm</th><th style="padding:4px 6px">Team</th>'
      + '<th style="padding:4px 6px;text-align:center">Side</th>'
      + '<th style="padding:4px 8px;text-align:center">Home</th><th style="padding:4px 8px;text-align:center">Away</th>'
      + '<th style="padding:4px 8px;text-align:center">All</th></tr></thead><tbody>' + frows + '</tbody></table></div>';
    el.appendChild(fwc);
  }

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

  // ---- Fade vs-team watchlist (pitcher-vs-team, review-only) ----
  // Auto-screen: >= minStarts starts vs a team with minEra ≤ ERA < fadeEra
  // against them (ERA ≥ fadeEra promotes to the vs-team FADE list). fadeRecord
  // is the in-sample fade result (bet the opponent), shown for review.
  if (vtwData && (vtwData.candidates || []).length) {
    const vtc = document.createElement('div');
    vtc.className = 'card card-games';
    vtc.style.cssText = 'padding:8px 4px;margin-top:16px';
    const vrows = vtwData.candidates.map(c => {
      const r = c.fadeRecord || {};
      const u = r.u || 0, n = r.n || 0;
      const rec = n ? (r.w + '–' + r.l + ' <span style="color:' + uColor(u) + '">'
        + (u >= 0 ? '+' : '') + u.toFixed(2) + 'u</span>') : '—';
      // User-removed arms (WATCH_EXCLUDE) keep their row for history but are
      // dimmed + badged and never surface as today's plays.
      return '<tr' + (c.excluded ? ' style="opacity:.45"' : '') + '>'
        + '<td style="padding:4px 8px;font-weight:600">' + esc(c.pitcher)
          + (c.excluded ? ' <span style="font-size:10px;color:#888;font-weight:400">(removed)</span>' : '') + '</td>'
        + '<td style="padding:4px 6px;text-align:center;color:#999">' + esc(c.arm_team || '') + '</td>'
        + '<td style="padding:4px 6px;text-align:center;color:' + ORANGE + ';font-weight:700">' + esc(c.opp) + '</td>'
        + '<td style="padding:4px 8px;text-align:right;color:' + RED + ';font-weight:600">'
          + (c.eraVsOpp != null ? c.eraVsOpp.toFixed(2) : '—') + '</td>'
        + '<td style="padding:4px 8px;text-align:center;color:#999">' + (c.startsVsOpp || 0) + '</td>'
        + '<td style="padding:4px 8px;text-align:center">' + rec + '</td>'
        + '</tr>';
    }).join('');
    const ms = vtwData.minStarts, me = vtwData.minEra, fe = vtwData.fadeEra || 8;
    vtc.innerHTML =
      '<div style="padding:6px 8px"><span class="card-title" style="padding:0">Fade vs-team watchlist</span>'
      + '<span style="font-size:11px;color:#888;margin-left:8px">pitchers with ≥' + ms + ' starts vs a team & '
      + me + ' ≤ ERA &lt; ' + fe + ' vs them (≥' + fe + ' promotes to the vs-team fade list) · review only, not bet · in-sample</span></div>'
      + '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:13px">'
      + '<thead><tr style="color:#888;text-align:left;border-bottom:1px solid #333">'
      + '<th style="padding:4px 8px">Pitcher</th><th style="padding:4px 6px;text-align:center">Team</th>'
      + '<th style="padding:4px 6px;text-align:center">vs</th>'
      + '<th style="padding:4px 8px;text-align:right">ERA vs</th>'
      + '<th style="padding:4px 8px;text-align:center">GS</th>'
      + '<th style="padding:4px 8px;text-align:center">Fade (bet opp)</th></tr></thead><tbody>' + vrows + '</tbody></table></div>';
    el.appendChild(vtc);
  }

  // ---- Team wRC+ by opposing-starter hand (reference table) ----
  // Self-computed windows (build_team_woba_splits.py) — park-adjusted wRC+
  // approximation, sliceable by month / last-60..15 via the Window dropdown.
  // View-only: nothing here feeds a fade pick or grade.
  {
    // Build the list of windows: FG season snapshot first (if present), then
    // computed windows (recent first, months newest-first, computed season last).
    const MONW = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const winOpts = [];
    const wobaWins = (wobaData && wobaData.windows) || {};
    const monthKeys = Object.keys(wobaWins).filter(k => /^\d{4}-\d{2}$/.test(k)).sort().reverse();
    // Self-computed windows only (Season first, then rolling/month).
    if (wobaWins.season) winOpts.push({ key: 'season', label: 'Season' });
    if (wobaWins.asb) winOpts.push({ key: 'asb', label: 'Since All-Star break' });
    if (wobaWins.last15) winOpts.push({ key: 'last15', label: 'Last 15 days' });
    if (wobaWins.last20) winOpts.push({ key: 'last20', label: 'Last 20 days' });
    if (wobaWins.last30) winOpts.push({ key: 'last30', label: 'Last 30 days' });
    if (wobaWins.last45) winOpts.push({ key: 'last45', label: 'Last 45 days' });
    if (wobaWins.last60) winOpts.push({ key: 'last60', label: 'Last 60 days' });
    monthKeys.forEach(m => winOpts.push({ key: m, label: MONW[+m.slice(5)] + ' ' + m.slice(0, 4) }));

    if (winOpts.length) {
      const wrcCell = (v) => {
        if (v == null) return '<td style="padding:4px 10px;text-align:right;color:#666">—</td>';
        const dev = Math.max(-1, Math.min(1, (v - 100) / 30));
        const col = dev >= 0 ? GREEN : RED;
        const bg = dev >= 0
          ? 'rgba(63,185,80,' + (0.05 + 0.22 * dev).toFixed(3) + ')'
          : 'rgba(248,81,73,' + (0.05 + 0.22 * -dev).toFixed(3) + ')';
        return '<td style="padding:4px 10px;text-align:right;font-weight:600;color:'
          + col + ';background:' + bg + '">' + v + '</td>';
      };
      // Normalize either source into {team:{vsLHP,vsRHP,paL,paR}} for a window.
      const teamsFor = teamsForWindow;   // shared normalizer (defined up top)
      const allTeams = [...new Set(winOpts.flatMap(w => Object.keys(teamsFor(w.key))))].sort();

      const wrcCard = document.createElement('div');
      wrcCard.className = 'card card-games';
      wrcCard.style.cssText = 'padding:8px 4px;margin-top:16px';
      wrcCard.innerHTML =
        '<div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:6px 8px">'
        + '<span class="card-title" style="padding:0">Team wRC+ vs LHP / RHP</span>'
        + '<label style="font-size:11px;color:#888">Window '
        + '<select id="wrcWinSel" style="' + selCss + '">'
        + (function () {
          const defKey = winOpts.some(w => w.key === DEFAULT_WRC_WIN) ? DEFAULT_WRC_WIN : (winOpts[0] || {}).key;
          return winOpts.map(w => '<option value="' + esc(w.key) + '"' + (w.key === defKey ? ' selected' : '') + '>' + esc(w.label) + '</option>').join('');
        })()
        + '</select></label>'
        + '<label style="font-size:11px;color:#888">Venue '
        + '<select id="wrcVenueSel" style="' + selCss + '">'
        + '<option value="">All</option><option value="home">Home</option><option value="road">Road</option>'
        + '</select></label>'
        + '<label style="font-size:11px;color:#888">Pitcher '
        + '<select id="wrcRoleSel" style="' + selCss + '">'
        + '<option value="">All</option><option value="sp">Starters</option><option value="rp">Relievers</option>'
        + '</select></label>'
        + '<label style="font-size:11px;color:#888">Roster '
        + '<select id="wrcRosterSel" style="' + selCss + '">'
        + '<option value="">Whole team</option><option value="lineup">Confirmed lineup</option>'
        + '</select></label>'
        + '<label style="font-size:11px;color:#888">Team '
        + '<select id="wrcTeamSel" style="' + selCss + '"><option value="">All</option>'
        + allTeams.map(t => '<option value="' + esc(t) + '">' + esc(t) + '</option>').join('')
        + '</select></label>'
        + '</div>'
        + '<div id="wrcMatchups" style="display:flex;gap:6px;flex-wrap:wrap;padding:0 8px 6px"></div>'
        + '<div id="wrcNote" style="font-size:11px;color:#888;padding:0 8px 6px"></div>'
        + '<div id="wrcWrap" style="overflow-x:auto"></div>';
      // Place the wRC+ card between Today's plays and the Season bet log.
      el.insertBefore(wrcCard, log);

      const wrcWrap = wrcCard.querySelector('#wrcWrap');
      const wrcTeamSel = wrcCard.querySelector('#wrcTeamSel');
      const wrcWinSel = wrcCard.querySelector('#wrcWinSel');
      const wrcVenueSel = wrcCard.querySelector('#wrcVenueSel');
      const wrcRoleSel = wrcCard.querySelector('#wrcRoleSel');
      const wrcRosterSel = wrcCard.querySelector('#wrcRosterSel');
      const wrcNote = wrcCard.querySelector('#wrcNote');
      const wrcMatchupsEl = wrcCard.querySelector('#wrcMatchups');
      // Today's slate -> matchup filter buttons. Click one to show only that
      // game's two teams; "All" clears it.
      const todayGames = ((allmlData && allmlData.today) || []).filter(g => g.away && g.home);
      let wrcMatchup = null;   // today's game object (has hands) or null
      let wrcSort = { col: 'team', dir: 1 };
      const arrow = (c) => wrcSort.col === c ? (wrcSort.dir > 0 ? ' ▲' : ' ▼') : '';

      function drawWrc() {
        const win = wrcWinSel.value;
        const roster = wrcRosterSel.value;
        const isLineup = roster === 'lineup';
        const venue = wrcVenueSel.value;
        const role = wrcRoleSel.value;
        const teams = teamsForWindow(win, venue, role, roster);
        const venLbl = venue === 'home' ? 'home games · ' : (venue === 'road' ? 'road games · ' : '');
        const roleLbl = role === 'sp' ? 'vs starters only · ' : (role === 'rp' ? 'vs relievers only · ' : '');
        // PA hint so small samples are visible.
        const paTag = (n) => (n == null) ? '' : ' <span style="color:#666;font-weight:400;font-size:10px">(' + n + ')</span>';
        const noteBase = venLbl + roleLbl + (isLineup
          ? ('<b>Confirmed lineup wRC+</b> — tonight’s posted 9 per team, pooled over the selected '
            + 'window (park-neutral) · confirmed lineups only · through ' + esc(wobaData.throughDate || '?')
            + ' · (n) = PA · 100 = league avg · view-only')
          : ('Self-computed <b>park-adjusted wRC+</b> vs every pitcher faced (starters + relievers; '
            + 'PA-weighted by parks; ≈FG ±6 pts) · '
            + 'through ' + esc(wobaData.throughDate || '?') + ' · (n) = PA · 100 = league avg · view-only'));

        // FOCUSED matchup mode: a game is selected AND both starters' hands are
        // known -> show ONLY each team vs the hand of the pitcher it faces.
        const g = wrcMatchup;
        const knownHand = (h) => h === 'L' || h === 'R';
        if (g && knownHand(g.away_hand) && knownHand(g.home_hand)) {
          const sides = [
            { team: g.away, faceHand: g.home_hand, sp: g.home_pitcher },   // away bats vs home SP
            { team: g.home, faceHand: g.away_hand, sp: g.away_pitcher },   // home bats vs away SP
          ];
          const rows = sides.map(s => {
            const tm = teams[s.team] || {};
            const isL = s.faceHand === 'L';
            return '<tr><td style="padding:4px 10px;font-weight:600">' + esc(s.team) + '</td>'
              + '<td style="padding:4px 10px;color:#bbb">' + esc(s.sp || '?')
              + ' <span style="color:#7aa2d6;font-size:11px;font-weight:600">(' + s.faceHand + 'HP)</span></td>'
              + wrcCell(isL ? tm.vsLHP : tm.vsRHP).replace('</td>', paTag(isL ? tm.paL : tm.paR) + '</td>') + '</tr>';
          }).join('');
          wrcWrap.innerHTML =
            '<table style="width:100%;border-collapse:collapse;font-size:13px">'
            + '<thead><tr style="color:#888;text-align:left;border-bottom:1px solid #333">'
            + '<th style="padding:4px 10px">Team</th>'
            + '<th style="padding:4px 10px">Opposing starter</th>'
            + '<th style="padding:4px 10px;text-align:right">wRC+ vs their hand</th>'
            + '</tr></thead><tbody>' + rows + '</tbody></table>';
          wrcNote.innerHTML = '<b>' + esc(g.away) + ' @ ' + esc(g.home) + '</b> — each team vs the hand it faces · ' + noteBase;
          return;
        }

        // Normal table (All, single team, or matchup with unknown hands).
        const only = wrcTeamSel.value;
        let list = Object.keys(teams).map(t => ({ team: t, ...teams[t] }));
        if (g) list = list.filter(r => r.team === g.away || r.team === g.home);
        if (only) list = list.filter(r => r.team === only);
        list.sort((a, b) => {
          let av, bv;
          if (wrcSort.col === 'team') { av = a.team; bv = b.team; }
          else { av = a[wrcSort.col] == null ? -Infinity : a[wrcSort.col]; bv = b[wrcSort.col] == null ? -Infinity : b[wrcSort.col]; }
          return av < bv ? -wrcSort.dir : av > bv ? wrcSort.dir : 0;
        });
        const rows = list.map(r =>
          '<tr><td style="padding:4px 10px;font-weight:600">' + esc(r.team) + '</td>'
          + wrcCell(r.vsLHP).replace('</td>', paTag(r.paL) + '</td>')
          + wrcCell(r.vsRHP).replace('</td>', paTag(r.paR) + '</td>') + '</tr>').join('');
        wrcWrap.innerHTML =
          '<table style="width:100%;border-collapse:collapse;font-size:13px">'
          + '<thead><tr style="color:#888;text-align:left;border-bottom:1px solid #333">'
          + '<th data-c="team" style="padding:4px 10px;cursor:pointer">Team' + arrow('team') + '</th>'
          + '<th data-c="vsLHP" style="padding:4px 10px;text-align:right;cursor:pointer">wRC+ vs LHP' + arrow('vsLHP') + '</th>'
          + '<th data-c="vsRHP" style="padding:4px 10px;text-align:right;cursor:pointer">wRC+ vs RHP' + arrow('vsRHP') + '</th>'
          + '</tr></thead><tbody>' + rows + '</tbody></table>';
        wrcNote.innerHTML = noteBase;
        wrcWrap.querySelectorAll('th[data-c]').forEach(th => th.addEventListener('click', () => {
          const c = th.getAttribute('data-c');
          if (wrcSort.col === c) wrcSort.dir *= -1;
          else wrcSort = { col: c, dir: c === 'team' ? 1 : -1 };
          drawWrc();
        }));
      }
      // Matchup filter buttons (today's slate). Active button highlighted.
      function renderMatchupBtns() {
        if (!todayGames.length) { wrcMatchupsEl.style.display = 'none'; return; }
        const btnBase = 'font-size:11px;padding:3px 9px;border-radius:12px;border:1px solid #333;background:#1b1b1b;color:#ccc;cursor:pointer;white-space:nowrap';
        const btnOn = 'font-size:11px;padding:3px 9px;border-radius:12px;border:1px solid var(--blue,#4c9be8);background:rgba(76,155,232,0.20);color:#fff;cursor:pointer;white-space:nowrap';
        const cur = wrcMatchup ? (wrcMatchup.away + '@' + wrcMatchup.home) : '';
        const btns = [{ label: 'All', game: null }]
          .concat(todayGames.map(g => ({ label: g.away + ' vs ' + g.home, game: g })));
        wrcMatchupsEl.innerHTML = btns.map((b, i) => {
          const on = (b.game ? (b.game.away + '@' + b.game.home) : '') === cur;
          return '<button data-i="' + i + '" style="' + (on ? btnOn : btnBase) + '">' + esc(b.label) + '</button>';
        }).join('');
        wrcMatchupsEl.querySelectorAll('button').forEach(btn => btn.addEventListener('click', () => {
          const b = btns[+btn.getAttribute('data-i')];
          wrcMatchup = b.game;
          if (b.game) wrcTeamSel.value = '';   // matchup and single-team filters are exclusive
          renderMatchupBtns();
          drawWrc();
        }));
      }
      wrcTeamSel.addEventListener('change', () => { if (wrcTeamSel.value) { wrcMatchup = null; renderMatchupBtns(); } drawWrc(); });
      // Changing the window also re-points the Today's-plays wRC+ chips at that
      // window and repaints them.
      wrcWinSel.addEventListener('change', () => {
        wrcTeams = teamsForWindow(wrcWinSel.value);
        wrcTeamsWindowLabel = (winOpts.find(w => w.key === wrcWinSel.value) || {}).label || wrcWinSel.value;
        paintTodayPlays();
        drawWrc();
      });
      wrcVenueSel.addEventListener('change', drawWrc);
      wrcRoleSel.addEventListener('change', drawWrc);
      wrcRosterSel.addEventListener('change', drawWrc);
      renderMatchupBtns();
      drawWrc();
    }
  }
}

if (typeof esc === 'undefined') {
  window.esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
