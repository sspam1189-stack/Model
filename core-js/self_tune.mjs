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

export function createSelfTune(options = {}) {
  const {
    DEFAULT_W,
    DEFAULT_W_VAR,
    BAYES_HYPER,
    hcaClamp = [1.5, 3.5],
    hcaVarFloor = 0.2,
  } = options;

  if (!DEFAULT_W || !DEFAULT_W_VAR || !BAYES_HYPER) {
    throw new Error("createSelfTune requires DEFAULT_W, DEFAULT_W_VAR, and BAYES_HYPER");
  }

  const hcaMin = hcaClamp[0];
  const hcaMax = hcaClamp[1];

  function tuneWeights(currentW, currentWVar, completedRows) {
    const W     = { ...DEFAULT_W, ...currentW };
    const W_var = { ...DEFAULT_W_VAR, ...currentWVar };

    for (const k of Object.keys(W)) {
      if (W[k] == null || Number.isNaN(W[k])) W[k] = DEFAULT_W[k] ?? W[k];
    }
    for (const k of Object.keys(W_var)) {
      if (W_var[k] == null || Number.isNaN(W_var[k])) W_var[k] = DEFAULT_W_VAR[k] ?? W_var[k];
    }

    const marginNoise = BAYES_HYPER.marginNoise;
    const minVar = BAYES_HYPER.minWeightVar;
    const maxVar = BAYES_HYPER.maxWeightVar;

    const WEIGHT_KEYS = ["wTS", "wTO", "wORR", "wDEF", "hca"];
    let nBayes = 0;

    for (const r of completedRows) {
      if (!Number.isFinite(r.homeScore) || !Number.isFinite(r.awayScore)) continue;

      const mf = r._marginFeatures;
      if (!mf) continue;

      const x = [mf.dTS, mf.dTO, mf.dORR, mf.dDEF ?? 0, mf.hca];

      const baseline = mf._baseline || 0;
      let prediction = baseline;
      for (let i = 0; i < WEIGHT_KEYS.length; i++) {
        prediction += W[WEIGHT_KEYS[i]] * x[i];
      }

      const actualMargin = r.homeScore - r.awayScore;
      const error = actualMargin - prediction;

      const recencyW = Number.isFinite(r._recencyWeight) ? r._recencyWeight : 1.0;
      const effectiveNoise = marginNoise / recencyW;

      let S = effectiveNoise;
      for (let i = 0; i < WEIGHT_KEYS.length; i++) {
        S += x[i] * x[i] * W_var[WEIGHT_KEYS[i]];
      }

      for (let i = 0; i < WEIGHT_KEYS.length; i++) {
        const k = WEIGHT_KEYS[i];
        const K = W_var[k] * x[i] / S;

        W[k] = r3(W[k] + K * error);
        const floor = k === "hca" ? hcaVarFloor : minVar;
        W_var[k] = r4(clamp(W_var[k] * (1 - K * x[i]), floor, maxVar));
      }

      nBayes++;
    }

    W.wTS  = clamp(W.wTS,  0, 5);
    W.wTO  = clamp(W.wTO,  0, 5);
    W.wORR = clamp(W.wORR, 0, 5);
    W.wDEF = clamp(W.wDEF, 0, 5);
    W.hca  = clamp(W.hca,  hcaMin, hcaMax);

    if (nBayes > 0) {
      console.log(`  [self_tune] Bayesian update on ${nBayes} games ->` +
        ` wTS=${W.wTS} (var=${W_var.wTS.toFixed(3)})` +
        ` wTO=${W.wTO} (var=${W_var.wTO.toFixed(3)})` +
        ` wORR=${W.wORR} (var=${W_var.wORR.toFixed(3)})` +
        ` wDEF=${W.wDEF} (var=${W_var.wDEF.toFixed(3)})` +
        ` hca=${W.hca} (var=${W_var.hca.toFixed(3)})`
      );
    }

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
    const threshStep = 0.008;

    if (sprW + sprL >= MIN_SAMPLE) {
      const sprPct = sprW / (sprW + sprL);
      if (sprPct > 0.58) {
        W.probHigh = r3(Math.max(0.52, W.probHigh - threshStep * 0.67));
        console.log(`  [self_tune] Spread ATS ${(sprPct*100).toFixed(0)}% (${sprW}-${sprL}) > 58% -> probHigh down ${W.probHigh}`);
      } else if (sprPct < 0.52) {
        W.probHigh = r3(Math.min(0.65, W.probHigh + threshStep));
        console.log(`  [self_tune] Spread ATS ${(sprPct*100).toFixed(0)}% (${sprW}-${sprL}) < 52% -> probHigh up ${W.probHigh}`);
      } else {
        console.log(`  [self_tune] Spread ATS ${(sprPct*100).toFixed(0)}% (${sprW}-${sprL}) - probHigh holds at ${W.probHigh}`);
      }
    } else {
      console.log(`  [self_tune] Spread: only ${sprW + sprL} graded picks (need ${MIN_SAMPLE}) - probHigh unchanged`);
    }

    if (ouEW + ouEL >= MIN_SAMPLE) {
      const pct = ouEW / (ouEW + ouEL);
      if (pct > 0.58) {
        W.probOUElite = r3(Math.max(0.59, W.probOUElite - threshStep * 0.67));
        console.log(`  [self_tune] Total ELITE ${(pct*100).toFixed(0)}% (${ouEW}-${ouEL}) > 58% -> probOUElite down ${W.probOUElite}`);
      } else if (pct < 0.52) {
        W.probOUElite = r3(Math.min(0.80, W.probOUElite + threshStep));
        console.log(`  [self_tune] Total ELITE ${(pct*100).toFixed(0)}% (${ouEW}-${ouEL}) < 52% -> probOUElite up ${W.probOUElite}`);
      } else {
        console.log(`  [self_tune] Total ELITE ${(pct*100).toFixed(0)}% (${ouEW}-${ouEL}) - probOUElite holds at ${W.probOUElite}`);
      }
    } else {
      console.log(`  [self_tune] Total ELITE: only ${ouEW + ouEL} graded picks (need ${MIN_SAMPLE}) - probOUElite unchanged`);
    }

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

    for (const k of Object.keys(W)) {
      if (W[k] == null || Number.isNaN(W[k])) W[k] = DEFAULT_W[k] ?? 1;
    }
    for (const k of Object.keys(W_var)) {
      if (W_var[k] == null || Number.isNaN(W_var[k])) W_var[k] = DEFAULT_W_VAR[k] ?? 1;
    }

    return { W, W_var };
  }

  function computeResidualVar(runs) {
    const MIN_GAMES = 30;
    const errors = [];

    for (const r of runs) {
      if (r.burnIn) continue;
      for (const g of r.games || []) {
        if (!Number.isFinite(g.homeScore) || !Number.isFinite(g.awayScore)) continue;
        if (!Number.isFinite(g.margin) || !Number.isFinite(g.line)) continue;

        const actualEdge = (g.homeScore - g.awayScore) + g.line;
        const projEdge   = g.margin;
        errors.push(actualEdge - projEdge);
      }
    }

    if (errors.length < MIN_GAMES) {
      console.log(`  [self_tune] residualVar: only ${errors.length} games (need ${MIN_GAMES}) - using default ${BAYES_HYPER.residualVar}`);
      return BAYES_HYPER.residualVar;
    }

    const mean = errors.reduce((s, x) => s + x, 0) / errors.length;
    const variance = errors.reduce((s, x) => s + (x - mean) ** 2, 0) / errors.length;

    const rounded = Math.round(variance * 10) / 10;

    console.log(`  [self_tune] residualVar: ${rounded} (std=${Math.sqrt(variance).toFixed(1)}) from ${errors.length} games`);
    return rounded;
  }

  return { tuneWeights, computeResidualVar };
}
