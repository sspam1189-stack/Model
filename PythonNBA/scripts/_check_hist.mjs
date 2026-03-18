import fs from 'fs';
const h = JSON.parse(fs.readFileSync('data/history.json','utf8'));
const runs = h.runs || [];
console.log('Total runs:', runs.length);
console.log('First:', runs[0]?.dateDisplay, 'Last:', runs[runs.length-1]?.dateDisplay);
console.log('Burn-in runs:', runs.filter(r => r.burnIn).length);
console.log('Live runs:', runs.filter(r => !r.burnIn).length);

// Count graded picks
let w = 0, l = 0;
for (const r of runs) {
  if (r.burnIn) continue;
  for (const g of (r.games || [])) {
    if (g.sResult === 'WIN') w++;
    else if (g.sResult === 'LOSS') l++;
  }
}
console.log(`Spread record: ${w}-${l} (${(w/(w+l)*100).toFixed(1)}%)`);
console.log('Stored weights:', JSON.stringify(h.weights || {}).slice(0, 200));
console.log('Stored residualVar:', h.residualVar);
