const fs = require('fs');
const h = JSON.parse(fs.readFileSync('C:/Users/HenryVM/Desktop/ncaa_picks_daily_bot/data/history.json','utf8'));
const runs = h.runs || {};

// Check what the regrade actually produced
let dogs = 0, favs = 0;
let dogW = 0, dogL = 0, favW = 0, favL = 0;

for (const [key, run] of Object.entries(runs)) {
  if (!run.games) continue;
  for (const g of run.games) {
    if (!g.sPick || g.sPick === 'PASS' || !g.sResult || g.sResult === 'PUSH') continue;

    // Parse pick to determine if it's a dog or fav pick
    const m = g.sPick.match(/(.+?)\s+([+-])(\d+(?:\.\d+)?)/);
    if (!m) continue;
    const sign = m[2];
    // + = getting points (dog), - = giving points (fav)
    const isDogPick = sign === '+';

    if (isDogPick) {
      dogs++;
      if (g.sResult === 'WIN') dogW++;
      else dogL++;
    } else {
      favs++;
      if (g.sResult === 'WIN') favW++;
      else favL++;
    }
  }
}

console.log('After regrade:');
console.log('Dog picks:', dogs, '| Record:', dogW + '-' + dogL, '(' + (dogW/(dogW+dogL)*100).toFixed(1) + '%)');
console.log('Fav picks:', favs, '| Record:', favW + '-' + favL, '(' + (favs > 0 ? (favW/(favW+favL)*100).toFixed(1) : 'N/A') + '%)');
console.log('Total:', (dogW+favW) + '-' + (dogL+favL));

// Check a sample fav pick to see if the regrade messed up
if (favs > 0) {
  let count = 0;
  for (const [key, run] of Object.entries(runs)) {
    if (!run.games) continue;
    for (const g of run.games) {
      if (!g.sPick || g.sPick === 'PASS') continue;
      const m = g.sPick.match(/(.+?)\s+([+-])(\d+(?:\.\d+)?)/);
      if (!m || m[2] !== '-') continue;
      if (count >= 3) break;
      count++;
      console.log('');
      console.log('FAV PICK FOUND:', g.sPick);
      console.log('  line:', g.line, '| margin:', g.margin);
      console.log('  pHomeCover:', g.pHomeCover, '| pAwayCover:', g.pAwayCover);
    }
  }
}
