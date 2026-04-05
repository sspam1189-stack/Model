// scripts/sources/injuries.mjs
// Fetches per-game player availability from ESPN's game summary endpoint.
// This catches ALL reasons a player is out — injury, rest, personal, DTD, etc.
//
// Flow:
//   1. Fetch today's ESPN scoreboard → get event IDs + team names
//   2. For each game, hit /summary?event={id} → injuries[] per competitor
//   3. Cross-reference player MPG from NBA.com to classify tier
//
// Exports:
//   fetchInjuryData()  → { report, playerMPG }
//   getKeyInjuries(report, teamName) → [{ player, status, tier, mpg }]
//   gameUncertaintyScore(awayInj, homeInj) → number 0–4

function stripAccents(s) {
  return s.normalize("NFD").replace(/[\u0300-\u036f]/g, "");
}

const NBA_HEADERS = {
  "User-Agent":          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
  "Referer":             "https://www.nba.com/",
  "Origin":              "https://www.nba.com",
  "Accept":              "application/json, text/plain, */*",
  "Accept-Language":     "en-US,en;q=0.9",
  "x-nba-stats-origin": "stats",
  "x-nba-stats-token":  "true",
};

function currentSeason() {
  const now   = new Date();
  const year  = now.getFullYear();
  const month = now.getMonth() + 1;
  const start = month >= 10 ? year : year - 1;
  return `${start}-${String(start + 1).slice(2)}`;
}

function todayESPN() {
  const fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Chicago",
    year: "numeric", month: "2-digit", day: "2-digit"
  });
  return fmt.format(new Date()).replace(/-/g, ""); // YYYYMMDD
}

// ── Player MPG from NBA.com ─────────────────────────────────────────────────

// Attempt 1: leaguedashplayerstats (full stats endpoint)
async function fetchMPG_leaguedash(seasonType = "Regular Season") {
  const season = currentSeason();
  const params = new URLSearchParams({
    College: "", Conference: "", Country: "",
    DateFrom: "", DateTo: "", Division: "",
    DraftPick: "", DraftYear: "", GameScope: "",
    GameSegment: "", Height: "", ISTRound: "",
    LastNGames: "0", LeagueID: "00",
    Location: "", MeasureType: "Base",
    Month: "0", OpponentTeamID: "0",
    Outcome: "", PORound: "0",
    PaceAdjust: "N", PerMode: "PerGame",
    Period: "0", PlayerExperience: "",
    PlayerPosition: "", PlusMinus: "N",
    Rank: "N", Season: season,
    SeasonSegment: "", SeasonType: "Regular Season",  // Always Regular Season for MPG — playoff sample too small
    ShotClockRange: "", StarterBench: "",
    TeamID: "0", TwoWay: "0",
    VsConference: "", VsDivision: "", Weight: "",
  });

  const url = `https://stats.nba.com/stats/leaguedashplayerstats?${params}`;
  console.log("  [injuries] Trying leaguedashplayerstats...");

  const res = await fetch(url, { headers: NBA_HEADERS });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);

  const json = await res.json();
  const resultSet = json?.resultSets?.[0];
  if (!resultSet) throw new Error("no resultSet in response");

  const headers = resultSet.headers;
  const rows    = resultSet.rowSet;

  if (!headers?.length || !rows?.length) {
    throw new Error(`empty response (${headers?.length || 0} headers, ${rows?.length || 0} rows)`);
  }

  const iName = headers.indexOf("PLAYER_NAME");
  const iTeam = headers.indexOf("TEAM_NAME") !== -1 ? headers.indexOf("TEAM_NAME") : headers.indexOf("TEAM_ABBREVIATION");
  const iMIN  = headers.indexOf("MIN");
  const iGP   = headers.indexOf("GP");

  if ([iName, iTeam, iMIN, iGP].some(i => i === -1)) {
    console.warn(`  [injuries] leaguedash headers received: ${headers.slice(0, 10).join(", ")}...`);
    throw new Error(`missing columns (got: ${headers.filter(h => ["PLAYER_NAME","TEAM_NAME","TEAM_ABBREVIATION","MIN","GP"].includes(h)).join(",")})`);
  }

  const useAbbrev = headers.indexOf("TEAM_NAME") === -1;
  return { headers, rows, iName, iTeam, iMIN, iGP, useAbbrev };
}

