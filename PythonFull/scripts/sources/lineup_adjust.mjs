// scripts/sources/lineup_adjust.mjs
// Lineup-adjusted team efficiency: replaces season-long team OFF/DEF/TS/TO/ORR
// with tonight's expected values given who's actually playing.
//
// How it works:
//   1. Fetch per-player advanced stats (OFF_RATING, DEF_RATING, TS_PCT, etc.)
//      from NBA.com leaguedashplayerstats (MeasureType=Advanced).
//      These are "team's rate when this player is on court" — their minutes-weighted
//      average ≈ the team-level number, so the delta approach is clean.
//
//   2. For each team with injuries tonight:
//      a. Compute full-roster weighted average (all players, weighted by MPG)
//      b. Remove OUT/DOUBTFUL players, redistribute their minutes proportionally
//      c. Compute available-roster weighted average
//      d. Delta = available - full
//      e. Adjusted team stat = season stat + delta
//
//   3. Teams with no injuries pass through unchanged.
//
// Safe degradation: if any fetch fails, returns original stats untouched.
//
// Usage:
//   import { fetchPlayerAdvanced, adjustTeamStats } from "./sources/lineup_adjust.mjs";
//   const playerAdv = await fetchPlayerAdvanced();
//   const adjusted  = adjustTeamStats(teamStats, injuryReport, playerMPG, playerAdv, games);

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
  const now = new Date();
  const year = now.getFullYear();
  const month = now.getMonth() + 1;
  const start = month >= 10 ? year : year - 1;
  return `${start}-${String(start + 1).slice(2)}`;
}

// ── Fetch per-player advanced stats ─────────────────────────────────────────
// Returns: { [playerName]: { team, min, gp, offRtg, defRtg, tsPct, tovPct, orbPct, pace, netRtg } }

export async function fetchPlayerAdvanced({ seasonType = "Regular Season" } = {}) {
  const season = currentSeason();
  const params = new URLSearchParams({
    College: "", Conference: "", Country: "",
    DateFrom: "", DateTo: "", Division: "",
    DraftPick: "", DraftYear: "", GameScope: "",
    GameSegment: "", Height: "", ISTRound: "",
    LastNGames: "0", LeagueID: "00",
    Location: "", MeasureType: "Advanced",
    Month: "0", OpponentTeamID: "0",
    Outcome: "", PORound: "0",
    PaceAdjust: "N", PerMode: "PerGame",
    Period: "0", PlayerExperience: "",
    PlayerPosition: "", PlusMinus: "N",
    Rank: "N", Season: season,
    SeasonSegment: "", SeasonType: "Regular Season",
    ShotClockRange: "", StarterBench: "",
    TeamID: "0", TwoWay: "0",
    VsConference: "", VsDivision: "", Weight: "",
  });

  const url = `https://stats.nba.com/stats/leaguedashplayerstats?${params}`;


  const res = await fetch(url, { headers: NBA_HEADERS });
  if (!res.ok) throw new Error(`leaguedashplayerstats Advanced failed: HTTP ${res.status}`);

  const json = await res.json();
  const rs = json?.resultSets?.[0];
  if (!rs?.headers || !rs?.rowSet) throw new Error("unexpected response shape");

  const headers = rs.headers;
  const rows = rs.rowSet;

  const idx = (name) => headers.indexOf(name);
  const iName   = idx("PLAYER_NAME");
  const iTeam   = idx("TEAM_ABBREVIATION");
  const iTeamNm = idx("TEAM_NAME");     // may not exist in all responses
  const iMIN    = idx("MIN");
  const iGP     = idx("GP");
  const iOFF    = idx("OFF_RATING");
  const iDEF    = idx("DEF_RATING");
  const iNET    = idx("NET_RATING");
  const iTS     = idx("TS_PCT");
  const iTOV    = idx("TM_TOV_PCT");    // team turnover % when player is on court
  const iORB    = idx("OREB_PCT");      // team offensive rebound % when player is on court
  const iPACE   = idx("PACE");

  // Minimum required columns
  if ([iName, iMIN, iGP, iOFF, iDEF].some(i => i === -1)) {
    throw new Error(`missing expected columns (got: ${headers.slice(0, 15).join(", ")})`);
  }

  // Abbreviation → full name map (same as injuries.mjs)
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

  const players = {};
  let skipped = 0;

  for (const row of rows) {
    const name = row[iName];
    const gp   = Number(row[iGP]);
    const min  = Number(row[iMIN]);

    // Skip players with very few games or very low minutes — too noisy
    if (gp < 10 || min < 5) { skipped++; continue; }

    const abbrev = iTeam !== -1 ? row[iTeam] : "";
    const teamNm = iTeamNm !== -1 ? row[iTeamNm] : "";
    const team   = teamNm || ABBREV_TO_NAME[abbrev] || abbrev;

    players[name] = {
      team,
      min,
      gp,
      offRtg:  Number(row[iOFF]),
      defRtg:  Number(row[iDEF]),
      netRtg:  iNET !== -1 ? Number(row[iNET]) : null,
      tsPct:   iTS  !== -1 ? Number(row[iTS])  : null,
      tovPct:  iTOV !== -1 ? Number(row[iTOV]) : null,
      orbPct:  iORB !== -1 ? Number(row[iORB]) : null,
      pace:    iPACE !== -1 ? Number(row[iPACE]) : null,
    };
  }

  const count = Object.keys(players).length;

  return players;
}


