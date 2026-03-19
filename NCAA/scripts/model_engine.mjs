// scripts/model_engine.mjs
// ────────────────────────────────────────────────────────────────────────────
// BAYESIAN UPGRADE: projScore now returns { score, variance }.
// analyzeGame computes P(cover) from the projected distribution and uses
// probability thresholds instead of raw edge thresholds for pick logic.
//
// Backward compatible: still outputs sDiff, margin, etc. for display/grading.
// The old threshold-based logic is kept as a fallback if Kalman state is null.
//
// NCAA adaptation: no team aliases (362 D1 teams — use fuzzy matching only),
// Tournament games are neutral site — HCA is zeroed out automatically.

// higher HCA default (4.0), MIN_GP = 5, SDIFF_CAP = 12.
// ────────────────────────────────────────────────────────────────────────────

import { DEFAULT_STATS, DEFAULT_W, DEFAULT_W_VAR, BAYES_HYPER } from "./defaults.mjs";
import { isTournament } from "./sources/season_type.mjs";

export function loadDefaults() {
  return { DEFAULT_STATS, DEFAULT_W, DEFAULT_W_VAR, BAYES_HYPER };
}

export function getAvgs(H) {
  const teams = Object.values(H);
  const n = teams.length || 1;
  return {
    ts: teams.reduce((s, x) => s + x.TS, 0) / n,
    to: teams.reduce((s, x) => s + x.TO, 0) / n,
    orr: teams.reduce((s, x) => s + x.ORR, 0) / n,
  };
}


// ── Normal CDF (Abramowitz & Stegun approximation, ~1e-5 accuracy) ──────────

export function normalCDF(x) {
  const a1 = 0.254829592, a2 = -0.284496736, a3 = 1.421413741;
  const a4 = -1.453152027, a5 = 1.061405429, p = 0.3275911;
  const sign = x < 0 ? -1 : 1;
  const z = Math.abs(x) / Math.SQRT2;
  const t = 1.0 / (1.0 + p * z);
  const y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * Math.exp(-z * z);
  return 0.5 * (1.0 + sign * y);
}


// ── Team Name Resolution ────────────────────────────────────────────────────
// NCAA has 362 D1 teams — no hardcoded alias map. Fuzzy matching only.

