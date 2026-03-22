// scripts/sources/nba_stats.mjs
// Fetches NBA.com team stats — full season, last N games, home/away splits.
//
// Usage:
//   fetchNBAStats("2026-01-30")              → season stats as of Jan 30
//   fetchNBAStats()                           → season stats as of today
//   fetchNBAStatsEnhanced("20260301")         → { season, last10, home, away }

const NBA_HEADERS = {
  "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
  "Referer":    "https://www.nba.com/",
  "Origin":     "https://www.nba.com",
  "Accept":     "application/json, text/plain, */*",
  "Accept-Language": "en-US,en;q=0.9",
  "x-nba-stats-origin": "stats",
  "x-nba-stats-token":  "true",
};

// Auto-detect current NBA season string (e.g. "2025-26")
function currentSeason() {
  const now = new Date();
  const year = now.getFullYear();
  const month = now.getMonth() + 1;
  const startYear = month >= 10 ? year : year - 1;
  return `${startYear}-${String(startYear + 1).slice(2)}`;
}

// Convert YYYY-MM-DD or YYYYMMDD → MM/DD/YYYY for NBA.com API
function toNBADate(dateStr) {
  if (!dateStr) return "";
  const s = String(dateStr).replace(/-/g, "");
  if (s.length === 8) {
    return `${s.slice(4, 6)}/${s.slice(6, 8)}/${s.slice(0, 4)}`;
  }
  return dateStr;
}

// Core fetch — builds the NBA.com API URL with optional overrides
async function fetchTeamStats(dateTo = null, dateFrom = null, { lastNGames = 0, location = "", seasonType = "Regular Season" } = {}) {
  const season = currentSeason();
  const dateToParam   = dateTo   ? toNBADate(dateTo)   : "";
  const dateFromParam = dateFrom ? toNBADate(dateFrom) : "";

  const params = new URLSearchParams({
    MeasureType:     "Advanced",
    PerMode:         "PerGame",
    PaceAdjust:      "N",
    PlusMinus:       "N",
    Rank:            "N",
    Season:          season,
    SeasonType:      seasonType,
    DateFrom:        dateFromParam,
    DateTo:          dateToParam,
    Outcome:         "",
    Location:        location,   // "" = all, "Home" = home, "Road" = away
    Month:           "0",
    SeasonSegment:   "",
    OpponentTeamID:  "0",
    VsConference:    "",
    VsDivision:      "",
    GameSegment:     "",
    Period:          "0",
    ShotClockRange:  "",
    LastNGames:      String(lastNGames),  // 0 = all games
  });

  const url = `https://stats.nba.com/stats/leaguedashteamstats?${params}`;
  let res;
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 30000);
      res = await fetch(url, { headers: NBA_HEADERS, signal: controller.signal });
      clearTimeout(timeout);
      if (res.ok) break;
      throw new Error(`NBA.com stats failed: ${res.status}`);
    } catch (err) {
      if (attempt === 3) throw err;
      console.log(`  ⚠ NBA.com fetch attempt ${attempt}/3 failed (${err.message}), retrying in ${attempt * 5}s...`);
      await new Promise(r => setTimeout(r, attempt * 5000));
    }
  }
  if (!res.ok) throw new Error(`NBA.com stats failed: ${res.status}`);

  const json = await res.json();
  const resultSet = json?.resultSets?.[0];
  if (!resultSet) throw new Error("NBA.com: no resultSet in response");

  const headers = resultSet.headers;
  const rows    = resultSet.rowSet;

  const idx = (name) => headers.indexOf(name);
  const iTeamName = idx("TEAM_NAME");
  const iGP       = idx("GP");
  const iOFF      = idx("OFF_RATING");
  const iDEF      = idx("DEF_RATING");
  const iTS       = idx("TS_PCT");
  const iTO       = idx("TM_TOV_PCT");
  const iORR      = idx("OREB_PCT");
  const iPACE     = idx("PACE");

  if ([iTeamName, iGP, iOFF, iDEF, iTS, iTO, iORR, iPACE].some(i => i === -1)) {
    throw new Error("NBA.com: missing expected columns in response");
  }

  const stats = {};
  for (const row of rows) {
    const teamName = row[iTeamName];
    const gp       = Number(row[iGP]);
    stats[teamName] = {
      OFF:  Number(row[iOFF]),
      DEF:  Number(row[iDEF]),
      TS:   Number(row[iTS]),
      TO:   Number(row[iTO]),
      ORR:  Number(row[iORR]),
      PACE: Number(row[iPACE]),
      GP:   gp,
    };
  }
  return stats;
}

// ── Public: season stats (backward compatible) ─────────────────────────────
export async function fetchNBAStats(dateTo = null, dateFrom = null, { seasonType = "Regular Season" } = {}) {
  const rangeStr = dateFrom
    ? `${toNBADate(dateFrom)} → ${dateTo ? toNBADate(dateTo) : "today"}`
    : (dateTo ? `up to ${toNBADate(dateTo)}` : "full season");
  console.log(`  [nba_stats] Fetching stats (${rangeStr}) [${seasonType}]...`);

  const stats = await fetchTeamStats(dateTo, dateFrom, { seasonType });
  const count = Object.keys(stats).length;
  const skipped = Object.values(stats).filter(s => s.GP < 1).length;
  console.log(`  [nba_stats] Got ${count} teams (${skipped} with < 1 game)`);

  // Filter out teams with zero games
  for (const [k, v] of Object.entries(stats)) {
    if (v.GP < 1) delete stats[k];
  }

  if (Object.keys(stats).length === 0) {
    throw new Error(`NBA.com stats incomplete: only ${Object.keys(stats).length} teams`);
  }

  return stats;
}