// ── Team name matching ──────────────────────────────────────────────────────

function normKey(s) {
  return String(s || "").toLowerCase().replace(/[^a-z0-9 ]/g, " ").replace(/ +/g, " ").trim();
}

// Match a team name from injury report / odds to the stats object key
function resolveTeamName(teamName, knownKeys) {
  if (!teamName) return null;
  const wanted = normKey(teamName);
  for (const k of knownKeys) {
    if (normKey(k) === wanted) return k;
  }
  // Substring fallback
  for (const k of knownKeys) {
    const nk = normKey(k);
    if (nk.includes(wanted) || wanted.includes(nk)) return k;
  }
  return null;
}


// ── Core: Compute lineup-adjusted team stats ────────────────────────────────
//
// teamStats:     { "Cleveland Cavaliers": { OFF, DEF, TS, TO, ORR, PACE, GP } }
// injuryReport:  { "Cleveland Cavaliers": [{ player, status, tier, mpg }] }
// playerMPG:     { "Donovan Mitchell": { team: "Cleveland Cavaliers", mpg: 35, gp: 60 } }
// playerAdv:     { "Donovan Mitchell": { team, min, gp, offRtg, defRtg, tsPct, tovPct, orbPct } }
// todaysGames:   [{ away: "...", home: "..." }]  — just need team names to know who's playing
//
// Returns: adjusted copy of teamStats (same shape). Teams not playing today or
//          with no meaningful injuries pass through unchanged.

