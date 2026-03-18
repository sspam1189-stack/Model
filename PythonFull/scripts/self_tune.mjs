// scripts/self_tune.mjs
// ────────────────────────────────────────────────────────────────────────────
// BAYESIAN UPGRADE: Two learning signals, now principled.
//
//   SIGNAL 1: BAYESIAN WEIGHT UPDATE
//     Replaces gradient descent. Maintains a mean + variance for each weight.
//     After each game, the weight posterior is updated via a diagonal Kalman
//     filter (equivalent to diagonal-covariance Bayesian linear regression).
//     Weights with high uncertainty move more; confident weights move less.
//
//   SIGNAL 2: PROBABILITY THRESHOLD TUNING
//     Replaces sprHigh/ouHigh. Adjusts probHigh/probElite based on ATS
//     profitability. If we're winning at >55%, relax thresholds (more picks).
//     If losing at <48%, tighten thresholds (fewer, more confident picks).
//
//   SIGNAL 3: CONSTANT + PACE (gradient descent, unchanged)
//     These are non-linear in the projection formula (constant affects total
//     not margin, paceAdj is a multiplier). Gradient descent is fine for them.
// ────────────────────────────────────────────────────────────────────────────

import { DEFAULT_W, DEFAULT_W_VAR, BAYES_HYPER } from "./defaults.mjs";

// ── Helpers ─────────────────────────────────────────────────────────────────

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

function r4(x) { return Math.round(x * 10000) / 10000; }
function r3(x) { return Math.round(x * 1000) / 1000; }
function clamp(x, min, max) { return Math.max(min, Math.min(max, x)); }


// ── Main Tuner ──────────────────────────────────────────────────────────────
//
// Parameters:
//   currentW:        current weight means { wTS, wTO, wORR, wNET, hca, constant, paceAdj, ... }
//   currentWVar:     current weight variances { wTS, wTO, wORR, wNET, hca, constant }
//   completedRows:   array of graded game objects with _marginFeatures, homeScore, awayScore
//
// Returns: { W, W_var } — updated weight means and variances

