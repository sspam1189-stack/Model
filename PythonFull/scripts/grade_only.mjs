#!/usr/bin/env node
// scripts/grade_only.mjs
// ────────────────────────────────────────────────────────────────────────────
// Adds sResult / oResult to every game in history.json WITHOUT re-analyzing.
// Picks, projections, weights, Kalman — all untouched. Just grading.
//
// Usage:
//   node scripts/grade_only.mjs              # grade + save
//   node scripts/grade_only.mjs --dry-run    # preview only
// ────────────────────────────────────────────────────────────────────────────

import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const HISTORY_PATH = path.join(__dirname, "..", "data", "history.json");
const SAVE = !process.argv.includes("--dry-run");

function gradeSpread(g) {
  if (!g.sPick || g.sPick === "PASS") return null;
  if (!Number.isFinite(g.homeScore) || !Number.isFinite(g.awayScore)) return null;

  const m = g.sPick.match(/(.+?)\s+([+-])(\d+(?:\.\d+)?)/);
  if (!m) return null;
  const team = m[1].trim();
  const spread = m[2] === "-" ? -parseFloat(m[3]) : parseFloat(m[3]);
  const chosenIsHome = team === g.home;
  const margin = chosenIsHome
    ? g.homeScore - g.awayScore
    : g.awayScore - g.homeScore;
  const cover = margin + spread;
  return cover > 0 ? "WIN" : cover < 0 ? "LOSS" : "PUSH";
}

function gradeTotal(g) {
  if (!g.oPick || g.oPick === "PASS") return null;
  if (!Number.isFinite(g.homeScore) || !Number.isFinite(g.awayScore)) return null;
  if (!Number.isFinite(g.total)) return null;

  const actual = g.homeScore + g.awayScore;
  if (g.oPick === "OVER")  return actual > g.total ? "WIN" : actual < g.total ? "LOSS" : "PUSH";
  if (g.oPick === "UNDER") return actual < g.total ? "WIN" : actual > g.total ? "LOSS" : "PUSH";
  return null;
}

const store = JSON.parse(fs.readFileSync(HISTORY_PATH, "utf8"));
const runs = store.runs || [];

let graded = 0, alreadyGraded = 0, noScore = 0, noPick = 0;
let sW = 0, sL = 0, oW = 0, oL = 0;

for (const run of runs) {
  for (const g of run.games || []) {
    if (!g.sPick || g.sPick === "PASS") { noPick++; continue; }
    if (!Number.isFinite(g.homeScore) || !Number.isFinite(g.awayScore)) { noScore++; continue; }

    const sr = gradeSpread(g);
    const or = gradeTotal(g);

    if (g.sResult && g.sResult === sr) { alreadyGraded++; }
    else { graded++; }

    g.sResult = sr;
    g.oResult = or;

    if (sr === "WIN") sW++;
    if (sr === "LOSS") sL++;
    if (or === "WIN") oW++;
    if (or === "LOSS") oL++;
  }
}

const pct = (w, l) => (w + l > 0) ? (100 * w / (w + l)).toFixed(1) : "0.0";
const u = (w, l) => { const v = w * 0.9091 - l; return (v >= 0 ? "+" : "") + v.toFixed(1); };

console.log("=== Grade Only ===");
console.log("  Graded:  " + graded + " games (newly graded)");
console.log("  Already: " + alreadyGraded + " (unchanged)");
console.log("  No score:" + noScore + " (future/missing)");
console.log("  No pick: " + noPick + " (PASS/no pick)");
console.log();
console.log("  Spread: " + sW + "-" + sL + " (" + pct(sW, sL) + "%) " + u(sW, sL) + "u");
console.log("  Totals: " + oW + "-" + oL + " (" + pct(oW, oL) + "%) " + u(oW, oL) + "u");

if (SAVE) {
  const ts = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
  const backup = HISTORY_PATH.replace(".json", "_pre_grade_" + ts + ".json");
  fs.copyFileSync(HISTORY_PATH, backup);
  console.log("\n  Backup: " + path.basename(backup));

  fs.writeFileSync(HISTORY_PATH, JSON.stringify(store, null, 2));
  console.log("  Saved " + graded + " new grades to history.json");
} else {
  console.log("\n  DRY RUN — no changes. Remove --dry-run to save.");
}
