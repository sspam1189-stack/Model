const fs = require('fs');
const h = JSON.parse(fs.readFileSync('data/history.json','utf8'));
const runs = h.runs || {};
const confs = {};
let overW=0, overL=0, underW=0, underL=0;
let totalPicks = 0, passCount = 0;
for (const [key, run] of Object.entries(runs)) {
  if (!run.games) continue;
  for (const g of run.games) {
    if (!g.oPick || g.oPick === 'PASS') { passCount++; continue; }
    totalPicks++;
    const c = g.oConf || 'undefined';
    confs[c] = (confs[c] || 0) + 1;
    if (g.oResult === 'WIN') {
      if (g.oPick === 'OVER') overW++; else underW++;
    } else if (g.oResult === 'LOSS') {
      if (g.oPick === 'OVER') overL++; else underL++;
    }
  }
}
console.log('oConf distribution:');
for (const [c, n] of Object.entries(confs)) {
  console.log('  ' + c + ': ' + n);
}
console.log('');
console.log('Total oPicks (non-PASS):', totalPicks);
console.log('PASS:', passCount);
console.log('');
console.log('OVER:  ' + overW + '-' + overL + ' (' + (overW/(overW+overL)*100).toFixed(1) + '%)');
console.log('UNDER: ' + underW + '-' + underL + ' (' + (underW/(underW+underL)*100).toFixed(1) + '%)');
