import fs from 'fs';
const h = JSON.parse(fs.readFileSync('data/history.json','utf8'));

let w = 0, l = 0;
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
    }
  }
}

// Method 1: W * (100/110) - L  (what my script did)
console.log('Method 1 (W × 0.909 - L):');
console.log(`  Overall: ${(w * (100/110) - l).toFixed(1)}u`);
console.log(`  Favs:    ${(favW * (100/110) - favL).toFixed(1)}u`);
console.log(`  Dogs:    ${(dogW * (100/110) - dogL).toFixed(1)}u`);

// Method 2: W * 1 - L * 1.1  (win 1u, lose 1.1u at -110)
console.log('\nMethod 2 (W × 1 - L × 1.1):');
console.log(`  Overall: ${(w - l * 1.1).toFixed(1)}u`);
console.log(`  Favs:    ${(favW - favL * 1.1).toFixed(1)}u`);
console.log(`  Dogs:    ${(dogW - dogL * 1.1).toFixed(1)}u`);

// Method 3: flat 1u per pick, W - L (no juice)
console.log('\nMethod 3 (W - L, no juice):');
console.log(`  Overall: ${w - l}u`);
console.log(`  Favs:    ${favW - favL}u`);
console.log(`  Dogs:    ${dogW - dogL}u`);

// Your numbers: +41.9, +18.1, +23.8
// 131-81 → 131 - 81*1.1 = 131 - 89.1 = 41.9  ← THIS IS IT
console.log('\nYour expected: +41.9u, +18.1u, +23.8u');
console.log(`Check: 131 - 81×1.1 = ${(131 - 81*1.1).toFixed(1)}`);
console.log(`Check: 72 - 49×1.1 = ${(72 - 49*1.1).toFixed(1)}`);
console.log(`Check: 59 - 32×1.1 = ${(59 - 32*1.1).toFixed(1)}`);

console.log(`\nRecord: ${w}-${l} (${(w/(w+l)*100).toFixed(1)}%)`);
console.log(`Favs: ${favW}-${favL} (${(favW/(favW+favL)*100).toFixed(1)}%)`);
console.log(`Dogs: ${dogW}-${dogL} (${(dogW/(dogW+dogL)*100).toFixed(1)}%)`);
