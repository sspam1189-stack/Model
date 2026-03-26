const fs = require('fs');
const h = JSON.parse(fs.readFileSync('C:/Users/HenryVM/Desktop/ncaa_picks_daily_bot/data/history.json','utf8'));
const runs = h.runs || {};

// Step 1: Verify gradeSpread matches stored sResult on the ORIGINAL data
// (before regrade changed things, we can check the games that still have picks)

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

// Step 2: Walk through specific examples manually
console.log('=== MANUAL VERIFICATION OF SPECIFIC GAMES ===\n');

let count = 0;
for (const [key, run] of Object.entries(runs)) {
  if (!run.games) continue;
  for (const g of run.games) {
    if (!g.sPick || g.sPick === 'PASS' || g.homeScore == null) continue;
    if (count >= 20) break;
    count++;

    const p = parseSpreadPick(g.sPick);
    const chosenIsHome = p.team === g.home;
    const isDog = p.sign === '+';
    const isFav = p.sign === '-';

    // Manual cover check
    let covers;
    if (chosenIsHome) {
      if (isDog) {
        // Home dog getting points: homeScore + pts > awayScore?
        covers = g.homeScore + p.pts > g.awayScore;
      } else {
        // Home fav giving points: homeScore - pts > awayScore?
        covers = g.homeScore - p.pts > g.awayScore;
      }
    } else {
      if (isDog) {
        // Away dog getting points: awayScore + pts > homeScore?
        covers = g.awayScore + p.pts > g.homeScore;
      } else {
        // Away fav giving points: awayScore - pts > homeScore?
        covers = g.awayScore - p.pts > g.homeScore;
      }
    }

    const graded = gradeSpread(g);
    const manualResult = covers ? 'WIN' : 'LOSS';
    const match = graded === manualResult ? 'OK' : 'MISMATCH';

    if (count <= 5 || match === 'MISMATCH') {
      console.log(`${match} | ${g.sPick}`);
      console.log(`  H:${g.homeScore} A:${g.awayScore} | line:${g.line} | ${isDog?'DOG':'FAV'} | chosenIsHome:${chosenIsHome}`);
      console.log(`  gradeSpread: ${graded} | manual: ${manualResult}`);
      console.log('');
    }
  }
}

// Step 3: Now systematically check dogs vs favs with MANUAL cover logic
console.log('\n=== SYSTEMATIC CHECK: Dogs vs Favs (pCover>=0.63, sDiff 3-9) ===\n');

let dogW=0, dogL=0, favW=0, favL=0;
let dogSamples = [];
let favSamples = [];

for (const [key, run] of Object.entries(runs)) {
  if (!run.games) continue;
  for (const g of run.games) {
    if (g.homeScore == null || g.pHomeCover == null) continue;
    const sDiff = g.sDiff || 0;
    const pCover = Math.max(g.pHomeCover, g.pAwayCover);
    if (sDiff < 3 || sDiff > 9 || pCover < 0.63) continue;

    const side = g.pHomeCover >= g.pAwayCover ? 'home' : 'away';
    const absLine = Math.abs(g.line || 0);
    const homeFav = g.line < 0;

    // Build the hypothetical pick string
    let pickStr;
    if (side === 'home') {
      pickStr = homeFav ? `${g.home} -${absLine}` : `${g.home} +${absLine}`;
    } else {
      pickStr = homeFav ? `${g.away} +${absLine}` : `${g.away} -${absLine}`;
    }

    const p = parseSpreadPick(pickStr);
    const chosenIsHome = p.team === g.home;
    const isDog = p.sign === '+';

    // Manual cover
    let covers;
    if (chosenIsHome) {
      covers = isDog
        ? g.homeScore + p.pts > g.awayScore
        : g.homeScore - p.pts > g.awayScore;
    } else {
      covers = isDog
        ? g.awayScore + p.pts > g.homeScore
        : g.awayScore - p.pts > g.homeScore;
    }

    if (isDog) {
      if (covers) dogW++; else dogL++;
      if (dogSamples.length < 5) dogSamples.push({
        matchup: g.away + ' @ ' + g.home,
        pick: pickStr, H: g.homeScore, A: g.awayScore,
        covers, line: g.line
      });
    } else {
      if (covers) favW++; else favL++;
      if (favSamples.length < 5) favSamples.push({
        matchup: g.away + ' @ ' + g.home,
        pick: pickStr, H: g.homeScore, A: g.awayScore,
        covers, line: g.line
      });
    }
  }
}

console.log('DOGS: ' + dogW + '-' + dogL + ' (' + (dogW/(dogW+dogL)*100).toFixed(1) + '%)  n=' + (dogW+dogL));
console.log('FAVS: ' + favW + '-' + favL + ' (' + (favW/(favW+favL)*100).toFixed(1) + '%)  n=' + (favW+favL));
console.log('');

console.log('Dog samples:');
for (const s of dogSamples) {
  console.log('  ' + s.pick + ' | H:' + s.H + ' A:' + s.A + ' | line:' + s.line + ' | ' + (s.covers?'WIN':'LOSS'));
}
console.log('');
console.log('Fav samples:');
for (const s of favSamples) {
  console.log('  ' + s.pick + ' | H:' + s.H + ' A:' + s.A + ' | line:' + s.line + ' | ' + (s.covers?'WIN':'LOSS'));
}

// Step 4: Check specifically - when model picks a fav, what does that mean?
// Sportsbook convention: line < 0 = home favored, line > 0 = home dog
// If pHomeCover > pAwayCover AND home is favored (line < 0), then:
// pick = "Home -X" (fav). For this to cover: homeScore - X > awayScore

console.log('\n=== LINE CONVENTION CHECK ===');
console.log('homeFav = (line < 0) — sportsbook convention');
console.log('');

let lineChecks = 0;
for (const [key, run] of Object.entries(runs)) {
  if (!run.games) continue;
  for (const g of run.games) {
    if (g.homeScore == null || lineChecks >= 10) continue;
    if (Math.abs(g.line) < 3 || Math.abs(g.line) > 20) continue;
    lineChecks++;
    const homeFav = g.line < 0;
    // If home is favored, they should win more often (on average)
    const homeWon = g.homeScore > g.awayScore;
    console.log('  line: ' + String(g.line).padEnd(6) + ' homeFav: ' + homeFav + ' | H:' + g.homeScore + ' A:' + g.awayScore + ' | homeWon: ' + homeWon + ' | ' + g.away + ' @ ' + g.home);
  }
}

// Aggregate: when line < 0, does home win more? (sportsbook: line < 0 = home fav)
let linePosHomeWin = 0, linePosHomeTotal = 0;
let lineNegHomeWin = 0, lineNegHomeTotal = 0;
for (const [key, run] of Object.entries(runs)) {
  if (!run.games) continue;
  for (const g of run.games) {
    if (g.homeScore == null || Math.abs(g.line) < 1) continue;
    const homeWon = g.homeScore > g.awayScore;
    if (g.line > 0) { linePosHomeTotal++; if (homeWon) linePosHomeWin++; }
    else { lineNegHomeTotal++; if (homeWon) lineNegHomeWin++; }
  }
}
console.log('');
console.log('line > 0: home wins ' + linePosHomeWin + '/' + linePosHomeTotal + ' (' + (linePosHomeWin/linePosHomeTotal*100).toFixed(1) + '%)');
console.log('line < 0: home wins ' + lineNegHomeWin + '/' + lineNegHomeTotal + ' (' + (lineNegHomeWin/lineNegHomeTotal*100).toFixed(1) + '%)');
console.log('');
console.log('If line<0 means home favored, home should win >50% when line<0');
