const fs = require('fs');
const h = JSON.parse(fs.readFileSync('data/history.json','utf8'));
const runs = h.runs || {};
let z = 0;
for (const [key, run] of Object.entries(runs)) {
  if (!run.games) continue;
  for (const g of run.games) {
    if (g.sPick && g.sPick !== 'PASS' && g.sPick.includes('+0')) z++;
  }
}
console.log('Remaining +0 picks:', z);
