// NFL Player Props rendering
    async function renderNFLProps() {
      const el = document.getElementById('content');
      const data = await fetchData('nfl-props');
      if (!data || !data.props || !data.props.length) {
        el.textContent = '';
        const card = document.createElement('div');
        card.className = 'card card-games';
        card.appendChild(Object.assign(document.createElement('div'), {className:'card-title', textContent:'NFL Player Props'}));
        card.appendChild(Object.assign(document.createElement('div'), {className:'no-picks', textContent:'No prop projections available yet. Run the props pipeline to generate projections.'}));
        el.appendChild(card);
        return;
      }

      const marketLabels = {pass_yds:'PaYd', pass_tds:'pTD', rush_yds:'RuYd', rush_att:'RuAt', rec_yds:'RecY', receptions:'Rec', pass_att:'PassAtt'};
      let picks = data.props.filter(p => p.pick !== 'PASS');
      const isBacktest = picks.some(p => p.result != null);

      // Sort by week ascending
      if (isBacktest && picks.length > 0) {
        picks.sort((a, b) => {
          const wA = (a.season || 0) * 100 + (a.week || 0);
          const wB = (b.season || 0) * 100 + (b.week || 0);
          return wB - wA;
        });
      }

      const grouped = {};
      for (const p of picks) {
        const ml = marketLabels[p.market] || p.market;
        if (!grouped[ml]) grouped[ml] = [];
        grouped[ml].push(p);
      }

      el.textContent = '';

      // Header card
      const hdr = document.createElement('div');
      hdr.className = 'card card-games';
      hdr.style.marginBottom = '16px';
      const allPicks = data.props.filter(p => p.pick !== 'PASS');
      const allW = allPicks.filter(p => p.result === 'WIN').length;
      const allL = allPicks.filter(p => p.result === 'LOSS').length;
      const allU = allW * 1.0 + allL * (-1.1);
      const allPct = (allW + allL) > 0 ? (allW / (allW + allL) * 100).toFixed(1) : '0';
      const fullSummary = `${allW}W-${allL}L (${allPct}%) ${allU >= 0 ? '+' : ''}${allU.toFixed(1)}u`;
      const hasGraded = isBacktest && (allW + allL) > 0;
      hdr.appendChild(Object.assign(document.createElement('div'), {className:'card-title', textContent:`NFL Player Props \u2014 ${data.season || ''}`}));
      hdr.appendChild(Object.assign(document.createElement('div'), {className:'card-subtitle', textContent:`${fullSummary} | Generated: ${(data.generated || '').slice(0,16)}`}));
      el.appendChild(hdr);

      // Market breakdown placeholder (rendered dynamically inside renderNFLPropsTable)

      const headers = isBacktest
        ? ['Wk','Player','Proj','Line','Actual','Pick','Result','Conf']
        : ['Player','Team','vs','Proj','Line','Pick','Conf'];
      const colClasses = isBacktest
        ? ['col-wk','col-player','col-proj','col-line','col-actual','col-pick','col-result','col-conf']
        : ['col-player','col-team','col-opp','col-proj','col-line','col-pick','col-conf'];

      // ── Unified Toolbar ──
      const selStyle = 'padding:6px 12px;border-radius:6px;background:rgba(255,255,255,0.06);color:#fff;border:1px solid rgba(255,255,255,0.1);font-size:13px;outline:none';
      const pillStyle = 'padding:5px 14px;border-radius:16px;border:1px solid rgba(255,255,255,0.12);background:transparent;color:#999;font-size:12px;cursor:pointer;transition:all 0.15s';
      const pillActiveStyle = 'padding:5px 14px;border-radius:16px;border:1px solid #7c6cf0;background:#7c6cf0;color:#fff;font-size:12px;cursor:pointer;transition:all 0.15s';
      const tabStyle = 'padding:6px 16px;border:none;background:transparent;color:#999;font-size:13px;cursor:pointer;border-bottom:2px solid transparent;transition:all 0.15s';
      const tabActiveStyle = 'padding:6px 16px;border:none;background:transparent;color:#fff;font-size:13px;cursor:pointer;border-bottom:2px solid #7c6cf0;transition:all 0.15s';

      let nflPropsView = 'all'; // 'all' | 'weekly'

      const toolbar = document.createElement('div');
      toolbar.className = 'card';
      toolbar.style.cssText = 'padding:0;margin-bottom:16px;overflow:hidden';

      // Row 1: View tabs
      const tabRow = document.createElement('div');
      tabRow.className = 'props-toolbar-tabs';
      tabRow.style.cssText = 'display:flex;border-bottom:1px solid rgba(255,255,255,0.08)';
      const viewAllBtn = document.createElement('button');
      viewAllBtn.textContent = 'All Picks';
      const viewWeeklyBtn = document.createElement('button');
      viewWeeklyBtn.textContent = 'By Week';
      tabRow.appendChild(viewAllBtn);
      tabRow.appendChild(viewWeeklyBtn);
      toolbar.appendChild(tabRow);

      // Row 2: Market filter pills
      const allMarketKeys = [...new Set(picks.map(p => p.market))];
      const marketBtnBar = document.createElement('div');
      marketBtnBar.className = 'props-toolbar-pills';
      marketBtnBar.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap;padding:12px 16px;border-bottom:1px solid rgba(255,255,255,0.08)';
      let activeMarketFilter = 'all';

      function renderMarketBtns() {
        marketBtnBar.textContent = '';
        const allBtn = document.createElement('button');
        allBtn.textContent = 'All';
        allBtn.style.cssText = activeMarketFilter === 'all' ? pillActiveStyle : pillStyle;
        allBtn.onclick = () => { activeMarketFilter = 'all'; nflAllPicksPage = 0; nflWeeklyPage = 0; renderMarketBtns(); refreshNFLView(); };
        marketBtnBar.appendChild(allBtn);
        for (const m of allMarketKeys) {
          const btn = document.createElement('button');
          btn.textContent = marketLabels[m] || m;
          btn.style.cssText = activeMarketFilter === m ? pillActiveStyle : pillStyle;
          btn.onclick = () => { activeMarketFilter = m; nflAllPicksPage = 0; nflWeeklyPage = 0; renderMarketBtns(); refreshNFLView(); };
          marketBtnBar.appendChild(btn);
        }
      }
      toolbar.appendChild(marketBtnBar);

      // Row 3: Contextual filters
      const filterRow = document.createElement('div');
      filterRow.className = 'props-toolbar-filters';
      filterRow.style.cssText = 'display:flex;gap:12px;align-items:center;padding:12px 16px;flex-wrap:wrap';

      // All Picks filters
      const allSeasons = [...new Set(picks.map(p => p.season))].sort();
      const seasonSel = document.createElement('select');
      seasonSel.style.cssText = selStyle;
      seasonSel.innerHTML = '<option value="all">All Seasons</option>' + allSeasons.map(s => `<option value="${s}">${s}</option>`).join('');

      const weekSel = document.createElement('select');
      weekSel.style.cssText = selStyle;

      const teamSel = document.createElement('select');
      teamSel.style.cssText = selStyle;
      const allTeams = [...new Set(picks.map(p => p.team))].filter(Boolean).sort();
      teamSel.innerHTML = '<option value="all">All Teams</option>' + allTeams.map(t => `<option value="${t}">${t}</option>`).join('');

      const filterLabel = document.createElement('span');
      filterLabel.style.cssText = 'color:#666;font-size:12px;margin-left:auto';
      filterLabel.textContent = `${picks.length} picks`;

      // By Week filters
      const weeklySeasonSel = document.createElement('select');
      weeklySeasonSel.style.cssText = selStyle;
      weeklySeasonSel.innerHTML = '<option value="all">All Seasons</option>' + allSeasons.map(s => `<option value="${s}">${s}</option>`).join('');
      const weeklyFilterLabel = document.createElement('span');
      weeklyFilterLabel.style.cssText = 'color:#666;font-size:12px;margin-left:auto';

      function updateWeekOptions() {
        let seasonPicks = data.props.filter(p => p.pick !== 'PASS');
        if (seasonSel.value !== 'all') seasonPicks = seasonPicks.filter(p => String(p.season) === seasonSel.value);
        const weeks = [...new Set(seasonPicks.map(p => p.week))].sort((a, b) => a - b);
        weekSel.innerHTML = '<option value="all">All Weeks</option>' + weeks.map(w => `<option value="${w}">Week ${w}</option>`).join('');
      }
      updateWeekOptions();

      toolbar.appendChild(filterRow);
      el.appendChild(toolbar);

      const contentArea = document.createElement('div');
      el.appendChild(contentArea);

      const PAGE_SIZE = 25;

      function getFilteredPicks() {
        let fp = data.props.filter(p => p.pick !== 'PASS');
        if (seasonSel.value !== 'all') fp = fp.filter(p => String(p.season) === seasonSel.value);
        if (weekSel.value !== 'all') fp = fp.filter(p => String(p.week) === weekSel.value);
        if (activeMarketFilter !== 'all') fp = fp.filter(p => p.market === activeMarketFilter);
        if (teamSel.value !== 'all') fp = fp.filter(p => p.team === teamSel.value);
        fp.sort((a, b) => {
          const wA = (a.season || 0) * 100 + (a.week || 0);
          const wB = (b.season || 0) * 100 + (b.week || 0);
          return wB - wA;
        });
        return fp;
      }

      function buildNFLPropsTable(mProps) {
        const wrap = document.createElement('div');
        wrap.className = 'props-table-wrap';
        const tbl = document.createElement('table');
        tbl.className = 'props-data-table';
        tbl.style.cssText = 'width:100%;border-collapse:collapse;margin-top:8px';
        const hRow = tbl.createTHead().insertRow();
        headers.forEach((h, i) => {
          const th = document.createElement('th');
          th.textContent = h;
          th.className = colClasses[i] || 'col-' + i;
          th.style.cssText = 'padding:4px 4px;text-align:center;border-bottom:1px solid rgba(255,255,255,0.1);font-size:12px';
          if (h === 'Player') th.style.textAlign = 'left';
          hRow.appendChild(th);
        });
        const tbody = tbl.createTBody();
        for (const p of mProps) {
          const row = tbody.insertRow();
          row.style.borderBottom = '1px solid rgba(255,255,255,0.05)';
          const cells = isBacktest ? [
            (p.season ? String(p.season).slice(2) + ' ' : '') + 'W' + (p.week || ''),
            p.player, String(p.proj),
            p.line != null ? String(p.line) : '\u2014',
            p.actual != null ? String(p.actual) : '\u2014',
            p.pick === 'OVER' ? 'O' : 'U',
            p.result === 'WIN' ? 'W' : p.result === 'LOSS' ? 'L' : '\u2014',
            p.conf === 'elite' ? 'ELITE' : 'HIGH'
          ] : [
            p.player, p.team, p.opp || '', String(p.proj),
            p.line != null ? String(p.line) : '\u2014',
            p.pick === 'OVER' ? 'O' : 'U',
            p.conf === 'elite' ? 'ELITE' : 'HIGH'
          ];
          cells.forEach((val, i) => {
            const td = row.insertCell();
            td.textContent = val;
            td.className = colClasses[i] || 'col-' + i;
            td.style.cssText = 'padding:4px 4px;text-align:center;font-size:13px';
            if (isBacktest) {
              if (i === 1) { td.style.textAlign = 'left'; td.style.fontWeight = '600'; }
              if (i === 0) { td.style.color = '#999'; td.style.fontSize = '11px'; }
              if (i === 2) td.style.color = p.proj > p.line ? 'var(--green)' : p.proj < p.line ? 'var(--red)' : '';
              if (i === 5) { td.style.fontWeight = '700'; td.style.color = p.pick === 'OVER' ? 'var(--green)' : 'var(--red)'; }
              if (i === 6) { td.style.fontWeight = '700'; td.style.color = p.result === 'WIN' ? 'var(--green)' : 'var(--red)'; }
              if (i === 7 && p.conf === 'elite') { td.style.background = '#7c6cf0'; td.style.color = '#fff'; td.style.borderRadius = '4px'; td.style.fontSize = '10px'; }
            } else {
              if (i === 0) { td.style.textAlign = 'left'; td.style.fontWeight = '600'; }
              if (i === 1 || i === 2) td.style.color = '#999';
              if (i === 3) td.style.color = p.proj > p.line ? 'var(--green)' : p.proj < p.line ? 'var(--red)' : '';
              if (i === 5) { td.style.fontWeight = '700'; td.style.color = p.pick === 'OVER' ? 'var(--green)' : 'var(--red)'; }
              if (i === 6 && p.conf === 'elite') { td.style.background = '#7c6cf0'; td.style.color = '#fff'; td.style.borderRadius = '4px'; td.style.fontSize = '10px'; }
            }
          });
        }
        wrap.appendChild(tbl);
        return wrap;
      }

      function buildNFLMarketBreakdown(filteredPicks) {
        const nflMarketOrder = ['Pass Yards','Pass TDs','Rush Yards','Rush Att','Rec Yards','Receptions'];
        const fGrouped = {};
        for (const p of filteredPicks) {
          const ml = marketLabels[p.market] || p.market;
          if (!fGrouped[ml]) fGrouped[ml] = [];
          fGrouped[ml].push(p);
        }
        const sortedMarkets = Object.keys(fGrouped).sort((a, b) => {
          const ia = nflMarketOrder.indexOf(a); const ib = nflMarketOrder.indexOf(b);
          return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
        });
        return { fGrouped, sortedMarkets };
      }

      function makePgBar(pageStart, pageSize, total, totalPages, currentPage, onPrev, onNext) {
        const pgBar = document.createElement('div');
        pgBar.className = 'props-pg-bar'; pgBar.style.cssText = 'display:flex;align-items:center;gap:10px;flex-wrap:wrap';
        const pgLabel = document.createElement('span');
        pgLabel.style.cssText = 'color:#999;font-size:12px;flex:1';
        pgLabel.textContent = `${pageStart+1}\u2013${Math.min(pageStart+pageSize, total)} of ${total} picks`;
        const prevBtn = document.createElement('button');
        prevBtn.textContent = '\u2190 Prev';
        prevBtn.style.cssText = 'padding:4px 12px;border-radius:6px;border:1px solid rgba(255,255,255,0.15);background:transparent;color:#ccc;font-size:12px;cursor:pointer';
        prevBtn.disabled = currentPage === 0;
        prevBtn.style.opacity = currentPage === 0 ? '0.3' : '1';
        const nextBtn = document.createElement('button');
        nextBtn.textContent = 'Next \u2192';
        nextBtn.style.cssText = 'padding:4px 12px;border-radius:6px;border:1px solid rgba(255,255,255,0.15);background:transparent;color:#ccc;font-size:12px;cursor:pointer';
        nextBtn.disabled = currentPage >= totalPages - 1;
        nextBtn.style.opacity = currentPage >= totalPages - 1 ? '0.3' : '1';
        prevBtn.onclick = onPrev;
        nextBtn.onclick = onNext;
        pgBar.appendChild(pgLabel);
        pgBar.appendChild(prevBtn);
        pgBar.appendChild(document.createTextNode(`Page ${currentPage+1} / ${totalPages}`));
        pgBar.appendChild(nextBtn);
        return pgBar;
      }

      let nflAllPicksPage = 0;
      let nflWeeklyPage = 0;

      function renderNFLAllPicksView() {
        contentArea.textContent = '';
        const filteredPicks = getFilteredPicks();
        filterLabel.textContent = `${filteredPicks.length} picks`;

        if (filteredPicks.length === 0) {
          const empty = document.createElement('div');
          empty.className = 'card card-games';
          empty.appendChild(Object.assign(document.createElement('div'), {className:'no-picks', textContent:'No picks for selected filter.'}));
          contentArea.appendChild(empty);
          return;
        }

        const { fGrouped, sortedMarkets } = buildNFLMarketBreakdown(filteredPicks);

        // Market breakdown summary
        if (isBacktest) {
          const summCard = document.createElement('div');
          summCard.className = 'card card-games';
          summCard.style.marginBottom = '16px';
          summCard.appendChild(Object.assign(document.createElement('div'), {className:'card-title', textContent:'Market Breakdown'}));
          const summWrap = document.createElement('div');
          summWrap.className = 'props-table-wrap';
          const summTbl = document.createElement('table');
          summTbl.style.cssText = 'width:100%;border-collapse:collapse;margin-top:8px';
          const sh = summTbl.createTHead().insertRow();
          ['Cat','Picks','W','L','Win%','Units'].forEach(h => {
            const th = document.createElement('th');
            th.textContent = h;
            th.style.cssText = 'padding:6px 10px;text-align:right;border-bottom:1px solid rgba(255,255,255,0.1)';
            if (h === 'Cat') th.style.textAlign = 'left';
            sh.appendChild(th);
          });
          const sb = summTbl.createTBody();
          let gW = 0, gL = 0;
          for (const market of sortedMarkets) {
            const mPicks = fGrouped[market];
            const w = mPicks.filter(p => p.result === 'WIN').length;
            const l = mPicks.filter(p => p.result === 'LOSS').length;
            const u = w * 1.0 + l * (-1.1);
            const pct = (w + l) > 0 ? (w / (w + l) * 100).toFixed(1) : 'n/a';
            gW += w; gL += l;
            const sr = sb.insertRow();
            [market, String(mPicks.length), String(w), String(l), pct + '%', (u >= 0 ? '+' : '') + u.toFixed(1) + 'u'].forEach((v, i) => {
              const td = sr.insertCell();
              td.textContent = v;
              td.style.padding = '6px 10px';
              td.style.textAlign = i === 0 ? 'left' : 'right';
              if (i === 5) td.style.color = u >= 0 ? 'var(--green)' : 'var(--red)';
            });
          }
          const gU = gW * 1.0 + gL * (-1.1);
          const tr = sb.insertRow();
          tr.style.borderTop = '2px solid rgba(255,255,255,0.2)';
          tr.style.fontWeight = '700';
          ['TOTAL', String(filteredPicks.length), String(gW), String(gL),
           (gW+gL>0?(gW/(gW+gL)*100).toFixed(1):'0')+'%',
           (gU>=0?'+':'')+gU.toFixed(1)+'u'].forEach((v,i) => {
            const td = tr.insertCell();
            td.textContent = v;
            td.style.padding = '6px 10px';
            td.style.textAlign = i === 0 ? 'left' : 'right';
            if (i === 5) td.style.color = gU >= 0 ? 'var(--green)' : 'var(--red)';
          });
          summWrap.appendChild(summTbl); summCard.appendChild(summWrap);
          contentArea.appendChild(summCard);
        }

        // Single market selected → paginated table
        if (activeMarketFilter !== 'all') {
          nflAllPicksPage = Math.min(nflAllPicksPage, Math.floor(Math.max(0, filteredPicks.length - 1) / PAGE_SIZE));
          const totalPages = Math.ceil(filteredPicks.length / PAGE_SIZE);
          const pageStart = nflAllPicksPage * PAGE_SIZE;
          const pagePicks = filteredPicks.slice(pageStart, pageStart + PAGE_SIZE);

          const card = document.createElement('div');
          card.className = 'card card-games';
          const mW = filteredPicks.filter(p => p.result === 'WIN').length;
          const mL = filteredPicks.filter(p => p.result === 'LOSS').length;
          const titleSuffix = isBacktest ? ` (${mW}W-${mL}L)` : '';
          card.appendChild(Object.assign(document.createElement('div'), {className:'card-title', textContent:(marketLabels[activeMarketFilter]||activeMarketFilter)+titleSuffix}));

          if (totalPages > 1) {
            const pg = makePgBar(pageStart, PAGE_SIZE, filteredPicks.length, totalPages, nflAllPicksPage,
              () => { nflAllPicksPage--; renderNFLAllPicksView(); window.scrollTo(0,0); },
              () => { nflAllPicksPage++; renderNFLAllPicksView(); window.scrollTo(0,0); });
            pg.style.marginBottom = '12px';
            card.appendChild(pg);
          }
          card.appendChild(buildNFLPropsTable(pagePicks));
          if (totalPages > 1) {
            const pg2 = makePgBar(pageStart, PAGE_SIZE, filteredPicks.length, totalPages, nflAllPicksPage,
              () => { nflAllPicksPage--; renderNFLAllPicksView(); window.scrollTo(0,0); },
              () => { nflAllPicksPage++; renderNFLAllPicksView(); window.scrollTo(0,0); });
            pg2.style.marginTop = '12px';
            card.appendChild(pg2);
          }
          contentArea.appendChild(card);
          return;
        }

        // All markets → single paginated table with Market column
        nflAllPicksPage = Math.min(nflAllPicksPage, Math.floor((filteredPicks.length - 1) / PAGE_SIZE));
        const totalPages = Math.ceil(filteredPicks.length / PAGE_SIZE);
        const pageStart = nflAllPicksPage * PAGE_SIZE;
        const pagePicks = filteredPicks.slice(pageStart, pageStart + PAGE_SIZE);

        const card = document.createElement('div');
        card.className = 'card card-games';

        if (totalPages > 1) {
          const pg = makePgBar(pageStart, PAGE_SIZE, filteredPicks.length, totalPages, nflAllPicksPage,
            () => { nflAllPicksPage--; renderNFLAllPicksView(); window.scrollTo(0,0); },
            () => { nflAllPicksPage++; renderNFLAllPicksView(); window.scrollTo(0,0); });
          pg.style.marginBottom = '12px';
          card.appendChild(pg);
        }

        // Table with Market column
        const tbl = document.createElement('table');
        tbl.style.cssText = 'width:100%;border-collapse:collapse';
        const hdrs = isBacktest
          ? ['Wk','Player','Cat','Proj','Line','Actual','Pick','Result','Conf']
          : ['Player','Team','vs','Cat','Proj','Line','Pick','Conf'];
        const hRow = tbl.createTHead().insertRow();
        hdrs.forEach(h => {
          const th = document.createElement('th');
          th.textContent = h;
          th.style.cssText = 'padding:4px 4px;text-align:center;border-bottom:1px solid rgba(255,255,255,0.1);font-size:12px';
          if (h === 'Player') th.style.textAlign = 'left';
          hRow.appendChild(th);
        });
        const tbody = tbl.createTBody();
        for (const p of pagePicks) {
          const row = tbody.insertRow();
          row.style.borderBottom = '1px solid rgba(255,255,255,0.05)';
          const ml = marketLabels[p.market] || p.market;
          const cells = isBacktest ? [
            (p.season ? String(p.season).slice(2)+' ':'')+'W'+(p.week||''),
            p.player, ml, String(p.proj),
            p.line!=null?String(p.line):'\u2014',
            p.actual!=null?String(p.actual):'\u2014',
            p.pick==='OVER'?'O':'U',
            p.result==='WIN'?'W':p.result==='LOSS'?'L':'\u2014',
            p.conf==='elite'?'ELITE':'HIGH'
          ] : [
            p.player, p.team, p.opp||'', ml, String(p.proj),
            p.line!=null?String(p.line):'\u2014',
            p.pick==='OVER'?'O':'U',
            p.conf==='elite'?'ELITE':'HIGH'
          ];
          cells.forEach((val, i) => {
            const td = row.insertCell();
            td.textContent = val;
            td.className = colClasses[i] || 'col-' + i;
            td.style.cssText = 'padding:4px 4px;text-align:center;font-size:13px';
            if (isBacktest) {
              if (i===1) { td.style.textAlign='left'; td.style.fontWeight='600'; }
              if (i===0) { td.style.color='#999'; td.style.fontSize='11px'; }
              if (i===2) { td.style.color='#aaa'; td.style.fontSize='11px'; }
              if (i===3) td.style.color=p.proj>p.line?'var(--green)':p.proj<p.line?'var(--red)':'';
              if (i===6) { td.style.fontWeight='700'; td.style.color=p.pick==='OVER'?'var(--green)':'var(--red)'; }
              if (i===7) { td.style.fontWeight='700'; td.style.color=p.result==='WIN'?'var(--green)':'var(--red)'; }
              if (i===8&&p.conf==='elite') { td.style.background='#7c6cf0'; td.style.color='#fff'; td.style.borderRadius='4px'; td.style.fontSize='10px'; }
            } else {
              if (i===0) { td.style.textAlign='left'; td.style.fontWeight='600'; }
              if (i===1||i===2) td.style.color='#999';
              if (i===3) { td.style.color='#aaa'; td.style.fontSize='11px'; }
              if (i===4) td.style.color=p.proj>p.line?'var(--green)':p.proj<p.line?'var(--red)':'';
              if (i===6) { td.style.fontWeight='700'; td.style.color=p.pick==='OVER'?'var(--green)':'var(--red)'; }
              if (i===7&&p.conf==='elite') { td.style.background='#7c6cf0'; td.style.color='#fff'; td.style.borderRadius='4px'; td.style.fontSize='10px'; }
            }
          });
        }
        card.appendChild(tbl);

        if (totalPages > 1) {
          const pg2 = makePgBar(pageStart, PAGE_SIZE, filteredPicks.length, totalPages, nflAllPicksPage,
            () => { nflAllPicksPage--; renderNFLAllPicksView(); window.scrollTo(0,0); },
            () => { nflAllPicksPage++; renderNFLAllPicksView(); window.scrollTo(0,0); });
          pg2.style.marginTop = '12px';
          card.appendChild(pg2);
        }
        contentArea.appendChild(card);
      }

      function renderNFLWeeklyView() {
        contentArea.textContent = '';
        let fp = picks.slice();
        if (activeMarketFilter !== 'all') fp = fp.filter(p => p.market === activeMarketFilter);
        if (weeklySeasonSel.value !== 'all') fp = fp.filter(p => String(p.season) === weeklySeasonSel.value);

        // Group by season+week
        const weekMap = {};
        for (const p of fp) {
          const key = (p.season||'') + '-W' + (p.week||'');
          if (!weekMap[key]) weekMap[key] = { season: p.season, week: p.week, picks: [] };
          weekMap[key].picks.push(p);
        }
        const sortedWeekKeys = Object.keys(weekMap).sort((a, b) => {
          const wa = weekMap[a], wb = weekMap[b];
          const sA = (wa.season||0)*100+(wa.week||0);
          const sB = (wb.season||0)*100+(wb.week||0);
          return sB - sA; // latest first
        });

        weeklyFilterLabel.textContent = `${fp.length} picks across ${sortedWeekKeys.length} week${sortedWeekKeys.length !== 1 ? 's' : ''}`;

        if (sortedWeekKeys.length === 0) {
          const empty = document.createElement('div');
          empty.className = 'card card-games';
          empty.appendChild(Object.assign(document.createElement('div'), {className:'no-picks', textContent:'No picks for selected filter.'}));
          contentArea.appendChild(empty);
          return;
        }

        // Weekly summary table
        if (isBacktest) {
          const summCard = document.createElement('div');
          summCard.className = 'card card-games';
          summCard.style.marginBottom = '16px';
          summCard.appendChild(Object.assign(document.createElement('div'), {className:'card-title', textContent:'Weekly Summary'}));
          const summTbl = document.createElement('table');
          summTbl.style.cssText = 'width:100%;border-collapse:collapse;margin-top:8px';
          const sh = summTbl.createTHead().insertRow();
          ['Week','Picks','W','L','Win%','Units'].forEach(h => {
            const th = document.createElement('th');
            th.textContent = h;
            th.style.cssText = 'padding:6px 10px;text-align:right;border-bottom:1px solid rgba(255,255,255,0.1)';
            if (h === 'Week') th.style.textAlign = 'left';
            sh.appendChild(th);
          });
          const sb = summTbl.createTBody();
          let totW = 0, totL = 0;
          for (const key of sortedWeekKeys) {
            const wk = weekMap[key];
            const w = wk.picks.filter(p => p.result === 'WIN').length;
            const l = wk.picks.filter(p => p.result === 'LOSS').length;
            const u = w * 1.0 + l * (-1.1);
            const pct = (w + l) > 0 ? (w / (w + l) * 100).toFixed(1) : '\u2014';
            totW += w; totL += l;
            const sr = sb.insertRow();
            [`${wk.season} Week ${wk.week}`, String(wk.picks.length), String(w), String(l),
             (w+l>0?pct+'%':'\u2014'), (w+l>0?(u>=0?'+':'')+u.toFixed(1)+'u':'\u2014')].forEach((v, i) => {
              const td = sr.insertCell();
              td.textContent = v;
              td.style.padding = '6px 10px';
              td.style.textAlign = i === 0 ? 'left' : 'right';
              if (i === 5 && w+l > 0) td.style.color = u >= 0 ? 'var(--green)' : 'var(--red)';
            });
          }
          const totU = totW * 1.0 + totL * (-1.1);
          const tr = sb.insertRow();
          tr.style.borderTop = '2px solid rgba(255,255,255,0.2)';
          tr.style.fontWeight = '700';
          ['TOTAL', String(fp.length), String(totW), String(totL),
           (totW+totL>0?(totW/(totW+totL)*100).toFixed(1)+'%':'\u2014'),
           (totU>=0?'+':'')+totU.toFixed(1)+'u'].forEach((v,i) => {
            const td = tr.insertCell();
            td.textContent = v;
            td.style.padding = '6px 10px';
            td.style.textAlign = i === 0 ? 'left' : 'right';
            if (i === 5) td.style.color = totU >= 0 ? 'var(--green)' : 'var(--red)';
          });
          summWrap.appendChild(summTbl); summCard.appendChild(summWrap);
          contentArea.appendChild(summCard);
        }

        // 1 week per page pagination
        const totalWeekPages = sortedWeekKeys.length;
        nflWeeklyPage = Math.min(nflWeeklyPage, Math.max(0, totalWeekPages - 1));

        if (totalWeekPages > 1) {
          const pg = makePgBar(nflWeeklyPage, 1, totalWeekPages, totalWeekPages, nflWeeklyPage,
            () => { nflWeeklyPage--; renderNFLWeeklyView(); window.scrollTo(0,0); },
            () => { nflWeeklyPage++; renderNFLWeeklyView(); window.scrollTo(0,0); });
          // Override label for weeks
          pg.querySelector('span').textContent = `Week ${nflWeeklyPage+1} of ${totalWeekPages}`;
          pg.style.marginBottom = '12px';
          contentArea.appendChild(pg);
        }

        const key = sortedWeekKeys[nflWeeklyPage];
        const wk = weekMap[key];
        const wPicks = wk.picks.slice().sort((a,b) => (b.pCover||0) - (a.pCover||0));
        const wW = wPicks.filter(p => p.result === 'WIN').length;
        const wL = wPicks.filter(p => p.result === 'LOSS').length;
        const wU = wW * 1.0 + wL * (-1.1);
        const wPct = (wW + wL) > 0 ? ` \u2014 ${wW}W-${wL}L (${(wW/(wW+wL)*100).toFixed(1)}%) ${wU>=0?'+':''}${wU.toFixed(1)}u` : ` \u2014 ${wPicks.length} picks`;

        const card = document.createElement('div');
        card.className = 'card card-games';
        card.appendChild(Object.assign(document.createElement('div'), {
          className: 'card-title',
          textContent: `${wk.season} Week ${wk.week}${wPct}`
        }));

        // Mini market breakdown
        const { fGrouped: wGrouped, sortedMarkets: wMarkets } = buildNFLMarketBreakdown(wPicks);
        if (wMarkets.length > 1) {
          const mkRow = document.createElement('div');
          mkRow.style.cssText = 'display:flex;gap:16px;flex-wrap:wrap;margin:8px 0 12px;font-size:12px;color:#999';
          for (const mk of wMarkets) {
            const mp = wGrouped[mk];
            const mw = mp.filter(p => p.result === 'WIN').length;
            const ml2 = mp.length - mw;
            const mu = mw - ml2 * 1.1;
            const span = document.createElement('span');
            span.innerHTML = `<span style="color:#ccc">${mk}</span> ${mw}W-${ml2}L <span style="color:${mu>=0?'var(--green)':'var(--red)'}">${mu>=0?'+':''}${mu.toFixed(1)}u</span>`;
            mkRow.appendChild(span);
          }
          card.appendChild(mkRow);
        }

        card.appendChild(buildNFLPropsTable(wPicks));
        contentArea.appendChild(card);
      }

      function refreshNFLView() {
        if (nflPropsView === 'weekly') renderNFLWeeklyView();
        else renderNFLAllPicksView();
      }

      function setNFLPropsView(v) {
        nflPropsView = v;
        viewAllBtn.style.cssText = v === 'all' ? tabActiveStyle : tabStyle;
        viewWeeklyBtn.style.cssText = v === 'weekly' ? tabActiveStyle : tabStyle;
        filterRow.textContent = '';
        if (v === 'all') {
          filterRow.appendChild(seasonSel);
          filterRow.appendChild(weekSel);
          filterRow.appendChild(teamSel);
          filterRow.appendChild(filterLabel);
        } else {
          filterRow.appendChild(weeklySeasonSel);
          filterRow.appendChild(weeklyFilterLabel);
        }
        refreshNFLView();
      }

      viewAllBtn.onclick = () => setNFLPropsView('all');
      viewWeeklyBtn.onclick = () => setNFLPropsView('weekly');
      seasonSel.addEventListener('change', () => { updateWeekOptions(); nflAllPicksPage = 0; renderNFLAllPicksView(); });
      weekSel.addEventListener('change', () => { nflAllPicksPage = 0; renderNFLAllPicksView(); });
      teamSel.addEventListener('change', () => { nflAllPicksPage = 0; renderNFLAllPicksView(); });
      weeklySeasonSel.addEventListener('change', () => { nflWeeklyPage = 0; renderNFLWeeklyView(); });

      renderMarketBtns();
      setNFLPropsView('all');
    }
