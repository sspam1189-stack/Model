import fs from "fs";
const h = JSON.parse(fs.readFileSync("data/history.json", "utf8"));

let ow = 0, ol = 0, op = 0;
let overW = 0, overL = 0, underW = 0, underL = 0;
const diffBuckets = { "0-2": [], "2-4": [], "4-6": [], "6-8": [], "8+": [] };
const pOUBuckets = { "0.50-0.55": [], "0.55-0.60": [], "0.60-0.65": [], "0.65-0.70": [], "0.70+": [] };

let totalPicks = 0, totalPass = 0;

for (const r of h.runs || []) {
  if (r.burnIn) continue;
  for (const g of r.games || []) {
    if (g.oPick === "PASS" || !g.oPick) { totalPass++; continue; }
    totalPicks++;

    if (!g.oResult) continue;
    if (g.oResult === "PUSH") { op++; continue; }

    const v = g.oResult === "WIN" ? 1 : 0;
    if (g.oResult === "WIN") ow++;
    else ol++;

    if (g.oPick === "OVER") { if (v) overW++; else overL++; }
    else if (g.oPick === "UNDER") { if (v) underW++; else underL++; }

    const td = Math.abs(g.tDiff || g.cleanTDiff || 0);
    if (td <= 2) diffBuckets["0-2"].push(v);
    else if (td <= 4) diffBuckets["2-4"].push(v);
    else if (td <= 6) diffBuckets["4-6"].push(v);
    else if (td <= 8) diffBuckets["6-8"].push(v);
    else diffBuckets["8+"].push(v);

    const pou = g.pOU || 0;
    if (pou < 0.55) pOUBuckets["0.50-0.55"].push(v);
    else if (pou < 0.60) pOUBuckets["0.55-0.60"].push(v);
    else if (pou < 0.65) pOUBuckets["0.60-0.65"].push(v);
    else if (pou < 0.70) pOUBuckets["0.65-0.70"].push(v);
    else pOUBuckets["0.70+"].push(v);
  }
}

console.log(`TOTALS OVERALL: ${ow}-${ol}-${op} (${(ow / (ow + ol) * 100).toFixed(1)}%)`);
console.log(`Total picks made: ${totalPicks} / Passed: ${totalPass}`);
console.log(`Units (flat -110): ${(ow - ol * 1.1).toFixed(1)}u`);
console.log();
if (overW + overL > 0) console.log(`OVER picks:  ${overW}-${overL} (${(overW / (overW + overL) * 100).toFixed(1)}%)`);
if (underW + underL > 0) console.log(`UNDER picks: ${underW}-${underL} (${(underW / (underW + underL) * 100).toFixed(1)}%)`);

console.log("\nBy total diff:");
for (const [k, v] of Object.entries(diffBuckets)) {
  const wins = v.filter(x => x).length;
  const t = v.length;
  if (t > 0) console.log(`  ${k}: ${wins}-${t - wins} (${(wins / t * 100).toFixed(1)}%)`);
}

console.log("\nBy pOU:");
for (const [k, v] of Object.entries(pOUBuckets)) {
  const wins = v.filter(x => x).length;
  const t = v.length;
  if (t > 0) console.log(`  ${k}: ${wins}-${t - wins} (${(wins / t * 100).toFixed(1)}%)`);
}

// Over vs Under bias check
let overCount = 0, underCount = 0;
for (const r of h.runs || []) {
  if (r.burnIn) continue;
  for (const g of r.games || []) {
    if (g.oPick === "OVER") overCount++;
    else if (g.oPick === "UNDER") underCount++;
  }
}
console.log(`\nPick bias: ${overCount} OVER / ${underCount} UNDER`);
