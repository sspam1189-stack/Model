const fs = require('fs');
const h = JSON.parse(fs.readFileSync('data/history.json','utf8'));
const runs = h.runs || {};
let sW=0, sL=0, sP=0, oW=0, oL=0, oP=0;
for (const [key, run] of Object.entries(runs)) {
  if (!run.games) continue;
  for (const g of run.games) {
    if (g.sResult === 'WIN') sW++;
    else if (g.sResult === 'LOSS') sL++;
    else if (g.sResult === 'PUSH') sP++;
    if (g.oResult === 'WIN') oW++;
    else if (g.oResult === 'LOSS') oL++;
    else if (g.oResult === 'PUSH') oP++;
  }
}
console.log('NCAA MODEL ALL-TIME RECORDS');
console.log('═══════════════════════════════');
console.log('Spreads: ' + sW + '-' + sL + (sP ? '-'+sP : '') + ' (' + (sW/(sW+sL)*100).toFixed(1) + '%)');
console.log('Totals:  ' + oW + '-' + oL + (oP ? '-'+oP : '') + ' (' + (oW/(oW+oL)*100).toFixed(1) + '%)');
console.log('Combined: ' + (sW+oW) + '-' + (sL+oL) + ' (' + ((sW+oW)/(sW+oW+sL+oL)*100).toFixed(1) + '%)');
