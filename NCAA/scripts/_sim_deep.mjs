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
      margin: g.margin || 0,
    });
  }
}

function test(label, filterFn) {
  const filtered = picks.filter(filterFn);
  const w = filtered.filter(p => p.result).length;
  const l = filtered.length - w;
  const pct = filtered.length > 0 ? (w / filtered.length * 100).toFixed(1) : "n/a";
  const units = (w - l * 1.1).toFixed(1);
  const roi = filtered.length > 0 ? (parseFloat(units) / filtered.length * 100).toFixed(1) : "n/a";
  console.log(`${label.padEnd(55)} ${String(w + "-" + l).padEnd(10)} ${pct}%  ${units}u  ROI: ${roi}%`);
}

console.log("═══ WHY ARE FAVORITES BAD? ═══\n");
test("Favs ALL", p => p.isFav);
test("Favs line <= 3", p => p.isFav && p.line <= 3);
test("Favs line 3-6", p => p.isFav && p.line >= 3 && p.line <= 6);
test("Favs line 6-10", p => p.isFav && p.line >= 6 && p.line <= 10);
test("Favs pCover >= 0.65", p => p.isFav && p.pCover >= 0.65);
test("Favs pCover >= 0.67", p => p.isFav && p.pCover >= 0.67);
test("Favs pCover >= 0.70", p => p.isFav && p.pCover >= 0.70);
test("Favs sDiff >= 5", p => p.isFav && p.sDiff >= 5);
test("Favs sDiff >= 6", p => p.isFav && p.sDiff >= 6);
test("Favs line<=5 pCover>=0.65", p => p.isFav && p.line <= 5 && p.pCover >= 0.65);
test("Favs line<=5 pCover>=0.67", p => p.isFav && p.line <= 5 && p.pCover >= 0.67);

console.log("\n═══ DOGS DETAIL ═══\n");
test("Dogs ALL", p => !p.isFav);
test("Dogs line <= 3", p => !p.isFav && p.line <= 3);
test("Dogs line 3-6", p => !p.isFav && p.line >= 3 && p.line <= 6);
test("Dogs line 6-10", p => !p.isFav && p.line >= 6 && p.line <= 10);
test("Dogs pCover >= 0.65", p => !p.isFav && p.pCover >= 0.65);
test("Dogs pCover >= 0.67", p => !p.isFav && p.pCover >= 0.67);

console.log("\n═══ BOTH SIDES — pCover + sDiff combos ═══\n");
test("pCover>=0.65 sDiff>=5", p => p.pCover >= 0.65 && p.sDiff >= 5);
test("pCover>=0.65 sDiff>=6", p => p.pCover >= 0.65 && p.sDiff >= 6);
test("pCover>=0.67 sDiff>=4", p => p.pCover >= 0.67 && p.sDiff >= 4);
test("pCover>=0.67 sDiff>=5", p => p.pCover >= 0.67 && p.sDiff >= 5);
test("pCover>=0.67 sDiff>=6", p => p.pCover >= 0.67 && p.sDiff >= 6);
test("pCover>=0.70 sDiff>=4", p => p.pCover >= 0.70 && p.sDiff >= 4);

console.log("\n═══ REQUIRE HIGHER pCover FOR FAVS ═══\n");
test("Dogs pCover>=0.63 OR Favs pCover>=0.67", p =>
  (!p.isFav && p.pCover >= 0.63) || (p.isFav && p.pCover >= 0.67));
test("Dogs pCover>=0.63 OR Favs pCover>=0.70", p =>
  (!p.isFav && p.pCover >= 0.63) || (p.isFav && p.pCover >= 0.70));
test("Dogs pCover>=0.65 OR Favs pCover>=0.67", p =>
  (!p.isFav && p.pCover >= 0.65) || (p.isFav && p.pCover >= 0.67));
test("Dogs pCover>=0.65 OR Favs pCover>=0.70", p =>
  (!p.isFav && p.pCover >= 0.65) || (p.isFav && p.pCover >= 0.70));
test("Dogs pCover>=0.63 OR Favs pCover>=0.65 sDiff>=6", p =>
  (!p.isFav && p.pCover >= 0.63) || (p.isFav && p.pCover >= 0.65 && p.sDiff >= 6));
test("Dogs pCover>=0.63 OR Favs pCover>=0.67 sDiff>=5", p =>
  (!p.isFav && p.pCover >= 0.63) || (p.isFav && p.pCover >= 0.67 && p.sDiff >= 5));

console.log("\n═══ MARGIN-BASED (model disagreement size) ═══\n");
test("abs(margin) >= 3", p => Math.abs(p.margin) >= 3);
test("abs(margin) >= 4", p => Math.abs(p.margin) >= 4);
test("abs(margin) >= 5", p => Math.abs(p.margin) >= 5);
test("abs(margin) >= 3 AND pCover>=0.65", p => Math.abs(p.margin) >= 3 && p.pCover >= 0.65);
test("abs(margin) >= 4 AND pCover>=0.65", p => Math.abs(p.margin) >= 4 && p.pCover >= 0.65);
