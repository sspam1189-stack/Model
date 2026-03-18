#!/usr/bin/env node
import fs from "fs";

const store = JSON.parse(fs.readFileSync("data/history.json", "utf8"));
const runs = store.runs || [];

// Last 2 weeks = dates >= 20260303
const cutoff = "20260303";
const recentRuns = runs.filter(r => r.date >= cutoff);
const olderRuns = runs.filter(r => r.date < cutoff);

console.log(`Recent runs (>= ${cutoff}): ${recentRuns.length} days`);
console.log(`Older runs (< ${cutoff}): ${olderRuns.length} days\n`);

function gradeSpread(g, side) {
  const homeMargin = g.homeScore - g.awayScore;
  const v = homeMargin - g.line;
  if (side === "home") return v > 0 ? "WIN" : v === 0 ? "PUSH" : "LOSS";
  else return v < 0 ? "WIN" : v === 0 ? "PUSH" : "LOSS";
}

function sweep(runSet, label) {
  const games = [];
  for (const r of runSet) {
    for (const g of r.games) {
      if (g.homeScore === undefined || g.awayScore === undefined) continue;
      if (!g.line && g.line !== 0) continue;
      if (g.status === "SKIPPED" || g.status === "MISSING_STATS") continue;
      if (!g.pHomeCover && !g.pAwayCover) continue;
      games.push(g);
    }
  }

  console.log(`=== ${label} (${games.length} games) ===`);
  console.log("pCover | W    - L   - P | Pct%  | Picks/Day | Units");
  console.log("-------+----------------+-------+-----------+------");

  const days = runSet.length || 1;

  for (const pThresh of [0.55, 0.58, 0.60, 0.62, 0.65, 0.67]) {
    let w = 0, l = 0, p = 0;
    for (const g of games) {
      const pH = g.pHomeCover || 0;
      const pA = g.pAwayCover || 0;
      const bestP = Math.max(pH, pA);
      const side = pH >= pA ? "home" : "away";
      const absLine = Math.abs(g.line);

      // Dog check for fav line cap
      const isDog = side === "home" ? g.line < 0 : g.line > 0;
      const lineOK = isDog ? true : absLine <= 6;

      if (bestP >= pThresh && lineOK && absLine > 0) {
        const result = gradeSpread(g, side);
        if (result === "WIN") w++;
        else if (result === "LOSS") l++;
        else p++;
      }
    }
    const total = w + l;
    const pct = total > 0 ? (w / total * 100).toFixed(1) : "n/a";
    const units = (w * 1 - l * 1.1).toFixed(1);
    const ppd = ((w + l + p) / days).toFixed(1);
    console.log(`  ${pThresh.toFixed(2)}  | ${String(w).padStart(4)}-${String(l).padStart(4)}-${String(p).padStart(2)} | ${pct.padStart(5)} | ${ppd.padStart(9)} | ${units.padStart(6)}`);
  }
  console.log();
}

sweep(recentRuns, "LAST 2 WEEKS");
sweep(olderRuns, "BEFORE LAST 2 WEEKS");
sweep(runs, "FULL SEASON");
