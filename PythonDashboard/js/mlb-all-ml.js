// PythonDashboard/js/mlb-all-ml.js
// Renderer for the "MLB ALL ML" tab. Reads mlb-all-ml.json (build_all_ml.py):
// every settled game this season with both teams' FanDuel closing moneylines,
// both starters + throwing hand, and the result.
//
// Each game is pivoted into two TEAM-SIDE rows (home side, away side) so you
// can filter by team, home/away, favorite/underdog, and opposing-starter
// handedness in any combination, then grade betting that team's moneyline
// (flat 1u: risk-to-win-1u on negatives, risk-1u on positives).

async function renderMLBAllML() {
  const el = document.getElementById('content');
  el.innerHTML = '<div class="loading"><div class="spinner"></div><br>Loading all ML...</div>';

  const local = 'data/mlb-all-ml.json';
  const remote = 'https://raw.githubusercontent.com/sspam1189-stack/Model/main/MLBstrikeouts/data/mlb-all-ml.json';
  let data = null;
  for (const url of [local + '?t=' + Date.now(), remote + '?t=' + Date.now()]) {
    try { const r = await fetch(url, { cache: 'no-store' }); if (r.ok) { data = await r.json(); break; } }
    catch (e) { /* next */ }
  }

  el.textContent = '';
  if (!data || !Array.isArray(data.games)) {
    const c = document.createElement('div');
    c.className = 'card card-games';
    c.innerHTML = '<div class="card-title">2026 MLB Games</div>'
      + '<div class="no-picks" style="padding:20px 0 6px">Unable to load the all-ML feed.</div>';
    el.appendChild(c);
    return;
  }

  const runEl = document.getElementById('last-run-info');
  if (runEl && data.generated) {
    const d = new Date(data.generated);
    runEl.textContent = 'Last run (CT) — 2026 MLB Games: '
      + d.toLocaleString('en-US', { timeZone: 'America/Chicago', month: '2-digit', day: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: true });
  }

  const BLUE = 'var(--blue,#4c9be8)';
  const GREEN = 'var(--green,#3fb950)', RED = 'var(--red,#f85149)';
  const esc = (s) => (s == null ? '' : String(s).replace(/[&<>"]/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])));
  const fmtOdds = (o) => (o == null ? '—' : (o > 0 ? '+' + o : '' + o));
  const uColor = (u) => (u > 0 ? GREEN : (u < 0 ? RED : '#aaa'));
  const stakeFor = (o) => (o < 0 ? Math.abs(o) / 100 : 1);
  const profitFor = (o, won) => (won ? (o < 0 ? 1 : o / 100) : -stakeFor(o));

  // ---- Pivot each game into its two team-side views, kept PAIRED by game.
  // The log shows ONE row per game: the active filters select which side to
  // grade; when nothing distinguishes the two sides (e.g. no team/role/venue
  // filter) the home side is shown so the table and record stay one-per-game.
  const gamePairs = [];
  const allSides = [];
  for (const g of data.games) {
    if (g.home_ml == null || g.away_ml == null || g.home_win == null) continue;
    const gameFields = {
      date: g.date, home: g.home, away: g.away,
      homeML: g.home_ml, awayML: g.away_ml,
      awaySP: g.away_pitcher, homeSP: g.home_pitcher,
      awaySPHand: g.away_hand, homeSPHand: g.home_hand,
      awayScore: g.away_score, homeScore: g.home_score,
      totalLine: g.total_line, overML: g.over_ml, underML: g.under_ml,
      winner: g.home_win ? g.home : g.away, homeWin: g.home_win === true,
    };
    const home = {
      ...gameFields,
      team: g.home, opp: g.away, venue: 'home',
      teamML: g.home_ml, oppML: g.away_ml,
      teamSP: g.home_pitcher, oppSP: g.away_pitcher, oppHand: g.away_hand,
      won: g.home_win === true,
    };
    const away = {
      ...gameFields,
      team: g.away, opp: g.home, venue: 'away',
      teamML: g.away_ml, oppML: g.home_ml,
      teamSP: g.away_pitcher, oppSP: g.home_pitcher, oppHand: g.home_hand,
      won: g.home_win === false,
    };
    home.isFav = home.teamML < home.oppML;   // strict; pick'em -> dog
    away.isFav = away.teamML < away.oppML;
    gamePairs.push([home, away]);
    allSides.push(home, away);
  }
  const sides = allSides; // used only to derive dropdown option lists below
  const teams = [...new Set(sides.map(s => s.team))].sort();

  // Starting-pitcher options: each side's own starter (teamSP). Keyed by team
  // so the pitcher dropdown can narrow to just that club's arms when a team is
  // selected; also flattened to every starter for the no-team "search all" case.
  const pitchersByTeam = {};
  for (const s of sides) {
    if (!s.teamSP) continue;
    (pitchersByTeam[s.team] || (pitchersByTeam[s.team] = new Set())).add(s.teamSP);
  }
  const allPitchers = [...new Set(sides.map(s => s.teamSP).filter(Boolean))].sort();

  // Month / week helpers (mirror the Fade-ML tab; computed in UTC from the
  // plain ISO date so they're timezone-independent).
  const MON = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const monthLabel = (ym) => { const [y, m] = ym.split('-'); return MON[+m] + ' ' + y; };
  const weekStartOf = (iso) => {
    const [y, m, d] = iso.split('-').map(Number);
    const dt = new Date(Date.UTC(y, m - 1, d));
    const dow = dt.getUTCDay();
    dt.setUTCDate(dt.getUTCDate() + (dow === 0 ? -6 : 1 - dow)); // back to Monday
    return dt.toISOString().slice(0, 10);
  };
  const weekLabel = (mondayIso) => {
    const [y, m, d] = mondayIso.split('-').map(Number);
    const sun = new Date(Date.UTC(y, m - 1, d + 6));
    return MON[m] + ' ' + d + ' – ' + MON[sun.getUTCMonth() + 1] + ' ' + sun.getUTCDate() + ', ' + sun.getUTCFullYear();
  };
  for (const s of sides) s.week = weekStartOf(s.date);
  const months = [...new Set(sides.map(s => (s.date || '').slice(0, 7)).filter(Boolean))].sort().reverse();
  const weeks = [...new Set(sides.map(s => s.week).filter(Boolean))].sort().reverse();
  const dates = [...new Set(sides.map(s => s.date).filter(Boolean))].sort().reverse();
  const dateLabel = (iso) => { const [y, m, d] = iso.split('-').map(Number); return MON[m] + ' ' + d + ', ' + y; };

  const ROW_CAP = 500;
  const selCss = 'background:#1b1b1b;color:#ddd;border:1px solid #333;border-radius:6px;padding:4px 8px;font-size:12px';

  // ---- Banner ----
  const banner = document.createElement('div');
  banner.className = 'card';
  banner.style.cssText = 'margin-bottom:14px;border:1px solid ' + BLUE + ';background:rgba(76,155,232,0.08);padding:14px 18px';
  banner.innerHTML =
    '<div style="font-weight:600;color:' + BLUE + ';margin-bottom:6px">All games moneyline — filter & grade</div>'
    + '<div style="font-size:12px;color:#aaa">Every settled game this season (' + data.games.length
    + ' games, one row each). Filter by team, opponent, month, week, day, opposing-starter hand, '
    + 'favorite/underdog, and venue in any combination; the record grades betting that team’s '
    + 'closing moneyline at flat 1u. With no team/role/venue filter, the home side is shown.</div>'
    + '<div id="allMLBig" style="display:flex;gap:22px;flex-wrap:wrap;align-items:center;margin-top:12px"></div>';
  el.appendChild(banner);

  // ---- Today's games + odds (all games on the current slate; graded once
  // final). Odds are the FanDuel lines the Fade pipeline already caches. ----
  const today = Array.isArray(data.today) ? data.today : [];
  if (today.length) {
    const tcard = document.createElement('div');
    tcard.className = 'card card-games';
    tcard.style.cssText = 'padding:8px 4px;margin-bottom:14px';
    const hnd = (h) => (h ? ' <span style="color:#888">(' + h + ')</span>' : '');
    const trows = today.map(g => {
      const done = g.final && g.home_win != null;
      const result = done ? (g.home_win ? g.home : g.away) + ' won' : 'pending';
      const aWon = done && g.home_win === false, hWon = done && g.home_win === true;
      const sideTxt = (txt, won) =>
        '<span style="color:' + (won ? GREEN : '#ccc') + ';font-weight:' + (won ? 700 : 400) + '">' + txt + '</span>';
      // Matchup: scoreline "ARI 3 - 0 PIT" once final, else "ARI @ PIT".
      const matchup = done
        ? sideTxt(esc(g.away) + ' ' + esc(g.away_score), aWon)
          + '<span style="color:#666"> - </span>'
          + sideTxt(esc(g.home_score) + ' ' + esc(g.home), hWon)
        : '<span style="font-weight:600">' + esc(g.away) + ' @ ' + esc(g.home) + '</span>';
      // Total: closing line, green if the game went Over, red if Under (once final).
      const runsTot = (g.away_score != null && g.home_score != null) ? g.away_score + g.home_score : null;
      let totalCell = (g.total_line != null ? esc(g.total_line) : '—');
      if (g.total_line != null && runsTot != null) {
        const col = runsTot > g.total_line ? GREEN : (runsTot < g.total_line ? RED : '#aaa');
        totalCell = '<span style="color:' + col + ';font-weight:700">' + esc(g.total_line) + '</span>';
      }
      const ouOdds = g.total_line == null ? '—'
        : '<span style="color:#aaa">O ' + fmtOdds(g.over_ml) + ' / U ' + fmtOdds(g.under_ml) + '</span>';
      return '<tr style="border-top:1px solid #222">'
        + '<td style="padding:4px 8px;white-space:nowrap">' + matchup + '</td>'
        + '<td style="padding:4px 8px;text-align:right">' + fmtOdds(g.away_ml) + '</td>'
        + '<td style="padding:4px 8px;text-align:right">' + fmtOdds(g.home_ml) + '</td>'
        + '<td style="padding:4px 8px;text-align:center">' + totalCell + '</td>'
        + '<td style="padding:4px 8px;text-align:center;white-space:nowrap">' + ouOdds + '</td>'
        + '<td style="padding:4px 8px;white-space:nowrap">' + esc(g.away_pitcher || '?') + hnd(g.away_hand)
          + ' <span style="color:#666">vs</span> ' + esc(g.home_pitcher || '?') + hnd(g.home_hand) + '</td>'
        + '<td style="padding:4px 8px;color:' + (done ? GREEN : '#888') + '">' + esc(result) + '</td>'
        + '</tr>';
    }).join('');
    const thead = '<tr style="text-align:left;color:#888;font-size:11px">'
      + '<th style="padding:4px 8px">Matchup</th>'
      + '<th style="padding:4px 8px;text-align:right">Away ML</th>'
      + '<th style="padding:4px 8px;text-align:right">Home ML</th>'
      + '<th style="padding:4px 8px;text-align:center">Total</th>'
      + '<th style="padding:4px 8px;text-align:center">O/U odds</th>'
      + '<th style="padding:4px 8px">Pitchers (away vs home)</th>'
      + '<th style="padding:4px 8px">Result</th></tr>';
    tcard.innerHTML = '<div class="card-title" style="padding:6px 8px">Today’s games &amp; odds — '
      + esc(today[0].date) + ' (' + today.length + ')</div>'
      + '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:12px">'
      + '<thead>' + thead + '</thead><tbody>' + trows + '</tbody></table></div>';
    el.appendChild(tcard);
  }

  // ---- Filter bar ----
  const card = document.createElement('div');
  card.className = 'card card-games';
  card.style.cssText = 'padding:8px 4px';
  const opt = (v, label, sel) => '<option value="' + esc(v) + '"' + (sel ? ' selected' : '') + '>' + esc(label) + '</option>';
  card.innerHTML =
    '<div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:6px 8px">'
    + '<div class="card-title" style="padding:0">Game log</div>'
    + '<span id="allMLRec" style="font-size:13px;font-weight:700"></span>'
    + '</div>'
    // Row 1: Team / Opponent
    + '<div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:0 8px 6px">'
    + '<label style="font-size:11px;color:#888">Team '
    + '<select id="amTeam" style="' + selCss + '">' + opt('', 'All teams', true)
    + teams.map(t => opt(t, t)).join('') + '</select></label>'
    + '<label style="font-size:11px;color:#888">Opponent '
    + '<select id="amOpp" style="' + selCss + '">' + opt('', 'All', true)
    + teams.map(t => opt(t, t)).join('') + '</select></label>'
    + '</div>'
    // Row 2: Starter / Opp starter
    + '<div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:0 8px 6px">'
    + '<label style="font-size:11px;color:#888">Starter '
    + '<select id="amPitcher" style="' + selCss + '">' + opt('', 'All pitchers', true)
    + allPitchers.map(p => opt(p, p)).join('') + '</select></label>'
    + '<label style="font-size:11px;color:#888">Opp starter '
    + '<select id="amHand" style="' + selCss + '">' + opt('', 'All', true)
    + opt('L', 'vs LHP') + opt('R', 'vs RHP') + '</select></label>'
    + '</div>'
    // Row 3: Month / Week / Day
    + '<div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:0 8px 6px">'
    + '<label style="font-size:11px;color:#888">Month '
    + '<select id="amMonth" style="' + selCss + '">' + opt('', 'All', true)
    + months.map(m => opt(m, monthLabel(m))).join('') + '</select></label>'
    + '<label style="font-size:11px;color:#888">Week '
    + '<select id="amWeek" style="' + selCss + '">' + opt('', 'All', true)
    + weeks.map(w => opt(w, weekLabel(w))).join('') + '</select></label>'
    + '<label style="font-size:11px;color:#888">Day '
    + '<select id="amDay" style="' + selCss + '">' + opt('', 'All', true)
    + dates.map(dt => opt(dt, dateLabel(dt))).join('') + '</select></label>'
    + '</div>'
    // Row 4: Role / O/U / Venue
    + '<div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:0 8px 6px">'
    + '<label style="font-size:11px;color:#888">Role '
    + '<select id="amRole" style="' + selCss + '">' + opt('', 'All', true)
    + opt('fav', 'Favorite') + opt('dog', 'Underdog') + '</select></label>'
    + '<label style="font-size:11px;color:#888">O/U '
    + '<select id="amOU" style="' + selCss + '">' + opt('', 'All', true)
    + opt('over', 'Over') + opt('under', 'Under') + opt('push', 'Push') + '</select></label>'
    + '<label style="font-size:11px;color:#888">Venue '
    + '<select id="amVenue" style="' + selCss + '">' + opt('', 'All', true)
    + opt('home', 'Home') + opt('away', 'Away') + '</select></label>'
    + '</div>'
    + '<div id="allMLWrap" style="overflow-x:auto"></div>';
  el.appendChild(card);

  const teamSel = card.querySelector('#amTeam');
  const oppSel = card.querySelector('#amOpp');
  const monthSel = card.querySelector('#amMonth');
  const weekSel = card.querySelector('#amWeek');
  const daySel = card.querySelector('#amDay');
  const handSel = card.querySelector('#amHand');
  const pitcherSel = card.querySelector('#amPitcher');
  const roleSel = card.querySelector('#amRole');
  const ouSel = card.querySelector('#amOU');
  const venueSel = card.querySelector('#amVenue');
  const wrap = card.querySelector('#allMLWrap');
  const recEl = card.querySelector('#allMLRec');
  const bigEl = banner.querySelector('#allMLBig');

  const bigStat = (label, val, color) =>
    '<div style="text-align:center;min-width:78px">'
    + '<div style="font-size:22px;font-weight:700;color:' + (color || '#eee') + '">' + val + '</div>'
    + '<div style="font-size:11px;color:#aaa;text-transform:uppercase;letter-spacing:.04em">' + label + '</div></div>';

  function draw() {
    const tv = teamSel.value, ov = oppSel.value, mv = monthSel.value, wv = weekSel.value,
      dv = daySel.value, hv = handSel.value, pv = pitcherSel.value, rv = roleSel.value,
      ouv = ouSel.value, vv = venueSel.value;
    // Over/Under result for a side: total runs vs the closing line (null if
    // the game has no line or no final score).
    const ouOf = (s) => (s.totalLine == null || s.awayScore == null || s.homeScore == null) ? null
      : ((s.awayScore + s.homeScore) > s.totalLine ? 'over'
        : ((s.awayScore + s.homeScore) < s.totalLine ? 'under' : 'push'));
    const passes = (s) =>
      (!tv || s.team === tv) &&
      (!ov || s.opp === ov) &&
      (!mv || (s.date || '').slice(0, 7) === mv) &&
      (!wv || s.week === wv) &&
      (!dv || s.date === dv) &&
      (!hv || s.oppHand === hv) &&
      (!pv || s.teamSP === pv) &&
      (!rv || (rv === 'fav' ? s.isFav : !s.isFav)) &&
      (!ouv || ouOf(s) === ouv) &&
      (!vv || s.venue === vv);
    // One row per game: keep the eligible side; if both sides qualify (no
    // filter distinguishes them) show the home side so table == record.
    const view = [];
    for (const [home, away] of gamePairs) {
      const eh = passes(home), ea = passes(away);
      if (eh && ea) view.push(home);
      else if (eh) view.push(home);
      else if (ea) view.push(away);
    }

    let wins = 0, losses = 0, units = 0, staked = 0;
    for (const s of view) {
      if (s.won) wins++; else losses++;
      units += profitFor(s.teamML, s.won);
      staked += stakeFor(s.teamML);
    }
    const n = wins + losses;
    const wr = n ? (wins / n * 100) : 0;
    const roi = staked ? (units / staked * 100) : 0;

    bigEl.innerHTML =
      bigStat('Record', wins + '-' + losses)
      + bigStat('Win %', n ? wr.toFixed(1) + '%' : '—')
      + bigStat('Units', (units >= 0 ? '+' : '') + units.toFixed(2) + 'u', uColor(units))
      + bigStat('ROI', n ? (roi >= 0 ? '+' : '') + roi.toFixed(1) + '%' : '—', uColor(units));
    recEl.textContent = wins + '-' + losses + ' · ' + (units >= 0 ? '+' : '') + units.toFixed(2) + 'u'
      + (n ? ' · ' + roi.toFixed(1) + '% ROI' : '');
    recEl.style.color = uColor(units);

    const rows = view.slice().reverse().slice(0, ROW_CAP); // newest first
    const body = rows.map(s => {
      const awayWon = !s.homeWin, homeWon = s.homeWin;
      const winColor = '#eee';
      const td = 'padding:4px 8px;text-align:center';
      const hnd = (h) => (h ? ' <span style="color:#888">(' + h + ')</span>' : '');
      const pitchers = esc(s.awaySP || '?') + hnd(s.awaySPHand)
        + ' <span style="color:#666">vs</span> ' + esc(s.homeSP || '?') + hnd(s.homeSPHand);
      const scored = s.awayScore != null && s.homeScore != null;
      // Score sits in its own cell between the Away and Home team columns, so
      // the row reads "ARI  3 - 0  PIT"; the winning side's run total is green.
      const runCell = (v, won) =>
        '<span style="color:' + (won ? GREEN : '#ccc') + ';font-weight:' + (won ? 700 : 400) + '">' + esc(v) + '</span>';
      const scoreCell = scored
        ? runCell(s.awayScore, awayWon) + '<span style="color:#666"> - </span>' + runCell(s.homeScore, homeWon)
        : '—';
      // Total: the closing O/U line, green if the game went Over it, red if
      // Under, gray on a push. Grade = total runs vs the line.
      const runsTot = scored ? s.awayScore + s.homeScore : null;
      let totalCell = '—';
      if (s.totalLine != null) {
        const col = runsTot == null ? '#ccc'
          : (runsTot > s.totalLine ? GREEN : (runsTot < s.totalLine ? RED : '#aaa'));
        totalCell = '<span style="color:' + col + ';font-weight:700">' + esc(s.totalLine) + '</span>';
      }
      const ouOdds = s.totalLine == null ? '—'
        : '<span style="color:#aaa">O ' + fmtOdds(s.overML) + ' / U ' + fmtOdds(s.underML) + '</span>';
      return '<tr style="border-top:1px solid #222">'
        + '<td style="' + td + ';white-space:nowrap;color:#aaa">' + esc(s.date) + '</td>'
        + '<td style="' + td + ';white-space:nowrap;color:#888">' + esc(s.away) + ' @ ' + esc(s.home) + '</td>'
        + '<td style="' + td + '">' + fmtOdds(s.awayML) + '</td>'
        + '<td style="' + td + ';font-weight:600;color:' + (awayWon ? GREEN : '#ccc') + '">' + esc(s.away) + '</td>'
        + '<td style="' + td + ';white-space:nowrap">' + scoreCell + '</td>'
        + '<td style="' + td + ';font-weight:600;color:' + (homeWon ? GREEN : '#ccc') + '">' + esc(s.home) + '</td>'
        + '<td style="' + td + '">' + fmtOdds(s.homeML) + '</td>'
        + '<td style="' + td + ';white-space:nowrap">' + totalCell + '</td>'
        + '<td style="' + td + ';white-space:nowrap">' + ouOdds + '</td>'
        + '<td style="' + td + ';white-space:nowrap">' + pitchers + '</td>'
        + '<td style="' + td + ';font-weight:700;color:' + winColor + '">' + esc(s.winner) + '</td>'
        + '</tr>';
    }).join('');
    const head = '<tr style="text-align:center;color:#888;font-size:11px">'
      + '<th style="padding:4px 8px">Date</th><th style="padding:4px 8px">Matchup</th>'
      + '<th style="padding:4px 8px">Away odds</th>'
      + '<th style="padding:4px 8px">Away</th>'
      + '<th style="padding:4px 8px">Score</th>'
      + '<th style="padding:4px 8px">Home</th>'
      + '<th style="padding:4px 8px">Home odds</th>'
      + '<th style="padding:4px 8px">Total</th>'
      + '<th style="padding:4px 8px">O/U odds</th>'
      + '<th style="padding:4px 8px">Pitchers (away vs home)</th>'
      + '<th style="padding:4px 8px">Winner</th></tr>';
    const note = view.length > ROW_CAP
      ? '<div style="padding:6px 8px;color:#888;font-size:11px">Showing newest ' + ROW_CAP
        + ' of ' + view.length + ' games (record above reflects all ' + view.length + ').</div>'
      : '';
    wrap.innerHTML = note + '<table style="width:100%;border-collapse:collapse;font-size:12px">'
      + '<thead>' + head + '</thead><tbody>' + body + '</tbody></table>';
  }

  // Repopulate the Starter dropdown to match the Team filter: a specific team
  // narrows it to that club's arms, "All teams" restores every pitcher. The
  // current pick is kept if it still belongs to the new list, else cleared.
  function fillPitchers() {
    const team = teamSel.value;
    const cur = pitcherSel.value;
    const list = (team && pitchersByTeam[team]) ? [...pitchersByTeam[team]].sort() : allPitchers;
    const keep = list.includes(cur) ? cur : '';
    pitcherSel.innerHTML = opt('', 'All pitchers', !keep)
      + list.map(p => opt(p, p, p === keep)).join('');
    pitcherSel.value = keep;
  }
  teamSel.addEventListener('change', fillPitchers);

  [teamSel, oppSel, monthSel, weekSel, daySel, handSel, pitcherSel, roleSel, ouSel, venueSel].forEach(s => s.addEventListener('change', draw));
  draw();
}
