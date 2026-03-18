// scripts/kalman_state.mjs
// ────────────────────────────────────────────────────────────────────────────
// Bayesian team strength tracker using a Kalman filter.
//
// Each team has an "adjustment offset" — how many points better or worse
// they're performing relative to their season-long stats. This is a single
// number per team, not per-stat, because a single game result doesn't give
// us enough information to decompose into separate OFF/DEF/TS/TO signals.
//
// State per team:
//   adj_mean:  expected offset (starts at 0)
//   adj_var:   uncertainty around that offset (starts high, shrinks with games)
//
// After each graded game:
//   innovation = actual_margin - projected_margin
//   The innovation is split between both teams via Kalman gain.
//   A team with high variance (uncertain) absorbs more of the surprise.
//   A team with low variance (well-known) absorbs less.
//
// Daily drift:
//   Variance increases slightly each day — teams change (trades, fatigue,
//   chemistry shifts). This prevents the filter from locking on to old data.
//
// The offset gets added to projScore in model_engine.mjs.
// The variance feeds into the projection variance for P(cover).
//
// NCAA adaptation: higher initialVar (362 teams, more uncertain), higher
// gameNoise (14 pt std dev), higher maxVar (teams can go weeks between games).
// ────────────────────────────────────────────────────────────────────────────

import fs from "fs";
import path from "path";

const DATA_DIR = path.join(process.cwd(), "data");
const STATE_PATH = path.join(DATA_DIR, "kalman_state.json");

// ── Hyperparameters ─────────────────────────────────────────────────────────

export const KALMAN_DEFAULTS = {
  // Initial uncertainty per team (points²). ±4.5 points = variance 20.
  // Higher than NBA because 362 D1 teams means less prior knowledge per team.
  initialVar: 20,

  // Game outcome noise (points²). NCAA games have ~14 point std dev in margin
  // after accounting for team strength. 14² = 196.
  // Higher talent disparity in college → more random outcomes.
  gameNoise: 196,

  // Daily drift added to each team's variance. Prevents the filter from
  // becoming too confident over time. 0.15 means after 30 days with no games,
  // a team's variance grows by ~4.5 (≈ 2 extra points of uncertainty).
  dailyDrift: 0.15,

  // Minimum variance floor — never let a team get "perfectly known."
  // Even late-season teams have game-to-game variance in true strength.
  minVar: 2.0,

  // Maximum variance cap — don't let uncertainty blow up for teams with
  // long gaps between games. Higher than NBA because some college teams
  // can go weeks between games (exam breaks, conference tournament gaps).
  maxVar: 40,
};


// ── State Management ────────────────────────────────────────────────────────

const EMPTY_STATE = {
  teams: {},
  processedGames: {},   // { "YYYYMMDD:away@home": true } — prevents double-counting
  lastDriftDate: null,   // YYYYMMDD of last drift application
  meta: { season: null, created: null },
};

export function loadKalmanState() {
  try {
    const raw = fs.readFileSync(STATE_PATH, "utf8");
    const state = JSON.parse(raw);
    if (!state.teams) state.teams = {};
    if (!state.processedGames) state.processedGames = {};
    return state;
  } catch {
    return { ...EMPTY_STATE, teams: {}, processedGames: {} };
  }
}

export function saveKalmanState(state) {
  fs.mkdirSync(DATA_DIR, { recursive: true });
  fs.writeFileSync(STATE_PATH, JSON.stringify(state, null, 2));
}


// ── Initialization ──────────────────────────────────────────────────────────
// Call once at the start of the season or when the state file doesn't exist.
// Seeds every team with adj_mean=0 (no adjustment), adj_var=initialVar.

