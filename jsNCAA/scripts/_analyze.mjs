import fs from "fs";
const h = JSON.parse(fs.readFileSync("data/history.json", "utf8"));

let w = 0, l = 0, p = 0;
for (const r of h.runs || []) {
  if (r.burnIn) continue;
  for (const g of r.games || []) {
    if (g.sResult === "WIN") w++;
    else if (g.sResult === "LOSS") l++;
    else if (g.sResult === "PUSH") p++;
  }
}
console.log(`Overall: ${w}-${l}-${p} (${(w / (w + l) * 100).toFixed(1)}%)`);

// Last 14 days
const recent = (h.runs || []).filter(r => r.burnIn !== true).slice(-14);
let rw = 0, rl = 0;
for (const r of recent) {
  for (const g of r.games || []) {
    if (g.sResult === "WIN") rw++;
    else if (g.sResult === "LOSS") rl++;
  }
}
console.log(`Last 14 days: ${rw}-${rl} (${(rw / (rw + rl) * 100).toFixed(1)}%)`);

// By spread size
console.log("\nBy spread size:");
const sBuckets = { "0-5": [], "5-10": [], "10-15": [], "15-20": [], "20+": [] };
for (const r of h.runs || []) {
  if (r.burnIn) continue;
  for (const g of r.games || []) {
    if (g.sResult !== "WIN" && g.sResult !== "LOSS") continue;
    const line = Math.abs(g.line || 0);
    const v = g.sResult === "WIN" ? 1 : 0;
    if (line <= 5) sBuckets["0-5"].push(v);
    else if (line <= 10) sBuckets["5-10"].push(v);
    else if (line <= 15) sBuckets["10-15"].push(v);
    else if (line <= 20) sBuckets["15-20"].push(v);
    else sBuckets["20+"].push(v);
  }
}
for (const [k, v] of Object.entries(sBuckets)) {
  const wins = v.filter(x => x).length;
  const t = v.length;
  if (t > 0) console.log(`  ${k}: ${wins}-${t - wins} (${(wins / t * 100).toFixed(1)}%)`);
}

// By sDiff
console.log("\nBy sDiff:");
const sdBuckets = { "0-3": [], "3-6": [], "6-9": [], "9-12": [], "12+": [] };
for (const r of h.runs || []) {
  if (r.burnIn) continue;
  for (const g of r.games || []) {
    if (g.sResult !== "WIN" && g.sResult !== "LOSS") continue;
    const sd = Math.abs(g.sDiff || 0);
    const v = g.sResult === "WIN" ? 1 : 0;
    if (sd <= 3) sdBuckets["0-3"].push(v);
    else if (sd <= 6) sdBuckets["3-6"].push(v);
    else if (sd <= 9) sdBuckets["6-9"].push(v);
    else if (sd <= 12) sdBuckets["9-12"].push(v);
    else sdBuckets["12+"].push(v);
  }
}
for (const [k, v] of Object.entries(sdBuckets)) {
  const wins = v.filter(x => x).length;
  const t = v.length;
  if (t > 0) console.log(`  ${k}: ${wins}-${t - wins} (${(wins / t * 100).toFixed(1)}%)`);
}

// Fav vs dog
let favW = 0, favL = 0, dogW = 0, dogL = 0;
for (const r of h.runs || []) {
  if (r.burnIn) continue;
  for (const g of r.games || []) {
    if (g.sResult !== "WIN" && g.sResult !== "LOSS") continue;
    const pick = g.sPick || "";
    const isFav = pick.includes("-");
    const v = g.sResult === "WIN" ? 1 : 0;
    if (isFav) { favW += v; favL += (1 - v); }
    else { dogW += v; dogL += (1 - v); }
  }
}
console.log(`\nFavorites: ${favW}-${favL} (${(favW / (favW + favL) * 100).toFixed(1)}%)`);
console.log(`Underdogs: ${dogW}-${dogL} (${(dogW / (dogW + dogL) * 100).toFixed(1)}%)`);

// pCover buckets
console.log("\nBy pCover:");
const pcBuckets = { "0.50-0.57": [], "0.57-0.60": [], "0.60-0.65": [], "0.65-0.70": [], "0.70+": [] };
for (const r of h.runs || []) {
  if (r.burnIn) continue;
  for (const g of r.games || []) {
    if (g.sResult !== "WIN" && g.sResult !== "LOSS") continue;
    const pc = g.pCover || 0;
    const v = g.sResult === "WIN" ? 1 : 0;
    if (pc < 0.57) pcBuckets["0.50-0.57"].push(v);
    else if (pc < 0.60) pcBuckets["0.57-0.60"].push(v);
    else if (pc < 0.65) pcBuckets["0.60-0.65"].push(v);
    else if (pc < 0.70) pcBuckets["0.65-0.70"].push(v);
    else pcBuckets["0.70+"].push(v);
  }
}
for (const [k, v] of Object.entries(pcBuckets)) {
  const wins = v.filter(x => x).length;
  const t = v.length;
  if (t > 0) console.log(`  ${k}: ${wins}-${t - wins} (${(wins / t * 100).toFixed(1)}%)`);
}

// ResidualVar
const errors = [];
for (const r of h.runs || []) {
  if (r.burnIn) continue;
  for (const g of r.games || []) {
    if (typeof g.homeScore !== "number" || typeof g.awayScore !== "number") continue;
    if (typeof g.margin !== "number" || typeof g.line !== "number") continue;
    const err = (g.homeScore - g.awayScore) - g.line - g.margin;
    errors.push(err);
  }
}
const mean = errors.reduce((s, x) => s + x, 0) / errors.length;
const variance = errors.reduce((s, x) => s + (x - mean) ** 2, 0) / errors.length;
console.log(`\nResidualVar: ${variance.toFixed(1)} (std=${Math.sqrt(variance).toFixed(1)}) from ${errors.length} games`);

// Current weights
const wt = h.weights || {};
console.log("\nCurrent weights:");
for (const [k, v] of Object.entries(wt)) {
  if (typeof v === "number") console.log(`  ${k}: ${v.toFixed(3)}`);
}
