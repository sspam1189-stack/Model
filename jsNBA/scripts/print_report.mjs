import { loadStore } from "./store.mjs";

function gradeTotal(g) {
  if (g.oPick === "PASS") return null;
  if (!Number.isFinite(g.homeScore) || !Number.isFinite(g.awayScore)) return null;
  const actualTotal = g.homeScore + g.awayScore;
  if (!Number.isFinite(g.total) || g.total === 0) return null;
  if (actualTotal === g.total) return "push";
  return (g.oPick === "OVER") ? (actualTotal > g.total ? "win" : "loss") : (actualTotal < g.total ? "win" : "loss");
}

function gradeSpread(g) {
  if (g.sPick === "PASS") return null;
  if (!Number.isFinite(g.homeScore) || !Number.isFinite(g.awayScore)) return null;

  const m = g.sPick.match(/(.+?)\s+([+-])(\d+(?:\.\d+)?)/);
  if (!m) return null;
  const team = m[1].trim();
  const sign = m[2];
  const pts = parseFloat(m[3]);
  if (!Number.isFinite(pts)) return null;

  const chosenIsHome = team === g.home;
  const chosenScore = chosenIsHome ? g.homeScore : g.awayScore;
  const oppScore = chosenIsHome ? g.awayScore : g.homeScore;
  const chosenMargin = chosenScore - oppScore;

  const cover = sign === "+" ? (chosenMargin + pts > 0) : (chosenMargin - pts > 0);
  const push = sign === "+" ? (chosenMargin + pts === 0) : (chosenMargin - pts === 0);
  return push ? "push" : (cover ? "win" : "loss");
}

function tally(items, type, conf=null) {
  let w=0,l=0,p=0;
  for (const g of items) {
    const c = type==="spread" ? g.sConf : g.oConf;
    if (conf && c!==conf) continue;
    const res = type==="spread" ? gradeSpread(g) : gradeTotal(g);
    if (!res) continue;
    if (res==="win") w++; else if (res==="loss") l++; else p++;
  }
  const denom = w+l;
  const pct = denom ? (w/denom*100).toFixed(1) : "n/a";
  return {w,l,p,pct};
}

const store = loadStore();
const games = store.runs.flatMap(r => r.games.map(g => ({...g, date:r.dateDisplay})));

const sAll = tally(games,"spread");
const sHigh = tally(games,"spread","high");
const sMed = tally(games,"spread","medium");

const tAll = tally(games,"total");
const tHigh = tally(games,"total","high");
const tMed = tally(games,"total","medium");

console.log("Spread all:", sAll, "high:", sHigh, "med:", sMed);
console.log("Total  all:", tAll, "high:", tHigh, "med:", tMed);