// Attempt 2: leagueplayerondetails (alternative endpoint, different param set)
async function fetchMPG_playerindex(seasonType = "Regular Season") {
  const season = currentSeason();
  const params = new URLSearchParams({
    College: "", Country: "", DraftPick: "", DraftRound: "", DraftYear: "",
    Height: "", Historical: "1", LeagueID: "00", Season: season,
    SeasonType: "Regular Season", TeamID: "0", Weight: "",  // Always Regular Season for MPG
  });

  const url = `https://stats.nba.com/stats/playerindex?${params}`;
  console.log("  [injuries] Trying playerindex fallback...");

  const res = await fetch(url, { headers: NBA_HEADERS });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);

  const json = await res.json();
  const resultSet = json?.resultSets?.[0];
  if (!resultSet) throw new Error("no resultSet in response");

  const headers = resultSet.headers;
  const rows    = resultSet.rowSet;

  if (!headers?.length || !rows?.length) {
    throw new Error(`empty response (${headers?.length || 0} headers, ${rows?.length || 0} rows)`);
  }

  // playerindex has different column names
  const iFirst = headers.indexOf("PLAYER_FIRST_NAME");
  const iLast  = headers.indexOf("PLAYER_LAST_NAME");
  const iTeam  = headers.indexOf("TEAM_NAME");

  if ([iFirst, iLast, iTeam].some(i => i === -1)) {
    console.warn(`  [injuries] playerindex headers: ${headers.slice(0, 10).join(", ")}...`);
    throw new Error("playerindex missing expected columns");
  }

  // playerindex doesn't have MPG — we'll use it for team membership only
  // and estimate MPG from other signals
  return { headers, rows, iFirst, iLast, iTeam, isIndex: true };
}

// Attempt 3: ESPN roster minutes (completely different source)
async function fetchMPG_espn(espnType = 2) {
  console.log("  [injuries] Trying ESPN stats fallback...");

  // Use ESPN's per-player stats page which is more stable than /leaders
  // Always use regular season (type 2) for MPG — playoff sample too small
  const urls = [
    "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/statistics/byathlete?region=us&lang=en&contentorigin=espn&is498=true&type=team&limit=500&sort=general.mpg:desc",
    `https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/seasons/2026/types/2/leaders?limit=500`,
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/leaders?limit=500",
  ];

  for (const url of urls) {
    try {
      const res = await fetch(url, {
        headers: { "User-Agent": "Mozilla/5.0", Accept: "application/json" }
      });
      if (!res.ok) continue;

      const json = await res.json();
      const mpgMap = {};

      // Try different response shapes ESPN might use
      const categories = json?.leaders?.categories || json?.categories || [];
      const minCat = categories.find(c =>
        c.name === "minutesPG" || c.name === "minutes" ||
        c.abbreviation === "MIN" || c.displayName === "Minutes Per Game"
      );

      if (minCat?.leaders?.length) {
        for (const entry of minCat.leaders) {
          const name = entry?.athlete?.displayName;
          const team = entry?.athlete?.team?.displayName;
          const mpg  = parseFloat(entry?.displayValue || entry?.value || "0");
          if (name && team && mpg > 0) {
            mpgMap[name] = { team, mpg, gp: 50 };
          }
        }
      }

      // Also try flat athlete stats format
      if (!Object.keys(mpgMap).length && json?.athletes?.length) {
        for (const ath of json.athletes) {
          const name = ath?.athlete?.displayName;
          const team = ath?.athlete?.team?.displayName;
          const stats = ath?.stats || ath?.categories?.[0]?.stats || [];
          const mpgStat = stats.find(s => s.name === "mpg" || s.abbreviation === "MIN");
          const mpg = parseFloat(mpgStat?.displayValue || mpgStat?.value || "0");
          if (name && team && mpg > 0) {
            mpgMap[name] = { team, mpg, gp: 50 };
          }
        }
      }

      if (Object.keys(mpgMap).length >= 50) {
        return mpgMap;
      }
    } catch (e) { /* try next URL */ }
  }

  throw new Error("all ESPN endpoints failed or returned insufficient data");
}

