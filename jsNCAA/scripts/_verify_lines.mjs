#!/usr/bin/env node
import fs from "fs";

const store = JSON.parse(fs.readFileSync("data/history.json", "utf8"));
const runs = store.runs || [];

// Find games with big lines and check if the sign convention makes sense
console.log("=== Games with |line| > 15 (first 20) ===");
let count = 0;
for (const r of runs) {
  for (const g of r.games) {
    if (count >= 20) break;
    const absLine = Math.abs(g.line);
    if (absLine < 15) continue;
    if (g.homeScore === undefined) continue;

    const margin = g.homeScore - g.awayScore;
    console.log(`${r.date} | ${g.away} @ ${g.home} | line=${g.line} | score: away=${g.awayScore} home=${g.homeScore} | homeMargin=${margin}`);

    // If line > 0 (home dog), the favorite is away.
    // Favorite should usually win by more than the line. Check this.
    if (g.line > 0) {
      // Home is dog. Away is fav. Away "should" win.
      // If away won: margin < 0 (home lost)
      // Dog covers if |margin| < line, i.e. they didn't lose by more than the spread
      // Or dog won outright
      const dogCovered = margin + g.line > 0;
      console.log(`  Home is ${g.line} pt dog. Away won by ${-margin}. Dog covered? ${dogCovered}`);
    } else {
      // Away is dog. Home is fav.
      const dogCovered = -(margin + g.line) > 0;
      console.log(`  Away is ${absLine} pt dog. Home won by ${margin}. Dog covered? ${dogCovered}`);
    }
    count++;
  }
}

// KEY CHECK: what % of ALL dogs cover in NCAA? Should be ~50%
let totalDogW = 0, totalDogL = 0;
for (const r of runs) {
  for (const g of r.games) {
    if (g.homeScore === undefined || g.awayScore === undefined) continue;
    if (g.status === "SKIPPED" || g.status === "MISSING_STATS") continue;
    if (!g.line || g.line === 0) continue;

    const margin = g.homeScore - g.awayScore;
    const v = margin + g.line;

    // Dog is home when line > 0, away when line < 0
    if (g.line > 0) {
      // home is dog, covers when v > 0
      if (v > 0) totalDogW++; else if (v < 0) totalDogL++;
    } else {
      // away is dog, covers when v < 0
      if (v < 0) totalDogW++; else if (v > 0) totalDogL++;
    }
  }
}

console.log(`\n=== ALL dogs ATS (no model filter): ${totalDogW}-${totalDogL} (${(totalDogW/(totalDogW+totalDogL)*100).toFixed(1)}%) ===`);
console.log("(Should be ~50% if lines/grading are correct)");
