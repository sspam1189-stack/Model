import fs from 'fs';

// Compare the original backup vs recalculated history
const orig = JSON.parse(fs.readFileSync('data/history_pre_recalc_2026-03-15T15-00-39.json', 'utf8'));
const curr = JSON.parse(fs.readFileSync('data/history.json', 'utf8'));

const origRuns = orig.runs || [];
const currRuns = curr.runs || [];

console.log('=== Original vs Recalculated ===');
console.log(`Runs: ${origRuns.length} vs ${currRuns.length}\n`);

// Compare weights
console.log('Original weights:', JSON.stringify(orig.weights).slice(0, 200));
console.log('Current weights:', JSON.stringify(curr.weights).slice(0, 200));
console.log();

// Count picks per run
let origW = 0, origL = 0, currW = 0, currL = 0;
let origPicks = 0, currPicks = 0;

for (let i = 0; i < origRuns.length; i++) {
  const oRun = origRuns[i];
  const cRun = currRuns[i];
  if (!oRun || !cRun) continue;

  let oW = 0, oL = 0, cW = 0, cL = 0, oPicks = 0, cPicks = 0;

  for (const g of oRun.games || []) {
    if (g.sPick && g.sPick !== 'PASS' && (g.sConf === 'high' || g.sConf === 'elite')) {
      oPicks++;
      if (g.sResult === 'WIN') oW++;
      if (g.sResult === 'LOSS') oL++;
    }
  }

  for (const g of cRun.games || []) {
    if (g.sPick && g.sPick !== 'PASS' && (g.sConf === 'high' || g.sConf === 'elite')) {
      cPicks++;
      if (g.sResult === 'WIN') cW++;
      if (g.sResult === 'LOSS') cL++;
    }
  }

  origW += oW; origL += oL; origPicks += oPicks;
  currW += cW; currL += cL; currPicks += cPicks;

  if (oPicks !== cPicks || oW !== cW) {
    console.log(`${oRun.dateDisplay}: orig ${oW}-${oL} (${oPicks} picks) → recalc ${cW}-${cL} (${cPicks} picks)  [${oPicks - cPicks} picks lost]`);
  }
}

console.log(`\nOriginal total: ${origW}-${origL} (${origPicks} picks) ${(origW/(origW+origL)*100).toFixed(1)}%`);
console.log(`Recalc total:   ${currW}-${currL} (${currPicks} picks) ${(currW/(currW+currL)*100).toFixed(1)}%`);
console.log(`Lost: ${origPicks - currPicks} picks, ${origW - currW} wins, ${origL - currL} losses`);

// Check: are the lost picks from high-conf or from a threshold change?
console.log('\n=== Checking what changed ===');
// Compare probHigh thresholds
for (let i = 0; i < origRuns.length; i++) {
  const oW = origRuns[i]?.weightsUsed;
  const cW = currRuns[i]?.weightsUsed;
  if (!oW || !cW) continue;
  if (oW.probHigh !== cW.probHigh || oW.probElite !== cW.probElite) {
    console.log(`${origRuns[i].dateDisplay}: probHigh ${oW.probHigh}→${cW.probHigh}  probElite ${oW.probElite}→${cW.probElite}`);
  }
}