export function adjustTeamStats(teamStats, injuryReport, playerMPG, playerAdv, todaysGames) {
  if (!playerAdv || !Object.keys(playerAdv).length) {

    return teamStats;
  }

  const adjusted = { ...teamStats };
  const teamKeys = Object.keys(teamStats);

  // Build set of teams playing tonight
  const teamsTonight = new Set();
  for (const g of todaysGames) {
    if (g.away) teamsTonight.add(g.away);
    if (g.home) teamsTonight.add(g.home);
  }

  // Group players by team from the advanced stats
  const playersByTeam = {};
  for (const [name, p] of Object.entries(playerAdv)) {
    const teamKey = resolveTeamName(p.team, teamKeys);
    if (!teamKey) continue;
    if (!playersByTeam[teamKey]) playersByTeam[teamKey] = [];
    playersByTeam[teamKey].push({ name, ...p });
  }

  let adjustedCount = 0;

  for (const teamKey of teamKeys) {
    // Only adjust teams playing tonight
    const isTonight = teamsTonight.has(teamKey) ||
      [...teamsTonight].some(t => resolveTeamName(t, [teamKey]));
    if (!isTonight) continue;

    // Get injury report for this team
    const injKey = resolveTeamName(teamKey, Object.keys(injuryReport || {}));
    const injuries = injKey ? injuryReport[injKey] : [];
    if (!injuries || !injuries.length) continue;

    // Only care about players who are OUT or DOUBTFUL
    const outPlayers = new Set(
      injuries
        .filter(i => i.status === "out" || i.status === "doubtful")
        .map(i => i.player)
    );
    if (!outPlayers.size) continue;

    // Get all rostered players for this team
    const roster = playersByTeam[teamKey];
    if (!roster || roster.length < 5) {

      continue;
    }

    // Match out players to roster using exact + fuzzy name matching
    const rosterOut = new Set();
    for (const outName of outPlayers) {
      // Exact match
      let found = roster.find(r => r.name === outName);
      // Last-name fallback (same as injuries.mjs)
      if (!found) {
        const lastName = outName.split(" ").pop().toLowerCase();
        found = roster.find(r =>
          r.name.split(" ").pop().toLowerCase() === lastName
        );
      }
      if (found) rosterOut.add(found.name);
    }

    if (!rosterOut.size) continue;

    // Separate available vs out
    const available = roster.filter(r => !rosterOut.has(r.name));
    const out       = roster.filter(r => rosterOut.has(r.name));

    if (available.length < 5) {

      continue;
    }

    // Compute full-roster weighted averages (weighted by minutes)
    const fullTotalMin = roster.reduce((s, r) => s + r.min, 0);
    if (fullTotalMin <= 0) continue;

    const weightedAvg = (players, totalMin, getter) => {
      let sum = 0, validMin = 0;
      for (const p of players) {
        const val = getter(p);
        if (Number.isFinite(val)) {
          sum += p.min * val;
          validMin += p.min;
        }
      }
      return validMin > 0 ? sum / validMin : null;
    };

    // Full-roster weighted averages
    const fullOFF = weightedAvg(roster, fullTotalMin, p => p.offRtg);
    const fullDEF = weightedAvg(roster, fullTotalMin, p => p.defRtg);
    const fullTS  = weightedAvg(roster, fullTotalMin, p => p.tsPct);
    const fullTOV = weightedAvg(roster, fullTotalMin, p => p.tovPct);
    const fullORB = weightedAvg(roster, fullTotalMin, p => p.orbPct);

    // Available-roster: redistribute proportionally
    // Each available player's projected minutes = their MPG × (total / available_total)
    // But for the weighted average, the scaling cancels out — we just weight by each
    // available player's original minutes.
    const availOFF = weightedAvg(available, 0, p => p.offRtg);
    const availDEF = weightedAvg(available, 0, p => p.defRtg);
    const availTS  = weightedAvg(available, 0, p => p.tsPct);
    const availTOV = weightedAvg(available, 0, p => p.tovPct);
    const availORB = weightedAvg(available, 0, p => p.orbPct);

    // Compute deltas and apply to team stats
    // Impact-aware dampening — stars with high NET ratings have outsized impact
    // that the weighted-average approach underestimates (spacing, gravity, shot creation).
    // Scale the dampening factor based on the best out player's impact.
    function impactDampen(outList) {
      let best = 0.70;
      for (const p of outList) {
        const net = p.netRtg ?? (p.offRtg != null && p.defRtg != null ? p.offRtg - p.defRtg : 0);
        let d;
        if (p.min >= 28 && net > 5)  d = 1.20;  // star with elite NET
        else if (p.min >= 28)        d = 1.00;  // star, moderate NET
        else if (p.min >= 18)        d = 0.85;  // starter
        else                         d = 0.70;  // bench
        if (d > best) best = d;
      }
      return best;
    }
    const DAMPEN = impactDampen(out);

    const orig = teamStats[teamKey];
    const adj = { ...orig };
    let anyChange = false;

    const outNames = out.map(o => `${o.name} (${o.min.toFixed(0)} min)`).join(", ");

    if (fullOFF != null && availOFF != null) {
      const delta = (availOFF - fullOFF) * DAMPEN;
      adj.OFF = Math.round((orig.OFF + delta) * 100) / 100;
      anyChange = true;
    }
    if (fullDEF != null && availDEF != null) {
      const delta = (availDEF - fullDEF) * DAMPEN;
      adj.DEF = Math.round((orig.DEF + delta) * 100) / 100;
      anyChange = true;
    }
    if (fullTS != null && availTS != null) {
      const delta = (availTS - fullTS) * DAMPEN;
      adj.TS = Math.round((orig.TS + delta) * 10000) / 10000;
    }
    if (fullTOV != null && availTOV != null) {
      const delta = (availTOV - fullTOV) * DAMPEN;
      adj.TO = Math.round((orig.TO + delta) * 10000) / 10000;
    }
    if (fullORB != null && availORB != null) {
      const delta = (availORB - fullORB) * DAMPEN;
      adj.ORR = Math.round((orig.ORR + delta) * 10000) / 10000;
    }

    if (anyChange) {
      adjusted[teamKey] = adj;
      adjustedCount++;

      const offDelta = (adj.OFF - orig.OFF).toFixed(1);
      const defDelta = (adj.DEF - orig.DEF).toFixed(1);
    }
  }


  return adjusted;
}


// ── Diagnostic: build human-readable adjustment notes per game ──────────────
// Returns: { [teamName]: { offDelta, defDelta, outPlayers: [...] } }
// Useful for email display.

export function getAdjustmentNotes(originalStats, adjustedStats) {
  const notes = {};
  for (const [team, orig] of Object.entries(originalStats)) {
    const adj = adjustedStats[team];
    if (!adj || adj === orig) continue;

    const offDelta = adj.OFF - orig.OFF;
    const defDelta = adj.DEF - orig.DEF;

    // Only note if there's a meaningful change (> 0.3 pts)
    if (Math.abs(offDelta) > 0.3 || Math.abs(defDelta) > 0.3) {
      notes[team] = {
        offDelta: Math.round(offDelta * 10) / 10,
        defDelta: Math.round(defDelta * 10) / 10,
        adjOFF: adj.OFF,
        adjDEF: adj.DEF,
        origOFF: orig.OFF,
        origDEF: orig.DEF,
      };
    }
  }
  return notes;
}
