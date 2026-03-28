// scripts/bracket_sim.mjs
// March Madness 2026 bracket simulation using full model stats + Kalman adjustments
import fs from 'fs';

import path from 'path';
import { fileURLToPath } from 'url';
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const statsData = JSON.parse(fs.readFileSync(path.join(__dirname, '..', '..', 'data', 'stats_cache', 'ncaab', '20260317.json'), 'utf8'));
const H = statsData.season;
const ks = JSON.parse(fs.readFileSync('data/kalman_state.json', 'utf8'));

// ── Team name resolution ─────────────────────────────────────────────────────
function normKey(s) {
  return String(s || "").toLowerCase().replace(/[^a-z0-9\s]/g, " ").replace(/\s+/g, " ").trim();
}

function resolveTeam(name, data) {
  if (data[name]) return name;
  const teams = Object.keys(data);
  const wanted = normKey(name);
  for (const k of teams) {
    if (normKey(k) === wanted) return k;
  }
  // Longest prefix match to avoid "Miami" matching wrong team
  let bestPrefix = null, bestLen = 0;
  for (const k of teams) {
    const nk = normKey(k);
    if (nk === wanted || wanted === nk) return k;
    if ((wanted.startsWith(nk + " ") || nk.startsWith(wanted + " ")) && nk.length >= 3) {
      const len = Math.min(nk.length, wanted.length);
      if (len > bestLen) { bestLen = len; bestPrefix = k; }
    }
  }
  if (bestPrefix) return bestPrefix;
  // Fuzzy
  for (const k of teams) {
    const nk = normKey(k);
    if (nk.includes(wanted) || wanted.includes(nk)) return k;
  }
  return null;
}

// ── Normal CDF ───────────────────────────────────────────────────────────────
function normalCDF(x) {
  const a1 = 0.254829592, a2 = -0.284496736, a3 = 1.421413741;
  const a4 = -1.453152027, a5 = 1.061405429, p = 0.3275911;
  const sign = x < 0 ? -1 : 1;
  const z = Math.abs(x) / Math.SQRT2;
  const t = 1.0 / (1.0 + p * z);
  const y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * Math.exp(-z * z);
  return 0.5 * (1.0 + sign * y);
}

// ── Game simulation using efficiency stats + Kalman adjustment ───────────────
// Uses OFF/DEF efficiency ratings (points per 100 possessions) as the primary
// team strength measure, with Kalman adj_mean as a form adjustment.
// Tournament games are on neutral court (no HCA).

function simGame(teamA, teamB) {
  const aKey = resolveTeam(teamA, H);
  const bKey = resolveTeam(teamB, H);

  if (!aKey || !bKey) {
    if (!aKey && !bKey) return { winner: teamA, loser: teamB, prob: 0.5, margin: 0 };
    if (!aKey) return { winner: teamB, loser: teamA, prob: 0.75, margin: 5 };
    return { winner: teamA, loser: teamB, prob: 0.75, margin: 5 };
  }

  const a = H[aKey];
  const b = H[bKey];

  // Net efficiency: OFF - DEF (higher = better)
  // Projected margin = (A_OFF - B_DEF + B_OFF - A_DEF) / 2... simplified:
  // Margin ~ (A_NET - B_NET) / 2 adjusted for pace
  const aNet = a.OFF - a.DEF;
  const bNet = b.OFF - b.DEF;

  // Matchup-based margin: (A scores vs B defense) - (B scores vs A defense)
  const aExpected = (a.OFF + b.DEF) / 2;  // A's expected scoring rate
  const bExpected = (b.OFF + a.DEF) / 2;  // B's expected scoring rate
  const avgPace = (a.PACE + b.PACE) / 2 / 100;  // possessions factor

  let margin = (aExpected - bExpected) * avgPace;

  // Add Kalman adjustment (recent form drift)
  const aKalman = resolveTeam(teamA, ks.teams);
  const bKalman = resolveTeam(teamB, ks.teams);
  if (aKalman && bKalman) {
    margin += (ks.teams[aKalman].adj_mean - ks.teams[bKalman].adj_mean);
  }

  // Game-to-game standard deviation in college basketball is ~11 points
  const gameSD = 11;

  const pAWins = normalCDF(margin / gameSD);

  if (pAWins >= 0.5) {
    return { winner: teamA, loser: teamB, prob: pAWins, margin: margin };
  } else {
    return { winner: teamB, loser: teamA, prob: 1 - pAWins, margin: -margin };
  }
}

// ── Bracket definition ───────────────────────────────────────────────────────
console.log('╔══════════════════════════════════════════════════════════════════╗');
console.log('║         2026 NCAA MARCH MADNESS BRACKET PREDICTIONS            ║');
console.log('║         Bayesian Model + Kalman Filter Adjustments             ║');
console.log('╚══════════════════════════════════════════════════════════════════╝\n');

