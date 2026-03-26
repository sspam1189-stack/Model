const fs = require('fs');
const h = JSON.parse(fs.readFileSync('data/history.json','utf8'));
const runs = Array.isArray(h.runs) ? h.runs : Object.values(h.runs);

// Power conferences (teams that make March Madness regularly)
const POWER = new Set([
  // SEC
  'Alabama','Arkansas','Auburn','Florida','Georgia','Kentucky','LSU','Mississippi St.',
  'Missouri','Oklahoma','South Carolina','Tennessee','Texas','Texas A&M','Vanderbilt','Ole Miss',
  // Big 12
  'Arizona','Arizona St.','BYU','Baylor','Cincinnati','Colorado','Houston','Iowa St.',
  'Kansas','Kansas St.','Oklahoma St.','Oregon St.','TCU','Texas Tech','UCF','Utah','West Virginia',
  // Big Ten
  'Illinois','Indiana','Iowa','Maryland','Michigan','Michigan St.','Minnesota','Nebraska',
  'Northwestern','Ohio St.','Oregon','Penn St.','Purdue','Rutgers','UCLA','USC','Washington','Wisconsin',
  // ACC
  'Boston College','Clemson','Duke','Florida St.','Georgia Tech','Louisville','Miami FL',
  'NC State','North Carolina','Notre Dame','Pittsburgh','SMU','Stanford','Syracuse','Virginia',
  'Virginia Tech','Wake Forest','California',
  // Big East
  'Butler','UConn','Creighton','DePaul','Georgetown','Marquette','Providence',
  'Seton Hall','St. John\'s','Villanova','Xavier',
]);

function isPower(team) {
  return POWER.has(team);
}

function checkCover(g, side) {
  const absLine = Math.abs(g.line || 0);
  const homeFav = g.line < 0;
  if (side === 'home') {
    if (homeFav) return (g.homeScore - g.awayScore) - absLine > 0;
    else return (g.homeScore - g.awayScore) + absLine > 0;
  } else {
    if (homeFav) return (g.awayScore - g.homeScore) + absLine > 0;
    else return (g.awayScore - g.homeScore) - absLine > 0;
  }
}

// Categorize every qualifying spread pick
let powerW=0, powerL=0, midW=0, midL=0, lowW=0, lowL=0;
let powerPicks=[], midPicks=[], lowPicks=[];

for (const run of runs) {
  if (!run.games) continue;
  for (const g of run.games) {
    if (g.homeScore == null || g.pHomeCover == null) continue;
    if (Math.abs(g.line || 0) === 0) continue;
    const sDiff = g.sDiff || 0;
    const pCover = Math.max(g.pHomeCover, g.pAwayCover);
    const side = g.pHomeCover >= g.pAwayCover ? 'home' : 'away';
    const absLine = Math.abs(g.line);
    const isDog = side === 'home' ? g.line > 0 : g.line < 0;
    const lineOK = isDog ? true : absLine <= 6;
    if (pCover < 0.67 || sDiff < 3 || sDiff > 12 || !lineOK) continue;

    const covers = checkCover(g, side);
    const homePower = isPower(g.home);
    const awayPower = isPower(g.away);

    if (homePower && awayPower) {
      // Both power = high profile
      if (covers) powerW++; else powerL++;
      powerPicks.push({...g, covers, date: run.date});
    } else if (homePower || awayPower) {
      // One power = mid
      if (covers) midW++; else midL++;
      midPicks.push({...g, covers, date: run.date});
    } else {
      // Neither = low/small school
      if (covers) lowW++; else lowL++;
      lowPicks.push({...g, covers, date: run.date});
    }
  }
}

console.log('=== SPREAD PERFORMANCE BY GAME PROFILE ===');
console.log(`Power vs Power: ${powerW}-${powerL} (${powerW+powerL>0?(powerW/(powerW+powerL)*100).toFixed(1):'N/A'}%) n=${powerW+powerL}`);
console.log(`Power vs Mid:   ${midW}-${midL} (${midW+midL>0?(midW/(midW+midL)*100).toFixed(1):'N/A'}%) n=${midW+midL}`);
console.log(`Small vs Small: ${lowW}-${lowL} (${lowW+lowL>0?(lowW/(lowW+lowL)*100).toFixed(1):'N/A'}%) n=${lowW+lowL}`);

