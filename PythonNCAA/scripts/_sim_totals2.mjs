import fs from "fs";
const h = JSON.parse(fs.readFileSync("data/history.json", "utf8"));

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
      total: g.total || 0,
      line: Math.abs(g.line || 0),
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

console.log("═══ Targeting 60%+ ═══\n");

test("pOU>=0.75 AND total<=150", p => p.pOU >= 0.75 && p.total <= 150);
test("pOU>=0.75 AND total<=155", p => p.pOU >= 0.75 && p.total <= 155);
test("pOU>=0.75 AND total<=145", p => p.pOU >= 0.75 && p.total <= 145);
test("pOU>=0.78", p => p.pOU >= 0.78);
test("pOU>=0.80", p => p.pOU >= 0.80);
test("tDiff>=10 AND pOU>=0.70", p => p.tDiff >= 10 && p.pOU >= 0.70);
test("tDiff>=10 AND total<=155", p => p.tDiff >= 10 && p.total <= 155);
test("tDiff>=12", p => p.tDiff >= 12);
test("tDiff>=10 AND pOU>=0.75", p => p.tDiff >= 10 && p.pOU >= 0.75);

console.log("\n═══ OVER only combos ═══\n");
test("OVER pOU>=0.70 total<=155", p => p.side === "OVER" && p.pOU >= 0.70 && p.total <= 155);
test("OVER pOU>=0.65 total<=150", p => p.side === "OVER" && p.pOU >= 0.65 && p.total <= 150);
test("OVER pOU>=0.70 total<=150", p => p.side === "OVER" && p.pOU >= 0.70 && p.total <= 150);

console.log("\n═══ UNDER only combos ═══\n");
test("UNDER pOU>=0.75", p => p.side === "UNDER" && p.pOU >= 0.75);
test("UNDER pOU>=0.75 total<=155", p => p.side === "UNDER" && p.pOU >= 0.75 && p.total <= 155);
test("UNDER pOU>=0.78", p => p.side === "UNDER" && p.pOU >= 0.78);
test("UNDER tDiff>=10", p => p.side === "UNDER" && p.tDiff >= 10);

console.log("\n═══ Split strategy: different thresholds per side ═══\n");
test("OVER pOU>=0.65 OR UNDER pOU>=0.75", p =>
  (p.side === "OVER" && p.pOU >= 0.65) || (p.side === "UNDER" && p.pOU >= 0.75));
test("OVER pOU>=0.65 OR UNDER pOU>=0.78", p =>
  (p.side === "OVER" && p.pOU >= 0.65) || (p.side === "UNDER" && p.pOU >= 0.78));
test("OVER pOU>=0.70 OR UNDER pOU>=0.75", p =>
  (p.side === "OVER" && p.pOU >= 0.70) || (p.side === "UNDER" && p.pOU >= 0.75));
test("OVER pOU>=0.70 OR UNDER pOU>=0.78", p =>
  (p.side === "OVER" && p.pOU >= 0.70) || (p.side === "UNDER" && p.pOU >= 0.78));

console.log("\n═══ Best combo: asymmetric + total filter ═══\n");
test("(OVER pOU>=0.65) OR (UNDER pOU>=0.75 total<=155)", p =>
  (p.side === "OVER" && p.pOU >= 0.65) || (p.side === "UNDER" && p.pOU >= 0.75 && p.total <= 155));
test("(OVER pOU>=0.65 total<=155) OR (UNDER pOU>=0.75)", p =>
  (p.side === "OVER" && p.pOU >= 0.65 && p.total <= 155) || (p.side === "UNDER" && p.pOU >= 0.75));
test("(OVER pOU>=0.65) OR (UNDER pOU>=0.78)", p =>
  (p.side === "OVER" && p.pOU >= 0.65) || (p.side === "UNDER" && p.pOU >= 0.78));
