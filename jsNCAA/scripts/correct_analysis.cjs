const fs = require('fs');
const h = JSON.parse(fs.readFileSync('C:/Users/HenryVM/Desktop/ncaa_picks_daily_bot/data/history.json','utf8'));
const runs = h.runs || {};

// Correct cover check matching gradeSpread exactly
function checkCover(home, away, homeScore, awayScore, line, picksHome) {
  // Determine the pick spread: if picking home dog, they get |line| points
  // Pick format: "Team +X" or "Team -X"
  const absLine = Math.abs(line);

  if (picksHome) {
    // Sportsbook convention: line < 0 = home favored, line > 0 = home dog
    // line=+8.5 means home is dog getting 8.5 points
    // line=-8.5 means home is fav giving 8.5 points
    const homeFav = line < 0;
    if (homeFav) {
      // home is fav, pick = "home -absLine"
      // gradeSpread: val = (homeScore-awayScore) - absLine
      return (homeScore - awayScore) - absLine > 0;
    } else {
      // home is dog, pick = "home +absLine"
      // gradeSpread: val = (homeScore-awayScore) + absLine
      return (homeScore - awayScore) + absLine > 0;
    }
  } else {
    // Picking away
    const homeFav = line < 0;
    if (homeFav) {
      // away is dog (home is fav), pick = "away +absLine"
      return (awayScore - homeScore) + absLine > 0;
    } else {
      // away is fav (home is dog), pick = "away -absLine"
      return (awayScore - homeScore) - absLine > 0;
    }
  }
}

// Now redo the analysis correctly
const buckets = {
  'DOG': {w:0, l:0},
  'FAV': {w:0, l:0},
};
const dogByLine = {
  'dog 0-6': {w:0,l:0},
  'dog 6-10': {w:0,l:0},
  'dog 10+': {w:0,l:0},
};
const dogByDNET = {
  'dNET 0-5': {w:0,l:0},
  'dNET 5-10': {w:0,l:0},
  'dNET 10-15': {w:0,l:0},
  'dNET 15-20': {w:0,l:0},
  'dNET 20+': {w:0,l:0},
};
const favByLine = {
  'fav 0-6': {w:0,l:0},
  'fav 6-10': {w:0,l:0},
  'fav 10+': {w:0,l:0},
};
const dogByPCover = {};

for (const [key, run] of Object.entries(runs)) {
  if (!run.games) continue;
  for (const g of run.games) {
    if (g.homeScore == null || g.pHomeCover == null) continue;
    const sDiff = g.sDiff || 0;
    const pCover = Math.max(g.pHomeCover, g.pAwayCover);
    if (sDiff < 3 || sDiff > 9 || pCover < 0.60) continue;

    const side = g.pHomeCover >= g.pAwayCover ? 'home' : 'away';
    const absLine = Math.abs(g.line || 0);
    const homeFav = g.line < 0;
    const dNET = Math.abs((g._features?.dNET) || 0);

    // Is picked side dog? (sportsbook: line < 0 = home fav)
    const isDog = side === 'home' ? g.line > 0 : g.line < 0;

    const covers = checkCover(g.home, g.away, g.homeScore, g.awayScore, g.line, side === 'home');

    if (isDog) {
      if (covers) buckets.DOG.w++; else buckets.DOG.l++;

      // By line size
      if (absLine <= 6) { if (covers) dogByLine['dog 0-6'].w++; else dogByLine['dog 0-6'].l++; }
      else if (absLine <= 10) { if (covers) dogByLine['dog 6-10'].w++; else dogByLine['dog 6-10'].l++; }
      else { if (covers) dogByLine['dog 10+'].w++; else dogByLine['dog 10+'].l++; }

      // By dNET
      if (dNET <= 5) { if (covers) dogByDNET['dNET 0-5'].w++; else dogByDNET['dNET 0-5'].l++; }
      else if (dNET <= 10) { if (covers) dogByDNET['dNET 5-10'].w++; else dogByDNET['dNET 5-10'].l++; }
      else if (dNET <= 15) { if (covers) dogByDNET['dNET 10-15'].w++; else dogByDNET['dNET 10-15'].l++; }
      else if (dNET <= 20) { if (covers) dogByDNET['dNET 15-20'].w++; else dogByDNET['dNET 15-20'].l++; }
      else { if (covers) dogByDNET['dNET 20+'].w++; else dogByDNET['dNET 20+'].l++; }

      // By pCover
      for (const thresh of [0.55, 0.58, 0.60, 0.63, 0.65, 0.67, 0.70]) {
        if (pCover >= thresh) {
          const k = 'pCover>=' + thresh.toFixed(2);
          if (!dogByPCover[k]) dogByPCover[k] = {w:0,l:0};
          if (covers) dogByPCover[k].w++; else dogByPCover[k].l++;
        }
      }
    } else {
      if (covers) buckets.FAV.w++; else buckets.FAV.l++;
      if (absLine <= 6) { if (covers) favByLine['fav 0-6'].w++; else favByLine['fav 0-6'].l++; }
      else if (absLine <= 10) { if (covers) favByLine['fav 6-10'].w++; else favByLine['fav 6-10'].l++; }
      else { if (covers) favByLine['fav 10+'].w++; else favByLine['fav 10+'].l++; }
    }
  }
}

console.log('=== CORRECTED ANALYSIS (pCover >= 0.60, sDiff 3-9) ===');
console.log('');
for (const [name, {w,l}] of Object.entries(buckets)) {
  const n = w+l;
  console.log(name.padEnd(10) + w + '-' + l + ' (' + (w/n*100).toFixed(1) + '%)  n=' + n);
}

console.log('');
console.log('=== DOGS by line size ===');
for (const [name, {w,l}] of Object.entries(dogByLine)) {
  const n = w+l;
  if (n === 0) continue;
  console.log(name.padEnd(15) + w + '-' + l + ' (' + (w/n*100).toFixed(1) + '%)  n=' + n);
}

console.log('');
console.log('=== FAVS by line size ===');
for (const [name, {w,l}] of Object.entries(favByLine)) {
  const n = w+l;
  if (n === 0) continue;
  console.log(name.padEnd(15) + w + '-' + l + ' (' + (w/n*100).toFixed(1) + '%)  n=' + n);
}

console.log('');
console.log('=== DOGS by dNET ===');
for (const [name, {w,l}] of Object.entries(dogByDNET)) {
  const n = w+l;
  if (n === 0) continue;
  console.log(name.padEnd(15) + w + '-' + l + ' (' + (w/n*100).toFixed(1) + '%)  n=' + n);
}

console.log('');
console.log('=== DOGS by pCover threshold ===');
for (const [k, {w,l}] of Object.entries(dogByPCover)) {
  const n = w+l;
  console.log(k.padEnd(18) + w + '-' + l + ' (' + (w/n*100).toFixed(1) + '%)  n=' + n);
}
