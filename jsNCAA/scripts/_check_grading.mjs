#!/usr/bin/env node
import fs from "fs";

const store = JSON.parse(fs.readFileSync("data/history.json", "utf8"));
const runs = store.runs || [];

// Pick a known game and trace the grading
// Michigan +22.5 vs USC: away=Michigan(66), home=USC... wait, let me find the actual record
for (const r of runs) {
  for (const g of r.games) {
    if (r.date === "20260102" && g.away && g.away.includes("Michigan") && g.home && g.home.includes("USC")) {
      console.log("Found game:", JSON.stringify(g, null, 2));
      console.log("\n--- Grading analysis ---");
      console.log("away:", g.away, "| home:", g.home);
      console.log("awayScore:", g.awayScore, "| homeScore:", g.homeScore);
      console.log("line:", g.line, "(negative = home favored, positive = home is dog)");

      const absLine = Math.abs(g.line);
      const homeDog = g.line > 0;
      console.log("homeDog:", homeDog, "| absLine:", absLine);

      if (homeDog) {
        // Home is getting points. Home covers if homeScore + line > awayScore
        // i.e. (homeScore - awayScore) + line > 0
        const margin = g.homeScore - g.awayScore;
        const v = margin + g.line;
        console.log("Home margin:", margin, "+ line:", g.line, "= spreadResult:", v);
        console.log("Home covers?", v > 0);
      } else {
        // Away is getting points. Away covers if awayScore + absLine > homeScore
        // i.e. (awayScore - homeScore) + absLine > 0
        // or: -(homeScore - awayScore) - line > 0 (since line < 0)
        // or: -(margin + line) > 0
        const margin = g.homeScore - g.awayScore;
        const v = margin + g.line;
        console.log("Home margin:", margin, "+ line:", g.line, "= v:", v);
        console.log("Away covers?", v < 0);
      }
    }
  }
}

// Also check: what does the line sign convention actually mean?
// Let's look at games where sPick was made and result is known
console.log("\n\n=== VERIFY WITH ACTUAL GRADED PICKS ===");
let checked = 0;
for (const r of runs) {
  for (const g of r.games) {
    if (checked >= 5) break;
    if (!g.sPick || g.sPick === "PASS") continue;
    if (!g.result) continue;
    console.log(`\n${r.date}: ${g.away} @ ${g.home}`);
    console.log(`  sPick: ${g.sPick} | result: ${g.result}`);
    console.log(`  line: ${g.line} | score: ${g.awayScore}-${g.homeScore}`);
    console.log(`  pHomeCover: ${g.pHomeCover} | pAwayCover: ${g.pAwayCover}`);
    checked++;
  }
}
