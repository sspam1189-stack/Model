// sources/h2h_matchup.mjs
// ────────────────────────────────────────────────────────────────────────────
// Fetches season game logs from NBA.com and parses them into H2H matchup
// pairs. The adjustment computation lives in model_engine.mjs (computeH2HAdj).
//
// Returns an object keyed by "TeamA::TeamB" (sorted alphabetically) with
// an array of game results for each matchup pair.
// ────────────────────────────────────────────────────────────────────────────

const SEASON = "2025-26";
const GAME_LOG_URL = "https://stats.nba.com/stats/leaguegamelog";
const NBA_HEADERS = {
  "User-Agent": "Mozilla/5.0",
  "Accept": "application/json",
  "Referer": "https://www.nba.com/",
  "x-nba-stats-origin": "stats",
  "x-nba-stats-token": "true",
};


// ── Fetch all team game logs for the season ────────────────────────────────

async function fetchSeasonGameLogs(season = SEASON) {
  const params = new URLSearchParams({
    Counter: "0",
    Direction: "DESC",
    LeagueID: "00",
    PlayerOrTeam: "T",
    Season: season,
    SeasonType: "Regular Season",
    Sorter: "DATE",
  });

  const url = `${GAME_LOG_URL}?${params}`;
  const resp = await fetch(url, { headers: NBA_HEADERS });
  if (!resp.ok) throw new Error(`NBA game log fetch failed: ${resp.status}`);

  const data = await resp.json();
  const headers = data.resultSets[0].headers;
  const rows = data.resultSets[0].rowSet;

  return rows.map(row => {
    const obj = {};
    headers.forEach((h, i) => obj[h] = row[i]);
    return obj;
  });
}


// ── Parse game logs into H2H matchup pairs ─────────────────────────────────
//
// Each game has TWO rows in the log (one per team). GAME_ID links them.
// MATCHUP field: "TOR vs. CLE" = home, "CLE @ TOR" = away.

function parseMatchups(gameLogs) {
  const byGame = {};
  for (const row of gameLogs) {
    const gid = row.GAME_ID;
    if (!byGame[gid]) byGame[gid] = [];
    byGame[gid].push(row);
  }

  const matchups = {};

  for (const [gid, rows] of Object.entries(byGame)) {
    if (rows.length !== 2) continue;

    let home = null, away = null;
    for (const r of rows) {
      if (r.MATCHUP.includes("vs.")) home = r;
      else if (r.MATCHUP.includes("@")) away = r;
    }
    if (!home || !away) continue;

    const key = [home.TEAM_NAME, away.TEAM_NAME].sort().join("::");

    if (!matchups[key]) {
      matchups[key] = {
        team1: [home.TEAM_NAME, away.TEAM_NAME].sort()[0],
        team2: [home.TEAM_NAME, away.TEAM_NAME].sort()[1],
        games: [],
      };
    }

    matchups[key].games.push({
      gameId: gid,
      date: home.GAME_DATE,
      home: {
        team: home.TEAM_NAME,
        pts: home.PTS,
        fgPct: home.FG_PCT,
        fg3Pct: home.FG3_PCT,
        reb: home.REB,
        ast: home.AST,
        tov: home.TOV,
        plusMinus: home.PLUS_MINUS,
      },
      away: {
        team: away.TEAM_NAME,
        pts: away.PTS,
        fgPct: away.FG_PCT,
        fg3Pct: away.FG3_PCT,
        reb: away.REB,
        ast: away.AST,
        tov: away.TOV,
        plusMinus: away.PLUS_MINUS,
      },
    });
  }

  return matchups;
}


// ── Main export ─────────────────────────────────────────────────────────────

export async function fetchH2HMatchups(season = SEASON) {
  console.log("  [h2h] Fetching season game logs...");
  const logs = await fetchSeasonGameLogs(season);
  console.log(`  [h2h] Got ${logs.length} game log entries`);

  const matchups = parseMatchups(logs);
  const nMatchups = Object.keys(matchups).length;
  const totalGames = Object.values(matchups).reduce((s, m) => s + m.games.length, 0);
  console.log(`  [h2h] Parsed ${totalGames} games across ${nMatchups} matchup pairs`);

  return matchups;
}
