import fs from "fs";
const h = JSON.parse(fs.readFileSync("data/history.json", "utf8"));

console.log("=== PICK VOLUME (last 20 runs) ===");
const last20 = h.runs.slice(-20);
for (const r of last20) {
  const picks = r.games.filter(g => g.sPick && g.sPick !== "PASS");
  const total = r.games.length;
  const skipped = r.games.filter(g => g.status === "SKIPPED").length;
  console.log(`${r.date} | ${total} games | ${picks.length} spread picks | ${skipped} skipped`);
}

console.log("\n=== pCover DISTRIBUTION (all games with Kalman) ===");
const allPcovers = [];
for (const r of h.runs) {
  for (const g of r.games) {
    if (g.pCover) allPcovers.push(g.pCover);
  }
}

// Also check what pCover values the model is producing for PASS games
const passPcovers = [];
for (const r of h.runs.slice(-10)) {
  for (const g of r.games) {
    if (g.sPick === "PASS" && g.pHomeCover) {
      passPcovers.push({ date: r.date, game: `${g.away} @ ${g.home}`, best: Math.max(g.pHomeCover || 0, g.pAwayCover || 0) });
    }
  }
}

console.log(`Games with pCover (picked): ${allPcovers.length}`);
if (allPcovers.length) {
  allPcovers.sort((a, b) => a - b);
  console.log(`Min: ${allPcovers[0]}, Max: ${allPcovers[allPcovers.length - 1]}, Median: ${allPcovers[Math.floor(allPcovers.length / 2)]}`);
  const bins = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80];
  for (const t of bins) {
    const count = allPcovers.filter(p => p >= t).length;
    console.log(`  >= ${t}: ${count} picks`);
  }
}

console.log("\n=== SEASON RECORD ===");
let w = 0, l = 0, p = 0;
for (const r of h.runs) {
  for (const g of r.games) {
    if (g.sPick && g.sPick !== "PASS" && g.sResult) {
      if (g.sResult === "WIN") w++;
      else if (g.sResult === "LOSS") l++;
      else if (g.sResult === "PUSH") p++;
    }
  }
}
console.log(`Spread: ${w}-${l}-${p} (${(w / (w + l) * 100).toFixed(1)}%)`);

console.log("\n=== WHAT IF LOWER THRESHOLDS? ===");
// Check all games that have pHomeCover/pAwayCover stored
let gamesWithProbs = 0;
const threshResults = {};
for (const thresh of [0.55, 0.57, 0.60, 0.63, 0.65, 0.67, 0.70]) {
  threshResults[thresh] = { w: 0, l: 0, p: 0, total: 0 };
}

for (const r of h.runs) {
  for (const g of r.games) {
    if (g.status === "SKIPPED" || g.status === "MISSING_ODDS") continue;
    if (!g.pHomeCover && !g.pAwayCover) continue;
    gamesWithProbs++;
    const bestP = Math.max(g.pHomeCover || 0, g.pAwayCover || 0);
    const sDiff = g.sDiff || 0;
    const absLine = Math.abs(g.line || 0);
    if (sDiff < 3 || sDiff > 12 || absLine === 0) continue;

    // Determine which side has best pCover
    const side = (g.pHomeCover || 0) >= (g.pAwayCover || 0) ? "home" : "away";
    const isDog = side === "home" ? g.line > 0 : g.line < 0;
    const lineOK = isDog ? true : absLine <= 6;
    if (!lineOK) continue;

    // Check result
    const actualMargin = (g.homeScore - g.awayScore);
    const spread = g.line; // negative = home fav, positive = home dog
    const coverMargin = actualMargin + spread;
    let result;
    if (side === "home") {
      result = coverMargin > 0 ? "WIN" : coverMargin === 0 ? "PUSH" : "LOSS";
    } else {
      result = coverMargin < 0 ? "WIN" : coverMargin === 0 ? "PUSH" : "LOSS";
    }

    if (!Number.isFinite(g.homeScore)) continue;

    for (const thresh of Object.keys(threshResults)) {
      if (bestP >= parseFloat(thresh)) {
        threshResults[thresh].total++;
        if (result === "WIN") threshResults[thresh].w++;
        else if (result === "LOSS") threshResults[thresh].l++;
        else threshResults[thresh].p++;
      }
    }
  }
}

console.log(`Games with probability data: ${gamesWithProbs}`);
for (const [thresh, r] of Object.entries(threshResults)) {
  const pct = r.w + r.l > 0 ? (r.w / (r.w + r.l) * 100).toFixed(1) : "n/a";
  const units = r.w - r.l * 1.1;
  console.log(`  >= ${thresh}: ${r.w}-${r.l}-${r.p} (${pct}%) ${r.total} games, ${units.toFixed(1)}u`);
}
