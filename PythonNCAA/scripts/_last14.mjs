import fs from "fs";
const h = JSON.parse(fs.readFileSync("data/history.json", "utf8"));
const runs = (h.runs || []).filter(r => r.burnIn !== true);
const last14 = runs.slice(-14);

let sw = 0, sl = 0, tw = 0, tl = 0;
for (const r of last14) {
  console.log(`${r.date}: ${(r.games || []).length} games`);
  for (const g of r.games || []) {
    if (g.sResult === "WIN") sw++;
    else if (g.sResult === "LOSS") sl++;
    if (g.oResult === "WIN") tw++;
    else if (g.oResult === "LOSS") tl++;
  }
}
console.log(`\nLAST 14 DAYS:`);
console.log(`Spreads: ${sw}-${sl} (${(sw / (sw + sl) * 100).toFixed(1)}%)  ${sw + sl} picks`);
console.log(`Totals:  ${tw}-${tl} (${(tw / (tw + tl) * 100).toFixed(1)}%)  ${tw + tl} picks`);
console.log(`Combined: ${sw + tw}-${sl + tl} (${((sw + tw) / (sw + tw + sl + tl) * 100).toFixed(1)}%)`);