export function initializeKalman(teamStats, opts = {}) {
  const cfg = { ...KALMAN_DEFAULTS, ...opts };
  const state = { ...EMPTY_STATE, teams: {}, processedGames: {} };

  for (const teamName of Object.keys(teamStats)) {
    const gp = teamStats[teamName].GP || 0;

    // Scale initial variance by games played — a team with 60 GP is
    // better known than a team with 15 GP.
    const gpFactor = Math.max(0.5, Math.min(1.0, 30 / Math.max(gp, 1)));
    const initVar = cfg.initialVar * gpFactor;

    state.teams[teamName] = {
      adj_mean: 0,
      adj_var: Math.max(cfg.minVar, initVar),
    };
  }

  const now = new Date();
  state.meta = {
    season: currentSeason(),
    created: now.toISOString(),
  };
  state.lastDriftDate = todayYYYYMMDD();

  console.log(`  [kalman] Initialized ${Object.keys(state.teams).length} teams (initial_var=${cfg.initialVar})`);
  return state;
}


// ── Ensure Team Exists ──────────────────────────────────────────────────────
// If a team isn't in the state (mid-season init, name mismatch), add it.

function ensureTeam(state, teamName, cfg) {
  if (!state.teams[teamName]) {
    state.teams[teamName] = {
      adj_mean: 0,
      adj_var: cfg.initialVar,
    };
  }
}


// ── Daily Drift ─────────────────────────────────────────────────────────────
// Call once per day BEFORE analyzing games. Adds process noise to every team's
// variance, reflecting that teams change over time.

export function applyDailyDrift(state, today = null, opts = {}) {
  const cfg = { ...KALMAN_DEFAULTS, ...opts };
  const todayStr = today || todayYYYYMMDD();

  if (state.lastDriftDate === todayStr) return; // already applied today

  // How many days since last drift? Usually 1, but could be more if
  // the pipeline was down for a few days.
  let daysSinceDrift = 1;
  if (state.lastDriftDate) {
    const last = parseYYYYMMDD(state.lastDriftDate);
    const now = parseYYYYMMDD(todayStr);
    daysSinceDrift = Math.max(1, Math.round((now - last) / 86400000));
    daysSinceDrift = Math.min(daysSinceDrift, 14); // cap at 2 weeks
  }

  const totalDrift = cfg.dailyDrift * daysSinceDrift;

  for (const team of Object.values(state.teams)) {
    team.adj_var = Math.min(cfg.maxVar, team.adj_var + totalDrift);
  }

  state.lastDriftDate = todayStr;
}


// ── Game Update ─────────────────────────────────────────────────────────────
// Call after a game is graded. Updates both teams' Kalman state.
//
// Inputs:
//   state:        the kalman state object
//   game:         { home, away, homeScore, awayScore } — actual results
//   projMargin:   the model's projected margin (hS - aS) for this game
//   gameDate:     YYYYMMDD string for dedup
//   opts:         override hyperparameters
//
// The "innovation" is: actual_margin - projected_margin.
// If positive, the home team outperformed (or away underperformed).
// The Kalman gains determine how much each team absorbs.

export function updateFromGame(state, game, projMargin, gameDate = null, opts = {}) {
  const cfg = { ...KALMAN_DEFAULTS, ...opts };

  const { home, away, homeScore, awayScore } = game;
  if (!Number.isFinite(homeScore) || !Number.isFinite(awayScore)) return;
  if (!home || !away) return;

  // Dedup: don't process the same game twice
  if (gameDate) {
    const key = `${gameDate}:${away}@${home}`;
    if (state.processedGames[key]) return;
    state.processedGames[key] = true;
  }

  ensureTeam(state, home, cfg);
  ensureTeam(state, away, cfg);

  const h = state.teams[home];
  const a = state.teams[away];

  // Actual margin from home perspective
  const actualMargin = homeScore - awayScore;

  // Predicted margin includes both teams' current adjustments
  const predictedMargin = projMargin + h.adj_mean - a.adj_mean;

  // Innovation: surprise
  const innovation = actualMargin - predictedMargin;

  // Innovation variance: sum of both teams' uncertainty + game noise
  const S = h.adj_var + a.adj_var + cfg.gameNoise;

  // Kalman gains — more uncertain team absorbs more of the surprise
  const K_home = h.adj_var / S;
  const K_away = a.adj_var / S;

  // Update means
  h.adj_mean += K_home * innovation;
  a.adj_mean -= K_away * innovation;  // negative: if home outperformed, away underperformed

  // Update variances (shrink uncertainty)
  h.adj_var = Math.max(cfg.minVar, (1 - K_home) * h.adj_var);
  a.adj_var = Math.max(cfg.minVar, (1 - K_away) * a.adj_var);
}


