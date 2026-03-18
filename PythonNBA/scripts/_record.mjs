import fs from 'fs';
const h = JSON.parse(fs.readFileSync('data/history.json','utf8'));

let w = 0, l = 0, p = 0;
let favW = 0, favL = 0, dogW = 0, dogL = 0;

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
    } else if (g.sResult === 'PUSH') {
      p++;
    }
  }
}

const payout = 100/110;
const totalUnits = (w * payout - l).toFixed(1);
const favUnits = (favW * payout - favL).toFixed(1);
const dogUnits = (dogW * payout - dogL).toFixed(1);

console.log(`Overall: ${w}-${l}-${p} (${(w/(w+l)*100).toFixed(1)}%) ${totalUnits}u`);
console.log(`Favs:    ${favW}-${favL} (${(favW/(favW+favL)*100).toFixed(1)}%) ${favUnits}u`);
console.log(`Dogs:    ${dogW}-${dogL} (${(dogW/(dogW+dogL)*100).toFixed(1)}%) ${dogUnits}u`);
