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

  // Week filter: pick which run to show in the "Latest" view
  let selectedRun = latestRun;
  if (nflWeekFilter !== 'latest' && nflWeekFilter !== 'all') {
    const parsed = parseNflWeekFilter(nflWeekFilter);
    selectedRun = nonBurnIn.find(r =>
      r.week === parsed.week && (!parsed.season || r.season == parsed.season)
    ) || latestRun;
  }

  // For stats views: if a specific week is selected, show cumulative up to that week
  const statsRuns = (nflWeekFilter !== 'latest' && nflWeekFilter !== 'all')
    ? (() => {
        const parsed = parseNflWeekFilter(nflWeekFilter);
        return runs.filter(r => {
          if (parsed.season) {
            // With season: include all prior seasons + weeks up to selected in same season
            return r.season < parsed.season || (r.season == parsed.season && r.week <= parsed.week);
          }
          return r.week <= parsed.week;
        });
      })()
    : runs;

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
  const bannerRuns = viewMode === 'history' ? historyWeekStatsRuns : statsRuns;

  let html = nflRenderRecordBanner(bannerRuns);

  html += `
    <div class="view-toggle">
      <button class="view-btn ${viewMode === 'today' ? 'active' : ''}" onclick="setView('today')">Latest</button>
      <button class="view-btn ${viewMode === 'history' ? 'active' : ''}" onclick="setView('history')">History</button>
      ${seasonSelector(allRuns)}
      ${viewMode === 'today' ? nflWeekSelector(runs) : nflHistoryWeekSelector(runs)}
    </div>`;

  if (viewMode === 'today' && selectedRun) {
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

