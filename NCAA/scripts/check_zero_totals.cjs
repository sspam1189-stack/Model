const fs = require('fs');
const h = JSON.parse(fs.readFileSync('data/history.json','utf8'));
const runs = Array.isArray(h.runs) ? h.runs : Object.values(h.runs);
let count = 0, zeroWins = 0;
for (const run of runs) {
  if (!run.games) continue;
  for (const g of run.games) {
    if (!g.oPick || g.oPick === 'PASS') continue;
    if (!g.total || g.total === 0) {
      count++;
      if (g.oResult === 'WIN') zeroWins++;
      if (count <= 20) console.log(run.date + ' | ' + g.oPick + ' ' + g.total + ' | ' + g.away + ' @ ' + g.home + ' | ' + g.oResult + ' | pOU:' + g.pOU);
    }
  }
}
console.log('');
console.log('Total zero-total picks:', count);
console.log('Of which are "wins":', zeroWins);
