import fs from "fs";
const h = JSON.parse(fs.readFileSync("data/history.json", "utf8"));

// Compute prediction error variance per team
const teamErrors = {};

for (const r of h.runs || []) {
  if (r.burnIn) continue;
  for (const g of r.games || []) {
    if (typeof g.homeScore !== "number" || typeof g.awayScore !== "number") continue;
    if (typeof g.margin !== "number" || typeof g.line !== "number") continue;

    const actualEdge = (g.homeScore - g.awayScore) + g.line;
    const projEdge = g.margin;
    const err = actualEdge - projEdge;

    if (!teamErrors[g.home]) teamErrors[g.home] = [];
    if (!teamErrors[g.away]) teamErrors[g.away] = [];
    teamErrors[g.home].push(err);
    teamErrors[g.away].push(err);
  }
}

console.log("\n=== Per-Team Prediction Error Variance ===\n");
console.log("Team".padEnd(30) + "Games".padStart(6) + "  Var".padStart(8) + "  Std".padStart(6) + "  Win%".padStart(7));
console.log("-".repeat(60));

// Also compute win rate per team
const teamWL = {};
for (const r of h.runs || []) {
  if (r.burnIn) continue;
  for (const g of r.games || []) {
    if (g.sResult !== "WIN" && g.sResult !== "LOSS") continue;
    const pick = g.sPick || "";
    // Figure out which team was picked
    for (const team of [g.home, g.away]) {
      if (pick.includes(team)) {
        if (!teamWL[team]) teamWL[team] = { w: 0, l: 0 };
        if (g.sResult === "WIN") teamWL[team].w++;
        else teamWL[team].l++;
      }
    }
  }
}

const sorted = Object.entries(teamErrors)
  .filter(([, errs]) => errs.length >= 5)
  .map(([team, errs]) => {
    const mean = errs.reduce((s, x) => s + x, 0) / errs.length;
    const variance = errs.reduce((s, x) => s + (x - mean) ** 2, 0) / errs.length;
    const wl = teamWL[team] || { w: 0, l: 0 };
    const pct = wl.w + wl.l > 0 ? (wl.w / (wl.w + wl.l) * 100).toFixed(0) : "n/a";
    return { team, n: errs.length, variance, std: Math.sqrt(variance), winPct: pct, wl };
  })
  .sort((a, b) => b.variance - a.variance);

for (const t of sorted) {
  const wl = t.wl.w + t.wl.l > 0 ? `${t.wl.w}-${t.wl.l}` : "";
  console.log(
    t.team.padEnd(30) +
    String(t.n).padStart(6) +
    t.variance.toFixed(1).padStart(8) +
    t.std.toFixed(1).padStart(6) +
    `  ${t.winPct}% ${wl}`.padStart(12)
  );
}

console.log("\nOverall avg var:", (sorted.reduce((s, t) => s + t.variance, 0) / sorted.length).toFixed(1));
console.log("Most volatile vs least volatile ratio:", (sorted[0].variance / sorted[sorted.length - 1].variance).toFixed(1) + "x");
