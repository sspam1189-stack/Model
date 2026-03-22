const fs = require('fs');
const h = JSON.parse(fs.readFileSync('C:/Users/HenryVM/Desktop/ncaa_picks_daily_bot/data/history.json','utf8'));
const runs = h.runs || {};

// Reproduce the earlier analysis exactly
// Earlier we used: picksHome = margin > 0, then cover check was:
// if (picksHome) covers = g.homeScore + g.line > g.awayScore
// else covers = g.awayScore - g.line > g.homeScore

// Now check: does gradeSpread give the same answer?
function parseSpreadPick(pick) {
  if (!pick || pick === 'PASS') return null;
  const m = pick.match(/(.+?)\s+([+-])(\d+(?:\.\d+)?)/);
  return m ? { team: m[1].trim(), sign: m[2], pts: parseFloat(m[3]) } : null;
}

function gradeSpread(g) {
  const p = parseSpreadPick(g.sPick);
  if (!p) return null;
  const chosenIsHome = p.team === g.home;
  const margin = chosenIsHome ? g.homeScore - g.awayScore : g.awayScore - g.homeScore;
  const val = p.sign === '+' ? margin + p.pts : margin - p.pts;
  if (val === 0) return 'PUSH';
  return val > 0 ? 'WIN' : 'LOSS';
}

// Take a sample game and compare both methods
let count = 0;
for (const [key, run] of Object.entries(runs)) {
  if (!run.games) continue;
  for (const g of run.games) {
    if (!g.sPick || g.sPick === 'PASS' || g.homeScore == null) continue;
    if (count >= 10) break;

    // Method 1: from sPick string (gradeSpread)
    const result1 = gradeSpread(g);

    // Method 2: from margin > 0 (my earlier analysis)
    const picksHome = g.margin > 0;
    let covers;
    if (picksHome) covers = g.homeScore + g.line > g.awayScore;
    else covers = g.awayScore - g.line > g.homeScore;
    const result2 = covers ? 'WIN' : 'LOSS';

    if (result1 !== result2) {
      count++;
      console.log('MISMATCH:', g.sPick);
      console.log('  gradeSpread says:', result1, '| margin method says:', result2);
      console.log('  margin:', g.margin, '| line:', g.line, '| H:', g.homeScore, '| A:', g.awayScore);

      const p = parseSpreadPick(g.sPick);
      const chosenIsHome = p.team === g.home;
      console.log('  pick team:', p.team, '| chosenIsHome:', chosenIsHome, '| sign:', p.sign, '| pts:', p.pts);
      console.log('  pHomeCover:', g.pHomeCover, '| pAwayCover:', g.pAwayCover);

      // The margin method picks home when margin > 0
      // But the sPick might pick a different team based on pHomeCover vs pAwayCover
      console.log('  margin>0:', g.margin > 0, '| pHomeCover > pAwayCover:', (g.pHomeCover||0) > (g.pAwayCover||0));
    }
  }
}

if (count === 0) console.log('No mismatches in first batch');

// Check ALL mismatches
let totalMM = 0;
let totalChecked = 0;
for (const [key, run] of Object.entries(runs)) {
  if (!run.games) continue;
  for (const g of run.games) {
    if (!g.sPick || g.sPick === 'PASS' || g.homeScore == null) continue;
    totalChecked++;
    const result1 = gradeSpread(g);
    const picksHome = g.margin > 0;
    let covers;
    if (picksHome) covers = g.homeScore + g.line > g.awayScore;
    else covers = g.awayScore - g.line > g.homeScore;
    const result2 = covers ? 'WIN' : 'LOSS';
    if (result1 !== result2) totalMM++;
  }
}
console.log('');
console.log('Total checked:', totalChecked, '| Mismatches:', totalMM);

// The earlier analysis used margin > 0 to determine picked side
// But the Bayesian logic uses pHomeCover >= pAwayCover
// These could differ! margin is the raw score diff, pHomeCover is probability-based
// A game could have margin > 0 but pHomeCover < pAwayCover if variance is asymmetric
let marginDiffers = 0;
for (const [key, run] of Object.entries(runs)) {
  if (!run.games) continue;
  for (const g of run.games) {
    if (g.pHomeCover == null || g.homeScore == null) continue;
    const marginSaysHome = g.margin > 0;
    const probSaysHome = g.pHomeCover >= g.pAwayCover;
    if (marginSaysHome !== probSaysHome) marginDiffers++;
  }
}
console.log('Games where margin and pCover disagree on side:', marginDiffers);
