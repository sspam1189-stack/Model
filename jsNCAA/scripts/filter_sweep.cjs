const fs = require('fs');
const h = JSON.parse(fs.readFileSync('data/history.json','utf8'));
const runs = h.runs || {};

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

// Collect all qualifying games
const games = [];
for (const [key, run] of Object.entries(runs)) {
  if (!run.games) continue;
  for (const g of run.games) {
    if (g.homeScore == null || g.pHomeCover == null) continue;
    if (Math.abs(g.line || 0) === 0) continue; // skip zero-line
    const sDiff = g.sDiff || 0;
    const pCover = Math.max(g.pHomeCover, g.pAwayCover);
    const side = g.pHomeCover >= g.pAwayCover ? 'home' : 'away';
    const absLine = Math.abs(g.line || 0);
    const isDog = side === 'home' ? g.line > 0 : g.line < 0;
    const covers = checkCover(g, side);
    games.push({ sDiff, pCover, absLine, isDog, covers, side });
  }
}

console.log('Total games with data:', games.length);
console.log('');

// === SPREAD FILTER SWEEP ===
console.log('=== SPREAD FILTER COMBINATIONS ===');
console.log('pCover | sDiff    | dogRule      | favRule      | W-L (pct)     | ROI');
console.log('-'.repeat(85));

for (const pThresh of [0.58, 0.60, 0.63, 0.65, 0.67]) {
  for (const [sMin, sMax] of [[3,9], [3,12], [4,9], [3,7]]) {
    for (const favMode of ['all', 'cap6', 'cap4', 'none']) {
      let w = 0, l = 0;
      for (const g of games) {
        if (g.pCover < pThresh) continue;
        if (g.sDiff < sMin || g.sDiff > sMax) continue;

        if (g.isDog) {
          // dogs always in
        } else {
          if (favMode === 'none') continue;
          if (favMode === 'cap6' && g.absLine > 6) continue;
          if (favMode === 'cap4' && g.absLine > 4) continue;
        }

        if (g.covers) w++; else l++;
      }
      if (w + l < 20) continue;
      const n = w + l;
      const pct = (w / n * 100).toFixed(1);
      const roi = ((w * 0.91 - l) / n * 100).toFixed(1); // -110 juice
      const label = `>=${pThresh.toFixed(2)} | ${sMin}-${sMax}`.padEnd(20) +
        ` | dogs:all`.padEnd(14) +
        ` | favs:${favMode}`.padEnd(14) +
        ` | ${w}-${l} (${pct}%)`.padEnd(18) +
        ` | ${roi}%`;
      console.log(label);
    }
  }
}

// === DOGS ONLY DEEP DIVE ===
console.log('\n=== DOGS ONLY — by pCover threshold ===');
for (const pThresh of [0.55, 0.58, 0.60, 0.63, 0.65, 0.67, 0.70]) {
  for (const [sMin, sMax] of [[3,9], [3,12], [2,9]]) {
    let w = 0, l = 0;
    for (const g of games) {
      if (!g.isDog) continue;
      if (g.pCover < pThresh) continue;
      if (g.sDiff < sMin || g.sDiff > sMax) continue;
      if (g.covers) w++; else l++;
    }
    if (w + l < 10) continue;
    const n = w + l;
    const pct = (w / n * 100).toFixed(1);
    const roi = ((w * 0.91 - l) / n * 100).toFixed(1);
    console.log(`  p>=${pThresh.toFixed(2)} sDiff ${sMin}-${sMax}: ${w}-${l} (${pct}%) n=${n} ROI:${roi}%`);
  }
}

// === FAVS DEEP DIVE ===
console.log('\n=== FAVS ONLY — by pCover and line cap ===');
for (const pThresh of [0.60, 0.63, 0.65, 0.67, 0.70]) {
  for (const lineCap of [4, 6, 8, 99]) {
    for (const [sMin, sMax] of [[3,9]]) {
      let w = 0, l = 0;
      for (const g of games) {
        if (g.isDog) continue;
        if (g.pCover < pThresh) continue;
        if (g.sDiff < sMin || g.sDiff > sMax) continue;
        if (g.absLine > lineCap) continue;
        if (g.covers) w++; else l++;
      }
      if (w + l < 10) continue;
      const n = w + l;
      const pct = (w / n * 100).toFixed(1);
      const roi = ((w * 0.91 - l) / n * 100).toFixed(1);
      console.log(`  p>=${pThresh.toFixed(2)} line<=${lineCap} sDiff ${sMin}-${sMax}: ${w}-${l} (${pct}%) n=${n} ROI:${roi}%`);
    }
  }
}

// === BEST COMBOS RANKED BY ROI (min 50 picks) ===
console.log('\n=== TOP 15 COMBOS BY ROI (min 50 picks) ===');
const combos = [];
for (const pThresh of [0.58, 0.60, 0.63, 0.65, 0.67, 0.70]) {
  for (const [sMin, sMax] of [[3,9], [3,12], [4,9], [3,7], [2,9]]) {
    for (const favMode of ['all', 'cap6', 'cap4', 'none']) {
      let w = 0, l = 0;
      for (const g of games) {
        if (g.pCover < pThresh) continue;
        if (g.sDiff < sMin || g.sDiff > sMax) continue;
        if (g.isDog) { /* always */ }
        else {
          if (favMode === 'none') continue;
          if (favMode === 'cap6' && g.absLine > 6) continue;
          if (favMode === 'cap4' && g.absLine > 4) continue;
        }
        if (g.covers) w++; else l++;
      }
      const n = w + l;
      if (n < 50) continue;
      const pct = w / n * 100;
      const roi = (w * 0.91 - l) / n * 100;
      combos.push({ pThresh, sMin, sMax, favMode, w, l, n, pct, roi });
    }
  }
}
combos.sort((a, b) => b.roi - a.roi);
for (const c of combos.slice(0, 15)) {
  console.log(`  p>=${c.pThresh.toFixed(2)} sDiff ${c.sMin}-${c.sMax} favs:${c.favMode.padEnd(5)} | ${c.w}-${c.l} (${c.pct.toFixed(1)}%) n=${c.n} ROI:${c.roi.toFixed(1)}%`);
}

// === BEST COMBOS BY WIN% (min 50 picks) ===
console.log('\n=== TOP 15 COMBOS BY WIN% (min 50 picks) ===');
combos.sort((a, b) => b.pct - a.pct);
for (const c of combos.slice(0, 15)) {
  console.log(`  p>=${c.pThresh.toFixed(2)} sDiff ${c.sMin}-${c.sMax} favs:${c.favMode.padEnd(5)} | ${c.w}-${c.l} (${c.pct.toFixed(1)}%) n=${c.n} ROI:${c.roi.toFixed(1)}%`);
}

// === VOLUME SWEET SPOT (min 200 picks, best ROI) ===
console.log('\n=== BEST HIGH-VOLUME (min 200 picks) ===');
const highVol = combos.filter(c => c.n >= 200).sort((a, b) => b.roi - a.roi);
for (const c of highVol.slice(0, 10)) {
  console.log(`  p>=${c.pThresh.toFixed(2)} sDiff ${c.sMin}-${c.sMax} favs:${c.favMode.padEnd(5)} | ${c.w}-${c.l} (${c.pct.toFixed(1)}%) n=${c.n} ROI:${c.roi.toFixed(1)}%`);
}
