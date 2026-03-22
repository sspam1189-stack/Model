// scripts/sources/nba_recent_stats.mjs
// Fetches team stats from NBA.com scoped to a date range (e.g. post-trade-deadline).
// Blends with full-season Hollinger stats to prevent small-sample volatility.
//
// Usage:
//   import { fetchBlendedStats } from "./sources/nba_recent_stats.mjs";
//   const stats = await fetchBlendedStats(hollingerStats, "02/06/2026");
//
// If NBA.com fetch fails, falls back to Hollinger stats only (safe degradation).

const NBA_BASE = "https://stats.nba.com/stats/leaguedashteamstats";
// Auto-detect season: NBA season starts in October, so Oct-Dec = current year, Jan-Sep = previous year
function currentSeason() {
  const now = new Date();
  const year = now.getFullYear();
  const month = now.getMonth() + 1; // 1-12
  const startYear = month >= 10 ? year : year - 1;
  return `${startYear}-${String(startYear + 1).slice(2)}`;
}
const SEASON = currentSeason();

const NBA_HEADERS = {
  "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
  "Referer":    "https://www.nba.com/",
  "Origin":     "https://www.nba.com",
  "Accept":     "application/json, text/plain, */*",
  "Accept-Language": "en-US,en;q=0.9",
  "x-nba-stats-origin": "stats",
  "x-nba-stats-token":  "true",
  "Connection": "keep-alive"
};

function todayMMDDYYYY() {
  const d = new Date();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const yyyy = d.getFullYear();
  return `${mm}/${dd}/${yyyy}`;
}

function normKey(s) {
  return String(s || "").toLowerCase().replace(/[^a-z0-9 ]/g, " ").replace(/  +/g, " ").trim();
}

// Map NBA.com full team names → Hollinger city keys
const NBA_TO_HOLLINGER = {
  "atlanta hawks":          "Atlanta",
  "boston celtics":         "Boston",
  "brooklyn nets":          "Brooklyn",
  "charlotte hornets":      "Charlotte",
  "chicago bulls":          "Chicago",
  "cleveland cavaliers":    "Cleveland",
  "dallas mavericks":       "Dallas",
  "denver nuggets":         "Denver",
  "detroit pistons":        "Detroit",
  "golden state warriors":  "Golden State",
  "houston rockets":        "Houston",
  "indiana pacers":         "Indiana",
  "la clippers":            "LA Clippers",
  "los angeles clippers":   "LA Clippers",
  "la lakers":              "LA Lakers",
  "los angeles lakers":     "LA Lakers",
  "memphis grizzlies":      "Memphis",
  "miami heat":             "Miami",
  "milwaukee bucks":        "Milwaukee",
  "minnesota timberwolves": "Minnesota",
  "new orleans pelicans":   "New Orleans",
  "new york knicks":        "New York",
  "oklahoma city thunder":  "Oklahoma City",
  "orlando magic":          "Orlando",
  "philadelphia 76ers":     "Philadelphia",
  "phoenix suns":           "Phoenix",
  "portland trail blazers": "Portland",
  "sacramento kings":       "Sacramento",
  "san antonio spurs":      "San Antonio",
  "toronto raptors":        "Toronto",
  "utah jazz":              "Utah",
  "washington wizards":     "Washington",
};

function mapTeamName(nbaName) {
  const k = normKey(nbaName);
  return NBA_TO_HOLLINGER[k] || null;
}