// By line sharpness (small lines = sharper market)
console.log('\n=== BY LINE SIZE (proxy for market sharpness) ===');
for (const [lo, hi, label] of [[0,3,'0-3 (sharp)'], [3,6,'3-6'], [6,10,'6-10'], [10,20,'10-20 (soft)'], [20,99,'20+ (very soft)']]) {
  let w=0, l=0;
  for (const run of runs) {
    if (!run.games) continue;
    for (const g of run.games) {
      if (g.homeScore == null || g.pHomeCover == null) continue;
      const absLine = Math.abs(g.line || 0);
      if (absLine === 0 || absLine < lo || absLine >= hi) continue;
      const sDiff = g.sDiff || 0;
      const pCover = Math.max(g.pHomeCover, g.pAwayCover);
      const side = g.pHomeCover >= g.pAwayCover ? 'home' : 'away';
      const isDog = side === 'home' ? g.line > 0 : g.line < 0;
      const lineOK = isDog ? true : absLine <= 6;
      if (pCover < 0.67 || sDiff < 3 || sDiff > 12 || !lineOK) continue;
      const covers = checkCover(g, side);
      if (covers) w++; else l++;
    }
  }
  if (w+l === 0) continue;
  console.log(`  Line ${label}: ${w}-${l} (${(w/(w+l)*100).toFixed(1)}%) n=${w+l}`);
}

// What would the model pick on ONLY power conference games?
console.log('\n=== IF MARCH MADNESS = ONLY POWER GAMES ===');
console.log(`Model would get: ${powerW+midW}-${powerL+midL} (${((powerW+midW)/(powerW+midW+powerL+midL)*100).toFixed(1)}%) n=${powerW+midW+powerL+midL}`);
console.log(`(Losing the small-school edge of ${lowW}-${lowL})`);

// At a lower threshold for power games?
console.log('\n=== POWER GAMES AT LOWER THRESHOLD (0.63) ===');
let pw63=0, pl63=0;
for (const run of runs) {
  if (!run.games) continue;
  for (const g of run.games) {
    if (g.homeScore == null || g.pHomeCover == null) continue;
    if (Math.abs(g.line || 0) === 0) continue;
    if (!isPower(g.home) && !isPower(g.away)) continue;
    const sDiff = g.sDiff || 0;
    const pCover = Math.max(g.pHomeCover, g.pAwayCover);
    const side = g.pHomeCover >= g.pAwayCover ? 'home' : 'away';
    const absLine = Math.abs(g.line);
    const isDog = side === 'home' ? g.line > 0 : g.line < 0;
    const lineOK = isDog ? true : absLine <= 6;
    if (pCover < 0.63 || sDiff < 3 || sDiff > 12 || !lineOK) continue;
    const covers = checkCover(g, side);
    if (covers) pw63++; else pl63++;
  }
}
console.log(`  Power games @0.63: ${pw63}-${pl63} (${(pw63/(pw63+pl63)*100).toFixed(1)}%) n=${pw63+pl63}`);

let pw65=0, pl65=0;
for (const run of runs) {
  if (!run.games) continue;
  for (const g of run.games) {
    if (g.homeScore == null || g.pHomeCover == null) continue;
    if (Math.abs(g.line || 0) === 0) continue;
    if (!isPower(g.home) && !isPower(g.away)) continue;
    const sDiff = g.sDiff || 0;
    const pCover = Math.max(g.pHomeCover, g.pAwayCover);
    const side = g.pHomeCover >= g.pAwayCover ? 'home' : 'away';
    const absLine = Math.abs(g.line);
    const isDog = side === 'home' ? g.line > 0 : g.line < 0;
    const lineOK = isDog ? true : absLine <= 6;
    if (pCover < 0.65 || sDiff < 3 || sDiff > 12 || !lineOK) continue;
    const covers = checkCover(g, side);
    if (covers) pw65++; else pl65++;
  }
}
console.log(`  Power games @0.65: ${pw65}-${pl65} (${(pw65/(pw65+pl65)*100).toFixed(1)}%) n=${pw65+pl65}`);

// Sample some power vs power picks
console.log('\n=== SAMPLE POWER VS POWER PICKS ===');
for (const p of powerPicks.slice(-10)) {
  const side = p.pHomeCover >= p.pAwayCover ? 'home' : 'away';
  const pCover = Math.max(p.pHomeCover, p.pAwayCover);
  console.log(`  ${p.date} ${p.away} @ ${p.home} | pCover:${pCover.toFixed(2)} | sDiff:${p.sDiff} | ${p.covers?'WIN':'LOSS'}`);
}
