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
  // Always use Regular Season for player advanced stats — playoff sample too small
  // for reliable per-player on-court impact ratings.
  const effectiveType = "Regular Season";
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
    SeasonSegment: "", SeasonType: effectiveType,
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
  const iUSG    = idx("USG_PCT");       // usage rate — % of team possessions used by player

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
      usgPct:  iUSG !== -1 ? Number(row[iUSG]) : null,
    };
  }

  const count = Object.keys(players).length;

  return players;
}


// ── Name helpers ───────────────────────────────────────────────────────────

const NAME_SUFFIXES = new Set(["jr.", "jr", "sr.", "sr", "ii", "iii", "iv", "v"]);
function realLastName(fullName) {
  const parts = String(fullName || "").split(" ");
  for (let i = parts.length - 1; i >= 1; i--) {
    if (!NAME_SUFFIXES.has(parts[i].toLowerCase())) return parts[i].toLowerCase();
  }
  return parts[parts.length - 1].toLowerCase();
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

export function adjustTeamStats(teamStats, injuryReport, playerMPG, playerAdv, todaysGames, { recentInjuryDates = null, ofsPlayers = null } = {}) {
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

    const roster = playersByTeam[teamKey];
    if (!roster || roster.length < 5) continue;

    // Get injury report for this team
    const injKey = resolveTeamName(teamKey, Object.keys(injuryReport || {}));
    const injuries = injKey ? (injuryReport[injKey] || []) : [];

    const outPlayers = new Set(
      injuries
        .filter(i => i.status === "out" || i.status === "doubtful")
        .map(i => i.player)
    );

    // ── Injury-out adjustment ───────────────────────────────────────────
    // Only deduct for NEWLY out players. If a player has been out for many
    // recent games, the team's blended stats already reflect their absence.
    if (!outPlayers.size) continue;

    // Build set of players who were already out in recent caches (long-term)
    const longTermOut = new Set();
    if (recentInjuryDates && Object.keys(recentInjuryDates).length >= 5) {
      for (const outName of outPlayers) {
        const lastName = realLastName(outName);
        let datesOut = 0;
        for (const report of Object.values(recentInjuryDates)) {
          const teamInj = report[teamKey] ||
            report[Object.keys(report).find(k => resolveTeamName(k, [teamKey]))] || [];
          const wasOut = teamInj.some(inj =>
            (inj.status === "out" || inj.status === "doubtful") &&
            (inj.player === outName || realLastName(inj.player) === lastName)
          );
          if (wasOut) datesOut++;
        }
        if (datesOut >= 5) longTermOut.add(outName);
      }
      if (longTermOut.size) {
        console.log(`  [lineup] Skipping deduction for ${longTermOut.size} long-term out players on ${teamKey} (already in team stats)`);
      }
    }

    // Match out players to roster using exact + fuzzy name matching
    const rosterOut = new Set();
    for (const outName of outPlayers) {
      if (longTermOut.has(outName)) continue; // skip long-term absences
      // Exact match
      let found = roster.find(r => r.name === outName);
      // Last-name fallback (same as injuries.mjs)
      if (!found) {
        const lastName = realLastName(outName);
        found = roster.find(r =>
          realLastName(r.name) === lastName
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

    // Compute full-roster weighted averages
    // Offensive stats (OFF, TS%, TOV%) weighted by usage — captures shot creation impact
    // Defensive stats (DEF) and rebounding (ORB%) weighted by minutes — defense is about court time
    const fullTotalMin = roster.reduce((s, r) => s + r.min, 0);
    if (fullTotalMin <= 0) continue;

    const weightedAvg = (players, getter, weightFn) => {
      let sum = 0, totalW = 0;
      for (const p of players) {
        const val = getter(p);
        const w = weightFn(p);
        if (Number.isFinite(val) && Number.isFinite(w) && w > 0) {
          sum += w * val;
          totalW += w;
        }
      }
      return totalW > 0 ? sum / totalW : null;
    };

    const byUsage = (p) => (p.usgPct ?? 0) * p.min;  // usage × minutes = total possessions used
    const byMin   = (p) => p.min;

    // Only adjust OFF and DEF — these are the only stats the model uses in projScore.
    // OFF weighted by usage (captures shot creation impact), DEF weighted by minutes.
    const fullOFF  = weightedAvg(roster, p => p.offRtg, byUsage);
    const fullDEF  = weightedAvg(roster, p => p.defRtg, byMin);
    const availOFF = weightedAvg(available, p => p.offRtg, byUsage);
    const availDEF = weightedAvg(available, p => p.defRtg, byMin);

    const orig = adjusted[teamKey] || teamStats[teamKey];
    const adj = { ...orig };
    let anyChange = false;

    const outNames = out.map(o => {
      const usg = o.usgPct != null ? (o.usgPct * 100).toFixed(1) : "?";
      return `${o.name} (${o.min.toFixed(0)} mpg, USG ${usg}%)`;
    }).join(", ");
    const totalMPG = out.reduce((s, o) => s + o.min, 0);
    console.log(`  [lineup] ${teamKey} missing: ${outNames} (${totalMPG.toFixed(0)} total mpg)`);

    if (fullOFF != null && availOFF != null) {
      adj.OFF = Math.round((orig.OFF + (availOFF - fullOFF)) * 100) / 100;
      anyChange = true;
    }
    if (fullDEF != null && availDEF != null) {
      adj.DEF = Math.round((orig.DEF + (availDEF - fullDEF)) * 100) / 100;
      anyChange = true;
    }

    if (anyChange) {
      adjusted[teamKey] = adj;
      adjustedCount++;

      const offDelta = (adj.OFF - orig.OFF).toFixed(1);
      const defDelta = (adj.DEF - orig.DEF).toFixed(1);
    }
  }


  // --- Return boost: boost teams getting star/starter back from 5+ day absence ---
  if (recentInjuryDates && Object.keys(recentInjuryDates).length >= 5) {
    for (const teamKey of teamKeys) {
      const isTonight = teamsTonight.has(teamKey) ||
        [...teamsTonight].some(t => resolveTeamName(t, [teamKey]));
      if (!isTonight) continue;

      const roster = playersByTeam[teamKey];
      if (!roster || roster.length < 5) continue;

      // Today's out/doubtful players
      const injKey = resolveTeamName(teamKey, Object.keys(injuryReport || {}));
      const injuries = injKey ? (injuryReport[injKey] || []) : [];
      const todaysOut = new Set(
        injuries
          .filter(i => i.status === "out" || i.status === "doubtful")
          .map(i => i.player)
      );

      // Find players who were out 5+ of last 10 days but are NOT out today
      const returnees = [];
      for (const p of roster) {
        if (todaysOut.has(p.name)) continue; // still out
        if (p.min < 22) continue; // only star (>=32) and starter (>=22)
        const lastName = realLastName(p.name);
        let datesOut = 0;
        for (const report of Object.values(recentInjuryDates)) {
          const teamInj = report[teamKey] ||
            report[Object.keys(report).find(k => resolveTeamName(k, [teamKey]))] || [];
          const wasOut = teamInj.some(inj =>
            (inj.status === "out" || inj.status === "doubtful") &&
            (inj.player === p.name || realLastName(inj.player) === lastName)
          );
          if (wasOut) datesOut++;
        }
        if (datesOut >= 5) {
          const tier = p.min >= 32 ? "star" : "starter";
          returnees.push({ player: p, tier, daysOut: datesOut });
        }
      }

      if (!returnees.length) continue;

      const fullTotalMin = roster.reduce((s, r) => s + r.min, 0);
      if (fullTotalMin <= 0) continue;

      const returneeNames = new Set(returnees.map(r => r.player.name));
      const withoutReturnees = roster.filter(r => !returneeNames.has(r.name));
      if (withoutReturnees.length < 5) continue;

      const wAvg = (players, getter) => {
        let sum = 0, validMin = 0;
        for (const p of players) {
          const val = getter(p);
          if (Number.isFinite(val)) { sum += p.min * val; validMin += p.min; }
        }
        return validMin > 0 ? sum / validMin : null;
      };

      const fOFF = wAvg(roster, p => p.offRtg);
      const fDEF = wAvg(roster, p => p.defRtg);
      const fTS  = wAvg(roster, p => p.tsPct);
      const fTOV = wAvg(roster, p => p.tovPct);
      const fORB = wAvg(roster, p => p.orbPct);

      const wOFF = wAvg(withoutReturnees, p => p.offRtg);
      const wDEF = wAvg(withoutReturnees, p => p.defRtg);
      const wTS  = wAvg(withoutReturnees, p => p.tsPct);
      const wTOV = wAvg(withoutReturnees, p => p.tovPct);
      const wORB = wAvg(withoutReturnees, p => p.orbPct);

      // Dampening: 0.8x of stacked MPG+NET impact (ramp-up / minutes restriction)
      const retDampens = returnees.map(ret => {
        const p = ret.player;
        const net = p.netRtg ?? (p.offRtg != null && p.defRtg != null ? p.offRtg - p.defRtg : 0);
        return Math.min(1.40, Math.max(0.50, 0.50 + p.min * 0.008 + net * 0.03));
      }).sort((a, b) => b - a);
      let stackedDampen = retDampens[0] || 0.50;
      for (let i = 1; i < retDampens.length; i++) {
        if (retDampens[i] > 0.75) stackedDampen += retDampens[i] * 0.15;
      }
      const boostDampen = Math.min(1.60, stackedDampen) * 0.8;

      const orig = adjusted[teamKey] || teamStats[teamKey];
      const adj = { ...orig };
      let anyChange = false;

      if (fOFF != null && wOFF != null) {
        adj.OFF = Math.round((orig.OFF + (fOFF - wOFF) * boostDampen) * 100) / 100;
        anyChange = true;
      }
      if (fDEF != null && wDEF != null) {
        adj.DEF = Math.round((orig.DEF + (fDEF - wDEF) * boostDampen) * 100) / 100;
        anyChange = true;
      }
      if (fTS != null && wTS != null) {
        adj.TS = Math.round((orig.TS + (fTS - wTS) * boostDampen) * 10000) / 10000;
      }
      if (fTOV != null && wTOV != null) {
        adj.TO = Math.round((orig.TO + (fTOV - wTOV) * boostDampen) * 10000) / 10000;
      }
      if (fORB != null && wORB != null) {
        adj.ORR = Math.round((orig.ORR + (fORB - wORB) * boostDampen) * 10000) / 10000;
      }

      if (anyChange) {
        adjusted[teamKey] = adj;
        for (const ret of returnees) {
          const p = ret.player;
          const net = p.netRtg ?? (p.offRtg != null && p.defRtg != null ? p.offRtg - p.defRtg : 0);
          console.log(`  [lineup] Boosting ${teamKey} for returning ${ret.tier}: ${p.name} (${p.min.toFixed(0)} mpg, NET ${net >= 0 ? "+" : ""}${net.toFixed(1)}, dampen=${boostDampen.toFixed(2)}, was out ${ret.daysOut}/${Object.keys(recentInjuryDates).length} days)`);
        }
      }
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
