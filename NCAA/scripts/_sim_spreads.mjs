import fs from "fs";
const h = JSON.parse(fs.readFileSync("data/history.json", "utf8"));

const picks = [];
for (const r of h.runs || []) {
  if (r.burnIn) continue;
  for (const g of r.games || []) {
    if (!g.sPick || g.sPick === "PASS") continue;
    if (!g.sResult || g.sResult === "PUSH") continue;
    picks.push({
      result: g.sResult === "WIN" ? 1 : 0,
      pCover: g.pCover || 0,
      sDiff: Math.abs(g.sDiff || 0),
      line: Math.abs(g.line || 0),
      conf: g.sConf || "low",
      isFav: (g.sPick || "").includes("-"),
    });
  }
}

console.log(`Total graded spread picks: ${picks.length}\n`);

function test(label, filterFn) {
  const filtered = picks.filter(filterFn);
  const w = filtered.filter(p => p.result).length;
  const l = filtered.length - w;
  const pct = filtered.length > 0 ? (w / filtered.length * 100).toFixed(1) : "n/a";
  const units = (w - l * 1.1).toFixed(1);
  const roi = filtered.length > 0 ? (parseFloat(units) / filtered.length * 100).toFixed(1) : "n/a";
  console.log(`${label.padEnd(50)} ${String(w + "-" + l).padEnd(10)} ${pct}%  ${units}u  ROI: ${roi}%`);
}

console.log("═══ Current (baseline) ═══");
test("ALL picks", p => true);

console.log("\n═══ By pCover ═══");
test("pCover >= 0.58", p => p.pCover >= 0.58);
test("pCover >= 0.60", p => p.pCover >= 0.60);
test("pCover >= 0.62", p => p.pCover >= 0.62);
test("pCover >= 0.63", p => p.pCover >= 0.63);
test("pCover >= 0.65", p => p.pCover >= 0.65);
test("pCover >= 0.67", p => p.pCover >= 0.67);
test("pCover >= 0.70", p => p.pCover >= 0.70);

console.log("\n═══ By sDiff ═══");
test("sDiff >= 2", p => p.sDiff >= 2);
test("sDiff >= 3", p => p.sDiff >= 3);
test("sDiff >= 4", p => p.sDiff >= 4);
test("sDiff >= 5", p => p.sDiff >= 5);
test("sDiff >= 6", p => p.sDiff >= 6);

console.log("\n═══ By spread size ═══");
test("line <= 5", p => p.line <= 5);
test("line <= 8", p => p.line <= 8);
test("line <= 10", p => p.line <= 10);
test("line <= 12", p => p.line <= 12);
test("line <= 15", p => p.line <= 15);
test("line 3-10", p => p.line >= 3 && p.line <= 10);
test("line 3-12", p => p.line >= 3 && p.line <= 12);

console.log("\n═══ Fav vs Dog ═══");
test("Favorites only", p => p.isFav);
test("Underdogs only", p => !p.isFav);
test("Dogs pCover>=0.63", p => !p.isFav && p.pCover >= 0.63);
test("Dogs pCover>=0.65", p => !p.isFav && p.pCover >= 0.65);
test("Favs pCover>=0.65", p => p.isFav && p.pCover >= 0.65);

console.log("\n═══ Combined filters ═══");
test("pCover>=0.63 AND sDiff>=3", p => p.pCover >= 0.63 && p.sDiff >= 3);
test("pCover>=0.63 AND line<=10", p => p.pCover >= 0.63 && p.line <= 10);
test("pCover>=0.63 AND line<=12", p => p.pCover >= 0.63 && p.line <= 12);
test("pCover>=0.65 AND sDiff>=3", p => p.pCover >= 0.65 && p.sDiff >= 3);
test("pCover>=0.65 AND line<=10", p => p.pCover >= 0.65 && p.line <= 10);
test("pCover>=0.65 AND line<=12", p => p.pCover >= 0.65 && p.line <= 12);
test("pCover>=0.65 AND sDiff>=4", p => p.pCover >= 0.65 && p.sDiff >= 4);
test("pCover>=0.67 AND line<=10", p => p.pCover >= 0.67 && p.line <= 10);
test("pCover>=0.67 AND line<=12", p => p.pCover >= 0.67 && p.line <= 12);
test("pCover>=0.65 AND line 3-12", p => p.pCover >= 0.65 && p.line >= 3 && p.line <= 12);
test("pCover>=0.63 AND line 3-12", p => p.pCover >= 0.63 && p.line >= 3 && p.line <= 12);

console.log("\n═══ Dogs + combined ═══");
test("Dogs pCover>=0.63 line<=10", p => !p.isFav && p.pCover >= 0.63 && p.line <= 10);
test("Dogs pCover>=0.63 line<=12", p => !p.isFav && p.pCover >= 0.63 && p.line <= 12);
test("Dogs pCover>=0.65 line<=12", p => !p.isFav && p.pCover >= 0.65 && p.line <= 12);
test("Dogs pCover>=0.65 line<=10", p => !p.isFav && p.pCover >= 0.65 && p.line <= 10);
test("Dogs pCover>=0.67 line<=12", p => !p.isFav && p.pCover >= 0.67 && p.line <= 12);
