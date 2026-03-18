const fs = require('fs');
const h = JSON.parse(fs.readFileSync('data/history.json','utf8'));
const runs = Array.isArray(h.runs) ? h.runs : Object.values(h.runs);

// Collect all games with totals data
const games = [];
for (const run of runs) {
  if (!run.games) continue;
  for (const g of run.games) {
    if (g.homeScore == null || g.pOver == null || g.pUnder == null) continue;
    if (!g.total || g.total === 0) continue;
    const actual = g.homeScore + g.awayScore;
    const pOver = g.pOver;
    const pUnder = g.pUnder;
    const bestP = Math.max(pOver, pUnder);
    const side = pOver >= pUnder ? 'OVER' : 'UNDER';
    const tDiff = g.tDiff || 0;
    const covers = side === 'OVER' ? actual > g.total : actual < g.total;
    games.push({ side, bestP, total: g.total, tDiff: Math.abs(tDiff), covers, pOver, pUnder });
  }
}
console.log('Total games with totals data:', games.length);

// Current filters
console.log('\n=== CURRENT FILTERS ===');
let cW=0, cL=0;
for (const g of games) {
  const overQ = g.side === 'OVER' && g.bestP >= 0.65 && g.total <= 150;
  const underQ = g.side === 'UNDER' && g.bestP >= 0.80;
  if (!overQ && !underQ) continue;
  if (g.covers) cW++; else cL++;
}
console.log(`Current: ${cW}-${cL} (${(cW/(cW+cL)*100).toFixed(1)}%) n=${cW+cL} ROI:${((cW*0.91-cL)/(cW+cL)*100).toFixed(1)}%`);

// Over sweep
console.log('\n=== OVER FILTER SWEEP ===');
for (const pThresh of [0.60, 0.63, 0.65, 0.67, 0.70, 0.75, 0.80]) {
  for (const totalCap of [140, 145, 150, 155, 160, 999]) {
    let w=0, l=0;
    for (const g of games) {
      if (g.side !== 'OVER') continue;
      if (g.bestP < pThresh) continue;
      if (g.total > totalCap) continue;
      if (g.covers) w++; else l++;
    }
    if (w+l < 15) continue;
    const n = w+l;
    const pct = (w/n*100).toFixed(1);
    const roi = ((w*0.91-l)/n*100).toFixed(1);
    console.log(`  OVER p>=${pThresh.toFixed(2)} total<=${totalCap}: ${w}-${l} (${pct}%) n=${n} ROI:${roi}%`);
  }
}

// Under sweep
console.log('\n=== UNDER FILTER SWEEP ===');
for (const pThresh of [0.60, 0.63, 0.65, 0.67, 0.70, 0.75, 0.80, 0.85]) {
  let w=0, l=0;
  for (const g of games) {
    if (g.side !== 'UNDER') continue;
    if (g.bestP < pThresh) continue;
    if (g.covers) w++; else l++;
  }
  if (w+l < 10) continue;
  const n = w+l;
  const pct = (w/n*100).toFixed(1);
  const roi = ((w*0.91-l)/n*100).toFixed(1);
  console.log(`  UNDER p>=${pThresh.toFixed(2)}: ${w}-${l} (${pct}%) n=${n} ROI:${roi}%`);
}

// Under with tDiff filter
console.log('\n=== UNDER + tDiff FILTER ===');
for (const pThresh of [0.60, 0.65, 0.70, 0.75, 0.80]) {
  for (const tDiffMin of [0, 3, 5, 7, 10]) {
    let w=0, l=0;
    for (const g of games) {
      if (g.side !== 'UNDER') continue;
      if (g.bestP < pThresh) continue;
      if (g.tDiff < tDiffMin) continue;
      if (g.covers) w++; else l++;
    }
    if (w+l < 10) continue;
    const n = w+l;
    const pct = (w/n*100).toFixed(1);
    const roi = ((w*0.91-l)/n*100).toFixed(1);
    console.log(`  UNDER p>=${pThresh.toFixed(2)} tDiff>=${tDiffMin}: ${w}-${l} (${pct}%) n=${n} ROI:${roi}%`);
  }
}

// Over with tDiff filter
console.log('\n=== OVER + tDiff FILTER ===');
for (const pThresh of [0.60, 0.63, 0.65, 0.67, 0.70]) {
  for (const tDiffMin of [0, 3, 5, 7, 10]) {
    for (const totalCap of [145, 150, 160, 999]) {
      let w=0, l=0;
      for (const g of games) {
        if (g.side !== 'OVER') continue;
        if (g.bestP < pThresh) continue;
        if (g.total > totalCap) continue;
        if (g.tDiff < tDiffMin) continue;
        if (g.covers) w++; else l++;
      }
      if (w+l < 15) continue;
      const n = w+l;
      const pct = (w/n*100).toFixed(1);
      const roi = ((w*0.91-l)/n*100).toFixed(1);
      console.log(`  OVER p>=${pThresh.toFixed(2)} total<=${totalCap} tDiff>=${tDiffMin}: ${w}-${l} (${pct}%) n=${n} ROI:${roi}%`);
    }
  }
}

// Best combos ranked
console.log('\n=== TOP COMBINED TOTALS FILTERS (min 50 picks) ===');
const combos = [];
for (const overP of [0.60, 0.63, 0.65, 0.67, 0.70]) {
  for (const totalCap of [140, 145, 150, 160, 999]) {
    for (const overTD of [0, 3, 5, 7]) {
      for (const underP of [0.65, 0.70, 0.75, 0.80, 0.85]) {
        for (const underTD of [0, 3, 5, 7]) {
          let w=0, l=0;
          for (const g of games) {
            const overQ = g.side === 'OVER' && g.bestP >= overP && g.total <= totalCap && g.tDiff >= overTD;
            const underQ = g.side === 'UNDER' && g.bestP >= underP && g.tDiff >= underTD;
            if (!overQ && !underQ) continue;
            if (g.covers) w++; else l++;
          }
          const n = w+l;
          if (n < 50) continue;
          const pct = w/n*100;
          const roi = (w*0.91-l)/n*100;
          combos.push({overP, totalCap, overTD, underP, underTD, w, l, n, pct, roi});
        }
      }
    }
  }
}
combos.sort((a,b) => b.roi - a.roi);
for (const c of combos.slice(0, 20)) {
  console.log(`  O:p>=${c.overP.toFixed(2)} t<=${c.totalCap} td>=${c.overTD} | U:p>=${c.underP.toFixed(2)} td>=${c.underTD} | ${c.w}-${c.l} (${c.pct.toFixed(1)}%) n=${c.n} ROI:${c.roi.toFixed(1)}%`);
}
