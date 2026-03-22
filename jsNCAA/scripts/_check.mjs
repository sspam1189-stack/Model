import fs from 'fs';

// Check NCAA model for Purdue
const h = JSON.parse(fs.readFileSync('data/history.json','utf8'));
const runs = h.runs || [];
const today = runs.filter(r => r.date === '20260315' || r.dateDisplay === '2026-03-15');
if (!today.length) {
  console.log('No NCAA run for today');
} else {
  for (const r of today) {
    for (const g of (r.games || [])) {
      const name = (g.home + ' ' + g.away).toLowerCase();
      if (name.includes('purdue')) {
        console.log('=== PURDUE (NCAA Model) ===');
        console.log(`  ${g.away} @ ${g.home}`);
        console.log(`  Line: ${g.line}  Total: ${g.total}`);
        console.log(`  Proj: Away ${g.aS}  Home ${g.hS}  pT: ${g.pT}`);
        console.log(`  Margin: ${g.margin}  sDiff: ${g.sDiff}`);
        console.log(`  P(home cover): ${g.pHomeCover}  P(away cover): ${g.pAwayCover}`);
        console.log(`  sPick: ${g.sPick}  sConf: ${g.sConf}`);
        console.log(`  oPick: ${g.oPick}`);
        console.log(`  dNET: ${g._features?.dNET?.toFixed(1)}`);
      }
    }
    // Also show all picks for today
    console.log('\n=== All NCAA picks today ===');
    for (const g of (r.games || [])) {
      if (g.sPick !== 'PASS') {
        console.log(`  ${g.sPick} (P=${g.pCover}) — ${g.away} @ ${g.home}`);
      }
    }
  }
}
