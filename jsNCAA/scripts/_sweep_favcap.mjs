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

function gradeSpread(g, side) {
  const homeMargin = g.homeScore - g.awayScore;
  const v = homeMargin + g.line;
  if (side === "home") return v > 0 ? "WIN" : v === 0 ? "PUSH" : "LOSS";
  else return v < 0 ? "WIN" : v === 0 ? "PUSH" : "LOSS";
}

const totalDays = new Set(runs.map(r => r.date)).size;

// Break down fav picks by line size at pCover >= 0.60
console.log("=== FAV PICKS BY LINE SIZE (pCover >= 0.60) ===");
const buckets = [[0.5,3],[3.5,5],[5.5,6],[6.5,8],[8.5,10],[10.5,15],[15.5,30]];
for (const [lo, hi] of buckets) {
  let w = 0, l = 0;
  for (const g of games) {
    const pH = g.pHomeCover || 0;
    const pA = g.pAwayCover || 0;
    const bestP = Math.max(pH, pA);
    const side = pH >= pA ? "home" : "away";
    const absLine = Math.abs(g.line);
    // Is picked side the fav?
    const isDog = side === "home" ? g.line > 0 : g.line < 0;
    if (isDog) continue; // only favs
    if (bestP < 0.60) continue;
    if (absLine < lo || absLine > hi) continue;

    const result = gradeSpread(g, side);
    if (result === "WIN") w++;
    else if (result === "LOSS") l++;
  }
  const total = w + l;
  const pct = total > 0 ? (w / total * 100).toFixed(1) : "n/a";
  console.log(`  Line ${lo}-${hi}: ${w}-${l} (${pct}%) | ${total} picks`);
}

// Sweep fav cap options
console.log("\n=== ALL SIDES @ pCover >= 0.60 with different fav caps ===");
console.log("FavCap | W    - L   | Pct%  | Picks/Day | Units");
console.log("-------+------------+-------+-----------+------");

for (const cap of [4, 5, 6, 8, 10, 15, 99]) {
  let w = 0, l = 0, p = 0;
  for (const g of games) {
    const pH = g.pHomeCover || 0;
    const pA = g.pAwayCover || 0;
    const bestP = Math.max(pH, pA);
    const side = pH >= pA ? "home" : "away";
    const absLine = Math.abs(g.line);
    const isDog = side === "home" ? g.line > 0 : g.line < 0;
    const lineOK = isDog ? true : absLine <= cap;

    if (bestP >= 0.60 && lineOK && absLine > 0) {
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
  const label = cap === 99 ? "none" : String(cap);
  console.log(`  ${label.padStart(4)}  | ${String(w).padStart(4)}-${String(l).padStart(4)} | ${pct.padStart(5)} | ${ppd.padStart(9)} | ${units.padStart(6)}`);
}
