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

      // ── Yesterday's Recap + Today's Picks ──
      (function renderMLBDailyCards() {
        const allDates = [...new Set(picks.map(p => p.date))].sort();
        const latestPickDate = allDates[allDates.length - 1] || '';
        const todayStr = latestPickDate;
        const yest = new Date(latestPickDate + 'T12:00:00');
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
        const leanUnderGradedLow  = leanUnderGraded.filter(p => (p.pCover || 0) < 0.685);
        const leanUnderGradedHigh = leanUnderGraded.filter(p => (p.pCover || 0) >= 0.685);
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
          appendLeanRow(mb, leanOverGraded, 'Lean O .65-.70');
          appendLeanRow(mb, leanUnderGradedLow,  'Lean U .60-.65');
          appendLeanRow(mb, leanUnderGradedHigh, 'Lean U .65-.70');
          mbWrap.appendChild(mbTbl);
          mbCard.appendChild(mbWrap);
          el.appendChild(mbCard);
        }

        // Recent Record cutoff = the date the latest model tuning shipped.
        // Last tuning: 2026-05-13 — BF=0.95, VAR=1.15, kalmanBlend=0.0,
        // BF_CAP=23, pick threshold=0.72 (was 0.70), lean band 0.65-0.72.
        // Consolidated Kalman blend into a single knob (was compounded
        // 0.6×0.5 ≈ 0.3 effective). 4D walk-forward sweep: 375 picks/season,
        // 72.8% WR, +30.5% ROI / +15.2% recent.
        const recentCutoff = '2026-05-13';
        const recentPicks = gradedPicks.filter(p => p.date && p.date >= recentCutoff);
        if (recentPicks.length > 0) {
          const rW = recentPicks.filter(p => p.result === 'WIN').length;
          const rL = recentPicks.filter(p => p.result === 'LOSS').length;
          const rU = calcMLBPropsUnits(recentPicks);
          const rPct = (rW + rL) > 0 ? (rW / (rW + rL) * 100).toFixed(1) : '0';
          const rROI = (rW + rL) > 0 ? (rU / (rW + rL) * 100).toFixed(1) : '0';
          const rCard = document.createElement('div');
          rCard.className = 'card card-games';
          rCard.style.marginBottom = '16px';
          rCard.appendChild(Object.assign(document.createElement('div'), {className:'card-title', textContent:`Recent Record (${recentCutoff} \u2013 present)`}));
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
          const recentOverLeans      = leanOverGraded.filter(p => p.date && p.date >= recentCutoff);
          const recentUnderLeansLow  = leanUnderGradedLow.filter(p => p.date && p.date >= recentCutoff);
          const recentUnderLeansHigh = leanUnderGradedHigh.filter(p => p.date && p.date >= recentCutoff);
          appendLeanRow(rb, recentOverLeans,      'Lean O .65-.70');
          appendLeanRow(rb, recentUnderLeansLow,  'Lean U .60-.65');
          appendLeanRow(rb, recentUnderLeansHigh, 'Lean U .65-.70');
          rWrap.appendChild(rTbl);
          rCard.appendChild(rWrap);
          el.appendChild(rCard);
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

          // Yesterday's Leans (UNDER 0.60-0.70 watchlist) — full table beneath picks
          const yLeans = leanGraded.filter(p => p.date === yesterdayStr);
          if (yLeans.length > 0) {
            const yLW = yLeans.filter(p => p.result === 'WIN').length;
            const yLL = yLeans.filter(p => p.result === 'LOSS').length;
            const yLU = calcMLBPropsUnits(yLeans);
            const yLColor = yLU >= 0 ? 'var(--green)' : 'var(--red)';
            // Header: just the title + count (matches Today's Picks lean header style)
            const leanHeader = document.createElement('div');
            leanHeader.style.cssText = 'margin-top:14px;padding-top:8px;border-top:1px dashed rgba(244,180,0,0.4);font-size:12px;color:#f4b400;font-weight:600';
            leanHeader.textContent = `Leans — U .60-.70 + O .65-.70 (${yLeans.length})`;
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
          const BASELINE = {
            cutoff: '2026-05-10',
            picks: {
              total:    { w: 77, l: 66, u: -1.48 },
              weekly:   { w: 16, l: 12, u:  0.22 },  // 5/4–5/10
              yesterday:{ w:  1, l:  2, u: -2.12 },  // 5/10
            },
            leans: {
              total:    { w: 24, l: 13, u:  7.52 },
              weekly:   { w: 24, l: 13, u:  7.52 },  // 5/4–5/10
              yesterday:{ w:  2, l:  2, u: -0.66 },  // 5/10
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
          // show baseline weekly. Otherwise compute from new (post-cutoff) data.
          let wPicksTally, wLeansTally;
          if (weeklyEndStr <= BASELINE.cutoff) {
            wPicksTally = BASELINE.picks.weekly;
            wLeansTally = BASELINE.leans.weekly;
          } else {
            const inWeek = (d) => d && d >= weeklyStartStr && d <= weeklyEndStr;
            // Only count post-cutoff entries; pre-cutoff weekly contribution
            // is implicit in baseline (but baseline weekly only covers ONE
            // specific past week — if weeklyEnd > cutoff, that's a new
            // window and baseline-weekly doesn't apply).
            wPicksTally = combine({w:0,l:0,u:0}, newPicks.filter(p => inWeek(p.date)));
            wLeansTally = combine({w:0,l:0,u:0}, newLeans.filter(p => inWeek(p.date)));
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

            // Annotation: compute fresh from bucket diff only while UNCONFIRMED.
            // Once confirmed, freeze whatever annotation was already attached.
            // Any annotation that's ever been shown sticks (so an "(upgraded
            // from lean)" tag from a 9am render is still on the row at 11pm).
            let annotation = prev.annotation || '';
            if (!statusConfirmed) {
              const fresh = _annotationForDiff(prev.bucket, bucket);
              if (fresh) annotation = fresh;
            }

            _currentState[key] = {
              bucket,
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
            return `* ${name} ${dir}${p.line}${suffix} ${conf}${annotation}`;
          }
          const _picksBlock = _todayPicks.length
            ? `\nToday’s Picks (${todayStr})\n\n` +
              _todayPicks.map(p => _fmtRow(p, 'pick')).join('\n') + '\n'
            : '';
          const _leansBlock = _todayLeans.length
            ? `\nToday’s Leans (${todayStr})\n\n` +
              _todayLeans.map(p => _fmtRow(p, 'lean')).join('\n') + '\n'
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
            _leansBlock;

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
            leanHeader.textContent = `Leans — U .60-.70 + O .65-.70 (${todayLeans.length})`;
            todayCard.appendChild(leanHeader);
            todayCard.appendChild(lTbl);
            fitMLBTableToContainer(lTbl);
          }
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
            ['Outs',   null, false],
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
            // Engine emits decimal IP; display as total outs (decimal \u00d7 3).
            // 1 IP = 3 outs, so 5.67 decimal = ~17 outs.
            const outsStr  = p.proj_ip != null ? String(Math.round(p.proj_ip * 3)) : '\u2014';
            const bfStr    = p.proj_bf != null ? String(Math.round(p.proj_bf)) : '\u2014';
            const pitchStr = p.proj_pc != null ? String(Math.round(p.proj_pc)) : '\u2014';
            [displayName(p), p.team||'',
             outsStr, bfStr, pitchStr,
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
              if (i===2) td.style.color = '#bbb'; // Outs
              if (i===3) td.style.color = '#bbb'; // BF
              if (i===4) td.style.color = '#bbb'; // PC
              if (i===5 && p.line!=null) td.style.color = p.proj > p.line ? 'var(--green)' : p.proj < p.line ? 'var(--red)' : '';
              if (i===7 && edge!=null) td.style.color = edge > 0 ? 'var(--green)' : edge < 0 ? 'var(--red)' : '#999';
              if (i===8 && p.pCover!=null) td.style.color = p.pCover >= 0.70 ? 'var(--green)' : p.pCover >= 0.65 ? 'var(--yellow)' : p.pCover <= 0.45 ? 'var(--red)' : '#ccc';
              if (i===9 && isPick) { td.style.fontWeight='700'; td.style.color = p.pick==='OVER'?'var(--green)':'var(--red)'; }
              if (i===10) td.style.color = '#999';
              if (i===11) {
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

      // ── Unified Toolbar ──
      const selStyle = 'padding:6px 12px;border-radius:6px;background:rgba(255,255,255,0.06);color:#fff;border:1px solid rgba(255,255,255,0.1);font-size:13px;outline:none';
      const pillStyle = 'padding:5px 14px;border-radius:16px;border:1px solid rgba(255,255,255,0.12);background:transparent;color:#999;font-size:12px;cursor:pointer;transition:all 0.15s';
      const pillActiveStyle = 'padding:5px 14px;border-radius:16px;border:1px solid #7c6cf0;background:#7c6cf0;color:#fff;font-size:12px;cursor:pointer;transition:all 0.15s';
      const tabStyle = 'padding:6px 16px;border:none;background:transparent;color:#999;font-size:13px;cursor:pointer;border-bottom:2px solid transparent;transition:all 0.15s';
      const tabActiveStyle = 'padding:6px 16px;border:none;background:transparent;color:#fff;font-size:13px;cursor:pointer;border-bottom:2px solid #7c6cf0;transition:all 0.15s';

      let mlbView = 'all'; // 'all' | 'weekly' | 'all-lean' | 'weekly-lean' | 'all-combined'

      // Watchlist leans (pick=PASS):
      //   UNDER pCover 0.60-0.70  — calibrated profitable (~70% WR)
      //   OVER  pCover 0.65-0.70  — profitable under current model (~64% WR)
      const leanPicks = (data.props || []).filter(p => {
        if (p.pick !== 'PASS') return false;
        const pc = p.pCover || 0;
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
        const todayStr = latestPickDate;
        const yest = new Date(latestPickDate + 'T12:00:00');
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
