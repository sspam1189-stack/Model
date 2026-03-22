// scripts/model_engine.mjs
// ────────────────────────────────────────────────────────────────────────────
// BAYESIAN UPGRADE: projScore now returns { score, variance }.
// analyzeGame computes P(cover) from the projected distribution and uses
// probability thresholds instead of raw edge thresholds for pick logic.
//
// Backward compatible: still outputs sDiff, margin, etc. for display/grading.
// The old threshold-based logic is kept as a fallback if Kalman state is null.
// ────────────────────────────────────────────────────────────────────────────

import { DEFAULT_STATS, DEFAULT_W, DEFAULT_W_VAR, BAYES_HYPER } from "./defaults.mjs";

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


// ── Team Name Resolution (unchanged) ────────────────────────────────────────

function normKey(s) {
  return String(s || "")
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

const TEAM_NAME_ALIASES = {
  "la lakers":             "Los Angeles Lakers",
  "lakers":                "Los Angeles Lakers",
  "la clippers":           "LA Clippers",
  "los angeles clippers":  "LA Clippers",
  "clippers":              "Los Angeles Clippers",
  "golden state":          "Golden State Warriors",
  "warriors":              "Golden State Warriors",
  "oklahoma city":         "Oklahoma City Thunder",
  "thunder":               "Oklahoma City Thunder",
  "new orleans":           "New Orleans Pelicans",
  "pelicans":              "New Orleans Pelicans",
  "new york":              "New York Knicks",
  "knicks":                "New York Knicks",
  "san antonio":           "San Antonio Spurs",
  "spurs":                 "San Antonio Spurs",
  "portland":              "Portland Trail Blazers",
  "trail blazers":         "Portland Trail Blazers",
  "philadelphia":          "Philadelphia 76ers",
  "76ers":                 "Philadelphia 76ers",
  "sixers":                "Philadelphia 76ers",
  "minnesota":             "Minnesota Timberwolves",
  "timberwolves":          "Minnesota Timberwolves",
  "wolves":                "Minnesota Timberwolves",
  "memphis":               "Memphis Grizzlies",
  "grizzlies":             "Memphis Grizzlies",
  "charlotte":             "Charlotte Hornets",
  "hornets":               "Charlotte Hornets",
  "indiana":               "Indiana Pacers",
  "pacers":                "Indiana Pacers",
  "washington":            "Washington Wizards",
  "wizards":               "Washington Wizards",
  "orlando":               "Orlando Magic",
  "magic":                 "Orlando Magic",
  "miami":                 "Miami Heat",
  "heat":                  "Miami Heat",
  "atlanta":               "Atlanta Hawks",
  "hawks":                 "Atlanta Hawks",
  "chicago":               "Chicago Bulls",
  "bulls":                 "Chicago Bulls",
  "detroit":               "Detroit Pistons",
  "pistons":               "Detroit Pistons",
  "cleveland":             "Cleveland Cavaliers",
  "cavaliers":             "Cleveland Cavaliers",
  "cavs":                  "Cleveland Cavaliers",
  "toronto":               "Toronto Raptors",
  "raptors":               "Toronto Raptors",
  "brooklyn":              "Brooklyn Nets",
  "nets":                  "Brooklyn Nets",
  "boston":                "Boston Celtics",
  "celtics":               "Boston Celtics",
  "milwaukee":             "Milwaukee Bucks",
  "bucks":                 "Milwaukee Bucks",
  "denver":                "Denver Nuggets",
  "nuggets":               "Denver Nuggets",
  "utah":                  "Utah Jazz",
  "jazz":                  "Utah Jazz",
  "phoenix":               "Phoenix Suns",
  "suns":                  "Phoenix Suns",
  "sacramento":            "Sacramento Kings",
  "kings":                 "Sacramento Kings",
  "dallas":                "Dallas Mavericks",
  "mavericks":             "Dallas Mavericks",
  "mavs":                  "Dallas Mavericks",
  "houston":               "Houston Rockets",
  "rockets":               "Houston Rockets",
};

function expandTeamName(name) {
  const n = normKey(name);
  return TEAM_NAME_ALIASES[n] || name;
}

function resolveTeam(H, name) {
  if (!H || !name) return null;
  if (H[name]) return name;
  const keys = Object.keys(H);
  const wanted = normKey(name);

  for (const k of keys) {
    if (normKey(k) === wanted) return k;
  }
  const expanded = normKey(expandTeamName(name));
  for (const k of keys) {
    if (normKey(k) === expanded) return k;
  }
  for (const k of keys) {
    const nk = normKey(k);
    if (nk.includes(wanted) || wanted.includes(nk) || nk.includes(expanded) || expanded.includes(nk)) return k;
  }
  return null;
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

export function projScore(team, opp, isHome, H, a, W, kalmanAdj = null, W_var = null) {
  const tKey = resolveTeam(H, team);
  const oKey = resolveTeam(H, opp);

  const t = tKey ? H[tKey] : null;
  const o = oKey ? H[oKey] : null;
  if (!t || !o) return null;

  const MIN_GP = 15;
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
  let score = base * pace + (isHome ? W.hca : 0);

  // Add Kalman adjustment if available
  if (kalmanAdj) {
    score += kalmanAdj.mean;
  }

  score = Math.round(score * 10) / 10;

  // ── Variance propagation ──────────────────────────────────────────────

  let variance = BAYES_HYPER.residualVar;  // baseline irreducible noise

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

  // Pace correction: NBA.com PACE overstates effective scoring possessions
  // (intentional fouls, garbage-time at reduced efficiency, transition plays).
  // Raw projTotal overshoots actual totals by ~3 pts. Optimal correction is ~2 pts
  // (slight over-projection improves pick selection by filtering weak OVERs).
  // 0.991 on a ~230 avg total ≈ -2.1 pts.
  const PACE_SCORING_FACTOR = 0.991;
  const pace = (((h.PACE + aw.PACE) / 2) * (W.paceAdj || 1)) / 100 * PACE_SCORING_FACTOR;

  // No Kalman here — Kalman tracks margin drift (team beating/missing spread),
  // not total scoring. A +3 Kalman team might be winning by defense, not offense.
  // Adding Kalman to totals inflates projections and generates bad OVER picks.
  return Math.round(totalBase * pace * 10) / 10;
}



// Returns the feature vector used by self_tune for the margin regression.
// margin ≈ features · weights + baseline

export function extractMarginFeatures(homeStats, awayStats, avgStats, paceAdj) {
  const pace = ((homeStats.PACE + awayStats.PACE) / 2 * paceAdj) / 100;
  return {
    dTS:  (homeStats.TS - awayStats.TS) * pace,
    dTO:  -(homeStats.TO - awayStats.TO) * pace,    // negative: higher TO is bad
    dORR: (homeStats.ORR - awayStats.ORR) * pace,
    dNET: 0.5 * ((homeStats.OFF - homeStats.DEF) - (awayStats.OFF - awayStats.DEF)) * pace,
    hca:  1.0,  // home court present
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

export function analyzeGame(g, H, a, W, injuryAdj = null, kalmanState = null, W_var = null) {
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

  // Project scores
  const aProj = projScore(gg.away, gg.home, false, H, a, W, awayKalman, W_var);
  const hProj = projScore(gg.home, gg.away, true, H, a, W, homeKalman, W_var);
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

    const probH  = W.probHigh  ?? 0.57;
    const probOH = W.probOUHigh  ?? 0.58;
    const probOE = W.probOUElite ?? 0.64;

    // Spread — single tier
    const bestSpreadP = Math.max(pHomeCover, pAwayCover);
    const spreadSide  = pHomeCover >= pAwayCover ? "home" : "away";

    if (bestSpreadP >= probH) {
      if (spreadSide === "home") {
        sPick = homeFav ? `${gg.home} -${absLine}` : `${gg.home} +${absLine}`;
      } else {
        sPick = homeFav ? `${gg.away} +${absLine}` : `${gg.away} -${absLine}`;
      }
      sConf = "elite";
      pCover = bestSpreadP;
    }

    // Total — elite only (high totals 49.4%, not profitable)
    const bestTotalP = Math.max(pOver, pUnder);
    if (bestTotalP >= probOE) {
      oPick = pOver >= pUnder ? "OVER" : "UNDER";
      oConf = "elite";
      pOU = bestTotalP;
    }

  } else {
    // ── Legacy threshold-based logic (fallback) ─────────────────────────

    if (sDiff >= W.sprHigh) {
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

  const hTeam = H[homeKey], aTeam = H[awayKey];
  const _features = {
    dTS:  hTeam.TS  - aTeam.TS,
    dTO:  hTeam.TO  - aTeam.TO,
    dORR: hTeam.ORR - aTeam.ORR,
    dNET: (hTeam.OFF - hTeam.DEF) - (aTeam.OFF - aTeam.DEF),
    avgPace: (hTeam.PACE + aTeam.PACE) / 2,
  };

  // Margin features for Bayesian weight update
  const _marginFeatures = extractMarginFeatures(hTeam, aTeam, a, W.paceAdj);

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