export async function fetchPlayerMPG({ seasonType = "Regular Season", espnType = 2 } = {}) {
  // Try NBA.com leaguedash first (best data), then playerindex, then ESPN
  let mpgMap = {};

  // Attempt 1: leaguedashplayerstats
  try {
    const data = await fetchMPG_leaguedash(seasonType);

    // If NBA.com returned abbreviations instead of full names, map them
    const ABBREV_TO_NAME = {
      ATL: "Atlanta Hawks", BOS: "Boston Celtics", BKN: "Brooklyn Nets",
      CHA: "Charlotte Hornets", CHI: "Chicago Bulls", CLE: "Cleveland Cavaliers",
      DAL: "Dallas Mavericks", DEN: "Denver Nuggets", DET: "Detroit Pistons",
      GSW: "Golden State Warriors", HOU: "Houston Rockets", IND: "Indiana Pacers",
      LAC: "LA Clippers", LAL: "Los Angeles Lakers", MEM: "Memphis Grizzlies",
      MIA: "Miami Heat", MIL: "Milwaukee Bucks", MIN: "Minnesota Timberwolves",
      NOP: "New Orleans Pelicans", NYK: "New York Knicks", OKC: "Oklahoma City Thunder",
      ORL: "Orlando Magic", PHI: "Philadelphia 76ers", PHX: "Phoenix Suns",
      POR: "Portland Trail Blazers", SAC: "Sacramento Kings", SAS: "San Antonio Spurs",
      TOR: "Toronto Raptors", UTA: "Utah Jazz", WAS: "Washington Wizards",
    };

    for (const row of data.rows) {
      const name   = row[data.iName];
      const rawTm  = row[data.iTeam];
      const team   = data.useAbbrev ? (ABBREV_TO_NAME[rawTm] || rawTm) : rawTm;
      const mpg    = Number(row[data.iMIN]);
      const gp     = Number(row[data.iGP]);
      if (!name || gp < 5) continue;
      mpgMap[name] = { team, mpg, gp };
    }
    if (Object.keys(mpgMap).length > 100) {
      console.log(`  [injuries] Got MPG for ${Object.keys(mpgMap).length} players (leaguedash${data.useAbbrev ? ", abbrev→name" : ""})`);
      return mpgMap;
    }
    console.warn(`  [injuries] leaguedash returned only ${Object.keys(mpgMap).length} players, trying fallback...`);
  } catch (e) {
    console.warn(`  [injuries] leaguedash failed: ${e.message}`);
  }

  // Attempt 2: ESPN leaders
  try {
    mpgMap = await fetchMPG_espn(espnType);
    console.log(`  [injuries] Got MPG for ${Object.keys(mpgMap).length} players (ESPN fallback)`);
    return mpgMap;
  } catch (e) {
    console.warn(`  [injuries] ESPN fallback failed: ${e.message}`);
  }

  // All failed — return empty (system degrades gracefully)
  console.warn("  [injuries] All MPG sources failed — injury tiers will default to bench");
  return {};
}