// ── Blend playoff + regular season stats per team ────────────────────────────
// In early playoffs, teams have few games played. Blend playoff stats with
// regular season stats so projections aren't based on 2-3 game samples.
// Weight ramps linearly: 0 playoff games → 100% regular season,
// 16 playoff games (conf finals) → 100% playoff stats.
const STAT_KEYS = ["OFF", "DEF", "TS", "TO", "ORR", "PACE"];
const PLAYOFF_RAMP_GAMES = 16;

function blendPlayoffStats(regSeason, playoffStats) {
  if (!playoffStats || !Object.keys(playoffStats).length) return regSeason;

  const blended = {};
  let blendCount = 0;

  for (const [team, rs] of Object.entries(regSeason)) {
    const po = playoffStats[team];
    if (!po || !po.GP || po.GP < 1) {
      blended[team] = { ...rs };
      continue;
    }

    const poWeight = Math.min(po.GP / PLAYOFF_RAMP_GAMES, 1.0);
    const out = { ...rs };
    for (const k of STAT_KEYS) {
      if (Number.isFinite(po[k]) && Number.isFinite(rs[k])) {
        out[k] = rs[k] * (1 - poWeight) + po[k] * poWeight;
      }
    }
    out.GP = rs.GP;
    blended[team] = out;
    blendCount++;
  }

  if (blendCount > 0) {
    console.log(`  [nba_stats] Blended playoff+regular for ${blendCount} teams`);
  }
  return blended;
}


// ── Public: enhanced stats (season + last10 + home + away) ─────────────────
// Returns { season, last10, home, away } — all keyed by full team name.
// In playoffs: fetches both regular season + playoff stats and blends them.
export async function fetchNBAStatsEnhanced(dateTo = null, { seasonType = "Regular Season" } = {}) {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const inPlayoffs = seasonType === "Playoffs";

  console.log(`  [nba_stats] Fetching enhanced stats (season + last10 + home + away) [${seasonType}]...`);

  // In playoffs: fetch regular season as base, then overlay playoff stats
  let regSeason = null;
  if (inPlayoffs) {
    regSeason = await fetchTeamStats(dateTo, null, { seasonType: "Regular Season" });
    console.log(`  [nba_stats] Regular season base: ${Object.keys(regSeason).length} teams`);
    await sleep(1000);
  }

  // 1. Season stats (must succeed)
  const rawSeason = await fetchTeamStats(dateTo, null, { seasonType });
  const season = inPlayoffs ? blendPlayoffStats(regSeason, rawSeason) : rawSeason;
  await sleep(1000);

  // 2. Last 10 (sequential — needs gap after season)
  let last10 = null;
  try {
    // In playoffs, use regular season L10 (more stable)
    const l10Type = inPlayoffs ? "Regular Season" : seasonType;
    // LastNGames counts from TODAY, not DateTo — useless for historical dates.
    // Fix: when dateTo is provided, use a DateFrom window (~25 days back) instead.
    if (dateTo) {
      const s = String(dateTo).replace(/-/g, "");
      const dt = new Date(Date.UTC(+s.slice(0,4), +s.slice(4,6)-1, +s.slice(6,8)));
      dt.setUTCDate(dt.getUTCDate() - 25);
      const from = dt.toISOString().slice(0, 10); // YYYY-MM-DD
      last10 = await fetchTeamStats(dateTo, from, { seasonType: l10Type });
    } else {
      last10 = await fetchTeamStats(dateTo, null, { lastNGames: 10, seasonType: l10Type });
    }
    console.log(`  [nba_stats] Last 10: ${Object.keys(last10).length} teams`);
  } catch (e) {
    console.warn(`  [nba_stats] Last 10 fetch failed (${e.message}) — skipping`);
    last10 = null;
  }
  await sleep(1000);

  // 3. Home + Away in parallel — use regular season in playoffs (too few playoff splits)
  let home = null, away = null;
  const splitType = inPlayoffs ? "Regular Season" : seasonType;
  const [homeResult, awayResult] = await Promise.allSettled([
    fetchTeamStats(dateTo, null, { location: "Home", seasonType: splitType }),
    fetchTeamStats(dateTo, null, { location: "Road", seasonType: splitType }),
  ]);

  if (homeResult.status === "fulfilled") {
    home = homeResult.value;
    console.log(`  [nba_stats] Home splits: ${Object.keys(home).length} teams`);
  } else {
    console.warn(`  [nba_stats] Home fetch failed (${homeResult.reason?.message}) — skipping`);
  }

  if (awayResult.status === "fulfilled") {
    away = awayResult.value;
    console.log(`  [nba_stats] Away splits: ${Object.keys(away).length} teams`);
  } else {
    console.warn(`  [nba_stats] Away fetch failed (${awayResult.reason?.message}) — skipping`);
  }

  // Filter season stats for min games (backward compat)
  for (const [k, v] of Object.entries(season)) {
    if (v.GP < 1) delete season[k];
  }
  const count = Object.keys(season).length;
  console.log(`  [nba_stats] Enhanced: ${count} teams (season), ${last10 ? Object.keys(last10).length : 0} (L10), ${home ? Object.keys(home).length : 0} (home), ${away ? Object.keys(away).length : 0} (away)`);

  if (count === 0) {
    throw new Error(`NBA.com stats incomplete: only ${count} teams`);
  }

  return { season, last10, home, away };
}
