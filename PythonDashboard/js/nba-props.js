// NBA Player Props rendering
    // Staking: plus odds risk 1u to win payout, negative odds risk X to win 1u
    function calcPropsUnits(picks) {
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
    function shortName(name) {
      if (!name) return '';
      const parts = name.trim().split(/\s+/);
      if (parts.length < 2) return name;
      return parts[0][0] + '.' + parts[parts.length - 1];
    }
    async function renderNBAProps(dataKey = 'nba-props', title = 'NBA Player Props', opts = {}) {
      const el = document.getElementById('content');
      const data = await fetchData(dataKey);
      if (!data || !data.props || !data.props.length) {
        el.textContent = '';
        const card = document.createElement('div');
        card.className = 'card-games';
        card.appendChild(Object.assign(document.createElement('div'), {className:'card-title', textContent:title}));
        card.appendChild(Object.assign(document.createElement('div'), {className:'no-picks', textContent:'No prop projections available yet. Run the props pipeline to generate projections.'}));
        el.appendChild(card);
        return;
      }

      // Update last-run-info with generated timestamp
      const runEl = document.getElementById('last-run-info');
      if (runEl && data.generated) {
        const d = new Date(data.generated);
        const ct = d.toLocaleString('en-US', { timeZone: 'America/Chicago', month: '2-digit', day: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true });
        runEl.textContent = `Last run (CT) \u2014 ${title}: ${ct}`;
      }

      const marketLabels = {points:'PTS', rebounds:'REB', assists:'AST', threes:"3's", steals:'STL', blocks:'BLK', turnovers:'TO'};
      // Points market is retired for NBA — drop it. WNBA ships points (both
      // directions), so callers can keep it via opts.dropPoints:false.
      const dropPoints = opts.dropPoints !== false;
      const picks = data.props.filter(p => p.pick !== 'PASS' && p.market !== 'pts_rebs_asts' && (dropPoints ? p.market !== 'points' : true));
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



      // ── Yesterday's Recap + Today's Picks ──
      (function renderNBADailyCards() {
        // Use the engine's reported run date (data.date) as the real "today".
        // Fall back to the most recent date with picks if data.date is missing.
        // This avoids labelling stale picks as "today" on no-pick days.
        const allDates = [...new Set(picks.map(p => p.date))].sort();
        const latestPickDate = allDates[allDates.length - 1] || '';
        const todayStr = data.date || latestPickDate;
        const noPicksToday = todayStr !== latestPickDate;
        const yest = new Date(todayStr + 'T12:00:00');
        yest.setDate(yest.getDate() - 1);
        const yesterdayStr = yest.toISOString().slice(0, 10);

        const gradedPicks = picks.filter(p => p.result);

        // Market Breakdown (season-long, graded picks only)
        if (gradedPicks.length > 0) {
          const { fGrouped, sortedMarkets } = buildMarketBreakdown(gradedPicks);
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
          let gW = 0, gL = 0;
          for (const market of sortedMarkets) {
            const mPicks = fGrouped[market];
            const w = mPicks.filter(p => p.result === 'WIN').length;
            const l = mPicks.filter(p => p.result === 'LOSS').length;
            const u = calcPropsUnits(mPicks);
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
          const gU = calcPropsUnits(gradedPicks);
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

        // Playoffs (includes play-in tournament 4/14-4/17 + playoffs 4/18+).
        // NBA-only: 2026-04-14 is the NBA play-in date. WNBA's season runs
        // May-Sept, so every WNBA pick falls after it and would be mislabeled
        // "playoffs" — skip this card entirely for the WNBA props tab.
        const recentCutoff = '2026-04-14';
        const isWNBAProps = String(dataKey).startsWith('wnba');
        const recentPicks = isWNBAProps ? [] : gradedPicks.filter(p => p.date && p.date >= recentCutoff);
        if (recentPicks.length > 0) {
          const rW = recentPicks.filter(p => p.result === 'WIN').length;
          const rL = recentPicks.filter(p => p.result === 'LOSS').length;
          const rU = calcPropsUnits(recentPicks);
          const rPct = (rW + rL) > 0 ? (rW / (rW + rL) * 100).toFixed(1) : '0';
          const rROI = (rW + rL) > 0 ? (rU / (rW + rL) * 100).toFixed(1) : '0';
          const rCard = document.createElement('div');
          rCard.className = 'card card-games';
          rCard.style.marginBottom = '16px';
          rCard.appendChild(Object.assign(document.createElement('div'), {className:'card-title', textContent:`Playoffs (${recentCutoff} \u2013 present)`}));
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
          const { fGrouped: rGrouped, sortedMarkets: rMarkets } = buildMarketBreakdown(recentPicks);
          let rTotW = 0, rTotL = 0;
          for (const market of rMarkets) {
            const mPicks = rGrouped[market];
            const w = mPicks.filter(p => p.result === 'WIN').length;
            const l = mPicks.filter(p => p.result === 'LOSS').length;
            const u = calcPropsUnits(mPicks);
            const pct = (w + l) > 0 ? (w / (w + l) * 100).toFixed(1) : 'n/a';
            const roi = (w + l) > 0 ? (u / (w + l) * 100).toFixed(1) : 'n/a';
            rTotW += w; rTotL += l;
            const sr = rb.insertRow();
            [market, String(mPicks.length), String(w), String(l), pct+'%', (u>=0?'+':'')+u.toFixed(2)+'u', (roi>=0?'+':'')+roi+'%'].forEach((v,i) => {
              const td = sr.insertCell();
              td.textContent = v;
              td.style.padding = '6px 10px';
              td.style.textAlign = i === 0 ? 'left' : 'right';
              if (i === 5) td.style.color = u >= 0 ? 'var(--green)' : 'var(--red)';
              if (i === 6) td.style.color = parseFloat(roi) >= 0 ? 'var(--green)' : 'var(--red)';
            });
          }
          const rtr = rb.insertRow();
          rtr.style.borderTop = '2px solid rgba(255,255,255,0.2)';
          rtr.style.fontWeight = '700';
          ['TOTAL', String(recentPicks.length), String(rW), String(rL), rPct+'%', (rU>=0?'+':'')+rU.toFixed(2)+'u', (rROI>=0?'+':'')+rROI+'%'].forEach((v,i) => {
            const td = rtr.insertCell();
            td.textContent = v;
            td.style.padding = '6px 10px';
            td.style.textAlign = i === 0 ? 'left' : 'right';
            if (i === 5) td.style.color = rU >= 0 ? 'var(--green)' : 'var(--red)';
            if (i === 6) td.style.color = parseFloat(rROI) >= 0 ? 'var(--green)' : 'var(--red)';
          });
          rWrap.appendChild(rTbl);
          rCard.appendChild(rWrap);
          el.appendChild(rCard);
        }

        // Yesterday's Recap
        const yesterdayPicks = picks.filter(p => p.date === yesterdayStr && p.result);
        if (yesterdayPicks.length > 0) {
          const yW = yesterdayPicks.filter(p => p.result === 'WIN').length;
          const yL = yesterdayPicks.filter(p => p.result === 'LOSS').length;
          const yU = calcPropsUnits(yesterdayPicks);
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
          ['Name','Team','Opp','Cat','Proj','Line','Edge','Odds','Actual','O/U','W/L'].forEach((h, i) => {
            const th = document.createElement('th');
            th.textContent = h;
            th.style.cssText = 'padding:6px 10px;border-bottom:1px solid rgba(255,255,255,0.1);' + (i === 0 ? 'text-align:left' : 'text-align:center');
            hRow.appendChild(th);
          });
          const tbody = tbl.createTBody();
          const yCatOrder = {points:0, rebounds:1, assists:2, threes:3, steals:4, blocks:5, turnovers:6};
          for (const p of yesterdayPicks.sort((a,b) => (yCatOrder[a.market]??99) - (yCatOrder[b.market]??99) || (b.pCover||0) - (a.pCover||0))) {
            const row = tbody.insertRow();
            row.style.borderBottom = '1px solid rgba(255,255,255,0.05)';
            const yEdge = (p.proj != null && p.line != null) ? +(p.proj - p.line).toFixed(1) : null;
            const yEdgeStr = yEdge != null ? (yEdge > 0 ? '+'+yEdge : String(yEdge)) : '\u2014';
            const yPrice = p.odds != null ? (p.odds > 0 ? '+'+p.odds : String(p.odds)) : '\u2014';
            [shortName(p.player), p.team||'', p.opp||'', marketLabels[p.market]||p.market,
             String(p.proj), p.line!=null?String(p.line):'\u2014', yEdgeStr, yPrice,
             p.actual!=null?String(p.actual):'\u2014',
             p.pick==='OVER'?'O':'U', p.result==='WIN'?'W':'L'].forEach((v, i) => {
              const td = row.insertCell();
              td.textContent = v;
              td.style.cssText = 'padding:4px 4px;text-align:center';
              if (i === 0) { td.style.textAlign = 'left'; td.style.fontWeight = '600'; }
              if (i === 1 || i === 2) td.style.color = '#999';
              if (i === 4) td.style.color = p.proj > p.line ? 'var(--green)' : p.proj < p.line ? 'var(--red)' : '';
              if (i === 6 && yEdge != null) td.style.color = yEdge > 0 ? 'var(--green)' : yEdge < 0 ? 'var(--red)' : '#999';
              if (i === 7) td.style.color = '#999';
              if (i === 9) { td.style.fontWeight = '700'; td.style.color = p.pick === 'OVER' ? 'var(--green)' : 'var(--red)'; }
              if (i === 10) { td.style.fontWeight = '700'; td.style.color = p.result === 'WIN' ? 'var(--green)' : 'var(--red)'; }
            });
          }
          recapCard.appendChild(tbl);
          const tally = document.createElement('div');
          tally.className = 'l10-tally';
          tally.innerHTML = `Props: <b>${yW}W-${yL}L</b> &middot; <span style="color:${uColor}">${yU >= 0 ? '+' : ''}${yU.toFixed(2)}u</span>`;
          recapCard.appendChild(tally);
          el.appendChild(recapCard);
        }

        // Today's Picks (table format matching All Picks)
        const todayPicks = picks.filter(p => p.date === todayStr);
        if (noPicksToday) {
          // Latest run produced 0 picks for the actual today — render an honest
          // empty state so users don't mistake older picks for tonight's slate.
          const emptyCard = document.createElement('div');
          emptyCard.className = 'card card-picks';
          emptyCard.style.marginBottom = '16px';
          emptyCard.appendChild(Object.assign(document.createElement('div'), {
            className: 'card-title',
            textContent: `Today’s Picks (${todayStr})`
          }));
          const msg = document.createElement('div');
          msg.style.cssText = 'padding:14px 4px;color:var(--text-secondary,#9aa3b2);font-size:0.9rem';
          msg.textContent = `No high-confidence picks for ${todayStr}. Most recent picks: ${latestPickDate}.`;
          emptyCard.appendChild(msg);
          el.appendChild(emptyCard);
        }
        if (todayPicks.length > 0) {
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
          let sortMode = 'pcover';
          sortToggle.textContent = 'Sort: pCover';
          titleRow.appendChild(sortToggle);
          todayCard.appendChild(titleRow);
          const tbl = document.createElement('table');
          tbl.className = 'props-data-table';
          tbl.style.cssText = 'width:100%;border-collapse:collapse;margin-top:8px';
          const todayHeaders = ['Name','Team','Opp','Cat','Proj','Line','Edge','Odds','O/U','pCover','Status'];
          const hRow = tbl.createTHead().insertRow();
          todayHeaders.forEach((h, i) => {
            const th = document.createElement('th');
            th.textContent = h;
            th.style.cssText = 'padding:4px 4px;text-align:center;border-bottom:1px solid rgba(255,255,255,0.1)';
            if (h === 'Name') th.style.textAlign = 'left';
            hRow.appendChild(th);
          });
          let tbody = tbl.createTBody();
          const catOrder = {points:0, rebounds:1, assists:2, threes:3, steals:4, blocks:5, turnovers:6};
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
              const tEdgeStr = tEdge != null ? (tEdge > 0 ? '+'+tEdge : String(tEdge)) : '—';
              const tPrice = p.odds != null ? (p.odds > 0 ? '+'+p.odds : String(p.odds)) : '\u2014';
              const gt = gameTimesToday[p.team] || gameTimesToday[p.opp] || '';
              const started = gt ? (new Date(gt).getTime() <= Date.now()) : false;
              const cells = [
                shortName(p.player), p.team || '', p.opp || '',
                marketLabels[p.market] || p.market,
                String(p.proj),
                p.line != null ? String(p.line) : '\u2014',
                tEdgeStr, tPrice,
                p.pick === 'OVER' ? 'O' : 'U',
                p.pCover != null ? (p.pCover * 100).toFixed(1) + '%' : '—',
                started ? '\u{1F552}' : ''
              ];
              cells.forEach((val, i) => {
                const td = row.insertCell();
                td.textContent = val;
                td.style.cssText = 'padding:4px 4px;text-align:center';
                if (i === 0) { td.style.textAlign = 'left'; td.style.fontWeight = '600'; }
                if (i === 1 || i === 2) td.style.color = '#999';
                if (i === 4) td.style.color = p.proj > p.line ? 'var(--green)' : p.proj < p.line ? 'var(--red)' : '';
                if (i === 6 && tEdge != null) td.style.color = tEdge > 0 ? 'var(--green)' : tEdge < 0 ? 'var(--red)' : '#999';
                if (i === 7) td.style.color = '#999';
                if (i === 8) { td.style.fontWeight = '700'; td.style.color = p.pick === 'OVER' ? 'var(--green)' : 'var(--red)'; }
                if (i === 9 && p.pCover != null) { td.style.fontWeight = '600'; td.style.color = p.pCover >= 0.65 ? 'var(--green)' : (p.pCover < 0.60 ? '#999' : ''); }
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
          todayCard.appendChild(tbl);
          el.appendChild(todayCard);
        }

        // ── Tomorrow's Potential Picks (look-ahead) ──
        // Driven by data.tomorrowPicks emitted by run_daily.py Stage 8b.
        // Hidden when the field is absent or empty (default state).
        try {
          const tomPicks = Array.isArray(data.tomorrowPicks) ? data.tomorrowPicks.slice() : [];
          const tomDate = data.tomorrowDate || (tomPicks[0] && tomPicks[0].date) || '';
          if (tomPicks.length > 0) {
            const tomCard = document.createElement('div');
            tomCard.className = 'card card-picks';
            tomCard.style.marginBottom = '16px';
            tomCard.appendChild(Object.assign(document.createElement('div'), {
              className: 'card-title',
              textContent: `Tomorrow’s Potential Picks${tomDate ? ' (' + tomDate + ')' : ''}`,
            }));
            const note = document.createElement('div');
            note.style.cssText = 'padding:4px 4px 8px;color:var(--text-secondary,#9aa3b2);font-size:0.75rem;font-style:italic';
            note.textContent = 'Look-ahead based on lines posted early. Injury news, lineups, and final lines may shift these.';
            tomCard.appendChild(note);
            const tomTbl = document.createElement('table');
            tomTbl.className = 'props-data-table';
            tomTbl.style.cssText = 'width:100%;border-collapse:collapse;margin-top:8px';
            const tomHeaders = ['Name','Team','Opp','Cat','Proj','Line','Edge','Odds','O/U','pCover'];
            const tomHRow = tomTbl.createTHead().insertRow();
            tomHeaders.forEach((h) => {
              const th = document.createElement('th');
              th.textContent = h;
              th.style.cssText = 'padding:4px 4px;text-align:center;border-bottom:1px solid rgba(255,255,255,0.1)';
              if (h === 'Name') th.style.textAlign = 'left';
              tomHRow.appendChild(th);
            });
            const tomBody = tomTbl.createTBody();
            const tomCatOrder = {points:0, rebounds:1, assists:2, threes:3, steals:4, blocks:5, turnovers:6};
            tomPicks.sort((a,b) =>
              (tomCatOrder[a.market]??99) - (tomCatOrder[b.market]??99)
              || (b.pCover||0) - (a.pCover||0)
            );
            for (const p of tomPicks) {
              const row = tomBody.insertRow();
              row.style.borderBottom = '1px solid rgba(255,255,255,0.05)';
              const tEdge = (p.proj != null && p.line != null) ? +(p.proj - p.line).toFixed(1) : null;
              const tEdgeStr = tEdge != null ? (tEdge > 0 ? '+'+tEdge : String(tEdge)) : '—';
              const tPrice = p.odds != null ? (p.odds > 0 ? '+'+p.odds : String(p.odds)) : '—';
              const cells = [
                shortName(p.player), p.team || '', p.opp || '',
                marketLabels[p.market] || p.market,
                p.proj != null ? String(p.proj) : '—',
                p.line != null ? String(p.line) : '—',
                tEdgeStr, tPrice,
                p.pick === 'OVER' ? 'O' : 'U',
                p.pCover != null ? (p.pCover * 100).toFixed(1) + '%' : '—',
              ];
              cells.forEach((val, i) => {
                const td = row.insertCell();
                td.textContent = val;
                td.style.cssText = 'padding:4px 4px;text-align:center';
                if (i === 0) { td.style.textAlign = 'left'; td.style.fontWeight = '600'; }
                if (i === 1 || i === 2) td.style.color = '#999';
                if (i === 4 && p.proj != null && p.line != null) {
                  td.style.color = p.proj > p.line ? 'var(--green)' : p.proj < p.line ? 'var(--red)' : '';
                }
                if (i === 6 && tEdge != null) td.style.color = tEdge > 0 ? 'var(--green)' : tEdge < 0 ? 'var(--red)' : '#999';
                if (i === 7) td.style.color = '#999';
                if (i === 8) { td.style.fontWeight = '700'; td.style.color = p.pick === 'OVER' ? 'var(--green)' : 'var(--red)'; }
                if (i === 9 && p.pCover != null) { td.style.fontWeight = '600'; td.style.color = p.pCover >= 0.65 ? 'var(--green)' : (p.pCover < 0.60 ? '#999' : ''); }
              });
            }
            tomCard.appendChild(tomTbl);
            el.appendChild(tomCard);
          }
        } catch (e) {
          console.warn('[nba-props] tomorrow look-ahead render failed', e);
        }
      })();

      // ── Today's Games Explorer ──
      (function renderGamesSection() {
        const allDates = [...new Set(data.props.map(p => p.date))].sort();
        // Prefer the engine's reported run date so projections still render
        // on no-pick days (otherwise this filters to "latest pick date" which
        // can be yesterday and the today-projections drop off completely).
        const todayDateStr = data.date || allDates[allDates.length - 1] || '';
        const tomorrowDateStr = data.tomorrowDate || '';

        function loadProj(mode) {
          if (mode === 'tomorrow') {
            return (data.tomorrowProjections || data.tomorrowPicks || [])
              .filter(p => p.market !== 'pts_rebs_asts' && p.proj != null && p.line != null);
          }
          return (data.todayProjections || data.props)
            .filter(p => p.date === todayDateStr && p.market !== 'pts_rebs_asts' && p.proj != null && p.line != null);
        }
        const todayProj = loadProj('today');
        const tomorrowProj = loadProj('tomorrow');
        const hasTomorrow = tomorrowProj.length > 0;
        if (todayProj.length === 0 && !hasTomorrow) return;

        let dayMode = todayProj.length === 0 ? 'tomorrow' : 'today';
        let todayAllProj = dayMode === 'today' ? todayProj : tomorrowProj;
        let activeDateStr = dayMode === 'today' ? todayDateStr : tomorrowDateStr;

        function buildGames(arr) {
          const set = new Map();
          for (const p of arr) {
            const key = [p.team, p.opp].sort().join('@');
            if (!set.has(key)) set.set(key, `${p.team} vs ${p.opp}`);
          }
          return [...set.entries()];
        }
        let games = buildGames(todayAllProj);

        const gCard = document.createElement('div');
        gCard.className = 'card';
        gCard.style.cssText = 'padding:0;margin-bottom:16px;overflow:hidden';

        // Title row with inline Day toggle (small segmented, top-right)
        const titleRow = document.createElement('div');
        titleRow.style.cssText = 'display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 16px 8px;border-bottom:1px solid rgba(255,255,255,0.08)';
        const titleEl = Object.assign(document.createElement('div'), {className:'card-title'});
        function updateTitle() {
          const label = dayMode === 'tomorrow' ? 'Tomorrow\u2019s Games' : 'Today\u2019s Games';
          titleEl.textContent = `${label} (${activeDateStr})`;
        }
        updateTitle();
        titleRow.appendChild(titleEl);

        // Inline day toggle \u2014 only rendered when tomorrow data exists
        const dayToggleWrap = document.createElement('div');
        dayToggleWrap.style.cssText = 'display:inline-flex;gap:4px;background:rgba(255,255,255,0.05);padding:3px;border-radius:6px';
        function refreshDayToggle() {
          dayToggleWrap.textContent = '';
          if (!hasTomorrow) { dayToggleWrap.style.display = 'none'; return; }
          dayToggleWrap.style.display = 'inline-flex';
          for (const mode of ['today', 'tomorrow']) {
            const b = document.createElement('button');
            b.textContent = mode === 'today' ? 'Today' : 'Tomorrow';
            const isActive = mode === dayMode;
            const dayHasData = mode === 'today' ? todayProj.length > 0 : tomorrowProj.length > 0;
            b.style.cssText = 'font-size:11px;padding:4px 10px;border:0;border-radius:4px;cursor:'
              + (dayHasData ? 'pointer;' : 'not-allowed;')
              + (isActive
                  ? 'background:rgba(249,115,22,0.35);color:#fff;font-weight:600;'
                  : 'background:transparent;color:' + (dayHasData ? '#aaa;' : '#555;'));
            if (!dayHasData) b.disabled = true;
            else b.onclick = () => {
              if (dayMode === mode) return;
              dayMode = mode;
              todayAllProj = mode === 'today' ? todayProj : tomorrowProj;
              activeDateStr = mode === 'today' ? todayDateStr : tomorrowDateStr;
              games = buildGames(todayAllProj);
              activeGame = 'all'; activePlayer = 'all'; activeMkt = 'all'; currentPage = 0;
              updateTitle();
              refreshDayToggle();
              refreshPills();
              refreshPlayerDropdown();
              renderGameTable();
            };
            dayToggleWrap.appendChild(b);
          }
        }
        titleRow.appendChild(dayToggleWrap);
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

        // Market filter pills
        const mktPills = document.createElement('div');
        mktPills.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap;padding:10px 16px;border-bottom:1px solid rgba(255,255,255,0.08)';
        const mktFilters = ['all','points','rebounds','assists','threes'];
        let activeMkt = 'all';

        // Table container
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
        playerSelect.onchange = () => { activePlayer = playerSelect.value; currentPage = 0; renderGameTable(); };

        const PAGE_SIZE = 25;
        let currentPage = 0;
        // sortCol: 'cat'|'edge'|'cover'|'proj'  sortDir: 1=asc -1=desc
        let sortCol = 'cat';
        let sortDir = 1;

        const catOrd = {points:0,rebounds:1,assists:2,threes:3,steals:4,blocks:5,turnovers:6};

        function sortRows(rows) {
          return [...rows].sort((a, b) => {
            let v;
            if (sortCol === 'cat') {
              v = ((catOrd[a.market]??99) - (catOrd[b.market]??99)) ||
                  (((a.proj??0)-(a.line??0)) < ((b.proj??0)-(b.line??0)) ? 1 : ((a.proj??0)-(a.line??0)) > ((b.proj??0)-(b.line??0)) ? -1 : 0);
            } else if (sortCol === 'edge') {
              v = ((b.proj??0)-(b.line??0)) - ((a.proj??0)-(a.line??0));
            } else if (sortCol === 'cover') {
              v = (b.pCover??0) - (a.pCover??0);
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
            const playerMatch = activePlayer === 'all' || p.player === activePlayer;
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
            ['Name', null, true],
            ['Team',   null, false],
            ['Cat',    'cat', false],
            ['Proj',   'proj', false],
            ['Line',   null, false],
            ['Edge',   'edge', false],
            ['%', 'cover', false],
            ['O/U',   null, false],
          ];
          cols.forEach(([label, key, leftAlign], i) => {
            const th = document.createElement('th');
            const isActive = key && sortCol === key;
            const arrow = isActive ? (sortDir === 1 ? ' ↑' : ' ↓') : '';
            th.textContent = label + arrow;
            th.style.cssText = `padding:5px 8px;text-align:${leftAlign?'left':'center'};border-bottom:1px solid rgba(255,255,255,0.1);font-size:12px;color:${isActive?'#fff':'#999'};${key?'cursor:pointer;user-select:none':''}`;
            if (key) th.onclick = () => {
              if (sortCol === key) { sortDir *= -1; } else { sortCol = key; sortDir = key === 'cat' ? 1 : -1; }
              currentPage = 0;
              renderGameTable();
            };
            hRow.appendChild(th);
          });

          const tbody = tbl.createTBody();
          for (const p of pageRows) {
            const row = tbody.insertRow();
            row.style.borderBottom = '1px solid rgba(255,255,255,0.04)';
            const isPick = p.pick && p.pick !== 'PASS';
            if (isPick) row.style.background = 'rgba(124,108,240,0.06)';
            const edge = (p.proj != null && p.line != null) ? +(p.proj - p.line).toFixed(1) : null;
            const edgeStr = edge != null ? (edge > 0 ? '+'+edge : String(edge)) : '—';
            const coverStr = p.pCover != null ? (p.pCover * 100).toFixed(1) + '%' : '—';
            [shortName(p.player), p.team||'', marketLabels[p.market]||p.market,
             p.proj!=null?String(p.proj):'—', p.line!=null?String(p.line):'—',
             edgeStr, coverStr,
             isPick?(p.pick==='OVER'?'O':'U'):'—'
            ].forEach((v,i) => {
              const td = row.insertCell();
              td.textContent = v;
              td.style.cssText = 'padding:5px 8px;text-align:'+(i===0?'left':'center')+';font-size:12px';
              if (i===0) td.style.fontWeight = '600';
              if (i===1) td.style.color = '#999';
              if (i===3 && p.line!=null) td.style.color = p.proj > p.line ? 'var(--green)' : p.proj < p.line ? 'var(--red)' : '';
              if (i===5 && edge!=null) td.style.color = edge > 0 ? 'var(--green)' : edge < 0 ? 'var(--red)' : '#999';
              if (i===6 && p.pCover!=null) td.style.color = p.pCover >= 0.65 ? 'var(--green)' : p.pCover <= 0.45 ? 'var(--red)' : '#ccc';
              if (i===7 && isPick) { td.style.fontWeight='700'; td.style.color = p.pick==='OVER'?'var(--green)':'var(--red)'; }
            });
          }
          tableWrap.appendChild(tbl);

          // Pagination controls (only show when more than one page)
          if (totalPages > 1) {
            const pgRow = document.createElement('div');
            pgRow.style.cssText = 'display:flex;align-items:center;justify-content:center;gap:8px;padding:10px 0 2px';
            const prevBtn = document.createElement('button');
            prevBtn.textContent = '← Prev';
            prevBtn.style.cssText = 'padding:4px 12px;border-radius:6px;border:1px solid rgba(255,255,255,0.12);background:transparent;color:'+(currentPage===0?'#444':'#ccc')+';font-size:12px;cursor:'+(currentPage===0?'default':'pointer');
            prevBtn.disabled = currentPage === 0;
            prevBtn.onclick = () => { currentPage--; renderGameTable(); tableWrap.scrollIntoView({behavior:'smooth',block:'nearest'}); };
            const nextBtn = document.createElement('button');
            nextBtn.textContent = 'Next →';
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
          // Market pills
          mktPills.textContent = '';
          for (const m of mktFilters) {
            const label = m === 'all' ? 'All' : (marketLabels[m] || m);
            const btn = document.createElement('button');
            btn.textContent = label;
            btn.style.cssText = m === activeMkt
              ? 'padding:5px 14px;border-radius:16px;border:1px solid #7c6cf0;background:#7c6cf0;color:#fff;font-size:12px;cursor:pointer'
              : 'padding:5px 14px;border-radius:16px;border:1px solid rgba(255,255,255,0.12);background:transparent;color:#999;font-size:12px;cursor:pointer';
            btn.onclick = () => { activeMkt = m; currentPage = 0; refreshPills(); renderGameTable(); };
            mktPills.appendChild(btn);
          }
        }

        refreshDayToggle();
        refreshPills();
        refreshPlayerDropdown();
        renderGameTable();
        gCard.appendChild(gamePills);
        gCard.appendChild(mktPills);
        gCard.appendChild(playerRow);
        gCard.appendChild(tableWrap);
        el.appendChild(gCard);
      })();

      // ── Player History — drill into one player's full prop log ──
      // Mirrors the MLB "Pitcher History" card: pick a player from the
      // dropdown and see every prop pick the model has made for them (all
      // markets, all dates) with results + a running record. Shared by the
      // NBA and WNBA props tabs (points column included when opts allows it).
      (function renderPlayerHistory() {
        const phDates = [...new Set(picks.map(p => p.date))].sort();
        const phToday = data.date || phDates[phDates.length - 1] || '';
        const pKey = (p) => `${p.player}|${p.team || ''}`;

        // Players with any shipped pick in history.
        const histPlayers = new Map();
        for (const p of picks) {
          const k = pKey(p);
          if (!histPlayers.has(k)) histPlayers.set(k, { key: k, name: p.player, team: p.team || '' });
        }
        if (histPlayers.size === 0) return;
        const todayKeys = new Set(picks.filter(p => p.date === phToday).map(pKey));
        const players = [...histPlayers.values()].sort((a, b) => {
          const ta = todayKeys.has(a.key) ? 0 : 1, tb = todayKeys.has(b.key) ? 0 : 1;
          if (ta !== tb) return ta - tb;
          return String(a.name).localeCompare(String(b.name));
        });

        const card = document.createElement('div');
        card.className = 'card card-games';
        card.style.marginBottom = '16px';
        card.appendChild(Object.assign(document.createElement('div'), {
          className: 'card-title', textContent: 'Player History'
        }));

        const ctrlRow = document.createElement('div');
        ctrlRow.style.cssText = 'display:flex;align-items:center;gap:10px;padding:8px 12px 6px;flex-wrap:wrap';
        const lbl = document.createElement('label');
        lbl.textContent = 'Player:';
        lbl.style.cssText = 'font-size:12px;color:#bbb;font-weight:600';
        const sel = document.createElement('select');
        sel.style.cssText = 'background:rgba(255,255,255,0.05);color:#fff;border:1px solid rgba(255,255,255,0.15);border-radius:4px;padding:6px 10px;font-size:12px;min-width:240px;cursor:pointer';
        let sepAdded = false;
        players.forEach(pl => {
          if (!todayKeys.has(pl.key) && !sepAdded && todayKeys.size > 0) {
            const s = document.createElement('option');
            s.disabled = true; s.textContent = '── All Players ──';
            sel.appendChild(s); sepAdded = true;
          }
          const opt = document.createElement('option');
          opt.value = pl.key;
          opt.textContent = pl.team ? `${pl.name} (${pl.team})` : pl.name;
          sel.appendChild(opt);
        });
        const summary = document.createElement('div');
        summary.style.cssText = 'margin-left:auto;font-size:12px;color:#999;font-weight:600';
        ctrlRow.appendChild(lbl); ctrlRow.appendChild(sel); ctrlRow.appendChild(summary);
        card.appendChild(ctrlRow);

        // Market filter row (All + each market present in the data — so it
        // adapts to NBA vs WNBA, which ship different market sets).
        let phMarket = 'all';
        const phOrder = ['points', 'rebounds', 'assists', 'threes', 'steals', 'blocks', 'turnovers'];
        const phMarkets = [...new Set(picks.map(p => p.market))]
          .sort((a, b) => ((phOrder.indexOf(a) + 1) || 99) - ((phOrder.indexOf(b) + 1) || 99));
        const mktRow = document.createElement('div');
        mktRow.style.cssText = 'display:flex;gap:4px;flex-wrap:wrap;padding:0 12px 8px';
        const mktBtns = {};
        function styleMktBtns() {
          Object.entries(mktBtns).forEach(([k, b]) => {
            const on = k === phMarket;
            b.style.background = on ? '#7c6cf0' : 'transparent';
            b.style.color = on ? '#fff' : '#999';
            b.style.borderColor = on ? '#7c6cf0' : 'rgba(255,255,255,0.15)';
          });
        }
        const mkMktBtn = (key, label) => {
          const b = document.createElement('button');
          b.textContent = label;
          b.style.cssText = 'padding:4px 12px;font-size:11px;font-weight:600;border:1px solid rgba(255,255,255,0.15);border-radius:14px;background:transparent;color:#999;cursor:pointer;transition:all 0.15s';
          b.addEventListener('click', () => { phMarket = key; styleMktBtns(); renderPH(sel.value); });
          mktBtns[key] = b; mktRow.appendChild(b);
        };
        mkMktBtn('all', 'All');
        phMarkets.forEach(m => mkMktBtn(m, marketLabels[m] || m));
        styleMktBtns();
        card.appendChild(mktRow);

        const tblWrap = document.createElement('div');
        tblWrap.style.cssText = 'overflow-x:auto';
        card.appendChild(tblWrap);

        function rowUnits(p) {
          if (p.result !== 'WIN' && p.result !== 'LOSS') return null;
          const o = (p.odds != null) ? Number(p.odds) : -110;
          if (p.result === 'WIN') return o > 0 ? o / 100 : 1;
          return o > 0 ? -1 : -Math.abs(o) / 100;
        }

        function renderPH(key) {
          const rows = picks.filter(p => pKey(p) === key && (phMarket === 'all' || p.market === phMarket))
                            .sort((a, b) => String(b.date || '').localeCompare(String(a.date || '')));
          tblWrap.innerHTML = '';
          if (!rows.length) {
            const mlbl = phMarket === 'all' ? '' : (marketLabels[phMarket] || phMarket) + ' ';
            tblWrap.innerHTML = `<div style="padding:14px;color:#888;font-size:12px;font-style:italic">No ${mlbl}picks for this player.</div>`;
            summary.textContent = '';
            return;
          }
          const tbl = document.createElement('table');
          tbl.style.cssText = 'width:100%;border-collapse:collapse';
          const head = tbl.createTHead().insertRow();
          [['Date', 'left'], ['Opp', 'center'], ['Mkt', 'center'], ['Dir', 'center'],
           ['Line', 'right'], ['Proj', 'right'], ['Edge', 'right'], ['pC%', 'right'],
           ['Actual', 'right'], ['Result', 'center'], ['Units', 'right']]
            .forEach(([t, al]) => {
              const th = document.createElement('th');
              th.textContent = t;
              th.style.cssText = `padding:6px 8px;text-align:${al};border-bottom:1px solid rgba(255,255,255,0.1);font-size:11px;color:#999`;
              head.appendChild(th);
            });
          const body = tbl.createTBody();
          let w = 0, l = 0, pu = 0, u = 0;
          for (const p of rows) {
            const ru = rowUnits(p);
            if (p.result === 'WIN') { w++; u += ru; }
            else if (p.result === 'LOSS') { l++; u += ru; }
            else if (p.result === 'PUSH') pu++;
            const dir = String(p.pick || '').toUpperCase();
            const edge = (p.proj != null && p.line != null) ? (p.proj - p.line) : null;
            const resColor = p.result === 'WIN' ? 'var(--green)' : p.result === 'LOSS' ? 'var(--red)' : '#888';
            const dirColor = dir === 'OVER' ? 'var(--green)' : dir === 'UNDER' ? 'var(--red)' : '#999';
            const uColor = ru == null ? '#888' : (ru > 0 ? 'var(--green)' : ru < 0 ? 'var(--red)' : '#ccc');
            const cells = [
              { v: p.date || '—', align: 'left', color: '#ccc' },
              { v: p.opp || '—', align: 'center', color: '#ccc' },
              { v: marketLabels[p.market] || p.market, align: 'center', color: '#bbb', weight: '600' },
              { v: dir === 'OVER' ? 'O' : dir === 'UNDER' ? 'U' : '—', align: 'center', color: dirColor, weight: '600' },
              { v: p.line != null ? String(p.line) : '—', align: 'right' },
              { v: p.proj != null ? Number(p.proj).toFixed(1) : '—', align: 'right' },
              { v: edge != null ? (edge > 0 ? '+' : '') + edge.toFixed(1) : '—', align: 'right', color: edge > 0 ? 'var(--green)' : edge < 0 ? 'var(--red)' : '#999' },
              { v: p.pCover != null ? (p.pCover * 100).toFixed(1) + '%' : '—', align: 'right' },
              { v: p.actual != null ? String(p.actual) : '—', align: 'right' },
              { v: p.result || '—', align: 'center', color: resColor, weight: '700' },
              { v: ru == null ? '—' : (ru >= 0 ? '+' : '') + ru.toFixed(2) + 'u', align: 'right', color: uColor, weight: '600' },
            ];
            const tr = body.insertRow();
            tr.style.borderBottom = '1px solid rgba(255,255,255,0.04)';
            cells.forEach(c => {
              const td = tr.insertCell();
              td.textContent = c.v;
              td.style.cssText = `padding:5px 8px;text-align:${c.align};font-size:12px`;
              if (c.color) td.style.color = c.color;
              if (c.weight) td.style.fontWeight = c.weight;
            });
          }
          tblWrap.appendChild(tbl);
          if (window.fitTableToContainer) window.fitTableToContainer(tbl);
          const gt = w + l;
          const wr = gt ? (100 * w / gt).toFixed(1) : '0.0';
          summary.innerHTML = (gt || pu)
            ? `${w}-${l}${pu ? '-' + pu : ''} &middot; <span style="color:${gt && (w / gt) >= 0.524 ? 'var(--green)' : '#ccc'}">${wr}%</span> &middot; <span style="color:${u >= 0 ? 'var(--green)' : 'var(--red)'}">${u >= 0 ? '+' : ''}${u.toFixed(2)}u</span>`
            : '';
        }

        sel.addEventListener('change', () => renderPH(sel.value));
        el.appendChild(card);
        renderPH(players[0].key);
      })();

      // ── Unified Toolbar ──
      const selStyle = 'padding:6px 12px;border-radius:6px;background:rgba(255,255,255,0.06);color:#fff;border:1px solid rgba(255,255,255,0.1);font-size:13px;outline:none';
      const pillStyle = 'padding:5px 14px;border-radius:16px;border:1px solid rgba(255,255,255,0.12);background:transparent;color:#999;font-size:12px;cursor:pointer;transition:all 0.15s';
      const pillActiveStyle = 'padding:5px 14px;border-radius:16px;border:1px solid #7c6cf0;background:#7c6cf0;color:#fff;font-size:12px;cursor:pointer;transition:all 0.15s';
      const tabStyle = 'padding:6px 16px;border:none;background:transparent;color:#999;font-size:13px;cursor:pointer;border-bottom:2px solid transparent;transition:all 0.15s';
      const tabActiveStyle = 'padding:6px 16px;border:none;background:transparent;color:#fff;font-size:13px;cursor:pointer;border-bottom:2px solid #7c6cf0;transition:all 0.15s';

      let nbaView = 'all'; // 'all' | 'weekly'

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
      viewWeeklyBtn.textContent = 'Weekly';
      tabRow.appendChild(viewAllBtn);
      tabRow.appendChild(viewWeeklyBtn);
      toolbar.appendChild(tabRow);

      // Row 2: Market filter pills
      const nbaButtonOrder = ['points','rebounds','assists','threes','steals','blocks','turnovers'];
      const allMarketKeys = [...new Set(picks.map(p => p.market))].sort((a, b) => {
        const ia = nbaButtonOrder.indexOf(a); const ib = nbaButtonOrder.indexOf(b);
        return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
      });
      const nbaMarketBtnBar = document.createElement('div');
      nbaMarketBtnBar.className = 'props-toolbar-pills';
      nbaMarketBtnBar.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap;padding:12px 16px;border-bottom:1px solid rgba(255,255,255,0.08)';
      let nbaActiveMarket = 'all';

      function renderNBAMarketBtns() {
        nbaMarketBtnBar.textContent = '';
        const allBtn = document.createElement('button');
        allBtn.textContent = 'All';
        allBtn.style.cssText = nbaActiveMarket === 'all' ? pillActiveStyle : pillStyle;
        allBtn.onclick = () => { nbaActiveMarket = 'all'; allPicksPage = 0; weeklyPage = 0; renderNBAMarketBtns(); refreshView(); };
        nbaMarketBtnBar.appendChild(allBtn);
        for (const m of allMarketKeys) {
          const btn = document.createElement('button');
          btn.textContent = marketLabels[m] || m;
          btn.style.cssText = nbaActiveMarket === m ? pillActiveStyle : pillStyle;
          btn.onclick = () => { nbaActiveMarket = m; allPicksPage = 0; weeklyPage = 0; renderNBAMarketBtns(); refreshView(); };
          nbaMarketBtnBar.appendChild(btn);
        }
      }
      toolbar.appendChild(nbaMarketBtnBar);

      // Row 3: Contextual filters
      const filterRow = document.createElement('div');
      filterRow.className = 'props-toolbar-filters';
      filterRow.style.cssText = 'display:flex;gap:12px;align-items:center;padding:12px 16px;flex-wrap:wrap';

      // All Picks filters
      const allDates = [...new Set(picks.map(p => p.date))].sort().reverse();
      const dateSel = document.createElement('select');
      dateSel.style.cssText = selStyle;
      dateSel.innerHTML = '<option value="all">All Dates</option>' + allDates.map(d => `<option value="${d}">${d}</option>`).join('');
      const teamSel = document.createElement('select');
      teamSel.style.cssText = selStyle;
      const allTeams = [...new Set(picks.map(p => p.team))].filter(Boolean).sort();
      teamSel.innerHTML = '<option value="all">All Teams</option>' + allTeams.map(t => `<option value="${t}">${t}</option>`).join('');
      // shared with the NFL props toolbar — see makePlayerSearch
      const playerSearch = makePlayerSearch(() => refreshView());

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
      const weekFilterLabel = document.createElement('span');
      weekFilterLabel.style.cssText = 'color:#666;font-size:12px;margin-left:auto';

      toolbar.appendChild(filterRow);

      const contentArea = document.createElement('div');
      contentArea.style.cssText = 'padding:0 16px 16px';

      allPicksCard.appendChild(toolbar);
      allPicksCard.appendChild(contentArea);
      el.appendChild(allPicksCard);

      const headers = isBacktest
        ? ['Date','Name','Team','Opp','Proj','Line','Edge','%','Actual','O/U','W/L']
        : ['Name','Team','vs','Proj','Line','Edge','%','O/U'];
      const colClasses = isBacktest
        ? ['col-date','col-player','col-team','col-opp','col-proj','col-line','col-edge','col-pcov','col-actual','col-pick','col-result']
        : ['col-player','col-team','col-opp','col-proj','col-line','col-edge','col-pcov','col-pick'];

      function getFilteredPicks() {
        let fp = picks.slice();
        if (dateSel.value !== 'all') fp = fp.filter(p => p.date === dateSel.value);
        if (nbaActiveMarket !== 'all') fp = fp.filter(p => p.market === nbaActiveMarket);
        if (teamSel.value !== 'all') fp = fp.filter(p => p.team === teamSel.value);
        fp = fp.filter(p => playerMatches(p, playerSearch.value));
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
        headers.forEach((h, i) => {
          const th = document.createElement('th');
          th.textContent = h;
          th.className = colClasses[i] || 'col-' + i;
          th.style.cssText = 'padding:4px 4px;text-align:center;border-bottom:1px solid rgba(255,255,255,0.1)';
          if (h === 'Name') th.style.textAlign = 'left';
          hRow.appendChild(th);
        });
        const tbody = tbl.createTBody();
        for (const p of mProps) {
          const row = tbody.insertRow();
          row.style.borderBottom = '1px solid rgba(255,255,255,0.05)';
          const edgeVal = (p.proj != null && p.line != null) ? (p.proj - p.line) : null;
          const edgeStr = edgeVal != null ? (edgeVal > 0 ? '+' : '') + edgeVal.toFixed(1) : '\u2014';
          const pcStr = p.pCover != null ? Math.round(p.pCover * 100) + '%' : '\u2014';
          const cells = isBacktest ? [
            p.date ? (parseInt(p.date.slice(5,7))+'/'+parseInt(p.date.slice(8))) : '', shortName(p.player), p.team || '', p.opp || '',
            String(p.proj),
            p.line != null ? String(p.line) : '\u2014',
            edgeStr,
            pcStr,
            p.actual != null ? String(p.actual) : '\u2014',
            p.pick === 'OVER' ? 'O' : 'U',
            p.result === 'WIN' ? 'W' : p.result === 'LOSS' ? 'L' : '\u2014'
          ] : [
            shortName(p.player), p.team, p.opp || '', String(p.proj),
            p.line != null ? String(p.line) : '\u2014',
            edgeStr,
            pcStr,
            p.pick === 'OVER' ? 'O' : 'U'
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
              if (i === 9) { td.style.fontWeight = '700'; td.style.color = p.pick === 'OVER' ? 'var(--green)' : 'var(--red)'; }
              if (i === 10) { td.style.fontWeight = '700'; td.style.color = p.result === 'WIN' ? 'var(--green)' : 'var(--red)'; }
            } else {
              if (i === 0) { td.style.textAlign = 'left'; td.style.fontWeight = '600'; }
              if (i === 1 || i === 2) td.style.color = '#999';
              if (i === 3) td.style.color = p.proj > p.line ? 'var(--green)' : p.proj < p.line ? 'var(--red)' : '';
              if (i === 5) td.style.color = edgeVal > 0 ? 'var(--green)' : edgeVal < 0 ? 'var(--red)' : '#999';
              if (i === 6) td.style.color = '#aaa';
              if (i === 7) { td.style.fontWeight = '700'; td.style.color = p.pick === 'OVER' ? 'var(--green)' : 'var(--red)'; }
            }
          });
        }
        wrap.appendChild(tbl);
        return wrap;
      }

      function buildMarketBreakdown(filteredPicks) {
        const nbaMarketOrder = ['PTS','REB','AST',"3's",'STL','BLK','TO'];
        const fGrouped = {};
        for (const p of filteredPicks) {
          const ml = marketLabels[p.market] || p.market;
          if (!fGrouped[ml]) fGrouped[ml] = [];
          fGrouped[ml].push(p);
        }
        const sortedMarkets = Object.keys(fGrouped).sort((a, b) => {
          const ia = nbaMarketOrder.indexOf(a); const ib = nbaMarketOrder.indexOf(b);
          return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
        });
        return { fGrouped, sortedMarkets };
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

        const { fGrouped, sortedMarkets } = buildMarketBreakdown(filteredPicks);


        // Single market selected → paginated table for that market
        if (nbaActiveMarket !== 'all') {
          allPicksPage = Math.min(allPicksPage, Math.floor(Math.max(0, filteredPicks.length - 1) / PAGE_SIZE));
          const totalPages = Math.ceil(filteredPicks.length / PAGE_SIZE);
          const pageStart = allPicksPage * PAGE_SIZE;
          const pagePicks = filteredPicks.slice(pageStart, pageStart + PAGE_SIZE);

          const card = document.createElement('div');
          card.className = 'card-games';
          const mW = filteredPicks.filter(p => p.result === 'WIN').length;
          const mL = filteredPicks.filter(p => p.result === 'LOSS').length;
          const mU = calcPropsUnits(filteredPicks);
          const titleSuffix = isBacktest ? ` (${mW}W-${mL}L ${mU>=0?'+':''}${mU.toFixed(2)}u)` : '';
          card.appendChild(Object.assign(document.createElement('div'), {className:'card-title', textContent:(marketLabels[nbaActiveMarket]||nbaActiveMarket)+titleSuffix}));

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

        // All markets → single paginated table
        allPicksPage = Math.min(allPicksPage, Math.floor((filteredPicks.length - 1) / PAGE_SIZE));
        const totalPages = Math.ceil(filteredPicks.length / PAGE_SIZE);
        const pageStart = allPicksPage * PAGE_SIZE;
        const pagePicks = filteredPicks.slice(pageStart, pageStart + PAGE_SIZE);

        const card = document.createElement('div');
        card.className = 'card-games';

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
          ? ['Date','Name','Team','Opp','Cat','Proj','Line','Edge','%','Actual','O/U','W/L']
          : ['Name','Team','vs','Cat','Proj','Line','Edge','%','O/U'];
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
          const edgeVal = (p.proj != null && p.line != null) ? (p.proj - p.line) : null;
          const edgeStr = edgeVal != null ? (edgeVal > 0 ? '+' : '') + edgeVal.toFixed(1) : '\u2014';
          const pcStr = p.pCover != null ? Math.round(p.pCover * 100) + '%' : '\u2014';
          const cells = isBacktest ? [
            p.date?(parseInt(p.date.slice(5,7))+'/'+parseInt(p.date.slice(8))):'', shortName(p.player), p.team||'', p.opp||'', ml,
            String(p.proj), p.line!=null?String(p.line):'\u2014',
            edgeStr,
            pcStr,
            p.actual!=null?String(p.actual):'\u2014',
            p.pick==='OVER'?'O':'U',
            p.result==='WIN'?'W':p.result==='LOSS'?'L':'\u2014'
          ] : [
            shortName(p.player), p.team, p.opp||'', ml, String(p.proj),
            p.line!=null?String(p.line):'\u2014',
            edgeStr,
            pcStr,
            p.pick==='OVER'?'O':'U'
          ];
          cells.forEach((val, i) => {
            const td = row.insertCell();
            td.textContent = val;
            td.style.cssText = 'padding:4px 4px;text-align:center;font-size:13px';
            if (isBacktest) {
              if (i===1) { td.style.textAlign='left'; td.style.fontWeight='600'; }
              if (i===0) { td.style.color='#999'; td.style.fontSize='11px'; }
              if (i===2||i===3) td.style.color='#999';
              if (i===4) { td.style.color='#aaa'; td.style.fontSize='11px'; }
              if (i===5) td.style.color=p.proj>p.line?'var(--green)':p.proj<p.line?'var(--red)':'';
              if (i===7) td.style.color = edgeVal > 0 ? 'var(--green)' : edgeVal < 0 ? 'var(--red)' : '#999';
              if (i===8) td.style.color='#aaa';
              if (i===10) { td.style.fontWeight='700'; td.style.color=p.pick==='OVER'?'var(--green)':'var(--red)'; }
              if (i===11) { td.style.fontWeight='700'; td.style.color=p.result==='WIN'?'var(--green)':'var(--red)'; }
            } else {
              if (i===0) { td.style.textAlign='left'; td.style.fontWeight='600'; }
              if (i===1||i===2) td.style.color='#999';
              if (i===3) { td.style.color='#aaa'; td.style.fontSize='11px'; }
              if (i===4) td.style.color=p.proj>p.line?'var(--green)':p.proj<p.line?'var(--red)':'';
              if (i===6) td.style.color = edgeVal > 0 ? 'var(--green)' : edgeVal < 0 ? 'var(--red)' : '#999';
              if (i===7) td.style.color='#aaa';
              if (i===8) { td.style.fontWeight='700'; td.style.color=p.pick==='OVER'?'var(--green)':'var(--red)'; }
            }
          });
        }
        card.appendChild(tbl);
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
        let fp = picks.slice();
        if (nbaActiveMarket !== 'all') fp = fp.filter(p => p.market === nbaActiveMarket);
        if (weekSel.value !== 'all') fp = fp.filter(p => p.date && getWeekStart(p.date) === weekSel.value);
        fp = fp.filter(p => playerMatches(p, playerSearch.value));

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
          const u = calcPropsUnits(wPicks);
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
        const totU = calcPropsUnits(fp);
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

        // Per-week pick cards — paginated (WEEKS_PER_PAGE per page)
        const totalWeekPages = Math.ceil(sortedWeeks.length / WEEKS_PER_PAGE);
        weeklyPage = Math.min(weeklyPage, Math.max(0, totalWeekPages - 1));
        const weekPageStart = weeklyPage * WEEKS_PER_PAGE;
        const visibleWeeks = weekSel.value !== 'all'
          ? sortedWeeks  // specific week selected → show it (no pagination)
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
          const wU = calcPropsUnits(wPicks);
          const wPct = (wW + wL) > 0 ? ` \u2014 ${wW}W-${wL}L (${(wW/(wW+wL)*100).toFixed(1)}%) ${wU>=0?'+':''}${wU.toFixed(2)}u` : ` \u2014 ${wPicks.length} picks`;

          const card = document.createElement('div');
          card.className = 'card-games';
          card.appendChild(Object.assign(document.createElement('div'), {
            className: 'card-title',
            textContent: `Week of ${ws} \u2013 ${we}${wPct}`
          }));

          // Mini market breakdown for this week
          const { fGrouped: wGrouped, sortedMarkets: wMarkets } = buildMarketBreakdown(wPicks);
          if (wMarkets.length > 1) {
            const mkRow = document.createElement('div');
            mkRow.style.cssText = 'display:flex;gap:16px;flex-wrap:wrap;margin:8px 0 12px;font-size:12px;color:#999';
            for (const mk of wMarkets) {
              const mp = wGrouped[mk];
              const mw = mp.filter(p => p.result === 'WIN').length;
              const ml2 = mp.length - mw;
              const mu = calcPropsUnits(mp);
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
        if (nbaView === 'weekly') renderWeeklyView();
        else renderAllPicksView();
      }

      function setView(v) {
        nbaView = v;
        viewAllBtn.style.cssText = v === 'all' ? tabActiveStyle : tabStyle;
        viewWeeklyBtn.style.cssText = v === 'weekly' ? tabActiveStyle : tabStyle;
        // Swap filter row contents
        filterRow.textContent = '';
        if (v === 'all') {
          filterRow.appendChild(dateSel);
          filterRow.appendChild(teamSel);
          filterRow.appendChild(playerSearch);
          filterRow.appendChild(filterLabel);
        } else {
          filterRow.appendChild(weekSel);
          filterRow.appendChild(playerSearch);
          filterRow.appendChild(weekFilterLabel);
        }
        refreshView();
      }

      viewAllBtn.onclick = () => setView('all');
      viewWeeklyBtn.onclick = () => setView('weekly');
      dateSel.addEventListener('change', renderAllPicksView);
      teamSel.addEventListener('change', renderAllPicksView);
      weekSel.addEventListener('change', renderWeeklyView);

      renderNBAMarketBtns();
      setView('all');
    }