// ── ESPN per-game availability ──────────────────────────────────────────────
// Returns a map of teamDisplayName -> [{ player, status, reason }]
// Only includes players listed as Out, Doubtful, or Questionable for THIS specific game.
async function fetchGameAvailability(date) {
  const day = date || todayESPN();

  // Step 1: get event IDs — use header API (returns all games) with scoreboard fallback
  let events = [];
  try {
    const hdrUrl = `https://site.web.api.espn.com/apis/v2/scoreboard/header?sport=basketball&league=nba&dates=${day}`;
    const hdrRes = await fetch(hdrUrl, { headers: { "User-Agent": "Mozilla/5.0", Accept: "application/json" } });
    if (hdrRes.ok) {
      const hdr = await hdrRes.json();
      const leagues = hdr?.sports?.[0]?.leagues?.[0];
      events = (leagues?.events || []).map(e => ({ id: e.id }));
    }
  } catch (_) { /* fall through to scoreboard */ }

  if (!events.length) {
    const sbUrl = `https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates=${day}`;
    const sbRes = await fetch(sbUrl, { headers: { "User-Agent": "Mozilla/5.0", Accept: "application/json" } });
    if (!sbRes.ok) throw new Error(`ESPN scoreboard failed: ${sbRes.status}`);
    const sb = await sbRes.json();
    events = sb?.events || [];
  }
  if (!events.length) {
    console.log("  [injuries] No games today on ESPN scoreboard");
    return {};
  }

  console.log(`  [injuries] Fetching game-specific availability for ${events.length} games...`);

  const availability = {}; // teamDisplayName -> [{ player, status, reason }]

  for (const ev of events) {
    const eventId = ev?.id;
    if (!eventId) continue;

    // Step 2: game summary -> injuries per competitor (includes rest, personal, etc.)
    const sumUrl = `https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event=${eventId}`;
    const sumRes = await fetch(sumUrl, {
      headers: { "User-Agent": "Mozilla/5.0", Accept: "application/json" }
    }).catch(() => null);
    if (!sumRes?.ok) continue;

    const summary = await sumRes.json().catch(() => null);
    if (!summary) continue;

    // injuries[] in the summary is per-team, per-game — exactly what we need
    const injEntries = summary?.injuries || [];
    for (const teamEntry of injEntries) {
      const teamName = teamEntry?.team?.displayName;
      if (!teamName) continue;

      const players = [];
      for (const inj of teamEntry?.injuries || []) {
        const playerName = inj?.athlete?.displayName;
        if (!playerName) continue;

        // status: "Out", "Questionable", "Doubtful", "Day-To-Day", "Probable", etc.
        const rawStatus = inj?.status || "";
        const status    = normalizeStatus(rawStatus);
        if (status === "active" || status === "probable") continue;

        // reason includes: "Injury/Illness", "Rest", "Personal Reasons", "Load Management", etc.
        const reason = inj?.shortComment || inj?.longComment || rawStatus || "";
        players.push({ player: playerName, status, reason });
      }

      if (players.length) {
        availability[teamName] = players;
      }
    }
  }

  const total = Object.values(availability).reduce((s, v) => s + v.length, 0);
  console.log(`  [injuries] ${total} players listed out/limited for today's games`);
  return availability;
}

// ── Helpers ─────────────────────────────────────────────────────────────────
function normalizeStatus(raw) {
  const s = String(raw || "").toLowerCase();
  if (s.includes("out"))                                                          return "out";
  if (s.includes("doubtful"))                                                     return "doubtful";
  if (s.includes("questionable") || s.includes("day-to-day") || s.includes("dtd")) return "questionable";
  if (s.includes("probable"))                                                     return "probable";
  return "active";
}

function classifyTier(mpg) {
  if (mpg >= 32) return "star";
  if (mpg >= 22) return "starter";
  return "bench";
}

// ── Main export ─────────────────────────────────────────────────────────────
// report -> { "Boston Celtics": [{ player, status, tier, mpg, reason }] }
export async function fetchInjuryData(date = null, { seasonType = "Regular Season", espnType = 2 } = {}) {
  const [gameAvailability, playerMPG] = await Promise.all([
    fetchGameAvailability(date).catch(e => {
      console.warn("  [injuries] Game availability fetch failed:", e.message);
      return {};
    }),
    fetchPlayerMPG({ seasonType, espnType }).catch(e => {
      console.warn("  [injuries] MPG fetch failed:", e.message);
      return {};
    }),
  ]);

  const report = {};

  for (const [teamName, players] of Object.entries(gameAvailability)) {
    const enriched = players.map(p => {
      // Look up MPG — exact match first, then accent-stripped / last-name fallback
      let mpgEntry = playerMPG[p.player];
      if (!mpgEntry) {
        const stripped = stripAccents(p.player).toLowerCase();
        const lastName = stripped.split(" ").pop();
        const found = Object.keys(playerMPG).find(k =>
          (stripAccents(k).toLowerCase() === stripped ||
           stripAccents(k).split(" ").pop().toLowerCase() === lastName) &&
          playerMPG[k].team === teamName
        );
        if (found) mpgEntry = playerMPG[found];
      }

      const mpg  = mpgEntry?.mpg ?? 0;
      const tier = classifyTier(mpg);
      return { ...p, tier, mpg };
    });

    report[teamName] = enriched;
  }

  return { report, playerMPG };
}

// ── Convenience exports ───────────────────────────────────────────────────────
// Returns only Out/Doubtful players who are Star or Starter tier
const TEAM_ALIASES = {
  "Los Angeles Clippers": "LA Clippers",
  "LA Clippers": "Los Angeles Clippers",
};

