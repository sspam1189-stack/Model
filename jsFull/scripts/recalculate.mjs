#!/usr/bin/env node
// scripts/recalculate.mjs
// ────────────────────────────────────────────────────────────────────────────
// Re-runs every game in history.json through the CURRENT model engine
// using CACHED stats only — no API calls.
//
// All data needed is already stored:
//   - Team stats: data/stats_cache/YYYYMMDD.json (from prior backfill runs)
//   - Game lines/totals/scores: stored in history.json
//
// Rebuilds Kalman state and evolves weights from scratch — a true replay.
//
// Usage:
//   node scripts/recalculate.mjs              # recalculate + save (default)
//   node scripts/recalculate.mjs --dry-run    # preview changes without saving
//
// Preserves: actual scores, lines, totals, _book, trends, run dates
// Recalculates: projections, picks, grades, Kalman state, weights
// ────────────────────────────────────────────────────────────────────────────

import { createRequire } from "module";
const require = createRequire(import.meta.url);
require("dotenv").config();

import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

import { loadDefaults, getAvgs, analyzeGame } from "./model_engine.mjs";
import { tuneWeights, computeResidualVar } from "./self_tune.mjs";
import {
  initializeKalman, applyDailyDrift, batchUpdate,
  saveKalmanState, pruneProcessedGames, kalmanSummary,
} from "./kalman_state.mjs";
import { applyB2BAdjustment } from "./sources/rest_detect.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DATA_DIR = path.join(__dirname, "..", "data");
const HISTORY_PATH = path.join(DATA_DIR, "history.json");
const CACHE_DIR = path.join(DATA_DIR, "stats_cache");

const SAVE = !process.argv.includes("--dry-run");

// ─── Inline Grading ─────────────────────────────────────────────────────────

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

// ─── Per-Date Stats from Disk Cache Only ────────────────────────────────────

function cacheFile(dateStr) {
  return path.join(CACHE_DIR, dateStr + ".json");
}

function getStatsForDate(dateStr) {
  const cf = cacheFile(dateStr);
  if (!fs.existsSync(cf)) return null;

  const raw = JSON.parse(fs.readFileSync(cf, "utf8"));
  // New enhanced format has { season, last10, home, away }
  // Old format is plain { "TeamName": { OFF, DEF, ... } }
  if (raw.season) return raw;  // enhanced
  return { season: raw, last10: null, home: null, away: null };  // legacy
}

// ─── Main ───────────────────────────────────────────────────────────────────