console.log('━━━━━━━━━━━━━━━━━━ FIRST FOUR ━━━━━━━━━━━━━━━━━━');
const ff1 = simGame('Texas', 'N.C. State');        // 11 seed West
const ff2 = simGame('UMBC', 'Howard');              // 16 seed Midwest
const ff3 = simGame('Miami OH', 'SMU');             // 11 seed Midwest
const ff4 = simGame('Prairie View A&M', 'Lehigh');  // 16 seed South

for (const g of [ff1, ff2, ff3, ff4]) {
  console.log(`  ${g.winner.padEnd(22)} def. ${g.loser.padEnd(22)} (${(g.prob*100).toFixed(0)}% | proj margin: ${Math.abs(g.margin).toFixed(1)})`);
}

// ── Region definitions ───────────────────────────────────────────────────────
const regions = {
  'EAST': [
    ['Duke', 'Siena'],                             // 1 vs 16
    ['Ohio St.', 'TCU'],                            // 8 vs 9
    ["St. John's", 'Northern Iowa'],                // 5 vs 12
    ['Kansas', 'Cal Baptist'],                      // 4 vs 13
    ['Louisville', 'South Florida'],                // 6 vs 11
    ['Michigan St.', 'North Dakota St.'],           // 3 vs 14
    ['UCLA', 'UCF'],                                // 7 vs 10
    ['Connecticut', 'Furman'],                      // 2 vs 15
  ],
  'WEST': [
    ['Arizona', 'LIU'],                             // 1 vs 16
    ['Villanova', 'Utah St.'],                      // 8 vs 9
    ['Wisconsin', 'High Point'],                    // 5 vs 12
    ['Arkansas', 'Hawaii'],                         // 4 vs 13
    ['BYU', ff1.winner],                            // 6 vs 11
    ['Gonzaga', 'Kennesaw St.'],                    // 3 vs 14
    ['Miami FL', 'Missouri'],                       // 7 vs 10
    ['Purdue', 'Queens'],                           // 2 vs 15
  ],
  'MIDWEST': [
    ['Michigan', ff2.winner],                       // 1 vs 16
    ['Georgia', 'Saint Louis'],                     // 8 vs 9
    ['Texas Tech', 'Akron'],                        // 5 vs 12
    ['Alabama', 'Hofstra'],                         // 4 vs 13
    ['Tennessee', ff3.winner],                      // 6 vs 11
    ['Virginia', 'Wright St.'],                     // 3 vs 14
    ['Kentucky', 'Santa Clara'],                    // 7 vs 10
    ['Iowa St.', 'Tennessee St.'],                  // 2 vs 15
  ],
  'SOUTH': [
    ['Florida', ff4.winner],                        // 1 vs 16
    ['Clemson', 'Iowa'],                            // 8 vs 9
    ['Vanderbilt', 'McNeese St.'],                  // 5 vs 12
    ['Nebraska', 'Troy'],                           // 4 vs 13
    ['North Carolina', 'VCU'],                      // 6 vs 11
    ['Illinois', 'Penn'],                           // 3 vs 14
    ["Saint Mary's", 'Texas A&M'],                  // 7 vs 10
    ['Houston', 'Idaho'],                           // 2 vs 15
  ],
};

const seeds = {
  'EAST': { 'Duke': 1, 'Siena': 16, 'Ohio St.': 8, 'TCU': 9, "St. John's": 5, 'Northern Iowa': 12, 'Kansas': 4, 'Cal Baptist': 13, 'Louisville': 6, 'South Florida': 11, 'Michigan St.': 3, 'North Dakota St.': 14, 'UCLA': 7, 'UCF': 10, 'Connecticut': 2, 'Furman': 15 },
  'WEST': { 'Arizona': 1, 'LIU': 16, 'Villanova': 8, 'Utah St.': 9, 'Wisconsin': 5, 'High Point': 12, 'Arkansas': 4, 'Hawaii': 13, 'BYU': 6, [ff1.winner]: 11, 'Gonzaga': 3, 'Kennesaw St.': 14, 'Miami FL': 7, 'Missouri': 10, 'Purdue': 2, 'Queens': 15 },
  'MIDWEST': { 'Michigan': 1, [ff2.winner]: 16, 'Georgia': 8, 'Saint Louis': 9, 'Texas Tech': 5, 'Akron': 12, 'Alabama': 4, 'Hofstra': 13, 'Tennessee': 6, [ff3.winner]: 11, 'Virginia': 3, 'Wright St.': 14, 'Kentucky': 7, 'Santa Clara': 10, 'Iowa St.': 2, 'Tennessee St.': 15 },
  'SOUTH': { 'Florida': 1, [ff4.winner]: 16, 'Clemson': 8, 'Iowa': 9, 'Vanderbilt': 5, 'McNeese St.': 12, 'Nebraska': 4, 'Troy': 13, 'North Carolina': 6, 'VCU': 11, 'Illinois': 3, 'Penn': 14, "Saint Mary's": 7, 'Texas A&M': 10, 'Houston': 2, 'Idaho': 15 },
};

