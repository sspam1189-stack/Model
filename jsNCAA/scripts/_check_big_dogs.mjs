#!/usr/bin/env node
import fs from "fs";

const store = JSON.parse(fs.readFileSync("data/history.json", "utf8"));
const runs = store.runs || [];

// Check: for 7+ point dogs with pCover >= 0.60, show the actual games
let wins = 0, losses = 0;
const lossList = [];

for (const r of runs) {
  for (const g of r.games) {
    if (g.homeScore === undefined || g.awayScore === undefined) continue;
    if (g.status === "SKIPPED" || g.status === "MISSING_STATS") continue;
    const absLine = Math.abs(g.line);
    if (absLine < 7) continue;

    const homeDog = g.line > 0;
    if (!homeDog && g.line >= 0) continue; // line=0, skip
    const awayDog = g.line < 0;

    const dogSide = homeDog ? "home" : "away";
    const dogP = homeDog ? (g.pHomeCover || 0) : (g.pAwayCover || 0);
    if (dogP < 0.60) continue;

    const margin = g.homeScore - g.awayScore;
    const v = margin + g.line;
    const covered = dogSide === "home" ? v > 0 : v < 0;

    if (covered) {
      wins++;
    } else {
      losses++;
      lossList.push({
        date: r.date,
        game: `${g.away} @ ${g.home}`,
        line: g.line,
        score: `${g.awayScore}-${g.homeScore}`,
        dogP: dogP.toFixed(3),
        margin: margin,
        spreadResult: v
      });
    }
  }
}

console.log(`7+ point dogs with pCover >= 0.60: ${wins}-${losses}`);
console.log(`\nLosses:`);
for (const l of lossList) {
  console.log(`  ${l.date} | ${l.game} | line: ${l.line} | score: ${l.score} | dogP: ${l.dogP} | margin: ${l.margin} | spread result: ${l.spreadResult}`);
}

// Now check: what % of ALL 7+ point dogs cover (regardless of model)?
let allBigDogW = 0, allBigDogL = 0;
for (const r of runs) {
  for (const g of r.games) {
    if (g.homeScore === undefined || g.awayScore === undefined) continue;
    if (g.status === "SKIPPED" || g.status === "MISSING_STATS") continue;
    const absLine = Math.abs(g.line);
    if (absLine < 7) continue;

    const homeDog = g.line > 0;
    if (!homeDog && g.line >= 0) continue;
    const dogSide = homeDog ? "home" : "away";

    const margin = g.homeScore - g.awayScore;
    const v = margin + g.line;
    const covered = dogSide === "home" ? v > 0 : v < 0;
    if (covered) allBigDogW++;
    else allBigDogL++;
  }
}

console.log(`\nALL 7+ point dogs (no pCover filter): ${allBigDogW}-${allBigDogL} (${(allBigDogW/(allBigDogW+allBigDogL)*100).toFixed(1)}%)`);

// Check a few sample games to verify the grading
console.log("\n=== SAMPLE 7+ pt dog wins (first 10) ===");
let shown = 0;
for (const r of runs) {
  if (shown >= 10) break;
  for (const g of r.games) {
    if (shown >= 10) break;
    if (g.homeScore === undefined || g.awayScore === undefined) continue;
    if (g.status === "SKIPPED" || g.status === "MISSING_STATS") continue;
    const absLine = Math.abs(g.line);
    if (absLine < 7) continue;
    const homeDog = g.line > 0;
    if (!homeDog && g.line >= 0) continue;
    const dogSide = homeDog ? "home" : "away";
    const dogP = homeDog ? (g.pHomeCover || 0) : (g.pAwayCover || 0);
    if (dogP < 0.60) continue;

    const margin = g.homeScore - g.awayScore;
    const v = margin + g.line;
    const covered = dogSide === "home" ? v > 0 : v < 0;
    if (!covered) continue;

    const dogTeam = dogSide === "home" ? g.home : g.away;
    const favTeam = dogSide === "home" ? g.away : g.home;
    console.log(`  ${r.date} | ${dogTeam} +${absLine} vs ${favTeam} | ${g.awayScore}-${g.homeScore} | dogP=${dogP.toFixed(3)} | spreadResult=${v.toFixed(1)}`);
    shown++;
  }
}
