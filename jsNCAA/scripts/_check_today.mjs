import fs from "fs";
const h = JSON.parse(fs.readFileSync("data/history.json","utf8"));
const last = h.runs[h.runs.length-1];
console.log("Date:", last.date, "| Games:", last.games.length);
console.log("\nAll non-skipped games:");
for (const x of last.games) {
  if (x.status === "SKIPPED") continue;
  const pH = x.pHomeCover ?? x.pCover;
  const pA = x.pAwayCover ?? 0;
  const best = Math.max(pH || 0, pA || 0);
  console.log(
    `${x.away} @ ${x.home}`,
    `| bestP: ${best.toFixed(3)}`,
    `| sDiff: ${(x.sDiff||0).toFixed(1)}`,
    `| line: ${x.line}`,
    `| pick: ${x.sPick || "PASS"}`
  );
}

// Also check recent hit rate at different thresholds
console.log("\n--- Backtest: recent pCover distribution ---");
const allGames = h.runs.flatMap(r => r.games).filter(g =>
  g.status !== "SKIPPED" && g.status !== "MISSING_STATS" && g.result !== undefined
);
const graded = allGames.filter(g => g.pCover > 0);
console.log("Total graded games with pCover:", graded.length);

for (const thresh of [0.55, 0.58, 0.60, 0.62, 0.65, 0.67, 0.70]) {
  const picks = graded.filter(g => g.pCover >= thresh);
  const wins = picks.filter(g => g.result === "WIN").length;
  const losses = picks.filter(g => g.result === "LOSS").length;
  const pushes = picks.filter(g => g.result === "PUSH").length;
  const pct = (wins+losses) > 0 ? (wins/(wins+losses)*100).toFixed(1) : "n/a";
  console.log(`  >= ${thresh}: ${wins}-${losses}-${pushes} (${pct}%) | ${picks.length} picks total`);
}