function getSeed(region, team) {
  // Check all regions if not found in specified one (for Final Four)
  if (seeds[region]?.[team] != null) return seeds[region][team];
  for (const r of Object.values(seeds)) {
    if (r[team] != null) return r[team];
  }
  return '?';
}

function simRound(matchups, region, roundName) {
  const results = [];
  console.log(`\n  ${roundName}:`);
  for (const [a, b] of matchups) {
    const g = simGame(a, b);
    const sW = getSeed(region, g.winner);
    const sL = getSeed(region, g.loser);
    console.log(`    (${String(sW).padStart(2)}) ${g.winner.padEnd(20)} def. (${String(sL).padStart(2)}) ${g.loser.padEnd(20)} ${(g.prob*100).toFixed(0)}%  margin: ${Math.abs(g.margin).toFixed(1)}`);
    results.push(g.winner);
  }
  return results;
}

const finalFourTeams = [];

for (const [regionName, matchups] of Object.entries(regions)) {
  console.log(`\n${'━'.repeat(22)} ${regionName} REGION ${'━'.repeat(22)}`);

  const r64 = simRound(matchups, regionName, 'Round of 64');

  const r32matchups = [];
  for (let i = 0; i < r64.length; i += 2) r32matchups.push([r64[i], r64[i + 1]]);
  const r32 = simRound(r32matchups, regionName, 'Round of 32');

  const s16matchups = [];
  for (let i = 0; i < r32.length; i += 2) s16matchups.push([r32[i], r32[i + 1]]);
  const s16 = simRound(s16matchups, regionName, 'Sweet 16');

  const e8 = simRound([[s16[0], s16[1]]], regionName, 'Elite 8');

  console.log(`\n  >>> ${regionName} CHAMPION: (${getSeed(regionName, e8[0])}) ${e8[0]}`);
  finalFourTeams.push(e8[0]);
}

// ── Final Four ───────────────────────────────────────────────────────────────
console.log(`\n${'━'.repeat(22)} FINAL FOUR ${'━'.repeat(22)}`);
console.log(`  Lucas Oil Stadium, Indianapolis — April 4, 2026\n`);

// NCAA Final Four pairings: East vs South, Midwest vs West
const semi1 = simGame(finalFourTeams[0], finalFourTeams[3]); // East vs South
const semi2 = simGame(finalFourTeams[2], finalFourTeams[1]); // Midwest vs West

console.log(`  Semifinal 1 (East vs South):`);
console.log(`    ${semi1.winner.padEnd(20)} def. ${semi1.loser.padEnd(20)} (${(semi1.prob*100).toFixed(0)}% | margin: ${Math.abs(semi1.margin).toFixed(1)})`);
console.log(`  Semifinal 2 (Midwest vs West):`);
console.log(`    ${semi2.winner.padEnd(20)} def. ${semi2.loser.padEnd(20)} (${(semi2.prob*100).toFixed(0)}% | margin: ${Math.abs(semi2.margin).toFixed(1)})`);

// ── Championship ─────────────────────────────────────────────────────────────
console.log(`\n${'━'.repeat(22)} NATIONAL CHAMPIONSHIP ${'━'.repeat(22)}`);
console.log(`  April 6, 2026 — Lucas Oil Stadium, Indianapolis\n`);

const champ = simGame(semi1.winner, semi2.winner);
console.log(`    ${champ.winner.padEnd(20)} def. ${champ.loser.padEnd(20)} (${(champ.prob*100).toFixed(0)}% | margin: ${Math.abs(champ.margin).toFixed(1)})`);

console.log(`\n${'═'.repeat(60)}`);
console.log(`  NATIONAL CHAMPION: ${champ.winner}`);
console.log(`${'═'.repeat(60)}`);

// ── Summary ──────────────────────────────────────────────────────────────────
console.log('\n BRACKET SUMMARY');
console.log('─'.repeat(40));
const regionNames = Object.keys(regions);
console.log(`  Final Four:`);
for (let i = 0; i < finalFourTeams.length; i++) {
  console.log(`    ${regionNames[i].padEnd(8)}: (${getSeed(regionNames[i], finalFourTeams[i])}) ${finalFourTeams[i]}`);
}
console.log(`  Champion:  ${champ.winner}`);
console.log(`  Runner-up: ${champ.loser}\n`);

// ── Upset alerts ─────────────────────────────────────────────────────────────
console.log('  NOTABLE UPSETS TO WATCH:');
const allGames = [];
function trackGame(g, round, seedW, seedL) {
  if (seedW > seedL && seedW - seedL >= 3) {
    allGames.push({ ...g, round, seedW, seedL });
  }
}
// Re-sim to collect upsets (we already printed results above)
// This is just for the summary
console.log('  (Teams where a lower seed wins by 3+ seed lines)');
console.log('─'.repeat(40));
