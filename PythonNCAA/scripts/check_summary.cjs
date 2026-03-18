const fs = require('fs');
const h = JSON.parse(fs.readFileSync('data/history.json','utf8'));
const runs = h.runs || {};

// Simulate what tallyPicks does for side="over"
let w=0, l=0, p=0;
for (const [key, run] of Object.entries(runs)) {
  if (!run.games) continue;
  if (run.burnIn) continue;
  for (const g of run.games) {
    if (!Number.isFinite(g.homeScore) || !Number.isFinite(g.awayScore)) continue;
    if (g.status === "MISSING_ODDS" || g.status === "SKIPPED") continue;

    const pick = g.oPick;
    const pickConf = g.oConf;
    if (!pick || pick === 'PASS') continue;

    // isActionable check
    const actionable = ['high','elite'].includes(String(pickConf).toLowerCase());
    if (!actionable) continue;

    if (pick !== 'OVER') continue; // side filter

    const actual = g.homeScore + g.awayScore;
    let result;
    if (actual === g.total) result = 'PUSH';
    else if (pick === 'OVER') result = actual > g.total ? 'WIN' : 'LOSS';

    if (!result) continue;
    if (result === 'WIN') w++;
    else if (result === 'LOSS') l++;
    else p++;
  }
}
console.log('Manual OVER tally:', w + '-' + l + '-' + p);

// Now check: does gradeResult work?
// Check what gradeResult function does
// The issue might be that oResult is already stored from regrade
// but the tally function calls gradeResult which re-computes

// Check a few games
let checked = 0;
for (const [key, run] of Object.entries(runs)) {
  if (!run.games) continue;
  for (const g of run.games) {
    if (g.oPick && g.oPick !== 'PASS' && g.oResult && checked < 5) {
      checked++;
      console.log(g.oPick, g.oConf, '| oResult:', g.oResult, '| total:', g.total, '| actual:', (g.homeScore||0)+(g.awayScore||0));
    }
  }
}
