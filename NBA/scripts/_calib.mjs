import fs from 'fs';
import { buildCalibrationTable } from './calibration.mjs';

const h = JSON.parse(fs.readFileSync('data/history.json','utf8'));

// Check sResult values
const resultVals = new Set();
let buckets = {};

for (const r of (h.runs || [])) {
  if (r.burnIn) continue;
  for (const g of (r.games || [])) {
    if (!g.sPick || g.sPick === 'PASS') continue;
    if (g.sResult) resultVals.add(g.sResult);
    if (g.sResult !== 'WIN' && g.sResult !== 'LOSS') continue;
    const p = g.pCover || 0;
    const b = Math.floor(p * 20) / 20;
    const key = b.toFixed(2);
    if (!buckets[key]) buckets[key] = { w: 0, l: 0 };
    if (g.sResult === 'WIN') buckets[key].w++;
    else buckets[key].l++;
  }
}

console.log('sResult values:', [...resultVals]);
console.log('\n=== NBA P(cover) Calibration ===');
console.log('Bucket    | W    | L    | Actual | Expected | Δ');
for (const [k, v] of Object.entries(buckets).sort((a,b) => a[0]-b[0])) {
  const n = v.w + v.l;
  const actual = (v.w / n * 100).toFixed(1);
  const expected = (parseFloat(k) * 100 + 2.5).toFixed(0);
  const delta = (v.w / n * 100 - parseFloat(k) * 100 - 2.5).toFixed(1);
  console.log(`P=${k}  | ${String(v.w).padEnd(5)}| ${String(v.l).padEnd(5)}| ${actual.padEnd(7)}| ${expected.padEnd(9)}| ${delta}`);
}

// Also try calibration module
console.log('\n=== NBA Calibration Module Output ===');
const table = buildCalibrationTable(h);
for (const r of table) {
  if (r.spread.n === 0) continue;
  const mid = Math.round(r.midpoint * 100);
  const delta = r.spread.winPct != null ? r.spread.winPct - mid : null;
  console.log(`${r.label.padEnd(10)} n=${String(r.spread.n).padEnd(5)} actual=${r.spread.winPct}%  expected=${mid}%  Δ=${delta}  units=${r.spread.units}`);
}
