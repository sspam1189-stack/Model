import fs from 'fs';
import { BAYES_HYPER } from './defaults.mjs';

const h = JSON.parse(fs.readFileSync('data/history.json','utf8'));
const runs = h.runs || [];
const errors = [];

for (const r of runs) {
  if (r.burnIn) continue;
  for (const g of r.games || []) {
    if (!Number.isFinite(g.homeScore) || !Number.isFinite(g.awayScore)) continue;
    if (!Number.isFinite(g.margin) || !Number.isFinite(g.line)) continue;
    const actualEdge = (g.homeScore - g.awayScore) - g.line;
    const projEdge = g.margin;
    errors.push(actualEdge - projEdge);
  }
}

const mean = errors.reduce((s, x) => s + x, 0) / errors.length;
const variance = errors.reduce((s, x) => s + (x - mean) ** 2, 0) / errors.length;
console.log(`residualVar from data: ${variance.toFixed(1)} (std=${Math.sqrt(variance).toFixed(1)}) from ${errors.length} games`);
console.log(`BAYES_HYPER.residualVar default: ${BAYES_HYPER.residualVar}`);
console.log(`BAYES_HYPER.marginNoise default: ${BAYES_HYPER.marginNoise}`);

// What the model actually uses per-team:
// variance = residualVar / 2  (per team half)
// marginVar = home_variance + away_variance = residualVar (+ kalman + weight vars)
// marginStd = sqrt(marginVar)
console.log(`\nActual marginStd used: ~${Math.sqrt(variance).toFixed(1)} pts`);
console.log(`If using default: ~${Math.sqrt(BAYES_HYPER.residualVar).toFixed(1)} pts`);

// What marginStd SHOULD be for good calibration?
// If a pick has P(cover)=0.70 and margin=5, then:
//   0.70 = Φ(5/marginStd) → marginStd = 5 / Φ⁻¹(0.70) = 5/0.524 = 9.5
// But if actual hit rate at P=0.70 is 60%, then:
//   We need: 0.60 = Φ(5/marginStd) → marginStd = 5 / Φ⁻¹(0.60) = 5/0.253 = 19.7
// The model's marginStd is way too small

// Let's check what typical margins are for picks at different P levels
const byP = {};
for (const r of runs) {
  if (r.burnIn) continue;
  for (const g of r.games || []) {
    if (!g.sPick || g.sPick === 'PASS') continue;
    if (!g.pCover) continue;
    const bucket = Math.floor(g.pCover * 20) / 20;
    const key = bucket.toFixed(2);
    if (!byP[key]) byP[key] = { margins: [], results: [] };
    byP[key].margins.push(g.margin);
    byP[key].results.push(g.sResult === 'WIN' ? 1 : 0);
  }
}

console.log('\n=== P(cover) bucket analysis ===');
for (const [k, v] of Object.entries(byP).sort((a,b) => a[0] - b[0])) {
  const avgMargin = v.margins.reduce((s,x) => s + Math.abs(x), 0) / v.margins.length;
  const hitRate = v.results.reduce((s,x) => s + x, 0) / v.results.length;
  console.log(`P=${k}: n=${v.margins.length}  avgAbsMargin=${avgMargin.toFixed(1)}  actualHitRate=${(hitRate*100).toFixed(1)}%  expectedP=${(parseFloat(k)*100+2.5).toFixed(0)}%`);
}