// ── Batch Update ────────────────────────────────────────────────────────────
// Process multiple graded games at once. Used by run_daily after grading.

export function batchUpdate(state, gradedGames, opts = {}) {
  let updated = 0;

  for (const g of gradedGames) {
    if (!Number.isFinite(g.homeScore) || !Number.isFinite(g.awayScore)) continue;
    if (!Number.isFinite(g.hS) || !Number.isFinite(g.aS)) continue;

    const projMargin = g.hS - g.aS;
    updateFromGame(state, g, projMargin, g._kalmanDate || null, opts);
    updated++;
  }

  if (updated > 0) {
    console.log(`  [kalman] Updated from ${updated} graded game(s)`);
  }
  return updated;
}


// ── Get Team Adjustment ─────────────────────────────────────────────────────
// Returns { mean, var } for a team. If team not in state, returns neutral.

export function getTeamAdj(state, teamName) {
  const t = state.teams[teamName];
  if (!t) return { mean: 0, var: KALMAN_DEFAULTS.initialVar };
  return { mean: t.adj_mean, var: t.adj_var };
}


// ── Diagnostics ─────────────────────────────────────────────────────────────
// Human-readable summary for logging.

export function kalmanSummary(state, topN = 10) {
  const entries = Object.entries(state.teams)
    .map(([name, t]) => ({ name, ...t }))
    .sort((a, b) => Math.abs(b.adj_mean) - Math.abs(a.adj_mean));

  const lines = [`  [kalman] Team adjustments (top ${topN} by |offset|):`];
  for (const t of entries.slice(0, topN)) {
    const sign = t.adj_mean >= 0 ? "+" : "";
    const conf = Math.sqrt(t.adj_var).toFixed(1);
    lines.push(`    ${t.name.padEnd(28)} ${sign}${t.adj_mean.toFixed(2)} pts  (±${conf})`);
  }
  return lines.join("\n");
}


// ── Prune Old Processed Games ───────────────────────────────────────────────
// Keep the processedGames map from growing forever. Called periodically.

export function pruneProcessedGames(state, keepDays = 30) {
  const cutoff = dateMinusDays(keepDays);
  const before = Object.keys(state.processedGames).length;

  for (const key of Object.keys(state.processedGames)) {
    const date = key.split(":")[0];
    if (date < cutoff) delete state.processedGames[key];
  }

  const after = Object.keys(state.processedGames).length;
  if (before !== after) {
    console.log(`  [kalman] Pruned ${before - after} old game records (kept last ${keepDays} days)`);
  }
}


// ── Utility ─────────────────────────────────────────────────────────────────

function currentSeason() {
  const now = new Date();
  const y = now.getFullYear();
  const m = now.getMonth() + 1;
  const start = m >= 10 ? y : y - 1;
  return `${start}-${String(start + 1).slice(2)}`;
}

function todayYYYYMMDD() {
  const fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Chicago",
    year: "numeric", month: "2-digit", day: "2-digit",
  });
  return fmt.format(new Date()).replace(/-/g, "");
}

function parseYYYYMMDD(s) {
  return new Date(
    Number(s.slice(0, 4)),
    Number(s.slice(4, 6)) - 1,
    Number(s.slice(6, 8))
  );
}

function dateMinusDays(n) {
  const d = new Date();
  d.setDate(d.getDate() - n);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${y}${m}${dd}`;
}