async function fetchNBAStats(measureType, dateFrom, dateTo) {
  const params = new URLSearchParams({
    MeasureType:   measureType,
    PerMode:       "PerGame",
    PaceAdjust:    "N",
    PlusMinus:     "N",
    Rank:          "N",
    Season:        SEASON,
    SeasonType:    "Regular Season",
    DateFrom:      dateFrom,
    DateTo:        dateTo,
    Outcome:       "",
    Location:      "",
    Month:         "0",
    SeasonSegment: "",
    OpponentTeamID:"0",
    VsConference:  "",
    VsDivision:    "",
    GameSegment:   "",
    Period:        "0",
    ShotClockRange:"",
    LastNGames:    "0"
  });

  const url = `${NBA_BASE}?${params}`;
  const res = await fetch(url, { headers: NBA_HEADERS });
  if (!res.ok) throw new Error(`NBA.com ${measureType} failed: ${res.status}`);

  const json = await res.json();
  const rs = json?.resultSets?.[0];
  if (!rs?.headers || !rs?.rowSet) throw new Error(`NBA.com ${measureType}: unexpected response shape`);

  // Convert to map: hollingerKey → { colName: value }
  const out = {};
  for (const row of rs.rowSet) {
    const obj = {};
    rs.headers.forEach((h, i) => { obj[h] = row[i]; });
    const teamName = obj.TEAM_NAME || "";
    const key = mapTeamName(teamName);
    if (key) out[key] = obj;
  }
  return out;
}

// Fetch post-deadline stats and blend with full-season Hollinger stats.
// recentWeight: 0.0–1.0, how much to weight recent stats (default 0.65)
export async function fetchBlendedStats(hollingerStats, dateFrom, recentWeight = 0.65) {
  const dateTo = todayMMDDYYYY();

  let advancedMap = null;
  let minGames = null;

  try {
    console.log(`  [nba_recent] Fetching post-deadline stats (${dateFrom} → ${dateTo})...`);
    [advancedMap] = await Promise.all([
      fetchNBAStats("Advanced", dateFrom, dateTo),
    ]);

    const gameCounts = Object.values(advancedMap).map(r => r.GP || 0);
    minGames = Math.min(...gameCounts);
    console.log(`  [nba_recent] Got ${Object.keys(advancedMap).length} teams, min games: ${minGames}`);
  } catch (e) {
    console.warn(`  [nba_recent] NBA.com fetch failed (${e.message}) — using Hollinger only`);
    return hollingerStats;
  }

  // If too few games (< 5), don't trust recent stats — fall back to Hollinger
  if (minGames < 5) {
    console.warn(`  [nba_recent] Too few games (${minGames}) — using Hollinger only`);
    return hollingerStats;
  }

  const w = recentWeight;
  const blended = { ...hollingerStats };

  for (const [key, h] of Object.entries(hollingerStats)) {
    const recent = advancedMap[key];
    if (!recent) {
      console.warn(`  [nba_recent] No recent data for ${key} — keeping Hollinger`);
      continue;
    }

    // NBA.com Advanced: OFF_RATING, DEF_RATING, PACE, TS_PCT, TM_TOV_PCT, OREB_PCT
    const rOFF  = Number(recent.OFF_RATING);
    const rDEF  = Number(recent.DEF_RATING);
    const rPACE = Number(recent.PACE);
    const rTS   = Number(recent.TS_PCT);      // already 0–1 decimal
    const rTO   = Number(recent.TM_TOV_PCT);  // already 0–1 decimal (e.g. 0.135)
    const rORR  = Number(recent.OREB_PCT);    // already 0–1 decimal (e.g. 0.329)

    blended[key] = {
      ...h,
      OFF:  Number.isFinite(rOFF)  ? (w * rOFF  + (1-w) * h.OFF)  : h.OFF,
      DEF:  Number.isFinite(rDEF)  ? (w * rDEF  + (1-w) * h.DEF)  : h.DEF,
      PACE: Number.isFinite(rPACE) ? (w * rPACE + (1-w) * h.PACE) : h.PACE,
      TS:   Number.isFinite(rTS)   ? (w * rTS   + (1-w) * h.TS)   : h.TS,
      TO:   Number.isFinite(rTO)   ? (w * rTO   + (1-w) * h.TO)   : h.TO,
      ORR:  Number.isFinite(rORR)  ? (w * rORR  + (1-w) * h.ORR)  : h.ORR,
    };
  }

  return blended;
}
