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

function gradeSpread(g, side) {
  const margin = g.homeScore - g.awayScore;
  const v = margin + g.line;
  if (side === "home") return v > 0 ? "WIN" : v === 0 ? "PUSH" : "LOSS";
  else return v < 0 ? "WIN" : v === 0 ? "PUSH" : "LOSS";
}

// Check line distribution of "dog" picks
console.log("=== LINE DISTRIBUTION OF DOG PICKS (pCover >= 0.60) ===");
const buckets = { "0": 0, "0.5-1": 0, "1.5-2.5": 0, "3-5": 0, "5.5-7": 0, "7+": 0 };
const bucketWL = { "0": [0,0], "0.5-1": [0,0], "1.5-2.5": [0,0], "3-5": [0,0], "5.5-7": [0,0], "7+": [0,0] };

for (const g of games) {
  const absLine = Math.abs(g.line);
  const homeDog = g.line > 0;
  const awayDog = g.line < 0;
  if (!homeDog && !awayDog) continue; // line = 0, skip

  const dogPCover = homeDog ? (g.pHomeCover || 0) : (g.pAwayCover || 0);
  const dogSide = homeDog ? "home" : "away";

  if (dogPCover < 0.60) continue;

  let bucket;
  if (absLine === 0) bucket = "0";
  else if (absLine <= 1) bucket = "0.5-1";
  else if (absLine <= 2.5) bucket = "1.5-2.5";
  else if (absLine <= 5) bucket = "3-5";
  else if (absLine <= 7) bucket = "5.5-7";
  else bucket = "7+";

  buckets[bucket]++;
  const result = gradeSpread(g, dogSide);
  if (result === "WIN") bucketWL[bucket][0]++;
  else if (result === "LOSS") bucketWL[bucket][1]++;
}

for (const [b, count] of Object.entries(buckets)) {
  const [w, l] = bucketWL[b];
  const pct = (w + l) > 0 ? (w / (w + l) * 100).toFixed(1) : "n/a";
  console.log(`  Line ${b.padEnd(8)}: ${count} picks | ${w}-${l} (${pct}%)`);
}

// Now sweep with minimum line requirement
console.log("\n=== DOGS ONLY — with minimum line floor ===");
console.log("pCover | minLine | W    - L   - P | Pct%  | Picks/Day | Units");
console.log("-------+---------+----------------+-------+-----------+------");

for (const pThresh of [0.55, 0.57, 0.58, 0.60, 0.62, 0.65]) {
  for (const minLine of [0.5, 1.5, 2.5, 3.5]) {
    let w = 0, l = 0, p = 0;
    for (const g of games) {
      const absLine = Math.abs(g.line);
      if (absLine < minLine) continue;

      const homeDog = g.line > 0;
      const awayDog = g.line < 0;
      if (!homeDog && !awayDog) continue;

      const dogPCover = homeDog ? (g.pHomeCover || 0) : (g.pAwayCover || 0);
      const dogSide = homeDog ? "home" : "away";

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

// How many "dogs" had line = 0?
const zeroLine = games.filter(g => g.line === 0).length;
const totalG = games.length;
console.log(`\nGames with line = 0: ${zeroLine} out of ${totalG}`);

// Check: how many times does pHomeCover > 0.50 when line > 0 (home is dog)?
// This tells us if the model systematically thinks dogs cover
let dogFavored = 0, dogNotFavored = 0;
for (const g of games) {
  const homeDog = g.line > 0;
  const awayDog = g.line < 0;
  if (!homeDog && !awayDog) continue;
  const dogP = homeDog ? (g.pHomeCover || 0) : (g.pAwayCover || 0);
  if (dogP >= 0.50) dogFavored++;
  else dogNotFavored++;
}
console.log(`Dog pCover >= 0.50: ${dogFavored} | Dog pCover < 0.50: ${dogNotFavored}`);
console.log(`Model thinks dog covers ${(dogFavored/(dogFavored+dogNotFavored)*100).toFixed(1)}% of the time`);
