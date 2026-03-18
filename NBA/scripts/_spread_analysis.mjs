import fs from "fs";
const h = JSON.parse(fs.readFileSync("data/history.json", "utf8"));
const buckets = { "0-5": [], "5-10": [], "10-15": [], "15+": [] };

for (const r of h.runs || []) {
  if (r.burnIn) continue;
  for (const g of r.games || []) {
    if (g.sResult !== "WIN" && g.sResult !== "LOSS") continue;
    const line = Math.abs(g.line || 0);
    const w = g.sResult === "WIN" ? 1 : 0;
    if (line <= 5) buckets["0-5"].push(w);
    else if (line <= 10) buckets["5-10"].push(w);
    else if (line <= 15) buckets["10-15"].push(w);
    else buckets["15+"].push(w);
  }
}

console.log("\n=== Win Rate by Spread Size ===\n");
for (const [k, v] of Object.entries(buckets)) {
  const wins = v.filter(x => x).length;
  const t = v.length;
  console.log(`  ${k}: ${wins}-${t - wins} (${t ? (wins / t * 100).toFixed(1) : 0}%)`);
}
