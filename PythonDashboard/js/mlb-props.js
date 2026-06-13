// MLB Pitcher Props rendering

    // Pick threshold — mirrors MARKET_THRESHOLDS["strikeouts"]["high"] in
    // MLBstrikeouts/scripts/defaults.py. pCover >= this => bet (green).
    // 0.60 <= pCover < this => watch tier (yellow). Keep in sync with defaults.py.
    const MLB_PICK_THRESHOLD = 0.67;   // 2026-06-13: raised 0.64 -> 0.67 (green = bet)
    const MLB_WATCH_FLOOR    = 0.60;   // 2026-06-13: watch/yellow band 0.60-0.67

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

      // Scratched/voided pitchers (late scratch, pitcher swap) are treated as
      // if they were never projected — strip them from data.props and
      // data.todayProjections so they vanish from EVERY downstream table, card,
      // record, and stat. The voided rows are stashed in _voidedPicks solely so
      // the Reddit "Downgraded" call-out can still explain a same-day scratch
      // (rather than mislabeling it a model downgrade).
      const _voidedPicks = (data.props || []).filter(p => p.result === 'VOID');
      // todayProjections rows are forward projections and carry NO `result`
      // field, so they can't be filtered on result === 'VOID'. Key the voided
      // pitchers by player|date and strip every matching projection (all
      // markets) so a scratched starter disappears from the today views too.
      const _voidedDayKeys = new Set(_voidedPicks.map(p => `${p.player}|${p.date}`));
      data.props = (data.props || []).filter(p => p.result !== 'VOID');
      if (Array.isArray(data.todayProjections)) {
        data.todayProjections = data.todayProjections.filter(
          p => !_voidedDayKeys.has(`${p.player}|${p.date}`)
        );
      }

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
      // Subscribers for "user picked a game" events fired by Today's Games.
      // Pitcher History and Team History both register here so a single click
      // on a game pill drives both cards. Each subscriber gets {teams:[a,b]}.
      const _gameClickSubs = [];
      // Subscribers for "user toggled the active pitcher within the current
      // matchup" — fired by Pitcher History's per-game pitcher toggle. Team
      // History uses this to flip its View-as side to that pitcher's team.
      const _pitcherToggleSubs = [];
      // Season Market Breakdown card is built early (it needs gradedPicks)
      // but rendered LATE — after Matchup History — so the page flows:
      // top cards → Today's Picks → Today's Games → Pitcher/Team History →
      // Matchup History → Season Market Breakdown (all-history summary).
      let _seasonBreakdownCard = null;
      // Yesterday's Recap is built in the early IIFE but folded into the
      // Today's Picks card (as a Today/Yesterday toggle) instead of rendering
      // separately. The flag lets the Today's Picks IIFE know it exists.
      let _yesterdayRecapCard = null;
      let _yesterdayStrCached = '';
      // Recent Record container is built early but appended late, between
      // Season Market Breakdown and the All-history paginated table.
      let _recentRecordContainer = null;
      // Slots inside the Picks card where _renderMatchupCard injects its
      // matchup-history table + Read narrative — one for Today, one for
      // Yesterday (walk-forward). Lets the matchup content live INSIDE the
      // Picks card body while still being built later in the render flow
      // (matchup builder needs access to graded history that hasn't been
      // assembled when Picks first renders).
      let _todayMatchupSlot = null;
      let _yesterdayMatchupSlot = null;
      // Read Record card: backtest of the TAKE/PASS verdict against actual
      // results. Built late so it has access to renderReadRow's helpers via
      // the shared scope. Hoisted slot lets us append it between Season
      // Market Breakdown and Recent Record.
      let _readRecordCard = null;
      // EV Gate card: backtest of the price-aware EV verdict (evVerdict) vs
      // actual results. Built late (after Read Record) and appended right below
      // it so the two shadow-monitor gates sit together.
      let _evRecordCard = null;
      // MAE Gate card (formerly "Edge Gate") — built inside _renderRedditCard but
      // hoisted here so all three shadow-monitor gates can be appended together
      // at the very bottom in order Read -> MAE -> EV.
      let _maeGateCard = null;

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
        //
        // 2026-05-25 cutover: pick threshold lowered to 0.68 and the lean
        // tier was retired. Everything 0.68+ is now a flat-2u pick; the
        // 0.60-0.68 band lives in the watch tier (not bet). Lean cards,
        // tables, and Reddit blocks are hidden globally — isLean returns
        // false unconditionally. Historical lean tallies (through
        // BASELINE.cutoff = 2026-05-24) are preserved in the baseline
        // constant so the Reddit total still reflects what was posted.
        // Watch tier = pick=PASS row with a would_be_pick (intended side)
        // and pCover in the watch band (0.60-0.68). Used by the matchup
        // history cohort builder so the broader lens picks up watchlist
        // plays in the same direction as today's pick.
        function isLean(p) {
          if (p.pick !== 'PASS') return false;
          if (!p.would_be_pick) return false;
          const pc = p.pCover || 0;
          return pc >= MLB_WATCH_FLOOR && pc < MLB_PICK_THRESHOLD;  // watch tier; pick threshold -> 0.64 (2026-06-08)
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
          // Leans (watch tier) removed from Season Market Breakdown — we
          // don't bet them by default at the 0.68+ threshold.
          mbWrap.appendChild(mbTbl);
          mbCard.appendChild(mbWrap);
          // Deferred: append after Matchup History at the end of render so
          // Season Market Breakdown lives below the per-game cards instead
          // of at the top of the slate.
          _seasonBreakdownCard = mbCard;
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
        // Second toggle: all graded picks vs. only read-widget TAKEs.
        // Mirrors the Reddit-widget tally so users can A/B the model-only
        // record against the read-filtered subset within the same window.
        const recentModeOptions = [
          { label: 'All',  filter: () => true,                       title: 'Recent Record' },
          { label: 'Read', filter: p => p.readVerdict === 'TAKE',    title: 'Recent Read Record' },
          { label: 'EV',   filter: p => p.evVerdict === 'TAKE',      title: 'Recent EV Record' },
        ];
        let recentModeIdx = 0;

        const rCardContainer = document.createElement('div');
        // Deferred — appended after Season Market Breakdown so the page
        // flows: per-game cards → Season Market Breakdown → Recent Record
        // → All-history table.
        _recentRecordContainer = rCardContainer;

        function _buildToggleGroup(options, getIdx, setIdx) {
          const wrap = document.createElement('div');
          wrap.style.cssText = 'display:inline-flex;gap:4px;background:rgba(255,255,255,0.05);padding:3px;border-radius:6px';
          options.forEach((opt, idx) => {
            const b = document.createElement('button');
            b.textContent = opt.label;
            const active = idx === getIdx();
            b.style.cssText = 'font-size:11px;padding:4px 10px;border:0;border-radius:4px;cursor:pointer;'
              + (active
                  ? 'background:rgba(168,85,247,0.35);color:#fff;font-weight:600'
                  : 'background:transparent;color:#aaa');
            b.addEventListener('click', () => {
              if (getIdx() !== idx) {
                setIdx(idx);
                renderRecentRecord();
              }
            });
            wrap.appendChild(b);
          });
          return wrap;
        }
        const buildRecentToggle = () =>
          _buildToggleGroup(recentCutoffOptions, () => recentCutoffIdx, i => { recentCutoffIdx = i; });
        const buildRecentModeToggle = () =>
          _buildToggleGroup(recentModeOptions, () => recentModeIdx, i => { recentModeIdx = i; });

        function renderRecentRecord() {
          rCardContainer.innerHTML = '';
          const opt = recentCutoffOptions[recentCutoffIdx];
          const mode = recentModeOptions[recentModeIdx];
          const recentPicks = gradedPicks.filter(p =>
            p.date && p.date >= opt.start && (!opt.end || p.date <= opt.end) && mode.filter(p)
          );
          const rCard = document.createElement('div');
          rCard.className = 'card card-games';
          rCard.style.marginBottom = '16px';
          const titleRow = document.createElement('div');
          titleRow.className = 'card-title';
          titleRow.style.cssText = 'display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap';
          const titleSpan = document.createElement('span');
          titleSpan.textContent = opt.end
            ? `${mode.title} (${opt.start} to ${opt.end})`
            : `${mode.title} (${opt.start} - present)`;
          titleRow.appendChild(titleSpan);
          const togglesWrap = document.createElement('div');
          togglesWrap.style.cssText = 'display:inline-flex;gap:8px;flex-wrap:wrap';
          togglesWrap.appendChild(buildRecentToggle());
          togglesWrap.appendChild(buildRecentModeToggle());
          titleRow.appendChild(togglesWrap);
          rCard.appendChild(titleRow);

          if (recentPicks.length === 0) {
            const note = document.createElement('div');
            note.style.cssText = 'padding:12px;color:#888;font-style:italic;font-size:13px';
            note.textContent = recentModeIdx === 1
              ? 'No graded Read TAKEs in this window yet.'
              : 'No graded picks in this window yet.';
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
          // Lean rows removed from Recent Record — we don't bet leans at
          // the 0.68+ threshold so the recent-window summary stays
          // picks-only (matches Season Market Breakdown).
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

          // Yesterday's Leans section removed — we don't bet leans at the
          // 0.68+ threshold, so the recap stays picks-only.
          const yLeans = [];
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
          // Deferred: Today's Picks IIFE pulls this card's body into a
          // shared wrapper with a Today/Yesterday toggle.
          _yesterdayRecapCard = recapCard;
          _yesterdayStrCached = yesterdayStr;
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
            // 2026-06-12 cutoff roll to 6/10: 6/9 and 6/10 baked into the
            // AS-POSTED record from the 6/11 Reddit post (graded through 6/10).
            //   Posted record through 6/10:  picks 166-133 +3.92u
            //   Before codefix: 111-91 +0.08u  |  After codefix through 6/10: 55-42 +3.84u
            //   Weekly (current wk 6/8-, through cutoff 6/10): 11-9 -1.75u
            //   Yesterday 6/10: 4-3 -0.24u
            // Weekly = the CURRENT in-progress Mon-Sun week (resets each Monday);
            // this week's Monday is 6/8, so the baseline weekly covers 6/8-6/10.
            // `before_codefix` is fixed history (never changes). `codefix`
            // (= "after codefix") grows with each post-cutoff pick like `total`.
            cutoff: '2026-06-10',
            picks: {
              total:        { w: 166, l: 133, u:  3.92 },
              before_codefix:{ w: 111, l:  91, u:  0.08 },  // static, pre-codefix
              codefix:      { w:  55, l:  42, u:  3.84 },  // after codefix, through 6/10
              weekly:       { w:  11, l:   9, u:  -1.75 },  // current wk through cutoff (6/10)
              yesterday:    { w:   4, l:   3, u:  -0.24 },  // 6/10
            },
            leans: {
              // Leans retired at the 5/25 cutover (none after 5/24), so the
              // post-cutoff weekly/yesterday lean tallies are zero. total kept
              // for vestigial references; leans are hidden from the Reddit copy.
              total:    { w:  60, l: 39, u: 11.30 },
              weekly:   { w:   0, l:  0, u:  0.00 },
              yesterday:{ w:   0, l:  0, u:  0.00 },
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
          // 2026-06-09: the Reddit widget filters to the Read verdict again —
          // only readVerdict==='TAKE' picks count toward the copy + tallies.
          // Full-season backtest: TAKE 329-120 (73.3%) +175.6u vs ALL 332-125
          // (72.6%) +171.5u — the 8 PASS picks went 3-5 (-4.2u), so gating to
          // TAKE gains +4.1u / +0.7pp WR. (Reverses the 2026-05-29 all-picks
          // copy.) The pre-cutoff BASELINE stays as-posted (all-picks era);
          // only post-cutoff picks are TAKE-gated going forward.
          const _readTake = (p) => p.readVerdict === 'TAKE';
          const _postCutoff = (p) => !!p.date && p.date > BASELINE.cutoff;
          // 2026-06-09: reverted the readVerdict==='TAKE' gate — count ALL
          // post-cutoff graded picks again, not just TAKE (matches the
          // as-posted all-picks Reddit record).
          const newPicks = allGradedPicks.filter(p => _postCutoff(p));
          const newLeans = allGradedLeans.filter(_postCutoff);

          // Weekly window logic: the CURRENT in-progress Mon-Sun week, which
          // resets every Monday. curMon = this week's Monday (the window start);
          // the window runs through "today" so a fresh week shows its results
          // from day one (e.g. on a Tuesday the weekly covers Mon-Tue) instead
          // of lagging a full week behind on the last complete week.
          const _today = new Date(yesterdayStr + 'T12:00:00');
          _today.setDate(_today.getDate() + 1);
          const _todayDow = _today.getDay();   // 0=Sun,1=Mon,..6=Sat
          const _curMon = new Date(_today);
          _curMon.setDate(_today.getDate() - ((_todayDow + 6) % 7));  // current week's Monday
          let wStart = new Date(_curMon);        // Monday of the current week
          let wEnd = new Date(_today);           // through today (in-progress)
          const weekLabel = 'Weekly';
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
          let totalPicks = combine(BASELINE.picks.total, newPicks);
          const totalLeans = combine(BASELINE.leans.total, newLeans);
          // CODEFIX = baked since-codefix baseline + everything post-cutoff
          // (grows identically to total).
          const codefixPicks = combine(BASELINE.picks.codefix, newPicks);

          // WEEKLY: if the weekly window is entirely at-or-before cutoff,
          // show baseline weekly. Otherwise compute from new (post-cutoff) data,
          // and if the window straddles the cutoff date itself, fold in
          // BASELINE.weekly so the pre-cutoff portion of the week isn't dropped
          // (BASELINE.weekly already covers everything up through the cutoff day).
          let wPicksTally, wLeansTally;
          if (weeklyEndStr <= BASELINE.cutoff) {
            wPicksTally = BASELINE.picks.weekly;
            wLeansTally = BASELINE.leans.weekly;
          } else {
            const inWeek = (d) => d && d >= weeklyStartStr && d <= weeklyEndStr;
            const cutoffInWeek = BASELINE.cutoff >= weeklyStartStr
                                 && BASELINE.cutoff <= weeklyEndStr;
            const pBase = cutoffInWeek ? BASELINE.picks.weekly : {w:0,l:0,u:0};
            const lBase = cutoffInWeek ? BASELINE.leans.weekly : {w:0,l:0,u:0};
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
            if (p.result === 'VOID') return true;  // pitcher swap, postponement, etc.
            const gs = _gameStatusesR[p.team] || _gameStatusesR[p.opp] || '';
            return _VOID_GS_R.has(gs);
          };
          // Look up the current today's-prop by key so the dropped-line
          // call-out below can read voidReason from the live row.
          const _todayByKey = {};
          // Include the stashed voided rows here (and only here) so a same-day
          // scratch can still be labeled "voided — pitcher swap" below, even
          // though it's been stripped from data.props everywhere else.
          [...(data.props || []), ..._voidedPicks].forEach(p => {
            if (p.date !== todayStr) return;
            const dir = p.pick === 'PASS' ? (p.would_be_pick || 'OVER') : p.pick;
            _todayByKey[`${displayName(p)}|${p.market}|${dir}`] = p;
          });
          const _LOCK_R = new Set(['lineup_confirmed','game_started','final']);
          const _MARKET_SUFFIX = {
            strikeouts: 'k', outs: 'outs', hits_allowed: 'h', game_hits: 'h',
          };
          // Sort each bucket by pCover descending — matches the dashboard
          // tables' default order so the Reddit copy mirrors what's onscreen.
          const _sortByPCover = (arr) => arr.slice().sort(
            (a, b) => (b.pCover || 0) - (a.pCover || 0)
          );
          // 2026-06-09: Read gate reverted — today's Reddit copy lists ALL
          // picks again (not just readVerdict==='TAKE').
          const _isRedditTake = (p) => true;
          const _todayPicks = _sortByPCover(
            picks.filter(p => p.date === todayStr && !_isVoid(p) && _isRedditTake(p))
          );
          // Watch-tier rows are never bet at our 0.68+ threshold, so the
          // Reddit copy doesn't include them. Empty array kept for any
          // downstream code expecting the variable.
          const _todayLeans = _sortByPCover(
            []
              .filter(p =>
                p.date === todayStr && isLean(p) && !_isVoid(p) && _isRedditTake(p)
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
          // Format a Date as e.g. "12:45 PM" in the user's local timezone.
          const _fmtConfirmTime = (d) => {
            try {
              return d.toLocaleTimeString([], {
                hour: 'numeric', minute: '2-digit', timeZone: 'America/Chicago',
              }) + ' CT';
            } catch (_) {
              const h = d.getHours(), m = d.getMinutes();
              const hh = ((h + 11) % 12) + 1;
              return `${hh}:${String(m).padStart(2,'0')} ${h < 12 ? 'AM' : 'PM'} CT`;
            }
          };
          function _resolveEntry(p, bucket) {
            const key = _keyOf(p);
            const prev = _prev[key] || {};
            const isConfirmed = _LOCK_R.has(p.lockState);

            // Status (confirmed/unconfirmed) is sticky: once confirmed, stays
            // confirmed for the rest of the day — even if a later run somehow
            // reverts the lockState.
            const statusConfirmed = prev.status === 'confirmed' || isConfirmed;

            // Prefer the backend-supplied lockedAt (when the lineup actually
            // locked) over a client-side "first seen confirmed" stamp.
            // Falling back to local time + sticky prev keeps backward-compat
            // for entries the backend didn't tag.
            let confirmedAt = prev.confirmedAt || '';
            if (statusConfirmed) {
              if (p.lockedAt) {
                const d = new Date(p.lockedAt);
                if (!isNaN(d)) confirmedAt = _fmtConfirmTime(d);
              }
              if (!confirmedAt) confirmedAt = _fmtConfirmTime(new Date());
            }

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
              confirmedAt,
              team: p.team || prev.team || '',
              opp: p.opp || prev.opp || '',
            };
            return { statusConfirmed, annotation, confirmedAt };
          }

          function _fmtRow(p, bucket) {
            const name = displayName(p);
            const dir = _dirOf(p) === 'OVER' ? 'o' : 'u';
            const suffix = _MARKET_SUFFIX[p.market] || '';
            const { statusConfirmed, annotation, confirmedAt } = _resolveEntry(p, bucket);
            const conf = statusConfirmed ? '**confirmed**' : 'unconfirmed';
            // Projection is appended ONLY when the row is confirmed —
            // unconfirmed projections can shift once the real lineup locks,
            // so withholding the number until confirmation avoids posting a
            // figure we'll have to walk back.
            // Locked-in price for the side we picked. Shows only on
            // confirmed rows alongside the projection (unconfirmed odds can
            // drift before lineup lock — withhold until lock for the same
            // reason proj is withheld).
            const pickedOdds = _dirOf(p) === 'OVER' ? p.over_price : p.under_price;
            const oddsStr = (statusConfirmed && pickedOdds != null)
              ? ` ${pickedOdds > 0 ? '+' : ''}${pickedOdds}`
              : '';
            // pCover shown as a percentage on confirmed rows only — same
            // withhold-until-lock rationale as proj/odds (pCover shifts with
            // the lineup until it confirms).
            const pcStr = (statusConfirmed && p.pCover != null)
              ? ` ${(Number(p.pCover) * 100).toFixed(1)}%`
              : '';
            const projTag = (statusConfirmed && p.proj != null)
              ? ` proj: ${Number(p.proj).toFixed(1)}${oddsStr}${pcStr}${confirmedAt ? ` @ ${confirmedAt}` : ''}`
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

          // Dropped entries: previously-shown picks/leans that no longer
          // appear in today's slate at all. Collected into a separate
          // "Today's Downgraded" section so they don't clutter the active
          // Picks/Leans blocks. Fires for both confirmed and unconfirmed
          // entries — an unconfirmed pick that falls off the slate is still
          // called out as a downgrade rather than vanishing silently.
          // Build the confirmed body text for a live row that dropped to a
          // nonpick *on confirmation*. Mirrors the confirmed branch of
          // _fmtRow so the struck-through line shows the real locked-lineup
          // projection/odds/pCover that caused the downgrade, rather than the
          // stale "unconfirmed" body we last rendered while it was still a pick.
          const _confirmedBodyFor = (p) => {
            const name = displayName(p);
            const dir = _dirOf(p) === 'OVER' ? 'o' : 'u';
            const suffix = _MARKET_SUFFIX[p.market] || '';
            const pickedOdds = _dirOf(p) === 'OVER' ? p.over_price : p.under_price;
            const oddsStr = pickedOdds != null
              ? ` ${pickedOdds > 0 ? '+' : ''}${pickedOdds}` : '';
            const pcStr = p.pCover != null
              ? ` ${(Number(p.pCover) * 100).toFixed(1)}%` : '';
            let confirmedAt = '';
            if (p.lockedAt) {
              const d = new Date(p.lockedAt);
              if (!isNaN(d)) confirmedAt = _fmtConfirmTime(d);
            }
            if (!confirmedAt) confirmedAt = _fmtConfirmTime(new Date());
            const projTag = p.proj != null
              ? ` proj: ${Number(p.proj).toFixed(1)}${oddsStr}${pcStr} @ ${confirmedAt}`
              : '';
            return `${name} ${dir}${p.line}${suffix} **confirmed**${projTag}`;
          };

          const _droppedLines = [];
          for (const [key, prev] of Object.entries(_prev)) {
            if (_currentState[key]) continue;          // still present today
            if (!prev.lineText) continue;               // legacy entry without saved text
            // Direction-flip guard: if no live row matches this exact key but
            // the SAME player+market is live today under a different direction,
            // this snapshot is a stale artifact from an earlier run that bet
            // the other side (e.g. an early run confirmed C.Rodón OVER that a
            // later run corrected to UNDER). The current-direction row already
            // represents this player, so skip the phantom — and by not adding
            // it to _currentState below, it's purged from storage next render.
            if (!_todayByKey[key]) {
              const _pm = key.slice(0, key.lastIndexOf('|'));   // "name|market"
              const _flipped = Object.keys(_todayByKey).some(
                k => k.slice(0, k.lastIndexOf('|')) === _pm
              );
              if (_flipped) continue;
            }
            // If the underlying game was postponed/suspended/cancelled, the
            // entry didn't drop on merit — call it out as a postponement
            // instead of a model-driven nonpick downgrade.
            const _gs = (prev.team && _gameStatusesR[prev.team])
              || (prev.opp && _gameStatusesR[prev.opp])
              || '';
            const wasPostponed = _VOID_GS_R.has(_gs);
            // If the live row is still in today's slate but voided (pitcher
            // swap, late scratch), surface that reason explicitly instead
            // of the generic "downgraded to nonpick".
            const _liveRow = _todayByKey[key];
            const _voidReason = _liveRow && _liveRow.result === 'VOID'
              ? (_liveRow.voidReason || '') : '';
            // A previously-unconfirmed pick that fell off the slate once the
            // real lineup locked dropped *because of* confirmation — show the
            // confirmed projection that caused it, not the stale unconfirmed
            // body + generic "downgraded to nonpick".
            const _droppedOnConfirm = _liveRow && _liveRow.result !== 'VOID'
              && _LOCK_R.has(_liveRow.lockState) && prev.status !== 'confirmed';
            let reason;
            let bodyText = prev.lineText;
            if (wasPostponed) {
              reason = `downgraded — game ${_gs.toLowerCase()}`;
            } else if (_voidReason === 'pitcher_swapped') {
              const note = _liveRow && _liveRow.voidNote ? ` (${_liveRow.voidNote})` : '';
              reason = `voided — pitcher swap${note}`;
            } else if (_droppedOnConfirm) {
              // Show the confirmed projection that caused the drop, but keep
              // the "downgraded to nonpick" reason so the section stays
              // consistent with the other dropped lines.
              bodyText = _confirmedBodyFor(_liveRow);
              reason = 'downgraded to nonpick';
            } else {
              reason = 'downgraded to nonpick';
            }
            _droppedLines.push(reason ? `* ~~${bodyText}~~ ${reason}` : `* ~~${bodyText}~~`);
            // Persist so the strikethrough sticks on subsequent renders even
            // though the underlying entry is gone. Freeze the confirmed body +
            // status when the drop was confirmation-driven so it stays correct
            // even after the live row leaves the slate entirely.
            _currentState[key] = {
              ...prev,
              lineText: bodyText,
              status: _droppedOnConfirm ? 'confirmed' : prev.status,
              droppedToNonPick: !wasPostponed,
              droppedPostponed: wasPostponed,
            };
          }

          const _picksBlock = _pickLines.length
            ? `\nToday’s Picks (${todayStr})\n\n` + _pickLines.join('\n') + '\n'
            : '';
          const _leansBlock = _leanLines.length
            ? `\nToday’s Leans (${todayStr})\n\n` + _leanLines.join('\n') + '\n'
            : '';
          const _droppedBlock = _droppedLines.length
            ? `\nToday's Downgraded (non-picks)\n\n` + _droppedLines.join('\n') + '\n'
            : '';

          // Persist current state for the next render. Stores bucket +
          // sticky status + sticky annotation per entry, resetting only
          // when the date rolls over.
          try {
            localStorage.setItem(_STORE_KEY, JSON.stringify({
              date: todayStr, state: _currentState,
            }));
          } catch (_) {}

          // (The former 5/25 and 5/26 manual pins were removed at the 2026-05-29
          // CSW cutover — those dates are now <= BASELINE.cutoff (5/28) and are
          // baked into BASELINE.picks.* with their actually-posted values. Re-
          // applying the pins here would double-count them.)

          // Reddit copy is picks-only at the 0.68+ threshold — Leans
          // section removed entirely (totals, weekly, yesterday, and the
          // per-row leans block).
          const redditText =
            `Picks will be updated throughout the day as lineups come in.\n\n` +
            `Lines are based on draftkingsORfanduel\n\n` +
            `Picks:\n\n` +
            `* Total: ${fmt(totalPicks)}\n` +
            `* Before codefix: ${fmt(BASELINE.picks.before_codefix)}\n` +
            `* After codefix: ${fmt(codefixPicks)}\n` +
            `* ${weekLabel} (${weekRange}): ${fmt(wPicksTally)}\n` +
            `* Yesterday (${yMD}): ${fmt(yPicksTally)}\n` +
            _picksBlock +
            _droppedBlock +
            `\n\nNot needed but appreaciated : https://buymeacoffee.com/henitals`;

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

          // --- MAE Gate (shadow monitor — NOT live) ---------------------------
          // Watches whether your recent picks are out-predicting the closing line.
          // Named for its signal: trailing model MAE |proj-actual| vs Vegas line
          // MAE |line-actual|. (Formerly "Edge Gate" — renamed to name the
          // mechanism and to disambiguate from the EV Gate.)
          // Signal = trailing 3-day (model |proj-actual| MAE) minus (line MAE) over
          // graded picks (pCover>=0.64). gap<0 => you're sharper than Vegas => bet
          // full card (>=0.64). gap>=0 => your recent picks are TRAILING the line
          // => the gate would tighten to >=0.67 and skip the 0.64-0.67 marginal
          // tier. This card ONLY tracks the counterfactual so you can decide
          // whether to adopt it once enough flip events accumulate. It does not
          // change any picks. Window=3d chosen to ~match a series; leak-free
          // (each date's gap uses only strictly-prior graded picks).
          (function renderMaeGate(){
            const LOWT=0.64, HIGHT=0.67, GWIN=3, GMIN=8;
            const g=(data.props||[]).filter(p =>
              (p.pick==='OVER'||p.pick==='UNDER') &&
              (p.result==='WIN'||p.result==='LOSS') &&
              p.pCover!=null && p.line!=null && p.proj!=null && p.actual!=null &&
              p.pCover>=LOWT);
            if (!g.length) return;
            g.sort((a,b)=>(a.date||'').localeCompare(b.date||''));
            const dts=[...new Set(g.map(p=>p.date))].sort();
            const dadd=(s,n)=>{const x=new Date(s+'T12:00:00');x.setDate(x.getDate()+n);return x.toISOString().slice(0,10);};
            function gap(D){
              const lo=dadd(D,-GWIN);
              const w=g.filter(p=>p.date>=lo && p.date<D);
              if (w.length<GMIN) return null;
              const mm=w.reduce((s,p)=>s+Math.abs(p.proj-p.actual),0)/w.length;
              const lm=w.reduce((s,p)=>s+Math.abs(p.line-p.actual),0)/w.length;
              return mm-lm;
            }
            // historical flips + the marginal picks the gate would have skipped
            const flips=[]; let cumU=0, cumW=0, cumL=0;
            for (const D of dts){
              const ga=gap(D);
              if (ga!=null && ga>=0){
                const dropped=g.filter(p=>p.date===D && p.pCover>=LOWT && p.pCover<HIGHT);
                const u=calcMLBPropsUnits(dropped);
                const w=dropped.filter(p=>p.result==='WIN').length;
                const l=dropped.filter(p=>p.result==='LOSS').length;
                cumU+=u; cumW+=w; cumL+=l;
                flips.push({date:D, gap:ga, dropped, u, w, l});
              }
            }
            // today's gate state (gap from strictly-prior graded picks)
            const tGap = (typeof todayStr!=='undefined' && todayStr) ? gap(todayStr) : gap(dadd(dts[dts.length-1],1));
            const tighten = (tGap!=null && tGap>=0);

            const card=document.createElement('div');
            card.className='card';
            card.style.marginBottom='16px';
            const title=document.createElement('div');
            title.className='card-title';
            title.textContent='MAE Gate — shadow monitor (not live)';
            card.appendChild(title);

            // status banner
            const banner=document.createElement('div');
            const gapTxt=(tGap==null)?'n/a (insufficient recent picks)':(tGap>=0?'+':'')+tGap.toFixed(2)+' K';
            banner.style.cssText='margin-top:10px;padding:10px 12px;border-radius:6px;font-size:13px;font-weight:600;'+
              (tighten?'background:rgba(255,160,0,0.12);color:#ffb000;border:1px solid rgba(255,160,0,0.35)'
                     :'background:rgba(0,200,120,0.10);color:#19c37d;border:1px solid rgba(0,200,120,0.30)');
            banner.textContent = tighten
              ? `⚠ TIGHTEN — recent picks trailing the line (3d gap ${gapTxt}). Gate would bet only ≥0.67 and skip the 0.64–0.67 tier today.`
              : `✓ NORMAL — beating the line (3d gap ${gapTxt}). Gate would bet the full card (≥0.64).`;
            card.appendChild(banner);

            // today's actionable picks the gate would skip (pending — not gradeable yet)
            if (tighten && typeof todayStr!=='undefined' && todayStr){
              const todMarg=(data.props||[]).filter(p=>p.date===todayStr &&
                (p.pick==='OVER'||p.pick==='UNDER') && p.pCover!=null &&
                p.pCover>=LOWT && p.pCover<HIGHT).sort((a,b)=>b.pCover-a.pCover);
              const tw=document.createElement('div');
              tw.style.cssText='margin-top:10px;padding:8px 10px;background:rgba(255,160,0,0.07);border:1px solid rgba(255,160,0,0.25);border-radius:4px;font-size:12px';
              const th=document.createElement('div');
              th.style.cssText='font-weight:600;color:#ffb000;margin-bottom:'+(todMarg.length?'5px':'0');
              th.textContent = todMarg.length
                ? `Today (${todayStr}) — gate would SKIP these ${todMarg.length} marginal pick${todMarg.length===1?'':'s'} (pending):`
                : `Today (${todayStr}) — TIGHTEN, but no 0.64–0.67 picks to skip.`;
              tw.appendChild(th);
              for (const p of todMarg){
                const dir=p.pick==='OVER'?'o':'u';
                const row=document.createElement('div');
                row.style.cssText='color:#ddd;padding:1px 0';
                row.innerHTML=`${p.player} ${dir}${p.line} <span style="color:#888">(pC ${Number(p.pCover).toFixed(2)})</span>`;
                tw.appendChild(row);
              }
              card.appendChild(tw);
            }

            // cumulative shadow result
            const saved = -cumU;  // skipping net-negative dropped picks = saved units
            const summary=document.createElement('div');
            summary.style.cssText='margin-top:10px;font-size:13px;color:#ddd;line-height:1.6';
            summary.innerHTML =
              `<b>Shadow ledger</b> (counterfactual, ${flips.length} flip day${flips.length===1?'':'s'} so far):<br>`+
              `Marginal picks the gate would've skipped went <b>${cumW}-${cumL}</b> (${cumU>=0?'+':''}${cumU.toFixed(2)}u).<br>`+
              `Adopting it would have changed your P/L by <b style="color:${saved>=0?'#19c37d':'#ff5c5c'}">${saved>=0?'+':''}${saved.toFixed(2)}u</b>.`;
            card.appendChild(summary);

            // per-flip-day detail — collapsible: click a date to expand its dropped picks
            if (flips.length){
              const wrap=document.createElement('div');
              wrap.style.cssText='margin-top:12px';
              for (const f of flips.slice().reverse()){
                const det=document.createElement('details');
                det.style.cssText='margin-bottom:6px;padding:6px 10px;background:rgba(255,255,255,0.03);border-radius:4px;font-size:12px';
                const sum=document.createElement('summary');
                sum.style.cssText='cursor:pointer;font-weight:600;color:#cdd;list-style:revert';
                sum.textContent=`${f.date}  ·  3d gap +${f.gap.toFixed(2)}  ·  skipped ${f.w}-${f.l}  ·  saved ${((-f.u)>=0?'+':'')}${(-f.u).toFixed(2)}u`;
                det.appendChild(sum);
                const body=document.createElement('div');
                body.style.cssText='margin-top:6px';
                if (!f.dropped.length){
                  body.innerHTML='<div style="color:#888">(no 0.64–0.67 picks that day — flip had no effect)</div>';
                } else {
                  for (const p of f.dropped){
                    const win=p.result==='WIN';
                    const u=calcMLBPropsUnits([p]);
                    const dir=p.pick==='OVER'?'o':'u';
                    const row=document.createElement('div');
                    row.style.cssText='display:flex;justify-content:space-between;gap:10px;padding:1px 0;color:'+(win?'#19c37d':'#ff5c5c');
                    row.innerHTML=`<span>${p.player} ${dir}${p.line} <span style="color:#888">(pC ${Number(p.pCover).toFixed(2)})</span></span>`+
                                  `<span style="white-space:nowrap">${win?'W':'L'} ${u>=0?'+':''}${u.toFixed(2)}u</span>`;
                    body.appendChild(row);
                  }
                }
                det.appendChild(body);
                wrap.appendChild(det);
              }
              card.appendChild(wrap);
            }

            const note=document.createElement('div');
            note.style.cssText='margin-top:10px;font-size:11px;color:#888;line-height:1.5';
            note.textContent='Monitor only — your actual picks are unchanged. The "Saved u" is the counterfactual P/L of skipping the 0.64–0.67 tier on days the gate flipped. Watch whether the cumulative stays positive across more flip days before adopting — it is currently fit to one regime event.';
            card.appendChild(note);

            // Hoisted — appended at the very bottom with the other two shadow
            // gates (Read -> MAE -> EV) rather than inline here.
            _maeGateCard = card;
          })();
        };

        // --- Matchup History card ---
        // For each of today's actionable picks and leans, look up the
        // model's historical record vs that opponent in that direction
        // (within the Picks+Leans universe — excluding watchlist plays).
        _renderMatchupCard = function renderMatchupCard() {
          // Resolve the betting direction for any row: picks use `pick`,
          // leans store the intended side in `would_be_pick`.
          const _dirOf = (p) => p.pick === 'PASS' ? (p.would_be_pick || null) : p.pick;

          // Pure verdict function — TAKE/PASS decision. Hoisted ABOVE
          // buildMatchupSection so both the matchup builder (live reads)
          // and the backtest below (Read Record card) can share one rule
          // set. Returns 'TAKE' or 'PASS'.
          function readVerdictFor({ dirRec, allRec, pitRec, pitAllRec }) {
            const bktN = dirRec ? dirRec.w + dirRec.l : 0;
            const bktWR = bktN > 0 ? dirRec.w / bktN : null;
            const broadN = allRec ? allRec.w + allRec.l : 0;
            const broadWR = broadN > 0 ? allRec.w / broadN : null;
            const broadU = allRec ? allRec.u : 0;
            // 2026-05-29: loosened to caution-only (see negative gate below).
            // Only the caution cohort carries real loss signal; coin/small/
            // empty/pitcher gates were dropping profitable picks, so removed.
            const caution = bktWR != null && bktN >= 4 && bktWR < 0.45;

            // --- Positive overrides --- (opp OR pitcher dominant)
            const pitN = pitRec ? pitRec.w + pitRec.l : 0;
            const pitWR = pitN > 0 ? pitRec.w / pitN : null;
            const pitAllN = pitAllRec ? pitAllRec.w + pitAllRec.l : 0;
            const pitAllWR = pitAllN > 0 ? pitAllRec.w / pitAllN : null;
            if (bktN >= 4 && bktWR >= 0.75 && dirRec.u >= 2) return 'TAKE';
            if (broadN >= 8 && broadWR >= 0.70 && broadU >= 2) return 'TAKE';
            if (pitN >= 4 && pitWR >= 0.75 && pitRec.u >= 2) return 'TAKE';
            if (pitAllN >= 6 && pitAllWR >= 0.70 && pitAllRec.u >= 2) return 'TAKE';

            // --- Negative gate (PASS) — loosened to caution-only 2026-05-29 ---
            // CSW backfill rule breakdown: caution 6p 3-3 -0.55u (KEEP, net -EV);
            // coin+bWeak 12p 7-5 -0.06u, small/empty+bWeak 5p 5-0 +5.00u,
            // pit_weak 1p 1-0 +1.00u (all dropped profitable/neutral picks).
            // caution-only => 315 TAKE +136.45u vs old 297 +130.51u. Mirrors
            // _verdict() in run_daily.py — keep both in sync.
            if (caution) return 'PASS';
            return 'TAKE';
          }

          // Builder for the matchup-history section (table + Read narrative).
          // Called twice from this function — once for today's picks and
          // once for yesterday's — with `gradedCutoff` set to each day's
          // date so the cohort it draws from is walk-forward safe (only
          // picks resolved BEFORE that day count).
          //
          // Returns a body div (no card-title) or null when there are no
          // rows to show. The Picks card injects the body into its
          // today/yesterday slots so the matchup history lives inside the
          // unified Picks card instead of as a separate card.
          function buildMatchupSection({ picksToShow, leansToShow, gradedCutoff }) {
          // Build TWO maps so the Dir record can be bucket-specific:
          //   byOppDirPicks[opp][OVER|UNDER] — graded actionable picks only
          //   byOppDirLeans[opp][OVER|UNDER] — graded leans only
          //   byOppAll[opp]                  — picks+leans, both directions
          const graded = (data.props || []).filter(p =>
            p.market === 'strikeouts'
            && (p.result === 'WIN' || p.result === 'LOSS')
            && p.opp
            && (p.pick === 'OVER' || p.pick === 'UNDER' || isLean(p))
            && (!gradedCutoff || (p.date || '') < gradedCutoff)
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
          // Same shape, keyed by pitcher (displayName|team) instead of opp.
          // Powers the "P O//U" columns and the pitcher-history Read accent.
          const byPitDirPicks = {};
          const byPitDirLeans = {};
          // Bucket-only (both directions) pitcher rollup — pitcher's "O&&U"
          // record across the season, for the Read narrative.
          const byPitPicks = {};
          const byPitLeans = {};
          // Key helper — must match the dedupe key used by Pitcher History
          // (displayName + team) so a pitcher with a name-format quirk lines
          // up across the two cards.
          const _pitKey = (p) => `${displayName(p)}|${p.team || ''}`;
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
            // Pitcher-direction roll-up across all opponents.
            const pitKey = _pitKey(p);
            const pitMap = isPickRow ? byPitDirPicks : byPitDirLeans;
            if (!pitMap[pitKey]) pitMap[pitKey] = {OVER:{w:0,l:0,u:0}, UNDER:{w:0,l:0,u:0}};
            const pd = pitMap[pitKey][dir];
            if (won) pd.w++; else pd.l++;
            pd.u += u;
            // Pitcher bucket-only (both dirs) — "O&&U" for this pitcher.
            const pitBktMap = isPickRow ? byPitPicks : byPitLeans;
            if (!pitBktMap[pitKey]) pitBktMap[pitKey] = {w:0,l:0,u:0};
            if (won) pitBktMap[pitKey].w++; else pitBktMap[pitKey].l++;
            pitBktMap[pitKey].u += u;
          }
          function bucketDirRec(opp, dir, bucket) {
            const m = bucket === 'Pick' ? byOppDirPicks : byOppDirLeans;
            return (m[opp] && dir) ? m[opp][dir] : null;
          }
          // Pitcher's own bucket+direction record across all opponents.
          function pitcherDirRec(player, team, dir, bucket) {
            const m = bucket === 'Pick' ? byPitDirPicks : byPitDirLeans;
            const k = `${player}|${team || ''}`;
            return (m[k] && dir) ? m[k][dir] : null;
          }
          // Pitcher's bucket-only record (both directions, all opps).
          function pitcherBothDirsRec(player, team, bucket) {
            const m = bucket === 'Pick' ? byPitPicks : byPitLeans;
            const k = `${player}|${team || ''}`;
            return m[k] || null;
          }
          // Pitcher picks+watch combined (same direction).
          function pitcherAllBucketsDirRec(player, team, dir) {
            const pk = pitcherDirRec(player, team, dir, 'Pick');
            const ln = pitcherDirRec(player, team, dir, 'Lean');
            if (!pk && !ln) return null;
            return {
              w: (pk ? pk.w : 0) + (ln ? ln.w : 0),
              l: (pk ? pk.l : 0) + (ln ? ln.l : 0),
              u: (pk ? pk.u : 0) + (ln ? ln.u : 0),
            };
          }
          // Pitcher picks+watch combined (both directions).
          function pitcherAllBucketsBothDirsRec(player, team) {
            const pk = pitcherBothDirsRec(player, team, 'Pick');
            const ln = pitcherBothDirsRec(player, team, 'Lean');
            if (!pk && !ln) return null;
            return {
              w: (pk ? pk.w : 0) + (ln ? ln.w : 0),
              l: (pk ? pk.l : 0) + (ln ? ln.l : 0),
              u: (pk ? pk.u : 0) + (ln ? ln.u : 0),
            };
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

          // picksToShow / leansToShow are passed in; they're already filtered
          // and sorted by the caller. Local aliases below match the old
          // variable names used through the build code without renaming
          // every site.
          const todayPicks = picksToShow;
          // Leans (watch tier, pCover 0.60-0.68) are NOT bet by default —
          // our picks line is 0.68+. So we only surface a lean when at
          // least one positive override fires for it: a cohort or pitcher
          // track strong enough to "bump" it into a take. Everything else
          // gets hidden.
          // A lean is "bumped" when treating it as a pick would trigger a
          // positive override. Cohorts are looked up against the PICK
          // bucket (the relevant question is: do picks vs this opp/pitcher
          // in this direction win enough to upgrade a watchlist play into
          // a play we'd actually bet?).
          const _isBumpedLean = (p) => {
            const dir = _dirOf(p);
            if (!dir) return false;
            const dr = bucketDirRec(p.opp, dir, 'Pick');
            const ar = allBucketsDirRec(p.opp, dir);
            const prc = pitcherAllBucketsDirRec(displayName(p), p.team, dir);
            const pall = pitcherAllBucketsBothDirsRec(displayName(p), p.team);
            const drN = dr ? dr.w + dr.l : 0;
            const drW = drN > 0 ? dr.w / drN : 0;
            const arN = ar ? ar.w + ar.l : 0;
            const arW = arN > 0 ? ar.w / arN : 0;
            const arU = ar ? ar.u : 0;
            const prN = prc ? prc.w + prc.l : 0;
            const prW = prN > 0 ? prc.w / prN : 0;
            const paN = pall ? pall.w + pall.l : 0;
            const paW = paN > 0 ? pall.w / paN : 0;
            if (drN >= 4 && drW >= 0.75 && dr.u >= 2) return true;
            if (arN >= 6 && arW >= 0.70 && arU >= 2) return true;
            if (prN >= 4 && prW >= 0.75 && prc.u >= 2) return true;
            if (paN >= 6 && paW >= 0.70 && pall.u >= 2) return true;
            return false;
          };
          // Watch rows shown in BOTH table and Read are bumped-only —
          // the rest are noise. Section header makes clear they weren't
          // bet.
          const bumpedLeans = leansToShow.filter(_isBumpedLean);
          const todayLeans = bumpedLeans;
          const rows = [...todayPicks.map(p => ({p, bucket:'Pick'})),
                        ...todayLeans.map(p => ({p, bucket:'Lean'}))];
          if (rows.length === 0) return null;

          // body collects table + read + footer; no card-title (the unified
          // Picks card supplies the heading).
          const card = document.createElement('div');

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
            ['P O//U',  'center'],
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
            const recPitDir   = pitcherDirRec(displayName(p), p.team, dir, bucket);
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
              {v: fmtRec(recPitDir)},
              {v: fmtWR(recPitDir),  color: wrColor(recPitDir),  align:'right'},
              {v: fmtU(recPitDir),   color: uColor(recPitDir),   align:'right'},
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
          // Split picks into TAKE / PASS sub-sections using the shared
          // verdict — matches the Read narrative grouping so the table
          // reads consistently with the prose.
          const _pickVerdict = (p) => {
            const dir = _dirOf(p);
            return readVerdictFor({
              dirRec: bucketDirRec(p.opp, dir, 'Pick'),
              allRec: allBucketsDirRec(p.opp, dir),
              pitRec: pitcherAllBucketsDirRec(displayName(p), p.team, dir),
              pitAllRec: pitcherAllBucketsBothDirsRec(displayName(p), p.team),
            });
          };
          const takePicksT = todayPicks.filter(p => _pickVerdict(p) === 'TAKE');
          const passPicksT = todayPicks.filter(p => _pickVerdict(p) === 'PASS');
          // Collapsible TAKE / PASS sections (mirrors the Watch toggle below).
          // Default open so picks stay visible; click the header to collapse.
          const _appendCollapsibleSection = (label, color, rows, defaultOpen) => {
            if (!rows.length) return;
            const tr = tb.insertRow();
            const td = tr.insertCell();
            td.colSpan = cols.length;
            td.style.cssText = `padding:8px 8px 4px;font-size:11px;color:${color};font-weight:700;letter-spacing:0.08em;text-transform:uppercase;background:rgba(255,255,255,0.02);border-top:1px solid rgba(255,255,255,0.08);cursor:pointer;user-select:none`;
            let open = defaultOpen;
            const setTxt = () => { td.textContent = (open ? '▼ ' : '▶ ') + label; };
            setTxt();
            const dataTRs = [];
            for (const p of rows) {
              const before = tb.rows.length;
              appendDataRow(p, 'Pick');
              const nr = tb.rows[before];
              if (nr) { nr.style.display = open ? '' : 'none'; dataTRs.push(nr); }
            }
            tr.addEventListener('click', () => {
              open = !open;
              setTxt();
              dataTRs.forEach(r => { r.style.display = open ? '' : 'none'; });
            });
          };
          _appendCollapsibleSection(`Picks — TAKE (${takePicksT.length})`, 'var(--green)', takePicksT, false);
          _appendCollapsibleSection(`Picks — PASS (${passPicksT.length})`, 'var(--red)', passPicksT, false);
          if (todayLeans.length) {
            // Collapsible "Watch — history" section: shows every watch-tier
            // row for context (these are projections that didn't clear the
            // 0.68+ pick threshold). Never bet, but visible for awareness.
            // Bumped ones are surfaced in the Read narrative below.
            const tr = tb.insertRow();
            const td = tr.insertCell();
            td.colSpan = cols.length;
            td.style.cssText = `padding:8px 8px 4px;font-size:11px;color:var(--yellow);font-weight:700;letter-spacing:0.08em;text-transform:uppercase;background:rgba(255,255,255,0.02);border-top:1px solid rgba(255,255,255,0.08);cursor:pointer;user-select:none`;
            let _watchOpen = false;
            const _setTxt = () => {
              td.textContent = (_watchOpen ? '▼ ' : '▶ ') + `Watch — bumped (${todayLeans.length}) — not bet`;
            };
            _setTxt();
            const watchTRs = [];
            for (const p of todayLeans) {
              const before = tb.rows.length;
              appendDataRow(p, 'Lean');
              const newRow = tb.rows[before];
              if (newRow) {
                newRow.style.display = 'none';
                watchTRs.push(newRow);
              }
            }
            tr.addEventListener('click', () => {
              _watchOpen = !_watchOpen;
              _setTxt();
              watchTRs.forEach(r => { r.style.display = _watchOpen ? '' : 'none'; });
            });
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
              // Broader lens: same direction, picks + Watch tier (any pCover
              // ≥ 0.60). Direction stays locked because side matters; the
              // sample widens by including watch-tier graded plays that
              // share the projected direction. Excludes PASS (pCover < 0.60).
              const allRec = allBucketsDirRec(r.p.opp, dir);
              // Pitcher's own bucket+direction record (across all opponents).
              // Surfaces "the model is X-Y on Lopez Unders historically" alongside
              // the opponent cohort so a strong pitcher track-record (or red flag)
              // factors into the read.
              const pitRec = pitcherAllBucketsDirRec(displayName(r.p), r.p.team, dir);
              const pitAllRec = pitcherAllBucketsBothDirsRec(displayName(r.p), r.p.team);
              return {...r, dir, rec, allRec, pitRec, pitAllRec, cls: classify(rec)};
            });
          }
          // Order reads by model confidence (pCover) descending so the
          // highest-confidence play floats to the top of each section.
          function rankByPCover(arr) {
            return arr.sort((a, b) => (b.p.pCover || 0) - (a.p.pCover || 0));
          }
          const pickEntries = rankByPCover(annotate(todayPicks.map(p => ({p, bucket:'Pick'}))));
          // Read narrative only narrates bumped leans (matches user
          // intent — leans aren't bet; only highlight ones the read
          // would elevate).
          const leanEntries = rankByPCover(annotate(bumpedLeans.map(p => ({p, bucket:'Lean'}))));

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
            const recOf = (rec) => rec ? `${rec.w}-${rec.l}` : '—';
            const uOf   = (rec) => rec ? ((rec.u>=0?'+':'')+rec.u.toFixed(2)+'u') : '—';
            // Quality color for a record line: green if it's a good spot,
            // red if it's a bad one, gray if neutral or sample too thin.
            // Win-rate thresholds mirror the elite/solid/coin/caution buckets.
            const qualColor = (wr, n) => (n >= 4 && wr != null)
              ? (wr >= 0.65 ? 'var(--green)' : (wr < 0.45 ? 'var(--red)' : '#bbb'))
              : '#bbb';
            const bktPretty   = (rec) => rec ? `${recOf(rec)} ${uOf(rec)}` : '—';

            // Tier booleans for narrative branching.
            const elite     = bktWR != null && bktN >= 4 && bktWR >= 0.80;
            const solid     = bktWR != null && bktN >= 4 && bktWR >= 0.65 && bktWR < 0.80;
            const coin      = bktWR != null && bktN >= 4 && bktWR >= 0.45 && bktWR < 0.65;
            const caution   = bktWR != null && bktN >= 4 && bktWR < 0.45;
            const small     = bktN > 0 && bktN < 4;
            const empty     = bktN === 0;
            const bElite    = broadWR != null && broadN >= 8 && broadWR >= 0.80 && broadU >= 4;
            const bSolid    = broadWR != null && broadN >= 8 && broadWR >= 0.65 && broadU > 0;
            const bCaution  = broadWR != null && broadN >= 8 && broadWR <= 0.45 && broadU <= -2;
            // bWeak matches readVerdictFor() — broader WR < 0.45 at n>=8
            // (units axis dropped). Used to route the narrative into PASS-
            // flavored prose when coin + bWeak triggers the verdict gate.
            const bWeak     = broadWR != null && broadN >= 8 && broadWR < 0.45;
            const widerThanBkt = broadN > bktN;
            const dirWord = r.dir.toLowerCase() + 's';
            const oppStr = r.p.opp;
            // "Over" / "Under" — capitalized direction for the read narrative's
            // lead record phrase, which is prefixed with the side + opponent
            // so the in-bucket record is self-describing (e.g.
            // "Over Picks vs LAD 6-4 (60.0%, +1.67u)").
            const dirCap = r.dir.charAt(0) + r.dir.slice(1).toLowerCase();
            // Lead label reflects the row's own bucket: Pick-tier rows read
            // "… Picks vs LAD", watch-tier (Lean) rows read "… Watch vs LAD"
            // so the in-bucket record isn't mislabeled as a pick record.
            const bucketLabel = r.bucket === 'Lean' ? 'Watch' : 'Picks';
            const leadLabel = `${dirCap} ${bucketLabel} vs ${oppStr}`;

            // Build narrative — the section header (TAKE / PASS) and the PASS
            // badge already signal the verdict, so the prose just delivers the
            // why without a redundant "TAKE." / "PASS." prefix.
            // Confidence (pcPct) is shown once in the header (@ X%), so the
            // narratives below intentionally omit it — no "86.7% model" echo.
            // The whole vs-team line is colored by its record quality below
            // (qualColor), so the branches stay plain text — no inline sg/sr.
            let take = '';
            if (caution) {
              take = `${leadLabel} have bled here (${recOf(dirRec)}, ${(bktWR*100).toFixed(1)}%, ${uOf(dirRec)}) and broader isn't a rescue (${recOf(allRec)}). Model can't outrun history.`;
            } else if (coin && bWeak) {
              take = `${leadLabel} ${recOf(dirRec)} (${(bktWR*100).toFixed(1)}%, ${uOf(dirRec)}) and the broader cohort isn't carrying it either (${recOf(allRec)}, ${(broadWR*100).toFixed(1)}%, ${uOf(allRec)}) — flagged PASS.`;
            } else if ((small || empty) && bWeak) {
              take = `${leadLabel}: bucket thin (${recOf(dirRec)}) and the broader cohort is weak (${recOf(allRec)}, ${(broadWR*100).toFixed(1)}%, ${uOf(allRec)}) — flagged PASS.`;
            } else if (elite && (bElite || (broadWR && broadWR >= 0.80))) {
              take = `Cleanest spot of the night — ${leadLabel} ${recOf(dirRec)}, broader matchup ${recOf(allRec)} (${(broadWR*100).toFixed(1)}%, ${uOf(allRec)}).`;
            } else if (elite) {
              take = `${leadLabel} ${recOf(dirRec)} (${(bktWR*100).toFixed(1)}%, ${uOf(dirRec)}).`;
            } else if (solid && bSolid) {
              take = `${leadLabel} ${recOf(dirRec)} (${(bktWR*100).toFixed(1)}%, ${uOf(dirRec)}) and the broader cohort backs it (${recOf(allRec)}, ${(broadWR*100).toFixed(1)}%, ${uOf(allRec)}).`;
            } else if (solid) {
              take = `${leadLabel} ${recOf(dirRec)} (${(bktWR*100).toFixed(1)}%, ${uOf(dirRec)}). Broader ${recOf(allRec)}.`;
            } else if (coin && bSolid) {
              take = `${leadLabel} ${recOf(dirRec)} (${(bktWR*100).toFixed(1)}%) but the broader matchup widens to ${recOf(allRec)} (${(broadWR*100).toFixed(1)}%, ${uOf(allRec)}).`;
            } else if (coin) {
              take = `${leadLabel} ${recOf(dirRec)} (${(bktWR*100).toFixed(1)}%, ${uOf(dirRec)}), broader (picks+watch ${dirCap}) ${recOf(allRec)} — baseline TAKE.`;
            } else if ((small || empty) && (bElite || (broadWR && broadWR >= 0.80))) {
              take = `${leadLabel}: bucket sample thin (${recOf(dirRec)}) but P+L ${dirWord} vs ${oppStr} are ${recOf(allRec)} (${(broadWR*100).toFixed(1)}%, ${uOf(allRec)}).`;
            } else if ((small || empty) && bCaution) {
              take = `${leadLabel}: bucket thin (${recOf(dirRec)}) and the broader matchup is bad (${recOf(allRec)}, ${(broadWR*100).toFixed(1)}%, ${uOf(allRec)}) — model can't override.`;
            } else if (small || empty) {
              take = `${leadLabel}: bucket thin (${recOf(dirRec)}), broader ${recOf(allRec)} — riding the model.`;
            } else {
              take = `${leadLabel} ${recOf(dirRec)} (${(bktWR*100).toFixed(1)}%, ${uOf(dirRec)}). Broader ${recOf(allRec)}.`;
            }
            if (dirRec && dirRec.l === 0 && dirRec.w >= 4) {
              take += ` Perfect ${dirRec.w}-0 cohort in-bucket.`;
            }
            // vs-team line color: green good / red bad / gray neutral, by the
            // in-bucket (vs-opponent) record — same rule as the pitcher line.
            const takeColor = qualColor(bktWR, bktN);
            // --- Pitcher-history accent ---
            // Surface how the model has done on THIS pitcher (same bucket).
            // Two lenses: direction-matched ("P O//U") and both-directions
            // ("P O&&U") — the broader lens catches "model is good on this
            // pitcher regardless of side" or "model bleeds on this pitcher
            // either way".
            const pitRec = r.pitRec;
            const pitAllRec = r.pitAllRec;
            const pitN = pitRec ? pitRec.w + pitRec.l : 0;
            const pitAllN = pitAllRec ? pitAllRec.w + pitAllRec.l : 0;
            const pitNameTxt = `${displayName(r.p)} ${r.dir.toLowerCase()}s`;
            // Pitcher-history accents render on their OWN line below the
            // vs-opponent record. Only chime in when the signal is decisive —
            // co-sign reinforces TAKE, red flag reinforces PASS; anything in
            // between stays silent so the line doesn't fill with "neutral" notes.
            let pitTake = '';
            let pitColor = '#bbb';   // green good / red bad / gray neutral
            if (pitN >= 4) {
              const pitWR = pitRec.w / pitN;
              const pitTxt = `${recOf(pitRec)} (${(pitWR*100).toFixed(1)}%, ${uOf(pitRec)})`;
              if (pitWR >= 0.80 && pitRec.u >= 2) {
                pitTake = `${pitNameTxt} ${pitTxt} — pitcher track co-signs.`;
                pitColor = 'var(--green)';
              } else if (pitWR >= 0.65) {
                pitTake = `${pitNameTxt} ${pitTxt} — supportive pitcher track.`;
                pitColor = 'var(--green)';
              } else if (pitWR <= 0.40 && pitRec.u <= -1) {
                pitTake = `${pitNameTxt} have been ${pitTxt} — pitcher track is a red flag.`;
                pitColor = 'var(--red)';
              }
              // pitWR between 0.40 and 0.65 → no sentence (avoids "neutral").
            }
            // Broader (both-directions) pitcher cohort — only mention when
            // it pushes the decision one way or the other.
            if (pitAllRec && pitAllN >= 6) {
              const allWR = pitAllRec.w / pitAllN;
              const allTxt = `${recOf(pitAllRec)} (${(allWR*100).toFixed(1)}%, ${uOf(pitAllRec)})`;
              const broadName = `${displayName(r.p)} both ways`;
              const sep = pitTake ? ' ' : '';
              if (allWR >= 0.70 && pitAllRec.u >= 2) {
                pitTake += `${sep}Broader: ${broadName} ${allTxt} — whole book profitable.`;
                if (pitColor === '#bbb') pitColor = 'var(--green)';
              } else if (allWR <= 0.45 && pitAllRec.u <= -2) {
                pitTake += `${sep}Broader: ${broadName} ${allTxt} — model bleeds on this pitcher.`;
                if (pitColor === '#bbb') pitColor = 'var(--red)';
              }
            }
            // --- Sizing recommendation ---
            // Delegate to the shared readVerdictFor() — keeps the live read
            // and the historical backtest in lockstep. Watch (Lean) rows
            // don't get a sizing badge: even bumped ones are surfaced as
            // info, not as a "bet 1u" call.
            const _verdict = readVerdictFor({ dirRec, allRec, pitRec, pitAllRec });
            // No "1u" badge — every TAKE is a flat 1u, so the label carries no
            // information and just clutters the row (and ran into the name as
            // "1uC.Bassitt" when copied). Only surface the PASS flag, which is
            // meaningful: it marks a model pick the read is overriding.
            let sizeBadge = '';
            if (r.bucket === 'Pick' && _verdict === 'PASS') {
              sizeBadge = `<strong style="color:var(--red);margin-right:6px">PASS</strong>`;
            }
            // Three lines for readability: the bet up top, the vs-opponent
            // record next, and any pitcher-history note on its own line.
            line.innerHTML =
              `<div>${sizeBadge}${nameSpan} ${dirSpan} ${oppSpan} <span style="color:#888">@ ${pcPct}%</span></div>`
              + `<div style="margin-top:3px;color:${takeColor}">${take}</div>`
              + (pitTake ? `<div style="margin-top:2px;color:${pitColor}">${pitTake}</div>` : '');
            return line;
          }

          function appendReadSection(label, color, entries, opts) {
            if (!entries.length) return;
            const collapsible = opts && opts.collapsible;
            const sub = document.createElement('div');
            sub.style.cssText = `font-size:11px;color:${color};font-weight:700;letter-spacing:0.08em;text-transform:uppercase;margin:10px 0 6px;padding-left:2px${collapsible ? ';cursor:pointer;user-select:none' : ''}`;
            const updateLabel = (open) => {
              sub.textContent = collapsible
                ? (open ? '▼ ' : '▶ ') + `${label} (${entries.length})`
                : `${label} (${entries.length})`;
            };
            updateLabel(false);
            readBlock.appendChild(sub);
            const rowDivs = entries.map(r => renderReadRow(r));
            for (const d of rowDivs) {
              if (collapsible) d.style.display = 'none';
              readBlock.appendChild(d);
            }
            if (collapsible) {
              let open = false;
              sub.addEventListener('click', () => {
                open = !open;
                updateLabel(open);
                rowDivs.forEach(d => { d.style.display = open ? '' : 'none'; });
              });
            }
          }
          // Split Picks into TAKE / PASS sub-sections so the verdict is
          // visible at a glance. TAKE group stays expanded (it's what we
          // bet); PASS group collapses so the list stays focused.
          const _verdictFor = (e) => readVerdictFor({
            dirRec: e.rec, allRec: e.allRec, pitRec: e.pitRec, pitAllRec: e.pitAllRec,
          });
          const takePicks = pickEntries.filter(e => _verdictFor(e) === 'TAKE');
          const passPicks = pickEntries.filter(e => _verdictFor(e) === 'PASS');
          appendReadSection('Picks — TAKE', 'var(--green)', takePicks);
          appendReadSection('Picks — PASS', 'var(--red)', passPicks);
          appendReadSection('Watch — bumped', 'var(--yellow)', leanEntries, { collapsible: true });

          card.appendChild(readBlock);

          // Footer caption — definition list with bolded column names.
          const note = document.createElement('div');
          note.style.cssText = 'padding:12px 4px 4px;color:#999;font-size:11px;line-height:1.7';
          const defStyle = 'color:#bbb;font-weight:600;font-family:ui-monospace,Menlo,Consolas,monospace';
          note.innerHTML = `
            <div style="margin-bottom:6px"><span style="${defStyle}">O//U</span> &nbsp;&nbsp; Picks + direction VS OPP (narrowest cohort)</div>
            <div style="margin-bottom:6px"><span style="${defStyle}">O&amp;&amp;U</span> &nbsp; Picks, both directions combined VS OPP</div>
            <div style="margin-bottom:6px"><span style="${defStyle}">P O//U</span> &nbsp; Picks + direction for THIS pitcher (all opps)</div>
            <div style="margin-top:8px;padding-top:6px;border-top:1px solid rgba(255,255,255,0.05);color:#888;font-style:italic">
              Pick rows count picks history (&gt;= 0.65 pCover) · Watch and Pass tiers excluded<br>
              Tiers: <span style="color:var(--green)">Elite ≥80%</span> · <span style="color:#9ee493">Solid ≥65%</span> · <span style="color:#ccc">Neutral ≥50%</span> · <span style="color:var(--red)">Caution &lt;50%</span> · <span style="color:#aaa">Small &lt;4 samples</span>
            </div>
          `;
          card.appendChild(note);
          return card;
          } // ← end buildMatchupSection

          // === Build today + yesterday matchup sections and inject into
          // the Picks card's slots ===
          const _gameStatusesM = data.gameStatuses || {};
          const _VOID_M = new Set([
            'Postponed','Cancelled','Canceled','Suspended',
            'Postponed Inclement Weather','Postponed Rain',
            'Suspended: Inclement Weather','Suspended: Rain',
          ]);
          const _voidRowM = (p) => _VOID_M.has(
            _gameStatusesM[p.team] || _gameStatusesM[p.opp] || ''
          );
          const _voidPickM = (p) => p.result === 'VOID';

          const _todayPicksM = picks
            .filter(p => p.date === todayStr && !_voidRowM(p) && !_voidPickM(p))
            .sort((a, b) => (b.pCover || 0) - (a.pCover || 0));
          const _todayLeansM = (data.props || [])
            .filter(p => p.date === todayStr && isLean(p) && !_voidRowM(p) && !_voidPickM(p))
            .sort((a, b) => (b.pCover || 0) - (a.pCover || 0));
          const _todayMatchupBody = buildMatchupSection({
            picksToShow: _todayPicksM,
            leansToShow: _todayLeansM,
            gradedCutoff: todayStr,
          });
          if (_todayMatchupBody && _todayMatchupSlot) {
            while (_todayMatchupBody.firstChild) {
              _todayMatchupSlot.appendChild(_todayMatchupBody.firstChild);
            }
          }

          // Yesterday section — gradedCutoff = the latest prior date in the
          // dataset, so the cohort maps reflect only what was known BEFORE
          // yesterday's picks were locked.
          const _datesM = [...new Set((data.props || []).map(p => p.date))].sort();
          const _yesterdayStrM = (() => {
            for (let i = _datesM.length - 1; i >= 0; i--) {
              if (_datesM[i] && _datesM[i] < todayStr) return _datesM[i];
            }
            return null;
          })();
          if (_yesterdayStrM) {
            const _yPicks = picks
              .filter(p => p.date === _yesterdayStrM && !_voidRowM(p) && !_voidPickM(p))
              .sort((a, b) => (b.pCover || 0) - (a.pCover || 0));
            const _yLeans = (data.props || [])
              .filter(p => p.date === _yesterdayStrM && isLean(p) && !_voidRowM(p) && !_voidPickM(p))
              .sort((a, b) => (b.pCover || 0) - (a.pCover || 0));
            const _yesterdayMatchupBody = buildMatchupSection({
              picksToShow: _yPicks,
              leansToShow: _yLeans,
              gradedCutoff: _yesterdayStrM,
            });
            if (_yesterdayMatchupBody && _yesterdayMatchupSlot) {
              while (_yesterdayMatchupBody.firstChild) {
                _yesterdayMatchupSlot.appendChild(_yesterdayMatchupBody.firstChild);
              }
            }
          }

          // Grading + units helpers shared by Pitcher History and Team History.
          // Hoisted here so the Team History block below can call them without
          // each card re-declaring its own copy.
          const _phUnits = (o, won, sz) => {
            if (o == null || won == null) return 0;
            if (o > 0) return won ? sz * (o / 100) : -sz;
            return won ? sz : sz * (-Math.abs(o) / 100);
          };
          const _phGrade = (p) => {
            if (p.result === 'VOID') return 'V';
            if (p.actual == null || p.line == null) return null;
            const dir = p.pick === 'OVER' || p.pick === 'UNDER'
              ? p.pick
              : (p.would_be_pick || ((p.proj || 0) > (p.line || 0) ? 'OVER' : 'UNDER'));
            if (dir === 'OVER')  return p.actual > p.line ? 'W' : 'L';
            return p.actual < p.line ? 'W' : 'L';
          };
          const _phBucket = (p) => {
            if (p.pick === 'OVER' || p.pick === 'UNDER') return 'PICK';
            const pc = p.pCover || 0;
            if (pc >= MLB_WATCH_FLOOR && pc < MLB_PICK_THRESHOLD) return 'WATCH';
            return 'PASS';
          };
          const _phOdds = (p) => {
            if (p.odds != null) return p.odds;
            const dir = p.would_be_pick || ((p.proj || 0) > (p.line || 0) ? 'OVER' : 'UNDER');
            return dir === 'OVER' ? (p.over_price ?? null) : (p.under_price ?? null);
          };

          // ── Pitcher History — drill into one pitcher's full season log ──
          // Sits directly under Today's Games. Dropdown of TODAY's projected pitchers;
          // table shows every projection ever made for the selected pitcher.
          // Useful when a Soriano/Sugano-style pattern shows up — see at-a-glance
          // every prior pick/lean/pass and how the model has graded them.
          const phCard = document.createElement('div');
          phCard.className = 'card card-games';
          phCard.style.marginBottom = '16px';
          phCard.appendChild(Object.assign(document.createElement('div'), {
            className:'card-title',
            textContent:`Pitcher History (${todayStr})`,
          }));

          // Build list of pitchers for the dropdown.
          // Today's probables come from todayProjections (ALL projected
          // pitchers, not just picks/leans in data.props). Then all other
          // pitchers with any historical entry follow so you can look up
          // anyone on their off day.
          const _pitcherKey = (p) => `${displayName(p)}|${p.team || ''}`;

          // --- Today's probables from todayProjections (includes PASS) ---
          const _todayPropsAll = (data.todayProjections || []).filter(p => p.market === 'strikeouts');
          const _byTodayPitcher = new Map();
          for (const p of _todayPropsAll) {
            const k = _pitcherKey(p);
            const prev = _byTodayPitcher.get(k);
            const score = (p.pCover || 0);
            if (!prev || score > prev.pCover) {
              // Direction shown in the dropdown tag — picks/leans use their
              // committed side, watch/pass fall back to would_be_pick.
              const _dir = (p.pick === 'OVER' || p.pick === 'UNDER')
                ? p.pick
                : (p.would_be_pick
                    || ((p.proj || 0) > (p.line || 0) ? 'OVER' : 'UNDER'));
              _byTodayPitcher.set(k, {
                key: k,
                name: displayName(p),
                team: p.team || '',
                opp:  p.opp  || '',
                pCover: score,
                pick: p.pick,
                dir: _dir,
                isLean: isLean(p),
                isToday: true,
              });
            }
          }
          const _todayProbables = [...(_byTodayPitcher.values())].sort((a, b) => {
            const rank = (x) => {
              if (x.pick === 'OVER' || x.pick === 'UNDER') return 0;
              if (x.isLean) return 1;
              return 2;
            };
            const ra = rank(a), rb = rank(b);
            if (ra !== rb) return ra - rb;
            return (b.pCover || 0) - (a.pCover || 0);
          });

          // --- All pitchers with any historical strikeouts entry ---
          const _allProps = (data.props || []).filter(p => p.market === 'strikeouts');
          const _byAllPitcher = new Map();
          for (const p of _allProps) {
            const k = _pitcherKey(p);
            if (!_byAllPitcher.has(k)) {
              _byAllPitcher.set(k, {
                key: k,
                name: displayName(p),
                team: p.team || '',
                opp:  p.opp  || '',
                pCover: 0,
                pick: 'PASS',
                isLean: false,
                isToday: false,
              });
            }
          }
          for (const k of _byTodayPitcher.keys()) _byAllPitcher.delete(k);
          const _restPitchers = [...(_byAllPitcher.values())].sort((a, b) =>
            a.name.localeCompare(b.name)
          );

          const _probables = [..._todayProbables, ..._restPitchers];

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
            let _addedSeparator = false;
            _probables.forEach((pr, i) => {
              if (!pr.isToday && !_addedSeparator && _todayProbables.length > 0) {
                const sep = document.createElement('option');
                sep.disabled = true;
                sep.textContent = '── All Pitchers ──';
                sel.appendChild(sep);
                _addedSeparator = true;
              }
              const opt = document.createElement('option');
              opt.value = pr.key;
              if (pr.isToday) {
                // Show direction (Over/Under) + pCover instead of bucket
                // label so the dropdown communicates the projected side.
                const pct = pr.pCover ? (pr.pCover * 100).toFixed(1) + '%' : '';
                const side = pr.dir === 'OVER' ? 'Over'
                           : pr.dir === 'UNDER' ? 'Under'
                           : '';
                const tag = (side && pct) ? ` [${side} ${pct}]`
                          : (pct ? ` [${pct}]` : '');
                opt.textContent = `${pr.name} (${pr.team} vs ${pr.opp})${tag}`;
              } else {
                opt.textContent = `${pr.name} (${pr.team})`;
              }
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
              { key: 'ALL',   label: 'All',     color: '#ccc'           },
              { key: 'PICK',  label: 'Picks',   color: '#a78bfa'        },
              { key: 'WATCH', label: 'Watch',   color: 'var(--yellow)'  },
              { key: 'PASS',  label: 'Passes',  color: '#888'           },
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

            // Stake/grade helpers are hoisted above so Team History can reuse
            // them without re-declaring.

            // Render function — pulls all rows for the selected pitcher key.
            // Merges data.props (picks/leans/watchlist from all dates) with
            // todayProjections (all pitchers including low-pCover PASSes)
            // so today's projection always appears even when it's a PASS.
            function renderPitcherHistory(key) {
              const fromProps = (data.props || []).filter(p =>
                p.market === 'strikeouts' && _pitcherKey(p) === key
              );
              const fromToday = (data.todayProjections || []).filter(p =>
                p.market === 'strikeouts' && _pitcherKey(p) === key
              );
              const seen = new Set(fromProps.map(p => p.date));
              const merged = [...fromProps];
              for (const p of fromToday) {
                if (!seen.has(p.date)) {
                  merged.push(p);
                  seen.add(p.date);
                }
              }
              // Newest first — matches Team History's sort order so the two
              // cards read top-to-bottom the same way.
              const allEver = merged.sort((a, b) => (b.date || '').localeCompare(a.date || ''));

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

              // Summary tally is owned by _renderSummary(allEver) below — no
              // need for an in-table accumulator. Loop is purely for rendering.
              for (const p of all) {
                const tr = body.insertRow();
                tr.style.borderBottom = '1px solid rgba(255,255,255,0.04)';
                const bkt = _phBucket(p);
                const dir = (p.pick === 'OVER' || p.pick === 'UNDER')
                  ? p.pick
                  : (p.would_be_pick || ((p.proj || 0) > (p.line || 0) ? 'OVER' : 'UNDER'));
                const grade = _phGrade(p);
                const odds = _phOdds(p);
                const sz = bkt === 'PICK' ? 1.0 : (bkt === 'WATCH' ? 1.0 : 0);
                const u = (grade === 'V')
                  ? 0
                  : ((bkt !== 'PASS' && grade != null)
                      ? _phUnits(odds, grade === 'W', sz)
                      : null);

                const edge = (p.proj != null && p.line != null) ? (p.proj - p.line).toFixed(1) : '—';
                const edgeStr = edge !== '—' ? (parseFloat(edge) > 0 ? '+' + edge : edge) : '—';
                const bktColor = bkt === 'PICK' ? '#a78bfa' : bkt === 'WATCH' ? 'var(--yellow)' : '#888';
                const dirColor = dir === 'OVER' ? 'var(--green)' : 'var(--red)';
                const resColor = grade === 'W' ? 'var(--green)' : grade === 'L' ? 'var(--red)' : grade === 'V' ? '#888' : '#888';
                const resLabel = grade === 'V' ? 'VOID' : (grade || '—');
                // Watch units render yellow regardless of sign (W/L color
                // already lives in the Result column); Picks keep the
                // green/red positive/negative convention.
                const uColor = u == null
                  ? '#888'
                  : (grade === 'V'
                      ? '#888'
                      : (bkt === 'WATCH'
                          ? 'var(--yellow)'
                          : (u > 0 ? 'var(--green)' : u < 0 ? 'var(--red)' : '#ccc')));
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
                  {v: resLabel, align:'center', color:resColor, weight:'700'},
                  {v: grade === 'V' ? 'void' : (u == null ? '—' : (u >= 0 ? '+' : '') + u.toFixed(2) + 'u'), align:'right', color:uColor, weight:'600'},
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
              let pw=0, pl=0, lw=0, ll=0, p_u=0, l_u=0, aw=0, al=0, a_u=0;
              for (const p of allEver) {
                const bkt = _phBucket(p);
                const grade = _phGrade(p);
                if (!grade || grade === 'V') continue;
                const dir = (p.pick === 'OVER' || p.pick === 'UNDER')
                  ? p.pick
                  : (p.would_be_pick || ((p.proj || 0) > (p.line || 0) ? 'OVER' : 'UNDER'));
                const odds = _phOdds(p);
                // All tier = every graded play (Picks + Watch + Pass) —
                // mirrors Team History's "All" combined record.
                if (grade === 'W') aw++; else al++;
                a_u += _phUnits(odds, grade === 'W', 1.0);
                if (bkt === 'PICK') {
                  if (grade === 'W') pw++; else pl++;
                  p_u += _phUnits(odds, grade === 'W', 1.0);
                } else if (bkt === 'WATCH') {
                  if (grade === 'W') lw++; else ll++;
                  l_u += _phUnits(odds, grade === 'W', 1.0);
                }
              }
              const pickTotal = pw + pl, watchTotal = lw + ll, allTotal = aw + al;
              const parts = [];
              // Color-match the Bkt column (Picks=purple, Watch=yellow).
              const pickColor  = '#a78bfa';
              const watchColor = 'var(--yellow)';
              const allColor   = '#ccc';
              if (allTotal > 0) {
                const wr = (aw/allTotal*100).toFixed(1);
                const u  = (a_u >= 0 ? '+' : '') + a_u.toFixed(2) + 'u';
                parts.push(`<span style="color:${allColor};font-weight:600">All ${aw}-${al} (${wr}%) ${u}</span>`);
              }
              if (pickTotal > 0) {
                const wr = (pw/pickTotal*100).toFixed(1);
                const u  = (p_u >= 0 ? '+' : '') + p_u.toFixed(2) + 'u';
                parts.push(`<span style="color:${pickColor};font-weight:600">Picks ${pw}-${pl} (${wr}%) ${u}</span>`);
              }
              if (watchTotal > 0) {
                const wr = (lw/watchTotal*100).toFixed(1);
                const u  = (l_u >= 0 ? '+' : '') + l_u.toFixed(2) + 'u';
                parts.push(`<span style="color:${watchColor};font-weight:600">Watch ${lw}-${ll} (${wr}%) ${u}</span>`);
              }
              if (parts.length === 0) parts.push('<span style="color:#888">No graded plays yet</span>');
              // Stack each record on its own line so picks/leans don't compete
              // for the same horizontal slot when both have long unit values.
              summarySpan.innerHTML = parts.map(p => `<div>${p}</div>`).join('');
            }

            // Picking a pitcher from the dropdown also drives Team History:
            //   - pitcher IS in today's slate → jump to that matchup +
            //     view-as the pitcher's team (dropdown shows opp).
            //   - pitcher is NOT in today's slate → hide the per-game
            //     pitcher toggle (it's no longer meaningful) and reset Team
            //     History to its "All" view.
            // The free-text dropdown in Team History still works either way.
            sel.addEventListener('change', () => {
              renderPitcherHistory(sel.value);
              const parts = (sel.value || '').split('|');
              const name = parts[0] || '';
              const team = parts[1] || '';
              const todayRow = _todayPropsAll.find(p =>
                displayName(p) === name && p.team === team
              );
              if (todayRow && todayRow.team && todayRow.opp) {
                // Today-slate pitcher: repaint the per-game toggle to THIS
                // pitcher's matchup (so we don't keep showing the previous
                // game's pair), then activate the just-picked pitcher.
                _populatePitcherToggle([todayRow.team, todayRow.opp]);
                _stylePhPitBtns(sel.value);
                _pitcherToggleSubs.forEach(fn => {
                  try { fn({ team: todayRow.team, opp: todayRow.opp, fromToday: true }); } catch (e) {}
                });
              } else {
                // Free-search pick — drop the per-game toggle row so the
                // dropdown is the only context.
                phPitToggleRow.style.display = 'none';
                _pitcherToggleSubs.forEach(fn => {
                  try { fn({ reset: true }); } catch (e) {}
                });
              }
            });
            // Initial render = first probable (highest conviction). Also
            // sync Team History to this pitcher's matchup so the two cards
            // agree on first page load (without it, Team History defaults
            // to its own alphabetical first team and the user has to
            // re-click to align them).
            const _initialKey = _probables[0].key;
            renderPitcherHistory(_initialKey);
            (function _syncTeamHistoryInitial() {
              const initial = _todayPropsAll.find(p =>
                _pitcherKey(p) === _initialKey
              );
              if (initial && initial.team && initial.opp) {
                // Defer to give Team History a tick to finish wiring up its
                // own subscribers (built later in the render sequence).
                setTimeout(() => {
                  _pitcherToggleSubs.forEach(fn => {
                    try {
                      fn({ team: initial.team, opp: initial.opp, fromToday: true });
                    } catch (e) {}
                  });
                }, 0);
              }
            })();

            // --- Per-game pitcher toggle ---
            // Hidden until Today's Games fires a "game picked" event. When
            // it does, both starters in that game render as toggle buttons
            // so the user can flip between the two pitchers in the matchup.
            const phPitToggleRow = document.createElement('div');
            phPitToggleRow.style.cssText = 'display:none;align-items:center;gap:8px;padding:4px 4px 8px;flex-wrap:wrap';
            const phPitToggleLabel = document.createElement('span');
            phPitToggleLabel.style.cssText = 'font-size:11px;color:#bbb;font-weight:600';
            phPitToggleLabel.textContent = 'Pitchers:';
            phPitToggleRow.appendChild(phPitToggleLabel);
            const phPitBtns = [document.createElement('button'), document.createElement('button')];
            phPitBtns.forEach(b => {
              b.style.cssText = 'padding:4px 12px;font-size:11px;font-weight:600;border:1px solid rgba(255,255,255,0.2);background:transparent;color:#ccc;border-radius:4px;cursor:pointer;transition:all 0.15s';
              b.addEventListener('click', () => {
                _stylePhPitBtns(b.dataset.key);
                sel.value = b.dataset.key;
                renderPitcherHistory(b.dataset.key);
                // Tell Team History to mirror — flip View-as to this
                // pitcher's team so its dropdown shows the OPP.
                const team = b.dataset.team || '';
                _pitcherToggleSubs.forEach(fn => { try { fn({ team }); } catch (e) {} });
              });
              phPitToggleRow.appendChild(b);
            });
            function _stylePhPitBtns(activeKey) {
              phPitBtns.forEach(b => {
                const isActive = b.dataset.key === activeKey;
                b.style.background = isActive ? '#a78bfa' : 'transparent';
                b.style.color = isActive ? '#0a0a0a' : '#ccc';
                b.style.borderColor = isActive ? '#a78bfa' : 'rgba(255,255,255,0.2)';
              });
            }
            // Inject the toggle row just below the dropdown row.
            ctrlRow.parentNode.insertBefore(phPitToggleRow, ctrlRow.nextSibling);

            // Populate the per-game pitcher toggle row for a matchup. Shared
            // between the Today's-Games game-click sub and the dropdown
            // change handler (so dropdown-jumping to a today-slate pitcher
            // in another matchup updates the toggle to that matchup's pair).
            const _findPitcherForTeam = (tm) => {
              const fromToday = _todayProbables.find(pr => pr.team === tm);
              if (fromToday) return { key: fromToday.key, name: fromToday.name, team: tm };
              const proj = (data.todayProjections || []).find(p =>
                p.market === 'strikeouts' && p.team === tm
              );
              if (proj) return { key: _pitcherKey(proj), name: displayName(proj), team: tm };
              return null;
            };
            // Repaint the toggle row for the given teams. Returns the two
            // pitcher records found, in slot order.
            function _populatePitcherToggle(teams) {
              const pa = _findPitcherForTeam(teams[0]);
              const pb = _findPitcherForTeam(teams[1]);
              if (!pa && !pb) return [null, null];
              phPitToggleRow.style.display = 'flex';
              [pa, pb].forEach((s, i) => {
                const btn = phPitBtns[i];
                if (s) {
                  btn.dataset.key = s.key;
                  btn.dataset.team = s.team;
                  btn.textContent = s.name;
                  btn.style.display = '';
                } else {
                  btn.style.display = 'none';
                }
              });
              return [pa, pb];
            }

            _gameClickSubs.push(({ teams, reset }) => {
              if (reset) {
                phPitToggleRow.style.display = 'none';
                return;
              }
              if (!teams) return;
              const [pa, pb] = _populatePitcherToggle(teams);
              if (!pa && !pb) return;
              // Activate whichever side has the higher-conviction projection.
              const order = [pa, pb].filter(Boolean).sort((x, y) => {
                const xp = _todayProbables.find(p => p.key === x.key);
                const yp = _todayProbables.find(p => p.key === y.key);
                return (yp?.pCover || 0) - (xp?.pCover || 0);
              });
              const top = order[0];
              if (top) {
                _stylePhPitBtns(top.key);
                sel.value = top.key;
                renderPitcherHistory(top.key);
                // Sync Team History to this pitcher (deferred so Team
                // History's own game-click sub finishes first).
                setTimeout(() => {
                  _pitcherToggleSubs.forEach(fn => { try { fn({ team: top.team }); } catch (e) {} });
                }, 0);
              }
            });

            el.appendChild(phCard);
          }

          // ── Team History — how the model has done VS a specific opponent ──
          // Sits between Pitcher History and Matchup History. Dropdown lists
          // today's opponent teams first (the lineups our picks are facing),
          // then every other team with any historical entry. Useful for
          // "how have we done picking strikeouts against CLE this year".
          const thCard = document.createElement('div');
          thCard.className = 'card card-games';
          thCard.style.marginBottom = '16px';
          thCard.appendChild(Object.assign(document.createElement('div'), {
            className:'card-title',
            textContent:`Team History (${todayStr})`,
          }));

          // Key by opponent team — that's the lineup being faced.
          const _teamKey = (p) => p.opp || '';

          // Today's opponent teams (any pitcher projected against them today).
          const _todayPropsAllT = (data.todayProjections || []).filter(p => p.market === 'strikeouts');
          const _byTodayTeam = new Map();
          for (const p of _todayPropsAllT) {
            const k = _teamKey(p);
            if (!k) continue;
            if (!_byTodayTeam.has(k)) {
              _byTodayTeam.set(k, { key: k, isToday: true });
            }
          }
          const _todayTeams = [..._byTodayTeam.keys()].sort();

          // Today's matchups — paired teams sorted by game time. Mirrors
          // the Today's Games card so the user gets the same slate view here.
          const _gameTimesT = data.gameTimes || {};
          const _matchupSet = new Map();
          // Pair teams by matching scheduled time.
          const _teamsByTimeT = {};
          for (const [tm, t] of Object.entries(_gameTimesT)) {
            if (!_teamsByTimeT[t]) _teamsByTimeT[t] = [];
            _teamsByTimeT[t].push(tm);
          }
          // Display order: home team on the RIGHT (matches Today's Games
          // and standard baseball-scoreboard convention).
          const _homeAwayT = (data.homeAway || {})[todayStr] || {};
          const _orderTeamPair = (a, b) => {
            const aHome = _homeAwayT[a] === 'home';
            const bHome = _homeAwayT[b] === 'home';
            if (aHome && !bHome) return [b, a];
            if (bHome && !aHome) return [a, b];
            return [a, b];
          };
          for (const [t, teams] of Object.entries(_teamsByTimeT)) {
            if (teams.length === 2) {
              const k = [...teams].sort().join('@');
              const ordered = _orderTeamPair(teams[0], teams[1]);
              _matchupSet.set(k, { teams: ordered, time: t });
            }
          }
          // Fallback for games where multiple games share a start time
          // (so the pair-by-time pass above can't isolate them as 2-team
          // buckets). Look up the actual time per team — same as Today's
          // Games — so these still slot into the correct chronological
          // position instead of getting pinned to the end.
          for (const p of _todayPropsAllT) {
            if (!p.team || !p.opp) continue;
            const k = [p.team, p.opp].sort().join('@');
            if (!_matchupSet.has(k)) {
              const t = _gameTimesT[p.team] || _gameTimesT[p.opp] || '9999';
              const ordered = _orderTeamPair(p.team, p.opp);
              _matchupSet.set(k, { teams: ordered, time: t });
            }
          }
          const _todayMatchups = [..._matchupSet.values()]
            .sort((a, b) => (a.time || '').localeCompare(b.time || ''));

          // Every other team appearing in history (any side).
          const _allPropsT = (data.props || []).filter(p => p.market === 'strikeouts');
          const _allTeamsSet = new Set();
          for (const p of _allPropsT) {
            const k = _teamKey(p);
            if (k) _allTeamsSet.add(k);
          }
          for (const k of _byTodayTeam.keys()) _allTeamsSet.delete(k);
          const _restTeams = [..._allTeamsSet].sort();

          const _teamList = [
            ..._todayTeams.map(k => ({ key: k, isToday: true })),
            ..._restTeams.map(k => ({ key: k, isToday: false })),
          ];

          if (_teamList.length === 0) {
            const empty = document.createElement('div');
            empty.style.cssText = 'padding:12px;color:#888;font-size:12px;font-style:italic';
            empty.textContent = 'No opponent teams found in history.';
            thCard.appendChild(empty);
            el.appendChild(thCard);
          } else {
            // --- Today's matchups row + per-matchup team toggle ---
            // Renders the slate as clickable chips. Clicking a matchup chip
            // selects it and reveals two team-toggle buttons below; each
            // toggle updates the dropdown and re-renders the table for that
            // team. The dropdown stays available for searching/jumping to
            // any team in history.
            // Sort the pair so the key matches _activeMatchKey (which is
            // computed via _kFromTeams below). Without this, matchups built
            // from display-ordered teams index under a different key than
            // _showMatchup sets — _styleMatchupBtns then can't find the
            // matching chip and no purple highlight shows.
            const _matchKey = (m) => [...m.teams].sort().join('@');
            let _activeMatchKey = null;
            if (_todayMatchups.length > 0) {
              const matchTitle = document.createElement('div');
              matchTitle.style.cssText = 'font-size:11px;color:#bbb;font-weight:600;letter-spacing:0.06em;text-transform:uppercase;padding:4px 4px 6px';
              matchTitle.textContent = `Today's Matchups (${_todayMatchups.length})`;
              thCard.appendChild(matchTitle);

              const matchRow = document.createElement('div');
              matchRow.style.cssText = 'display:flex;flex-wrap:wrap;gap:6px;padding:0 4px 6px';
              const matchBtns = {};
              const teamToggleRow = document.createElement('div');
              teamToggleRow.style.cssText = 'display:none;align-items:center;gap:8px;padding:4px 4px 8px;flex-wrap:wrap';
              const teamToggleLabel = document.createElement('span');
              teamToggleLabel.style.cssText = 'font-size:11px;color:#bbb;font-weight:600';
              teamToggleLabel.textContent = 'View as:';
              teamToggleRow.appendChild(teamToggleLabel);
              const teamBtns = [
                document.createElement('button'),
                document.createElement('button'),
              ];

              function _styleMatchupBtns() {
                for (const k in matchBtns) {
                  const b = matchBtns[k];
                  const active = (k === _activeMatchKey);
                  b.style.background = active ? '#a78bfa' : 'rgba(255,255,255,0.04)';
                  b.style.color = active ? '#0a0a0a' : '#ccc';
                  b.style.borderColor = active ? '#a78bfa' : 'rgba(255,255,255,0.15)';
                  b.style.fontWeight = active ? '700' : '500';
                }
              }
              function _styleTeamBtns(activeTeam) {
                teamBtns.forEach(b => {
                  const isActive = b.dataset.team === activeTeam;
                  b.style.background = isActive ? '#a78bfa' : 'transparent';
                  b.style.color = isActive ? '#0a0a0a' : '#ccc';
                  b.style.borderColor = isActive ? '#a78bfa' : 'rgba(255,255,255,0.2)';
                });
              }
              // _matchKey expects alphabetical pair; m.teams may be unsorted
              // for display, so derive the key off a sorted copy.
              const _kFromTeams = (teams) => [...teams].sort().join('@');
              function _showMatchup(m) {
                _activeMatchKey = _kFromTeams(m.teams);
                _styleMatchupBtns();
                teamToggleRow.style.display = 'flex';
                teamBtns.forEach((b, i) => {
                  const tm = m.teams[i];
                  b.dataset.team = tm;     // also the dropdown filter value
                  b.textContent = tm;
                });
                // Default View-as: pick whichever team has a today projection
                // targeting it (i.e., model is firing AGAINST this lineup).
                // The actual final selection is finalized by Pitcher History's
                // toggle event right after, so this is just a sane fallback.
                const viewAs = _byTodayTeam.has(m.teams[0]) ? m.teams[0]
                             : _byTodayTeam.has(m.teams[1]) ? m.teams[1]
                             : m.teams[0];
                _styleTeamBtns(viewAs);
                thSel.value = viewAs;
                renderTeamHistory(viewAs);
              }

              // "All" chip — clears the matchup selection so the user can
              // browse the full Team History without committing to a game.
              // Hides the per-matchup team toggle row and returns the
              // dropdown to its first option (alphabetical first team).
              const allMatchBtn = document.createElement('button');
              allMatchBtn.textContent = 'All';
              allMatchBtn.style.cssText = 'padding:5px 10px;font-size:11px;font-weight:500;border:1px solid rgba(255,255,255,0.15);background:rgba(255,255,255,0.04);color:#ccc;border-radius:4px;cursor:pointer;transition:all 0.15s';
              allMatchBtn.addEventListener('click', () => {
                _activeMatchKey = 'ALL';
                _styleMatchupBtns();
                teamToggleRow.style.display = 'none';
                thSel.value = _teamList[0].key;
                renderTeamHistory(thSel.value);
              });
              matchBtns['ALL'] = allMatchBtn;
              matchRow.appendChild(allMatchBtn);

              _todayMatchups.forEach(m => {
                const b = document.createElement('button');
                b.textContent = m.teams.join(' vs ');
                b.style.cssText = 'padding:5px 10px;font-size:11px;font-weight:500;border:1px solid rgba(255,255,255,0.15);background:rgba(255,255,255,0.04);color:#ccc;border-radius:4px;cursor:pointer;transition:all 0.15s';
                b.addEventListener('click', () => _showMatchup(m));
                matchBtns[_matchKey(m)] = b;
                matchRow.appendChild(b);
              });
              thCard.appendChild(matchRow);

              // Subscribe to Today's Games pill clicks so picking a game
              // there also activates the corresponding matchup here.
              _gameClickSubs.push(({ teams, reset }) => {
                if (reset) {
                  if (matchBtns['ALL']) matchBtns['ALL'].click();
                  return;
                }
                if (!teams) return;
                const sorted = [...teams].sort().join('@');
                const target = _todayMatchups.find(m => _kFromTeams(m.teams) === sorted);
                if (target) _showMatchup(target);
              });

              // Pitcher History → Team History sync.
              // The View-as label = the team being faced. So when a pitcher
              // is picked we click the button labeled with the pitcher's
              // OPPONENT (not their own team).
              //   { reset: true }            → click "All" chip; clear matchup
              //   { team, opp, fromToday }   → navigate to matchup + click
              //                                the opp's button
              //   { team }                   → within current matchup, click
              //                                the OTHER button (the opp)
              _pitcherToggleSubs.push((evt) => {
                if (!evt) return;
                if (evt.reset) {
                  if (matchBtns['ALL']) matchBtns['ALL'].click();
                  return;
                }
                const { team, opp, fromToday } = evt;
                if (!team) return;
                // Fast path: current matchup contains this pitcher's team.
                // The button to highlight is the OTHER team in the toggle.
                if (teamToggleRow.style.display !== 'none') {
                  const oppBtn = teamBtns.find(b =>
                    b.dataset.team && b.dataset.team !== team && b.style.display !== 'none'
                  );
                  const sameBtn = teamBtns.find(b => b.dataset.team === team);
                  if (oppBtn && sameBtn) { oppBtn.click(); return; }
                }
                // Navigate to a different matchup and flip view-as to opp.
                if (fromToday && opp) {
                  const sorted = [team, opp].sort().join('@');
                  const target = _todayMatchups.find(m => _kFromTeams(m.teams) === sorted);
                  if (target) {
                    _showMatchup(target);
                    const oppBtn = teamBtns.find(b => b.dataset.team === opp);
                    if (oppBtn) oppBtn.click();
                  }
                }
              });

              teamBtns.forEach(b => {
                b.style.cssText = 'padding:4px 12px;font-size:11px;font-weight:600;border:1px solid rgba(255,255,255,0.2);background:transparent;color:#ccc;border-radius:4px;cursor:pointer;transition:all 0.15s';
                b.addEventListener('click', () => {
                  // Button label = the team whose lineup we filter by. So
                  // clicking "MIN" shows plays vs MIN. The pitcher selected
                  // in Pitcher History is the one facing MIN (not from MIN).
                  _styleTeamBtns(b.dataset.team);
                  thSel.value = b.dataset.team;
                  renderTeamHistory(b.dataset.team);
                });
                teamToggleRow.appendChild(b);
              });
              thCard.appendChild(teamToggleRow);
            }

            const thCtrlRow = document.createElement('div');
            thCtrlRow.style.cssText = 'display:flex;align-items:center;gap:10px;padding:8px 4px 6px;flex-wrap:wrap';
            const thSelLabel = document.createElement('label');
            thSelLabel.textContent = 'Team:';
            thSelLabel.style.cssText = 'font-size:12px;color:#bbb;font-weight:600';
            const thSel = document.createElement('select');
            thSel.style.cssText = 'background:rgba(255,255,255,0.05);color:#fff;border:1px solid rgba(255,255,255,0.15);border-radius:4px;padding:6px 10px;font-size:12px;min-width:220px;cursor:pointer';
            let _thAddedSep = false;
            _teamList.forEach((t) => {
              if (!t.isToday && !_thAddedSep && _todayTeams.length > 0) {
                const sep = document.createElement('option');
                sep.disabled = true;
                sep.textContent = '── All Teams ──';
                thSel.appendChild(sep);
                _thAddedSep = true;
              }
              const opt = document.createElement('option');
              opt.value = t.key;
              opt.textContent = t.isToday ? `${t.key} (today)` : t.key;
              thSel.appendChild(opt);
            });
            thCtrlRow.appendChild(thSelLabel);
            thCtrlRow.appendChild(thSel);

            const thSummary = document.createElement('div');
            thSummary.style.cssText = 'margin-left:auto;font-size:12px;color:#999;display:flex;flex-direction:column;align-items:flex-end;gap:2px;line-height:1.4';
            thCtrlRow.appendChild(thSummary);

            const thFilterRow = document.createElement('div');
            thFilterRow.style.cssText = 'display:flex;gap:4px;padding:8px 4px 4px;flex-wrap:wrap;align-items:center';
            const TH_FILTERS = [
              { key: 'ALL',   label: 'All',     color: '#ccc'           },
              { key: 'PICK',  label: 'Picks',   color: '#a78bfa'        },
              { key: 'WATCH', label: 'Watch',   color: 'var(--yellow)'  },
              { key: 'PASS',  label: 'Passes',  color: '#888'           },
            ];
            let _thActiveFilter = 'ALL';
            const _thFilterBtns = {};
            TH_FILTERS.forEach(f => {
              const b = document.createElement('button');
              b.textContent = f.label;
              b.dataset.key = f.key;
              b.style.cssText = `padding:5px 12px;font-size:11px;font-weight:600;border:1px solid rgba(255,255,255,0.15);background:transparent;color:${f.color};border-radius:4px;cursor:pointer;transition:all 0.15s`;
              b.addEventListener('click', () => {
                _thActiveFilter = f.key;
                _thStyleBtns();
                renderTeamHistory(thSel.value);
              });
              _thFilterBtns[f.key] = b;
              thFilterRow.appendChild(b);
            });
            function _thStyleBtns() {
              TH_FILTERS.forEach(f => {
                const b = _thFilterBtns[f.key];
                const active = f.key === _thActiveFilter;
                b.style.background = active ? f.color : 'transparent';
                b.style.color = active ? '#0a0a0a' : f.color;
                b.style.borderColor = active ? f.color : 'rgba(255,255,255,0.15)';
              });
            }
            _thStyleBtns();

            // Direction filter — All / Overs / Unders. Sits in the same row
            // as the bucket filters, separated by a small gap. Defaults to
            // "All"; selecting a side narrows the table and summary tally
            // to that direction.
            const thDirDivider = document.createElement('div');
            thDirDivider.style.cssText = 'width:1px;height:18px;background:rgba(255,255,255,0.15);margin:0 6px';
            thFilterRow.appendChild(thDirDivider);
            const TH_DIRS = [
              { key: 'ALL',   label: 'All',    color: '#ccc'         },
              { key: 'OVER',  label: 'Overs',  color: 'var(--green)' },
              { key: 'UNDER', label: 'Unders', color: 'var(--red)'   },
            ];
            let _thActiveDir = 'ALL';
            const _thDirBtns = {};
            TH_DIRS.forEach(f => {
              const b = document.createElement('button');
              b.textContent = f.label;
              b.dataset.key = f.key;
              b.style.cssText = `padding:5px 12px;font-size:11px;font-weight:600;border:1px solid rgba(255,255,255,0.15);background:transparent;color:${f.color};border-radius:4px;cursor:pointer;transition:all 0.15s`;
              b.addEventListener('click', () => {
                _thActiveDir = f.key;
                _thStyleDirBtns();
                renderTeamHistory(thSel.value);
              });
              _thDirBtns[f.key] = b;
              thFilterRow.appendChild(b);
            });
            function _thStyleDirBtns() {
              TH_DIRS.forEach(f => {
                const b = _thDirBtns[f.key];
                const active = f.key === _thActiveDir;
                b.style.background = active ? f.color : 'transparent';
                b.style.color = active ? '#0a0a0a' : f.color;
                b.style.borderColor = active ? f.color : 'rgba(255,255,255,0.15)';
              });
            }
            _thStyleDirBtns();
            thCard.appendChild(thFilterRow);
            thCard.appendChild(thCtrlRow);

            // --- Month filter row ---
            // Buttons for "All" + every month present in the data, derived
            // from p.date (YYYY-MM-DD). Sorted chronologically so the chip
            // strip reads left-to-right April → today's month.
            const _monthsSet = new Set();
            for (const p of (data.props || [])) {
              if (p.market !== 'strikeouts') continue;
              const d = p.date || '';
              if (d.length >= 7) _monthsSet.add(d.slice(0, 7));   // "YYYY-MM"
            }
            const _months = [..._monthsSet].sort();
            let _thActiveMonth = 'ALL';
            const _monthLabel = (ym) => {
              const m = parseInt(ym.slice(5, 7), 10);
              const names = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
              return names[m - 1] || ym;
            };
            // Month buttons live inline with the Team dropdown (compact row).
            const thMonthRow = document.createElement('div');
            thMonthRow.style.cssText = 'display:inline-flex;gap:4px;flex-wrap:wrap;align-items:center';
            const thMonthLabel = document.createElement('span');
            thMonthLabel.style.cssText = 'font-size:12px;color:#bbb;font-weight:600;margin-left:8px;margin-right:2px';
            thMonthLabel.textContent = 'Month:';
            thMonthRow.appendChild(thMonthLabel);
            const _thMonthBtns = {};
            const _MTH_ACTIVE = 'padding:4px 10px;font-size:11px;font-weight:700;border:1px solid #a78bfa;background:#a78bfa;color:#0a0a0a;border-radius:4px;cursor:pointer;transition:all 0.15s';
            const _MTH_IDLE   = 'padding:4px 10px;font-size:11px;font-weight:500;border:1px solid rgba(255,255,255,0.15);background:rgba(255,255,255,0.04);color:#ccc;border-radius:4px;cursor:pointer;transition:all 0.15s';
            function _styleMonths() {
              for (const k in _thMonthBtns) {
                const b = _thMonthBtns[k];
                b.style.cssText = (k === _thActiveMonth) ? _MTH_ACTIVE : _MTH_IDLE;
              }
            }
            const allMth = document.createElement('button');
            allMth.textContent = 'All';
            allMth.addEventListener('click', () => {
              _thActiveMonth = 'ALL';
              _styleMonths();
              renderTeamHistory(thSel.value);
            });
            _thMonthBtns['ALL'] = allMth;
            thMonthRow.appendChild(allMth);
            for (const ym of _months) {
              const b = document.createElement('button');
              b.textContent = _monthLabel(ym);
              b.title = ym;
              b.addEventListener('click', () => {
                _thActiveMonth = ym;
                _styleMonths();
                renderTeamHistory(thSel.value);
              });
              _thMonthBtns[ym] = b;
              thMonthRow.appendChild(b);
            }
            _styleMonths();
            // Mount month row inside the Team dropdown row, before the
            // summary chip — keeps the controls in one compact line.
            thCtrlRow.insertBefore(thMonthRow, thSummary);

            const thTblWrap = document.createElement('div');
            thTblWrap.className = 'props-table-wrap';
            thCard.appendChild(thTblWrap);

            // Grading helpers are shared with Pitcher History above — no need
            // to redeclare. Aliases below keep call sites readable.
            const _thUnits = _phUnits;
            const _thGrade = _phGrade;
            const _thBucket = _phBucket;
            const _thOdds = _phOdds;

            let _thPage = 0, _thAllRows = [], _thAllEver = [];
            function renderTeamHistory(team) {
              const fromProps = (data.props || []).filter(p =>
                p.market === 'strikeouts' && _teamKey(p) === team
              );
              const fromToday = (data.todayProjections || []).filter(p =>
                p.market === 'strikeouts' && _teamKey(p) === team
              );
              // Dedupe on (pitcher, date) so a today projection doesn't double
              // up with a historical row for the same start.
              // Use displayName (same key Pitcher History uses) so a today
              // projection and a historical row for the same start can't
              // diverge on raw vs formatted player name.
              const _rowKey = (p) => `${displayName(p)}|${p.date}`;
              const seen = new Set(fromProps.map(_rowKey));
              const merged = [...fromProps];
              for (const p of fromToday) {
                const k = _rowKey(p);
                if (!seen.has(k)) { merged.push(p); seen.add(k); }
              }
              const _mergedSorted = merged.sort((a, b) =>
                (b.date || '').localeCompare(a.date || '')   // newest first
                || (a.player || '').localeCompare(b.player || '')
              );
              // Apply month filter — affects both the visible table and the
              // summary tally so the chip narrows scope consistently.
              const monthFiltered = (_thActiveMonth === 'ALL')
                ? _mergedSorted
                : _mergedSorted.filter(p => (p.date || '').slice(0, 7) === _thActiveMonth);
              // Direction filter: a row's direction is its picked side, or
              // for Watch/Pass rows the would_be_pick (intended side). Falls
              // back to comparing proj vs line if neither field is set.
              const _dirRow = (p) => {
                if (p.pick === 'OVER' || p.pick === 'UNDER') return p.pick;
                if (p.would_be_pick) return p.would_be_pick;
                return ((p.proj || 0) > (p.line || 0)) ? 'OVER' : 'UNDER';
              };
              const allEver = (_thActiveDir === 'ALL')
                ? monthFiltered
                : monthFiltered.filter(p => _dirRow(p) === _thActiveDir);

              const all = allEver.filter(p => {
                if (_thActiveFilter === 'ALL') return true;
                return _thBucket(p) === _thActiveFilter;
              });

              _thAllRows = all;
              _thAllEver = allEver;
              _thPage = 0;          // reset to first page on any filter/team change
              _thDraw();
            }

            // Render one 25-row page of the stashed Team History rows plus
            // Prev/Next controls. renderTeamHistory() resets to page 0 on any
            // filter/team change; the pager buttons bump _thPage and redraw.
            function _thDraw() {
              const all = _thAllRows, allEver = _thAllEver;
              thTblWrap.innerHTML = '';
              if (all.length === 0) {
                const e = document.createElement('div');
                e.style.cssText = 'padding:14px;color:#888;font-size:12px;font-style:italic';
                e.textContent = allEver.length === 0
                  ? 'No projections found vs this team.'
                  : `No ${_thActiveFilter.toLowerCase()}s vs this team.`;
                thTblWrap.appendChild(e);
                _thRenderSummary(allEver);
                return;
              }

              const TH_PAGE_SIZE = 25;
              const _thPages = Math.max(1, Math.ceil(all.length / TH_PAGE_SIZE));
              if (_thPage >= _thPages) _thPage = _thPages - 1;
              if (_thPage < 0) _thPage = 0;
              const _thStart = _thPage * TH_PAGE_SIZE;
              const _pageRows = all.slice(_thStart, _thStart + TH_PAGE_SIZE);

              const tbl = document.createElement('table');
              tbl.style.cssText = 'width:100%;border-collapse:collapse';
              const head = tbl.createTHead().insertRow();
              const headCols = [
                ['Date', 'left'], ['Pitcher', 'left'], ['Tm', 'center'],
                ['Bkt', 'center'], ['Dir', 'center'], ['Line', 'right'],
                ['Proj', 'right'], ['Edge', 'right'], ['pC%', 'right'],
                ['Actual', 'right'], ['Odds', 'right'],
                ['Result', 'center'], ['Units', 'right'],
              ];
              headCols.forEach(([lbl, al]) => {
                const th = document.createElement('th');
                th.textContent = lbl;
                th.style.cssText = `padding:6px 8px;text-align:${al};border-bottom:1px solid rgba(255,255,255,0.1);font-size:11px;color:#999`;
                head.appendChild(th);
              });
              const body = tbl.createTBody();

              for (const p of _pageRows) {
                const tr = body.insertRow();
                tr.style.borderBottom = '1px solid rgba(255,255,255,0.04)';
                const bkt = _thBucket(p);
                const dir = (p.pick === 'OVER' || p.pick === 'UNDER')
                  ? p.pick
                  : (p.would_be_pick || ((p.proj || 0) > (p.line || 0) ? 'OVER' : 'UNDER'));
                const grade = _thGrade(p);
                const odds = _thOdds(p);
                const sz = bkt === 'PICK' ? 1.0 : (bkt === 'WATCH' ? 1.0 : 0);
                const u = (grade === 'V')
                  ? 0
                  : ((bkt !== 'PASS' && grade != null)
                      ? _thUnits(odds, grade === 'W', sz)
                      : null);
                const edge = (p.proj != null && p.line != null) ? (p.proj - p.line).toFixed(1) : '—';
                const edgeStr = edge !== '—' ? (parseFloat(edge) > 0 ? '+' + edge : edge) : '—';
                const bktColor = bkt === 'PICK' ? '#a78bfa' : bkt === 'WATCH' ? 'var(--yellow)' : '#888';
                const dirColor = dir === 'OVER' ? 'var(--green)' : 'var(--red)';
                const resColor = grade === 'W' ? 'var(--green)' : grade === 'L' ? 'var(--red)' : '#888';
                const resLabel = grade === 'V' ? 'VOID' : (grade || '—');
                const uColor = u == null
                  ? '#888'
                  : (grade === 'V'
                      ? '#888'
                      : (bkt === 'WATCH'
                          ? 'var(--yellow)'
                          : (u > 0 ? 'var(--green)' : u < 0 ? 'var(--red)' : '#ccc')));
                const fmtOdds = (o) => o == null ? '—' : (o > 0 ? '+' + o : String(o));

                const cells = [
                  {v: p.date || '—', align:'left', color:'#ccc'},
                  {v: displayName(p), align:'left', color:'#fff', weight:'600'},
                  {v: p.team || '—', align:'center', color:'#999'},
                  {v: bkt, align:'center', color:bktColor, weight:'600'},
                  {v: dir === 'OVER' ? 'O' : 'U', align:'center', color:dirColor, weight:'600'},
                  {v: p.line != null ? String(p.line) : '—', align:'right'},
                  {v: p.proj != null ? p.proj.toFixed(1) : '—', align:'right'},
                  {v: edgeStr, align:'right', color: edge !== '—' && parseFloat(edge) > 0 ? 'var(--green)' : edge !== '—' && parseFloat(edge) < 0 ? 'var(--red)' : '#999'},
                  {v: p.pCover != null ? (p.pCover * 100).toFixed(1) + '%' : '—', align:'right'},
                  {v: p.actual != null ? String(p.actual) : '—', align:'right'},
                  {v: fmtOdds(odds), align:'right', color:'#ccc'},
                  {v: resLabel, align:'center', color:resColor, weight:'700'},
                  {v: grade === 'V' ? 'void' : (u == null ? '—' : (u >= 0 ? '+' : '') + u.toFixed(2) + 'u'), align:'right', color:uColor, weight:'600'},
                ];
                cells.forEach((c) => {
                  const td = tr.insertCell();
                  td.textContent = c.v;
                  td.style.cssText = `padding:5px 8px;text-align:${c.align};font-size:12px`;
                  if (c.color) td.style.color = c.color;
                  if (c.weight) td.style.fontWeight = c.weight;
                });
              }
              thTblWrap.appendChild(tbl);
              fitMLBTableToContainer(tbl);

              if (_thPages > 1) {
                const pager = document.createElement('div');
                pager.style.cssText = 'display:flex;align-items:center;justify-content:center;gap:12px;padding:8px;font-size:12px;color:#bbb';
                const mkBtn = (label, disabled, onClick) => {
                  const b = document.createElement('button');
                  b.textContent = label;
                  b.disabled = disabled;
                  b.style.cssText = `padding:4px 12px;border-radius:4px;border:1px solid rgba(255,255,255,0.15);background:${disabled ? 'rgba(255,255,255,0.03)' : 'rgba(255,255,255,0.08)'};color:${disabled ? '#555' : '#ddd'};cursor:${disabled ? 'default' : 'pointer'};font-size:12px`;
                  if (!disabled) b.addEventListener('click', onClick);
                  return b;
                };
                pager.appendChild(mkBtn('‹ Prev', _thPage <= 0, () => { _thPage--; _thDraw(); }));
                const info = document.createElement('span');
                info.textContent = `Page ${_thPage + 1} of ${_thPages} · ${all.length} rows`;
                pager.appendChild(info);
                pager.appendChild(mkBtn('Next ›', _thPage >= _thPages - 1, () => { _thPage++; _thDraw(); }));
                thTblWrap.appendChild(pager);
              }

              _thRenderSummary(allEver);
            }

            function _thRenderSummary(allEver) {
              let pw=0, pl=0, lw=0, ll=0, p_u=0, l_u=0, aw=0, al=0, a_u=0;
              for (const p of allEver) {
                const bkt = _thBucket(p);
                const grade = _thGrade(p);
                if (!grade || grade === 'V') continue;
                const odds = _thOdds(p);
                // All tier = every graded play (Picks + Watch + Pass).
                if (grade === 'W') aw++; else al++;
                a_u += _thUnits(odds, grade === 'W', 1.0);
                if (bkt === 'PICK') {
                  if (grade === 'W') pw++; else pl++;
                  p_u += _thUnits(odds, grade === 'W', 1.0);
                } else if (bkt === 'WATCH') {
                  if (grade === 'W') lw++; else ll++;
                  l_u += _thUnits(odds, grade === 'W', 1.0);
                }
              }
              const pickTotal = pw + pl, watchTotal = lw + ll, allTotal = aw + al;
              const parts = [];
              const pickColor  = '#a78bfa';
              const watchColor = 'var(--yellow)';
              const allColor   = '#ccc';
              if (allTotal > 0) {
                const wr = (aw/allTotal*100).toFixed(1);
                const u  = (a_u >= 0 ? '+' : '') + a_u.toFixed(2) + 'u';
                parts.push(`<span style="color:${allColor};font-weight:600">All ${aw}-${al} (${wr}%) ${u}</span>`);
              }
              if (pickTotal > 0) {
                const wr = (pw/pickTotal*100).toFixed(1);
                const u  = (p_u >= 0 ? '+' : '') + p_u.toFixed(2) + 'u';
                parts.push(`<span style="color:${pickColor};font-weight:600">Picks ${pw}-${pl} (${wr}%) ${u}</span>`);
              }
              if (watchTotal > 0) {
                const wr = (lw/watchTotal*100).toFixed(1);
                const u  = (l_u >= 0 ? '+' : '') + l_u.toFixed(2) + 'u';
                parts.push(`<span style="color:${watchColor};font-weight:600">Watch ${lw}-${ll} (${wr}%) ${u}</span>`);
              }
              if (parts.length === 0) parts.push('<span style="color:#888">No graded plays yet</span>');
              thSummary.innerHTML = parts.map(p => `<div>${p}</div>`).join('');
            }

            thSel.addEventListener('change', () => renderTeamHistory(thSel.value));
            renderTeamHistory(_teamList[0].key);
            el.appendChild(thCard);
          }

          // Matchup History no longer rendered as a standalone card — its
          // body lives inside the Picks card's Today/Yesterday slots above.

          // ── Read Record (backtest) ──
          // Walk every graded pick chronologically. For each one, compute
          // the verdict using ONLY history available before that date, then
          // compare to the actual result. This shows how the TAKE/PASS read
          // would have performed historically.
          (function buildReadRecord() {
            // ALL rows that can contribute to the broader lens — picks AND
            // watch-tier — in date order. Picks gate the verdict; watch
            // expands the broader same-dir cohort.
            const allGraded = (data.props || []).filter(p =>
              p.market === 'strikeouts'
              && (p.result === 'WIN' || p.result === 'LOSS')
              && p.opp
              && ((p.pick === 'OVER' || p.pick === 'UNDER') || isLean(p))
            ).sort((a, b) => (a.date || '').localeCompare(b.date || ''));
            if (allGraded.length === 0) return;

            // Running aggregates — updated as we walk forward.
            const _byOppDirP = {};       // opp → dir → {w,l,u}  (picks-only)
            const _byOppDirWide = {};    // opp → dir → {w,l,u}  (picks + watch, same dir)
            const _byPitDirP = {};       // pitKey → dir → {w,l,u}  (picks-only)
            const _byPitP    = {};       // pitKey → {w,l,u}        (picks both dirs)
            const _pkeyFor = (p) => `${displayName(p)}|${p.team || ''}`;
            const _dirOfRow = (p) => p.pick === 'OVER' || p.pick === 'UNDER'
              ? p.pick
              : p.would_be_pick;

            // Tally containers per verdict / per month.
            let takeW = 0, takeL = 0, takeU = 0;
            let passW = 0, passL = 0, passU = 0; // what PASS WOULD have done
            const byMonth = {}; // "YYYY-MM" → {takeW, takeL, takeU, passW, passL, passU}
            const rows = [];    // detail rows for the table

            function _unitsOf(p) {
              const won = p.result === 'WIN';
              const od = p.odds;
              if (od == null) return won ? 1 : -1;
              const o = Number(od);
              if (o > 0) return won ? o / 100 : -1;
              return won ? 1 : -Math.abs(o) / 100;
            }

            for (const p of allGraded) {
              const opp = p.opp;
              const dir = _dirOfRow(p);
              const pitKey = _pkeyFor(p);
              const isPickRow = p.pick === 'OVER' || p.pick === 'UNDER';
              const dirRec  = (_byOppDirP[opp]   && dir) ? _byOppDirP[opp][dir]   : null;
              const dirRecP = dirRec ? { w:dirRec.w, l:dirRec.l, u:dirRec.u } : null;
              const allRec  = (_byOppDirWide[opp] && dir) ? _byOppDirWide[opp][dir] : null;
              const allRecP = allRec ? { w:allRec.w, l:allRec.l, u:allRec.u } : null;
              const pitRec  = (_byPitDirP[pitKey] && dir) ? _byPitDirP[pitKey][dir] : null;
              const pitRecP = pitRec ? { w:pitRec.w, l:pitRec.l, u:pitRec.u } : null;
              const pitAll  = _byPitP[pitKey] ? { w:_byPitP[pitKey].w, l:_byPitP[pitKey].l, u:_byPitP[pitKey].u } : null;

              // Only PICK rows get a verdict assigned to the tally; watch
              // rows just contribute their result to the broader cohort.
              let verdict = null;
              if (isPickRow) {
                verdict = readVerdictFor({
                  dirRec: dirRecP, allRec: allRecP, pitRec: pitRecP, pitAllRec: pitAll,
                });
              }
              const u = _unitsOf(p);
              const won = p.result === 'WIN';
              const ym = (p.date || '').slice(0, 7);
              if (!byMonth[ym]) byMonth[ym] = { takeW:0, takeL:0, takeU:0, passW:0, passL:0, passU:0 };
              if (verdict === 'TAKE') {
                if (won) { takeW++; byMonth[ym].takeW++; } else { takeL++; byMonth[ym].takeL++; }
                takeU += u; byMonth[ym].takeU += u;
                rows.push({ p, verdict, u, won });
              } else if (verdict === 'PASS') {
                if (won) { passW++; byMonth[ym].passW++; } else { passL++; byMonth[ym].passL++; }
                passU += u; byMonth[ym].passU += u;
                rows.push({ p, verdict, u, won });
              }
              // watch rows (no verdict) only contribute to aggregates below.

              // Update aggregates so the NEXT row sees this one's result.
              // Widened same-dir map gets BOTH picks and watch.
              if (dir) {
                if (!_byOppDirWide[opp]) _byOppDirWide[opp] = { OVER:{w:0,l:0,u:0}, UNDER:{w:0,l:0,u:0} };
                const bw = _byOppDirWide[opp][dir];
                if (won) bw.w++; else bw.l++; bw.u += u;
              }
              // Picks-only maps stay narrow.
              if (isPickRow) {
                if (!_byOppDirP[opp]) _byOppDirP[opp] = { OVER:{w:0,l:0,u:0}, UNDER:{w:0,l:0,u:0} };
                const bd = _byOppDirP[opp][dir];
                if (won) bd.w++; else bd.l++; bd.u += u;
                if (!_byPitDirP[pitKey]) _byPitDirP[pitKey] = { OVER:{w:0,l:0,u:0}, UNDER:{w:0,l:0,u:0} };
                const pd = _byPitDirP[pitKey][dir];
                if (won) pd.w++; else pd.l++; pd.u += u;
                if (!_byPitP[pitKey]) _byPitP[pitKey] = { w:0, l:0, u:0 };
                const pb = _byPitP[pitKey];
                if (won) pb.w++; else pb.l++; pb.u += u;
              }
            }

            const rrCard = document.createElement('div');
            rrCard.className = 'card card-games';
            rrCard.style.marginBottom = '16px';
            rrCard.appendChild(Object.assign(document.createElement('div'), {
              className: 'card-title',
              textContent: `Read Model History Gate — shadow monitor (not live)`,
            }));

            // Summary row: TAKE / PASS totals.
            const sumRow = document.createElement('div');
            sumRow.style.cssText = 'display:flex;gap:18px;padding:10px 4px 6px;flex-wrap:wrap;font-size:13px';
            const takeN = takeW + takeL;
            const passN = passW + passL;
            const fmt = (w, l, u) => {
              const n = w + l;
              const wr = n > 0 ? (w/n*100).toFixed(1) + '%' : '—';
              const uS = (u >= 0 ? '+' : '') + u.toFixed(2) + 'u';
              return `${w}-${l} (${wr}) ${uS}`;
            };
            sumRow.innerHTML = `
              <div><span style="color:#bbb;font-weight:600">TAKE:</span>
                <span style="color:${takeU>=0?'var(--green)':'var(--red)'};font-weight:600">${fmt(takeW, takeL, takeU)}</span></div>
              <div><span style="color:#bbb;font-weight:600">PASS would-be:</span>
                <span style="color:${passU>=0?'var(--green)':'var(--red)'};font-weight:600">${fmt(passW, passL, passU)}</span></div>
              <div style="color:#888;font-size:12px;align-self:center">backtested across ${rows.length} graded picks</div>
            `;
            rrCard.appendChild(sumRow);

            // Month breakdown table.
            const months = Object.keys(byMonth).sort();
            if (months.length > 0) {
              const wrap = document.createElement('div');
              wrap.className = 'props-table-wrap';
              const tbl = document.createElement('table');
              tbl.style.cssText = 'width:100%;border-collapse:collapse;margin-top:6px';
              const hr = tbl.createTHead().insertRow();
              ['Month','TAKE Record','TAKE WR%','TAKE Units','PASS Record','PASS WR%','PASS Units'].forEach((h, i) => {
                const th = document.createElement('th');
                th.textContent = h;
                th.style.cssText = `padding:6px 8px;border-bottom:1px solid rgba(255,255,255,0.1);font-size:11px;color:#999;text-align:${i===0?'left':'right'}`;
                hr.appendChild(th);
              });
              const tb = tbl.createTBody();
              const monthName = (ym) => {
                const m = parseInt(ym.slice(5, 7), 10);
                const names = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
                return names[m - 1] + ' ' + ym.slice(0, 4);
              };
              for (const ym of months) {
                const r = byMonth[ym];
                const tN = r.takeW + r.takeL;
                const pN = r.passW + r.passL;
                const tWR = tN > 0 ? (r.takeW/tN*100).toFixed(1) + '%' : '—';
                const pWR = pN > 0 ? (r.passW/pN*100).toFixed(1) + '%' : '—';
                const tUStr = (r.takeU >= 0 ? '+' : '') + r.takeU.toFixed(2) + 'u';
                const pUStr = (r.passU >= 0 ? '+' : '') + r.passU.toFixed(2) + 'u';
                const tr = tb.insertRow();
                tr.style.borderBottom = '1px solid rgba(255,255,255,0.04)';
                const cells = [
                  { v: monthName(ym), align: 'left', color: '#ccc' },
                  { v: `${r.takeW}-${r.takeL}`, align: 'right' },
                  { v: tWR, align: 'right', color: tN > 0 ? (r.takeW/tN >= 0.55 ? 'var(--green)' : r.takeW/tN < 0.50 ? 'var(--red)' : '#ccc') : '#888' },
                  { v: tUStr, align: 'right', color: r.takeU >= 0 ? 'var(--green)' : 'var(--red)' },
                  { v: `${r.passW}-${r.passL}`, align: 'right' },
                  { v: pWR, align: 'right', color: pN > 0 ? (r.passW/pN >= 0.55 ? 'var(--green)' : r.passW/pN < 0.50 ? 'var(--red)' : '#ccc') : '#888' },
                  { v: pUStr, align: 'right', color: r.passU >= 0 ? 'var(--green)' : 'var(--red)' },
                ];
                cells.forEach(c => {
                  const td = tr.insertCell();
                  td.textContent = c.v;
                  td.style.cssText = `padding:5px 8px;font-size:12px;text-align:${c.align}`;
                  if (c.color) td.style.color = c.color;
                });
              }
              wrap.appendChild(tbl);
              rrCard.appendChild(wrap);
            }
            // --- Drill-down: search by verdict + month ---
            // Collapsed by default — the historical table is hundreds of
            // rows. User clicks the disclosure to expand.
            const drillToggleRow = document.createElement('div');
            drillToggleRow.style.cssText = 'border-top:1px solid rgba(255,255,255,0.06);margin-top:12px;padding:10px 4px 0';
            const drillToggleBtn = document.createElement('button');
            let _drillOpen = false;
            drillToggleBtn.style.cssText = 'background:none;border:none;color:#a78bfa;font-size:12px;font-weight:600;cursor:pointer;padding:4px 0;display:flex;align-items:center;gap:6px';
            const _setDrillTxt = () => {
              drillToggleBtn.textContent = (_drillOpen ? '▼ ' : '▶ ') + 'Drill into individual picks';
            };
            _setDrillTxt();
            drillToggleRow.appendChild(drillToggleBtn);
            rrCard.appendChild(drillToggleRow);

            const drillBody = document.createElement('div');
            drillBody.style.display = 'none';
            drillToggleBtn.addEventListener('click', () => {
              _drillOpen = !_drillOpen;
              _setDrillTxt();
              drillBody.style.display = _drillOpen ? '' : 'none';
            });

            const drillRow = document.createElement('div');
            drillRow.style.cssText = 'display:flex;gap:6px;padding:8px 4px 6px;flex-wrap:wrap;align-items:center';
            const drillLabel = document.createElement('span');
            drillLabel.style.cssText = 'font-size:11px;color:#bbb;font-weight:600;letter-spacing:0.06em;text-transform:uppercase;margin-right:6px';
            drillLabel.textContent = 'Drill:';
            drillRow.appendChild(drillLabel);

            const _RR_ACTIVE = 'padding:4px 12px;font-size:11px;font-weight:700;border:1px solid #a78bfa;background:#a78bfa;color:#0a0a0a;border-radius:4px;cursor:pointer';
            const _RR_IDLE = 'padding:4px 12px;font-size:11px;font-weight:500;border:1px solid rgba(255,255,255,0.15);background:rgba(255,255,255,0.04);color:#ccc;border-radius:4px;cursor:pointer';
            let _rrFilter = 'ALL';
            let _rrMonth = 'ALL';
            const RR_FILTERS = [
              { key: 'ALL',  label: 'All' },
              { key: 'TAKE', label: 'TAKE' },
              { key: 'PASS', label: 'PASS' },
            ];
            const _rrFilterBtns = {};
            RR_FILTERS.forEach(f => {
              const b = document.createElement('button');
              b.textContent = f.label;
              b.style.cssText = _rrFilter === f.key ? _RR_ACTIVE : _RR_IDLE;
              b.addEventListener('click', () => { _rrFilter = f.key; _styleRR(); _renderDrill(); });
              _rrFilterBtns[f.key] = b;
              drillRow.appendChild(b);
            });

            // Month chips — derived from the same `byMonth` keys already
            // computed above. "All" first, then chronological months.
            const monthDivider = document.createElement('div');
            monthDivider.style.cssText = 'width:1px;height:18px;background:rgba(255,255,255,0.15);margin:0 4px';
            drillRow.appendChild(monthDivider);
            const monthLabel = document.createElement('span');
            monthLabel.style.cssText = 'font-size:11px;color:#bbb;font-weight:600';
            monthLabel.textContent = 'Month:';
            drillRow.appendChild(monthLabel);
            const _rrMonthBtns = {};
            const _rrMonthName = (ym) => {
              const m = parseInt(ym.slice(5, 7), 10);
              return ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][m - 1] || ym;
            };
            const allMonths = Object.keys(byMonth).sort();
            const allMonthBtn = document.createElement('button');
            allMonthBtn.textContent = 'All';
            allMonthBtn.style.cssText = _RR_ACTIVE;
            allMonthBtn.addEventListener('click', () => { _rrMonth = 'ALL'; _styleRR(); _renderDrill(); });
            _rrMonthBtns['ALL'] = allMonthBtn;
            drillRow.appendChild(allMonthBtn);
            for (const ym of allMonths) {
              const b = document.createElement('button');
              b.textContent = _rrMonthName(ym);
              b.title = ym;
              b.style.cssText = _RR_IDLE;
              b.addEventListener('click', () => { _rrMonth = ym; _styleRR(); _renderDrill(); });
              _rrMonthBtns[ym] = b;
              drillRow.appendChild(b);
            }
            function _styleRR() {
              for (const k in _rrFilterBtns) _rrFilterBtns[k].style.cssText = (k === _rrFilter) ? _RR_ACTIVE : _RR_IDLE;
              for (const k in _rrMonthBtns) _rrMonthBtns[k].style.cssText = (k === _rrMonth) ? _RR_ACTIVE : _RR_IDLE;
            }

            drillBody.appendChild(drillRow);

            // Tally line + drill-down table mount — inside collapsible body.
            const drillSummary = document.createElement('div');
            drillSummary.style.cssText = 'padding:4px 4px 8px;font-size:12px;color:#999';
            drillBody.appendChild(drillSummary);
            const drillWrap = document.createElement('div');
            drillWrap.className = 'props-table-wrap';
            drillBody.appendChild(drillWrap);
            rrCard.appendChild(drillBody);

            function _renderDrill() {
              const filtered = rows.filter(r => {
                if (_rrFilter !== 'ALL' && r.verdict !== _rrFilter) return false;
                if (_rrMonth !== 'ALL' && (r.p.date || '').slice(0, 7) !== _rrMonth) return false;
                return true;
              });
              // Mini summary
              let w = 0, l = 0, uTotal = 0;
              for (const r of filtered) {
                if (r.won) w++; else l++;
                uTotal += r.u;
              }
              const wr = (w + l) > 0 ? (w / (w + l) * 100).toFixed(1) + '%' : '—';
              const uStr = (uTotal >= 0 ? '+' : '') + uTotal.toFixed(2) + 'u';
              const uColor = uTotal >= 0 ? 'var(--green)' : 'var(--red)';
              drillSummary.innerHTML = `Showing <strong style="color:#ccc">${filtered.length}</strong> picks · <strong>${w}-${l}</strong> (${wr}) <span style="color:${uColor};font-weight:600">${uStr}</span>`;

              drillWrap.innerHTML = '';
              if (filtered.length === 0) {
                const empty = document.createElement('div');
                empty.style.cssText = 'padding:14px;color:#888;font-size:12px;font-style:italic';
                empty.textContent = 'No picks match the current filters.';
                drillWrap.appendChild(empty);
                return;
              }
              const tbl = document.createElement('table');
              tbl.style.cssText = 'width:100%;border-collapse:collapse;margin-top:4px';
              const hr = tbl.createTHead().insertRow();
              ['Date','Pitcher','Tm','Opp','Dir','Line','Proj','pC%','Actual','Odds','Verdict','Result','Units'].forEach((h, i) => {
                const th = document.createElement('th');
                th.textContent = h;
                th.style.cssText = `padding:6px 8px;border-bottom:1px solid rgba(255,255,255,0.1);font-size:11px;color:#999;text-align:${i < 4 ? 'left' : (i >= 10 ? 'center' : 'right')}`;
                hr.appendChild(th);
              });
              const tb = tbl.createTBody();
              // Newest first so the latest result is up top.
              const sortedRows = [...filtered].sort((a, b) =>
                (b.p.date || '').localeCompare(a.p.date || '')
              );
              for (const r of sortedRows) {
                const p = r.p;
                const tr = tb.insertRow();
                tr.style.borderBottom = '1px solid rgba(255,255,255,0.04)';
                const fmtOdds = (o) => o == null ? '—' : (o > 0 ? '+' + o : String(o));
                const verdictColor = r.verdict === 'TAKE' ? 'var(--green)' : 'var(--red)';
                const dirColor = p.pick === 'OVER' ? 'var(--green)' : 'var(--red)';
                const resColor = p.result === 'WIN' ? 'var(--green)' : 'var(--red)';
                const uColorRow = r.u >= 0 ? 'var(--green)' : 'var(--red)';
                const cells = [
                  { v: p.date || '—', align: 'left', color: '#ccc' },
                  { v: displayName(p), align: 'left', color: '#fff', weight: '600' },
                  { v: p.team || '—', align: 'left', color: '#999' },
                  { v: p.opp || '—', align: 'left', color: '#999' },
                  { v: p.pick === 'OVER' ? 'O' : 'U', align: 'right', color: dirColor, weight: '600' },
                  { v: p.line != null ? String(p.line) : '—', align: 'right' },
                  { v: p.proj != null ? p.proj.toFixed(1) : '—', align: 'right' },
                  { v: p.pCover != null ? (p.pCover * 100).toFixed(1) + '%' : '—', align: 'right' },
                  { v: p.actual != null ? String(p.actual) : '—', align: 'right' },
                  { v: fmtOdds(p.odds), align: 'right', color: '#ccc' },
                  { v: r.verdict, align: 'center', color: verdictColor, weight: '700' },
                  { v: p.result === 'WIN' ? 'W' : 'L', align: 'center', color: resColor, weight: '700' },
                  { v: (r.u >= 0 ? '+' : '') + r.u.toFixed(2) + 'u', align: 'right', color: uColorRow, weight: '600' },
                ];
                cells.forEach(c => {
                  const td = tr.insertCell();
                  td.textContent = c.v;
                  td.style.cssText = `padding:5px 8px;font-size:12px;text-align:${c.align}`;
                  if (c.color) td.style.color = c.color;
                  if (c.weight) td.style.fontWeight = c.weight;
                });
              }
              drillWrap.appendChild(tbl);
            }
            _renderDrill();

            // Caption.
            const cap = document.createElement('div');
            cap.style.cssText = 'padding:8px 4px;color:#888;font-size:11px;font-style:italic;line-height:1.5';
            cap.innerHTML = `
              Each pick's verdict is computed using only history before that date.<br>
              <strong style="color:var(--green)">TAKE</strong> = actually betting these picks (1u each).<br>
              <strong style="color:var(--red)">PASS would-be</strong> = picks the read flagged to skip — what they would have done if bet anyway.
            `;
            rrCard.appendChild(cap);

            _readRecordCard = rrCard;
          })();

          // ── EV Gate (backtest) — shadow monitor (not live) ──
          // Price-aware second gate layered on the pCover threshold. For each
          // graded pick, convert the offered American price to a breakeven
          // probability and compare to pCover. Verdict is SELF-CONTAINED per
          // pick (no walk-forward cohort needed). Mirrors _stamp_ev_verdicts in
          // run_daily.py (if you change one, change both). Shadow only — never
          // alters which picks are bet.
          (function buildEvRecord() {
            // EV_GATE_MARGIN mirrors defaults.py (default 0.0). Kept inline so
            // the dashboard needs no extra data plumbing; if the Python default
            // changes, update here too.
            const EV_GATE_MARGIN = 0.0;
            // Breakeven prob of an integer American price — sign-aware, from
            // odds directly (NOT from to_win_1u, which is stake-to-win-1u).
            const _breakeven = (odds) => {
              if (odds == null) return null;
              const o = Number(odds);
              if (!isFinite(o) || o === 0) return null;
              return o < 0 ? Math.abs(o) / (Math.abs(o) + 100) : 100 / (o + 100);
            };
            // Per-pick verdict: default TAKE, PASS only when clearly -EV.
            const evVerdictFor = (p) => {
              const be = _breakeven(p.odds);
              const pc = p.pCover;
              if (be == null || pc == null) return 'TAKE';
              return (Number(pc) < be - EV_GATE_MARGIN) ? 'PASS' : 'TAKE';
            };

            // Only actionable OVER/UNDER strikeouts picks, graded, in date order.
            const graded = (data.props || []).filter(p =>
              p.market === 'strikeouts'
              && (p.result === 'WIN' || p.result === 'LOSS')
              && (p.pick === 'OVER' || p.pick === 'UNDER')
            ).sort((a, b) => (a.date || '').localeCompare(b.date || ''));
            if (graded.length === 0) return;

            const _unitsOf = (p) => {
              const won = p.result === 'WIN';
              const od = p.odds;
              if (od == null) return won ? 1 : -1;
              const o = Number(od);
              if (o > 0) return won ? o / 100 : -1;
              return won ? 1 : -Math.abs(o) / 100;
            };

            let takeW = 0, takeL = 0, takeU = 0;
            let passW = 0, passL = 0, passU = 0; // what PASS WOULD have done
            const byMonth = {};
            const rows = [];
            for (const p of graded) {
              const verdict = evVerdictFor(p);
              const u = _unitsOf(p);
              const won = p.result === 'WIN';
              const ym = (p.date || '').slice(0, 7);
              if (!byMonth[ym]) byMonth[ym] = { takeW:0, takeL:0, takeU:0, passW:0, passL:0, passU:0 };
              if (verdict === 'TAKE') {
                if (won) { takeW++; byMonth[ym].takeW++; } else { takeL++; byMonth[ym].takeL++; }
                takeU += u; byMonth[ym].takeU += u;
              } else {
                if (won) { passW++; byMonth[ym].passW++; } else { passL++; byMonth[ym].passL++; }
                passU += u; byMonth[ym].passU += u;
              }
              rows.push({ p, verdict, u, won, be: _breakeven(p.odds) });
            }

            const evCard = document.createElement('div');
            evCard.className = 'card card-games';
            evCard.style.marginBottom = '16px';
            evCard.appendChild(Object.assign(document.createElement('div'), {
              className: 'card-title',
              textContent: `EV Gate — shadow monitor (not live)`,
            }));

            const sumRow = document.createElement('div');
            sumRow.style.cssText = 'display:flex;gap:18px;padding:10px 4px 6px;flex-wrap:wrap;font-size:13px';
            const fmt = (w, l, u) => {
              const n = w + l;
              const wr = n > 0 ? (w/n*100).toFixed(1) + '%' : '—';
              const uS = (u >= 0 ? '+' : '') + u.toFixed(2) + 'u';
              return `${w}-${l} (${wr}) ${uS}`;
            };
            sumRow.innerHTML = `
              <div><span style="color:#bbb;font-weight:600">TAKE (+EV):</span>
                <span style="color:${takeU>=0?'var(--green)':'var(--red)'};font-weight:600">${fmt(takeW, takeL, takeU)}</span></div>
              <div><span style="color:#bbb;font-weight:600">PASS would-be (−EV):</span>
                <span style="color:${passU>=0?'var(--green)':'var(--red)'};font-weight:600">${fmt(passW, passL, passU)}</span></div>
              <div style="color:#888;font-size:12px;align-self:center">backtested across ${rows.length} graded picks</div>
            `;
            evCard.appendChild(sumRow);

            const months = Object.keys(byMonth).sort();
            if (months.length > 0) {
              const wrap = document.createElement('div');
              wrap.className = 'props-table-wrap';
              const tbl = document.createElement('table');
              tbl.style.cssText = 'width:100%;border-collapse:collapse;margin-top:6px';
              const hr = tbl.createTHead().insertRow();
              ['Month','TAKE Record','TAKE WR%','TAKE Units','PASS Record','PASS WR%','PASS Units'].forEach((h, i) => {
                const th = document.createElement('th');
                th.textContent = h;
                th.style.cssText = `padding:6px 8px;border-bottom:1px solid rgba(255,255,255,0.1);font-size:11px;color:#999;text-align:${i===0?'left':'right'}`;
                hr.appendChild(th);
              });
              const tb = tbl.createTBody();
              const monthName = (ym) => {
                const m = parseInt(ym.slice(5, 7), 10);
                const names = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
                return names[m - 1] + ' ' + ym.slice(0, 4);
              };
              for (const ym of months) {
                const r = byMonth[ym];
                const tN = r.takeW + r.takeL;
                const pN = r.passW + r.passL;
                const tWR = tN > 0 ? (r.takeW/tN*100).toFixed(1) + '%' : '—';
                const pWR = pN > 0 ? (r.passW/pN*100).toFixed(1) + '%' : '—';
                const tUStr = (r.takeU >= 0 ? '+' : '') + r.takeU.toFixed(2) + 'u';
                const pUStr = (r.passU >= 0 ? '+' : '') + r.passU.toFixed(2) + 'u';
                const tr = tb.insertRow();
                tr.style.borderBottom = '1px solid rgba(255,255,255,0.04)';
                const cells = [
                  { v: monthName(ym), align: 'left', color: '#ccc' },
                  { v: `${r.takeW}-${r.takeL}`, align: 'right' },
                  { v: tWR, align: 'right', color: tN > 0 ? (r.takeW/tN >= 0.55 ? 'var(--green)' : r.takeW/tN < 0.50 ? 'var(--red)' : '#ccc') : '#888' },
                  { v: tUStr, align: 'right', color: r.takeU >= 0 ? 'var(--green)' : 'var(--red)' },
                  { v: `${r.passW}-${r.passL}`, align: 'right' },
                  { v: pWR, align: 'right', color: pN > 0 ? (r.passW/pN >= 0.55 ? 'var(--green)' : r.passW/pN < 0.50 ? 'var(--red)' : '#ccc') : '#888' },
                  { v: pUStr, align: 'right', color: r.passU >= 0 ? 'var(--green)' : 'var(--red)' },
                ];
                cells.forEach(c => {
                  const td = tr.insertCell();
                  td.textContent = c.v;
                  td.style.cssText = `padding:5px 8px;font-size:12px;text-align:${c.align}`;
                  if (c.color) td.style.color = c.color;
                });
              }
              wrap.appendChild(tbl);
              evCard.appendChild(wrap);
            }

            // --- All picks: our pCover / our line vs Vegas line / Vegas pCover ---
            // Collapsed by default. Lists every actionable pick (graded AND
            // pending today) with the model side-by-side against the market:
            //   our pCover  = model P(cover)
            //   our line    = fair American odds implied by our pCover (no vig)
            //   Vegas line  = the offered American price
            //   Vegas pCover= breakeven prob of that price (vig included)
            //   Δ           = our pCover − Vegas pCover (positive => +EV edge)
            const _probToAmerican = (p) => {
              if (p == null || p <= 0 || p >= 1) return null;
              return p >= 0.5 ? Math.round(-100 * p / (1 - p)) : Math.round(100 * (1 - p) / p);
            };
            const _fmtOdds = (o) => o == null ? '—' : (o > 0 ? '+' : '') + o;
            const _fmtPct = (x) => x == null ? '—' : (x * 100).toFixed(1) + '%';

            const allPicks = (data.props || []).filter(p =>
              p.market === 'strikeouts'
              && (p.pick === 'OVER' || p.pick === 'UNDER')
              && p.pCover != null && p.odds != null
            ).sort((a, b) => (b.date || '').localeCompare(a.date || '')
                          || (a.player || '').localeCompare(b.player || ''));

            if (allPicks.length) {
              // Precompute one record per pick with both display strings and raw
              // sort values, so filtering/sorting is cheap and re-render is just
              // a tbody rebuild.
              const apData = allPicks.map(p => {
                const pc = Number(p.pCover);
                const be = _breakeven(p.odds);
                const ourLine = _probToAmerican(pc);
                const vegasLine = Number(p.odds);
                const edgePP = (be != null) ? (pc - be) * 100 : null;
                const verdict = evVerdictFor(p);
                const dir = p.pick === 'OVER' ? 'o' : 'u';
                const res = (p.result === 'WIN' || p.result === 'LOSS') ? p.result[0] : '·';
                const won = p.result === 'WIN' ? true : p.result === 'LOSS' ? false : null;
                return { date: p.date || '', player: p.player || '', pickStr: `${dir}${p.line}`,
                         lineNum: Number(p.line), proj: p.proj == null ? null : Number(p.proj),
                         actual: p.actual == null ? null : Number(p.actual),
                         pc, ourLine, vegasLine, be, edgePP, verdict, res,
                         dirFull: p.pick, won, u: won === null ? 0 : _unitsOf(p),
                         week: p.date ? getWeekStart(p.date) : '' };
              });

              // Column definitions: label, alignment, sort type/accessor, and a
              // cell renderer returning {v, color}.
              const AP_COLS = [
                { key:'date',      label:'Date',         align:'left',  type:'str', val:r=>r.date,      cell:r=>({v:r.date,    color:'#bbb'}) },
                { key:'player',    label:'Player',       align:'left',  type:'str', val:r=>r.player,    cell:r=>({v:r.player,  color:'#ddd'}) },
                { key:'pick',      label:'Pick',         align:'left',  type:'num', val:r=>r.lineNum,   cell:r=>({v:r.pickStr, color:'#bbb'}) },
                { key:'proj',      label:'Proj',         align:'right', type:'num', val:r=>r.proj,      cell:r=>({v:r.proj==null?'—':r.proj.toFixed(1), color:'#9ad'}) },
                { key:'actual',    label:'Actual',       align:'right', type:'num', val:r=>r.actual,    cell:r=>({v:r.actual==null?'·':String(r.actual), color:r.actual==null?'#888':'#ddd'}) },
                { key:'pc',        label:'Our pCover',   align:'right', type:'num', val:r=>r.pc,        cell:r=>({v:_fmtPct(r.pc),       color:'#ddd'}) },
                { key:'ourLine',   label:'Our line',     align:'right', type:'num', val:r=>r.ourLine,   cell:r=>({v:_fmtOdds(r.ourLine), color:'#9ad'}) },
                { key:'vegasLine', label:'Vegas line',   align:'right', type:'num', val:r=>r.vegasLine, cell:r=>({v:_fmtOdds(r.vegasLine),color:'#ddd'}) },
                { key:'be',        label:'Vegas pCover', align:'right', type:'num', val:r=>r.be,        cell:r=>({v:_fmtPct(r.be),       color:'#caa'}) },
                { key:'edge',      label:'Δ',            align:'right', type:'num', val:r=>r.edgePP,    cell:r=>({v:r.edgePP==null?'—':(r.edgePP>=0?'+':'')+r.edgePP.toFixed(1)+'pp', color:r.edgePP==null?'#888':(r.edgePP>=0?'var(--green)':'var(--red)')}) },
                { key:'verdict',   label:'Verdict',      align:'right', type:'str', val:r=>r.verdict,   cell:r=>({v:r.verdict, color:r.verdict==='TAKE'?'var(--green)':'var(--red)'}) },
                { key:'result',    label:'Result',       align:'right', type:'str', val:r=>r.res,       cell:r=>({v:r.res, color:r.res==='W'?'var(--green)':r.res==='L'?'var(--red)':'#888'}) },
              ];

              // Sort + filter state. Default: date descending (most recent first).
              let _apSortKey = 'date', _apSortDir = -1; // 1 asc, -1 desc
              let _fVerdict = 'ALL', _fResult = 'ALL', _fEdge = 'ALL', _fSearch = '';
              let _fDir = 'ALL', _fWeek = 'ALL', _fDay = 'ALL';

              // Distinct weeks (Mon-start) and days present, most-recent first.
              const _fmtWeekLabel = (mon) => {
                if (!mon) return mon;
                const end = getWeekEnd(mon);
                const mn = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
                const a = new Date(mon + 'T00:00:00Z'), b = new Date(end + 'T00:00:00Z');
                const aM = mn[a.getUTCMonth()], bM = mn[b.getUTCMonth()];
                return aM === bM ? `${aM} ${a.getUTCDate()}–${b.getUTCDate()}`
                                 : `${aM} ${a.getUTCDate()}–${bM} ${b.getUTCDate()}`;
              };
              const _allWeeks = [...new Set(apData.map(r => r.week).filter(Boolean))].sort().reverse();
              const _daysForWeek = (wk) => [...new Set(apData
                .filter(r => wk === 'ALL' || r.week === wk)
                .map(r => r.date).filter(Boolean))].sort().reverse();

              const apToggleRow = document.createElement('div');
              apToggleRow.style.cssText = 'border-top:1px solid rgba(255,255,255,0.06);margin-top:12px;padding:10px 4px 0';
              const apBtn = document.createElement('button');
              let _apOpen = false;
              apBtn.style.cssText = 'background:none;border:none;color:#a78bfa;font-size:12px;font-weight:600;cursor:pointer;padding:4px 0;display:flex;align-items:center;gap:6px';
              const _setApTxt = () => { apBtn.textContent = (_apOpen ? '▼ ' : '▶ ') + `All picks — our vs Vegas (${allPicks.length})`; };
              _setApTxt();
              apToggleRow.appendChild(apBtn);
              evCard.appendChild(apToggleRow);

              const apBody = document.createElement('div');
              apBody.style.display = 'none';
              apBtn.addEventListener('click', () => { _apOpen = !_apOpen; _setApTxt(); apBody.style.display = _apOpen ? '' : 'none'; });

              // --- Filter bar: chip groups + player search ---
              const _AP_ACTIVE = 'padding:3px 9px;font-size:11px;font-weight:700;border:1px solid #a78bfa;background:#a78bfa;color:#0a0a0a;border-radius:4px;cursor:pointer';
              const _AP_IDLE   = 'padding:3px 9px;font-size:11px;font-weight:500;border:1px solid rgba(255,255,255,0.15);background:rgba(255,255,255,0.04);color:#ccc;border-radius:4px;cursor:pointer';
              const _chipGroup = (label, opts, getVal, setVal) => {
                const wrap = document.createElement('div');
                wrap.style.cssText = 'display:flex;gap:4px;align-items:center';
                const lab = document.createElement('span');
                lab.textContent = label; lab.style.cssText = 'font-size:11px;color:#bbb;font-weight:600;margin-right:2px';
                wrap.appendChild(lab);
                const btns = {};
                opts.forEach(o => {
                  const b = document.createElement('button');
                  b.textContent = o.label;
                  b.style.cssText = getVal() === o.key ? _AP_ACTIVE : _AP_IDLE;
                  b.addEventListener('click', () => {
                    setVal(o.key);
                    for (const k in btns) btns[k].style.cssText = (k === o.key) ? _AP_ACTIVE : _AP_IDLE;
                    _renderApRows();
                  });
                  btns[o.key] = b; wrap.appendChild(b);
                });
                return wrap;
              };

              const apFilterBar = document.createElement('div');
              apFilterBar.style.cssText = 'display:flex;gap:14px;flex-wrap:wrap;align-items:center;padding:10px 4px 4px';
              apFilterBar.appendChild(_chipGroup('Verdict', [{key:'ALL',label:'All'},{key:'TAKE',label:'TAKE'},{key:'PASS',label:'PASS'}], () => _fVerdict, v => _fVerdict = v));
              apFilterBar.appendChild(_chipGroup('Result',  [{key:'ALL',label:'All'},{key:'W',label:'Win'},{key:'L',label:'Loss'},{key:'P',label:'Pending'}], () => _fResult, v => _fResult = v));
              apFilterBar.appendChild(_chipGroup('Edge',    [{key:'ALL',label:'All'},{key:'POS',label:'+EV'},{key:'NEG',label:'−EV'}], () => _fEdge, v => _fEdge = v));
              apFilterBar.appendChild(_chipGroup('Side',    [{key:'ALL',label:'All'},{key:'OVER',label:'Over'},{key:'UNDER',label:'Under'}], () => _fDir, v => _fDir = v));

              // Week + Day dropdowns. Selecting a week narrows the Day options to
              // that week (and resets Day to All).
              const _selStyle = 'padding:3px 8px;font-size:11px;border-radius:4px;border:1px solid rgba(255,255,255,0.15);background:rgba(255,255,255,0.04);color:#fff;outline:none;cursor:pointer';
              const _mkSelWrap = (label, sel) => {
                const w = document.createElement('div');
                w.style.cssText = 'display:flex;gap:4px;align-items:center';
                const lab = document.createElement('span');
                lab.textContent = label; lab.style.cssText = 'font-size:11px;color:#bbb;font-weight:600;margin-right:2px';
                w.appendChild(lab); w.appendChild(sel); return w;
              };
              const weekSel = document.createElement('select');
              weekSel.style.cssText = _selStyle;
              const _fillWeekOpts = () => {
                weekSel.innerHTML = '';
                weekSel.appendChild(new Option('All weeks', 'ALL'));
                _allWeeks.forEach(wk => weekSel.appendChild(new Option(_fmtWeekLabel(wk), wk)));
              };
              _fillWeekOpts();
              const daySel = document.createElement('select');
              daySel.style.cssText = _selStyle;
              const _fillDayOpts = () => {
                daySel.innerHTML = '';
                daySel.appendChild(new Option('All days', 'ALL'));
                _daysForWeek(_fWeek).forEach(d => daySel.appendChild(new Option(d, d)));
                daySel.value = _fDay;
              };
              _fillDayOpts();
              weekSel.addEventListener('change', () => {
                _fWeek = weekSel.value; _fDay = 'ALL'; _fillDayOpts(); _renderApRows();
              });
              daySel.addEventListener('change', () => { _fDay = daySel.value; _renderApRows(); });
              apFilterBar.appendChild(_mkSelWrap('Week', weekSel));
              apFilterBar.appendChild(_mkSelWrap('Day', daySel));

              const srch = document.createElement('input');
              srch.type = 'text'; srch.placeholder = 'player…';
              srch.style.cssText = 'padding:3px 8px;font-size:11px;border-radius:4px;border:1px solid rgba(255,255,255,0.15);background:rgba(255,255,255,0.04);color:#fff;outline:none;width:120px';
              srch.addEventListener('input', () => { _fSearch = srch.value; _renderApRows(); });
              apFilterBar.appendChild(srch);
              const apCount = document.createElement('span');
              apCount.style.cssText = 'font-size:11px;color:#888;margin-left:auto';
              apFilterBar.appendChild(apCount);
              apBody.appendChild(apFilterBar);

              // Over vs Under W-L summary for the CURRENT filtered set (graded
              // rows only). Updated by _renderApRows.
              const apOuSummary = document.createElement('div');
              apOuSummary.style.cssText = 'display:flex;gap:20px;flex-wrap:wrap;padding:4px 4px 8px;font-size:12px';
              apBody.appendChild(apOuSummary);

              // --- Table ---
              const apWrap = document.createElement('div');
              apWrap.className = 'props-table-wrap';
              apWrap.style.cssText = 'max-height:420px;overflow:auto;margin-top:4px';
              const apTbl = document.createElement('table');
              apTbl.style.cssText = 'width:100%;border-collapse:collapse';
              const apHead = apTbl.createTHead().insertRow();
              AP_COLS.forEach(c => {
                const th = document.createElement('th');
                th.dataset.key = c.key;
                th.style.cssText = `position:sticky;top:0;background:#15151c;padding:6px 8px;border-bottom:1px solid rgba(255,255,255,0.1);font-size:11px;color:#999;text-align:${c.align};white-space:nowrap;cursor:pointer;user-select:none`;
                th.addEventListener('click', () => {
                  if (_apSortKey === c.key) { _apSortDir = -_apSortDir; }
                  else { _apSortKey = c.key; _apSortDir = (c.type === 'num') ? -1 : 1; }
                  _updateApHeaders(); _renderApRows();
                });
                apHead.appendChild(th);
              });
              const apTb = apTbl.createTBody();
              apWrap.appendChild(apTbl);
              apBody.appendChild(apWrap);
              evCard.appendChild(apBody);

              function _updateApHeaders() {
                [...apHead.children].forEach(th => {
                  const c = AP_COLS.find(x => x.key === th.dataset.key);
                  const active = th.dataset.key === _apSortKey;
                  th.textContent = c.label + (active ? (_apSortDir === 1 ? ' ▲' : ' ▼') : '');
                  th.style.color = active ? '#a78bfa' : '#999';
                });
              }

              function _renderApRows() {
                const sLower = _fSearch.trim().toLowerCase();
                let rows = apData.filter(r => {
                  if (_fVerdict !== 'ALL' && r.verdict !== _fVerdict) return false;
                  if (_fResult === 'W' && r.res !== 'W') return false;
                  if (_fResult === 'L' && r.res !== 'L') return false;
                  if (_fResult === 'P' && r.res !== '·') return false;
                  if (_fEdge === 'POS' && !(r.edgePP != null && r.edgePP >= 0)) return false;
                  if (_fEdge === 'NEG' && !(r.edgePP != null && r.edgePP < 0)) return false;
                  if (_fDir !== 'ALL' && r.dirFull !== _fDir) return false;
                  if (_fWeek !== 'ALL' && r.week !== _fWeek) return false;
                  if (_fDay !== 'ALL' && r.date !== _fDay) return false;
                  if (sLower && !r.player.toLowerCase().includes(sLower)) return false;
                  return true;
                });
                const col = AP_COLS.find(c => c.key === _apSortKey);
                rows.sort((a, b) => {
                  let va = col.val(a), vb = col.val(b);
                  if (col.type === 'num') {
                    va = (va == null || isNaN(va)) ? -Infinity : va;
                    vb = (vb == null || isNaN(vb)) ? -Infinity : vb;
                    return (va - vb) * _apSortDir;
                  }
                  return String(va).localeCompare(String(vb)) * _apSortDir;
                });
                apTb.textContent = '';
                for (const r of rows) {
                  const tr = apTb.insertRow();
                  tr.style.borderBottom = '1px solid rgba(255,255,255,0.04)';
                  AP_COLS.forEach(c => {
                    const cd = c.cell(r);
                    const td = tr.insertCell();
                    td.textContent = cd.v;
                    td.style.cssText = `padding:4px 8px;font-size:12px;text-align:${c.align};white-space:nowrap`;
                    if (cd.color) td.style.color = cd.color;
                  });
                }
                apCount.textContent = `${rows.length} of ${apData.length}`;

                // Over vs Under W-L (+units) for the graded rows in this view.
                const acc = { OVER: { w:0, l:0, u:0 }, UNDER: { w:0, l:0, u:0 } };
                rows.forEach(r => {
                  if (r.won === null) return; // skip pending
                  const a = acc[r.dirFull]; if (!a) return;
                  if (r.won) a.w++; else a.l++;
                  a.u += r.u;
                });
                const _ouChip = (label, a) => {
                  const n = a.w + a.l;
                  const wr = n > 0 ? (a.w / n * 100).toFixed(1) + '%' : '—';
                  const uS = (a.u >= 0 ? '+' : '') + a.u.toFixed(2) + 'u';
                  return `<div><span style="color:#bbb;font-weight:600">${label}:</span> `
                       + `<span style="color:#ddd;font-weight:600">${a.w}-${a.l}</span> `
                       + `<span style="color:#999">(${wr})</span> `
                       + `<span style="color:${a.u >= 0 ? 'var(--green)' : 'var(--red)'};font-weight:600">${uS}</span></div>`;
                };
                apOuSummary.innerHTML = _ouChip('Overs', acc.OVER) + _ouChip('Unders', acc.UNDER);
              }

              _updateApHeaders();
              _renderApRows();
            }

            const cap = document.createElement('div');
            cap.style.cssText = 'padding:8px 4px;color:#888;font-size:11px;font-style:italic;line-height:1.5';
            cap.innerHTML = `
              Converts each pick's offered price to a breakeven probability and compares it to the model's pCover.<br>
              <strong style="color:var(--green)">TAKE</strong> = +EV at the price (pCover ≥ breakeven − margin).<br>
              <strong style="color:var(--red)">PASS would-be</strong> = −EV picks the EV Gate flags to skip — what they would have done if bet anyway.<br>
              Shadow monitor: this does <strong>not</strong> change which picks are bet. Margin ${EV_GATE_MARGIN.toFixed(2)} (pure EV&gt;0).
            `;
            evCard.appendChild(cap);

            _evRecordCard = evCard;
          })();
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
        // Per-pick void (pitcher_scratched / pitcher_swapped) also drops the
        // row from today's table — the bet is refunded, no actionable edge.
        const _isVoidPick = (p) => p.result === 'VOID';
        const todayPicks = picks.filter(p =>
          p.date === todayStr && !_isVoidGame(p) && !_isVoidPick(p)
        );
        const todayLeans = (data.props || []).filter(p =>
          p.date === todayStr && isLean(p) && !_isVoidGame(p) && !_isVoidPick(p)
        );
        if (todayPicks.length > 0 || todayLeans.length > 0) {
          const todayCard = document.createElement('div');
          todayCard.className = 'card card-picks';
          todayCard.style.marginBottom = '16px';
          // Today / Yesterday toggle lives in the title row when a recap
          // exists. Selecting Yesterday swaps the Today-only body for the
          // Yesterday Recap body that was built earlier and held back.
          const titleRow = document.createElement('div');
          titleRow.style.cssText = 'display:flex;align-items:center;justify-content:space-between;gap:12px';
          const titleLeft = document.createElement('div');
          titleLeft.style.cssText = 'display:flex;align-items:center;gap:10px;flex-wrap:wrap';
          const titleEl = Object.assign(document.createElement('div'), {
            className: 'card-title',
            textContent: `Picks (${todayStr})`,
          });
          titleLeft.appendChild(titleEl);
          const dayToggleWrap = document.createElement('div');
          if (_yesterdayRecapCard) {
            dayToggleWrap.style.cssText = 'display:inline-flex;gap:4px';
            const _DAY_ACTIVE = 'padding:4px 12px;font-size:11px;font-weight:700;border:1px solid #a78bfa;background:#a78bfa;color:#0a0a0a;border-radius:4px;cursor:pointer;transition:all 0.15s';
            const _DAY_IDLE   = 'padding:4px 12px;font-size:11px;font-weight:500;border:1px solid rgba(255,255,255,0.15);background:rgba(255,255,255,0.04);color:#ccc;border-radius:4px;cursor:pointer;transition:all 0.15s';
            const todayBtn = document.createElement('button');
            todayBtn.textContent = `Today (${todayStr.slice(5)})`;
            const yBtn = document.createElement('button');
            yBtn.textContent = `Yesterday (${(_yesterdayStrCached || '').slice(5)})`;
            function _setDay(which) {
              const isToday = which === 'today';
              todayBtn.style.cssText = isToday ? _DAY_ACTIVE : _DAY_IDLE;
              yBtn.style.cssText = isToday ? _DAY_IDLE : _DAY_ACTIVE;
              if (todayBody) todayBody.style.display = isToday ? '' : 'none';
              if (recapBody) recapBody.style.display = isToday ? 'none' : '';
              titleEl.textContent = isToday
                ? `Picks (${todayStr})`
                : `Yesterday\u2019s Recap (${_yesterdayStrCached})`;
              if (sortToggle) sortToggle.style.display = isToday ? '' : 'none';
            }
            todayBtn.addEventListener('click', () => _setDay('today'));
            yBtn.addEventListener('click', () => _setDay('yesterday'));
            // Stash the setter so we can call it after bodies are wired up.
            dayToggleWrap._setDay = _setDay;
            dayToggleWrap.appendChild(todayBtn);
            dayToggleWrap.appendChild(yBtn);
            titleLeft.appendChild(dayToggleWrap);
          }
          titleRow.appendChild(titleLeft);
          const sortToggle = document.createElement('button');
          sortToggle.type = 'button';
          sortToggle.style.cssText = 'background:var(--accent,#5a6cff);color:#fff;border:none;border-radius:6px;padding:4px 10px;font-size:0.75rem;cursor:pointer';
          let sortMode = 'pcover'; // default
          sortToggle.textContent = 'Sort: pCover';
          titleRow.appendChild(sortToggle);
          todayCard.appendChild(titleRow);
          // Wrap today's body so the Today/Yesterday toggle can swap it in/out.
          const todayBody = document.createElement('div');
          let recapBody = null;

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
          // Bodies go into todayBody so the Today/Yesterday toggle can swap.
          if (todayPicks.length > 0) { todayBody.appendChild(tbl); fitMLBTableToContainer(tbl); }
          // Today's Leans table removed — we don't bet leans. The Watch —
          // bumped section inside the matchup history below still surfaces
          // watchlist plays the read has elevated.
          // Placeholder for Matchup History (table + Read). Filled later by
          // _renderMatchupCard via the hoisted slot reference.
          _todayMatchupSlot = document.createElement('div');
          _todayMatchupSlot.style.cssText = 'margin-top:16px;padding-top:12px;border-top:1px solid rgba(255,255,255,0.08)';
          todayBody.appendChild(_todayMatchupSlot);
          todayCard.appendChild(todayBody);
          // Yesterday Recap body: pull children off the deferred recap card
          // (skipping its own card-title since we render a unified one).
          if (_yesterdayRecapCard) {
            recapBody = document.createElement('div');
            recapBody.style.display = 'none';
            while (_yesterdayRecapCard.firstChild) {
              const child = _yesterdayRecapCard.firstChild;
              if (child.classList && child.classList.contains('card-title')) {
                _yesterdayRecapCard.removeChild(child);
                continue;
              }
              recapBody.appendChild(child);
            }
            // Same matchup-history placeholder for yesterday — filled later
            // with walk-forward records (cohort excludes yesterday's picks).
            _yesterdayMatchupSlot = document.createElement('div');
            _yesterdayMatchupSlot.style.cssText = 'margin-top:16px;padding-top:12px;border-top:1px solid rgba(255,255,255,0.08)';
            recapBody.appendChild(_yesterdayMatchupSlot);
            todayCard.appendChild(recapBody);
            // Now that both bodies exist, set the initial active day.
            if (dayToggleWrap._setDay) dayToggleWrap._setDay('today');
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
        // Use todayProjections (all projections incl. PASS) if available, else fall back to picks only.
        // VOIDed rows (pitcher_scratched / pitcher_swapped) are dropped so a
        // scratched starter doesn't keep projecting in Today's Games. Cross-
        // reference against data.props for the void marker since
        // todayProjections may not carry result/voidReason fields.
        const _voidedKey = new Set(
          (data.props || [])
            .filter(p => p.result === 'VOID' && p.date === todayStr)
            .map(p => `${p.player}|${p.team}|${p.market}`)
        );
        const todayAllProj = (data.todayProjections || data.props)
          .filter(p => p.date === todayStr && p.proj != null && p.line != null)
          .filter(p => p.result !== 'VOID'
                       && !_voidedKey.has(`${p.player}|${p.team}|${p.market}`));
        if (todayAllProj.length === 0) return;

        // Build unique games from gameTimes (all scheduled games, not just ones
        // with prop lines). Falls back to projections if gameTimes missing.
        const gameTimes = data.gameTimes || {};
        const gameSet = new Map();

        // First add games from gameTimes (covers games with no prop lines yet)
        // gameTimes is {team_abbr: ISO_time} — pair up teams by matching times.
        // Order each pair so home team renders on the RIGHT ("AWAY vs HOME"),
        // consistent with how baseball scoreboards read.
        const homeAwayToday = (data.homeAway || {})[todayStr] || {};
        const _orderPair = (a, b) => {
          const aHome = homeAwayToday[a] === 'home';
          const bHome = homeAwayToday[b] === 'home';
          if (aHome && !bHome) return [b, a];   // a is home → put it on right
          if (bHome && !aHome) return [a, b];   // b is home → already right
          return [a, b];                        // unknown → leave as is
        };
        const teamsByTime = {};
        for (const [team, t] of Object.entries(gameTimes)) {
          if (!teamsByTime[t]) teamsByTime[t] = [];
          teamsByTime[t].push(team);
        }
        for (const [t, teams] of Object.entries(teamsByTime)) {
          if (teams.length === 2) {
            const key = [...teams].sort().join('@');
            const [left, right] = _orderPair(teams[0], teams[1]);
            gameSet.set(key, { label: `${left} vs ${right}`, time: t });
          }
        }

        // Also add any games from projections (handles cases where gameTimes is missing)
        for (const p of todayAllProj) {
          const key = [p.team, p.opp].sort().join('@');
          if (!gameSet.has(key)) {
            const t = gameTimes[p.team] || gameTimes[p.opp] || '9999';
            const [left, right] = _orderPair(p.team, p.opp);
            gameSet.set(key, { label: `${left} vs ${right}`, time: t });
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
              if (i===10 && p.pCover!=null) td.style.color = p.pCover >= MLB_PICK_THRESHOLD ? 'var(--green)' : p.pCover >= MLB_WATCH_FLOOR ? 'var(--yellow)' : p.pCover <= 0.45 ? 'var(--red)' : '#ccc';
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
          // Same chip style as Team History → Today's Matchups for visual
          // consistency across the page (rounded 4px, purple-on-dark when
          // active, soft border + faint fill when inactive).
          const _ACTIVE_PILL = 'padding:5px 10px;font-size:11px;font-weight:700;border:1px solid #a78bfa;background:#a78bfa;color:#0a0a0a;border-radius:4px;cursor:pointer;transition:all 0.15s';
          const _IDLE_PILL   = 'padding:5px 10px;font-size:11px;font-weight:500;border:1px solid rgba(255,255,255,0.15);background:rgba(255,255,255,0.04);color:#ccc;border-radius:4px;cursor:pointer;transition:all 0.15s';
          allGamesBtn.textContent = 'All';
          allGamesBtn.style.cssText = activeGame === 'all' ? _ACTIVE_PILL : _IDLE_PILL;
          allGamesBtn.onclick = () => {
            activeGame = 'all';
            activePlayer = 'all';
            currentPage = 0;
            refreshPills();
            refreshPlayerDropdown();
            renderGameTable();
            // Tell Pitcher History + Team History to clear their per-game
            // state (hide the pitcher toggle, clear the matchup chip).
            _gameClickSubs.forEach(fn => { try { fn({ reset: true }); } catch (e) {} });
          };
          gamePills.appendChild(allGamesBtn);
          for (const [key, label] of games) {
            const btn = document.createElement('button');
            btn.textContent = label;
            btn.style.cssText = key === activeGame ? _ACTIVE_PILL : _IDLE_PILL;
            btn.onclick = () => {
              activeGame = key;
              activePlayer = 'all';
              currentPage = 0;
              refreshPills();
              refreshPlayerDropdown();
              renderGameTable();
              // Fan out to Pitcher History + Team History so they jump to
              // the same matchup. Use the LABEL's team order (matches what
              // the user sees on the chip) so both downstream cards render
              // their toggles in the same left/right order. The key is
              // alphabetical and would invert pairs like "MIN vs CWS".
              const labelTeams = (label || '').split(' vs ');
              const teams = labelTeams.length === 2 ? labelTeams : key.split('@');
              if (teams.length === 2) {
                _gameClickSubs.forEach(fn => { try { fn({ teams }); } catch (e) {} });
              }
            };
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
      if (_renderMatchupCard) {
        try { _renderMatchupCard(); }
        catch (e) { console.error('_renderMatchupCard failed:', e); }
      }
      // Season Market Breakdown sits below Matchup History — it summarizes
      // all history, so it's a natural footer to the per-game cards.
      if (_seasonBreakdownCard) el.appendChild(_seasonBreakdownCard);
      // The three shadow-monitor gates (Read -> MAE -> EV) are NOT appended
      // here — they are grouped together at the very bottom, after the Reddit
      // card / all-history table (see the append block after _renderRedditCard).
      // Recent Record sits above that group.
      if (_recentRecordContainer) el.appendChild(_recentRecordContainer);

      // ── Unified Toolbar ──
      const selStyle = 'padding:6px 12px;border-radius:6px;background:rgba(255,255,255,0.06);color:#fff;border:1px solid rgba(255,255,255,0.1);font-size:13px;outline:none';
      const pillStyle = 'padding:5px 14px;border-radius:16px;border:1px solid rgba(255,255,255,0.12);background:transparent;color:#999;font-size:12px;cursor:pointer;transition:all 0.15s';
      const pillActiveStyle = 'padding:5px 14px;border-radius:16px;border:1px solid #7c6cf0;background:#7c6cf0;color:#fff;font-size:12px;cursor:pointer;transition:all 0.15s';
      const tabStyle = 'padding:6px 16px;border:none;background:transparent;color:#999;font-size:13px;cursor:pointer;border-bottom:2px solid transparent;transition:all 0.15s';
      const tabActiveStyle = 'padding:6px 16px;border:none;background:transparent;color:#fff;font-size:13px;cursor:pointer;border-bottom:2px solid #7c6cf0;transition:all 0.15s';

      let mlbView = 'all'; // 'all' | 'weekly' | 'all-lean' | 'weekly-lean' | 'all-combined'

      // Two sub-pick tiers (both pick=PASS, neither bet):
      //   Watch: 0.60 <= pCover < 0.65 — near the 0.65 pick threshold,
      //          worth monitoring (former lean band, conceptually similar).
      //   Pass:  0.50 <= pCover < 0.60 — sub-watch, kept in JSON for
      //          analysis only.
      // Picks (>= 0.65) are flat-2u and live in `picks`.
      const watchPicks = (data.props || []).filter(p => {
        if (p.pick !== 'PASS') return false;
        const pc = p.pCover || 0;
        return pc >= 0.60 && pc < 0.65;
      });
      const passPicks = (data.props || []).filter(p => {
        if (p.pick !== 'PASS') return false;
        const pc = p.pCover || 0;
        return pc >= 0.50 && pc < 0.60;
      });
      // Backcompat alias — older references (Reddit widget, history card)
      // still use the `leanPicks` name to mean "sub-pick rows worth showing".
      // Map to the watch tier so existing logic keeps working unchanged.
      const leanPicks = watchPicks;

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
      viewAllLeanBtn.textContent = 'All Watch';
      const viewWeeklyLeanBtn = document.createElement('button');
      viewWeeklyLeanBtn.textContent = 'Weekly Watch';
      const viewAllPassBtn = document.createElement('button');
      viewAllPassBtn.textContent = 'All Pass';
      const viewWeeklyPassBtn = document.createElement('button');
      viewWeeklyPassBtn.textContent = 'Weekly Pass';
      const viewAllCombinedBtn = document.createElement('button');
      viewAllCombinedBtn.textContent = 'All';
      const viewWeeklyCombinedBtn = document.createElement('button');
      viewWeeklyCombinedBtn.textContent = 'Weekly All';
      tabRow.appendChild(viewAllCombinedBtn);
      tabRow.appendChild(viewWeeklyCombinedBtn);
      tabRow.appendChild(viewAllBtn);
      tabRow.appendChild(viewWeeklyBtn);
      tabRow.appendChild(viewAllLeanBtn);
      tabRow.appendChild(viewWeeklyLeanBtn);
      tabRow.appendChild(viewAllPassBtn);
      tabRow.appendChild(viewWeeklyPassBtn);
      toolbar.appendChild(tabRow);

      // Market filter row removed — strikeouts is the only active market.
      let mlbActiveMarket = 'all';
      function renderMLBMarketBtns() { /* no-op: single-market mode */ }

      // Row 3: Contextual filters
      const filterRow = document.createElement('div');
      filterRow.className = 'props-toolbar-filters';
      filterRow.style.cssText = 'display:flex;gap:12px;align-items:center;padding:12px 16px;flex-wrap:wrap';

      // All Picks filters
      const allDates = [...new Set(picks.concat(watchPicks, passPicks).map(p => p.date))].sort().reverse();
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
      // Line filter — every distinct betting line present in the data
      // (e.g. 3.5, 4.5, 5.5, 6.5, 7.5 for strikeouts), sorted ascending.
      // Populated from picks+watch+pass so the option list stays stable as
      // the user switches view tabs.
      const allLines = [...new Set(
        picks.concat(watchPicks, passPicks)
          .map(p => p.line)
          .filter(v => v != null)
      )].sort((a, b) => a - b);
      const lineSel = document.createElement('select');
      lineSel.style.cssText = selStyle;
      lineSel.innerHTML = '<option value="all">All Lines</option>'
        + allLines.map(v => `<option value="${v}">${v}</option>`).join('');
      const teamSel = document.createElement('select');
      teamSel.style.cssText = selStyle;
      const allTeams = [...new Set(picks.map(p => p.team))].filter(Boolean).sort();
      teamSel.innerHTML = '<option value="all">All Teams</option>' + allTeams.map(t => `<option value="${t}">${t}</option>`).join('');
      const filterLabel = document.createElement('span');
      filterLabel.style.cssText = 'color:#666;font-size:12px;margin-left:auto';
      filterLabel.textContent = `${picks.length} picks`;

      // Weekly filters — include picks + watch + pass so the week dropdown
      // populates even when a tier has activity on a week the others miss.
      const allWeekStarts = [...new Set(
        picks.concat(watchPicks, passPicks).filter(p => p.date).map(p => getWeekStart(p.date))
      )].sort().reverse();
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
        const src = (mlbView === 'weekly-lean') ? watchPicks
                  : (mlbView === 'weekly-pass') ? passPicks
                  : (mlbView === 'weekly-combined') ? picks.concat(watchPicks, passPicks)
                  : picks;
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
        if (mlbView === 'all-lean'  || mlbView === 'weekly-lean')  return watchPicks;
        if (mlbView === 'all-pass'  || mlbView === 'weekly-pass')  return passPicks;
        if (mlbView === 'all-combined' || mlbView === 'weekly-combined') return picks.concat(watchPicks, passPicks);
        return picks;
      }

      function getFilteredPicks() {
        let fp = activeSource().slice();
        if (dateSel.value !== 'all') fp = fp.filter(p => p.date === dateSel.value);
        if (teamSel.value !== 'all') fp = fp.filter(p => p.team === teamSel.value);
        if (lineSel.value !== 'all') fp = fp.filter(p => p.line != null && +p.line === +lineSel.value);
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

      // Column sort for the All Picks table. `label` matches a header string;
      // `dir` is 1 (asc) / -1 (desc). Click a header to toggle.
      let allPicksSort = { label: null, dir: 1 };
      const allPicksSortKey = {
        'Date':  p => p.date || '',
        'Name':  p => displayName(p).toLowerCase(),
        'Team':  p => p.team || '',
        'Opp':   p => p.opp || '',
        'vs':    p => p.opp || '',
        'pOuts': p => p.proj_ip != null ? p.proj_ip * 3 : null,
        'aOuts': p => p.actual_outs,
        'pBF':   p => p.proj_bf,
        'aBF':   p => p.actual_bf,
        'pPC':   p => p.proj_pc,
        'aPC':   p => p.actual_pitches,
        'Proj':  p => p.proj,
        'Line':  p => p.line,
        'Edge':  p => (p.proj != null && p.line != null) ? p.proj - p.line : null,
        '%':     p => p.pCover,
        'Actual':p => p.actual,
        'O/U':   p => effectiveDir(p) || '',
        'Odds':  p => p.odds,
        'W/L':   p => p.result || ''
      };
      function sortAllPicks(arr) {
        const fn = allPicksSortKey[allPicksSort.label];
        if (!fn) return arr;
        const dir = allPicksSort.dir;
        return arr.slice().sort((a, b) => {
          let va = fn(a), vb = fn(b);
          // Nulls/undefined always sort to the bottom regardless of direction.
          const na = va == null || va === '', nb = vb == null || vb === '';
          if (na && nb) return 0;
          if (na) return 1;
          if (nb) return -1;
          if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * dir;
          return String(va).localeCompare(String(vb)) * dir;
        });
      }

      function renderAllPicksView() {
        contentArea.textContent = '';
        const filteredPicks = sortAllPicks(getFilteredPicks());
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
          const sortable = !!allPicksSortKey[h];
          const arrow = (allPicksSort.label === h) ? (allPicksSort.dir === 1 ? ' ▲' : ' ▼') : '';
          th.textContent = h + arrow;
          th.style.cssText = 'padding:4px 4px;text-align:center;border-bottom:1px solid rgba(255,255,255,0.1);font-size:12px'
            + (sortable ? ';cursor:pointer;user-select:none' : '');
          if (h === 'Name') th.style.textAlign = 'left';
          if (allPicksSort.label === h) th.style.color = '#fff';
          if (sortable) {
            th.onclick = () => {
              if (allPicksSort.label === h) allPicksSort.dir *= -1;
              else { allPicksSort.label = h; allPicksSort.dir = 1; }
              allPicksPage = 0;
              renderAllPicksView();
            };
          }
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
        if (lineSel.value !== 'all') fp = fp.filter(p => p.line != null && +p.line === +lineSel.value);
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
        if (mlbView === 'weekly' || mlbView === 'weekly-lean' || mlbView === 'weekly-pass' || mlbView === 'weekly-combined') renderWeeklyView();
        else renderAllPicksView();
      }

      function setView(v) {
        mlbView = v;
        viewAllBtn.style.cssText = v === 'all' ? tabActiveStyle : tabStyle;
        viewWeeklyBtn.style.cssText = v === 'weekly' ? tabActiveStyle : tabStyle;
        viewAllLeanBtn.style.cssText = v === 'all-lean' ? tabActiveStyle : tabStyle;
        viewWeeklyLeanBtn.style.cssText = v === 'weekly-lean' ? tabActiveStyle : tabStyle;
        viewAllPassBtn.style.cssText = v === 'all-pass' ? tabActiveStyle : tabStyle;
        viewWeeklyPassBtn.style.cssText = v === 'weekly-pass' ? tabActiveStyle : tabStyle;
        viewAllCombinedBtn.style.cssText = v === 'all-combined' ? tabActiveStyle : tabStyle;
        viewWeeklyCombinedBtn.style.cssText = v === 'weekly-combined' ? tabActiveStyle : tabStyle;
        // Swap filter row contents
        filterRow.textContent = '';
        const isWeekly = (v === 'weekly' || v === 'weekly-lean' || v === 'weekly-pass' || v === 'weekly-combined');
        if (!isWeekly) {
          filterRow.appendChild(dateSel);
          filterRow.appendChild(teamSel);
          filterRow.appendChild(lineSel);
          filterRow.appendChild(dirSel);
          filterRow.appendChild(bucketSel);
          filterRow.appendChild(filterLabel);
        } else {
          refreshDayOptions();
          filterRow.appendChild(weekSel);
          filterRow.appendChild(daySel);
          filterRow.appendChild(lineSel);
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
      viewAllPassBtn.onclick = () => setView('all-pass');
      viewWeeklyPassBtn.onclick = () => setView('weekly-pass');
      viewAllCombinedBtn.onclick = () => setView('all-combined');
      viewWeeklyCombinedBtn.onclick = () => setView('weekly-combined');
      dateSel.addEventListener('change', renderAllPicksView);
      teamSel.addEventListener('change', renderAllPicksView);
      lineSel.addEventListener('change', refreshView);
      dirSel.addEventListener('change', refreshView);
      bucketSel.addEventListener('change', refreshView);
      weekSel.addEventListener('change', () => { refreshDayOptions(); renderWeeklyView(); });
      daySel.addEventListener('change', renderWeeklyView);

      renderMLBMarketBtns();
      setView('all');

      // Reddit summary card — always rendered at the very bottom. Also builds
      // the MAE Gate card and assigns it to _maeGateCard (hoisted, not appended).
      if (_renderRedditCard) _renderRedditCard();

      // Shadow-monitor gates (not live), grouped together at the very bottom in
      // order Read -> MAE -> EV. None of them change which picks are bet; each
      // backtests a TAKE/PASS verdict against actual results.
      if (_readRecordCard) el.appendChild(_readRecordCard);
      if (_maeGateCard) el.appendChild(_maeGateCard);
      if (_evRecordCard) el.appendChild(_evRecordCard);
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
        const _homeAwayToday = (data.homeAway || {})[todayStr] || {};
        const _orderBatPair = (a, b) => {
          const aHome = _homeAwayToday[a] === 'home';
          const bHome = _homeAwayToday[b] === 'home';
          if (aHome && !bHome) return [b, a];
          if (bHome && !aHome) return [a, b];
          return [a, b];
        };
        for (const p of todayAllProj) {
          const key = [p.team, p.opp].sort().join('@');
          if (!gameSet.has(key)) {
            const t = gameTimes[p.team] || gameTimes[p.opp] || '9999';
            const [left, right] = _orderBatPair(p.team, p.opp);
            gameSet.set(key, { label: `${left} vs ${right}`, time: t });
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
              if (i===6 && p.pCover!=null) td.style.color = p.pCover >= MLB_PICK_THRESHOLD ? 'var(--green)' : p.pCover >= MLB_WATCH_FLOOR ? 'var(--yellow)' : p.pCover <= 0.45 ? 'var(--red)' : '#ccc';
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
          // Same chip style as Team History → Today's Matchups (see pitcher
          // card above) — consistent visual language across slate selectors.
          const _ACTIVE_PILL = 'padding:5px 10px;font-size:11px;font-weight:700;border:1px solid #a78bfa;background:#a78bfa;color:#0a0a0a;border-radius:4px;cursor:pointer;transition:all 0.15s';
          const _IDLE_PILL   = 'padding:5px 10px;font-size:11px;font-weight:500;border:1px solid rgba(255,255,255,0.15);background:rgba(255,255,255,0.04);color:#ccc;border-radius:4px;cursor:pointer;transition:all 0.15s';
          allGamesBtn.textContent = 'All';
          allGamesBtn.style.cssText = activeGame === 'all' ? _ACTIVE_PILL : _IDLE_PILL;
          allGamesBtn.onclick = () => { activeGame = 'all'; activePlayer = 'all'; bCurrentPage = 0; refreshPills(); refreshPlayerDropdown(); renderBatterTable(); };
          gamePills.appendChild(allGamesBtn);
          for (const [key, label] of games) {
            const btn = document.createElement('button');
            btn.textContent = label;
            btn.style.cssText = key === activeGame ? _ACTIVE_PILL : _IDLE_PILL;
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