export function tuneWeights(currentW, currentWVar, completedRows) {
  const W     = { ...DEFAULT_W, ...currentW };
  const W_var = { ...DEFAULT_W_VAR, ...currentWVar };

  // Replace any null/NaN with defaults
  for (const k of Object.keys(W)) {
    if (W[k] == null || Number.isNaN(W[k])) W[k] = DEFAULT_W[k] ?? W[k];
  }
  for (const k of Object.keys(W_var)) {
    if (W_var[k] == null || Number.isNaN(W_var[k])) W_var[k] = DEFAULT_W_VAR[k] ?? W_var[k];
  }

  const marginNoise = BAYES_HYPER.marginNoise;
  const minVar = BAYES_HYPER.minWeightVar;
  const maxVar = BAYES_HYPER.maxWeightVar;

  // ====================================================================
  // SIGNAL 1: Bayesian weight update (replaces gradient descent)
  // ====================================================================
  //
  // For each graded game with margin features, update the weight posterior.
  //
  // Model: actual_margin = baseline + w·x + noise
  //   where x = [dTS*p, -dTO*p, dORR*p, 0.5*dNET*p, hca_indicator]
  //   and baseline = ((hOFF+aDEF)/2 - (aOFF+hDEF)/2) * pace
  //
  // Diagonal Kalman update:
  //   prediction = baseline + dot(W_mean, x)
  //   error = actual_margin - prediction
  //   S = sum(x_i² · var_i) + marginNoise
  //   K_i = var_i · x_i / S
  //   W_mean_i += K_i · error
  //   W_var_i  *= (1 - K_i · x_i)

  const WEIGHT_KEYS = ["wTS", "wTO", "wORR", "wNET", "hca"];
  let nBayes = 0;

  for (const r of completedRows) {
    if (!Number.isFinite(r.homeScore) || !Number.isFinite(r.awayScore)) continue;

    const mf = r._marginFeatures;
    if (!mf) continue;

    // Feature vector (same order as WEIGHT_KEYS)
    const x = [mf.dTS, mf.dTO, mf.dORR, mf.dNET, mf.hca];

    // Prediction from current weights
    const baseline = mf._baseline || 0;
    let prediction = baseline;
    for (let i = 0; i < WEIGHT_KEYS.length; i++) {
      prediction += W[WEIGHT_KEYS[i]] * x[i];
    }
    // Add HCA (already in the baseline via projScore, but in the margin context
    // it's part of the weight vector)
    // Actually hca is x[4]=1.0 and W.hca is the weight, so it's included above.

    const actualMargin = r.homeScore - r.awayScore;
    const error = actualMargin - prediction;

    // Recency weighting: down-weight older games
    const recencyW = Number.isFinite(r._recencyWeight) ? r._recencyWeight : 1.0;
    const effectiveNoise = marginNoise / recencyW;  // lower noise = more influence

    // Innovation variance
    let S = effectiveNoise;
    for (let i = 0; i < WEIGHT_KEYS.length; i++) {
      S += x[i] * x[i] * W_var[WEIGHT_KEYS[i]];
    }

    // Update each weight
    for (let i = 0; i < WEIGHT_KEYS.length; i++) {
      const k = WEIGHT_KEYS[i];
      const K = W_var[k] * x[i] / S;

      W[k]     = r3(W[k] + K * error);
      // HCA is well-studied — use a higher variance floor so it moves slower
      const floor = k === "hca" ? 0.2 : minVar;
      W_var[k] = r4(clamp(W_var[k] * (1 - K * x[i]), floor, maxVar));
    }

    nBayes++;
  }

  // Clamp weight means to reasonable ranges
  W.wTS  = clamp(W.wTS,  0, 5);
  W.wTO  = clamp(W.wTO,  0, 5);
  W.wORR = clamp(W.wORR, 0, 5);
  W.wNET = clamp(W.wNET, 0, 5);
  W.hca  = clamp(W.hca,  1.5, 3.5);  // NBA HCA is well-studied: 2-3 pts. Prevent wild swings.

  if (nBayes > 0) {
    console.log(`  [self_tune] Bayesian update on ${nBayes} games →` +
      ` wTS=${W.wTS} (σ²=${W_var.wTS.toFixed(3)})` +
      ` wTO=${W.wTO} (σ²=${W_var.wTO.toFixed(3)})` +
      ` wNET=${W.wNET} (σ²=${W_var.wNET.toFixed(3)})` +
      ` hca=${W.hca} (σ²=${W_var.hca.toFixed(3)})`
    );
  }


  // ====================================================================
  // SIGNAL 2: Profitability → probability threshold tuning
  // ====================================================================
  //
  // Single tier (elite removed — calibration showed it was overconfident).
  // Adjust probHigh based on ATS profitability of all actionable picks.

  let sprW = 0, sprL = 0, ouEW = 0, ouEL = 0;

  for (const r of completedRows) {
    if (!Number.isFinite(r.homeScore) || !Number.isFinite(r.awayScore)) continue;

    if (r.sPick && r.sPick !== "PASS" && r.sConf === "high") {
      const res = r.sResult || gradeSpread(r);
      if (res === "WIN") sprW++;
      else if (res === "LOSS") sprL++;
    }

    if (r.oPick && r.oPick !== "PASS" && r.oConf === "elite") {
      const res = r.oResult || gradeTotal(r);
      if (res === "WIN") ouEW++;
      else if (res === "LOSS") ouEL++;
    }
  }

  const MIN_SAMPLE = 10;
  const threshStep = 0.008;  // probability step (~0.8% per adjustment)

  // Spread probability threshold (single tier)
  if (sprW + sprL >= MIN_SAMPLE) {
    const sprPct = sprW / (sprW + sprL);
    if (sprPct > 0.58) {
      W.probHigh = r3(Math.max(0.52, W.probHigh - threshStep * 0.67));
      console.log(`  [self_tune] Spread ATS ${(sprPct*100).toFixed(0)}% (${sprW}-${sprL}) > 58% → probHigh down ${W.probHigh}`);
    } else if (sprPct < 0.52) {
      W.probHigh = r3(Math.min(0.65, W.probHigh + threshStep));
      console.log(`  [self_tune] Spread ATS ${(sprPct*100).toFixed(0)}% (${sprW}-${sprL}) < 52% → probHigh up ${W.probHigh}`);
    } else {
      console.log(`  [self_tune] Spread ATS ${(sprPct*100).toFixed(0)}% (${sprW}-${sprL}) — probHigh holds at ${W.probHigh}`);
    }
  } else {
    console.log(`  [self_tune] Spread: only ${sprW + sprL} graded picks (need ${MIN_SAMPLE}) — probHigh unchanged`);
  }

  // Total: elite threshold only (high totals removed — not profitable)
  if (ouEW + ouEL >= MIN_SAMPLE) {
    const pct = ouEW / (ouEW + ouEL);
    if (pct > 0.58) {
      W.probOUElite = r3(Math.max(0.59, W.probOUElite - threshStep * 0.67));
      console.log(`  [self_tune] Total ELITE ${(pct*100).toFixed(0)}% (${ouEW}-${ouEL}) > 58% → probOUElite down ${W.probOUElite}`);
    } else if (pct < 0.52) {
      W.probOUElite = r3(Math.min(0.80, W.probOUElite + threshStep));
      console.log(`  [self_tune] Total ELITE ${(pct*100).toFixed(0)}% (${ouEW}-${ouEL}) < 52% → probOUElite up ${W.probOUElite}`);
    } else {
      console.log(`  [self_tune] Total ELITE ${(pct*100).toFixed(0)}% (${ouEW}-${ouEL}) — probOUElite holds at ${W.probOUElite}`);
    }
  } else {
    console.log(`  [self_tune] Total ELITE: only ${ouEW + ouEL} graded picks (need ${MIN_SAMPLE}) — probOUElite unchanged`);
  }

  // Also keep legacy thresholds in sync (for backward compat / display)
  // sprHigh/ouHigh still used by backfill and recalculate
  const ouW = ouEW, ouL = ouEL;
  if (sprW + sprL >= MIN_SAMPLE) {
    const sprPct = sprW / (sprW + sprL);
    if (sprPct > 0.58) W.sprHigh = r3(Math.max(2.5, W.sprHigh - 0.1));
    else if (sprPct < 0.52) W.sprHigh = r3(Math.min(8, W.sprHigh + 0.15));
  }
  if (ouW + ouL >= MIN_SAMPLE) {
    const ouPct = ouW / (ouW + ouL);
    if (ouPct > 0.58) W.ouHigh = r3(Math.max(3, W.ouHigh - 0.1));
    else if (ouPct < 0.52) W.ouHigh = r3(Math.min(10, W.ouHigh + 0.15));
  }


  // ====================================================================
  // SIGNAL 3: Constant + paceAdj (gradient descent, unchanged)
  // ====================================================================
  //
  // These affect the total (hS + aS) not the margin, and paceAdj is a
  // multiplier, so they don't fit cleanly into the linear Bayesian update.
  // Gradient descent is fine for these two parameters.

  const lr = 0.0005;
  const maxStep = 0.08;

  let gConstant = 0, gPaceAdj = 0, n = 0;

  for (const r of completedRows) {
    if (!Number.isFinite(r.homeScore) || !Number.isFinite(r.awayScore)) continue;
    if (!Number.isFinite(r.pT)) continue;

    const w = Number.isFinite(r._recencyWeight) ? r._recencyWeight : 1.0;
    const actualTotal = r.homeScore + r.awayScore;
    const errTotal = r.pT - actualTotal;

    gConstant += w * errTotal * 2;
    gPaceAdj  += w * errTotal * (r.pT / Math.max(W.paceAdj ?? 1, 0.1)) * 0.01;
    n += w;
  }

  if (n > 0) {
    gConstant /= n;
    gPaceAdj  /= n;

    function clampStep(x) { return Math.max(-maxStep, Math.min(maxStep, x)); }

    W.constant = r3(W.constant - clampStep(lr * gConstant));
    W.paceAdj  = r3(clamp(W.paceAdj - clampStep(lr * gPaceAdj), 0.5, 1.5));
  }


  // ── Final guard ─────────────────────────────────────────────────────────
  for (const k of Object.keys(W)) {
    if (W[k] == null || Number.isNaN(W[k])) W[k] = DEFAULT_W[k] ?? 1;
  }
  for (const k of Object.keys(W_var)) {
    if (W_var[k] == null || Number.isNaN(W_var[k])) W_var[k] = DEFAULT_W_VAR[k] ?? 1;
  }

  return { W, W_var };
}