function normKey(s) {
  return String(s || "")
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

// Collapse abbreviation dots only: "N.C. State" → "NC State", "St." → "St"
function collapseAbbr(s) {
  return s.replace(/\./g, "");
}

const TEAM_NAME_ALIASES = {};

function expandTeamName(name) {
  const n = normKey(name);
  return TEAM_NAME_ALIASES[n] || name;
}

// Safe substring check: only match if lengths are similar (within 30%)
// This prevents "arkansas" matching "kansas", "oregon" matching "oregon st", etc.
function safeFuzzy(a, b) {
  if (!a || !b) return false;
  if (a === b) return true;
  // Only allow includes() if the shorter string is at least 70% the length of the longer
  const shorter = a.length < b.length ? a : b;
  const longer  = a.length < b.length ? b : a;
  if (shorter.length / longer.length < 0.85) return false;
  return longer.includes(shorter);
}

function resolveTeam(H, name) {
  if (!H || !name) return null;
  if (H[name]) return name;
  const keys = Object.keys(H);
  const wanted = normKey(name);
  const wantedCollapsed = normKey(collapseAbbr(name));

  // Exact normKey match
  for (const k of keys) {
    if (normKey(k) === wanted) return k;
  }
  // Collapsed abbreviation match: "N.C. State" ↔ "NC State"
  for (const k of keys) {
    if (normKey(collapseAbbr(k)) === wantedCollapsed) return k;
  }
  const expanded = normKey(expandTeamName(name));
  for (const k of keys) {
    if (normKey(k) === expanded) return k;
  }
  for (const k of keys) {
    const nk = normKey(k);
    if (safeFuzzy(nk, wanted) || safeFuzzy(nk, expanded)) return k;
  }
  // "School Mascot" prefix match: odds API sends "UMBC Retrievers", cache has "UMBC"
  // Find the LONGEST matching prefix to avoid "Oregon" beating "Oregon St."
  let bestPrefix = null, bestLen = 0;
  for (const k of keys) {
    const nk = normKey(k);
    const nkc = normKey(collapseAbbr(k));
    const matchNk = wanted.startsWith(nk + " ") || wantedCollapsed.startsWith(nk + " ");
    const matchNkc = wantedCollapsed.startsWith(nkc + " ");
    if ((matchNk || matchNkc) && nk.length >= 3) {
      const len = Math.max(nk.length, nkc.length);
      if (len > bestLen) { bestLen = len; bestPrefix = k; }
    }
  }
  if (bestPrefix) return bestPrefix;
  return null;
}


// ── Team-specific Home Court Advantage ───────────────────────────────────────
// Computes per-team HCA from home/away splits: ((homeNET) - (awayNET)) / 2
// Blended 50/50 with league average to stabilize small-sample splits.
// Returns a map: { "Team Name": hca_value, ... }

export function computeTeamHCA(homeSplits, awaySplits, leagueHCA = 4.0) {
  if (!homeSplits || !awaySplits) return null;
  const hcaMap = {};
  for (const team of Object.keys(homeSplits)) {
    const h = homeSplits[team];
    const a = awaySplits[team];
    if (!h || !a || !h.GP || !a.GP || h.GP < 10 || a.GP < 10) continue;
    const homeNet = h.OFF - h.DEF;
    const awayNet = a.OFF - a.DEF;
    const rawHCA = (homeNet - awayNet) / 2;
    // Blend 50/50 with league average to prevent overfitting
    hcaMap[team] = rawHCA * 0.5 + leagueHCA * 0.5;
  }
  return hcaMap;
}


// ── Projection ──────────────────────────────────────────────────────────────
// Returns { score, variance } instead of a single number.
//
// The score is the same formula as before.
// The variance is the sum of:
//   - Kalman team uncertainty (if provided)
//   - Weight uncertainty propagated through features (if W_var provided)
//   - Residual game noise
//
// Parameters:
//   kalmanAdj: { mean, var } from kalman_state.getTeamAdj() — optional
//   W_var:     weight variances { wTS, wTO, wORR, wNET, hca } — optional
//   teamHCA:   per-team HCA map from computeTeamHCA() — optional

export function projScore(team, opp, isHome, H, a, W, kalmanAdj = null, W_var = null, residualVar = null, teamHCA = null) {
  const tKey = resolveTeam(H, team);
  const oKey = resolveTeam(H, opp);

  const t = tKey ? H[tKey] : null;
  const o = oKey ? H[oKey] : null;
  if (!t || !o) return null;

  const MIN_GP = 5;
  if ((t.GP != null && t.GP < MIN_GP) || (o.GP != null && o.GP < MIN_GP)) return null;

  const tOFF = t.OFF;
  const tDEF = t.DEF;

  // ── Point estimate (same formula as before) ───────────────────────────

  const base =
    (tOFF + o.DEF) / 2 +
    (t.TS - a.ts) * W.wTS -
    (t.TO - a.to) * W.wTO +
    (t.ORR - a.orr) * W.wORR +
    (W.wNET * 0.5) * ((tOFF - tDEF) - (o.OFF - o.DEF)) +
    W.constant;

  const pace = (((t.PACE + o.PACE) / 2) * W.paceAdj) / 100;
  const hca = isHome ? (teamHCA?.[tKey] ?? W.hca) : 0;
  let score = base * pace + hca;

  // Add Kalman adjustment if available
  if (kalmanAdj) {
    score += kalmanAdj.mean;
  }

  score = Math.round(score * 10) / 10;

  // ── Variance propagation ──────────────────────────────────────────────
  // Dynamic residualVar is total margin noise (measured from margin errors).
  // Split in half per team since analyzeGame sums home + away variance.
  // Default (BAYES_HYPER) is already calibrated as per-team value.

  let variance = residualVar != null ? residualVar / 2 : BAYES_HYPER.residualVar;

  // Kalman team uncertainty
  if (kalmanAdj) {
    variance += kalmanAdj.var;
  }

  // Weight uncertainty propagated through features
  // Var(w·x) ≈ x² · Var(w)  (diagonal approximation)
  if (W_var) {
    const dTS  = t.TS - a.ts;
    const dTO  = t.TO - a.to;
    const dORR = t.ORR - a.orr;
    const dNET = (tOFF - tDEF) - (o.OFF - o.DEF);

    const weightVar =
      (dTS * pace) ** 2  * (W_var.wTS  || 0) +
      (dTO * pace) ** 2  * (W_var.wTO  || 0) +
      (dORR * pace) ** 2 * (W_var.wORR || 0) +
      (0.5 * dNET * pace) ** 2 * (W_var.wNET || 0) +
      (isHome ? 1 : 0) * (W_var.hca || 0);

    variance += weightVar;
  }

  return { score, variance };
}


// ── Total projection (avoids double-counting) ──────────────────────────────
// OFF/DEF ratings already embed TS%, TO%, ORR effects. projScore adds those
// as separate corrections — fine for margin (cancels), bad for total (stacks).
// HCA is a margin effect (home scores more, away scores less), not a total effect.
// This function computes the total directly from the matchup without inflation.

export function projTotal(homeTeam, awayTeam, H, a, W) {
  const hKey = resolveTeam(H, homeTeam);
  const aKey = resolveTeam(H, awayTeam);
  if (!hKey || !aKey) return null;

  const h = H[hKey], aw = H[aKey];
  if (!h || !aw) return null;

  // Clean total: (hOFF + aDEF)/2 + (aOFF + hDEF)/2 = matchup-based expected points
  // No TS/TO/ORR corrections (already in OFF/DEF), no HCA (margin effect, not total)
  const totalBase = (h.OFF + aw.DEF) / 2 + (aw.OFF + h.DEF) / 2;

  // Pace correction: college pace stats may overstate effective scoring possessions.
  // Raw projTotal can overshoot actual totals. Slight correction to improve accuracy.
  // 0.991 on a ~140 avg total ≈ -1.3 pts.
  const PACE_SCORING_FACTOR = 0.991;
  const pace = (((h.PACE + aw.PACE) / 2) * (W.paceAdj || 1)) / 100 * PACE_SCORING_FACTOR;

  // No Kalman here — Kalman tracks margin drift (team beating/missing spread),
  // not total scoring. A +3 Kalman team might be winning by defense, not offense.
  // Adding Kalman to totals inflates projections and generates bad OVER picks.
  return Math.round(totalBase * pace * 10) / 10;
}



// Returns the feature vector used by self_tune for the margin regression.
// margin ≈ features · weights + baseline

export function extractMarginFeatures(homeStats, awayStats, avgStats, paceAdj, neutral = false) {
  const pace = ((homeStats.PACE + awayStats.PACE) / 2 * paceAdj) / 100;
  return {
    dTS:  (homeStats.TS - awayStats.TS) * pace,
    dTO:  -(homeStats.TO - awayStats.TO) * pace,    // negative: higher TO is bad
    dORR: (homeStats.ORR - awayStats.ORR) * pace,
    dNET: 0.5 * ((homeStats.OFF - homeStats.DEF) - (awayStats.OFF - awayStats.DEF)) * pace,
    hca:  neutral ? 0.0 : 1.0,  // zero for neutral-site tournament games
    // Baseline (not weight-dependent): ((hOFF+aDEF)/2 - (aOFF+hDEF)/2) * pace
    _baseline: ((homeStats.OFF + awayStats.DEF) / 2 - (awayStats.OFF + homeStats.DEF) / 2) * pace,
    _pace: pace,
  };
}


// ── Injury note builder (unchanged) ─────────────────────────────────────────

function buildInjuryNote(injuryAdj) {
  if (!injuryAdj) return null;
  const parts = [];
  if (injuryAdj.awayInjuries?.length) parts.push(`Away: ${injuryAdj.awayInjuries.map(i => `${i.player} (${i.status}/${i.tier})`).join(", ")}`);
  if (injuryAdj.homeInjuries?.length) parts.push(`Home: ${injuryAdj.homeInjuries.map(i => `${i.player} (${i.status}/${i.tier})`).join(", ")}`);
  return parts.length ? parts.join(" | ") : null;
}


// ── Game Analysis ───────────────────────────────────────────────────────────
// Now computes P(cover) for spread and total.
//
// New parameters:
//   kalmanState: the full kalman state object (from kalman_state.mjs) — optional
//   W_var:       weight variances — optional
//
// If kalmanState and W_var are null, falls back to the legacy threshold logic.

export function analyzeGame(g, H, a, W, injuryAdj = null, kalmanState = null, W_var = null, residualVar = null, teamHCA = null) {
  const awayKey = resolveTeam(H, g.away);
  const homeKey = resolveTeam(H, g.home);
  if (!awayKey || !homeKey) return null;

  const gg = { ...g, away: awayKey, home: homeKey };

  // Get Kalman adjustments if available
  let homeKalman = null, awayKalman = null;
  const getAdj = kalmanState?.teams
    ? (name) => {
        const t = kalmanState.teams[name];
        return t ? { mean: t.adj_mean, var: t.adj_var } : null;
      }
    : () => null;

  homeKalman = getAdj(homeKey);
  awayKalman = getAdj(awayKey);

  // Tournament games are neutral site — no home court advantage
  const neutral = isTournament(g._date);
  const homeFlag = !neutral;

  // Project scores
  const aProj = projScore(gg.away, gg.home, false, H, a, W, awayKalman, W_var, residualVar, teamHCA);
  const hProj = projScore(gg.home, gg.away, homeFlag, H, a, W, homeKalman, W_var, residualVar, teamHCA);
  if (!aProj || !hProj) return null;

  const aS = aProj.score;
  const hS = hProj.score;
  const pT = Math.round((aS + hS) * 10) / 10;

  // Clean total for over/under probability (no TS/TO/ORR double-counting, no HCA)
  const cleanTotal = projTotal(gg.home, gg.away, H, a, W) || pT;

  // ── Spread analysis ───────────────────────────────────────────────────

  const margin = hS - aS - gg.line;
  const sDiff = Math.abs(margin);
  const tDiff = Math.round((pT - gg.total) * 10) / 10;
  const cleanTDiff = Math.round((cleanTotal - gg.total) * 10) / 10;

  const absLine = Math.abs(gg.line);
  const homeFav = gg.line > 0;

  // ── Probability of covering ───────────────────────────────────────────
  // margin_mean = hS - aS - line (positive = model favors home cover)
  // margin_var  = home_var + away_var (team uncertainties are independent)
  // P(home covers) = Φ(margin_mean / sqrt(margin_var))

  const marginVar = (hProj.variance || 0) + (aProj.variance || 0);
  const marginStd = Math.sqrt(Math.max(marginVar, 1));  // floor at 1 to avoid division by zero

  const pHomeCover = normalCDF(margin / marginStd);
  const pAwayCover = 1 - pHomeCover;

  // Total: P(over) using clean total projection (no double-counting)
  const totalVar = marginVar * 1.1;  // totals slightly noisier
  const totalStd = Math.sqrt(Math.max(totalVar, 1));
  const pOver  = normalCDF(cleanTDiff / totalStd);
  const pUnder = 1 - pOver;

  // ── Pick logic ────────────────────────────────────────────────────────
  // Primary: probability-based (if Kalman state available)
  // Fallback: legacy threshold-based

  const hTeam = H[homeKey], aTeam = H[awayKey];

  let sPick = "PASS";
  let sConf = "low";
  let oPick = "PASS";
  let oConf = "low";
  let pCover = null;  // the P(cover) for the chosen side
  let pOU = null;     // the P(over/under) for the chosen side

  const useBayesian = kalmanState != null && W_var != null;

  if (useBayesian) {
    // ── Bayesian pick logic (probability-based) ─────────────────────────
    // Spread: single tier (elite was overconfident — worse than high).
    // Totals: keep elite tier (elite totals 58.6% vs high totals 49.4%).

    const probOH = W.probOUHigh  ?? 0.58;
    const probOE = W.probOUElite ?? 0.64;

    // Spread — pCover is sole gatekeeper (sDiff redundant above 0.60).
    //   Fav line cap 8. Dogs: no line cap.
    const P_COVER_THRESH = 0.60;
    const FAV_LINE_CAP = 8;
    const bestSpreadP = Math.max(pHomeCover, pAwayCover);
    const spreadSide  = pHomeCover >= pAwayCover ? "home" : "away";

    const pickedSideIsDog = spreadSide === "home"
      ? gg.line < 0
      : gg.line > 0;

    const lineOK = pickedSideIsDog ? true : absLine <= FAV_LINE_CAP;

    if (bestSpreadP >= P_COVER_THRESH && lineOK && absLine > 0) {
      if (spreadSide === "home") {
        sPick = homeFav ? `${gg.home} -${absLine}` : `${gg.home} +${absLine}`;
      } else {
        sPick = homeFav ? `${gg.away} +${absLine}` : `${gg.away} -${absLine}`;
      }
      sConf = "elite";
      pCover = bestSpreadP;
    }

    // Total picks disabled — edge not holding (53% last 2 weeks).
    // Keeping spread-only for cleaner signal.

  } else {
    // ── Legacy threshold-based logic (fallback) ─────────────────────────

    if (sDiff >= 3 && sDiff <= 9 && absLine <= 10 && sDiff >= W.sprHigh) {
      sPick = margin > 0
        ? (homeFav ? `${gg.home} -${absLine}` : `${gg.home} +${absLine}`)
        : (homeFav ? `${gg.away} +${absLine}` : `${gg.away} -${absLine}`);
      sConf = "elite";
    }

    if (Math.abs(cleanTDiff) >= W.ouHigh) {
      const ouEliteAdj  = W.ouHigh  + (W.ouEliteBump ?? 3);
      if (Math.abs(cleanTDiff) >= ouEliteAdj) {
        oPick = cleanTDiff > 0 ? "OVER" : "UNDER";
        oConf = "elite";
      }
    }
  }

  // ── Feature deltas for self_tune ──────────────────────────────────────

  const _features = {
    dTS:  hTeam.TS  - aTeam.TS,
    dTO:  hTeam.TO  - aTeam.TO,
    dORR: hTeam.ORR - aTeam.ORR,
    dNET: (hTeam.OFF - hTeam.DEF) - (aTeam.OFF - aTeam.DEF),
    avgPace: (hTeam.PACE + aTeam.PACE) / 2,
  };

  // Margin features for Bayesian weight update
  const _marginFeatures = extractMarginFeatures(hTeam, aTeam, a, W.paceAdj, neutral);

  return {
    ...gg,
    aS,
    hS,
    pT,
    margin: Math.round(margin * 10) / 10,
    sDiff:  Math.round(sDiff * 10) / 10,
    tDiff: cleanTDiff,
    sPick,
    sConf,
    oPick,
    oConf,
    injuryNote: injuryAdj ? buildInjuryNote(injuryAdj) : null,

    // Bayesian outputs
    pHomeCover: Math.round(pHomeCover * 1000) / 1000,
    pAwayCover: Math.round(pAwayCover * 1000) / 1000,
    pOver:      Math.round(pOver * 1000) / 1000,
    pUnder:     Math.round(pUnder * 1000) / 1000,
    pCover:     pCover ? Math.round(pCover * 1000) / 1000 : null,
    pOU:        pOU ? Math.round(pOU * 1000) / 1000 : null,
    marginVar:  Math.round(marginVar * 10) / 10,
    marginStd:  Math.round(marginStd * 10) / 10,

    // Features for tuning
    _features,
    _marginFeatures,
  };
}
