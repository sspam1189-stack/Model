import fs from "fs";
const h = JSON.parse(fs.readFileSync("data/history.json", "utf8"));

// Collect all graded totals picks with their metadata
const picks = [];
for (const r of h.runs || []) {
  if (r.burnIn) continue;
  for (const g of r.games || []) {
    if (!g.oPick || g.oPick === "PASS") continue;
    if (!g.oResult || g.oResult === "PUSH") continue;
    picks.push({
      side: g.oPick,
      result: g.oResult === "WIN" ? 1 : 0,
      pOU: g.pOU || 0,
      tDiff: Math.abs(g.tDiff || g.cleanTDiff || 0),
      conf: g.oConf || "low",
      total: g.total || 0,
      sDiff: Math.abs(g.sDiff || 0),
      line: Math.abs(g.line || 0),
    });
  }
}

console.log(`Total graded picks: ${picks.length}\n`);

// Test different filter combos
function test(label, filterFn) {
  const filtered = picks.filter(filterFn);
  const w = filtered.filter(p => p.result).length;
  const l = filtered.length - w;
  const pct = filtered.length > 0 ? (w / filtered.length * 100).toFixed(1) : "n/a";
  const units = (w - l * 1.1).toFixed(1);
  const roi = filtered.length > 0 ? (parseFloat(units) / filtered.length * 100).toFixed(1) : "n/a";
  console.log(`${label.padEnd(45)} ${w}-${l} (${pct}%) ${units}u  ROI: ${roi}%`);
}

console.log("═══ pOU threshold ═══");
test("pOU >= 0.65", p => p.pOU >= 0.65);
test("pOU >= 0.68", p => p.pOU >= 0.68);
test("pOU >= 0.70", p => p.pOU >= 0.70);
test("pOU >= 0.72", p => p.pOU >= 0.72);
test("pOU >= 0.75", p => p.pOU >= 0.75);

console.log("\n═══ tDiff threshold ═══");
test("tDiff >= 4", p => p.tDiff >= 4);
test("tDiff >= 5", p => p.tDiff >= 5);
test("tDiff >= 6", p => p.tDiff >= 6);
test("tDiff >= 8", p => p.tDiff >= 8);
test("tDiff >= 10", p => p.tDiff >= 10);

console.log("\n═══ Combined: pOU + tDiff ═══");
test("pOU>=0.65 AND tDiff>=5", p => p.pOU >= 0.65 && p.tDiff >= 5);
test("pOU>=0.65 AND tDiff>=6", p => p.pOU >= 0.65 && p.tDiff >= 6);
test("pOU>=0.68 AND tDiff>=5", p => p.pOU >= 0.68 && p.tDiff >= 5);
test("pOU>=0.68 AND tDiff>=6", p => p.pOU >= 0.68 && p.tDiff >= 6);
test("pOU>=0.70 AND tDiff>=5", p => p.pOU >= 0.70 && p.tDiff >= 5);
test("pOU>=0.70 AND tDiff>=6", p => p.pOU >= 0.70 && p.tDiff >= 6);
test("pOU>=0.70 AND tDiff>=8", p => p.pOU >= 0.70 && p.tDiff >= 8);
test("pOU>=0.72 AND tDiff>=6", p => p.pOU >= 0.72 && p.tDiff >= 6);
test("pOU>=0.75 AND tDiff>=6", p => p.pOU >= 0.75 && p.tDiff >= 6);

console.log("\n═══ By side ═══");
test("UNDER only", p => p.side === "UNDER");
test("OVER only", p => p.side === "OVER");
test("UNDER pOU>=0.70", p => p.side === "UNDER" && p.pOU >= 0.70);
test("OVER pOU>=0.70", p => p.side === "OVER" && p.pOU >= 0.70);
test("UNDER pOU>=0.70 tDiff>=6", p => p.side === "UNDER" && p.pOU >= 0.70 && p.tDiff >= 6);
test("OVER pOU>=0.65", p => p.side === "OVER" && p.pOU >= 0.65);
test("OVER pOU>=0.68", p => p.side === "OVER" && p.pOU >= 0.68);

console.log("\n═══ Elite only ═══");
test("elite conf only", p => p.conf === "elite");
test("elite + pOU>=0.70", p => p.conf === "elite" && p.pOU >= 0.70);

console.log("\n═══ Spread size filter ═══");
test("spread <= 10", p => p.line <= 10);
test("spread <= 15", p => p.line <= 15);
test("spread <= 10 AND pOU>=0.70", p => p.line <= 10 && p.pOU >= 0.70);
test("spread <= 15 AND pOU>=0.70", p => p.line <= 15 && p.pOU >= 0.70);

console.log("\n═══ Total line filter ═══");
test("total >= 130", p => p.total >= 130);
test("total >= 140", p => p.total >= 140);
test("total <= 140", p => p.total <= 140);
test("total <= 150", p => p.total <= 150);
test("total 130-155", p => p.total >= 130 && p.total <= 155);
test("total 130-155 AND pOU>=0.70", p => p.total >= 130 && p.total <= 155 && p.pOU >= 0.70);
