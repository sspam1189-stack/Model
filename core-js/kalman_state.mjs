import fs from "fs";
import path from "path";

const KALMAN_BASE_DEFAULTS = {
  initialVar: 16,
  gameNoise: 144,
  dailyDrift: 0.15,
  minVar: 2.0,
  maxVar: 30,
};

const EMPTY_STATE = {
  teams: {},
  processedGames: {},
  lastDriftDate: null,
  meta: { season: null, created: null },
};

export function createKalmanState(options = {}) {
  const { dataDir, statePath, ...overrides } = options;

  const KALMAN_DEFAULTS = { ...KALMAN_BASE_DEFAULTS, ...overrides };
  const DATA_DIR = dataDir || path.join(process.cwd(), "data");
  const STATE_PATH = statePath || path.join(DATA_DIR, "kalman_state.json");

  function loadKalmanState() {
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

  function saveKalmanState(state) {
    fs.mkdirSync(DATA_DIR, { recursive: true });
    fs.writeFileSync(STATE_PATH, JSON.stringify(state, null, 2));
  }

  function initializeKalman(teamStats, opts = {}) {
    const cfg = { ...KALMAN_DEFAULTS, ...opts };
    const state = { ...EMPTY_STATE, teams: {}, processedGames: {} };

    for (const teamName of Object.keys(teamStats)) {
      const gp = teamStats[teamName].GP || 0;
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

  function applyDailyDrift(state, today = null, opts = {}) {
    const cfg = { ...KALMAN_DEFAULTS, ...opts };
    const todayStr = today || todayYYYYMMDD();

    if (state.lastDriftDate === todayStr) return;

    let daysSinceDrift = 1;
    if (state.lastDriftDate) {
      const last = parseYYYYMMDD(state.lastDriftDate);
      const now = parseYYYYMMDD(todayStr);
      daysSinceDrift = Math.max(1, Math.round((now - last) / 86400000));
      daysSinceDrift = Math.min(daysSinceDrift, 14);
    }

    const totalDrift = cfg.dailyDrift * daysSinceDrift;

    for (const team of Object.values(state.teams)) {
      team.adj_var = Math.min(cfg.maxVar, team.adj_var + totalDrift);
    }

    state.lastDriftDate = todayStr;
  }

  function updateFromGame(state, game, projMargin, gameDate = null, opts = {}) {
    const cfg = { ...KALMAN_DEFAULTS, ...opts };

    const { home, away, homeScore, awayScore } = game;
    if (!Number.isFinite(homeScore) || !Number.isFinite(awayScore)) return;
    if (!home || !away) return;

    if (gameDate) {
      const key = `${gameDate}:${away}@${home}`;
      if (state.processedGames[key]) return;
      state.processedGames[key] = true;
    }

    ensureTeam(state, home, cfg);
    ensureTeam(state, away, cfg);

    const h = state.teams[home];
    const a = state.teams[away];

    const actualMargin = homeScore - awayScore;
    const predictedMargin = projMargin + h.adj_mean - a.adj_mean;
    const innovation = actualMargin - predictedMargin;

    const S = h.adj_var + a.adj_var + cfg.gameNoise;
    const K_home = h.adj_var / S;
    const K_away = a.adj_var / S;

    h.adj_mean += K_home * innovation;
    a.adj_mean -= K_away * innovation;

    h.adj_var = Math.max(cfg.minVar, (1 - K_home) * h.adj_var);
    a.adj_var = Math.max(cfg.minVar, (1 - K_away) * a.adj_var);
  }

  function batchUpdate(state, gradedGames, opts = {}) {
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

  function getTeamAdj(state, teamName, opts = {}) {
    const cfg = { ...KALMAN_DEFAULTS, ...opts };
    const t = state.teams[teamName];
    if (!t) return { mean: 0, var: cfg.initialVar };
    return { mean: t.adj_mean, var: t.adj_var };
  }

  function kalmanSummary(state, topN = 10) {
    const entries = Object.entries(state.teams)
      .map(([name, t]) => ({ name, ...t }))
      .sort((a, b) => Math.abs(b.adj_mean) - Math.abs(a.adj_mean));

    const lines = [`  [kalman] Team adjustments (top ${topN} by |offset|):`];
    for (const t of entries.slice(0, topN)) {
      const sign = t.adj_mean >= 0 ? "+" : "";
      const conf = Math.sqrt(t.adj_var).toFixed(1);
      lines.push(`    ${t.name.padEnd(28)} ${sign}${t.adj_mean.toFixed(2)} pts  (+/-${conf})`);
    }
    return lines.join("\n");
  }

  function pruneProcessedGames(state, keepDays = 30) {
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

  return {
    KALMAN_DEFAULTS,
    loadKalmanState,
    saveKalmanState,
    initializeKalman,
    applyDailyDrift,
    updateFromGame,
    batchUpdate,
    getTeamAdj,
    kalmanSummary,
    pruneProcessedGames,
  };
}

export { KALMAN_BASE_DEFAULTS };

function ensureTeam(state, teamName, cfg) {
  if (!state.teams[teamName]) {
    state.teams[teamName] = {
      adj_mean: 0,
      adj_var: cfg.initialVar,
    };
  }
}

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
