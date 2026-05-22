// MLB Pitcher Props rendering

    // Tables on this tab have many columns (pitcher workload + projections +
    // results). Rather than hide columns or scroll horizontally, shrink the
    // font until the table fits its container width. Tracked tables are
    // re-fit on viewport resize / orientation change.
    const __mlbFitTables = new Set();
    function fitMLBTableToContainer(tbl) {
      if (!tbl) return;
      __mlbFitTables.add(tbl);
      requestAnimationFrame(() => {
        const parent = tbl.parentElement;
        if (!parent || !document.body.contains(tbl)) return;
        const available = parent.clientWidth;
        if (available <= 0) return;
        const cells = tbl.querySelectorAll('th, td');
        if (!cells.length) return;
        const setSize = (px) => cells.forEach(c => c.style.setProperty('font-size', px + 'px', 'important'));
        let fontSize = 13;
        const minFont = 6;
        setSize(fontSize);
        let guard = 0;
        while (tbl.scrollWidth > available + 1 && fontSize > minFont && guard < 60) {
          fontSize -= 0.5;
          setSize(fontSize);
          guard++;
        }
      });
    }
    if (typeof window !== 'undefined' && !window.__mlbFitListenerAdded) {
      window.__mlbFitListenerAdded = true;
      let __mlbFitTimer;
      window.addEventListener('resize', () => {
        clearTimeout(__mlbFitTimer);
        __mlbFitTimer = setTimeout(() => {
          for (const t of [...__mlbFitTables]) {
            if (!document.body.contains(t)) { __mlbFitTables.delete(t); continue; }
            fitMLBTableToContainer(t);
          }
        }, 150);
      });
    }

    // Staking: plus odds risk 1u to win payout, negative odds risk X to win 1u
    function calcMLBPropsUnits(picks) {
      let u = 0;
      for (const p of picks) {
        const price = p.odds != null ? Number(p.odds) : null;
        if (p.result === 'WIN') {
          if (price != null && price > 0) u += price / 100;        // +120 → risk 1u, win 1.2u
          else if (price != null && price < 0) u += 1.0;           // -130 → risk 1.3u, win 1u
          else u += 1.0;
        } else if (p.result === 'LOSS') {
          if (price != null && price > 0) u -= 1.0;                // +120 → risk 1u
          else if (price != null && price < 0) u -= Math.abs(price) / 100;  // -130 → risk 1.3u
          else u -= 1.1;
        }
      }
      return u;
    }
    function mlbShortName(name) {
      if (!name) return '';
      const parts = name.trim().split(/\s+/);
      if (parts.length < 2) return name;
      return parts[0][0] + '.' + parts[parts.length - 1];
    }

    function buildMLBMarketBreakdown(filteredPicks) {
      const mlbMarketLabels = {strikeouts:'K', outs:'OUTS', hits_allowed:'HA', game_hits:'HITS'};
      const mlbMarketOrder = ['K','OUTS','HA','HITS'];
      const fGrouped = {};
      for (const p of filteredPicks) {
        const ml = mlbMarketLabels[p.market] || p.market;
        if (!fGrouped[ml]) fGrouped[ml] = [];
        fGrouped[ml].push(p);
      }
      const sortedMarkets = Object.keys(fGrouped).sort((a, b) => {
        const ia = mlbMarketOrder.indexOf(a); const ib = mlbMarketOrder.indexOf(b);
        return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
      });
      return { fGrouped, sortedMarkets };
    }

    // Append a market row (Overs / Unders / Total / etc).
    // isTotal=true draws a top-border separator and bolds the row.
    function appendMarketRow(tbody, label, picks, isTotal) {
      const w = picks.filter(p => p.result === 'WIN').length;
      const l = picks.filter(p => p.result === 'LOSS').length;
      const u = calcMLBPropsUnits(picks);
      const pct = (w + l) > 0 ? (w / (w + l) * 100).toFixed(1) : 'n/a';
      const roi = (w + l) > 0 ? (u / (w + l) * 100).toFixed(1) : 'n/a';
      const sr = tbody.insertRow();
      if (isTotal) {
        sr.style.borderTop = '1px solid rgba(255,255,255,0.15)';
        sr.style.fontWeight = '600';
      }
      [label, String(picks.length), String(w), String(l),
       (w + l) > 0 ? pct + '%' : 'n/a',
       (u >= 0 ? '+' : '') + u.toFixed(2) + 'u',
       (w + l) > 0 ? (roi >= 0 ? '+' : '') + roi + '%' : 'n/a'
      ].forEach((v, i) => {
        const td = sr.insertCell();
        td.textContent = v;
        td.style.padding = '6px 10px';
        td.style.textAlign = i === 0 ? 'left' : 'right';
        if (i === 5) td.style.color = u >= 0 ? 'var(--green)' : 'var(--red)';
        if (i === 6 && (w + l) > 0) td.style.color = parseFloat(roi) >= 0 ? 'var(--green)' : 'var(--red)';
      });
    }

    async function renderMLBProps() {
      const el = document.getElementById('content');
      const data = await fetchData('mlb-props');
      if (!data || !data.props || !data.props.length) {
        el.textContent = '';
        const card = document.createElement('div');
        card.className = 'card-games';
        card.appendChild(Object.assign(document.createElement('div'), {className:'card-title', textContent:'MLB Strikeouts'}));
        card.appendChild(Object.assign(document.createElement('div'), {className:'no-picks', textContent:'No prop projections available yet. Run the props pipeline to generate projections.'}));
        el.appendChild(card);
        return;
      }

      // Update last-run-info with generated timestamp
      const runEl = document.getElementById('last-run-info');
      if (runEl && data.generated) {
        const d = new Date(data.generated);
        const ct = d.toLocaleString('en-US', { timeZone: 'America/Chicago', month: '2-digit', day: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true });
        runEl.textContent = `Last run (CT) \u2014 MLB Strikeouts: ${ct}`;
      }

      const marketLabels = {strikeouts:'K', outs:'OUTS', hits_allowed:'HA', game_hits:'HITS'};
      const picks = data.props.filter(p => p.pick !== 'PASS');
      const isBacktest = picks.some(p => p.result != null);

      // Helper: get ISO week start (Monday) for a date string
      function getWeekStart(dateStr) {
        const d = new Date(dateStr + 'T00:00:00Z');
        const day = d.getUTCDay();
        const diff = day === 0 ? -6 : 1 - day;
        d.setUTCDate(d.getUTCDate() + diff);
        return d.toISOString().slice(0, 10);
      }
      function getWeekEnd(weekStart) {
        const d = new Date(weekStart + 'T00:00:00Z');
        d.setUTCDate(d.getUTCDate() + 6);
        return d.toISOString().slice(0, 10);
      }

      el.textContent = '';

      // Helper: display name for a pick — game_hits shows game label, others show pitcher
      function displayName(p) {
        if (p.market === 'game_hits') {
          // Build game label from team/opp
          const t = p.team || '';
          const o = p.opp || '';
          return t && o ? `${t}@${o}` : (p.player || '');
        }
        return mlbShortName(p.player);
      }

      // Hoisted slot for the Reddit summary card so it can be invoked
      // at the very bottom of the page (after the All Picks tables).
      let _renderRedditCard = null;
      // Hoisted slot for the Matchup History card — rendered just under
      // the Reddit card. Shows each of today's picks/leans alongside the
      // model's historical record vs that opponent in that direction.
      let _renderMatchupCard = null;

      // ── Yesterday's Recap + Today's Picks ──
      (function renderMLBDailyCards() {
        const allDates = [...new Set(picks.map(p => p.date))].sort();
        const latestPickDate = allDates[allDates.length - 1] || '';
        // Anchor on data.date (today's slate) so zero-pick days still advance
        // the card — falling back to the most recent non-PASS date only if
        // the payload omits it.
        const todayStr = data.date || latestPickDate;
        const yest = new Date(todayStr + 'T12:00:00');
        yest.setDate(yest.getDate() - 1);
        const yesterdayStr = yest.toISOString().slice(0, 10);

        const gradedPicks = picks.filter(p => p.result && p.result !== 'VOID');

        // Watchlist leans (saved with pick=PASS):
        //   Both sides, 0.65 <= pCover < 0.72 — calibrated under the 2026-05-12
        //   config (BF=0.95, VAR=1.15, BLEND=0.0, CAP=23, pick threshold=0.72).
        //   Walk-forward backfill: 149 leans/season, 71.8% WR, +29.9% ROI.
        //   The 0.60-0.65 band was dropped — sub-marginal (~56% WR, ~+1% ROI)
        //   and -32% ROI in the slump period. Tracked separately from picks
        //   (>= 0.72) so the higher-conviction tier can be sized differently.
        function isLean(p) {
          if (p.pick !== 'PASS') return false;
          const pc = p.pCover || 0;
          return pc >= 0.65 && pc < 0.72;
        }
        const leanAll = (data.props || []).filter(isLean);
        const leanGraded = leanAll.filter(p => p.result === 'WIN' || p.result === 'LOSS');
        const leanUnderGraded = leanGraded.filter(p => p.would_be_pick === 'UNDER');
        const leanOverGraded = leanGraded.filter(p => p.would_be_pick === 'OVER');
        function leanRowFor(filteredLeans) {
          const w = filteredLeans.filter(p => p.result === 'WIN').length;
          const l = filteredLeans.filter(p => p.result === 'LOSS').length;
          const u = calcMLBPropsUnits(filteredLeans);
          const pct = (w + l) > 0 ? (w / (w + l) * 100).toFixed(1) : 'n/a';
          const roi = (w + l) > 0 ? (u / (w + l) * 100).toFixed(1) : 'n/a';
          return { w, l, n: filteredLeans.length, u, pct, roi };
        }
        function appendLeanRow(tbody, leans, label) {
          const r = leanRowFor(leans);
          if (r.n === 0) return;
          const tr = tbody.insertRow();
          tr.style.borderTop = '1px dashed rgba(244,180,0,0.4)';
          tr.style.color = '#f4b400';
          [label, String(r.n), String(r.w), String(r.l), r.pct+'%',
           (r.u>=0?'+':'')+r.u.toFixed(2)+'u',
           (r.roi==='n/a'?'n/a':(parseFloat(r.roi)>=0?'+':'')+r.roi+'%')].forEach((v,i) => {
            const td = tr.insertCell();
            td.textContent = v;
            td.style.padding = '6px 10px';
            td.style.textAlign = i === 0 ? 'left' : 'right';
            td.style.fontStyle = 'italic';
          });
        }

        // Market Breakdown (season-long, graded picks only)
        if (gradedPicks.length > 0) {
          const { fGrouped, sortedMarkets } = buildMLBMarketBreakdown(gradedPicks);
          const mbCard = document.createElement('div');
          mbCard.className = 'card card-games';
          mbCard.style.marginBottom = '16px';
          mbCard.appendChild(Object.assign(document.createElement('div'), {className:'card-title', textContent:'Season Market Breakdown'}));
          const mbWrap = document.createElement('div');
          mbWrap.className = 'props-table-wrap';
          const mbTbl = document.createElement('table');
          mbTbl.style.cssText = 'width:100%;border-collapse:collapse;margin-top:8px';
          const mh = mbTbl.createTHead().insertRow();
          ['Cat','Picks','W','L','Win%','Units','ROI'].forEach(h => {
            const th = document.createElement('th');
            th.textContent = h;
            th.style.cssText = 'padding:6px 10px;text-align:right;border-bottom:1px solid rgba(255,255,255,0.1)';
            if (h === 'Cat') th.style.textAlign = 'left';
            mh.appendChild(th);
          });
          const mb = mbTbl.createTBody();
          // Order: Overs → Unders → Total (per-market). Single market for now (K).
          const allOvers  = gradedPicks.filter(p => p.pick === 'OVER');
          const allUnders = gradedPicks.filter(p => p.pick === 'UNDER');
          if (allOvers.length)  appendMarketRow(mb, 'Overs',  allOvers,  false);
          if (allUnders.length) appendMarketRow(mb, 'Unders', allUnders, false);
          appendMarketRow(mb, 'Total', gradedPicks, true);
          appendLeanRow(mb, leanOverGraded, 'Lean O .65-.72');
          appendLeanRow(mb, leanUnderGraded, 'Lean U .65-.72');
          appendLeanRow(mb, leanGraded,      'Lean Total .65-.72');
          mbWrap.appendChild(mbTbl);
          mbCard.appendChild(mbWrap);
          el.appendChild(mbCard);
        }

        // Recent Record — toggleable window.
        // "This week" = current Mon-Sun calendar week (recomputed at render).
        // "Last 2 weeks" = the two most-recently completed Mon-Sun weeks.
        function _isoDate(d) {
          return d.toISOString().slice(0, 10);
        }
        function _weekWindows() {
          const today = new Date();
          today.setUTCHours(0, 0, 0, 0);
          const daysSinceMon = (today.getUTCDay() + 6) % 7;
          const thisMon = new Date(today);
          thisMon.setUTCDate(today.getUTCDate() - daysSinceMon);
          const thisSun = new Date(thisMon);
          thisSun.setUTCDate(thisMon.getUTCDate() + 6);
          const lastMon = new Date(thisMon);
          lastMon.setUTCDate(thisMon.getUTCDate() - 7);
          const twoMon = new Date(thisMon);
          twoMon.setUTCDate(thisMon.getUTCDate() - 14);
          const lastSun = new Date(thisMon);
          lastSun.setUTCDate(thisMon.getUTCDate() - 1);
          return {
            thisWeek:    { start: _isoDate(thisMon), end: _isoDate(thisSun) },
            lastTwoWeek: { start: _isoDate(twoMon),  end: _isoDate(lastSun) },
          };
        }
        const _ww = _weekWindows();
        const recentCutoffOptions = [
          { label: 'This week',     start: _ww.thisWeek.start,    end: _ww.thisWeek.end },
          { label: 'Last 2 weeks',  start: _ww.lastTwoWeek.start, end: _ww.lastTwoWeek.end },
        ];
        let recentCutoffIdx = 0;

        const rCardContainer = document.createElement('div');
        el.appendChild(rCardContainer);

        function buildRecentToggle() {
          const wrap = document.createElement('div');
          wrap.style.cssText = 'display:inline-flex;gap:4px;background:rgba(255,255,255,0.05);padding:3px;border-radius:6px';
          recentCutoffOptions.forEach((opt, idx) => {
            const b = document.createElement('button');
            b.textContent = opt.label;
            const active = idx === recentCutoffIdx;
            b.style.cssText = 'font-size:11px;padding:4px 10px;border:0;border-radius:4px;cursor:pointer;'
              + (active
                  ? 'background:rgba(168,85,247,0.35);color:#fff;font-weight:600'
                  : 'background:transparent;color:#aaa');
            b.addEventListener('click', () => {
              if (recentCutoffIdx !== idx) {
                recentCutoffIdx = idx;
                renderRecentRecord();
              }
            });
            wrap.appendChild(b);
          });
          return wrap;
        }

        function renderRecentRecord() {
          rCardContainer.innerHTML = '';
          const opt = recentCutoffOptions[recentCutoffIdx];
          const recentPicks = gradedPicks.filter(p =>
            p.date && p.date >= opt.start && (!opt.end || p.date <= opt.end)
          );
          const rCard = document.createElement('div');
          rCard.className = 'card card-games';
          rCard.style.marginBottom = '16px';
          const titleRow = document.createElement('div');
          titleRow.className = 'card-title';
          titleRow.style.cssText = 'display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap';
          const titleSpan = document.createElement('span');
          titleSpan.textContent = opt.end
            ? `Recent Record (${opt.start} to ${opt.end})`
            : `Recent Record (${opt.start} - present)`;
          titleRow.appendChild(titleSpan);
          titleRow.appendChild(buildRecentToggle());
          rCard.appendChild(titleRow);

          if (recentPicks.length === 0) {
            const note = document.createElement('div');
            note.style.cssText = 'padding:12px;color:#888;font-style:italic;font-size:13px';
            note.textContent = 'No graded picks in this window yet.';
            rCard.appendChild(note);
            rCardContainer.appendChild(rCard);
            return;
          }
          const rWrap = document.createElement('div');
          rWrap.className = 'props-table-wrap';
          const rTbl = document.createElement('table');
          rTbl.style.cssText = 'width:100%;border-collapse:collapse;margin-top:8px';
          const rh = rTbl.createTHead().insertRow();
          ['Cat','Picks','W','L','Win%','Units','ROI'].forEach(h => {
            const th = document.createElement('th');
            th.textContent = h;
            th.style.cssText = 'padding:6px 10px;text-align:right;border-bottom:1px solid rgba(255,255,255,0.1)';
            if (h === 'Cat') th.style.textAlign = 'left';
            rh.appendChild(th);
          });
          const rb = rTbl.createTBody();
          const recentOvers  = recentPicks.filter(p => p.pick === 'OVER');
          const recentUnders = recentPicks.filter(p => p.pick === 'UNDER');
          if (recentOvers.length)  appendMarketRow(rb, 'Overs',  recentOvers,  false);
          if (recentUnders.length) appendMarketRow(rb, 'Unders', recentUnders, false);
          appendMarketRow(rb, 'Total', recentPicks, true);
          const _inWin = p => p.date && p.date >= opt.start && (!opt.end || p.date <= opt.end);
          const recentOverLeans   = leanOverGraded.filter(_inWin);
          const recentUnderLeans  = leanUnderGraded.filter(_inWin);
          const recentLeansAll    = leanGraded.filter(_inWin);
          appendLeanRow(rb, recentOverLeans,  'Lean O .65-.72');
          appendLeanRow(rb, recentUnderLeans, 'Lean U .65-.72');
          appendLeanRow(rb, recentLeansAll,   'Lean Total .65-.72');
          rWrap.appendChild(rTbl);
          rCard.appendChild(rWrap);
          rCardContainer.appendChild(rCard);
        }

        renderRecentRecord();

        // Yesterday's Recap
        const yesterdayPicks = picks.filter(p =>
          p.date === yesterdayStr && p.result && p.result !== 'VOID'
        );
        if (yesterdayPicks.length > 0) {
          const yW = yesterdayPicks.filter(p => p.result === 'WIN').length;
          const yL = yesterdayPicks.filter(p => p.result === 'LOSS').length;
          const yU = calcMLBPropsUnits(yesterdayPicks);
          const uColor = yU >= 0 ? 'var(--green)' : 'var(--red)';
          const recapCard = document.createElement('div');
          recapCard.className = 'card card-recap';
          recapCard.style.marginBottom = '16px';
          recapCard.appendChild(Object.assign(document.createElement('div'), {
            className: 'card-title',
            textContent: `Yesterday\u2019s Recap (${yesterdayStr})`
          }));
          const tbl = document.createElement('table');
          tbl.className = 'data';
          tbl.style.cssText = 'width:100%;border-collapse:collapse;margin-top:8px';
          const hRow = tbl.createTHead().insertRow();
          ['Name','Team','Opp','Proj','Line','Edge','Odds','Actual','O/U','W/L'].forEach((h, i) => {
            const th = document.createElement('th');
            th.textContent = h;
            th.style.cssText = 'padding:6px 10px;border-bottom:1px solid rgba(255,255,255,0.1);' + (i === 0 ? 'text-align:left' : 'text-align:center');
            hRow.appendChild(th);
          });
          const tbody = tbl.createTBody();
          const yCatOrder = {strikeouts:0, outs:1, hits_allowed:2, game_hits:3};
          for (const p of yesterdayPicks.sort((a,b) => (yCatOrder[a.market]??99) - (yCatOrder[b.market]??99) || (b.pCover||0) - (a.pCover||0))) {
            const row = tbody.insertRow();
            row.style.borderBottom = '1px solid rgba(255,255,255,0.05)';
            const yEdge = (p.proj != null && p.line != null) ? +(p.proj - p.line).toFixed(1) : null;
            const yEdgeStr = yEdge != null ? (yEdge > 0 ? '+'+yEdge : String(yEdge)) : '\u2014';
            const yPrice = p.odds != null ? (p.odds > 0 ? '+'+p.odds : String(p.odds)) : '\u2014';
            [displayName(p), p.team||'', p.opp||'',
             String(p.proj), p.line!=null?String(p.line):'\u2014', yEdgeStr, yPrice,
             p.actual!=null?String(p.actual):'\u2014',
             p.pick==='OVER'?'O':'U', p.result==='WIN'?'W':'L'].forEach((v, i) => {
              const td = row.insertCell();
              td.textContent = v;
              td.style.cssText = 'padding:4px 4px;text-align:center';
              if (i === 0) { td.style.textAlign = 'left'; td.style.fontWeight = '600'; }
              if (i === 1 || i === 2) td.style.color = '#999';
              if (i === 3) td.style.color = p.proj > p.line ? 'var(--green)' : p.proj < p.line ? 'var(--red)' : '';
              if (i === 5 && yEdge != null) td.style.color = yEdge > 0 ? 'var(--green)' : yEdge < 0 ? 'var(--red)' : '#999';
              if (i === 6) td.style.color = '#999';
              if (i === 8) { td.style.fontWeight = '700'; td.style.color = p.pick === 'OVER' ? 'var(--green)' : 'var(--red)'; }
              if (i === 9) { td.style.fontWeight = '700'; td.style.color = p.result === 'WIN' ? 'var(--green)' : 'var(--red)'; }
            });
          }
          recapCard.appendChild(tbl);
          fitMLBTableToContainer(tbl);
          const tally = document.createElement('div');
          tally.className = 'l10-tally';
          tally.innerHTML = `Props: <b>${yW}W-${yL}L</b> &middot; <span style="color:${uColor}">${yU >= 0 ? '+' : ''}${yU.toFixed(2)}u</span>`;
          recapCard.appendChild(tally);

          // Yesterday's Leans (0.65-0.72 watchlist, both sides) — full table beneath picks
          const yLeans = leanGraded.filter(p => p.date === yesterdayStr);
          if (yLeans.length > 0) {
            const yLW = yLeans.filter(p => p.result === 'WIN').length;
            const yLL = yLeans.filter(p => p.result === 'LOSS').length;
            const yLU = calcMLBPropsUnits(yLeans);
            const yLColor = yLU >= 0 ? 'var(--green)' : 'var(--red)';
            // Header: just the title + count (matches Today's Picks lean header style)
            const leanHeader = document.createElement('div');
            leanHeader.style.cssText = 'margin-top:14px;padding-top:8px;border-top:1px dashed rgba(244,180,0,0.4);font-size:12px;color:#f4b400;font-weight:600';
            leanHeader.textContent = `Leans — .65-.72 (${yLeans.length})`;
            recapCard.appendChild(leanHeader);
            const lTbl = document.createElement('table');
            lTbl.className = 'data';
            lTbl.style.cssText = 'width:100%;border-collapse:collapse;margin-top:8px';
            const lhRow = lTbl.createTHead().insertRow();
            ['Name','Team','Opp','Proj','Line','Edge','%','Odds','Actual','O/U','W/L'].forEach((h, i) => {
              const th = document.createElement('th');
              th.textContent = h;
              th.style.cssText = 'padding:6px 10px;border-bottom:1px solid rgba(255,255,255,0.1);' + (i === 0 ? 'text-align:left' : 'text-align:center');
              lhRow.appendChild(th);
            });
            const lTbody = lTbl.createTBody();
            for (const p of yLeans.sort((a, b) => (b.pCover || 0) - (a.pCover || 0))) {
              const row = lTbody.insertRow();
              row.style.borderBottom = '1px solid rgba(255,255,255,0.05)';
              const yEdge = (p.proj != null && p.line != null) ? +(p.proj - p.line).toFixed(1) : null;
              const yEdgeStr = yEdge != null ? (yEdge > 0 ? '+'+yEdge : String(yEdge)) : '—';
              const yPrice = p.odds != null ? (p.odds > 0 ? '+'+p.odds : String(p.odds)) : '—';
              const pcStr = p.pCover != null ? (p.pCover * 100).toFixed(1) + '%' : '—';
              [displayName(p), p.team||'', p.opp||'',
               String(p.proj), p.line!=null?String(p.line):'—', yEdgeStr, pcStr, yPrice,
               p.actual!=null?String(p.actual):'—',
               p.would_be_pick === 'OVER' ? 'O' : 'U',
               p.result==='WIN'?'W':'L'].forEach((v, i) => {
                const td = row.insertCell();
                td.textContent = v;
                td.style.cssText = 'padding:4px 4px;text-align:center';
                if (i === 0) { td.style.textAlign = 'left'; td.style.fontWeight = '600'; }
                if (i === 1 || i === 2) td.style.color = '#999';
                if (i === 3) td.style.color = p.proj > p.line ? 'var(--green)' : p.proj < p.line ? 'var(--red)' : '';
                if (i === 5 && yEdge != null) td.style.color = yEdge > 0 ? 'var(--green)' : yEdge < 0 ? 'var(--red)' : '#999';
                if (i === 6) td.style.color = '#aaa';
                if (i === 7) td.style.color = '#999';
                if (i === 9) { td.style.fontWeight = '700'; td.style.color = p.would_be_pick === 'OVER' ? 'var(--green)' : 'var(--red)'; }
                if (i === 10) { td.style.fontWeight = '700'; td.style.color = p.result === 'WIN' ? 'var(--green)' : 'var(--red)'; }
              });
            }
            recapCard.appendChild(lTbl);
            fitMLBTableToContainer(lTbl);
            // Tally below the lean table — mirrors the Props tally below picks
            const leanTally = document.createElement('div');
            leanTally.style.cssText = 'margin-top:6px;font-size:12px;color:#f4b400;font-style:italic';
            leanTally.innerHTML = `Leans: <b>${yLW}W-${yLL}L</b> &middot; <span style="color:${yLColor}">${yLU >= 0 ? '+' : ''}${yLU.toFixed(2)}u</span>`;
            recapCard.appendChild(leanTally);
          }
          el.appendChild(recapCard);
        }

        // --- Reddit-format summary card (defined here, rendered at bottom) ---
        // Auto-generated copy-pasteable tally of season / weekly / yesterday
        // performance — formatted for posting on Reddit. Assigned to the
        // outer scope so it can be invoked after All Picks tables render.
        _renderRedditCard = function renderRedditCard() {
          // --- Baseline anchor (manually-tracked Reddit history through cutoff) ---
          // Anything dated AT OR BEFORE cutoff is collapsed into these hardcoded
          // tallies — matches what's been posted on Reddit. Dates AFTER cutoff
          // come from the live mlb-props.json and accumulate automatically.
          // 5/19 cutoff bump: backfill re-scored 5/19 with a slightly tighter
          // emp_std (full walk-forward regen), nudging Griffin Canning's
          // pCover from 0.648 (live) to 0.651 (post-backfill) — crossing the
          // 0.65 lean threshold and adding a phantom 1W to the lean tally.
          // Live 5/19 (risk-to-win-1u sizing): picks 3W-2L +0.64u
          // (Roupp -136 L = -1.36u, Warren +124 L = -1.00u), leans 0W-4L
          // -4.98u (McLean -158, Sheehan -132, Nelson -102, Detmers -106).
          // Pin via cutoff so the widget matches what was posted on Reddit.
          const BASELINE = {
            cutoff: '2026-05-21',
            picks: {
              total:    { w: 97, l: 76, u:  4.96 },
              weekly:   { w:  8, l:  5, u:  1.58 },  // 5/18–5/21
              yesterday:{ w:  3, l:  0, u:  3.18 },  // 5/21
            },
            leans: {
              total:    { w: 48, l: 35, u:  3.88 },
              weekly:   { w:  5, l:  9, u: -6.22 },  // 5/18–5/21
              yesterday:{ w:  0, l:  0, u:  0.00 },  // 5/21
            },
          };

          const allGradedPicks = picks.filter(p =>
            p.result === 'WIN' || p.result === 'LOSS'
          );
          const allGradedLeans = (data.props || []).filter(p =>
            isLean(p) && (p.result === 'WIN' || p.result === 'LOSS')
          );
          if (allGradedPicks.length === 0 && allGradedLeans.length === 0) return;

          // Anything after the baseline cutoff date — the actually-new picks.
          const newPicks = allGradedPicks.filter(p => p.date && p.date > BASELINE.cutoff);
          const newLeans = allGradedLeans.filter(p => p.date && p.date > BASELINE.cutoff);

          // Weekly window logic:
          //   - On Monday: previous Mon-Sun (a fully-completed week),
          //     labeled "last Weekly".
          //   - Tue-Sun: current week's Mon through yesterday, labeled "Weekly".
          const _today = new Date(yesterdayStr + 'T12:00:00');
          _today.setDate(_today.getDate() + 1);
          const _todayDow = _today.getDay();   // 0=Sun,1=Mon,..6=Sat
          const isMonday = _todayDow === 1;
          let wStart;
          let wEnd;
          let weekLabel;
          if (isMonday) {
            wEnd = new Date(yesterdayStr + 'T12:00:00');
            wStart = new Date(yesterdayStr + 'T12:00:00');
            wStart.setDate(wStart.getDate() - 6);
            weekLabel = 'last Weekly';
          } else {
            wEnd = new Date(yesterdayStr + 'T12:00:00');
            const daysSinceMon = (_todayDow + 6) % 7;
            wStart = new Date(_today);
            wStart.setDate(_today.getDate() - daysSinceMon);
            weekLabel = 'Weekly';
          }
          const weeklyStartStr = wStart.toISOString().slice(0, 10);
          const weeklyEndStr = wEnd.toISOString().slice(0, 10);

          const fmtMD = (dStr) => {
            if (!dStr) return '';
            const parts = dStr.split('-');
            return `${parseInt(parts[1], 10)}/${parseInt(parts[2], 10)}`;
          };

          // Helper: combine baseline tally with array of new picks
          function combine(base, arr) {
            const w = base.w + arr.filter(p => p.result === 'WIN').length;
            const l = base.l + arr.filter(p => p.result === 'LOSS').length;
            const u = base.u + calcMLBPropsUnits(arr);
            return { w, l, u };
          }
          function fmt(tally) {
            return `${tally.w}-${tally.l} ${tally.u >= 0 ? '+' : ''}${tally.u.toFixed(2)}u`;
          }

          // TOTAL = baseline + everything post-cutoff
          const totalPicks = combine(BASELINE.picks.total, newPicks);
          const totalLeans = combine(BASELINE.leans.total, newLeans);

          // WEEKLY: if the weekly window is entirely at-or-before cutoff,
          // show baseline weekly. Otherwise compute from new (post-cutoff) data,
          // and if the window straddles the cutoff date itself, fold in
          // BASELINE.yesterday so the cutoff-day contribution isn't dropped.
          let wPicksTally, wLeansTally;
          if (weeklyEndStr <= BASELINE.cutoff) {
            wPicksTally = BASELINE.picks.weekly;
            wLeansTally = BASELINE.leans.weekly;
          } else {
            const inWeek = (d) => d && d >= weeklyStartStr && d <= weeklyEndStr;
            const cutoffInWeek = BASELINE.cutoff >= weeklyStartStr
                                 && BASELINE.cutoff <= weeklyEndStr;
            const pBase = cutoffInWeek ? BASELINE.picks.yesterday : {w:0,l:0,u:0};
            const lBase = cutoffInWeek ? BASELINE.leans.yesterday : {w:0,l:0,u:0};
            wPicksTally = combine(pBase, newPicks.filter(p => inWeek(p.date)));
            wLeansTally = combine(lBase, newLeans.filter(p => inWeek(p.date)));
          }

          // YESTERDAY: use baseline if yesterday == cutoff, else compute fresh
          let yPicksTally, yLeansTally;
          if (yesterdayStr === BASELINE.cutoff) {
            yPicksTally = BASELINE.picks.yesterday;
            yLeansTally = BASELINE.leans.yesterday;
          } else {
            yPicksTally = combine({w:0,l:0,u:0}, newPicks.filter(p => p.date === yesterdayStr));
            yLeansTally = combine({w:0,l:0,u:0}, newLeans.filter(p => p.date === yesterdayStr));
          }

          const weekRange = `${fmtMD(weeklyStartStr)}–${fmtMD(weeklyEndStr)}`;
          const yMD = fmtMD(yesterdayStr);

          // --- Today's picks + leans for copy-paste ---
          // Mirrors the void/confirmed logic used by the Today's Picks card.
          const _gameStatusesR = data.gameStatuses || {};
          const _VOID_GS_R = new Set([
            'Postponed','Cancelled','Canceled','Suspended',
            'Postponed Inclement Weather','Postponed Rain',
            'Suspended: Inclement Weather','Suspended: Rain',
          ]);
          const _isVoid = (p) => {
            const gs = _gameStatusesR[p.team] || _gameStatusesR[p.opp] || '';
            return _VOID_GS_R.has(gs);
          };
          const _LOCK_R = new Set(['lineup_confirmed','game_started','final']);
          const _MARKET_SUFFIX = {
            strikeouts: 'k', outs: 'outs', hits_allowed: 'h', game_hits: 'h',
          };
          // Sort each bucket by pCover descending — matches the dashboard
          // tables' default order so the Reddit copy mirrors what's onscreen.
          const _sortByPCover = (arr) => arr.slice().sort(
            (a, b) => (b.pCover || 0) - (a.pCover || 0)
          );
          const _todayPicks = _sortByPCover(
            picks.filter(p => p.date === todayStr && !_isVoid(p))
          );
          const _todayLeans = _sortByPCover(
            (data.props || []).filter(p =>
              p.date === todayStr && isLean(p) && !_isVoid(p)
            )
          );

          // --- Upgrade/downgrade detection vs previously-displayed state ---
          // On each render we snapshot every entry's current bucket
          // ('pick' | 'lean') keyed by player+market+direction in
          // localStorage. The next render compares against that snapshot
          // (only if same date) so intra-day lineup confirmations that
          // flip a row's bucket can be called out in the Reddit copy.
          // Direction for a pick is `pick` (OVER/UNDER); for a lean it's
          // stored in `would_be_pick` since `pick === 'PASS'`. Use whichever
          // is the actual betting direction.
          const _dirOf = (p) => p.pick === 'PASS' ? (p.would_be_pick || 'OVER') : p.pick;
          const _keyOf = (p) => `${displayName(p)}|${p.market}|${_dirOf(p)}`;
          // Bumped to v2: snapshot format changed from string bucket to
          // { bucket, status, annotation } so confirmations + annotations
          // stick once set, instead of recomputing each render.
          const _STORE_KEY = 'mlb-reddit-state-v2';
          let _prev = {};
          try {
            const raw = localStorage.getItem(_STORE_KEY);
            if (raw) {
              const parsed = JSON.parse(raw);
              if (parsed && parsed.date === todayStr) _prev = parsed.state || {};
            }
          } catch (_) {}

          function _annotationForDiff(prevBucket, bucket) {
            if (!prevBucket || prevBucket === bucket) {
              if (!prevBucket && bucket === 'pick') return ' (upgraded from non-pick)';
              return '';
            }
            if (bucket === 'pick' && prevBucket === 'lean') return ' (upgraded from lean)';
            if (bucket === 'lean' && prevBucket === 'pick') return ' (downgraded from pick)';
            return '';
          }

          const _currentState = {};
          function _resolveEntry(p, bucket) {
            const key = _keyOf(p);
            const prev = _prev[key] || {};
            const isConfirmed = _LOCK_R.has(p.lockState);

            // Status (confirmed/unconfirmed) is sticky: once confirmed, stays
            // confirmed for the rest of the day — even if a later run somehow
            // reverts the lockState.
            const statusConfirmed = prev.status === 'confirmed' || isConfirmed;

            // Track the bucket at first sight (when the row was unconfirmed)
            // so we can detect upgrade/downgrade vs that initial bucket when
            // confirmation eventually lands. Once an initialBucket is set, it
            // stays put.
            const initialBucket = prev.initialBucket || bucket;

            // Annotation is shown ONLY after confirmation. Once the row
            // confirms, we compute the annotation once (initial bucket vs
            // current confirmed bucket) and freeze it. Unconfirmed rows show
            // no annotation regardless of bucket changes during the day.
            let annotation = prev.annotation || '';
            if (statusConfirmed && !annotation) {
              const fresh = _annotationForDiff(initialBucket, bucket);
              if (fresh) annotation = fresh;
            }

            _currentState[key] = {
              bucket,
              initialBucket,
              status: statusConfirmed ? 'confirmed' : 'unconfirmed',
              annotation,
            };
            return { statusConfirmed, annotation };
          }

          function _fmtRow(p, bucket) {
            const name = displayName(p);
            const dir = _dirOf(p) === 'OVER' ? 'o' : 'u';
            const suffix = _MARKET_SUFFIX[p.market] || '';
            const { statusConfirmed, annotation } = _resolveEntry(p, bucket);
            const conf = statusConfirmed ? '**confirmed**' : 'unconfirmed';
            // Projection is appended ONLY when the row is confirmed —
            // unconfirmed projections can shift once the real lineup locks,
            // so withholding the number until confirmation avoids posting a
            // figure we'll have to walk back.
            const projTag = (statusConfirmed && p.proj != null)
              ? ` proj: ${Number(p.proj).toFixed(1)}`
              : '';
            // Body of the line WITHOUT the leading "* " or trailing annotation.
            // We stash this in state so that if this entry later disappears
            // from picks/leans entirely we can render it struck-through using
            // the same body text we showed today.
            const body = `${name} ${dir}${p.line}${suffix} ${conf}${projTag}`;
            _currentState[_keyOf(p)].lineText = body;
            return `* ${body}${annotation}`;
          }

          // Render current picks/leans first (this populates _currentState).
          const _pickLines = _todayPicks.map(p => _fmtRow(p, 'pick'));
          const _leanLines = _todayLeans.map(p => _fmtRow(p, 'lean'));

          // Dropped entries: previously confirmed picks/leans that no longer
          // appear in today's slate at all. Collected into a separate
          // "Today's Downgraded" section so they don't clutter the active
          // Picks/Leans blocks. Only fires for entries that were CONFIRMED
          // before disappearing — unconfirmed drops just vanish silently.
          const _droppedLines = [];
          for (const [key, prev] of Object.entries(_prev)) {
            if (_currentState[key]) continue;          // still present today
            if (prev.status !== 'confirmed') continue;  // never confirmed → no call-out
            if (!prev.lineText) continue;               // legacy entry without saved text
            _droppedLines.push(`* ~~${prev.lineText}~~ downgraded to nonpick`);
            // Persist so the strikethrough sticks on subsequent renders even
            // though the underlying entry is gone.
            _currentState[key] = { ...prev, droppedToNonPick: true };
          }

          const _picksBlock = _pickLines.length
            ? `\nToday’s Picks (${todayStr})\n\n` + _pickLines.join('\n') + '\n'
            : '';
          const _leansBlock = _leanLines.length
            ? `\nToday’s Leans (${todayStr})\n\n` + _leanLines.join('\n') + '\n'
            : '';
          const _droppedBlock = _droppedLines.length
            ? `\nToday's Downgraded\n\n` + _droppedLines.join('\n') + '\n'
            : '';

          // Persist current state for the next render. Stores bucket +
          // sticky status + sticky annotation per entry, resetting only
          // when the date rolls over.
          try {
            localStorage.setItem(_STORE_KEY, JSON.stringify({
              date: todayStr, state: _currentState,
            }));
          } catch (_) {}

          const redditText =
            `Picks:\n\n` +
            `* Total: ${fmt(totalPicks)}\n` +
            `* ${weekLabel} (${weekRange}): ${fmt(wPicksTally)}\n` +
            `* Yesterday (${yMD}): ${fmt(yPicksTally)}\n` +
            `\n` +
            `Leans:\n\n` +
            `* Total: ${fmt(totalLeans)}\n` +
            `* ${weekLabel} (${weekRange}): ${fmt(wLeansTally)}\n` +
            `* Yesterday (${yMD}): ${fmt(yLeansTally)}\n` +
            _picksBlock +
            _leansBlock +
            _droppedBlock;

          const redditCard = document.createElement('div');
          redditCard.className = 'card card-reddit';
          redditCard.style.marginBottom = '16px';

          const titleRow = document.createElement('div');
          titleRow.style.cssText = 'display:flex;align-items:center;justify-content:space-between;gap:12px';
          titleRow.appendChild(Object.assign(document.createElement('div'), {
            className: 'card-title',
            textContent: 'Reddit'
          }));
          const copyBtn = document.createElement('button');
          copyBtn.textContent = 'Copy';
          copyBtn.style.cssText = 'padding:6px 14px;background:#ff4500;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:12px;font-weight:600';
          copyBtn.addEventListener('click', () => {
            navigator.clipboard.writeText(redditText).then(() => {
              copyBtn.textContent = 'Copied!';
              setTimeout(() => { copyBtn.textContent = 'Copy'; }, 1500);
            }).catch(() => {
              copyBtn.textContent = 'Failed';
              setTimeout(() => { copyBtn.textContent = 'Copy'; }, 1500);
            });
          });
          titleRow.appendChild(copyBtn);
          redditCard.appendChild(titleRow);

          const pre = document.createElement('pre');
          pre.style.cssText = 'font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;background:rgba(255,255,255,0.04);padding:10px;border-radius:4px;margin-top:10px;white-space:pre-wrap;color:#ddd;line-height:1.5';
          pre.textContent = redditText;
          redditCard.appendChild(pre);

          el.appendChild(redditCard);
        };

        // --- Matchup History card ---
        // For each of today's actionable picks and leans, look up the
        // model's historical record vs that opponent in that direction
        // (within the Picks+Leans universe — excluding watchlist plays).
        _renderMatchupCard = function renderMatchupCard() {
          // Resolve the betting direction for any row: picks use `pick`,
          // leans store the intended side in `would_be_pick`.
          const _dirOf = (p) => p.pick === 'PASS' ? (p.would_be_pick || null) : p.pick;

          // Build TWO maps so the Dir record can be bucket-specific:
          //   byOppDirPicks[opp][OVER|UNDER] — graded actionable picks only
          //   byOppDirLeans[opp][OVER|UNDER] — graded leans only
          //   byOppAll[opp]                  — picks+leans, both directions
          const graded = (data.props || []).filter(p =>
            p.market === 'strikeouts'
            && (p.result === 'WIN' || p.result === 'LOSS')
            && p.opp
            && (p.pick === 'OVER' || p.pick === 'UNDER' || isLean(p))
          );

          // Four maps so each column can be bucket-scoped exactly:
          //   byOppDirPicks[opp][O|U] — picks, this direction       (O/U Hist on Pick rows)
          //   byOppDirLeans[opp][O|U] — leans, this direction       (O/U Hist on Lean rows)
          //   byOppPicks[opp]         — picks, both directions      (O&U Hist on Pick rows)
          //   byOppLeans[opp]         — leans, both directions      (O&U Hist on Lean rows)
          const byOppDirPicks = {};
          const byOppDirLeans = {};
          const byOppPicks = {};
          const byOppLeans = {};
          for (const p of graded) {
            const opp = p.opp;
            const dir = _dirOf(p);
            if (!dir) continue;
            const isPickRow = (p.pick === 'OVER' || p.pick === 'UNDER');
            const won = p.result === 'WIN';
            const u = (() => {
              const od = p.odds;
              if (od == null) return 0;
              const o = Number(od);
              if (o > 0) return won ? o / 100 : -1;
              return won ? 1 : -Math.abs(o) / 100;
            })();
            const dirMap = isPickRow ? byOppDirPicks : byOppDirLeans;
            if (!dirMap[opp]) dirMap[opp] = {OVER:{w:0,l:0,u:0}, UNDER:{w:0,l:0,u:0}};
            const bd = dirMap[opp][dir];
            if (won) bd.w++; else bd.l++;
            bd.u += u;
            const bucketMap = isPickRow ? byOppPicks : byOppLeans;
            if (!bucketMap[opp]) bucketMap[opp] = {w:0,l:0,u:0};
            if (won) bucketMap[opp].w++; else bucketMap[opp].l++;
            bucketMap[opp].u += u;
          }
          function bucketDirRec(opp, dir, bucket) {
            const m = bucket === 'Pick' ? byOppDirPicks : byOppDirLeans;
            return (m[opp] && dir) ? m[opp][dir] : null;
          }
          // Same bucket, BOTH directions vs this opponent.
          function bucketBothDirsRec(opp, bucket) {
            const m = bucket === 'Pick' ? byOppPicks : byOppLeans;
            return m[opp] || null;
          }
          // Both buckets (P+L), SAME direction vs this opponent.
          function allBucketsDirRec(opp, dir) {
            if (!dir) return null;
            const pk = byOppDirPicks[opp] ? byOppDirPicks[opp][dir] : null;
            const ln = byOppDirLeans[opp] ? byOppDirLeans[opp][dir] : null;
            if (!pk && !ln) return null;
            return {
              w: (pk ? pk.w : 0) + (ln ? ln.w : 0),
              l: (pk ? pk.l : 0) + (ln ? ln.l : 0),
              u: (pk ? pk.u : 0) + (ln ? ln.u : 0),
            };
          }

          // Today's actionable picks + leans (excluding void games),
          // sorted picks first (by pCover desc), then leans (by pCover desc).
          const _gameStatusesM = data.gameStatuses || {};
          const _VOID_M = new Set([
            'Postponed','Cancelled','Canceled','Suspended',
            'Postponed Inclement Weather','Postponed Rain',
            'Suspended: Inclement Weather','Suspended: Rain',
          ]);
          const _voidRow = (p) => _VOID_M.has(
            _gameStatusesM[p.team] || _gameStatusesM[p.opp] || ''
          );
          const todayPicks = picks
            .filter(p => p.date === todayStr && !_voidRow(p))
            .sort((a, b) => (b.pCover || 0) - (a.pCover || 0));
          const todayLeans = (data.props || [])
            .filter(p => p.date === todayStr && isLean(p) && !_voidRow(p))
            .sort((a, b) => (b.pCover || 0) - (a.pCover || 0));
          const rows = [...todayPicks.map(p => ({p, bucket:'Pick'})),
                        ...todayLeans.map(p => ({p, bucket:'Lean'}))];
          if (rows.length === 0) return;

          const card = document.createElement('div');
          card.className = 'card card-games';
          card.style.marginBottom = '16px';
          card.appendChild(Object.assign(document.createElement('div'), {
            className:'card-title',
            textContent:`Matchup History VS OPP — Today’s Picks & Leans (${todayStr})`,
          }));

          const wrap = document.createElement('div');
          wrap.className = 'props-table-wrap';
          const tbl = document.createElement('table');
          tbl.style.cssText = 'width:100%;border-collapse:collapse;margin-top:8px';
          const cols = [
            ['Player', 'left'],
            ['Tm',     'center'],
            ['Opp',    'center'],
            ['LU K%',  'right'],
            ['Tm K%',  'right'],
            ['O/U',    'center'],
            ['Line',   'center'],
            ['O//U',    'center'],
            ['WR%',     'right'],
            ['Units',   'right'],
            ['O&&U',    'center'],
            ['WR%',     'right'],
            ['Units',   'right'],
            ['P+L O//U','center'],
            ['WR%',     'right'],
            ['Units',   'right'],
          ];
          const hr = tbl.createTHead().insertRow();
          cols.forEach(([label, align]) => {
            const th = document.createElement('th');
            th.textContent = label;
            th.style.cssText = `padding:6px 8px;text-align:${align};border-bottom:1px solid rgba(255,255,255,0.1);font-size:11px;color:#999`;
            hr.appendChild(th);
          });

          const tb = tbl.createTBody();
          const fmtRec = (r) => r ? `${r.w}-${r.l}` : '—';
          const fmtWR  = (r) => (r && (r.w + r.l) > 0)
            ? (r.w / (r.w + r.l) * 100).toFixed(1) + '%' : '—';
          const fmtU   = (r) => r
            ? (r.u >= 0 ? '+' : '') + r.u.toFixed(2) + 'u' : '—';
          const wrColor = (r) => {
            if (!r || (r.w + r.l) === 0) return '#888';
            const w = r.w / (r.w + r.l);
            if (w >= 0.70) return 'var(--green)';
            if (w >= 0.55) return '#ccc';
            return 'var(--red)';
          };
          const uColor = (r) => {
            if (!r) return '#888';
            if (r.u > 0) return 'var(--green)';
            if (r.u < 0) return 'var(--red)';
            return '#ccc';
          };
          function appendSectionHeader(label, color) {
            const tr = tb.insertRow();
            const td = tr.insertCell();
            td.colSpan = cols.length;
            td.textContent = label;
            td.style.cssText = `padding:8px 8px 4px;font-size:11px;color:${color};font-weight:700;letter-spacing:0.08em;text-transform:uppercase;background:rgba(255,255,255,0.02);border-top:1px solid rgba(255,255,255,0.08)`;
          }
          function appendDataRow(p, bucket) {
            const tr = tb.insertRow();
            const dir = _dirOf(p);
            const opp = p.opp || '';
            const recBktDir   = bucketDirRec(opp, dir, bucket);
            const recBktBoth  = bucketBothDirsRec(opp, bucket);
            const recAllDir   = allBucketsDirRec(opp, dir);
            const _tmK = (p.opp_team_k_pct != null) ? p.opp_team_k_pct.toFixed(1) + '%' : '—';
            const _luK = (p.lineup_k_pct   != null) ? p.lineup_k_pct.toFixed(1)   + '%' : '—';
            const cells = [
              {v: displayName(p), color: '#fff', weight:'600', align:'left'},
              {v: p.team || '', color:'#999'},
              {v: opp,           color:'#999'},
              {v: _luK, color:'#ccc', align:'right'},
              {v: _tmK, color:'#ccc', align:'right'},
              {v: dir === 'OVER' ? 'O' : dir === 'UNDER' ? 'U' : '—', color: dir === 'OVER' ? 'var(--green)' : dir === 'UNDER' ? 'var(--red)' : '#999', weight:'600'},
              {v: p.line != null ? String(p.line) : '—'},
              {v: fmtRec(recBktDir)},
              {v: fmtWR(recBktDir),  color: wrColor(recBktDir),  align:'right'},
              {v: fmtU(recBktDir),   color: uColor(recBktDir),   align:'right'},
              {v: fmtRec(recBktBoth)},
              {v: fmtWR(recBktBoth), color: wrColor(recBktBoth), align:'right'},
              {v: fmtU(recBktBoth),  color: uColor(recBktBoth),  align:'right'},
              {v: fmtRec(recAllDir)},
              {v: fmtWR(recAllDir),  color: wrColor(recAllDir),  align:'right'},
              {v: fmtU(recAllDir),   color: uColor(recAllDir),   align:'right'},
            ];
            cells.forEach((c, i) => {
              const td = tr.insertCell();
              td.textContent = c.v;
              td.style.padding = '5px 8px';
              td.style.fontSize = '12px';
              td.style.textAlign = c.align || cols[i][1];
              if (c.color)  td.style.color = c.color;
              if (c.weight) td.style.fontWeight = c.weight;
            });
          }
          if (todayPicks.length) {
            appendSectionHeader(`Picks (${todayPicks.length})`, '#a78bfa');
            for (const p of todayPicks) appendDataRow(p, 'Pick');
          }
          if (todayLeans.length) {
            appendSectionHeader(`Leans (${todayLeans.length})`, 'var(--yellow)');
            for (const p of todayLeans) appendDataRow(p, 'Lean');
          }
          wrap.appendChild(tbl);
          card.appendChild(wrap);

          // --- Read / take section ---
          // Auto-generated one-liner per row reading the historical record.
          // Tiers (by Dir vs Opp record), with sample-size guard:
          //   n >= 4 AND WR >= 0.80 -> Elite (green)
          //   n >= 4 AND WR >= 0.65 -> Solid (green-dim)
          //   n >= 4 AND WR <= 0.45 -> Caution (red)
          //   n >= 4 AND 0.45<WR<0.65 -> Neutral (gray)
          //   n < 4 -> Small sample (gray, hedge language)
          // Extra callouts:
          //   - Lean whose all-opp WR >= 0.85 on n>=8 -> "matchup arguably elevates to a pick"
          //   - 100% record (any sample) -> "perfect cohort"
          //   - 0 units or negative units in cohort -> warn
          const readBlock = document.createElement('div');
          readBlock.style.cssText = 'padding:14px 6px 4px;border-top:1px solid rgba(255,255,255,0.06);margin-top:10px';
          const readTitle = document.createElement('div');
          readTitle.style.cssText = 'font-size:12px;color:#bbb;font-weight:600;margin-bottom:8px;letter-spacing:0.05em;text-transform:uppercase';
          readTitle.textContent = 'Read';
          readBlock.appendChild(readTitle);

          function classify(rec) {
            const n = rec ? rec.w + rec.l : 0;
            if (n === 0) return {tier:'none',  label:'no history', color:'#888'};
            if (n < 4)   return {tier:'small', label:'small sample', color:'#aaa'};
            const wr = rec.w / n;
            if (wr >= 0.80) return {tier:'elite',   label:'elite', color:'var(--green)'};
            if (wr >= 0.65) return {tier:'solid',   label:'solid', color:'#9ee493'};
            if (wr >= 0.50) return {tier:'neutral', label:'neutral', color:'#ccc'};
            return                {tier:'caution', label:'caution', color:'var(--red)'};
          }

          // Build ranked sets per bucket so picks/leans render separately,
          // each sorted by direction-record tier then units.
          function annotate(arr) {
            return arr.map(r => {
              const dir = _dirOf(r.p);
              const rec = bucketDirRec(r.p.opp, dir, r.bucket);
              // For the Lean upgrade flag we deliberately widen the lens to
              // P+L same-direction — the flag's job is to surface matchups
              // strong enough that a Lean deserves play, and the broadest
              // direction-matched cohort is the right signal for that.
              // Main take text remains bucket-locked (uses r.rec).
              const allRec = allBucketsDirRec(r.p.opp, dir);
              return {...r, dir, rec, allRec, cls: classify(rec)};
            });
          }
          // Order reads by model confidence (pCover) descending so the
          // highest-confidence play floats to the top of each section.
          function rankByPCover(arr) {
            return arr.sort((a, b) => (b.p.pCover || 0) - (a.p.pCover || 0));
          }
          const pickEntries = rankByPCover(annotate(todayPicks.map(p => ({p, bucket:'Pick'}))));
          const leanEntries = rankByPCover(annotate(todayLeans.map(p => ({p, bucket:'Lean'}))));

          function renderReadRow(r) {
            const dirRec = r.rec;
            const allRec = r.allRec;
            const n = dirRec ? dirRec.w + dirRec.l : 0;
            const wr = (n > 0) ? (dirRec.w / n * 100).toFixed(1) : null;
            const allN = allRec ? allRec.w + allRec.l : 0;
            const allWR = (allN > 0) ? (allRec.w / allN * 100).toFixed(1) : null;
            const allU = allRec ? allRec.u : 0;
            const line = document.createElement('div');
            line.style.cssText = 'padding:6px 8px;margin-bottom:4px;font-size:12px;border-left:3px solid '+r.cls.color+';background:rgba(255,255,255,0.025);border-radius:0 4px 4px 0;line-height:1.45';
            const nameSpan = `<strong style="color:#fff">${displayName(r.p)}</strong>`;
            const dirSpan = `<span style="color:${r.dir==='OVER'?'var(--green)':'var(--red)'};font-weight:600">${r.dir} ${r.p.line}</span>`;
            const oppSpan = `<span style="color:#ccc">vs ${r.p.opp}</span>`;
            const pCover = r.p.pCover || 0;
            const pcPct = (pCover * 100).toFixed(1);
            const bktN = n;
            const bktWR = bktN > 0 ? dirRec.w / bktN : null;
            const broadN = allN;
            const broadWR = broadN > 0 ? allRec.w / broadN : null;
            const broadU = allU;
            const sg = (t) => `<strong style="color:var(--green)">${t}</strong>`;
            const sy = (t) => `<strong style="color:var(--yellow)">${t}</strong>`;
            const sr = (t) => `<strong style="color:var(--red)">${t}</strong>`;
            const recOf = (rec) => rec ? `${rec.w}-${rec.l}` : '—';
            const uOf   = (rec) => rec ? ((rec.u>=0?'+':'')+rec.u.toFixed(2)+'u') : '—';
            const bktPretty   = (rec) => rec ? `${recOf(rec)} ${uOf(rec)}` : '—';

            // Tier booleans for narrative branching.
            const elite     = bktWR != null && bktN >= 4 && bktWR >= 0.80;
            const solid     = bktWR != null && bktN >= 4 && bktWR >= 0.65 && bktWR < 0.80;
            const coin      = bktWR != null && bktN >= 4 && bktWR >= 0.45 && bktWR < 0.65;
            const caution   = bktWR != null && bktN >= 4 && bktWR < 0.45;
            const small     = bktN > 0 && bktN < 4;
            const empty     = bktN === 0;
            const bElite    = broadWR != null && broadN >= 6 && broadWR >= 0.80 && broadU >= 4;
            const bSolid    = broadWR != null && broadN >= 6 && broadWR >= 0.65 && broadU > 0;
            const bCaution  = broadWR != null && broadN >= 6 && broadWR <= 0.45 && broadU <= -2;
            const widerThanBkt = broadN > bktN;
            const dirWord = r.dir.toLowerCase() + 's';
            const oppStr = r.p.opp;

            // Build narrative — written like a quick read I'd give over the shoulder.
            let take = '';
            if (r.bucket === 'Pick') {
              if (elite && (bElite || (broadWR && broadWR >= 0.80))) {
                take = `${sg('Cleanest spot of the night.')} Picks-only is ${sg(recOf(dirRec))} and the broader matchup widens to ${sg(recOf(allRec))} (${(broadWR*100).toFixed(1)}%, ${uOf(allRec)}). Model fires at ${pcPct}% — size up.`;
              } else if (elite && widerThanBkt && bSolid) {
                take = `${sg(recOf(dirRec))} picks-only, ${sg(recOf(allRec))} once you widen the lens (${(broadWR*100).toFixed(1)}%, ${uOf(allRec)}). ${pcPct}% model — play with conviction.`;
              } else if (elite) {
                take = `Picks-only is ${sg(recOf(dirRec))} (${(bktWR*100).toFixed(1)}%, ${uOf(dirRec)}) at ${pcPct}% model confidence. Comfortable play.`;
              } else if (solid && bSolid) {
                take = `Picks-only is ${recOf(dirRec)} (${(bktWR*100).toFixed(1)}%, ${uOf(dirRec)}) and the broader cohort backs it (${recOf(allRec)}, ${(broadWR*100).toFixed(1)}%, ${uOf(allRec)}). ${pcPct}% model — solid play.`;
              } else if (coin && bCaution) {
                take = `${sr('Hardest read on the slate.')} Picks-only is ${recOf(dirRec)} and the broader matchup makes it worse (${sr(recOf(allRec))}, ${(broadWR*100).toFixed(1)}%, ${uOf(allRec)}). Model fires at ${pcPct}% but I'd skip or token.`;
              } else if (coin && bSolid) {
                take = `Picks-only is a flip (${recOf(dirRec)}, ${(bktWR*100).toFixed(1)}%), but the broader matchup widens to ${sy(recOf(allRec))} (${(broadWR*100).toFixed(1)}%, ${uOf(allRec)}). ${pcPct}% model — smaller play than the elite spots, but I'm in.`;
              } else if (coin) {
                take = `Picks-only is mixed (${recOf(dirRec)}, ${(bktWR*100).toFixed(1)}%, ${uOf(dirRec)}) and the broader cohort doesn't move the needle (${recOf(allRec)}). ${pcPct}% — lowest-conviction pick of the slate, small if at all.`;
              } else if (caution) {
                take = `${sr('Picks-only has bled here')} (${sr(recOf(dirRec))}, ${(bktWR*100).toFixed(1)}%, ${uOf(dirRec)}) and broader isn't a rescue (${recOf(allRec)}). ${pcPct}% model — pass.`;
              } else if (small && (bElite || (broadWR && broadWR >= 0.80))) {
                take = `Picks sample is thin (${recOf(dirRec)}) but ${sg(`P+L ${dirWord} vs ${oppStr} are ${recOf(allRec)} (${(broadWR*100).toFixed(1)}%, ${uOf(allRec)})`)}. ${pcPct}% model — I'm in.`;
              } else if (small && bSolid) {
                take = `Bucket sample is small (${recOf(dirRec)}) but the broader matchup hasn't burned anyone (${recOf(allRec)}, ${(broadWR*100).toFixed(1)}%, ${uOf(allRec)}). Play at ${pcPct}% confidence.`;
              } else if (small) {
                take = `Tiny sample both ways (${recOf(dirRec)} picks, ${recOf(allRec)} broader). ${pcPct}% — model is the only thing saying yes. Standard size.`;
              } else if (empty) {
                take = `No prior picks vs ${oppStr} ${r.dir.toLowerCase()} — flying on ${pcPct}% model alone. Half-unit play.`;
              } else {
                take = `Picks-only ${recOf(dirRec)} (${(bktWR*100).toFixed(1)}%, ${uOf(dirRec)}). Broader ${recOf(allRec)}. Model at ${pcPct}%.`;
              }
            } else {
              // Lean rows
              if (bElite && (small || empty)) {
                take = `${sy('Classified as a Lean but the matchup says play.')} Bucket alone is thin (${recOf(dirRec)}) but ${sg(`P+L ${dirWord} vs ${oppStr} are ${recOf(allRec)} (${(broadWR*100).toFixed(1)}%, ${uOf(allRec)})`)}. ${pcPct}% model — treat this like a pick.`;
              } else if (bElite) {
                take = `${sy('Lean by the book')}, but the broader matchup is dominant (${sg(recOf(allRec))}, ${(broadWR*100).toFixed(1)}%, ${uOf(allRec)}). Bucket alone is ${recOf(dirRec)} (${(bktWR*100).toFixed(1)}%). ${pcPct}% model — full play.`;
              } else if (elite && bSolid) {
                take = `Lean band, but leans-only history is ${sg(recOf(dirRec))} (${(bktWR*100).toFixed(1)}%, ${uOf(dirRec)}) and the broader cohort holds (${recOf(allRec)}, ${(broadWR*100).toFixed(1)}%). ${pcPct}% model — spot play.`;
              } else if (elite) {
                take = `${recOf(dirRec)} leans-only vs ${oppStr} (${(bktWR*100).toFixed(1)}%, ${uOf(dirRec)}). Bottom of the lean band at ${pcPct}% but the numbers are clean.`;
              } else if (bSolid && (small || empty)) {
                take = `Sample's thin (${recOf(dirRec)}) but the broader matchup nudges toward play (${recOf(allRec)}, ${(broadWR*100).toFixed(1)}%, ${uOf(allRec)}). Small play at ${pcPct}%.`;
              } else if (bCaution) {
                take = `${sr('Skip.')} Leans-only is ${recOf(dirRec)} and the broader cohort is actively bad (${sr(recOf(allRec))}, ${(broadWR*100).toFixed(1)}%, ${uOf(allRec)}). ${pcPct}% model but history disagrees hard.`;
              } else if (small || empty) {
                take = `Thinnest sample of the night — ${recOf(dirRec)} bucket, ${recOf(allRec)} broader. ${pcPct}% isn't enough by itself. Pass or token.`;
              } else if (coin || caution) {
                take = `Leans-only has not been kind here (${recOf(dirRec)}, ${(bktWR*100).toFixed(1)}%, ${uOf(dirRec)}). Broader ${recOf(allRec)}. ${pcPct}% — I'd pass unless broader looks much better.`;
              } else {
                take = `Leans-only ${recOf(dirRec)} (${(bktWR*100).toFixed(1)}%, ${uOf(dirRec)}). Broader ${recOf(allRec)}. Model ${pcPct}%.`;
              }
            }
            if (dirRec && dirRec.l === 0 && dirRec.w >= 4) {
              take += ` ${sg(`Perfect ${dirRec.w}-0 cohort in-bucket.`)}`;
            }
            line.innerHTML = `${nameSpan} ${dirSpan} ${oppSpan} <span style="color:#888">@ ${pcPct}%</span> — ${take}`;
            return line;
          }

          function appendReadSection(label, color, entries) {
            if (!entries.length) return;
            const sub = document.createElement('div');
            sub.style.cssText = `font-size:11px;color:${color};font-weight:700;letter-spacing:0.08em;text-transform:uppercase;margin:10px 0 6px;padding-left:2px`;
            sub.textContent = `${label} (${entries.length})`;
            readBlock.appendChild(sub);
            for (const r of entries) readBlock.appendChild(renderReadRow(r));
          }
          appendReadSection('Picks', '#a78bfa', pickEntries);
          appendReadSection('Leans', 'var(--yellow)', leanEntries);

          card.appendChild(readBlock);

          // Footer caption — definition list with bolded column names.
          const note = document.createElement('div');
          note.style.cssText = 'padding:12px 4px 4px;color:#999;font-size:11px;line-height:1.7';
          const defStyle = 'color:#bbb;font-weight:600;font-family:ui-monospace,Menlo,Consolas,monospace';
          note.innerHTML = `
            <div style="margin-bottom:6px"><span style="${defStyle}">O//U</span> &nbsp;&nbsp; Picks/Leans + direction VS OPP (narrowest cohort)</div>
            <div style="margin-bottom:6px"><span style="${defStyle}">O&amp;&amp;U</span> &nbsp; Picks/Leans, both directions combined VS OPP</div>
            <div style="margin-bottom:6px"><span style="${defStyle}">P+L O//U</span> Picks + Leans combined, this direction only VS OPP</div>
            <div style="margin-top:8px;padding-top:6px;border-top:1px solid rgba(255,255,255,0.05);color:#888;font-style:italic">
              Pick rows count picks-only history · Lean rows count leans-only · Watchlist (&lt;0.65) excluded<br>
              Tiers: <span style="color:var(--green)">Elite ≥80%</span> · <span style="color:#9ee493">Solid ≥65%</span> · <span style="color:#ccc">Neutral ≥50%</span> · <span style="color:var(--red)">Caution &lt;50%</span> · <span style="color:#aaa">Small &lt;4 samples</span>
            </div>
          `;
          card.appendChild(note);

          el.appendChild(card);

          // ── Pitcher History — drill into one pitcher's full season log ──
          // Sits under Matchup History. Dropdown of TODAY's projected pitchers;
          // table shows every projection ever made for the selected pitcher.
          // Useful when a Soriano/Sugano-style pattern shows up — see at-a-glance
          // every prior pick/lean/pass and how the model has graded them.
          const phCard = document.createElement('div');
          phCard.className = 'card card-games';
          phCard.style.marginBottom = '16px';
          phCard.appendChild(Object.assign(document.createElement('div'), {
            className:'card-title',
            textContent:`Pitcher History — Today's Probables (${todayStr})`,
          }));

          // Build list of today's projected pitchers (any market entry for today).
          const _todayPropsAll = (data.props || []).filter(p => p.date === todayStr);
          // Group by displayName + team so duplicates collapse (one entry per
          // probable). Each entry tracks { name, team, opp, pickType, pCover, pid }
          // so we can sort by conviction.
          const _pitcherKey = (p) => `${displayName(p)}|${p.team || ''}`;
          const _byPitcher = new Map();
          for (const p of _todayPropsAll) {
            const k = _pitcherKey(p);
            const prev = _byPitcher.get(k);
            const score = (p.pCover || 0);
            if (!prev || score > prev.pCover) {
              _byPitcher.set(k, {
                key: k,
                name: displayName(p),
                team: p.team || '',
                opp:  p.opp  || '',
                pCover: score,
                pick: p.pick,
                isLean: isLean(p),
              });
            }
          }
          const _probables = [...(_byPitcher.values() || [])].sort((a, b) => {
            // Picks first (by pCover desc), then leans (by pCover desc),
            // then everything else (by pCover desc).
            const rank = (x) => {
              if (x.pick === 'OVER' || x.pick === 'UNDER') return 0;
              if (x.isLean) return 1;
              return 2;
            };
            const ra = rank(a), rb = rank(b);
            if (ra !== rb) return ra - rb;
            return (b.pCover || 0) - (a.pCover || 0);
          });

          if (_probables.length === 0) {
            const empty = document.createElement('div');
            empty.style.cssText = 'padding:12px;color:#888;font-size:12px;font-style:italic';
            empty.textContent = 'No probable pitchers projected today.';
            phCard.appendChild(empty);
            el.appendChild(phCard);
          } else {
            // Row 1: dropdown + summary on the right
            const ctrlRow = document.createElement('div');
            ctrlRow.style.cssText = 'display:flex;align-items:center;gap:10px;padding:8px 4px 6px;flex-wrap:wrap';
            const selLabel = document.createElement('label');
            selLabel.textContent = 'Pitcher:';
            selLabel.style.cssText = 'font-size:12px;color:#bbb;font-weight:600';
            const sel = document.createElement('select');
            sel.style.cssText = 'background:rgba(255,255,255,0.05);color:#fff;border:1px solid rgba(255,255,255,0.15);border-radius:4px;padding:6px 10px;font-size:12px;min-width:260px;cursor:pointer';
            _probables.forEach((pr, i) => {
              const opt = document.createElement('option');
              opt.value = pr.key;
              const tag = (pr.pick === 'OVER' || pr.pick === 'UNDER')
                ? ` [PICK ${pr.pCover ? (pr.pCover*100).toFixed(1)+'%' : ''}]`
                : (pr.isLean ? ` [LEAN ${pr.pCover ? (pr.pCover*100).toFixed(1)+'%' : ''}]` : '');
              opt.textContent = `${pr.name} (${pr.team} vs ${pr.opp})${tag}`;
              sel.appendChild(opt);
            });
            ctrlRow.appendChild(selLabel);
            ctrlRow.appendChild(sel);

            const summarySpan = document.createElement('div');
            summarySpan.style.cssText = 'margin-left:auto;font-size:12px;color:#999;display:flex;flex-direction:column;align-items:flex-end;gap:2px;line-height:1.4';
            ctrlRow.appendChild(summarySpan);

            // Filter toggles row — flush left, sits ABOVE the dropdown row.
            const filterRow = document.createElement('div');
            filterRow.style.cssText = 'display:flex;gap:4px;padding:8px 4px 4px';
            const FILTERS = [
              { key: 'ALL',  label: 'All',    color: '#ccc'           },
              { key: 'PICK', label: 'Picks',  color: '#a78bfa'        },
              { key: 'LEAN', label: 'Leans',  color: 'var(--yellow)'  },
              { key: 'PASS', label: 'Passes', color: '#888'           },
            ];
            let _activeFilter = 'ALL';
            const _filterBtns = {};
            FILTERS.forEach(f => {
              const b = document.createElement('button');
              b.textContent = f.label;
              b.dataset.key = f.key;
              b.style.cssText = `padding:5px 12px;font-size:11px;font-weight:600;border:1px solid rgba(255,255,255,0.15);background:transparent;color:${f.color};border-radius:4px;cursor:pointer;transition:all 0.15s`;
              b.addEventListener('click', () => {
                _activeFilter = f.key;
                _styleFilterBtns();
                renderPitcherHistory(sel.value);
              });
              _filterBtns[f.key] = b;
              filterRow.appendChild(b);
            });
            function _styleFilterBtns() {
              FILTERS.forEach(f => {
                const b = _filterBtns[f.key];
                const active = f.key === _activeFilter;
                b.style.background = active ? f.color : 'transparent';
                b.style.color = active ? '#0a0a0a' : f.color;
                b.style.borderColor = active ? f.color : 'rgba(255,255,255,0.15)';
              });
            }
            _styleFilterBtns();
            // Filter toggles go FIRST (above the dropdown), then the dropdown
            // row with the summary chip stack on the right.
            phCard.appendChild(filterRow);
            phCard.appendChild(ctrlRow);

            // Table mount
            const phTblWrap = document.createElement('div');
            phTblWrap.className = 'props-table-wrap';
            phCard.appendChild(phTblWrap);

            // Stake helpers (matches risk-to-win-1u convention used elsewhere)
            const _phUnits = (o, won, sz) => {
              if (o == null || won == null) return 0;
              if (o > 0) return won ? sz * (o / 100) : -sz;
              return won ? sz : sz * (-Math.abs(o) / 100);
            };
            const _phGrade = (p) => {
              if (p.actual == null || p.line == null) return null;
              const dir = p.pick === 'OVER' || p.pick === 'UNDER'
                ? p.pick
                : (p.would_be_pick || ((p.proj || 0) > (p.line || 0) ? 'OVER' : 'UNDER'));
              if (dir === 'OVER')  return p.actual > p.line ? 'W' : 'L';
              return p.actual < p.line ? 'W' : 'L';
            };
            const _phBucket = (p) => {
              if (p.pick === 'OVER' || p.pick === 'UNDER') return 'PICK';
              if (isLean(p)) return 'LEAN';
              return 'PASS';
            };
            const _phOdds = (p) => {
              if (p.odds != null) return p.odds;
              const dir = p.would_be_pick || ((p.proj || 0) > (p.line || 0) ? 'OVER' : 'UNDER');
              return dir === 'OVER' ? (p.over_price ?? null) : (p.under_price ?? null);
            };

            // Render function — pulls all rows for the selected pitcher key
            // from data.props (every market entry that matches the displayName+team).
            function renderPitcherHistory(key) {
              const allEver = (data.props || []).filter(p => {
                if (p.market !== 'strikeouts') return false;
                return _pitcherKey(p) === key;
              }).sort((a, b) => (a.date || '').localeCompare(b.date || ''));

              // Apply active filter — summary totals always use the full set
              // (allEver) so they reflect the pitcher's true record regardless
              // of which view is showing. Table itself is filtered.
              const all = allEver.filter(p => {
                if (_activeFilter === 'ALL') return true;
                return _phBucket(p) === _activeFilter;
              });

              // Build table
              phTblWrap.innerHTML = '';
              if (all.length === 0) {
                const e = document.createElement('div');
                e.style.cssText = 'padding:14px;color:#888;font-size:12px;font-style:italic';
                e.textContent = allEver.length === 0
                  ? 'No projections found for this pitcher.'
                  : `No ${_activeFilter.toLowerCase()}s for this pitcher.`;
                phTblWrap.appendChild(e);
                // Summary still reflects ALL plays so user keeps career context
                // even when current filter view is empty.
                _renderSummary(allEver);
                return;
              }

              const tbl = document.createElement('table');
              tbl.style.cssText = 'width:100%;border-collapse:collapse';
              const head = tbl.createTHead().insertRow();
              const headCols = [
                ['Date', 'left'], ['Opp', 'center'], ['Bkt', 'center'],
                ['Dir', 'center'], ['Line', 'right'], ['Proj', 'right'],
                ['Edge', 'right'], ['pC%', 'right'], ['Actual', 'right'],
                ['Odds', 'right'], ['Result', 'center'], ['Units', 'right'],
              ];
              headCols.forEach(([lbl, al]) => {
                const th = document.createElement('th');
                th.textContent = lbl;
                th.style.cssText = `padding:6px 8px;text-align:${al};border-bottom:1px solid rgba(255,255,255,0.1);font-size:11px;color:#999`;
                head.appendChild(th);
              });
              const body = tbl.createTBody();

              // Tally totals (picks vs leans separate)
              let pw=0, pl=0, lw=0, ll=0, p_u=0, l_u=0;

              for (const p of all) {
                const tr = body.insertRow();
                tr.style.borderBottom = '1px solid rgba(255,255,255,0.04)';
                const bkt = _phBucket(p);
                const dir = (p.pick === 'OVER' || p.pick === 'UNDER')
                  ? p.pick
                  : (p.would_be_pick || ((p.proj || 0) > (p.line || 0) ? 'OVER' : 'UNDER'));
                const grade = _phGrade(p);
                const odds = _phOdds(p);
                const sz = bkt === 'PICK' ? 1.0 : (bkt === 'LEAN' ? 1.0 : 0);
                const u = (bkt !== 'PASS' && grade != null)
                  ? _phUnits(odds, grade === 'W', sz)
                  : null;

                if (bkt === 'PICK' && grade) {
                  if (grade === 'W') { pw++; p_u += u; }
                  else { pl++; p_u += u; }
                } else if (bkt === 'LEAN' && grade) {
                  if (grade === 'W') { lw++; l_u += u; }
                  else { ll++; l_u += u; }
                }

                const edge = (p.proj != null && p.line != null) ? (p.proj - p.line).toFixed(1) : '—';
                const edgeStr = edge !== '—' ? (parseFloat(edge) > 0 ? '+' + edge : edge) : '—';
                const bktColor = bkt === 'PICK' ? '#a78bfa' : bkt === 'LEAN' ? 'var(--yellow)' : '#888';
                const dirColor = dir === 'OVER' ? 'var(--green)' : 'var(--red)';
                const resColor = grade === 'W' ? 'var(--green)' : grade === 'L' ? 'var(--red)' : '#888';
                const uColor = u == null ? '#888' : (u > 0 ? 'var(--green)' : u < 0 ? 'var(--red)' : '#ccc');
                const fmtOdds = (o) => o == null ? '—' : (o > 0 ? '+' + o : String(o));

                const cells = [
                  {v: p.date || '—', align:'left', color:'#ccc'},
                  {v: p.opp || '—', align:'center', color:'#ccc'},
                  {v: bkt, align:'center', color:bktColor, weight:'600'},
                  {v: dir === 'OVER' ? 'O' : 'U', align:'center', color:dirColor, weight:'600'},
                  {v: p.line != null ? String(p.line) : '—', align:'right'},
                  {v: p.proj != null ? p.proj.toFixed(1) : '—', align:'right'},
                  {v: edgeStr, align:'right', color: edge !== '—' && parseFloat(edge) > 0 ? 'var(--green)' : edge !== '—' && parseFloat(edge) < 0 ? 'var(--red)' : '#999'},
                  {v: p.pCover != null ? (p.pCover * 100).toFixed(1) + '%' : '—', align:'right'},
                  {v: p.actual != null ? String(p.actual) : '—', align:'right'},
                  {v: fmtOdds(odds), align:'right', color:'#ccc'},
                  {v: grade || '—', align:'center', color:resColor, weight:'700'},
                  {v: u == null ? '—' : (u >= 0 ? '+' : '') + u.toFixed(2) + 'u', align:'right', color:uColor, weight:'600'},
                ];
                cells.forEach((c, i) => {
                  const td = tr.insertCell();
                  td.textContent = c.v;
                  td.style.cssText = `padding:5px 8px;text-align:${c.align};font-size:12px`;
                  if (c.color) td.style.color = c.color;
                  if (c.weight) td.style.fontWeight = c.weight;
                });
              }
              phTblWrap.appendChild(tbl);
              fitMLBTableToContainer(tbl);

              // Summary always reflects the full historical set, not the
              // current filter — so users see lifetime totals regardless
              // of which tab they're on.
              _renderSummary(allEver);
            }

            // Reusable summary renderer keyed off the full play set.
            function _renderSummary(allEver) {
              let pw=0, pl=0, lw=0, ll=0, p_u=0, l_u=0;
              for (const p of allEver) {
                const bkt = _phBucket(p);
                const grade = _phGrade(p);
                if (!grade) continue;
                const dir = (p.pick === 'OVER' || p.pick === 'UNDER')
                  ? p.pick
                  : (p.would_be_pick || ((p.proj || 0) > (p.line || 0) ? 'OVER' : 'UNDER'));
                const odds = _phOdds(p);
                if (bkt === 'PICK') {
                  if (grade === 'W') pw++; else pl++;
                  p_u += _phUnits(odds, grade === 'W', 1.0);
                } else if (bkt === 'LEAN') {
                  if (grade === 'W') lw++; else ll++;
                  l_u += _phUnits(odds, grade === 'W', 1.0);
                }
              }
              const pickTotal = pw + pl, leanTotal = lw + ll;
              const parts = [];
              // Color-match the Bkt column (Picks=purple, Leans=yellow) so the
              // career-summary chips read as the same tier-language as the table.
              const pickColor = '#a78bfa';
              const leanColor = 'var(--yellow)';
              if (pickTotal > 0) {
                const wr = (pw/pickTotal*100).toFixed(1);
                const u  = (p_u >= 0 ? '+' : '') + p_u.toFixed(2) + 'u';
                parts.push(`<span style="color:${pickColor};font-weight:600">Picks ${pw}-${pl} (${wr}%) ${u}</span>`);
              }
              if (leanTotal > 0) {
                const wr = (lw/leanTotal*100).toFixed(1);
                const u  = (l_u >= 0 ? '+' : '') + l_u.toFixed(2) + 'u';
                parts.push(`<span style="color:${leanColor};font-weight:600">Leans ${lw}-${ll} (${wr}%) ${u}</span>`);
              }
              if (parts.length === 0) parts.push('<span style="color:#888">No graded plays yet</span>');
              // Stack each record on its own line so picks/leans don't compete
              // for the same horizontal slot when both have long unit values.
              summarySpan.innerHTML = parts.map(p => `<div>${p}</div>`).join('');
            }

            sel.addEventListener('change', () => renderPitcherHistory(sel.value));
            // Initial render = first probable (highest conviction)
            renderPitcherHistory(_probables[0].key);
            el.appendChild(phCard);
          }
        };

        // Today's Picks + Leans (unified card with tabs)
        // Filter out picks where the underlying game has been postponed/etc.
        // — those bets are voided, no actionable edge to display.
        const _gameStatusesPicks = data.gameStatuses || {};
        const _VOID_GS = new Set([
          'Postponed','Cancelled','Canceled','Suspended',
          'Postponed Inclement Weather','Postponed Rain',
          'Suspended: Inclement Weather','Suspended: Rain',
        ]);
        const _isVoidGame = (p) => {
          const gs = _gameStatusesPicks[p.team] || _gameStatusesPicks[p.opp] || '';
          return _VOID_GS.has(gs);
        };
        const todayPicks = picks.filter(p => p.date === todayStr && !_isVoidGame(p));
        const todayLeans = (data.props || []).filter(p =>
          p.date === todayStr && isLean(p) && !_isVoidGame(p)
        );
        if (todayPicks.length > 0 || todayLeans.length > 0) {
          const todayCard = document.createElement('div');
          todayCard.className = 'card card-picks';
          todayCard.style.marginBottom = '16px';
          const titleRow = document.createElement('div');
          titleRow.style.cssText = 'display:flex;align-items:center;justify-content:space-between;gap:12px';
          titleRow.appendChild(Object.assign(document.createElement('div'), {
            className: 'card-title',
            textContent: `Today\u2019s Picks (${todayStr})`
          }));
          const sortToggle = document.createElement('button');
          sortToggle.type = 'button';
          sortToggle.style.cssText = 'background:var(--accent,#5a6cff);color:#fff;border:none;border-radius:6px;padding:4px 10px;font-size:0.75rem;cursor:pointer';
          let sortMode = 'pcover'; // default
          sortToggle.textContent = 'Sort: pCover';
          titleRow.appendChild(sortToggle);
          todayCard.appendChild(titleRow);

          const tbl = document.createElement('table');
          tbl.className = 'props-data-table';
          tbl.style.cssText = 'width:100%;border-collapse:collapse;margin-top:8px';
          const todayHeaders = ['Name','Team','Opp','Proj','Line','Edge','%','Odds','O/U','C/U','Status'];
          const hRow = tbl.createTHead().insertRow();
          todayHeaders.forEach((h, i) => {
            const th = document.createElement('th');
            th.textContent = h;
            th.style.cssText = 'padding:4px 4px;text-align:center;border-bottom:1px solid rgba(255,255,255,0.1)';
            if (h === 'Name') th.style.textAlign = 'left';
            hRow.appendChild(th);
          });
          let tbody = tbl.createTBody();
          const catOrder = {strikeouts:0, outs:1, hits_allowed:2, game_hits:3};
          const gameTimesToday = data.gameTimes || {};
          function applySort() {
            if (sortMode === 'time') {
              todayPicks.sort((a,b) => {
                const ta = gameTimesToday[a.team] || gameTimesToday[a.opp] || '9999';
                const tb = gameTimesToday[b.team] || gameTimesToday[b.opp] || '9999';
                return ta.localeCompare(tb)
                  || (catOrder[a.market]??99) - (catOrder[b.market]??99)
                  || (b.pCover||0) - (a.pCover||0);
              });
            } else {
              todayPicks.sort((a,b) => (catOrder[a.market]??99) - (catOrder[b.market]??99) || (b.pCover||0) - (a.pCover||0));
            }
          }
          applySort();
          function renderRows() {
            const newBody = document.createElement('tbody');
            for (const p of todayPicks) {
              const row = newBody.insertRow();
              row.style.borderBottom = '1px solid rgba(255,255,255,0.05)';
              const tEdge = (p.proj != null && p.line != null) ? +(p.proj - p.line).toFixed(1) : null;
              const tEdgeStr = tEdge != null ? (tEdge > 0 ? '+'+tEdge : String(tEdge)) : '\u2014';
              const tPrice = p.odds != null ? (p.odds > 0 ? '+'+p.odds : String(p.odds)) : '\u2014';
              const gt = gameTimesToday[p.team] || gameTimesToday[p.opp] || '';
              const started = gt ? (new Date(gt).getTime() <= Date.now()) : false;
              // Confirmed = lineup_confirmed / game_started / final (projection locked
              // using real confirmed lineup). Otherwise unconfirmed (pending).
              // If the scheduled game is postponed/suspended/cancelled, surface
              // that status instead of the lineup-lock state.
              const _LOCK_STATES = new Set(['lineup_confirmed','game_started','final']);
              const _VOID_GAME_STATUSES_TP = new Set([
                'Postponed','Cancelled','Canceled','Suspended',
                'Postponed Inclement Weather','Postponed Rain',
                'Suspended: Inclement Weather','Suspended: Rain',
              ]);
              const _gsTP = (data.gameStatuses || {})[p.team] || (data.gameStatuses || {})[p.opp] || '';
              const isPostponed = _VOID_GAME_STATUSES_TP.has(_gsTP);
              const isConfirmed = _LOCK_STATES.has(p.lockState);
              const confText = isConfirmed ? 'C' : 'U';
              const tPcStr = p.pCover != null ? (p.pCover * 100).toFixed(1) + '%' : '\u2014';
              const cells = [
                displayName(p), p.team || '', p.opp || '',
                String(p.proj),
                p.line != null ? String(p.line) : '\u2014',
                tEdgeStr, tPcStr, tPrice,
                p.pick === 'OVER' ? 'O' : 'U',
                confText,
                started ? '\u{1F552}' : ''
              ];
              cells.forEach((val, i) => {
                const td = row.insertCell();
                td.textContent = val;
                td.style.cssText = 'padding:4px 4px;text-align:center';
                if (i === 0) { td.style.textAlign = 'left'; td.style.fontWeight = '600'; }
                if (i === 1 || i === 2) td.style.color = '#999';
                if (i === 3) td.style.color = p.proj > p.line ? 'var(--green)' : p.proj < p.line ? 'var(--red)' : '';
                if (i === 5 && tEdge != null) td.style.color = tEdge > 0 ? 'var(--green)' : tEdge < 0 ? 'var(--red)' : '#999';
                if (i === 6) td.style.color = '#aaa';
                if (i === 7) td.style.color = '#999';
                if (i === 8) { td.style.fontWeight = '700'; td.style.color = p.pick === 'OVER' ? 'var(--green)' : 'var(--red)'; }
                if (i === 9) {
                  td.title = isPostponed ? _gsTP : (p.lockState || 'pending');
                  td.style.fontSize = '11px';
                  td.style.fontWeight = '600';
                  td.style.color = isConfirmed ? 'var(--green)' : '#999';
                }
              });
            }
            tbody.replaceWith(newBody);
            tbody = newBody;
          }
          renderRows();
          sortToggle.addEventListener('click', () => {
            sortMode = (sortMode === 'pcover') ? 'time' : 'pcover';
            sortToggle.textContent = sortMode === 'pcover' ? 'Sort: pCover' : 'Sort: Game Time';
            applySort();
            renderRows();
          });

          // Build leans table — same column structure as Picks for consistency
          let lTbl = null;
          if (todayLeans.length > 0) {
            lTbl = document.createElement('table');
            lTbl.className = 'props-data-table';
            lTbl.style.cssText = 'width:100%;border-collapse:collapse;margin-top:8px';
            const lhRow = lTbl.createTHead().insertRow();
            todayHeaders.forEach((h) => {
              const th = document.createElement('th');
              th.textContent = h;
              th.style.cssText = 'padding:4px 4px;text-align:center;border-bottom:1px solid rgba(255,255,255,0.1)';
              if (h === 'Name') th.style.textAlign = 'left';
              lhRow.appendChild(th);
            });
            const lTbody = lTbl.createTBody();
            todayLeans.sort((a, b) => (b.pCover || 0) - (a.pCover || 0));
            const _LOCK_STATES = new Set(['lineup_confirmed','game_started','final']);
            for (const p of todayLeans) {
              const row = lTbody.insertRow();
              row.style.borderBottom = '1px solid rgba(255,255,255,0.05)';
              const tEdge = (p.proj != null && p.line != null) ? +(p.proj - p.line).toFixed(1) : null;
              const tEdgeStr = tEdge != null ? (tEdge > 0 ? '+'+tEdge : String(tEdge)) : '—';
              const tPrice = p.odds != null ? (p.odds > 0 ? '+'+p.odds : String(p.odds)) : '—';
              const gt = gameTimesToday[p.team] || gameTimesToday[p.opp] || '';
              const started = gt ? (new Date(gt).getTime() <= Date.now()) : false;
              const _VOID_STATUSES_LEAN = new Set([
                'Postponed','Cancelled','Canceled','Suspended',
                'Postponed Inclement Weather','Postponed Rain',
                'Suspended: Inclement Weather','Suspended: Rain',
              ]);
              const _gsLean = (data.gameStatuses || {})[p.team] || (data.gameStatuses || {})[p.opp] || '';
              const isPostponed = _VOID_STATUSES_LEAN.has(_gsLean);
              const isConfirmed = _LOCK_STATES.has(p.lockState);
              const confText = isConfirmed ? 'C' : 'U';
              const lPcStr = p.pCover != null ? (p.pCover * 100).toFixed(1) + '%' : '—';
              const cells = [
                displayName(p), p.team || '', p.opp || '',
                String(p.proj),
                p.line != null ? String(p.line) : '—',
                tEdgeStr, lPcStr, tPrice,
                p.would_be_pick === 'OVER' ? 'O' : 'U',
                confText,
                started ? '\u{1F552}' : ''
              ];
              cells.forEach((val, i) => {
                const td = row.insertCell();
                td.textContent = val;
                td.style.cssText = 'padding:4px 4px;text-align:center';
                if (i === 0) { td.style.textAlign = 'left'; td.style.fontWeight = '600'; }
                if (i === 1 || i === 2) td.style.color = '#999';
                if (i === 3) td.style.color = p.proj > p.line ? 'var(--green)' : p.proj < p.line ? 'var(--red)' : '';
                if (i === 5 && tEdge != null) td.style.color = tEdge > 0 ? 'var(--green)' : tEdge < 0 ? 'var(--red)' : '#999';
                if (i === 6) td.style.color = '#aaa';
                if (i === 7) td.style.color = '#999';
                if (i === 8) { td.style.fontWeight = '700'; td.style.color = p.would_be_pick === 'OVER' ? 'var(--green)' : 'var(--red)'; }
                if (i === 9) {
                  td.title = isPostponed ? _gsLean : (p.lockState || 'pending');
                  td.style.fontSize = '11px';
                  td.style.fontWeight = '600';
                  td.style.color = isConfirmed ? 'var(--green)' : '#999';
                }
              });
            }
          }

          // Sequential layout (matches Yesterday's Recap structure): picks
          // table on top, then a Leans header line + leans table below.
          if (todayPicks.length > 0) { todayCard.appendChild(tbl); fitMLBTableToContainer(tbl); }
          if (lTbl) {
            const leanHeader = document.createElement('div');
            leanHeader.style.cssText = 'margin-top:14px;padding-top:8px;border-top:1px dashed rgba(244,180,0,0.4);font-size:12px;color:#f4b400;font-weight:600';
            leanHeader.textContent = `Leans — .65-.72 (${todayLeans.length})`;
            todayCard.appendChild(leanHeader);
            todayCard.appendChild(lTbl);
            fitMLBTableToContainer(lTbl);
          }
          el.appendChild(todayCard);
        } else {
          const todayCard = document.createElement('div');
          todayCard.className = 'card card-picks';
          todayCard.style.marginBottom = '16px';
          todayCard.appendChild(Object.assign(document.createElement('div'), {
            className: 'card-title',
            textContent: `Today’s Picks (${todayStr})`
          }));
          const empty = document.createElement('div');
          empty.className = 'no-picks';
          empty.style.cssText = 'padding:12px;color:#888;font-style:italic;font-size:13px';
          empty.textContent = 'No picks or leans for today.';
          todayCard.appendChild(empty);
          el.appendChild(todayCard);
        }
      })();

      // ── Today's Games Explorer ──
      (function renderGamesSection() {
        const allDates = [...new Set(data.props.map(p => p.date))].sort();
        const todayStr = allDates[allDates.length - 1] || '';
        // Use todayProjections (all projections incl. PASS) if available, else fall back to picks only
        const todayAllProj = (data.todayProjections || data.props)
          .filter(p => p.date === todayStr && p.proj != null && p.line != null);
        if (todayAllProj.length === 0) return;

        // Build unique games from gameTimes (all scheduled games, not just ones
        // with prop lines). Falls back to projections if gameTimes missing.
        const gameTimes = data.gameTimes || {};
        const gameSet = new Map();

        // First add games from gameTimes (covers games with no prop lines yet)
        // gameTimes is {team_abbr: ISO_time} — pair up teams by matching times
        const teamsByTime = {};
        for (const [team, t] of Object.entries(gameTimes)) {
          if (!teamsByTime[t]) teamsByTime[t] = [];
          teamsByTime[t].push(team);
        }
        for (const [t, teams] of Object.entries(teamsByTime)) {
          if (teams.length === 2) {
            const key = [...teams].sort().join('@');
            gameSet.set(key, { label: `${teams[0]} vs ${teams[1]}`, time: t });
          }
        }

        // Also add any games from projections (handles cases where gameTimes is missing)
        for (const p of todayAllProj) {
          const key = [p.team, p.opp].sort().join('@');
          if (!gameSet.has(key)) {
            const t = gameTimes[p.team] || gameTimes[p.opp] || '9999';
            gameSet.set(key, { label: `${p.team} vs ${p.opp}`, time: t });
          }
        }
        const games = [...gameSet.entries()]
          .sort((a, b) => (a[1].time || '').localeCompare(b[1].time || ''))
          .map(([k, v]) => [k, v.label]); // [[key, label], ...]

        const gCard = document.createElement('div');
        gCard.className = 'card';
        gCard.style.cssText = 'padding:0;margin-bottom:16px;overflow:hidden';

        // Title row
        const titleRow = document.createElement('div');
        titleRow.style.cssText = 'padding:12px 16px 8px;border-bottom:1px solid rgba(255,255,255,0.08)';
        titleRow.appendChild(Object.assign(document.createElement('div'), {className:'card-title', textContent:`Today\u2019s Games (${todayStr})`}));
        gCard.appendChild(titleRow);

        // Game selector pills
        const gamePills = document.createElement('div');
        gamePills.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap;padding:10px 16px;border-bottom:1px solid rgba(255,255,255,0.08)';
        let activeGame = 'all';

        // Pitcher dropdown
        const playerRow = document.createElement('div');
        playerRow.style.cssText = 'padding:8px 16px;border-bottom:1px solid rgba(255,255,255,0.08);display:flex;align-items:center;gap:8px';
        const playerLabel = Object.assign(document.createElement('span'), {textContent:'Pitcher:', style:'font-size:12px;color:#999'});
        const playerSelect = document.createElement('select');
        playerSelect.style.cssText = 'padding:5px 10px;border-radius:6px;background:rgba(255,255,255,0.06);color:#fff;border:1px solid rgba(255,255,255,0.1);font-size:12px;outline:none;cursor:pointer;max-width:200px';
        let activePlayer = 'all';
        playerRow.appendChild(playerLabel);
        playerRow.appendChild(playerSelect);

        // Market filter pills
        const mktPills = document.createElement('div');
        mktPills.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap;padding:10px 16px;border-bottom:1px solid rgba(255,255,255,0.08)';
        const mktFilters = ['all','strikeouts','outs','hits_allowed'];
        let activeMkt = 'all';

        // Table container
        const tableWrap = document.createElement('div');
        tableWrap.style.cssText = 'padding:12px 16px';

        function refreshPlayerDropdown() {
          playerSelect.textContent = '';
          const playersInGame = [...new Set(
            todayAllProj.filter(p => activeGame === 'all' || [p.team, p.opp].sort().join('@') === activeGame)
              .filter(p => p.market !== 'game_hits')
              .map(p => p.player)
          )].sort();
          const allOpt = document.createElement('option');
          allOpt.value = 'all'; allOpt.textContent = 'All Pitchers';
          playerSelect.appendChild(allOpt);
          for (const name of playersInGame) {
            const opt = document.createElement('option');
            opt.value = name; opt.textContent = name;
            playerSelect.appendChild(opt);
          }
          playerSelect.value = activePlayer === 'all' || !playersInGame.includes(activePlayer) ? 'all' : activePlayer;
          activePlayer = playerSelect.value;
        }
        playerSelect.onchange = () => { activePlayer = playerSelect.value; currentPage = 0; renderGameTable(); };

        const PAGE_SIZE = 30;
        let currentPage = 0;
        // sortCol: 'cat'|'edge'|'cover'|'proj'  sortDir: 1=desc -1=asc (legacy convention)
        let sortCol = 'cover'; // default: directional spectrum (OVER 70% top, UNDER 70% bottom)
        let sortDir = 1;

        const catOrd = {strikeouts:0, outs:1, hits_allowed:2, game_hits:3};

        function sortRows(rows) {
          return [...rows].sort((a, b) => {
            let v;
            if (sortCol === 'cat') {
              v = ((catOrd[a.market]??99) - (catOrd[b.market]??99)) ||
                  (((a.proj??0)-(a.line??0)) < ((b.proj??0)-(b.line??0)) ? 1 : ((a.proj??0)-(a.line??0)) > ((b.proj??0)-(b.line??0)) ? -1 : 0);
            } else if (sortCol === 'edge') {
              // Edge desc, with pCover desc as tiebreaker
              const ea = (a.proj??0)-(a.line??0), eb = (b.proj??0)-(b.line??0);
              v = (eb - ea) || ((b.pCover??0) - (a.pCover??0));
            } else if (sortCol === 'cover') {
              // Directional spectrum: OVERs sorted by pCover desc at top,
              // UNDERs sorted by pCover asc at bottom (so order reads
              // OVER 70 → OVER 65 → ~50 → UNDER 65 → UNDER 70).
              // Implemented via "implied OVER probability": OVER picks use
              // their pCover, UNDER picks use (1 - pCover).
              const aOver = (a.proj??0) >= (a.line??0);
              const bOver = (b.proj??0) >= (b.line??0);
              const aScore = aOver ? (a.pCover??0.5) : (1 - (a.pCover??0.5));
              const bScore = bOver ? (b.pCover??0.5) : (1 - (b.pCover??0.5));
              v = bScore - aScore;
            } else if (sortCol === 'proj') {
              v = (b.proj??0) - (a.proj??0);
            } else {
              v = 0;
            }
            return sortCol === 'cat' ? v : v * sortDir;
          });
        }

        function renderGameTable() {
          tableWrap.textContent = '';
          const gameProj = todayAllProj.filter(p => {
            const key = [p.team, p.opp].sort().join('@');
            const gameMatch = activeGame === 'all' || key === activeGame;
            const mktMatch = activeMkt === 'all' || p.market === activeMkt;
            const playerMatch = activePlayer === 'all' || p.player === activePlayer || p.market === 'game_hits';
            return gameMatch && mktMatch && playerMatch;
          });
          if (gameProj.length === 0) {
            tableWrap.appendChild(Object.assign(document.createElement('div'), {textContent:'No projections found.', style:'color:#666;font-size:13px'}));
            return;
          }

          const sorted = sortRows(gameProj);
          const totalPages = Math.ceil(sorted.length / PAGE_SIZE);
          currentPage = Math.max(0, Math.min(currentPage, totalPages - 1));
          const pageRows = sorted.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE);

          const tbl = document.createElement('table');
          tbl.style.cssText = 'width:100%;border-collapse:collapse';
          const hRow = tbl.createTHead().insertRow();

          // col def: [label, sortKey, align-left?]
          const cols = [
            ['Name',   null, true],
            ['Team',   null, false],
            ['Opp',    null, false],
            ['LU K%',  null, false],
            ['Tm K%',  null, false],
            ['BF',     null, false],
            ['PC',     null, false],
            ['Proj',   'proj', false],
            ['Line',   null, false],
            ['Edge',   'edge', false],
            ['%',      'cover', false],
            ['O/U',     null, false],
            ['Odds',   null, false],
            ['C/U',    null, false],
            ['Status', null, false],
          ];
          cols.forEach(([label, key, leftAlign], i) => {
            const th = document.createElement('th');
            const isActive = key && sortCol === key;
            const arrow = isActive ? (sortDir === 1 ? ' \u2191' : ' \u2193') : '';
            th.textContent = label + arrow;
            th.style.cssText = `padding:5px 8px;text-align:${leftAlign?'left':'center'};border-bottom:1px solid rgba(255,255,255,0.1);font-size:12px;color:${isActive?'#fff':'#999'};${key?'cursor:pointer;user-select:none':''}`;
            if (key) th.onclick = () => {
              if (sortCol === key) { sortDir *= -1; } else { sortCol = key; sortDir = (key === 'cat' || key === 'cover') ? 1 : -1; }
              currentPage = 0;
              renderGameTable();
            };
            hRow.appendChild(th);
          });

          const tbody = tbl.createTBody();
          const _LOCK_STATES_TBL = new Set(['lineup_confirmed','game_started','final']);
          const _gameTimes = data.gameTimes || {};
          const _gameStatuses = data.gameStatuses || {};
          // Schedule statuses that mean the game won't (or didn't) play tonight.
          const _VOID_GAME_STATUSES = new Set([
            'Postponed', 'Cancelled', 'Canceled', 'Suspended',
            'Postponed Inclement Weather', 'Postponed Rain',
            'Suspended: Inclement Weather', 'Suspended: Rain',
          ]);
          for (const p of pageRows) {
            const row = tbody.insertRow();
            row.style.borderBottom = '1px solid rgba(255,255,255,0.04)';
            const isPick = p.pick && p.pick !== 'PASS';
            if (isPick) row.style.background = 'rgba(124,108,240,0.06)';
            const edge = (p.proj != null && p.line != null) ? +(p.proj - p.line).toFixed(1) : null;
            const edgeStr = edge != null ? (edge > 0 ? '+'+edge : String(edge)) : '\u2014';
            const coverStr = p.pCover != null ? (p.pCover * 100).toFixed(1) + '%' : '\u2014';
            // Price column: for picks use the pick's odds; for non-picks show
            // the price of the direction the projection leans (OVER if proj>line,
            // UNDER if proj<line). This lets users see every available line's price.
            const fmtOdds = (o) => o == null ? '\u2014' : (o > 0 ? '+' + o : String(o));
            let priceStr = '\u2014';
            if (isPick && p.odds != null) {
              priceStr = fmtOdds(p.odds);
            } else if (p.proj != null && p.line != null) {
              if (p.proj > p.line && p.over_price != null) priceStr = fmtOdds(p.over_price);
              else if (p.proj < p.line && p.under_price != null) priceStr = fmtOdds(p.under_price);
            }
            // If the team's scheduled game has a void-class status (Postponed,
            // Suspended, Cancelled), surface that instead of the lineup-lock state.
            const _gs = _gameStatuses[p.team] || _gameStatuses[p.opp] || '';
            const isPostponed = _VOID_GAME_STATUSES.has(_gs);
            const isConfirmed = _LOCK_STATES_TBL.has(p.lockState);
            const confText = isConfirmed ? 'C' : 'U';
            const gt = _gameTimes[p.team] || _gameTimes[p.opp] || '';
            const started = gt ? (new Date(gt).getTime() <= Date.now()) : false;
            const statusStr = started ? '\u{1F552}' : '';
            const bfStr    = p.proj_bf != null ? String(Math.round(p.proj_bf)) : '\u2014';
            const pitchStr = p.proj_pc != null ? String(Math.round(p.proj_pc)) : '\u2014';
            // Engine emits lineup_k_pct and opp_team_k_pct already scaled to percent values
            // (e.g. 22.4 means 22.4%). Show one decimal place.
            const luKStr   = p.lineup_k_pct   != null ? p.lineup_k_pct.toFixed(1)   + '%' : '\u2014';
            const tmKStr   = p.opp_team_k_pct != null ? p.opp_team_k_pct.toFixed(1) + '%' : '\u2014';
            [displayName(p), p.team||'', p.opp||'',
             luKStr, tmKStr, bfStr, pitchStr,
             p.proj!=null?String(p.proj):'\u2014', p.line!=null?String(p.line):'\u2014',
             edgeStr, coverStr,
             isPick?(p.pick==='OVER'?'O':'U'):'\u2014',
             priceStr,
             confText,
             statusStr
            ].forEach((v,i) => {
              const td = row.insertCell();
              td.textContent = v;
              td.style.cssText = 'padding:5px 8px;text-align:'+(i===0?'left':'center')+';font-size:12px';
              if (i===0) td.style.fontWeight = '600';
              if (i===1) td.style.color = '#999';
              if (i===2) td.style.color = '#999'; // Opp
              if (i===3) td.style.color = '#bbb'; // LU K%
              if (i===4) td.style.color = '#bbb'; // Tm K%
              if (i===5) td.style.color = '#bbb'; // BF
              if (i===6) td.style.color = '#bbb'; // PC
              if (i===7 && p.line!=null) td.style.color = p.proj > p.line ? 'var(--green)' : p.proj < p.line ? 'var(--red)' : '';
              if (i===9 && edge!=null) td.style.color = edge > 0 ? 'var(--green)' : edge < 0 ? 'var(--red)' : '#999';
              if (i===10 && p.pCover!=null) td.style.color = p.pCover >= 0.72 ? 'var(--green)' : p.pCover >= 0.65 ? 'var(--yellow)' : p.pCover <= 0.45 ? 'var(--red)' : '#ccc';
              if (i===11 && isPick) { td.style.fontWeight='700'; td.style.color = p.pick==='OVER'?'var(--green)':'var(--red)'; }
              if (i===12) td.style.color = '#999';
              if (i===13) {
                td.title = isPostponed ? _gs : (p.lockState || 'pending');
                td.style.fontSize = '11px';
                td.style.fontWeight = '600';
                td.style.color = isConfirmed ? 'var(--green)' : '#999';
              }
            });
          }
          tableWrap.appendChild(tbl);
          fitMLBTableToContainer(tbl);

          // Pagination controls (only show when more than one page)
          if (totalPages > 1) {
            const pgRow = document.createElement('div');
            pgRow.style.cssText = 'display:flex;align-items:center;justify-content:center;gap:8px;padding:10px 0 2px';
            const prevBtn = document.createElement('button');
            prevBtn.textContent = '\u2190 Prev';
            prevBtn.style.cssText = 'padding:4px 12px;border-radius:6px;border:1px solid rgba(255,255,255,0.12);background:transparent;color:'+(currentPage===0?'#444':'#ccc')+';font-size:12px;cursor:'+(currentPage===0?'default':'pointer');
            prevBtn.disabled = currentPage === 0;
            prevBtn.onclick = () => { currentPage--; renderGameTable(); tableWrap.scrollIntoView({behavior:'smooth',block:'nearest'}); };
            const nextBtn = document.createElement('button');
            nextBtn.textContent = 'Next \u2192';
            nextBtn.style.cssText = 'padding:4px 12px;border-radius:6px;border:1px solid rgba(255,255,255,0.12);background:transparent;color:'+(currentPage===totalPages-1?'#444':'#ccc')+';font-size:12px;cursor:'+(currentPage===totalPages-1?'default':'pointer');
            nextBtn.disabled = currentPage === totalPages - 1;
            nextBtn.onclick = () => { currentPage++; renderGameTable(); tableWrap.scrollIntoView({behavior:'smooth',block:'nearest'}); };
            const info = Object.assign(document.createElement('span'), {
              textContent: `Page ${currentPage+1} of ${totalPages}  (${sorted.length} rows)`,
              style: 'font-size:12px;color:#666'
            });
            pgRow.appendChild(prevBtn);
            pgRow.appendChild(info);
            pgRow.appendChild(nextBtn);
            tableWrap.appendChild(pgRow);
          }
        }

        function refreshPills() {
          // Game pills — "All" first, then each game
          gamePills.textContent = '';
          const allGamesBtn = document.createElement('button');
          allGamesBtn.textContent = 'All';
          allGamesBtn.style.cssText = activeGame === 'all'
            ? 'padding:5px 14px;border-radius:16px;border:1px solid #7c6cf0;background:#7c6cf0;color:#fff;font-size:12px;cursor:pointer'
            : 'padding:5px 14px;border-radius:16px;border:1px solid rgba(255,255,255,0.12);background:transparent;color:#999;font-size:12px;cursor:pointer';
          allGamesBtn.onclick = () => { activeGame = 'all'; activePlayer = 'all'; currentPage = 0; refreshPills(); refreshPlayerDropdown(); renderGameTable(); };
          gamePills.appendChild(allGamesBtn);
          for (const [key, label] of games) {
            const btn = document.createElement('button');
            btn.textContent = label;
            btn.style.cssText = key === activeGame
              ? 'padding:5px 14px;border-radius:16px;border:1px solid #7c6cf0;background:#7c6cf0;color:#fff;font-size:12px;cursor:pointer'
              : 'padding:5px 14px;border-radius:16px;border:1px solid rgba(255,255,255,0.12);background:transparent;color:#999;font-size:12px;cursor:pointer';
            btn.onclick = () => { activeGame = key; activePlayer = 'all'; currentPage = 0; refreshPills(); refreshPlayerDropdown(); renderGameTable(); };
            gamePills.appendChild(btn);
          }
          // Market pills removed — strikeouts is the only market.
        }

        refreshPills();
        refreshPlayerDropdown();
        renderGameTable();
        gCard.appendChild(gamePills);
        gCard.appendChild(playerRow);
        gCard.appendChild(tableWrap);
        el.appendChild(gCard);
      })();

      // Matchup History card sits directly under Today's Games so the read
      // appears alongside the slate it describes, instead of at the page
      // bottom. (Definition lives inside renderMLBDailyCards; invoking here
      // appends to `el` at this insertion point.)
      if (_renderMatchupCard) _renderMatchupCard();

      // ── Unified Toolbar ──
      const selStyle = 'padding:6px 12px;border-radius:6px;background:rgba(255,255,255,0.06);color:#fff;border:1px solid rgba(255,255,255,0.1);font-size:13px;outline:none';
      const pillStyle = 'padding:5px 14px;border-radius:16px;border:1px solid rgba(255,255,255,0.12);background:transparent;color:#999;font-size:12px;cursor:pointer;transition:all 0.15s';
      const pillActiveStyle = 'padding:5px 14px;border-radius:16px;border:1px solid #7c6cf0;background:#7c6cf0;color:#fff;font-size:12px;cursor:pointer;transition:all 0.15s';
      const tabStyle = 'padding:6px 16px;border:none;background:transparent;color:#999;font-size:13px;cursor:pointer;border-bottom:2px solid transparent;transition:all 0.15s';
      const tabActiveStyle = 'padding:6px 16px;border:none;background:transparent;color:#fff;font-size:13px;cursor:pointer;border-bottom:2px solid #7c6cf0;transition:all 0.15s';

      let mlbView = 'all'; // 'all' | 'weekly' | 'all-lean' | 'weekly-lean' | 'all-combined'

      // Watchlist leans (pick=PASS) — DATE-AWARE filter so historical entries
      // keep the lean classification they had when originally posted, and new
      // entries use the current unified band.
      //
      //   Before 2026-05-13 (old config — threshold 0.70, asymmetric lean):
      //     UNDER  0.60 <= pCover < 0.70
      //     OVER   0.65 <= pCover < 0.70
      //   2026-05-13 and after (new config — threshold 0.72, unified lean):
      //     both   0.65 <= pCover < 0.72
      //
      // This preserves the visual history of past lean performance while
      // applying the new band going forward. The Reddit widget's isLean()
      // (line 185) uses 0.65-0.72 for all dates because the user's Reddit
      // posts have always tracked that band — those tallies don't change.
      const LEAN_CONFIG_CUTOFF = '2026-05-13';
      const leanPicks = (data.props || []).filter(p => {
        if (p.pick !== 'PASS') return false;
        const pc = p.pCover || 0;
        const isNewConfig = (p.date || '') >= LEAN_CONFIG_CUTOFF;
        if (isNewConfig) {
          return pc >= 0.65 && pc < 0.72;
        }
        // Pre-cutoff: old asymmetric band
        if (p.would_be_pick === 'UNDER') return pc >= 0.60 && pc < 0.70;
        if (p.would_be_pick === 'OVER')  return pc >= 0.65 && pc < 0.70;
        return false;
      });

      // Effective direction: actionable picks use p.pick, leans use p.would_be_pick
      const effectiveDir = (p) => p.pick === 'PASS' ? p.would_be_pick : p.pick;

      const allPicksCard = document.createElement('div');
      allPicksCard.className = 'card';
      allPicksCard.style.cssText = 'padding:0;margin-bottom:16px;overflow:hidden';

      const toolbar = document.createElement('div');
      toolbar.style.cssText = 'overflow:hidden';

      // Row 1: View tabs
      const tabRow = document.createElement('div');
      tabRow.className = 'props-toolbar-tabs';
      tabRow.style.cssText = 'display:flex;border-bottom:1px solid rgba(255,255,255,0.08)';
      const viewAllBtn = document.createElement('button');
      viewAllBtn.textContent = 'All Picks';
      const viewWeeklyBtn = document.createElement('button');
      viewWeeklyBtn.textContent = 'Weekly Picks';
      const viewAllLeanBtn = document.createElement('button');
      viewAllLeanBtn.textContent = 'All Lean';
      const viewWeeklyLeanBtn = document.createElement('button');
      viewWeeklyLeanBtn.textContent = 'Weekly Lean';
      const viewAllCombinedBtn = document.createElement('button');
      viewAllCombinedBtn.textContent = 'All';
      tabRow.appendChild(viewAllCombinedBtn);
      tabRow.appendChild(viewAllBtn);
      tabRow.appendChild(viewWeeklyBtn);
      tabRow.appendChild(viewAllLeanBtn);
      tabRow.appendChild(viewWeeklyLeanBtn);
      toolbar.appendChild(tabRow);

      // Market filter row removed — strikeouts is the only active market.
      let mlbActiveMarket = 'all';
      function renderMLBMarketBtns() { /* no-op: single-market mode */ }

      // Row 3: Contextual filters
      const filterRow = document.createElement('div');
      filterRow.className = 'props-toolbar-filters';
      filterRow.style.cssText = 'display:flex;gap:12px;align-items:center;padding:12px 16px;flex-wrap:wrap';

      // All Picks filters
      const allDates = [...new Set(picks.concat(leanPicks).map(p => p.date))].sort().reverse();
      const dateSel = document.createElement('select');
      dateSel.style.cssText = selStyle;
      dateSel.innerHTML = '<option value="all">All Dates</option>' + allDates.map(d => `<option value="${d}">${d}</option>`).join('');
      const dirSel = document.createElement('select');
      dirSel.style.cssText = selStyle;
      dirSel.innerHTML = '<option value="all">All O/U</option><option value="OVER">OVER</option><option value="UNDER">UNDER</option>';
      const bucketSel = document.createElement('select');
      bucketSel.style.cssText = selStyle;
      bucketSel.innerHTML = [
        '<option value="all">All Cover%</option>',
        '<option value="0.55-0.60">55–60%</option>',
        '<option value="0.60-0.65">60–65%</option>',
        '<option value="0.65-0.70">65–70%</option>',
        '<option value="0.70-0.75">70–75%</option>',
        '<option value="0.75-0.80">75–80%</option>',
        '<option value="0.80-0.85">80–85%</option>',
        '<option value="0.85-0.90">85–90%</option>',
        '<option value="0.90-1.01">90%+</option>',
      ].join('');
      function inBucket(p, val) {
        if (val === 'all') return true;
        const [lo, hi] = val.split('-').map(parseFloat);
        const pc = p.pCover || 0;
        return pc >= lo && pc < hi;
      }
      const teamSel = document.createElement('select');
      teamSel.style.cssText = selStyle;
      const allTeams = [...new Set(picks.map(p => p.team))].filter(Boolean).sort();
      teamSel.innerHTML = '<option value="all">All Teams</option>' + allTeams.map(t => `<option value="${t}">${t}</option>`).join('');
      const filterLabel = document.createElement('span');
      filterLabel.style.cssText = 'color:#666;font-size:12px;margin-left:auto';
      filterLabel.textContent = `${picks.length} picks`;

      // Weekly filters
      const allWeekStarts = [...new Set(picks.filter(p => p.date).map(p => getWeekStart(p.date)))].sort().reverse();
      const weekSel = document.createElement('select');
      weekSel.style.cssText = selStyle;
      weekSel.innerHTML = '<option value="all">All Weeks</option>' + allWeekStarts.map(ws => {
        const we = getWeekEnd(ws);
        return `<option value="${ws}">${ws} \u2013 ${we}</option>`;
      }).join('');
      // Day-of-week sub-filter for Weekly views (populated when a week is selected)
      const daySel = document.createElement('select');
      daySel.style.cssText = selStyle;
      daySel.innerHTML = '<option value="all">All Days</option>';
      function refreshDayOptions() {
        const src = (mlbView === 'weekly-lean') ? leanPicks : picks;
        let dates;
        if (weekSel.value === 'all') {
          dates = [...new Set(src.filter(p => p.date).map(p => p.date))].sort().reverse();
        } else {
          dates = [...new Set(src.filter(p => p.date && getWeekStart(p.date) === weekSel.value).map(p => p.date))].sort().reverse();
        }
        const prev = daySel.value;
        daySel.innerHTML = '<option value="all">All Days</option>'
          + dates.map(d => `<option value="${d}">${d}</option>`).join('');
        if (dates.includes(prev)) daySel.value = prev; else daySel.value = 'all';
      }
      const weekFilterLabel = document.createElement('span');
      weekFilterLabel.style.cssText = 'color:#666;font-size:12px;margin-left:auto';

      toolbar.appendChild(filterRow);

      const contentArea = document.createElement('div');
      contentArea.style.cssText = 'padding:0 16px 16px';

      allPicksCard.appendChild(toolbar);
      allPicksCard.appendChild(contentArea);
      el.appendChild(allPicksCard);

      const headers = isBacktest
        ? ['Date','Name','Team','Opp','Proj','Line','Edge','%','Actual','O/U','Odds','W/L']
        : ['Name','Team','vs','Proj','Line','Edge','%','O/U','Odds'];
      const colClasses = isBacktest
        ? ['col-date','col-player','col-team','col-opp','col-proj','col-line','col-edge','col-pcov','col-actual','col-pick','col-price','col-result']
        : ['col-player','col-team','col-opp','col-proj','col-line','col-edge','col-pcov','col-pick','col-price'];

      function activeSource() {
        if (mlbView === 'all-lean' || mlbView === 'weekly-lean') return leanPicks;
        if (mlbView === 'all-combined') return picks.concat(leanPicks);
        return picks;
      }

      function getFilteredPicks() {
        let fp = activeSource().slice();
        if (dateSel.value !== 'all') fp = fp.filter(p => p.date === dateSel.value);
        if (teamSel.value !== 'all') fp = fp.filter(p => p.team === teamSel.value);
        if (dirSel.value !== 'all') fp = fp.filter(p => effectiveDir(p) === dirSel.value);
        if (bucketSel.value !== 'all') fp = fp.filter(p => inBucket(p, bucketSel.value));
        if (dateSel.value !== 'all') {
          const catOrder = {strikeouts:0, outs:1, hits_allowed:2, game_hits:3};
          fp.sort((a, b) => (catOrder[a.market]??99) - (catOrder[b.market]??99) || (b.pCover||0) - (a.pCover||0));
        } else {
          fp.sort((a, b) => (b.date || '').localeCompare(a.date || ''));
        }
        return fp;
      }

      // Per-column sort accessors. Each returns a comparable value for the
      // row's underlying pick object \u2014 backtest tables share these keys.
      function _colSortKey(colName, p) {
        switch (colName) {
          case 'Date':   return p.date || '';
          case 'Name':   return (displayName(p) || '').toLowerCase();
          case 'Team':   return (p.team || '').toLowerCase();
          case 'Opp':
          case 'vs':     return (p.opp || '').toLowerCase();
          case 'Proj':   return p.proj != null ? +p.proj : -Infinity;
          case 'Line':   return p.line != null ? +p.line : -Infinity;
          case 'Edge':   return (p.proj != null && p.line != null) ? (p.proj - p.line) : -Infinity;
          case '%':      return p.pCover != null ? +p.pCover : -Infinity;
          case 'Actual': return p.actual != null ? +p.actual : -Infinity;
          case 'O/U':    return effectiveDir(p) === 'OVER' ? 1 : 0;
          case 'Odds':   return p.odds != null ? +p.odds : -Infinity;
          case 'W/L':    return p.result === 'WIN' ? 1 : p.result === 'LOSS' ? 0 : -1;
          default:       return 0;
        }
      }

      function buildPropsTable(mProps) {
        const wrap = document.createElement('div');
        wrap.className = 'props-table-wrap';
        const tbl = document.createElement('table');
        tbl.className = 'props-data-table';
        tbl.style.cssText = 'width:100%;border-collapse:collapse;margin-top:8px';

        // Sort state for this table instance.
        let sortColIdx = null;   // null = no manual sort (use original order)
        let sortDir = 1;         // 1 = ascending, -1 = descending
        const data = mProps.slice();

        function getSorted() {
          if (sortColIdx === null) return data;
          const colName = headers[sortColIdx];
          const dir = sortDir;
          return data.slice().sort((a, b) => {
            const av = _colSortKey(colName, a);
            const bv = _colSortKey(colName, b);
            if (av < bv) return -1 * dir;
            if (av > bv) return  1 * dir;
            return 0;
          });
        }

        const hRow = tbl.createTHead().insertRow();
        const ths = [];
        headers.forEach((h, i) => {
          const th = document.createElement('th');
          th.dataset.label = h;
          th.className = colClasses[i] || 'col-' + i;
          th.style.cssText = 'padding:4px 4px;text-align:center;border-bottom:1px solid rgba(255,255,255,0.1);cursor:pointer;user-select:none';
          if (h === 'Name') th.style.textAlign = 'left';
          th.addEventListener('click', () => {
            if (sortColIdx === i) sortDir *= -1;
            else { sortColIdx = i; sortDir = -1; }   // first click defaults desc
            updateHeaderLabels();
            rebuildBody();
          });
          hRow.appendChild(th);
          ths.push(th);
        });

        function updateHeaderLabels() {
          ths.forEach((th, i) => {
            const base = th.dataset.label;
            if (i === sortColIdx) {
              th.textContent = base + (sortDir === 1 ? ' \u25b2' : ' \u25bc');
              th.style.color = '#fff';
            } else {
              th.textContent = base;
              th.style.color = '';
            }
          });
        }
        updateHeaderLabels();

        const tbody = tbl.createTBody();

        function rebuildBody() {
          tbody.textContent = '';
          for (const p of getSorted()) {
            const row = tbody.insertRow();
            row.style.borderBottom = '1px solid rgba(255,255,255,0.05)';
            const priceStr = p.odds != null ? (p.odds > 0 ? '+' + p.odds : String(p.odds)) : '\u2014';
            const edgeVal = (p.proj != null && p.line != null) ? (p.proj - p.line) : null;
            const edgeStr = edgeVal != null ? (edgeVal > 0 ? '+' : '') + edgeVal.toFixed(1) : '\u2014';
            const pcStr = p.pCover != null ? (p.pCover * 100).toFixed(1) + '%' : '\u2014';
            const cells = isBacktest ? [
              p.date ? (parseInt(p.date.slice(5,7))+'/'+parseInt(p.date.slice(8))) : '', displayName(p), p.team || '', p.opp || '',
              String(p.proj),
              p.line != null ? String(p.line) : '\u2014',
              edgeStr,
              pcStr,
              p.actual != null ? String(p.actual) : '\u2014',
              effectiveDir(p) === 'OVER' ? 'O' : 'U',
              priceStr,
              p.result === 'WIN' ? 'W' : p.result === 'LOSS' ? 'L' : '\u2014'
            ] : [
              displayName(p), p.team, p.opp || '', String(p.proj),
              p.line != null ? String(p.line) : '\u2014',
              edgeStr,
              pcStr,
              effectiveDir(p) === 'OVER' ? 'O' : 'U',
              priceStr
            ];
            cells.forEach((val, i) => {
              const td = row.insertCell();
              td.textContent = val;
              td.className = colClasses[i] || 'col-' + i;
              td.style.cssText = 'padding:4px 4px;text-align:center';
              if (isBacktest) {
                if (i === 1) { td.style.textAlign = 'left'; td.style.fontWeight = '600'; }
                if (i === 0) { td.style.color = '#999'; td.style.fontSize = '12px'; }
                if (i === 2 || i === 3) td.style.color = '#999';
                if (i === 4) td.style.color = p.proj > p.line ? 'var(--green)' : p.proj < p.line ? 'var(--red)' : '';
                if (i === 6) td.style.color = edgeVal > 0 ? 'var(--green)' : edgeVal < 0 ? 'var(--red)' : '#999';
                if (i === 7) td.style.color = '#aaa';
                if (i === 9) { td.style.fontWeight = '700'; td.style.color = effectiveDir(p) === 'OVER' ? 'var(--green)' : 'var(--red)'; }
                if (i === 10) td.style.color = '#999';
                if (i === 11) { td.style.fontWeight = '700'; td.style.color = p.result === 'WIN' ? 'var(--green)' : 'var(--red)'; }
              } else {
                if (i === 0) { td.style.textAlign = 'left'; td.style.fontWeight = '600'; }
                if (i === 1 || i === 2) td.style.color = '#999';
                if (i === 3) td.style.color = p.proj > p.line ? 'var(--green)' : p.proj < p.line ? 'var(--red)' : '';
                if (i === 5) td.style.color = edgeVal > 0 ? 'var(--green)' : edgeVal < 0 ? 'var(--red)' : '#999';
                if (i === 6) td.style.color = '#aaa';
                if (i === 7) { td.style.fontWeight = '700'; td.style.color = effectiveDir(p) === 'OVER' ? 'var(--green)' : 'var(--red)'; }
                if (i === 8) td.style.color = '#999';
              }
            });
          }
        }
        rebuildBody();

        wrap.appendChild(tbl);
        fitMLBTableToContainer(tbl);
        return wrap;
      }

      const PAGE_SIZE = 25;
      const WEEKS_PER_PAGE = 1;
      let allPicksPage = 0;
      let weeklyPage = 0;

      function renderAllPicksView() {
        contentArea.textContent = '';
        const filteredPicks = getFilteredPicks();
        filterLabel.textContent = `Showing ${filteredPicks.length} picks`;

        if (filteredPicks.length === 0) {
          const empty = document.createElement('div');
          empty.className = 'card-games';
          empty.appendChild(Object.assign(document.createElement('div'), {className:'no-picks', textContent:'No picks for selected filter.'}));
          contentArea.appendChild(empty);
          return;
        }

        const { fGrouped, sortedMarkets } = buildMLBMarketBreakdown(filteredPicks);

        // Single market selected -> paginated table for that market
        if (mlbActiveMarket !== 'all') {
          allPicksPage = Math.min(allPicksPage, Math.floor(Math.max(0, filteredPicks.length - 1) / PAGE_SIZE));
          const totalPages = Math.ceil(filteredPicks.length / PAGE_SIZE);
          const pageStart = allPicksPage * PAGE_SIZE;
          const pagePicks = filteredPicks.slice(pageStart, pageStart + PAGE_SIZE);

          const card = document.createElement('div');
          card.className = 'card-games';
          const mW = filteredPicks.filter(p => p.result === 'WIN').length;
          const mL = filteredPicks.filter(p => p.result === 'LOSS').length;
          const mU = calcMLBPropsUnits(filteredPicks);
          const titleSuffix = isBacktest ? ` (${mW}W-${mL}L ${mU>=0?'+':''}${mU.toFixed(2)}u)` : '';
          card.appendChild(Object.assign(document.createElement('div'), {className:'card-title', textContent:(marketLabels[mlbActiveMarket]||mlbActiveMarket)+titleSuffix}));

          if (totalPages > 1) {
            const pgBar = document.createElement('div');
            pgBar.className = 'props-pg-bar'; pgBar.style.cssText = 'display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap';
            const pgLabel = document.createElement('span');
            pgLabel.style.cssText = 'color:#999;font-size:12px;flex:1';
            pgLabel.textContent = `${pageStart+1}\u2013${Math.min(pageStart+PAGE_SIZE, filteredPicks.length)} of ${filteredPicks.length} picks`;
            const prevBtn = document.createElement('button');
            prevBtn.textContent = '\u2190 Prev';
            prevBtn.style.cssText = 'padding:4px 12px;border-radius:6px;border:1px solid rgba(255,255,255,0.15);background:transparent;color:#ccc;font-size:12px;cursor:pointer';
            prevBtn.disabled = allPicksPage === 0;
            prevBtn.style.opacity = allPicksPage === 0 ? '0.3' : '1';
            const nextBtn = document.createElement('button');
            nextBtn.textContent = 'Next \u2192';
            nextBtn.style.cssText = 'padding:4px 12px;border-radius:6px;border:1px solid rgba(255,255,255,0.15);background:transparent;color:#ccc;font-size:12px;cursor:pointer';
            nextBtn.disabled = allPicksPage >= totalPages - 1;
            nextBtn.style.opacity = allPicksPage >= totalPages - 1 ? '0.3' : '1';
            prevBtn.onclick = () => { allPicksPage--; renderAllPicksView(); window.scrollTo(0,0); };
            nextBtn.onclick = () => { allPicksPage++; renderAllPicksView(); window.scrollTo(0,0); };
            pgBar.appendChild(pgLabel);
            pgBar.appendChild(prevBtn);
            pgBar.appendChild(document.createTextNode(`Page ${allPicksPage+1} / ${totalPages}`));
            pgBar.appendChild(nextBtn);
            card.appendChild(pgBar);
          }

          card.appendChild(buildPropsTable(pagePicks));

          if (totalPages > 1) {
            const pgBar2 = document.createElement('div');
            pgBar2.style.cssText = 'display:flex;align-items:center;gap:10px;margin-top:12px;flex-wrap:wrap';
            const pgLabel2 = document.createElement('span');
            pgLabel2.style.cssText = 'color:#999;font-size:12px;flex:1';
            pgLabel2.textContent = `${pageStart+1}\u2013${Math.min(pageStart+PAGE_SIZE, filteredPicks.length)} of ${filteredPicks.length} picks`;
            const prevBtn2 = document.createElement('button');
            prevBtn2.textContent = '\u2190 Prev';
            prevBtn2.style.cssText = 'padding:4px 12px;border-radius:6px;border:1px solid rgba(255,255,255,0.15);background:transparent;color:#ccc;font-size:12px;cursor:pointer';
            prevBtn2.disabled = allPicksPage === 0;
            prevBtn2.style.opacity = allPicksPage === 0 ? '0.3' : '1';
            const nextBtn2 = document.createElement('button');
            nextBtn2.textContent = 'Next \u2192';
            nextBtn2.style.cssText = 'padding:4px 12px;border-radius:6px;border:1px solid rgba(255,255,255,0.15);background:transparent;color:#ccc;font-size:12px;cursor:pointer';
            nextBtn2.disabled = allPicksPage >= totalPages - 1;
            nextBtn2.style.opacity = allPicksPage >= totalPages - 1 ? '0.3' : '1';
            prevBtn2.onclick = () => { allPicksPage--; renderAllPicksView(); window.scrollTo(0,0); };
            nextBtn2.onclick = () => { allPicksPage++; renderAllPicksView(); window.scrollTo(0,0); };
            pgBar2.appendChild(pgLabel2);
            pgBar2.appendChild(prevBtn2);
            pgBar2.appendChild(document.createTextNode(`Page ${allPicksPage+1} / ${totalPages}`));
            pgBar2.appendChild(nextBtn2);
            card.appendChild(pgBar2);
          }

          contentArea.appendChild(card);
          return;
        }

        // All markets -> single paginated table
        allPicksPage = Math.min(allPicksPage, Math.floor((filteredPicks.length - 1) / PAGE_SIZE));
        const totalPages = Math.ceil(filteredPicks.length / PAGE_SIZE);
        const pageStart = allPicksPage * PAGE_SIZE;
        const pagePicks = filteredPicks.slice(pageStart, pageStart + PAGE_SIZE);

        const card = document.createElement('div');
        card.className = 'card-games';

        // Summary title: "ALL (XW-YL +Zu)" — mirrors the per-market view
        const aW = filteredPicks.filter(p => p.result === 'WIN').length;
        const aL = filteredPicks.filter(p => p.result === 'LOSS').length;
        const aU = calcMLBPropsUnits(filteredPicks);
        const allTitleSuffix = isBacktest ? ` (${aW}W-${aL}L ${aU>=0?'+':''}${aU.toFixed(2)}u)` : '';
        card.appendChild(Object.assign(document.createElement('div'),
          {className:'card-title', textContent:'ALL'+allTitleSuffix}));

        // Pagination controls
        const pgBar = document.createElement('div');
        pgBar.className = 'props-pg-bar'; pgBar.style.cssText = 'display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap';
        const pgLabel = document.createElement('span');
        pgLabel.style.cssText = 'color:#999;font-size:12px;flex:1';
        pgLabel.textContent = `${pageStart+1}\u2013${Math.min(pageStart+PAGE_SIZE, filteredPicks.length)} of ${filteredPicks.length} picks`;
        const prevBtn = document.createElement('button');
        prevBtn.textContent = '\u2190 Prev';
        prevBtn.style.cssText = 'padding:4px 12px;border-radius:6px;border:1px solid rgba(255,255,255,0.15);background:transparent;color:#ccc;font-size:12px;cursor:pointer';
        prevBtn.disabled = allPicksPage === 0;
        prevBtn.style.opacity = allPicksPage === 0 ? '0.3' : '1';
        const nextBtn = document.createElement('button');
        nextBtn.textContent = 'Next \u2192';
        nextBtn.style.cssText = 'padding:4px 12px;border-radius:6px;border:1px solid rgba(255,255,255,0.15);background:transparent;color:#ccc;font-size:12px;cursor:pointer';
        nextBtn.disabled = allPicksPage >= totalPages - 1;
        nextBtn.style.opacity = allPicksPage >= totalPages - 1 ? '0.3' : '1';
        prevBtn.onclick = () => { allPicksPage--; renderAllPicksView(); window.scrollTo(0,0); };
        nextBtn.onclick = () => { allPicksPage++; renderAllPicksView(); window.scrollTo(0,0); };
        pgBar.appendChild(pgLabel);
        pgBar.appendChild(prevBtn);
        pgBar.appendChild(document.createTextNode(`Page ${allPicksPage+1} / ${totalPages}`));
        pgBar.appendChild(nextBtn);
        card.appendChild(pgBar);

        // Unified table with Market column
        const tbl = document.createElement('table');
        tbl.style.cssText = 'width:100%;border-collapse:collapse';
        const hdrs = isBacktest
          ? ['Date','Name','Team','Opp','pOuts','aOuts','pBF','aBF','pPC','aPC','Proj','Line','Edge','%','Actual','O/U','Odds','W/L']
          : ['Name','Team','vs','pOuts','pBF','pPC','Proj','Line','Edge','%','O/U','Odds'];
        const hRow = tbl.createTHead().insertRow();
        hdrs.forEach(h => {
          const th = document.createElement('th');
          th.textContent = h;
          th.style.cssText = 'padding:4px 4px;text-align:center;border-bottom:1px solid rgba(255,255,255,0.1);font-size:12px';
          if (h === 'Name') th.style.textAlign = 'left';
          hRow.appendChild(th);
        });
        const tbody = tbl.createTBody();
        for (const p of pagePicks) {
          const row = tbody.insertRow();
          row.style.borderBottom = '1px solid rgba(255,255,255,0.05)';
          const ml = marketLabels[p.market] || p.market;
          const priceStr = p.odds != null ? (p.odds > 0 ? '+' + p.odds : String(p.odds)) : '\u2014';
          const edgeVal = (p.proj != null && p.line != null) ? (p.proj - p.line) : null;
          const edgeStr = edgeVal != null ? (edgeVal > 0 ? '+' : '') + edgeVal.toFixed(1) : '\u2014';
          const pcStr = p.pCover != null ? (p.pCover * 100).toFixed(1) + '%' : '\u2014';
          // Projected pitcher workload (matches the top "Today's Games" table).
          const pOutsStr  = p.proj_ip != null ? String(Math.round(p.proj_ip * 3)) : '\u2014';
          const pBfStr    = p.proj_bf != null ? String(Math.round(p.proj_bf)) : '\u2014';
          const pPitchStr = p.proj_pc != null ? String(Math.round(p.proj_pc)) : '\u2014';
          // Actuals \u2014 actual_outs, actual_bf, actual_pitches all captured
          // by run_daily and props_backfill from the bf/outs/pitches game-log fields.
          const aOutsStr  = p.actual_outs != null ? String(p.actual_outs) : '\u2014';
          const aBfStr    = p.actual_bf != null ? String(p.actual_bf) : '\u2014';
          const aPitchStr = p.actual_pitches != null ? String(p.actual_pitches) : '\u2014';
          const cells = isBacktest ? [
            p.date?(parseInt(p.date.slice(5,7))+'/'+parseInt(p.date.slice(8))):'', displayName(p), p.team||'', p.opp||'',
            pOutsStr, aOutsStr,
            pBfStr, aBfStr,
            pPitchStr, aPitchStr,
            String(p.proj), p.line!=null?String(p.line):'\u2014',
            edgeStr,
            pcStr,
            p.actual!=null?String(p.actual):'\u2014',
            effectiveDir(p)==='OVER'?'O':'U',
            priceStr,
            p.result==='WIN'?'W':p.result==='LOSS'?'L':'\u2014'
          ] : [
            displayName(p), p.team, p.opp||'',
            pOutsStr, pBfStr, pPitchStr,
            String(p.proj),
            p.line!=null?String(p.line):'\u2014',
            edgeStr,
            pcStr,
            effectiveDir(p)==='OVER'?'O':'U',
            priceStr
          ];
          cells.forEach((val, i) => {
            const td = row.insertCell();
            td.textContent = val;
            td.style.cssText = 'padding:4px 4px;text-align:center;font-size:13px';
            if (isBacktest) {
              // 0:Date 1:Name 2:Team 3:Opp
              // 4:pOuts 5:aOuts 6:pBF 7:aBF 8:pPC 9:aPC
              // 10:Proj 11:Line 12:Edge 13:%
              // 14:Actual 15:OU 16:Odds 17:W/L
              if (i===1) { td.style.textAlign='left'; td.style.fontWeight='600'; }
              if (i===0) { td.style.color='#999'; td.style.fontSize='11px'; }
              if (i===2||i===3) td.style.color='#999';
              if (i>=4 && i<=9) td.style.color='#bbb';
              if (i===10) td.style.color=p.proj>p.line?'var(--green)':p.proj<p.line?'var(--red)':'';
              if (i===12) td.style.color = edgeVal > 0 ? 'var(--green)' : edgeVal < 0 ? 'var(--red)' : '#999';
              if (i===13) td.style.color='#aaa';
              if (i===14) td.style.color='#bbb';
              if (i===15) { td.style.fontWeight='700'; td.style.color=effectiveDir(p)==='OVER'?'var(--green)':'var(--red)'; }
              if (i===16) td.style.color='#999';
              if (i===17) { td.style.fontWeight='700'; td.style.color=p.result==='WIN'?'var(--green)':'var(--red)'; }
            } else {
              // 0:Name 1:Team 2:vs 3:pOuts 4:pBF 5:pPC
              // 6:Proj 7:Line 8:Edge 9:% 10:OU 11:Odds
              if (i===0) { td.style.textAlign='left'; td.style.fontWeight='600'; }
              if (i===1||i===2) td.style.color='#999';
              if (i===3||i===4||i===5) td.style.color='#bbb';
              if (i===6) td.style.color=p.proj>p.line?'var(--green)':p.proj<p.line?'var(--red)':'';
              if (i===8) td.style.color = edgeVal > 0 ? 'var(--green)' : edgeVal < 0 ? 'var(--red)' : '#999';
              if (i===9) td.style.color='#aaa';
              if (i===10) { td.style.fontWeight='700'; td.style.color=effectiveDir(p)==='OVER'?'var(--green)':'var(--red)'; }
              if (i===11) td.style.color='#999';
            }
          });
        }
        card.appendChild(tbl);
        fitMLBTableToContainer(tbl);
        // Bottom pagination
        const pgBar2 = pgBar.cloneNode(true);
        pgBar2.style.marginTop = '12px';
        pgBar2.style.marginBottom = '0';
        pgBar2.querySelectorAll('button')[0].onclick = () => { allPicksPage--; renderAllPicksView(); window.scrollTo(0,0); };
        pgBar2.querySelectorAll('button')[1].onclick = () => { allPicksPage++; renderAllPicksView(); window.scrollTo(0,0); };
        card.appendChild(pgBar2);
        contentArea.appendChild(card);
      }

      function renderWeeklyView() {
        contentArea.textContent = '';
        let fp = activeSource().slice();
        if (weekSel.value !== 'all') fp = fp.filter(p => p.date && getWeekStart(p.date) === weekSel.value);
        if (daySel.value !== 'all') fp = fp.filter(p => p.date === daySel.value);
        if (dirSel.value !== 'all') fp = fp.filter(p => effectiveDir(p) === dirSel.value);
        if (bucketSel.value !== 'all') fp = fp.filter(p => inBucket(p, bucketSel.value));

        // Group by week start
        const weekMap = {};
        for (const p of fp) {
          if (!p.date) continue;
          const ws = getWeekStart(p.date);
          if (!weekMap[ws]) weekMap[ws] = [];
          weekMap[ws].push(p);
        }
        const sortedWeeks = Object.keys(weekMap).sort().reverse();

        weekFilterLabel.textContent = `${fp.length} picks across ${sortedWeeks.length} week${sortedWeeks.length !== 1 ? 's' : ''}`;

        if (sortedWeeks.length === 0) {
          const empty = document.createElement('div');
          empty.className = 'card-games';
          empty.appendChild(Object.assign(document.createElement('div'), {className:'no-picks', textContent:'No picks for selected filter.'}));
          contentArea.appendChild(empty);
          return;
        }

        // Weekly summary table
        const summCard = document.createElement('div');
        summCard.className = 'card-games';
        summCard.style.marginBottom = '16px';
        summCard.appendChild(Object.assign(document.createElement('div'), {className:'card-title', textContent:'Weekly Summary'}));
        const summWrap = document.createElement('div');
        summWrap.className = 'props-table-wrap';
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
        for (const ws of [...sortedWeeks].reverse()) {
          const wPicks = weekMap[ws];
          const w = wPicks.filter(p => p.result === 'WIN').length;
          const l = wPicks.filter(p => p.result === 'LOSS').length;
          const u = calcMLBPropsUnits(wPicks);
          const pct = (w + l) > 0 ? (w / (w + l) * 100).toFixed(1) : '\u2014';
          totW += w; totL += l;
          const sr = sb.insertRow();
          const we = getWeekEnd(ws);
          [`${ws} \u2013 ${we}`, String(wPicks.length), String(w), String(l),
           (w+l>0?pct+'%':'\u2014'), (w+l>0?(u>=0?'+':'')+u.toFixed(2)+'u':'\u2014')].forEach((v, i) => {
            const td = sr.insertCell();
            td.textContent = v;
            td.style.padding = '6px 10px';
            td.style.textAlign = i === 0 ? 'left' : 'right';
            if (i === 5 && w+l > 0) td.style.color = u >= 0 ? 'var(--green)' : 'var(--red)';
          });
        }
        const totU = calcMLBPropsUnits(fp);
        const tr = sb.insertRow();
        tr.style.borderTop = '2px solid rgba(255,255,255,0.2)';
        tr.style.fontWeight = '700';
        ['TOTAL', String(fp.length), String(totW), String(totL),
         (totW+totL>0?(totW/(totW+totL)*100).toFixed(1)+'%':'\u2014'),
         (totU>=0?'+':'')+totU.toFixed(2)+'u'].forEach((v,i) => {
          const td = tr.insertCell();
          td.textContent = v;
          td.style.padding = '6px 10px';
          td.style.textAlign = i === 0 ? 'left' : 'right';
          if (i === 5) td.style.color = totU >= 0 ? 'var(--green)' : 'var(--red)';
        });
        summWrap.appendChild(summTbl); summCard.appendChild(summWrap);
        contentArea.appendChild(summCard);

        // Per-week pick cards -- paginated (WEEKS_PER_PAGE per page)
        const totalWeekPages = Math.ceil(sortedWeeks.length / WEEKS_PER_PAGE);
        weeklyPage = Math.min(weeklyPage, Math.max(0, totalWeekPages - 1));
        const weekPageStart = weeklyPage * WEEKS_PER_PAGE;
        const visibleWeeks = weekSel.value !== 'all'
          ? sortedWeeks  // specific week selected -> show it (no pagination)
          : sortedWeeks.slice(weekPageStart, weekPageStart + WEEKS_PER_PAGE);

        // Pagination bar (only shown when "all weeks" and more than one page)
        if (weekSel.value === 'all' && totalWeekPages > 1) {
          const pgBar = document.createElement('div');
          pgBar.className = 'props-pg-bar'; pgBar.style.cssText = 'display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap';
          const pgLabel = document.createElement('span');
          pgLabel.style.cssText = 'color:#999;font-size:12px;flex:1';
          pgLabel.textContent = `Weeks ${weekPageStart+1}\u2013${Math.min(weekPageStart+WEEKS_PER_PAGE, sortedWeeks.length)} of ${sortedWeeks.length}`;
          const prevBtn = document.createElement('button');
          prevBtn.textContent = '\u2190 Prev';
          prevBtn.style.cssText = 'padding:4px 12px;border-radius:6px;border:1px solid rgba(255,255,255,0.15);background:transparent;color:#ccc;font-size:12px;cursor:pointer';
          prevBtn.disabled = weeklyPage === 0;
          prevBtn.style.opacity = weeklyPage === 0 ? '0.3' : '1';
          const nextBtn = document.createElement('button');
          nextBtn.textContent = 'Next \u2192';
          nextBtn.style.cssText = 'padding:4px 12px;border-radius:6px;border:1px solid rgba(255,255,255,0.15);background:transparent;color:#ccc;font-size:12px;cursor:pointer';
          nextBtn.disabled = weeklyPage >= totalWeekPages - 1;
          nextBtn.style.opacity = weeklyPage >= totalWeekPages - 1 ? '0.3' : '1';
          prevBtn.onclick = () => { weeklyPage--; renderWeeklyView(); window.scrollTo(0,0); };
          nextBtn.onclick = () => { weeklyPage++; renderWeeklyView(); window.scrollTo(0,0); };
          pgBar.appendChild(pgLabel);
          pgBar.appendChild(prevBtn);
          pgBar.appendChild(document.createTextNode(`Page ${weeklyPage+1} / ${totalWeekPages}`));
          pgBar.appendChild(nextBtn);
          contentArea.appendChild(pgBar);
        }

        for (const ws of visibleWeeks) {
          const wPicks = weekMap[ws].slice().sort((a, b) => (b.date || '').localeCompare(a.date || ''));
          const we = getWeekEnd(ws);
          const wW = wPicks.filter(p => p.result === 'WIN').length;
          const wL = wPicks.filter(p => p.result === 'LOSS').length;
          const wU = calcMLBPropsUnits(wPicks);
          const wPct = (wW + wL) > 0 ? ` \u2014 ${wW}W-${wL}L (${(wW/(wW+wL)*100).toFixed(1)}%) ${wU>=0?'+':''}${wU.toFixed(2)}u` : ` \u2014 ${wPicks.length} picks`;

          const card = document.createElement('div');
          card.className = 'card-games';
          card.appendChild(Object.assign(document.createElement('div'), {
            className: 'card-title',
            textContent: `Week of ${ws} \u2013 ${we}${wPct}`
          }));

          // Mini market breakdown for this week
          const { fGrouped: wGrouped, sortedMarkets: wMarkets } = buildMLBMarketBreakdown(wPicks);
          if (wMarkets.length > 1) {
            const mkRow = document.createElement('div');
            mkRow.style.cssText = 'display:flex;gap:16px;flex-wrap:wrap;margin:8px 0 12px;font-size:12px;color:#999';
            for (const mk of wMarkets) {
              const mp = wGrouped[mk];
              const mw = mp.filter(p => p.result === 'WIN').length;
              const ml2 = mp.length - mw;
              const mu = calcMLBPropsUnits(mp);
              const span = document.createElement('span');
              span.innerHTML = `<span style="color:#ccc">${mk}</span> ${mw}W-${ml2}L <span style="color:${mu>=0?'var(--green)':'var(--red)'}">${mu>=0?'+':''}${mu.toFixed(2)}u</span>`;
              mkRow.appendChild(span);
            }
            card.appendChild(mkRow);
          }

          card.appendChild(buildPropsTable(wPicks));
          contentArea.appendChild(card);
        }
      }

      function refreshView() {
        if (mlbView === 'weekly' || mlbView === 'weekly-lean') renderWeeklyView();
        else renderAllPicksView();
      }

      function setView(v) {
        mlbView = v;
        viewAllBtn.style.cssText = v === 'all' ? tabActiveStyle : tabStyle;
        viewWeeklyBtn.style.cssText = v === 'weekly' ? tabActiveStyle : tabStyle;
        viewAllLeanBtn.style.cssText = v === 'all-lean' ? tabActiveStyle : tabStyle;
        viewWeeklyLeanBtn.style.cssText = v === 'weekly-lean' ? tabActiveStyle : tabStyle;
        viewAllCombinedBtn.style.cssText = v === 'all-combined' ? tabActiveStyle : tabStyle;
        // Swap filter row contents
        filterRow.textContent = '';
        const isWeekly = (v === 'weekly' || v === 'weekly-lean');
        if (!isWeekly) {
          filterRow.appendChild(dateSel);
          filterRow.appendChild(teamSel);
          filterRow.appendChild(dirSel);
          filterRow.appendChild(bucketSel);
          filterRow.appendChild(filterLabel);
        } else {
          refreshDayOptions();
          filterRow.appendChild(weekSel);
          filterRow.appendChild(daySel);
          filterRow.appendChild(dirSel);
          filterRow.appendChild(bucketSel);
          filterRow.appendChild(weekFilterLabel);
        }
        refreshView();
      }

      viewAllBtn.onclick = () => setView('all');
      viewWeeklyBtn.onclick = () => setView('weekly');
      viewAllLeanBtn.onclick = () => setView('all-lean');
      viewWeeklyLeanBtn.onclick = () => setView('weekly-lean');
      viewAllCombinedBtn.onclick = () => setView('all-combined');
      dateSel.addEventListener('change', renderAllPicksView);
      teamSel.addEventListener('change', renderAllPicksView);
      dirSel.addEventListener('change', refreshView);
      bucketSel.addEventListener('change', refreshView);
      weekSel.addEventListener('change', () => { refreshDayOptions(); renderWeeklyView(); });
      daySel.addEventListener('change', renderWeeklyView);

      renderMLBMarketBtns();
      setView('all');

      // Reddit summary card — always rendered at the very bottom.
      if (_renderRedditCard) _renderRedditCard();
      // Matchup History is appended above directly under Today's Games.
    }

    // =====================================================================
    // MLB Batter Props (Total Bases) — same layout as pitcher props
    // =====================================================================
    async function renderMLBBatterProps() {
      const el = document.getElementById('content');
      const data = await fetchData('mlb-batter-props');
      if (!data || !data.batterProps || !data.batterProps.length) {
        el.textContent = '';
        const card = document.createElement('div');
        card.className = 'card-games';
        card.appendChild(Object.assign(document.createElement('div'), {className:'card-title', textContent:'MLB Batter Props'}));
        card.appendChild(Object.assign(document.createElement('div'), {className:'no-picks', textContent:'No batter prop projections available yet. Run the batter props pipeline to generate projections.'}));
        el.appendChild(card);
        return;
      }

      const marketLabels = {total_bases:'TB'};
      const picks = data.batterProps.filter(p => p.pick !== 'PASS');
      const isBacktest = picks.some(p => p.result != null);

      function getWeekStart(dateStr) {
        const d = new Date(dateStr + 'T00:00:00Z');
        const day = d.getUTCDay();
        const diff = day === 0 ? -6 : 1 - day;
        d.setUTCDate(d.getUTCDate() + diff);
        return d.toISOString().slice(0, 10);
      }
      function getWeekEnd(weekStart) {
        const d = new Date(weekStart + 'T00:00:00Z');
        d.setUTCDate(d.getUTCDate() + 6);
        return d.toISOString().slice(0, 10);
      }

      el.textContent = '';

      function displayName(p) {
        return mlbShortName(p.player);
      }

      function buildBatterMarketBreakdown(filteredPicks) {
        const mlbMarketOrder = ['TB'];
        const fGrouped = {};
        for (const p of filteredPicks) {
          const ml = marketLabels[p.market] || p.market;
          if (!fGrouped[ml]) fGrouped[ml] = [];
          fGrouped[ml].push(p);
        }
        const sortedMarkets = Object.keys(fGrouped).sort((a, b) => {
          const ia = mlbMarketOrder.indexOf(a); const ib = mlbMarketOrder.indexOf(b);
          return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
        });
        return { fGrouped, sortedMarkets };
      }

      // ── Yesterday's Recap + Today's Picks ──
      (function renderBatterDailyCards() {
        const allDates = [...new Set(picks.map(p => p.date))].sort();
        const latestPickDate = allDates[allDates.length - 1] || '';
        // Anchor on data.date so zero-pick days still advance the card.
        const todayStr = data.date || latestPickDate;
        const yest = new Date(todayStr + 'T12:00:00');
        yest.setDate(yest.getDate() - 1);
        const yesterdayStr = yest.toISOString().slice(0, 10);

        const gradedPicks = picks.filter(p => p.result && p.result !== 'VOID');

        // Season Market Breakdown
        if (gradedPicks.length > 0) {
          const { fGrouped, sortedMarkets } = buildBatterMarketBreakdown(gradedPicks);
          const mbCard = document.createElement('div');
          mbCard.className = 'card card-games';
          mbCard.style.marginBottom = '16px';
          mbCard.appendChild(Object.assign(document.createElement('div'), {className:'card-title', textContent:'Season Record'}));
          const mbWrap = document.createElement('div');
          mbWrap.className = 'props-table-wrap';
          const mbTbl = document.createElement('table');
          mbTbl.style.cssText = 'width:100%;border-collapse:collapse;margin-top:8px';
          const mh = mbTbl.createTHead().insertRow();
          ['Cat','Picks','W','L','Win%','Units','ROI'].forEach(h => {
            const th = document.createElement('th');
            th.textContent = h;
            th.style.cssText = 'padding:6px 10px;text-align:right;border-bottom:1px solid rgba(255,255,255,0.1)';
            if (h === 'Cat') th.style.textAlign = 'left';
            mh.appendChild(th);
          });
          const mb = mbTbl.createTBody();
          let gW = 0, gL = 0;
          for (const market of sortedMarkets) {
            const mPicks = fGrouped[market];
            const w = mPicks.filter(p => p.result === 'WIN').length;
            const l = mPicks.filter(p => p.result === 'LOSS').length;
            const u = calcMLBPropsUnits(mPicks);
            const pct = (w + l) > 0 ? (w / (w + l) * 100).toFixed(1) : 'n/a';
            const roi = (w + l) > 0 ? (u / (w + l) * 100).toFixed(1) : 'n/a';
            gW += w; gL += l;
            const sr = mb.insertRow();
            [market, String(mPicks.length), String(w), String(l), pct+'%', (u>=0?'+':'')+u.toFixed(2)+'u', (roi>=0?'+':'')+roi+'%'].forEach((v,i) => {
              const td = sr.insertCell();
              td.textContent = v;
              td.style.padding = '6px 10px';
              td.style.textAlign = i === 0 ? 'left' : 'right';
              if (i === 5) td.style.color = u >= 0 ? 'var(--green)' : 'var(--red)';
              if (i === 6) td.style.color = parseFloat(roi) >= 0 ? 'var(--green)' : 'var(--red)';
            });
          }
          const gU = calcMLBPropsUnits(gradedPicks);
          const gROI = (gW+gL) > 0 ? (gU / (gW+gL) * 100).toFixed(1) : '0';
          const tr = mb.insertRow();
          tr.style.borderTop = '2px solid rgba(255,255,255,0.2)';
          tr.style.fontWeight = '700';
          ['TOTAL', String(gradedPicks.length), String(gW), String(gL),
           (gW+gL>0?(gW/(gW+gL)*100).toFixed(1):'0')+'%',
           (gU>=0?'+':'')+gU.toFixed(2)+'u', (gROI>=0?'+':'')+gROI+'%'].forEach((v,i) => {
            const td = tr.insertCell();
            td.textContent = v;
            td.style.padding = '6px 10px';
            td.style.textAlign = i === 0 ? 'left' : 'right';
            if (i === 5) td.style.color = gU >= 0 ? 'var(--green)' : 'var(--red)';
            if (i === 6) td.style.color = parseFloat(gROI) >= 0 ? 'var(--green)' : 'var(--red)';
          });
          mbWrap.appendChild(mbTbl);
          mbCard.appendChild(mbWrap);
          el.appendChild(mbCard);
        }

        // Yesterday's Recap
        const yesterdayPicks = picks.filter(p =>
          p.date === yesterdayStr && p.result && p.result !== 'VOID'
        );
        if (yesterdayPicks.length > 0) {
          const yW = yesterdayPicks.filter(p => p.result === 'WIN').length;
          const yL = yesterdayPicks.filter(p => p.result === 'LOSS').length;
          const yU = calcMLBPropsUnits(yesterdayPicks);
          const uColor = yU >= 0 ? 'var(--green)' : 'var(--red)';
          const recapCard = document.createElement('div');
          recapCard.className = 'card card-recap';
          recapCard.style.marginBottom = '16px';
          recapCard.appendChild(Object.assign(document.createElement('div'), {
            className: 'card-title',
            textContent: `Yesterday\u2019s Recap (${yesterdayStr})`
          }));
          const tbl = document.createElement('table');
          tbl.className = 'data';
          tbl.style.cssText = 'width:100%;border-collapse:collapse;margin-top:8px';
          const hRow = tbl.createTHead().insertRow();
          ['Player','Team','Opp','Proj','Line','Edge','Price','Actual','Pick','Result'].forEach((h, i) => {
            const th = document.createElement('th');
            th.textContent = h;
            th.style.cssText = 'padding:6px 10px;border-bottom:1px solid rgba(255,255,255,0.1);' + (i === 0 ? 'text-align:left' : 'text-align:center');
            hRow.appendChild(th);
          });
          const tbody = tbl.createTBody();
          for (const p of yesterdayPicks.sort((a,b) => (b.pCover||0) - (a.pCover||0))) {
            const row = tbody.insertRow();
            row.style.borderBottom = '1px solid rgba(255,255,255,0.05)';
            const yEdge = (p.proj != null && p.line != null) ? +(p.proj - p.line).toFixed(2) : null;
            const yEdgeStr = yEdge != null ? (yEdge > 0 ? '+'+yEdge : String(yEdge)) : '\u2014';
            const yPrice = p.odds != null ? (p.odds > 0 ? '+'+p.odds : String(p.odds)) : '\u2014';
            [displayName(p), p.team||'', p.opp||'',
             p.proj!=null?p.proj.toFixed(2):'\u2014', p.line!=null?String(p.line):'\u2014', yEdgeStr, yPrice,
             p.actual!=null?String(p.actual):'\u2014',
             p.pick==='OVER'?'O':'U', p.result==='WIN'?'W':'L'].forEach((v, i) => {
              const td = row.insertCell();
              td.textContent = v;
              td.style.cssText = 'padding:4px 4px;text-align:center';
              if (i === 0) { td.style.textAlign = 'left'; td.style.fontWeight = '600'; }
              if (i === 1 || i === 2) td.style.color = '#999';
              if (i === 3) td.style.color = p.proj > p.line ? 'var(--green)' : p.proj < p.line ? 'var(--red)' : '';
              if (i === 5 && yEdge != null) td.style.color = yEdge > 0 ? 'var(--green)' : yEdge < 0 ? 'var(--red)' : '#999';
              if (i === 6) td.style.color = '#999';
              if (i === 8) { td.style.fontWeight = '700'; td.style.color = p.pick === 'OVER' ? 'var(--green)' : 'var(--red)'; }
              if (i === 9) { td.style.fontWeight = '700'; td.style.color = p.result === 'WIN' ? 'var(--green)' : 'var(--red)'; }
            });
          }
          recapCard.appendChild(tbl);
          fitMLBTableToContainer(tbl);
          const tally = document.createElement('div');
          tally.className = 'l10-tally';
          tally.innerHTML = `Props: <b>${yW}W-${yL}L</b> &middot; <span style="color:${uColor}">${yU >= 0 ? '+' : ''}${yU.toFixed(2)}u</span>`;
          recapCard.appendChild(tally);
          el.appendChild(recapCard);
        }

        // Today's Picks
        const todayPicks = picks.filter(p => p.date === todayStr);
        if (todayPicks.length > 0) {
          const todayCard = document.createElement('div');
          todayCard.className = 'card card-picks';
          todayCard.style.marginBottom = '16px';
          todayCard.appendChild(Object.assign(document.createElement('div'), {
            className: 'card-title',
            textContent: `Today\u2019s Picks (${todayStr})`
          }));
          const tbl = document.createElement('table');
          tbl.className = 'props-data-table';
          tbl.style.cssText = 'width:100%;border-collapse:collapse;margin-top:8px';
          const todayHeaders = ['Player','Team','Opp','Opp SP','Proj','Line','Edge','Price','Pick'];
          const hRow = tbl.createTHead().insertRow();
          todayHeaders.forEach((h, i) => {
            const th = document.createElement('th');
            th.textContent = h;
            th.style.cssText = 'padding:4px 4px;text-align:center;border-bottom:1px solid rgba(255,255,255,0.1)';
            if (h === 'Player') th.style.textAlign = 'left';
            hRow.appendChild(th);
          });
          const tbody = tbl.createTBody();
          todayPicks.sort((a,b) => (b.pCover||0) - (a.pCover||0));
          for (const p of todayPicks) {
            const row = tbody.insertRow();
            row.style.borderBottom = '1px solid rgba(255,255,255,0.05)';
            const tEdge = (p.proj != null && p.line != null) ? +(p.proj - p.line).toFixed(2) : null;
            const tEdgeStr = tEdge != null ? (tEdge > 0 ? '+'+tEdge : String(tEdge)) : '\u2014';
            const tPrice = p.odds != null ? (p.odds > 0 ? '+'+p.odds : String(p.odds)) : '\u2014';
            const oppSP = p.opp_pitcher ? mlbShortName(p.opp_pitcher) : '\u2014';
            const cells = [
              displayName(p), p.team || '', p.opp || '', oppSP,
              p.proj!=null?p.proj.toFixed(2):'\u2014',
              p.line != null ? String(p.line) : '\u2014',
              tEdgeStr, tPrice,
              p.pick === 'OVER' ? 'O' : 'U'
            ];
            cells.forEach((val, i) => {
              const td = row.insertCell();
              td.textContent = val;
              td.style.cssText = 'padding:4px 4px;text-align:center';
              if (i === 0) { td.style.textAlign = 'left'; td.style.fontWeight = '600'; }
              if (i === 1 || i === 2) td.style.color = '#999';
              if (i === 3) td.style.color = '#aaa'; // opp SP
              if (i === 4) td.style.color = p.proj > p.line ? 'var(--green)' : p.proj < p.line ? 'var(--red)' : '';
              if (i === 6 && tEdge != null) td.style.color = tEdge > 0 ? 'var(--green)' : tEdge < 0 ? 'var(--red)' : '#999';
              if (i === 7) td.style.color = '#999';
              if (i === 8) { td.style.fontWeight = '700'; td.style.color = p.pick === 'OVER' ? 'var(--green)' : 'var(--red)'; }
            });
          }
          todayCard.appendChild(tbl);
          el.appendChild(todayCard);
        } else {
          const todayCard = document.createElement('div');
          todayCard.className = 'card card-picks';
          todayCard.style.marginBottom = '16px';
          todayCard.appendChild(Object.assign(document.createElement('div'), {
            className: 'card-title',
            textContent: `Today’s Picks (${todayStr})`
          }));
          const empty = document.createElement('div');
          empty.className = 'no-picks';
          empty.style.cssText = 'padding:12px;color:#888;font-style:italic;font-size:13px';
          empty.textContent = 'No picks for today.';
          todayCard.appendChild(empty);
          el.appendChild(todayCard);
        }
      })();

      // ── Today's Batter Explorer ──
      (function renderBatterGamesSection() {
        const allDates = [...new Set(picks.map(p => p.date))].sort();
        const todayStr = allDates[allDates.length - 1] || '';
        const todayAllProj = (data.batterProjections || data.batterProps)
          .filter(p => p.date === todayStr && p.proj != null && p.line != null);
        if (todayAllProj.length === 0) return;

        // Build unique games, sorted by start time
        const gameTimes = data.gameTimes || {};
        const gameSet = new Map();
        for (const p of todayAllProj) {
          const key = [p.team, p.opp].sort().join('@');
          if (!gameSet.has(key)) {
            const t = gameTimes[p.team] || gameTimes[p.opp] || '9999';
            gameSet.set(key, { label: `${p.team} vs ${p.opp}`, time: t });
          }
        }
        const games = [...gameSet.entries()]
          .sort((a, b) => (a[1].time || '').localeCompare(b[1].time || ''))
          .map(([k, v]) => [k, v.label]);

        const gCard = document.createElement('div');
        gCard.className = 'card';
        gCard.style.cssText = 'padding:0;margin-bottom:16px;overflow:hidden';

        const titleRow = document.createElement('div');
        titleRow.style.cssText = 'padding:12px 16px 8px;border-bottom:1px solid rgba(255,255,255,0.08)';
        titleRow.appendChild(Object.assign(document.createElement('div'), {className:'card-title', textContent:`Today\u2019s Batters (${todayStr})`}));
        gCard.appendChild(titleRow);

        // Game selector pills
        const gamePills = document.createElement('div');
        gamePills.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap;padding:10px 16px;border-bottom:1px solid rgba(255,255,255,0.08)';
        let activeGame = 'all';

        // Player dropdown
        const playerRow = document.createElement('div');
        playerRow.style.cssText = 'padding:8px 16px;border-bottom:1px solid rgba(255,255,255,0.08);display:flex;align-items:center;gap:8px';
        const playerLabel = Object.assign(document.createElement('span'), {textContent:'Player:', style:'font-size:12px;color:#999'});
        const playerSelect = document.createElement('select');
        playerSelect.style.cssText = 'padding:5px 10px;border-radius:6px;background:rgba(255,255,255,0.06);color:#fff;border:1px solid rgba(255,255,255,0.1);font-size:12px;outline:none;cursor:pointer;max-width:200px';
        let activePlayer = 'all';
        playerRow.appendChild(playerLabel);
        playerRow.appendChild(playerSelect);

        const tableWrap = document.createElement('div');
        tableWrap.style.cssText = 'padding:12px 16px';

        function refreshPlayerDropdown() {
          playerSelect.textContent = '';
          const playersInGame = [...new Set(
            todayAllProj.filter(p => activeGame === 'all' || [p.team, p.opp].sort().join('@') === activeGame)
              .map(p => p.player)
          )].sort();
          const allOpt = document.createElement('option');
          allOpt.value = 'all'; allOpt.textContent = 'All Players';
          playerSelect.appendChild(allOpt);
          for (const name of playersInGame) {
            const opt = document.createElement('option');
            opt.value = name; opt.textContent = name;
            playerSelect.appendChild(opt);
          }
          playerSelect.value = activePlayer === 'all' || !playersInGame.includes(activePlayer) ? 'all' : activePlayer;
          activePlayer = playerSelect.value;
        }
        playerSelect.onchange = () => { activePlayer = playerSelect.value; bCurrentPage = 0; renderBatterTable(); };

        const B_PAGE_SIZE = 30;
        let bCurrentPage = 0;
        let bSortCol = 'cover';
        let bSortDir = -1;

        function sortRows(rows) {
          return [...rows].sort((a, b) => {
            let v;
            if (bSortCol === 'edge') v = ((b.proj??0)-(b.line??0)) - ((a.proj??0)-(a.line??0));
            else if (bSortCol === 'cover') v = (b.pCover??0) - (a.pCover??0);
            else if (bSortCol === 'proj') v = (b.proj??0) - (a.proj??0);
            else v = 0;
            return v * bSortDir;
          });
        }

        function renderBatterTable() {
          tableWrap.textContent = '';
          const gameProj = todayAllProj.filter(p => {
            const key = [p.team, p.opp].sort().join('@');
            const gameMatch = activeGame === 'all' || key === activeGame;
            const playerMatch = activePlayer === 'all' || p.player === activePlayer;
            return gameMatch && playerMatch;
          });
          if (gameProj.length === 0) {
            tableWrap.appendChild(Object.assign(document.createElement('div'), {textContent:'No projections found.', style:'color:#666;font-size:13px'}));
            return;
          }

          const sorted = sortRows(gameProj);
          const totalPages = Math.ceil(sorted.length / B_PAGE_SIZE);
          bCurrentPage = Math.max(0, Math.min(bCurrentPage, totalPages - 1));
          const pageRows = sorted.slice(bCurrentPage * B_PAGE_SIZE, (bCurrentPage + 1) * B_PAGE_SIZE);

          const tbl = document.createElement('table');
          tbl.style.cssText = 'width:100%;border-collapse:collapse';
          const hRow = tbl.createTHead().insertRow();

          const cols = [
            ['Player', null, true],
            ['Team',   null, false],
            ['Opp SP', null, false],
            ['Proj',   'proj', false],
            ['Line',   null, false],
            ['Edge',   'edge', false],
            ['Cover%', 'cover', false],
            ['Pick',   null, false],
          ];
          cols.forEach(([label, key, leftAlign]) => {
            const th = document.createElement('th');
            const isActive = key && bSortCol === key;
            const arrow = isActive ? (bSortDir === 1 ? ' \u2191' : ' \u2193') : '';
            th.textContent = label + arrow;
            th.style.cssText = `padding:5px 8px;text-align:${leftAlign?'left':'center'};border-bottom:1px solid rgba(255,255,255,0.1);font-size:12px;color:${isActive?'#fff':'#999'};${key?'cursor:pointer;user-select:none':''}`;
            if (key) th.onclick = () => {
              if (bSortCol === key) { bSortDir *= -1; } else { bSortCol = key; bSortDir = -1; }
              bCurrentPage = 0;
              renderBatterTable();
            };
            hRow.appendChild(th);
          });

          const tbody = tbl.createTBody();
          for (const p of pageRows) {
            const row = tbody.insertRow();
            row.style.borderBottom = '1px solid rgba(255,255,255,0.04)';
            const isPick = p.pick && p.pick !== 'PASS';
            if (isPick) row.style.background = 'rgba(124,108,240,0.06)';
            const edge = (p.proj != null && p.line != null) ? +(p.proj - p.line).toFixed(2) : null;
            const edgeStr = edge != null ? (edge > 0 ? '+'+edge : String(edge)) : '\u2014';
            const coverStr = p.pCover != null ? (p.pCover * 100).toFixed(1) + '%' : '\u2014';
            const oppSP = p.opp_pitcher ? mlbShortName(p.opp_pitcher) : '\u2014';
            [displayName(p), p.team||'', oppSP,
             p.proj!=null?p.proj.toFixed(2):'\u2014', p.line!=null?String(p.line):'\u2014',
             edgeStr, coverStr,
             isPick?(p.pick==='OVER'?'O':'U'):'\u2014'
            ].forEach((v,i) => {
              const td = row.insertCell();
              td.textContent = v;
              td.style.cssText = 'padding:5px 8px;text-align:'+(i===0?'left':'center')+';font-size:12px';
              if (i===0) td.style.fontWeight = '600';
              if (i===1) td.style.color = '#999';
              if (i===2) td.style.color = '#aaa';
              if (i===3 && p.line!=null) td.style.color = p.proj > p.line ? 'var(--green)' : p.proj < p.line ? 'var(--red)' : '';
              if (i===5 && edge!=null) td.style.color = edge > 0 ? 'var(--green)' : edge < 0 ? 'var(--red)' : '#999';
              if (i===6 && p.pCover!=null) td.style.color = p.pCover >= 0.70 ? 'var(--green)' : p.pCover >= 0.65 ? 'var(--yellow)' : p.pCover <= 0.45 ? 'var(--red)' : '#ccc';
              if (i===7 && isPick) { td.style.fontWeight='700'; td.style.color = p.pick==='OVER'?'var(--green)':'var(--red)'; }
            });
          }
          tableWrap.appendChild(tbl);

          if (totalPages > 1) {
            const pgRow = document.createElement('div');
            pgRow.style.cssText = 'display:flex;align-items:center;justify-content:center;gap:8px;padding:10px 0 2px';
            const prevBtn = document.createElement('button');
            prevBtn.textContent = '\u2190 Prev';
            prevBtn.style.cssText = 'padding:4px 12px;border-radius:6px;border:1px solid rgba(255,255,255,0.12);background:transparent;color:'+(bCurrentPage===0?'#444':'#ccc')+';font-size:12px;cursor:'+(bCurrentPage===0?'default':'pointer');
            prevBtn.disabled = bCurrentPage === 0;
            prevBtn.onclick = () => { bCurrentPage--; renderBatterTable(); tableWrap.scrollIntoView({behavior:'smooth',block:'nearest'}); };
            const nextBtn = document.createElement('button');
            nextBtn.textContent = 'Next \u2192';
            nextBtn.style.cssText = 'padding:4px 12px;border-radius:6px;border:1px solid rgba(255,255,255,0.12);background:transparent;color:'+(bCurrentPage===totalPages-1?'#444':'#ccc')+';font-size:12px;cursor:'+(bCurrentPage===totalPages-1?'default':'pointer');
            nextBtn.disabled = bCurrentPage === totalPages - 1;
            nextBtn.onclick = () => { bCurrentPage++; renderBatterTable(); tableWrap.scrollIntoView({behavior:'smooth',block:'nearest'}); };
            const info = Object.assign(document.createElement('span'), {
              textContent: `Page ${bCurrentPage+1} of ${totalPages}  (${sorted.length} rows)`,
              style: 'font-size:12px;color:#666'
            });
            pgRow.appendChild(prevBtn);
            pgRow.appendChild(info);
            pgRow.appendChild(nextBtn);
            tableWrap.appendChild(pgRow);
          }
        }

        function refreshPills() {
          gamePills.textContent = '';
          const allGamesBtn = document.createElement('button');
          allGamesBtn.textContent = 'All';
          allGamesBtn.style.cssText = activeGame === 'all'
            ? 'padding:5px 14px;border-radius:16px;border:1px solid #7c6cf0;background:#7c6cf0;color:#fff;font-size:12px;cursor:pointer'
            : 'padding:5px 14px;border-radius:16px;border:1px solid rgba(255,255,255,0.12);background:transparent;color:#999;font-size:12px;cursor:pointer';
          allGamesBtn.onclick = () => { activeGame = 'all'; activePlayer = 'all'; bCurrentPage = 0; refreshPills(); refreshPlayerDropdown(); renderBatterTable(); };
          gamePills.appendChild(allGamesBtn);
          for (const [key, label] of games) {
            const btn = document.createElement('button');
            btn.textContent = label;
            btn.style.cssText = key === activeGame
              ? 'padding:5px 14px;border-radius:16px;border:1px solid #7c6cf0;background:#7c6cf0;color:#fff;font-size:12px;cursor:pointer'
              : 'padding:5px 14px;border-radius:16px;border:1px solid rgba(255,255,255,0.12);background:transparent;color:#999;font-size:12px;cursor:pointer';
            btn.onclick = () => { activeGame = key; activePlayer = 'all'; bCurrentPage = 0; refreshPills(); refreshPlayerDropdown(); renderBatterTable(); };
            gamePills.appendChild(btn);
          }
        }

        refreshPills();
        refreshPlayerDropdown();
        renderBatterTable();
        gCard.appendChild(gamePills);
        gCard.appendChild(playerRow);
        gCard.appendChild(tableWrap);
        el.appendChild(gCard);
      })();

      // ── All Picks (with weekly view) ──
      const selStyle = 'padding:6px 12px;border-radius:6px;background:rgba(255,255,255,0.06);color:#fff;border:1px solid rgba(255,255,255,0.1);font-size:13px;outline:none';
      const pillStyle = 'padding:5px 14px;border-radius:16px;border:1px solid rgba(255,255,255,0.12);background:transparent;color:#999;font-size:12px;cursor:pointer;transition:all 0.15s';
      const pillActiveStyle = 'padding:5px 14px;border-radius:16px;border:1px solid #7c6cf0;background:#7c6cf0;color:#fff;font-size:12px;cursor:pointer;transition:all 0.15s';
      const tabStyle = 'padding:6px 16px;border:none;background:transparent;color:#999;font-size:13px;cursor:pointer;border-bottom:2px solid transparent;transition:all 0.15s';
      const tabActiveStyle = 'padding:6px 16px;border:none;background:transparent;color:#fff;font-size:13px;cursor:pointer;border-bottom:2px solid #7c6cf0;transition:all 0.15s';

      let batView = 'all';

      const allPicksCard = document.createElement('div');
      allPicksCard.className = 'card';
      allPicksCard.style.cssText = 'padding:0;margin-bottom:16px;overflow:hidden';

      const toolbar = document.createElement('div');
      toolbar.style.cssText = 'overflow:hidden';

      const tabRow = document.createElement('div');
      tabRow.className = 'props-toolbar-tabs';
      tabRow.style.cssText = 'display:flex;border-bottom:1px solid rgba(255,255,255,0.08)';
      const viewAllBtn = document.createElement('button');
      viewAllBtn.textContent = 'All Picks';
      const viewWeeklyBtn = document.createElement('button');
      viewWeeklyBtn.textContent = 'Weekly';
      tabRow.appendChild(viewAllBtn);
      tabRow.appendChild(viewWeeklyBtn);
      toolbar.appendChild(tabRow);

      // Filters
      const filterRow = document.createElement('div');
      filterRow.className = 'props-toolbar-filters';
      filterRow.style.cssText = 'display:flex;gap:12px;align-items:center;padding:12px 16px;flex-wrap:wrap';

      const allDates = [...new Set(picks.map(p => p.date))].sort().reverse();
      const dateSel = document.createElement('select');
      dateSel.style.cssText = selStyle;
      dateSel.innerHTML = '<option value="all">All Dates</option>' + allDates.map(d => `<option value="${d}">${d}</option>`).join('');
      const teamSel = document.createElement('select');
      teamSel.style.cssText = selStyle;
      const allTeams = [...new Set(picks.map(p => p.team))].filter(Boolean).sort();
      teamSel.innerHTML = '<option value="all">All Teams</option>' + allTeams.map(t => `<option value="${t}">${t}</option>`).join('');
      const filterLabel = document.createElement('span');
      filterLabel.style.cssText = 'color:#666;font-size:12px;margin-left:auto';
      filterLabel.textContent = `${picks.length} picks`;

      const allWeekStarts = [...new Set(picks.filter(p => p.date).map(p => getWeekStart(p.date)))].sort().reverse();
      const weekSel = document.createElement('select');
      weekSel.style.cssText = selStyle;
      weekSel.innerHTML = '<option value="all">All Weeks</option>' + allWeekStarts.map(ws => {
        const we = getWeekEnd(ws);
        return `<option value="${ws}">${ws} \u2013 ${we}</option>`;
      }).join('');
      const weekFilterLabel = document.createElement('span');
      weekFilterLabel.style.cssText = 'color:#666;font-size:12px;margin-left:auto';

      toolbar.appendChild(filterRow);

      const contentArea = document.createElement('div');
      contentArea.style.cssText = 'padding:0 16px 16px';

      allPicksCard.appendChild(toolbar);
      allPicksCard.appendChild(contentArea);
      el.appendChild(allPicksCard);

      const headers = isBacktest
        ? ['Date','Player','Team','Opp','Proj','Line','Actual','Pick','Result']
        : ['Player','Team','vs','Proj','Line','Pick'];

      function getFilteredPicks() {
        let fp = picks.slice();
        if (dateSel.value !== 'all') fp = fp.filter(p => p.date === dateSel.value);
        if (teamSel.value !== 'all') fp = fp.filter(p => p.team === teamSel.value);
        fp.sort((a, b) => (b.date || '').localeCompare(a.date || ''));
        return fp;
      }

      function buildPropsTable(mProps) {
        const wrap = document.createElement('div');
        wrap.className = 'props-table-wrap';
        const tbl = document.createElement('table');
        tbl.className = 'props-data-table';
        tbl.style.cssText = 'width:100%;border-collapse:collapse;margin-top:8px';
        const hRow = tbl.createTHead().insertRow();
        headers.forEach((h) => {
          const th = document.createElement('th');
          th.textContent = h;
          th.style.cssText = 'padding:4px 4px;text-align:center;border-bottom:1px solid rgba(255,255,255,0.1)';
          if (h === 'Player') th.style.textAlign = 'left';
          hRow.appendChild(th);
        });
        const tbody = tbl.createTBody();
        for (const p of mProps) {
          const row = tbody.insertRow();
          row.style.borderBottom = '1px solid rgba(255,255,255,0.05)';
          const cells = isBacktest ? [
            p.date ? (parseInt(p.date.slice(5,7))+'/'+parseInt(p.date.slice(8))) : '', displayName(p), p.team || '', p.opp || '',
            p.proj!=null?p.proj.toFixed(2):'\u2014',
            p.line != null ? String(p.line) : '\u2014',
            p.actual != null ? String(p.actual) : '\u2014',
            p.pick === 'OVER' ? 'O' : 'U',
            p.result === 'WIN' ? 'W' : p.result === 'LOSS' ? 'L' : '\u2014'
          ] : [
            displayName(p), p.team, p.opp || '',
            p.proj!=null?p.proj.toFixed(2):'\u2014',
            p.line != null ? String(p.line) : '\u2014',
            p.pick === 'OVER' ? 'O' : 'U'
          ];
          cells.forEach((val, i) => {
            const td = row.insertCell();
            td.textContent = val;
            td.style.cssText = 'padding:4px 4px;text-align:center';
            if (isBacktest) {
              if (i === 1) { td.style.textAlign = 'left'; td.style.fontWeight = '600'; }
              if (i === 0) { td.style.color = '#999'; td.style.fontSize = '12px'; }
              if (i === 2 || i === 3) td.style.color = '#999';
              if (i === 4) td.style.color = p.proj > p.line ? 'var(--green)' : p.proj < p.line ? 'var(--red)' : '';
              if (i === 7) { td.style.fontWeight = '700'; td.style.color = p.pick === 'OVER' ? 'var(--green)' : 'var(--red)'; }
              if (i === 8) { td.style.fontWeight = '700'; td.style.color = p.result === 'WIN' ? 'var(--green)' : 'var(--red)'; }
            } else {
              if (i === 0) { td.style.textAlign = 'left'; td.style.fontWeight = '600'; }
              if (i === 1 || i === 2) td.style.color = '#999';
              if (i === 3) td.style.color = p.proj > p.line ? 'var(--green)' : p.proj < p.line ? 'var(--red)' : '';
              if (i === 5) { td.style.fontWeight = '700'; td.style.color = p.pick === 'OVER' ? 'var(--green)' : 'var(--red)'; }
            }
          });
        }
        wrap.appendChild(tbl);
        return wrap;
      }

      const BAT_PAGE_SIZE = 25;
      let allPicksPage = 0;
      let weeklyPage = 0;

      function renderAllPicksView() {
        contentArea.textContent = '';
        const filteredPicks = getFilteredPicks();
        filterLabel.textContent = `Showing ${filteredPicks.length} picks`;

        if (filteredPicks.length === 0) {
          contentArea.appendChild(Object.assign(document.createElement('div'), {className:'no-picks', textContent:'No picks for selected filter.'}));
          return;
        }

        allPicksPage = Math.min(allPicksPage, Math.floor((filteredPicks.length - 1) / BAT_PAGE_SIZE));
        const totalPages = Math.ceil(filteredPicks.length / BAT_PAGE_SIZE);
        const pageStart = allPicksPage * BAT_PAGE_SIZE;
        const pagePicks = filteredPicks.slice(pageStart, pageStart + BAT_PAGE_SIZE);

        const card = document.createElement('div');
        card.className = 'card-games';

        if (totalPages > 1) {
          const pgBar = document.createElement('div');
          pgBar.style.cssText = 'display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap';
          const pgLabel = document.createElement('span');
          pgLabel.style.cssText = 'color:#999;font-size:12px;flex:1';
          pgLabel.textContent = `${pageStart+1}\u2013${Math.min(pageStart+BAT_PAGE_SIZE, filteredPicks.length)} of ${filteredPicks.length} picks`;
          const prevBtn = document.createElement('button');
          prevBtn.textContent = '\u2190 Prev';
          prevBtn.style.cssText = 'padding:4px 12px;border-radius:6px;border:1px solid rgba(255,255,255,0.15);background:transparent;color:#ccc;font-size:12px;cursor:pointer';
          prevBtn.disabled = allPicksPage === 0;
          prevBtn.style.opacity = allPicksPage === 0 ? '0.3' : '1';
          const nextBtn = document.createElement('button');
          nextBtn.textContent = 'Next \u2192';
          nextBtn.style.cssText = 'padding:4px 12px;border-radius:6px;border:1px solid rgba(255,255,255,0.15);background:transparent;color:#ccc;font-size:12px;cursor:pointer';
          nextBtn.disabled = allPicksPage >= totalPages - 1;
          nextBtn.style.opacity = allPicksPage >= totalPages - 1 ? '0.3' : '1';
          prevBtn.onclick = () => { allPicksPage--; renderAllPicksView(); window.scrollTo(0,0); };
          nextBtn.onclick = () => { allPicksPage++; renderAllPicksView(); window.scrollTo(0,0); };
          pgBar.appendChild(pgLabel);
          pgBar.appendChild(prevBtn);
          pgBar.appendChild(document.createTextNode(`Page ${allPicksPage+1} / ${totalPages}`));
          pgBar.appendChild(nextBtn);
          card.appendChild(pgBar);
        }

        card.appendChild(buildPropsTable(pagePicks));
        contentArea.appendChild(card);
      }

      function renderWeeklyView() {
        contentArea.textContent = '';
        let fp = picks.slice();
        if (weekSel.value !== 'all') fp = fp.filter(p => p.date && getWeekStart(p.date) === weekSel.value);

        const weekMap = {};
        for (const p of fp) {
          if (!p.date) continue;
          const ws = getWeekStart(p.date);
          if (!weekMap[ws]) weekMap[ws] = [];
          weekMap[ws].push(p);
        }
        const sortedWeeks = Object.keys(weekMap).sort().reverse();
        weekFilterLabel.textContent = `${fp.length} picks across ${sortedWeeks.length} week${sortedWeeks.length !== 1 ? 's' : ''}`;

        if (sortedWeeks.length === 0) {
          contentArea.appendChild(Object.assign(document.createElement('div'), {className:'no-picks', textContent:'No picks for selected filter.'}));
          return;
        }

        // Weekly summary
        const summCard = document.createElement('div');
        summCard.className = 'card-games';
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
        for (const ws of [...sortedWeeks].reverse()) {
          const wPicks = weekMap[ws];
          const w = wPicks.filter(p => p.result === 'WIN').length;
          const l = wPicks.filter(p => p.result === 'LOSS').length;
          const u = calcMLBPropsUnits(wPicks);
          const pct = (w + l) > 0 ? (w / (w + l) * 100).toFixed(1) : '\u2014';
          totW += w; totL += l;
          const sr = sb.insertRow();
          const we = getWeekEnd(ws);
          [`${ws} \u2013 ${we}`, String(wPicks.length), String(w), String(l),
           (w+l>0?pct+'%':'\u2014'), (w+l>0?(u>=0?'+':'')+u.toFixed(2)+'u':'\u2014')].forEach((v, i) => {
            const td = sr.insertCell();
            td.textContent = v;
            td.style.padding = '6px 10px';
            td.style.textAlign = i === 0 ? 'left' : 'right';
            if (i === 5 && w+l > 0) td.style.color = u >= 0 ? 'var(--green)' : 'var(--red)';
          });
        }
        const totU = calcMLBPropsUnits(fp);
        const tr = sb.insertRow();
        tr.style.borderTop = '2px solid rgba(255,255,255,0.2)';
        tr.style.fontWeight = '700';
        ['TOTAL', String(fp.length), String(totW), String(totL),
         (totW+totL>0?(totW/(totW+totL)*100).toFixed(1)+'%':'\u2014'),
         (totU>=0?'+':'')+totU.toFixed(2)+'u'].forEach((v,i) => {
          const td = tr.insertCell();
          td.textContent = v;
          td.style.padding = '6px 10px';
          td.style.textAlign = i === 0 ? 'left' : 'right';
          if (i === 5) td.style.color = totU >= 0 ? 'var(--green)' : 'var(--red)';
        });
        summCard.appendChild(summTbl);
        contentArea.appendChild(summCard);

        // Per-week cards
        for (const ws of sortedWeeks) {
          const wPicks = weekMap[ws].slice().sort((a, b) => (b.date || '').localeCompare(a.date || ''));
          const we = getWeekEnd(ws);
          const wW = wPicks.filter(p => p.result === 'WIN').length;
          const wL = wPicks.filter(p => p.result === 'LOSS').length;
          const wU = calcMLBPropsUnits(wPicks);
          const wPct = (wW + wL) > 0 ? ` \u2014 ${wW}W-${wL}L (${(wW/(wW+wL)*100).toFixed(1)}%) ${wU>=0?'+':''}${wU.toFixed(2)}u` : ` \u2014 ${wPicks.length} picks`;

          const card = document.createElement('div');
          card.className = 'card-games';
          card.appendChild(Object.assign(document.createElement('div'), {
            className: 'card-title',
            textContent: `Week of ${ws} \u2013 ${we}${wPct}`
          }));
          card.appendChild(buildPropsTable(wPicks));
          contentArea.appendChild(card);
        }
      }

      function refreshView() {
        if (batView === 'weekly') renderWeeklyView();
        else renderAllPicksView();
      }

      function setView(v) {
        batView = v;
        viewAllBtn.style.cssText = v === 'all' ? tabActiveStyle : tabStyle;
        viewWeeklyBtn.style.cssText = v === 'weekly' ? tabActiveStyle : tabStyle;
        filterRow.textContent = '';
        if (v === 'all') {
          filterRow.appendChild(dateSel);
          filterRow.appendChild(teamSel);
          filterRow.appendChild(filterLabel);
        } else {
          filterRow.appendChild(weekSel);
          filterRow.appendChild(weekFilterLabel);
        }
        refreshView();
      }

      viewAllBtn.onclick = () => setView('all');
      viewWeeklyBtn.onclick = () => setView('weekly');
      dateSel.addEventListener('change', renderAllPicksView);
      teamSel.addEventListener('change', renderAllPicksView);
      weekSel.addEventListener('change', renderWeeklyView);

      setView('all');
    }
