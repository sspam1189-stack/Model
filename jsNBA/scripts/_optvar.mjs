import fs from 'fs';

function normalCDF(x) {
  const a1 = 0.254829592, a2 = -0.284496736, a3 = 1.421413741;
  const a4 = -1.453152027, a5 = 1.061405429, p = 0.3275911;
  const sign = x < 0 ? -1 : 1;
  const z = Math.abs(x) / Math.SQRT2;
  const t = 1.0 / (1.0 + p * z);
  const y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * Math.exp(-z * z);
  return 0.5 * (1.0 + sign * y);
}

function normalInvCDF(p) {
  if (p <= 0.001) return -3;
  if (p >= 0.999) return 3;
  let x = 0;
  for (let i = 0; i < 50; i++) {
    const err = normalCDF(x) - p;
    const pdf = Math.exp(-x*x/2) / Math.sqrt(2*Math.PI);
    x -= err / pdf;
  }
  return x;
}

const h = JSON.parse(fs.readFileSync('data/history.json','utf8'));
const games = [];
for (const r of (h.runs || [])) {
  if (r.burnIn) continue;
  for (const g of (r.games || [])) {
    if (!g.sPick || g.sPick === 'PASS') continue;
    if (g.sResult !== 'WIN' && g.sResult !== 'LOSS') continue;
    if (!Number.isFinite(g.pCover) || g.pCover <= 0.5 || g.pCover >= 1) continue;
    games.push({ pCover: g.pCover, win: g.sResult === 'WIN' ? 1 : 0 });
  }
}

console.log(`NBA graded picks: ${games.length}`);
console.log(`Overall: ${games.filter(g=>g.win).length}-${games.filter(g=>!g.win).length} (${(games.filter(g=>g.win).length/games.length*100).toFixed(1)}%)`);

console.log('\nFactor | CalibErr | Brier  | Description');
let bestFactor = 1, bestErr = 999;
for (let factor = 1.0; factor <= 4.0; factor += 0.1) {
  const buckets = {};
  let brierSum = 0;

  for (const g of games) {
    const origZ = normalInvCDF(g.pCover);
    const newP = normalCDF(origZ / Math.sqrt(factor));
    const bucket = Math.floor(newP * 20) / 20;
    const key = bucket.toFixed(2);
    if (!buckets[key]) buckets[key] = { w: 0, l: 0 };
    if (g.win) buckets[key].w++;
    else buckets[key].l++;
    brierSum += (newP - g.win) ** 2;
  }

  let calibErr = 0, calibN = 0;
  for (const [k, v] of Object.entries(buckets)) {
    const n = v.w + v.l;
    if (n < 5) continue;
    const actual = v.w / n;
    const expected = parseFloat(k) + 0.025;
    calibErr += (actual - expected) ** 2 * n;
    calibN += n;
  }

  const avgErr = Math.sqrt(calibErr / Math.max(calibN, 1));
  const brier = brierSum / games.length;
  if (avgErr < bestErr) { bestErr = avgErr; bestFactor = factor; }
  const mark = factor === bestFactor ? ' ← BEST' : '';
  console.log(`${factor.toFixed(1).padEnd(7)}| ${avgErr.toFixed(4).padEnd(9)}| ${brier.toFixed(4)}${mark}`);
}

console.log(`\nBest factor: ${bestFactor.toFixed(1)} (calibErr=${bestErr.toFixed(4)})`);
