import fs from 'fs';
const h = JSON.parse(fs.readFileSync('data/history.json','utf8'));
const runs = h.runs || [];

// Check what sResult values look like
const resultVals = new Set();
let buckets = {};

for (const r of runs) {
  for (const g of (r.games || [])) {
    if (!g.sPick || g.sPick === 'PASS' || g.sResult === undefined) continue;
    resultVals.add(g.sResult);

    const p = g.pCover || 0;
    const b = Math.floor(p * 20) / 20;
    const key = b.toFixed(2);
    if (!buckets[key]) buckets[key] = { w: 0, l: 0, total: 0 };
    if (g.sResult === 'WIN' || g.sResult === 'W') buckets[key].w++;
    else if (g.sResult === 'LOSS' || g.sResult === 'L') buckets[key].l++;
    buckets[key].total++;
  }
}

console.log('sResult values found:', [...resultVals]);
console.log('\npCover bucket | W    | L    | Total | ActualRate | Expected');
const sorted = Object.entries(buckets).sort((a, b) => parseFloat(a[0]) - parseFloat(b[0]));
for (const [k, v] of sorted) {
  const n = v.w + v.l;
  const rate = n > 0 ? (v.w / n * 100).toFixed(1) : 'N/A';
  const expected = (parseFloat(k) * 100 + 2.5).toFixed(0); // midpoint
  const delta = n > 0 ? (v.w / n * 100 - parseFloat(k) * 100 - 2.5).toFixed(1) : 'N/A';
  console.log(`${k.padEnd(14)}| ${String(v.w).padEnd(5)}| ${String(v.l).padEnd(5)}| ${String(v.total).padEnd(6)}| ${rate}%`.padEnd(55) + `| ~${expected}%  Δ=${delta}`);
}

// Now show the calibration.mjs output
console.log('\n=== Calibration module output ===');
import { buildCalibrationTable } from './calibration.mjs';
const table = buildCalibrationTable(h);
for (const r of table) {
  const mid = Math.round(r.midpoint * 100);
  const sDelta = r.spread.winPct != null ? r.spread.winPct - mid : null;
  console.log(`${r.label.padEnd(10)} Spread: n=${String(r.spread.n).padEnd(5)} actual=${r.spread.winPct}%  expected=${mid}%  Δ=${sDelta}  units=${r.spread.units}`);
}
