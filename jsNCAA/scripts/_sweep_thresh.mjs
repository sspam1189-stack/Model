#!/usr/bin/env node
import fs from "fs";

const store = JSON.parse(fs.readFileSync("data/history.json", "utf8"));
const runs = store.runs || [];

const games = [];
for (const r of runs) {
  for (const g of r.games) {
    if (g.homeScore === undefined || g.awayScore === undefined) continue;
    if (!g.line && g.line !== 0) continue;
    if (g.status === "SKIPPED" || g.status === "MISSING_STATS") continue;
    if (!g.pHomeCover && !g.pAwayCover) continue;
    games.push(g);
  }
}

const totalDays = new Set(runs.map(r => r.date)).size;

// LINE CONVENTION: line > 0 = home FAV, line < 0 = away FAV
// So: home is DOG when line < 0, away is DOG when line > 0

function gradeSpread(g, side) {
  // margin_mean = hS - aS - line => home covers when margin + line > ...
  // Actually: home covers spread when homeScore - awayScore > line (for fav)
  // or homeScore - awayScore + |line| > 0 (for dog getting points)
  // With convention line > 0 = home fav:
  //   Home pick covers: (homeScore - awayScore) - line > 0 (if fav, needs to win by more than line)
  //   Wait, need to think about this more carefully.
  //
  // Standard: if home is -7 fav (our line=7), home covers if they win by > 7
  // homeMargin = homeScore - awayScore
  // home covers: homeMargin > line (when line > 0, home fav)
  // home covers: homeMargin - line > 0
  //
  // If home is +7 dog (our line=-7), home covers if they lose by < 7 or win
  // home covers: homeMargin > line => homeMargin > -7 => homeMargin + 7 > 0
  // Same formula: homeMargin - line > 0

  const homeMargin = g.homeScore - g.awayScore;
  const v = homeMargin - g.line; // positive = home covers

  if (side === "home") {
    return v > 0 ? "WIN" : v === 0 ? "PUSH" : "LOSS";
  } else {
    return v < 0 ? "WIN" : v === 0 ? "PUSH" : "LOSS";
  }
}

// SANITY CHECK: all dogs should be ~50%
let dogW = 0, dogL = 0;
for (const g of games) {
  if (g.line === 0) continue;
  // home is dog when line < 0, away is dog when line > 0
  const dogSide = g.line < 0 ? "home" : "away";
  const r = gradeSpread(g, dogSide);
  if (r === "WIN") dogW++;
  else if (r === "LOSS") dogL++;
}
console.log(`SANITY: ALL dogs ATS = ${dogW}-${dogL} (${(dogW/(dogW+dogL)*100).toFixed(1)}%) — should be ~50%`);
console.log(`Total games: ${games.length}, Total days: ${totalDays}\n`);

// === ALL SIDES ===
console.log("=== ALL SIDES (best pCover side, fav line cap 6) ===");
console.log("pCover | sDiffMin | W    - L   - P | Pct%  | Picks/Day | Units");
console.log("-------+----------+----------------+-------+-----------+------");

for (const pThresh of [0.55, 0.58, 0.60, 0.62, 0.63, 0.65, 0.67, 0.70]) {
  for (const sdMin of [0, 3]) {
    let w = 0, l = 0, p = 0;
    for (const g of games) {
      const pH = g.pHomeCover || 0;
      const pA = g.pAwayCover || 0;
      const bestP = Math.max(pH, pA);
      const side = pH >= pA ? "home" : "away";
      const absLine = Math.abs(g.line);
      const sd = g.sDiff || 0;

      // Is picked side the dog?
      // home is dog when line < 0, away is dog when line > 0
      const isDog = side === "home" ? g.line < 0 : g.line > 0;
      const lineOK = isDog ? true : absLine <= 6;

      if (bestP >= pThresh && sd >= sdMin && sd <= 12 && lineOK && absLine > 0) {
        const result = gradeSpread(g, side);
        if (result === "WIN") w++;
        else if (result === "LOSS") l++;
        else p++;
      }
    }
    const total = w + l;
    const pct = total > 0 ? (w / total * 100).toFixed(1) : "n/a";
    const units = (w * 1 - l * 1.1).toFixed(1);
    const ppd = ((w + l + p) / totalDays).toFixed(1);
    console.log(`  ${pThresh.toFixed(2)}  |    ${sdMin}     | ${String(w).padStart(4)}-${String(l).padStart(4)}-${String(p).padStart(2)} | ${pct.padStart(5)} | ${ppd.padStart(9)} | ${units.padStart(6)}`);
  }
}

// === DOGS ONLY ===
console.log("\n=== DOGS ONLY (dog's pCover directly) ===");
console.log("pCover | minLine | W    - L   - P | Pct%  | Picks/Day | Units");
console.log("-------+---------+----------------+-------+-----------+------");

for (const pThresh of [0.50, 0.52, 0.53, 0.55, 0.57, 0.58, 0.60, 0.62, 0.65]) {
  for (const minLine of [0.5, 2.5, 3.5]) {
    let w = 0, l = 0, p = 0;
    for (const g of games) {
      const absLine = Math.abs(g.line);
      if (absLine < minLine) continue;

      // home is dog when line < 0, away is dog when line > 0
      const homeDog = g.line < 0;
      const awayDog = g.line > 0;
      if (!homeDog && !awayDog) continue;

      const dogSide = homeDog ? "home" : "away";
      const dogPCover = homeDog ? (g.pHomeCover || 0) : (g.pAwayCover || 0);

      if (dogPCover >= pThresh) {
        const result = gradeSpread(g, dogSide);
        if (result === "WIN") w++;
        else if (result === "LOSS") l++;
        else p++;
      }
    }
    const total = w + l;
    const pct = total > 0 ? (w / total * 100).toFixed(1) : "n/a";
    const units = (w * 1 - l * 1.1).toFixed(1);
    const ppd = ((w + l + p) / totalDays).toFixed(1);
    console.log(`  ${pThresh.toFixed(2)}  |  ${minLine.toFixed(1).padStart(4)} | ${String(w).padStart(4)}-${String(l).padStart(4)}-${String(p).padStart(2)} | ${pct.padStart(5)} | ${ppd.padStart(9)} | ${units.padStart(6)}`);
  }
}