async function main() {
  console.log("==========================================================");
  console.log("  RECALCULATE - Replay history with cached stats (no API)");
  console.log("==========================================================");
  console.log("  Mode:  " + (SAVE ? "SAVE" : "DRY RUN (--dry-run flag detected)"));
  console.log();

  // 1. Load history
  if (!fs.existsSync(HISTORY_PATH)) {
    console.error("  ERROR: " + HISTORY_PATH + " not found");
    process.exit(1);
  }
  const store = JSON.parse(fs.readFileSync(HISTORY_PATH, "utf8"));
  const runs = (store.runs || []).sort((a, b) => a.date.localeCompare(b.date));
  console.log("  " + runs.length + " runs in history.json (sorted chronologically)");

  // 2. Weights (start from defaults, evolve forward)
  const defaults = loadDefaults();
  console.log("  Starting weights: sprHigh=" + defaults.DEFAULT_W.sprHigh + " ouHigh=" + defaults.DEFAULT_W.ouHigh + " wNET=" + defaults.DEFAULT_W.wNET + " hca=" + defaults.DEFAULT_W.hca);

  // 3. Unique dates + cache check
  const uniqueDates = [...new Set(runs.map(r => r.date))].sort();
  const cached = uniqueDates.filter(d => fs.existsSync(cacheFile(d))).length;
  const missing = uniqueDates.length - cached;
  console.log("  " + uniqueDates.length + " unique dates - " + cached + " cached, " + missing + " missing");
  if (missing > 0) {
    console.log("  WARNING: " + missing + " dates have no cached stats — those games will be skipped");
  }
  console.log();

  // 4. Load all cached stats into memory
  console.log("-- Loading cached stats --");
  const statsMap = new Map();
  let cacheHits = 0, failures = 0;

  for (const date of uniqueDates) {
    const stats = getStatsForDate(date);
    if (stats) {
      statsMap.set(date, stats);
      cacheHits++;
    } else {
      failures++;
    }
  }
  console.log("  Ready: " + statsMap.size + " dates cached, " + failures + " missing");
  console.log();

  // 5. Replay every game — rebuild Kalman state + evolve weights from scratch
  const BURN_IN_DAYS = 20;
  console.log("-- Replaying games (with Kalman rebuild + evolving weights) --");

  let total = 0, done = 0, skip = 0, noStats = 0;
  let pickFlips = 0, confFlips = 0, newPicks = 0, lostPicks = 0;
  const oldRec = { w: 0, l: 0 };
  const newRec = { w: 0, l: 0 };

  // Initialize Kalman from first available date's stats
  let kalmanState = null;
  const W     = { ...defaults.DEFAULT_W };
  const W_var = { ...defaults.DEFAULT_W_VAR };
  let prevDate    = null;
  let dateIndex   = 0;
  let prevGraded  = [];  // graded games from previous date

  // H2H matchup history — builds progressively as games are replayed.
  // Each date only sees games that happened before it, same as run_daily.
  const h2hMatchups = {};

  // B2B tracking — derived from previous day's games, no API needed
  let b2bTeams = new Set();       // teams that played yesterday → penalty today
  let prevDayTeams = new Set();   // accumulates teams from current date, becomes b2bTeams next day

  // Freeze residualVar from original projections BEFORE replay overwrites them.
  // Computing per-date during replay causes cascade: new projections → different
  // residualVar → different P(cover) → different picks → different tuneWeights →
  // snowballs by January. ResidualVar measures game noise, not model state.
  const dynamicResidualVar = computeResidualVar(runs);
  console.log("  Frozen residualVar: " + (dynamicResidualVar ? dynamicResidualVar.toFixed(1) : "null") + " (from original projections, held constant during replay)");

  // Known B2B adjustment constants (must match rest_detect.mjs)
  const B2B_OFF_DELTA = -1.5;
  const B2B_DEF_DELTA = 0.8;

  for (const run of runs) {
    const enhanced = statsMap.get(run.date);
    if (!enhanced) {
      for (const g of run.games || []) { total++; noStats++; }
      continue;
    }

    // Initialize Kalman on first date with stats
    if (!kalmanState) {
      kalmanState = initializeKalman(enhanced.season);
      kalmanState.lastDriftDate = run.date;  // set initial drift date
      console.log("  Kalman initialized from " + run.date);
    }

    // ── Start of new day: process yesterday's results before today's analysis ──
    // Flow: learn → tune → H2H → B2B → drift → analyze (residualVar frozen)
    if (run.date !== prevDate) {
      prevDate = run.date;
      dateIndex++;
      const dateLabel = run.date.slice(0,4) + "-" + run.date.slice(4,6) + "-" + run.date.slice(6);
      process.stdout.write(`  [${dateIndex}/${uniqueDates.length}] ${dateLabel}`);

      if (prevGraded.length) {
        // 1. Update Kalman with yesterday's graded games (learn first)
        batchUpdate(kalmanState, prevGraded);

        // 2. Self-tune weights on yesterday's graded games
        const wasInBurnIn = dateIndex - 1 <= BURN_IN_DAYS;
        if (!wasInBurnIn) {
          const { W: tunedW, W_var: tunedWVar } = tuneWeights(W, W_var, prevGraded);
          Object.assign(W,     tunedW);
          Object.assign(W_var, tunedWVar);
        }

        // 3. Feed yesterday's games into H2H matchup history
        for (const g of prevGraded) {
          if (!Number.isFinite(g.homeScore) || !Number.isFinite(g.awayScore)) continue;
          const key = [g.home, g.away].sort().join("::");
          if (!h2hMatchups[key]) {
            h2hMatchups[key] = { team1: [g.home, g.away].sort()[0], team2: [g.home, g.away].sort()[1], games: [] };
          }
          h2hMatchups[key].games.push({
            date: prevGraded[0]?._kalmanDate || run.date,
            home: { team: g.home, pts: g.homeScore },
            away: { team: g.away, pts: g.awayScore },
          });
        }
      }
      prevGraded = [];

      // 4. Build B2B set for today — teams from yesterday's prevDayTeams
      b2bTeams = prevDayTeams;
      prevDayTeams = new Set();

      // 5. Drift AFTER learning — account for time passing to today
      applyDailyDrift(kalmanState, run.date);
      
      const gamesThisDate = (run.games || []).filter(g => g.status !== "MISSING_ODDS" && g.status !== "SKIPPED").length;
      process.stdout.write(` — ${gamesThisDate} games${dateIndex <= BURN_IN_DAYS ? " (burn-in)" : ""}\n`);
    }
    const inBurnIn = dateIndex <= BURN_IN_DAYS;

    // Use raw season stats only — no last-10 or home/away blending during replay
    // Blending adds noise that compounds through Kalman over 60+ dates
    const gameStats_base = enhanced.season;

    // Apply B2B rest penalty independently (derived from previous day's games)
    const todaysGames = (run.games || []).map(g => ({ away: g.away, home: g.home }));
    const { adjusted: b2bAdjusted, b2bNotes } = applyB2BAdjustment(gameStats_base, b2bTeams, todaysGames);

    // Collect graded games from this date for Kalman + weight update
    const gradedThisDate = [];

    for (const g of run.games || []) {
      total++;

      // Track all teams that played today → becomes b2bTeams for next day
      if (g.home) prevDayTeams.add(g.home);
      if (g.away) prevDayTeams.add(g.away);

      if (g.status === "MISSING_ODDS" || g.status === "SKIPPED") { skip++; continue; }
      if (typeof g.line !== "number" || typeof g.total !== "number") { skip++; continue; }

      // Save old values
      const prevPick = g.sPick;
      const prevConf = g.sConf;
      const prevSR   = g.sResult;

      if (prevSR === "WIN")  oldRec.w++;
      if (prevSR === "LOSS") oldRec.l++;

      // Start from B2B-adjusted stats (independent computation)
      const gameStats = { ...b2bAdjusted };

      // Replay cached lineup deltas from run_daily (if present).
      // Since we already applied B2B independently, subtract the known B2B
      // constants from _adjDeltas to avoid double-counting.
      if (g._adjDeltas) {
        const STAT_KEYS = ["OFF", "DEF", "TS", "TO", "ORR", "PACE"];
        for (const [side, teamName] of [["away", g.away], ["home", g.home]]) {
          const delta = g._adjDeltas[side];
          if (delta && gameStats[teamName]) {
            gameStats[teamName] = { ...gameStats[teamName] };
            // Copy delta so we don't mutate the stored record
            const adj = { ...delta };
            // If this team is B2B (already adjusted above), strip the B2B
            // portion from the cached delta — leaves only lineup adjustments
            if (b2bTeams.has(teamName)) {
              if (adj.OFF) adj.OFF = Math.round((adj.OFF - B2B_OFF_DELTA) * 10000) / 10000;
              if (adj.DEF) adj.DEF = Math.round((adj.DEF - B2B_DEF_DELTA) * 10000) / 10000;
            }
            for (const k of STAT_KEYS) {
              if (adj[k] && Math.abs(adj[k]) > 0.0001) {
                gameStats[teamName][k] = (gameStats[teamName][k] || 0) + adj[k];
              }
            }
          }
        }
      }

      // Update b2bNote from independent computation
      const awayB2B = b2bNotes[g.away] || null;
      const homeB2B = b2bNotes[g.home] || null;
      if (awayB2B || homeB2B) {
        g.b2bNote = [awayB2B ? `${g.away}: ${awayB2B}` : null, homeB2B ? `${g.home}: ${homeB2B}` : null].filter(Boolean).join(" | ");
      } else {
        delete g.b2bNote;
      }

      const gameAvgs = getAvgs(gameStats);

      // Re-analyze with season stats + Kalman + weight variance + H2H
      const result = analyzeGame(
        { away: g.away, home: g.home, line: g.line, total: g.total },
        gameStats, gameAvgs, W, null, kalmanState, W_var, dynamicResidualVar, h2hMatchups
      );

      if (!result) { skip++; continue; }
      done++;

      // Track changes
      if (prevPick !== result.sPick) {
        if      (prevPick === "PASS" && result.sPick !== "PASS") newPicks++;
        else if (prevPick !== "PASS" && result.sPick === "PASS") lostPicks++;
        else pickFlips++;
      }
      if (prevPick === result.sPick && prevConf !== result.sConf) confFlips++;

      // Apply recalculated values
      g.aS     = result.aS;
      g.hS     = result.hS;
      g.pT     = result.pT;
      g.margin = result.margin;
      g.sDiff  = result.sDiff;
      g.tDiff  = result.tDiff;
      g.sPick  = result.sPick;
      g.sConf  = result.sConf;
      g.oPick  = result.oPick;
      g.oConf  = result.oConf;
      // Preserve original uncertaintyScore from the live run
      if (typeof g.uncertaintyScore !== "number") {
        g.uncertaintyScore = 0;
      }

      // Bayesian fields
      g.pHomeCover = result.pHomeCover;
      g.pAwayCover = result.pAwayCover;
      g.pOver      = result.pOver;
      g.pUnder     = result.pUnder;
      g.pCover     = result.pCover;
      g.pOU        = result.pOU;
      g.marginVar  = result.marginVar;
      g.marginStd  = result.marginStd;

      // H2H matchup fields
      g.h2hAdj    = result.h2hAdj;
      g.h2hGames  = result.h2hGames;
      g.h2hRecord = result.h2hRecord;
      g.h2hNote   = result.h2hNote;

      // Feature snapshots needed by self-tune
      g._features        = result._features;
      g._marginFeatures  = result._marginFeatures;

      // Burn-in flag
      run.burnIn = inBurnIn;

      // Re-grade against actual scores
      if (Number.isFinite(g.homeScore) && Number.isFinite(g.awayScore)) {
        g.sResult = gradeSpread(g);
        g.oResult = gradeTotal(g);
        if (g.sResult === "WIN")  newRec.w++;
        if (g.sResult === "LOSS") newRec.l++;

        // Collect for Kalman update (needs hS, aS, home, away, scores)
        gradedThisDate.push({ ...g, _kalmanDate: run.date });
      } else {
        delete g.sResult;
        delete g.oResult;
      }
    }

    // Collect graded games for next day's processing
    // (Kalman update + self-tune + H2H all happen at start of next day)
    prevGraded = gradedThisDate;

    // Accumulate for next date's pre-analysis tuning
    // (kept for backward compat with weight tracking in run record)
    run.weightsUsed = { ...W };
    run.weightsNext = { ...W };
  }

  // 6. Report
  function pct(w, l) { return (w + l > 0) ? (100 * w / (w + l)).toFixed(1) : "0.0"; }
  function u(w, l) {
    var v = w * 0.9091 - l;
    return (v >= 0 ? "+" : "") + v.toFixed(1);
  }

  console.log();
  console.log("-- Summary --");
  console.log("  Games:  " + total + " total, " + done + " recalculated, " + skip + " skipped, " + noStats + " no-stats");
  console.log();
  console.log("  Pick flips:   " + pickFlips + " (different team/side)");
  console.log("  Conf changes: " + confFlips + " (same pick, different tier)");
  console.log("  New picks:    " + newPicks + " (PASS -> pick)");
  console.log("  Lost picks:   " + lostPicks + " (pick -> PASS)");
  console.log();

  console.log("-- Spread Record --");
  console.log("  OLD: " + oldRec.w + "-" + oldRec.l + " (" + pct(oldRec.w, oldRec.l) + "%) " + u(oldRec.w, oldRec.l) + "u");
  console.log("  NEW: " + newRec.w + "-" + newRec.l + " (" + pct(newRec.w, newRec.l) + "%) " + u(newRec.w, newRec.l) + "u");
  console.log();

  // Fav/dog split
  var fav = 0, dog = 0, favW = 0, dogW = 0, favL = 0, dogL = 0;
  for (const run of runs) {
    for (const g of run.games || []) {
      if (!g.sPick || g.sPick === "PASS" || !["high", "elite"].includes(g.sConf)) continue;
      const m = g.sPick.match(/([+-])(\d+(?:\.\d+)?)$/);
      if (!m) continue;
      if (m[1] === "-") {
        fav++; if (g.sResult === "WIN") favW++; if (g.sResult === "LOSS") favL++;
      } else {
        dog++; if (g.sResult === "WIN") dogW++; if (g.sResult === "LOSS") dogL++;
      }
    }
  }
  var tp = fav + dog;
  console.log("-- Fav / Dog Split --");
  console.log("  Fav: " + fav + " (" + (tp ? Math.round(100 * fav / tp) : 0) + "%) " + favW + "-" + favL + " " + u(favW, favL) + "u");
  console.log("  Dog: " + dog + " (" + (tp ? Math.round(100 * dog / tp) : 0) + "%) " + dogW + "-" + dogL + " " + u(dogW, dogL) + "u");
  console.log();

  // Over/Under split
  var oW = 0, oL = 0, uW = 0, uL = 0;
  for (const run of runs) {
    for (const g of run.games || []) {
      if (!g.oPick || g.oPick === "PASS" || !["high", "elite"].includes(g.oConf)) continue;
      if (g.oPick === "OVER") {
        if (g.oResult === "WIN") oW++; if (g.oResult === "LOSS") oL++;
      } else {
        if (g.oResult === "WIN") uW++; if (g.oResult === "LOSS") uL++;
      }
    }
  }
  console.log("-- Over / Under Split --");
  console.log("  OVER:  " + oW + "-" + oL + " (" + pct(oW, oL) + "%) " + u(oW, oL) + "u");
  console.log("  UNDER: " + uW + "-" + uL + " (" + pct(uW, uL) + "%) " + u(uW, uL) + "u");
  console.log();

  // Team records
  const teams = {};
  for (const run of runs) {
    for (const g of run.games || []) {
      if (!g.sPick || g.sPick === "PASS" || !["high", "elite"].includes(g.sConf) || !g.sResult) continue;
      const m = g.sPick.match(/(.+?)\s+([+-])/);
      if (!m) continue;
      const team = m[1].trim();
      const isFav = m[2] === "-";
      if (!teams[team]) teams[team] = { w: 0, l: 0, p: 0, fav: 0, dog: 0 };
      if      (g.sResult === "WIN")  teams[team].w++;
      else if (g.sResult === "LOSS") teams[team].l++;
      else    teams[team].p++;
      if (isFav) teams[team].fav++; else teams[team].dog++;
    }
  }
  const sorted = Object.entries(teams).sort(function(a, b) {
    return (b[1].w + b[1].l) - (a[1].w + a[1].l);
  });
  if (sorted.length) {
    console.log("-- Team Records (top 15) --");
    for (const [name, t] of sorted.slice(0, 15)) {
      const tot = t.w + t.l;
      const wp = tot > 0 ? Math.round(100 * t.w / tot) : 0;
      console.log("  " + name.padEnd(26) + " " + (t.w+"-"+t.l+"-"+t.p).padEnd(9) + " " + (wp+"%").padEnd(5) + " " + u(t.w,t.l).padStart(7) + "  (" + t.fav + "F/" + t.dog + "D)");
    }
    if (sorted.length > 15) console.log("  ... +" + (sorted.length - 15) + " more");
    console.log();
  }

  // Kalman summary
  if (kalmanState) {
    console.log(kalmanSummary(kalmanState, 10));
    console.log();
  }

  // 7. Save
  if (SAVE) {
    const ts = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
    const backup = HISTORY_PATH.replace(".json", "_pre_recalc_" + ts + ".json");
    fs.copyFileSync(HISTORY_PATH, backup);
    console.log("  Backup: " + path.basename(backup));

    store.weights    = { ...W };
    store.weightsVar = { ...W_var };
    store.residualVar = computeResidualVar(runs);

    fs.writeFileSync(HISTORY_PATH, JSON.stringify(store, null, 2));
    console.log("  history.json updated (" + done + " games recalculated)");

    // Save rebuilt Kalman state
    if (kalmanState) {
      pruneProcessedGames(kalmanState, 60);
      saveKalmanState(kalmanState);
      console.log("  kalman_state.json rebuilt");
    }
  } else {
    console.log("  DRY RUN — no changes written. Remove --dry-run to save.");
  }

  console.log();
  console.log("  Stats cache: " + CACHE_DIR);
  console.log("  Cached dates are reused automatically.");
}

main().catch(function(e) { console.error("FATAL:", e); process.exit(1); });
