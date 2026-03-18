#!/usr/bin/env node
// scripts/regrade_picks.mjs
// Re-applies current spread/total pick filters to all historical games
// without hitting any APIs. Uses existing projections (margin, pHomeCover, etc.)
// and re-grades against actual scores.
//
// Usage: node scripts/regrade_picks.mjs

import { loadStore, saveStore } from "./store.mjs";

function parseSpreadPick(pick) {
  if (!pick || pick === "PASS") return null;
  const m = pick.match(/(.+?)\s+([+-])(\d+(?:\.\d+)?)/);
  return m ? { team: m[1].trim(), sign: m[2], pts: parseFloat(m[3]) } : null;
}

function gradeSpread(g) {
  const p = parseSpreadPick(g.sPick);
  if (!p) return null;
  const chosenIsHome = p.team === g.home;
  const margin = chosenIsHome ? g.homeScore - g.awayScore : g.awayScore - g.homeScore;
  const val = p.sign === "+" ? margin + p.pts : margin - p.pts;
  if (val === 0) return "PUSH";
  return val > 0 ? "WIN" : "LOSS";
}

function gradeTotal(g) {
  if (!g.oPick || g.oPick === "PASS") return null;
  const actual = g.homeScore + g.awayScore;
  if (actual === g.total) return "PUSH";
  if (g.oPick === "OVER") return actual > g.total ? "WIN" : "LOSS";
  return actual < g.total ? "WIN" : "LOSS";
}

const SDIFF_MIN = 3;
const SDIFF_CAP = 12;
const P_COVER_THRESH = 0.65;
const FAV_LINE_CAP = 6;

function rePickSpread(g) {
  const pHomeCover = g.pHomeCover;
  const pAwayCover = g.pAwayCover;
  if (pHomeCover == null || pAwayCover == null) return; // no Bayesian data

  const sDiff = g.sDiff || 0;
  const bestP = Math.max(pHomeCover, pAwayCover);
  const side = pHomeCover >= pAwayCover ? "home" : "away";

  const absLine = Math.abs(g.line || 0);
  const homeFav = g.line > 0;

  // Dog/fav detection
  const isDog = side === "home" ? g.line < 0 : g.line > 0;
  // Dogs: no line cap. Favs: line <= 6
  const lineOK = isDog ? true : absLine <= FAV_LINE_CAP;

  if (bestP >= P_COVER_THRESH && sDiff >= SDIFF_MIN && sDiff <= SDIFF_CAP && lineOK && absLine > 0) {
    if (side === "home") {
      g.sPick = homeFav ? `${g.home} -${absLine}` : `${g.home} +${absLine}`;
    } else {
      g.sPick = homeFav ? `${g.away} +${absLine}` : `${g.away} -${absLine}`;
    }
    g.sConf = "elite";
    g.pCover = bestP;
  } else {
    g.sPick = "PASS";
    g.sConf = "low";
    g.pCover = null;
  }
}

function rePickTotal(g) {
  const pOver = g.pOver;
  const pUnder = g.pUnder;
  if (pOver == null || pUnder == null) return;

  const bestP = Math.max(pOver, pUnder);
  const side = pOver >= pUnder ? "OVER" : "UNDER";
  const vegasTotal = g.total || 0;

  // Total picks disabled — edge not holding
  g.oPick = "PASS";
  g.oConf = "low";
  g.pOU = null;
}

// ── Main ─────────────────────────────────────────────────────────────────

const store = loadStore();
const runs = store.runs || {};

let totalGames = 0;
let sWins = 0, sLosses = 0, sPushes = 0, sPicks = 0;
let oWins = 0, oLosses = 0, oPicks = 0;
let oldSPicks = 0, oldSWins = 0, oldSLosses = 0;

for (const [key, run] of Object.entries(runs)) {
  if (!run.games) continue;
  for (const g of run.games) {
    // Count old record for comparison
    if (g.sResult === "WIN") { oldSWins++; oldSPicks++; }
    else if (g.sResult === "LOSS") { oldSLosses++; oldSPicks++; }

    // Skip games without scores or no line
    if (g.homeScore == null || g.awayScore == null) continue;
    if (g.line == null && g.total == null) continue;

    totalGames++;

    // Re-apply spread pick logic
    rePickSpread(g);

    // Re-apply total pick logic
    rePickTotal(g);

    // Re-grade
    if (g.sPick !== "PASS") {
      g.sResult = gradeSpread(g);
      sPicks++;
      if (g.sResult === "WIN") sWins++;
      else if (g.sResult === "LOSS") sLosses++;
      else if (g.sResult === "PUSH") sPushes++;
    } else {
      g.sResult = null;
    }

    if (g.oPick !== "PASS") {
      g.oResult = gradeTotal(g);
      oPicks++;
      if (g.oResult === "WIN") oWins++;
      else if (g.oResult === "LOSS") oLosses++;
    } else {
      g.oResult = null;
    }
  }
}

saveStore(store);

const sPct = sPicks > 0 ? ((sWins / (sWins + sLosses)) * 100).toFixed(1) : "N/A";
const oPct = oPicks > 0 ? ((oWins / (oWins + oLosses)) * 100).toFixed(1) : "N/A";
const oldPct = oldSPicks > 0 ? ((oldSWins / (oldSWins + oldSLosses)) * 100).toFixed(1) : "N/A";

console.log(`\n══════════════════════════════════════════════════════════`);
console.log(`  REGRADE COMPLETE (dogs only, pCover >= ${P_COVER_THRESH}, sDiff ${SDIFF_MIN}-${SDIFF_CAP})`);
console.log(`══════════════════════════════════════════════════════════`);
console.log(`  Games processed: ${totalGames}`);
console.log(`──────────────────────────────────────────────────────────`);
console.log(`  OLD SPREAD RECORD:  ${oldSWins}-${oldSLosses} (${oldPct}%)  n=${oldSPicks}`);
console.log(`  NEW SPREAD RECORD:  ${sWins}-${sLosses}-${sPushes} (${sPct}%)  n=${sPicks}`);
console.log(`  TOTAL RECORD:       ${oWins}-${oLosses} (${oPct}%)  n=${oPicks}`);
console.log(`══════════════════════════════════════════════════════════\n`);