export function getKeyInjuries(report, teamName, playerMPG = null, { recentInjuryDates = null } = {}) {
  const entries = report[teamName] || report[TEAM_ALIASES[teamName]] || [];

  // Build set of long-term out players (5+ days) — these are already in team stats
  // and not used in lineup adjustment, so hide them from dashboard
  const longTermOut = new Set();
  if (recentInjuryDates && Object.keys(recentInjuryDates).length >= 5) {
    for (const entry of entries) {
      if (entry.status !== "out" && entry.status !== "doubtful") continue;
      const name = entry.player;
      const lastName = name.split(" ").pop().toLowerCase();
      let datesOut = 0;
      for (const dateReport of Object.values(recentInjuryDates)) {
        const teamInj = dateReport[teamName] || dateReport[TEAM_ALIASES[teamName]] || [];
        const wasOut = teamInj.some(inj =>
          (inj.status === "out" || inj.status === "doubtful") &&
          (inj.player === name || inj.player.split(" ").pop().toLowerCase() === lastName)
        );
        if (wasOut) datesOut++;
      }
      if (datesOut >= 5) longTermOut.add(name);
    }
  }

  return entries.filter(p => {
    if (p.status !== "out" && p.status !== "doubtful") return false;
    if (p.tier !== "star" && p.tier !== "starter") return false;
    if (longTermOut.has(p.player)) return false;
    if (playerMPG) {
      let mpgEntry = playerMPG[p.player];
      if (!mpgEntry) {
        const stripped = stripAccents(p.player).toLowerCase();
        const key = Object.keys(playerMPG).find(k => stripAccents(k).toLowerCase() === stripped);
        if (key) mpgEntry = playerMPG[key];
      }
      if (!mpgEntry || mpgEntry.gp < 10) return false;
    }
    return true;
  });
}

// Returns 0 (no concern) -> 5+ (skip the game)
// Tuned down: original values were too aggressive, causing most games to PASS
// when the injury feed is working properly.
export function gameUncertaintyScore(awayInjuries, homeInjuries) {
  let score = 0;
  for (const inj of [...awayInjuries, ...homeInjuries]) {
    if (inj.tier === "star" && inj.status === "out") score += 1;
    if (inj.tier === "star" && inj.status === "doubtful") score += 1;
  }
  return Math.min(score, 5);
}

// ── Out-for-season detection ────────────────────────────────────────────────
// Fetches the ESPN league-wide injuries page and returns a Set of player names
// whose fantasyStatus is "OFS" (Out For Season).
export async function fetchOutForSeason(gameInjuryReport = {}) {
  const url = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries";
  try {
    const res = await fetch(url, {
      headers: { "User-Agent": "Mozilla/5.0", Accept: "application/json" },
    }).catch(() => null);
    if (!res?.ok) {
      console.warn("  [injuries] ESPN league-wide injuries fetch failed");
      return new Set();
    }
    const data = await res.json().catch(() => null);
    if (!data?.injuries) return new Set();

    // Build set of players listed as questionable/DTD in today's per-game report
    // These players are day-to-day, not truly out — don't flag them
    const gameDTD = new Set();
    for (const players of Object.values(gameInjuryReport)) {
      for (const p of players) {
        if (p.status === "questionable" || p.status === "doubtful") {
          gameDTD.add(p.player);
        }
      }
    }

    const today = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" })
      .format(new Date());  // YYYY-MM-DD
    const ofsPlayers = new Set();
    for (const team of data.injuries) {
      for (const inj of team.injuries || []) {
        const status = (inj?.status || "").toLowerCase();
        const returnDate = inj?.details?.returnDate || "";
        if (status === "out" && returnDate > today) {
          const name = inj?.athlete?.displayName;
          if (name && !gameDTD.has(name)) {
            ofsPlayers.add(name);
          }
        }
      }
    }
    if (gameDTD.size) console.log(`  [injuries] Excluded ${gameDTD.size} game-day DTD/questionable players from out list`);
    console.log(`  [injuries] Found ${ofsPlayers.size} players still out (returnDate > ${today})`);
    return ofsPlayers;
  } catch (e) {
    console.warn(`  [injuries] OFS fetch error: ${e.message}`);
    return new Set();
  }
}
