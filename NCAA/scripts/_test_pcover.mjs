import fs from 'fs';
const h = JSON.parse(fs.readFileSync('data/history.json','utf8'));

// Test different pCover thresholds on existing graded picks
const thresholds = [0.57, 0.58, 0.59, 0.60, 0.61, 0.62, 0.63, 0.64, 0.65, 0.67, 0.70];

console.log('=== NCAA: Testing P(cover) thresholds ===');
console.log('Thresh | W    | L    | Win%  | Units   | Picks/day | Fav W% | Dog W%');
console.log('-------|------|------|-------|---------|-----------|--------|-------');

for (const t of thresholds) {
  let w = 0, l = 0, favW = 0, favL = 0, dogW = 0, dogL = 0;

  for (const r of (h.runs || [])) {
    if (r.burnIn) continue;
    for (const g of (r.games || [])) {
      if (!g.sPick || g.sPick === 'PASS') continue;
      if (!g.sResult || !g.pCover) continue;
      if (g.pCover < t) continue;

      const m = g.sPick.match(/([+-])(\d+(?:\.\d+)?)$/);
      const isFav = m && m[1] === '-';

      if (g.sResult === 'WIN') {
        w++;
        if (isFav) favW++; else dogW++;
      } else if (g.sResult === 'LOSS') {
        l++;
        if (isFav) favL++; else dogL++;
      }
    }
  }

  const n = w + l;
  const pct = n > 0 ? (w / n * 100).toFixed(1) : 'N/A';
  const units = (w - l * 1.1).toFixed(1);
  const perDay = (n / 73).toFixed(1);
  const favPct = (favW + favL) > 0 ? (favW / (favW + favL) * 100).toFixed(1) : 'N/A';
  const dogPct = (dogW + dogL) > 0 ? (dogW / (dogW + dogL) * 100).toFixed(1) : 'N/A';
  const mark = t === 0.63 ? ' ← current' : '';

  console.log(`${t.toFixed(2).padEnd(7)}| ${String(w).padEnd(5)}| ${String(l).padEnd(5)}| ${pct.padEnd(6)}| ${units.padEnd(8)}| ${perDay.padEnd(10)}| ${favPct.padEnd(7)}| ${dogPct}${mark}`);
}
