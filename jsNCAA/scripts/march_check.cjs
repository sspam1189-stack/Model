const fs = require('fs');
const h = JSON.parse(fs.readFileSync('data/history.json','utf8'));
const runs = Array.isArray(h.runs) ? h.runs : Object.values(h.runs);

// Daily pick counts for last 3 weeks
console.log('=== DAILY SPREAD PICK COUNTS (last 3 weeks) ===');
for (const run of runs) {
  if (!run.date || parseInt(run.date) < 20260224) continue;
  if (!run.games) continue;
  let sPicks = 0, sWins = 0, sLoss = 0;
  let totalGames = run.games.length;
  for (const g of run.games) {
    if (g.sPick && g.sPick !== 'PASS' && g.sResult) {
      sPicks++;
      if (g.sResult === 'WIN') sWins++;
      else if (g.sResult === 'LOSS') sLoss++;
    }
  }
  const pct = sPicks > 0 ? (sWins/(sWins+sLoss)*100).toFixed(0) : '-';
  console.log(`  ${run.date} | ${sPicks} picks / ${totalGames} games | ${sWins}-${sLoss} (${pct}%)`);
}

// Check games that JUST MISS the filter
console.log('\n=== GAMES JUST MISSING FILTER (pCover 0.60-0.67, sDiff 3-12) ===');
console.log('These would be picked at 0.63 but not at 0.67:\n');

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

let nearMissW = 0, nearMissL = 0;
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

    // Games that pass at 0.63 but fail at 0.67
    if (pCover >= 0.63 && pCover < 0.67 && sDiff >= 3 && sDiff <= 12 && lineOK) {
      const covers = checkCover(g, side);
      if (covers) nearMissW++; else nearMissL++;
    }
  }
}
console.log(`Near-miss (0.63-0.67): ${nearMissW}-${nearMissL} (${(nearMissW/(nearMissW+nearMissL)*100).toFixed(1)}%) n=${nearMissW+nearMissL}`);

// Check what a 0.63 threshold would look like
let w63=0, l63=0, w65=0, l65=0, w67=0, l67=0;
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
    if (sDiff < 3 || sDiff > 12 || !lineOK) continue;

    const covers = checkCover(g, side);
    if (pCover >= 0.63) { if (covers) w63++; else l63++; }
    if (pCover >= 0.65) { if (covers) w65++; else l65++; }
    if (pCover >= 0.67) { if (covers) w67++; else l67++; }
  }
}
console.log('\n=== THRESHOLD COMPARISON (sDiff 3-12, favs cap6) ===');
console.log(`  0.63: ${w63}-${l63} (${(w63/(w63+l63)*100).toFixed(1)}%) n=${w63+l63} | ROI: ${((w63*0.91-l63)/(w63+l63)*100).toFixed(1)}%`);
console.log(`  0.65: ${w65}-${l65} (${(w65/(w65+l65)*100).toFixed(1)}%) n=${w65+l65} | ROI: ${((w65*0.91-l65)/(w65+l65)*100).toFixed(1)}%`);
console.log(`  0.67: ${w67}-${l67} (${(w67/(w67+l67)*100).toFixed(1)}%) n=${w67+l67} | ROI: ${((w67*0.91-l67)/(w67+l67)*100).toFixed(1)}%`);

// March specifically
console.log('\n=== MARCH ONLY (all dates >= 20260301) ===');
let mw63=0,ml63=0,mw65=0,ml65=0,mw67=0,ml67=0;
let marchGames=0, marchWithData=0;
for (const run of runs) {
  if (!run.date || parseInt(run.date) < 20260301) continue;
  if (!run.games) continue;
  for (const g of run.games) {
    marchGames++;
    if (g.homeScore == null || g.pHomeCover == null) continue;
    if (Math.abs(g.line || 0) === 0) continue;
    marchWithData++;
    const sDiff = g.sDiff || 0;
    const pCover = Math.max(g.pHomeCover, g.pAwayCover);
    const side = g.pHomeCover >= g.pAwayCover ? 'home' : 'away';
    const absLine = Math.abs(g.line);
    const isDog = side === 'home' ? g.line > 0 : g.line < 0;
    const lineOK = isDog ? true : absLine <= 6;
    if (sDiff < 3 || sDiff > 12 || !lineOK) continue;

    const covers = checkCover(g, side);
    if (pCover >= 0.63) { if (covers) mw63++; else ml63++; }
    if (pCover >= 0.65) { if (covers) mw65++; else ml65++; }
    if (pCover >= 0.67) { if (covers) mw67++; else ml67++; }
  }
}
console.log(`  March games: ${marchGames} total, ${marchWithData} with data`);
console.log(`  0.63: ${mw63}-${ml63} (${mw63+ml63>0?(mw63/(mw63+ml63)*100).toFixed(1):'N/A'}%) n=${mw63+ml63}`);
console.log(`  0.65: ${mw65}-${ml65} (${mw65+ml65>0?(mw65/(mw65+ml65)*100).toFixed(1):'N/A'}%) n=${mw65+ml65}`);
console.log(`  0.67: ${mw67}-${ml67} (${mw67+ml67>0?(mw67/(mw67+ml67)*100).toFixed(1):'N/A'}%) n=${mw67+ml67}`);

// Picks per day in March
console.log('\n=== PICKS PER DAY AT EACH THRESHOLD (March) ===');
let days63={}, days65={}, days67={};
for (const run of runs) {
  if (!run.date || parseInt(run.date) < 20260301) continue;
  if (!run.games) continue;
  days63[run.date]=0; days65[run.date]=0; days67[run.date]=0;
  for (const g of run.games) {
    if (g.pHomeCover == null) continue;
    if (Math.abs(g.line || 0) === 0) continue;
    const sDiff = g.sDiff || 0;
    const pCover = Math.max(g.pHomeCover, g.pAwayCover);
    const side = g.pHomeCover >= g.pAwayCover ? 'home' : 'away';
    const absLine = Math.abs(g.line);
    const isDog = side === 'home' ? g.line > 0 : g.line < 0;
    const lineOK = isDog ? true : absLine <= 6;
    if (sDiff < 3 || sDiff > 12 || !lineOK) continue;

    if (pCover >= 0.63) days63[run.date]++;
    if (pCover >= 0.65) days65[run.date]++;
    if (pCover >= 0.67) days67[run.date]++;
  }
}
console.log('  Date     | @0.63 | @0.65 | @0.67');
for (const d of Object.keys(days63).sort()) {
  console.log(`  ${d} |   ${String(days63[d]).padEnd(4)} |   ${String(days65[d]).padEnd(4)} |   ${days67[d]}`);
}
const avg63 = Object.values(days63).reduce((a,b)=>a+b,0)/Object.keys(days63).length;
const avg65 = Object.values(days65).reduce((a,b)=>a+b,0)/Object.keys(days65).length;
const avg67 = Object.values(days67).reduce((a,b)=>a+b,0)/Object.keys(days67).length;
console.log(`  Average  |   ${avg63.toFixed(1)} |   ${avg65.toFixed(1)} |   ${avg67.toFixed(1)}`);
