// NFL Games rendering
// ─── NFL Main Render Dispatcher ───

async function renderNFL() {
  const el = document.getElementById('content');
  const data = await fetchData('nfl');
  if (!data || !data.runs || !data.runs.length) {
    el.innerHTML = `
      <div class="card card-games">
        <div class="card-title">NFL — No Data Available</div>
        <div class="no-picks" style="padding:20px 0 6px">No NFL data loaded yet. The pipeline has not produced any runs.</div>
        <div class="card-subtitle">Waiting for pyNFL to generate data into pyNFL/data/nfl.json.</div>
      </div>`;
    return;
  }
  const allRuns = data.runs;
  const runs = filterBySeason(allRuns);
  const nonBurnIn = runs.filter(r => !r.burnIn);
  const latestRun = nonBurnIn.length ? nonBurnIn[nonBurnIn.length - 1] : null;
  const totalPages = Math.ceil(nonBurnIn.length / DAYS_PER_PAGE);

  // Week filter: pick which run to show in the "Latest" view.
  // Search ALL runs, not just non-burn-in ones — the systems fire in weeks
  // 1-3 and selecting one of those used to fall through to latestRun, which
  // made the early weeks look like they had no plays at all.
  let selectedRun = latestRun;
  if (nflWeekFilter !== 'latest' && nflWeekFilter !== 'all') {
    const parsed = parseNflWeekFilter(nflWeekFilter);
    selectedRun = runs.find(r =>
      r.week === parsed.week && (!parsed.season || r.season == parsed.season)
    ) || latestRun;
  }

  // For stats views: if a specific week is selected, show cumulative up to that week.
  // cutTo() is shared so model stats and system stats slice the same way; the
  // difference is only WHICH runs feed them.
  const cutTo = (rs) => (nflWeekFilter !== 'latest' && nflWeekFilter !== 'all')
    ? (() => {
        const parsed = parseNflWeekFilter(nflWeekFilter);
        return rs.filter(r => {
          if (parsed.season) {
            // With season: include all prior seasons + weeks up to selected in same season
            return r.season < parsed.season || (r.season == parsed.season && r.week <= parsed.week);
          }
          return r.week <= parsed.week;
        });
      })()
    : rs;
  // MODEL stats exclude burn-in (the projection isn't trusted there) ...
  const statsRuns = cutTo(nonBurnIn);
  // ... but SYSTEM stats include every week: the systems don't use the
  // projection, so they have no warm-up and do produce plays in weeks 1-3.
  const systemStatsRuns = cutTo(runs);

  updateLastRunInfo();
  updateLastSyncInfo();

  // Apply history week filter to stats when in history mode
  const historyWeekStatsRuns = (viewMode === 'history' && nflHistoryWeekFilter !== 'all')
    ? (() => {
        const parsed = parseNflWeekFilter(nflHistoryWeekFilter);
        return statsRuns.filter(r =>
          r.week === parsed.week && (!parsed.season || r.season == parsed.season)
        );
      })()
    : statsRuns;
  // Banner shows the SYSTEM record, which has no burn-in, so feed it the
  // all-weeks slice rather than the model's non-burn-in one.
  const bannerRuns = viewMode === 'history' ? historyWeekStatsRuns : systemStatsRuns;

  let html = nflRenderRecordBanner(bannerRuns);

  html += `
    <div class="view-toggle">
      <button class="view-btn ${viewMode === 'today' ? 'active' : ''}" onclick="setView('today')">Latest</button>
      <button class="view-btn ${viewMode === 'history' ? 'active' : ''}" onclick="setView('history')">History</button>
      ${seasonSelector(allRuns)}
      ${viewMode === 'today' ? nflWeekSelector(runs) : nflHistoryWeekSelector(runs)}
    </div>`;

  if (viewMode === 'today' && selectedRun) {
    // Situational systems are the actual betting product — show them first.
    // They have NO burn-in: they don't use the projection, so they fire from
    // week 1. Pick their run from all runs, not just non-burn-in ones.
    const systemsRun = (nflWeekFilter !== 'latest' && nflWeekFilter !== 'all')
      ? selectedRun
      : (runs.length ? runs[runs.length - 1] : selectedRun);
    // ── THE PRODUCT: situational systems ──────────────────────────────
    // This tab is a SYSTEM NOTIFIER. The spread/total projection has no
    // measurable edge over the market (see engine_v2 header), so it is
    // reference material only and lives at the bottom behind a toggle.
    html += '<div class="section-label">System Plays</div>';
    html += nflRenderSystemPlays(systemsRun);
    html += '<div class="section-label">System Record</div>';
    html += nflRenderSystemRecord(systemStatsRuns);

    // ── Reference: the projection, collapsed by default ───────────────
    html += `<div class="section-label" style="cursor:pointer" onclick="nflToggleModel()">
        ${nflShowModel ? '▾' : '▸'} Model Reference (no market edge — not a betting product)
      </div>`;
    if (nflShowModel) {
      html += nflRenderTodayPicks(selectedRun);
      html += nflRenderWeeklyPicks(selectedRun);
      html += '<div class="section-label">Spread Record (ATS)</div>';
      html += renderSpreadRecord(statsRuns);
      html += '<div class="section-label">Season P&L</div>';
      html += nflRenderPnL(statsRuns);
      html += '<div class="section-label">Injury Adjustments</div>';
      html += nflRenderInjuries(selectedRun);
      html += '<div class="section-label">Model vs Market</div>';
      html += nflRenderScatter(statsRuns);
      html += '<div class="section-label">Hit Rate Trends</div>';
      html += nflRenderRollingRate(statsRuns);
      html += '<div class="section-label">Calibration</div>';
      html += nflRenderCalibration(statsRuns);
    }

  } else if (viewMode === 'history') {
    // Filter history runs by week if a specific week is selected
    const historyRuns = nflHistoryWeekFilter !== 'all'
      ? (() => {
          const parsed = parseNflWeekFilter(nflHistoryWeekFilter);
          return nonBurnIn.filter(r =>
            r.week === parsed.week && (!parsed.season || r.season == parsed.season)
          );
        })()
      : nonBurnIn;
    const historyTotalPages = Math.ceil(historyRuns.length / DAYS_PER_PAGE);
    html += `
      <div class="date-nav">
        <button onclick="prevPage()" ${historyPage === 0 ? 'disabled' : ''}>Newer</button>
        <span class="current-view">Page ${historyPage + 1} / ${historyTotalPages || 1}</span>
        <button onclick="nextPage()" ${historyPage >= historyTotalPages - 1 ? 'disabled' : ''}>Older</button>
      </div>`;
    const reversed = [...historyRuns].reverse();
    const start = historyPage * DAYS_PER_PAGE;
    const pageRuns = reversed.slice(start, start + DAYS_PER_PAGE);
    if (!pageRuns.length) {
      html += '<div class="no-picks">No picks available for this selection.</div>';
    } else {
      html += pageRuns.map(nflRenderHistoryWeek).join('');
    }
  } else {
    html += '<div class="no-picks">No NFL picks available yet.</div>';
  }

  el.innerHTML = html;
}

// ─── Main Render ───

