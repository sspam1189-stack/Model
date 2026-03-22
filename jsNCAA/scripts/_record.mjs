import fs from 'fs';
const h = JSON.parse(fs.readFileSync('data/history.json','utf8'));

let w = 0, l = 0;
let favW = 0, favL = 0, dogW = 0, dogL = 0;
let totalPicks = 0;

for (const r of (h.runs || [])) {
  if (r.burnIn) continue;
  for (const g of (r.games || [])) {
    if (!g.sPick || g.sPick === 'PASS') continue;
    if (!g.sResult) continue;

    const m = g.sPick.match(/([+-])(\d+(?:\.\d+)?)$/);
    const isFav = m && m[1] === '-';
    const isDog = m && m[1] === '+';

    if (g.sResult === 'WIN') {
      w++;
      if (isFav) favW++;
      if (isDog) dogW++;
    } else if (g.sResult === 'LOSS') {
      l++;
      if (isFav) favL++;
      if (isDog) dogL++;
    }
    totalPicks++;
  }
}

console.log('=== NCAA Current (with 1.4x factor) ===');
console.log(`Overall: ${w}-${l} (${(w/(w+l)*100).toFixed(1)}%) ${(w - l*1.1).toFixed(1)}u  (${totalPicks} picks, ${(totalPicks/73).toFixed(1)}/day)`);
console.log(`Favs:    ${favW}-${favL} (${(favW/(favW+favL)*100).toFixed(1)}%) ${(favW - favL*1.1).toFixed(1)}u`);
console.log(`Dogs:    ${dogW}-${dogL} (${(dogW/(dogW+dogL)*100).toFixed(1)}%) ${(dogW - dogL*1.1).toFixed(1)}u`);
