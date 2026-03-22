// Test different CALIB_FACTOR values by running recalculate logic in-memory
// Reports record + units for each factor without saving

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
require('dotenv').config();

import { loadDefaults, getAvgs, analyzeGame } from './model_engine.mjs';
import { tuneWeights } from './self_tune.mjs';
import { initializeKalman, applyDailyDrift, batchUpdate } from './kalman_state.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DATA_DIR = path.join(__dirname, '..', 'data');
const CACHE_DIR = path.join(DATA_DIR, 'stats_cache');
const HISTORY_PATH = path.join(DATA_DIR, 'history_pre_recalc_2026-03-15T15-00-39.json');
const KALMAN_PATH = path.join(DATA_DIR, 'kalman_state.json');

const BURN_IN_DAYS = 20;

function gradeSpread(g) {
  if (!g.sPick || g.sPick === 'PASS') return null;
  if (!Number.isFinite(g.homeScore) || !Number.isFinite(g.awayScore)) return null;
  const m = g.sPick.match(/(.+?)\s+([+-])(\d+(?:\.\d+)?)/);
  if (!m) return null;
  const spread = m[2] === '-' ? -parseFloat(m[3]) : parseFloat(m[3]);
  const chosenIsHome = m[1].trim() === g.home;
  const margin = chosenIsHome ? g.homeScore - g.awayScore : g.awayScore - g.homeScore;
  const cover = margin + spread;
  return cover > 0 ? 'WIN' : cover < 0 ? 'LOSS' : 'PUSH';
}

function getStatsForDate(dateStr) {
  const cf = path.join(CACHE_DIR, dateStr + '.json');
  if (!fs.existsSync(cf)) return null;
  const raw = JSON.parse(fs.readFileSync(cf, 'utf8'));
  if (raw.season) return raw;
  return { season: raw, last10: null, home: null, away: null };
}

// Compute residualVar with a given factor
function computeResidualVarWithFactor(runs, factor) {
  const errors = [];
  for (const r of runs) {
    if (r.burnIn) continue;
    for (const g of r.games || []) {
      if (!Number.isFinite(g.homeScore) || !Number.isFinite(g.awayScore)) continue;
      if (!Number.isFinite(g.margin) || !Number.isFinite(g.line)) continue;
      const actualEdge = (g.homeScore - g.awayScore) - g.line;
      const projEdge = g.margin;
      errors.push(actualEdge - projEdge);
    }
  }
  if (errors.length < 30) return 130; // default
  const mean = errors.reduce((s, x) => s + x, 0) / errors.length;
  const variance = errors.reduce((s, x) => s + (x - mean) ** 2, 0) / errors.length;
  return variance * factor;
}

async function testFactor(factor, store) {
  const runs = JSON.parse(JSON.stringify(store.runs || [])); // deep copy
  const defaults = loadDefaults();
  const W = { ...defaults.DEFAULT_W };
  const W_var = { ...defaults.DEFAULT_W_VAR };

  const residualVar = computeResidualVarWithFactor(runs, factor);

  const statsMap = new Map();
  const uniqueDates = [...new Set(runs.map(r => r.date))].sort();
  for (const d of uniqueDates) {
    const s = getStatsForDate(d);
    if (s) statsMap.set(d, s);
  }

  let kalmanState = null;
  let prevDate = null;
  let dateIndex = 0;
  let prevGraded = [];
  let totalW = 0, totalL = 0, totalPicks = 0;

  for (const run of runs) {
    const enhanced = statsMap.get(run.date);
    if (!enhanced) continue;

    if (!kalmanState) {
      kalmanState = initializeKalman(enhanced.season);
      kalmanState.lastDriftDate = run.date;
    }

    if (run.date !== prevDate) {
      applyDailyDrift(kalmanState, run.date);
      prevDate = run.date;
      dateIndex++;

      const wasInBurnIn = dateIndex - 1 <= BURN_IN_DAYS;
      if (!wasInBurnIn && prevGraded.length) {
        const { W: tunedW, W_var: tunedWVar } = tuneWeights(W, W_var, prevGraded);
        Object.assign(W, tunedW);
        Object.assign(W_var, tunedWVar);
      }
      prevGraded = [];
    }
    const inBurnIn = dateIndex <= BURN_IN_DAYS;

    const gameStats_base = enhanced.season;
    const gradedThisDate = [];

    for (const g of run.games || []) {
      if (g.status === 'MISSING_ODDS' || g.status === 'SKIPPED') continue;
      if (typeof g.line !== 'number' || typeof g.total !== 'number') continue;

      const gameStats = { ...gameStats_base };
      const gameAvgs = getAvgs(gameStats);

      const result = analyzeGame(
        { away: g.away, home: g.home, line: g.line, total: g.total },
        gameStats, gameAvgs, W, null, kalmanState, W_var, residualVar
      );

      if (!result) continue;

      result.awayScore = g.awayScore;
      result.homeScore = g.homeScore;

      if (Number.isFinite(g.homeScore) && Number.isFinite(g.awayScore)) {
        result.sResult = gradeSpread(result);
        if (!inBurnIn && result.sResult) {
          if (result.sResult === 'WIN') totalW++;
          if (result.sResult === 'LOSS') totalL++;
          if (result.sResult === 'WIN' || result.sResult === 'LOSS') totalPicks++;
        }
        gradedThisDate.push({ ...result, ...g, sPick: result.sPick, sResult: result.sResult, margin: result.margin });
      }
    }

    if (gradedThisDate.length) {
      batchUpdate(kalmanState, gradedThisDate);
    }
    prevGraded = gradedThisDate;
  }

  const pct = totalPicks > 0 ? (totalW / totalPicks * 100).toFixed(1) : 'N/A';
  const units = (totalW * (100/110) - totalL).toFixed(1);
  return { factor, totalW, totalL, totalPicks, pct, units };
}

async function main() {
  // Use backup (pre-recalc) history
  let histPath = HISTORY_PATH;
  if (!fs.existsSync(histPath)) {
    histPath = path.join(DATA_DIR, 'history.json');
  }
  const store = JSON.parse(fs.readFileSync(histPath, 'utf8'));
  console.log(`Testing factors on ${(store.runs||[]).length} runs from ${path.basename(histPath)}\n`);

  console.log('Factor | Record      | Win%  | Units');
  console.log('-------|-------------|-------|------');

  for (const factor of [1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.4]) {
    // Need to reset kalman file each time
    if (fs.existsSync(KALMAN_PATH)) {
      fs.unlinkSync(KALMAN_PATH);
    }
    const r = await testFactor(factor, store);
    const mark = parseFloat(r.units) > 40 ? ' ← BEST' : '';
    console.log(`${r.factor.toFixed(1).padEnd(7)}| ${(r.totalW+'-'+r.totalL).padEnd(12)}| ${r.pct.padEnd(6)}| ${r.units}${mark}`);
  }
}

main().catch(e => { console.error(e); process.exit(1); });
