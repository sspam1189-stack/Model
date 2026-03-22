import fs from 'fs';

// Find the residualVar that makes calibration accurate
// P(cover) = Φ(margin / marginStd), marginStd = sqrt(residualVar + kalman + weight vars)
// We need to find residualVar such that actual hit rates match predicted P(cover)

// Normal CDF
function normalCDF(x) {
  const a1 = 0.254829592, a2 = -0.284496736, a3 = 1.421413741;
  const a4 = -1.453152027, a5 = 1.061405429, p = 0.3275911;
  const sign = x < 0 ? -1 : 1;
  const z = Math.abs(x) / Math.SQRT2;
  const t = 1.0 / (1.0 + p * z);
  const y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * Math.exp(-z * z);
  return 0.5 * (1.0 + sign * y);
}

// Inverse normal CDF (Newton's method)
function normalInvCDF(p) {
  if (p <= 0) return -Infinity;
  if (p >= 1) return Infinity;
  let x = 0;
  for (let i = 0; i < 50; i++) {
    const err = normalCDF(x) - p;
    const pdf = Math.exp(-x*x/2) / Math.sqrt(2*Math.PI);
    x -= err / pdf;
  }
  return x;
}

const h = JSON.parse(fs.readFileSync('data/history.json','utf8'));
const runs = h.runs || [];

// Collect all picked games with their margin and result
const games = [];
for (const r of runs) {
  if (r.burnIn) continue;
  for (const g of r.games || []) {
    if (!g.sPick || g.sPick === 'PASS') continue;
    if (g.sResult !== 'WIN' && g.sResult !== 'LOSS') continue;
    if (!Number.isFinite(g.margin)) continue;
    games.push({
      margin: Math.abs(g.margin),  // model's edge (absolute)
      win: g.sResult === 'WIN' ? 1 : 0,
      pCover: g.pCover,
    });
  }
}

console.log(`Total graded picks: ${games.length}`);
console.log(`Overall record: ${games.filter(g=>g.win).length}-${games.filter(g=>!g.win).length} (${(games.filter(g=>g.win).length/games.length*100).toFixed(1)}%)`);

// Try different variance multipliers and find which one minimizes calibration error
console.log('\n=== Testing variance scaling factors ===');
console.log('Factor | AvgCalibErr | Brier | 57-60% | 60-63% | 63-66% | 66-69% | 69-72%');

for (let factor = 1.0; factor <= 3.0; factor += 0.1) {
  const buckets = {};
  let brierSum = 0;

  for (const g of games) {
    // Recalculate P(cover) with scaled variance
    // Original: pCover = Φ(margin / marginStd)
    // We want: margin / marginStd_original = Φ⁻¹(pCover_original)
    // New: pCover_new = Φ(margin / (marginStd * sqrt(factor)))
    // = Φ(Φ⁻¹(pCover_original) / sqrt(factor))

    const origZ = normalInvCDF(g.pCover);
    const newP = normalCDF(origZ / Math.sqrt(factor));

    const bucket = Math.floor(newP * 20) / 20;
    const key = bucket.toFixed(2);
    if (!buckets[key]) buckets[key] = { w: 0, l: 0 };
    if (g.win) buckets[key].w++;
    else buckets[key].l++;

    brierSum += (newP - g.win) ** 2;
  }

  let calibErr = 0;
  let calibN = 0;
  const bucketResults = {};
  for (const [k, v] of Object.entries(buckets)) {
    const n = v.w + v.l;
    if (n < 10) continue;
    const actual = v.w / n;
    const expected = parseFloat(k) + 0.025;
    calibErr += (actual - expected) ** 2 * n;
    calibN += n;
    bucketResults[k] = `${(actual*100).toFixed(0)}%`;
  }

  const avgCalibErr = Math.sqrt(calibErr / Math.max(calibN, 1));
  const brier = brierSum / games.length;

  console.log(
    `${factor.toFixed(1).padEnd(7)}| ${avgCalibErr.toFixed(4).padEnd(12)}| ${brier.toFixed(4).padEnd(6)}| ` +
    `${(bucketResults['0.55']||'—').padEnd(7)}| ${(bucketResults['0.60']||'—').padEnd(7)}| ${(bucketResults['0.65']||'—').padEnd(7)}| ${(bucketResults['0.55']||'—').padEnd(7)}| ${(bucketResults['0.60']||'—').padEnd(7)}`
  );
}