// ── Dynamic Residual Variance ────────────────────────────────────────────────
// Computes the actual prediction error variance from historical results.
// This replaces the hardcoded BAYES_HYPER.residualVar with reality.
//
// residualVar = Var(actual_margin - projected_margin)
//
// Uses all graded games from store.runs (excluding burn-in).
// Falls back to BAYES_HYPER.residualVar if insufficient data.

export function computeResidualVar(runs) {
  const MIN_GAMES = 30;  // need enough data for stable estimate
  const errors = [];

  for (const r of runs) {
    if (r.burnIn) continue;
    for (const g of r.games || []) {
      if (!Number.isFinite(g.homeScore) || !Number.isFinite(g.awayScore)) continue;
      if (!Number.isFinite(g.margin) || !Number.isFinite(g.line)) continue;

      const actualEdge = (g.homeScore - g.awayScore) - g.line;
      const projEdge   = g.margin;  // hS - aS - line
      errors.push(actualEdge - projEdge);
    }
  }

  if (errors.length < MIN_GAMES) {
    console.log(`  [self_tune] residualVar: only ${errors.length} games (need ${MIN_GAMES}) — using default ${BAYES_HYPER.residualVar}`);
    return BAYES_HYPER.residualVar;
  }

  const mean = errors.reduce((s, x) => s + x, 0) / errors.length;
  const variance = errors.reduce((s, x) => s + (x - mean) ** 2, 0) / errors.length;
  const rounded = Math.round(variance * 10) / 10;

  console.log(`  [self_tune] residualVar: ${rounded} (std=${Math.sqrt(variance).toFixed(1)}) from ${errors.length} games`);
  return rounded;
}