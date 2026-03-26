const fs = require('fs');
const h = JSON.parse(fs.readFileSync('C:/Users/HenryVM/Desktop/ncaa_picks_daily_bot/data/history.json','utf8'));
const runs = h.runs || {};

let withBayes = 0, withoutBayes = 0;
let newPicks = 0, newWins = 0, newLosses = 0;

for (const [key, run] of Object.entries(runs)) {
  if (!run.games) continue;
  for (const g of run.games) {
    if (g.homeScore == null) continue;
    if (g.pHomeCover != null && g.pAwayCover != null) {
      withBayes++;
      // Check the new pick
      const sDiff = g.sDiff || 0;
      const bestP = Math.max(g.pHomeCover, g.pAwayCover);
      const side = g.pHomeCover >= g.pAwayCover ? "home" : "away";
      const isDog = side === "home" ? g.line > 0 : g.line < 0;

      if (isDog && bestP >= 0.60 && sDiff >= 3 && sDiff <= 9) {
        newPicks++;
        // Check cover
        const absLine = Math.abs(g.line || 0);
        const pickSpread = side === "home" ? g.line : -g.line;
        let covers;
        if (side === "home") covers = g.homeScore + g.line > g.awayScore;
        else covers = g.awayScore - g.line > g.homeScore;
        if (covers) newWins++;
        else newLosses++;
      }
    } else {
      withoutBayes++;
    }
  }
}

console.log('Games with Bayesian probs:', withBayes);
console.log('Games without:', withoutBayes);
console.log('');
console.log('New filter (dogs only, pCover>=0.60, sDiff 3-9):');
console.log('Record:', newWins + '-' + newLosses, '(' + (newWins/(newWins+newLosses)*100).toFixed(1) + '%)  n=' + newPicks);

// Also check: what's the sPick showing now after regrade?
let sPickWins = 0, sPickLosses = 0;
for (const [key, run] of Object.entries(runs)) {
  if (!run.games) continue;
  for (const g of run.games) {
    if (g.sResult === 'WIN') sPickWins++;
    else if (g.sResult === 'LOSS') sPickLosses++;
  }
}
console.log('');
console.log('Current sResult in history:', sPickWins + '-' + sPickLosses);

// Check a specific loss - what's happening
let lossCount = 0;
for (const [key, run] of Object.entries(runs)) {
  if (!run.games) continue;
  for (const g of run.games) {
    if (g.sResult !== 'LOSS' || lossCount >= 5) continue;
    if (g.pHomeCover == null) continue;

    const side = g.pHomeCover >= g.pAwayCover ? "home" : "away";
    const isDog = side === "home" ? g.line > 0 : g.line < 0;

    lossCount++;
    console.log('');
    console.log('LOSS:', g.away, '@', g.home);
    console.log('  sPick:', g.sPick, '| line:', g.line);
    console.log('  pHomeCover:', g.pHomeCover, '| pAwayCover:', g.pAwayCover);
    console.log('  side:', side, '| isDog:', isDog);
    console.log('  H:', g.homeScore, '| A:', g.awayScore);
  }
}
