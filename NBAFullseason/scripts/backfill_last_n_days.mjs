// scripts/backfill_last_n_days.mjs
import { fetchNBAStats, fetchNBAStatsEnhanced } from "./sources/nba_stats.mjs";
import { blendBase, blendForGame } from "./sources/blend_stats.mjs";
import { fetchScoreboard, extractFinalScores } from "./sources/espn_scoreboard.mjs";
import { fetchATSTrends, fetchOUTRends } from "./sources/teamrankings_trends.mjs";
import { applyB2BAdjustment } from "./sources/rest_detect.mjs";

import { loadDefaults, getAvgs, analyzeGame } from "./model_engine.mjs";
import { loadStore, saveStore, upsertRun } from "./store.mjs";
import { tuneWeights, computeResidualVar } from "./self_tune.mjs";
import {
  loadKalmanState, saveKalmanState, initializeKalman,
  applyDailyDrift, batchUpdate, pruneProcessedGames,
} from "./kalman_state.mjs";

import { fetchOddsForDay } from "./sources/odds_batch_historical.mjs";

import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CACHE_DIR = path.join(__dirname, "..", "data", "stats_cache");

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

// Days at the START of the window (oldest) used for warm-up only — picks not evaluated
const BURN_IN_DAYS = 0;  // MIN_GP=15 handles warm-up — count every pick that fires

// Weighted recency: recent games matter more than older ones
function recencyWeight(daysAgo) {
  if (daysAgo <= 15) return 1.0;
  if (daysAgo <= 30) return 0.75;
  if (daysAgo <= 45) return 0.5;
  return 0.25;
}

// Minimum games a team must have played before we trust its stats
const MIN_GAMES = 25;

function toDisplayDate(yyyymmddStr) {
  return `${yyyymmddStr.slice(0, 4)}-${yyyymmddStr.slice(4, 6)}-${yyyymmddStr.slice(6, 8)}`;
}

function yyyymmddFromDate(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}${m}${day}`;
}

function dateMinusDaysCentral(daysAgo) {
  const now = new Date();
  const fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Chicago",
    year: "numeric",
    month: "2-digit",
    day: "2-digit"
  });
  const parts = fmt.formatToParts(now);
  const y = Number(parts.find((p) => p.type === "year").value);
  const m = Number(parts.find((p) => p.type === "month").value);
  const d = Number(parts.find((p) => p.type === "day").value);

  const base = new Date(Date.UTC(y, m - 1, d));
  base.setUTCDate(base.getUTCDate() - daysAgo);
  return yyyymmddFromDate(base);
}

function pickHomeAwayFromScoreboardEvent(ev) {
  const comp = ev?.competitions?.[0];
  const competitors = comp?.competitors;
  if (!Array.isArray(competitors) || competitors.length !== 2) return null;

  const home = competitors.find((c) => c?.homeAway === "home");
  const away = competitors.find((c) => c?.homeAway === "away");
  if (!home || !away) return null;

  const homeName = String(
    home?.team?.displayName || home?.team?.name || home?.team?.location || ""
  ).trim();
  const awayName = String(
    away?.team?.displayName || away?.team?.name || away?.team?.location || ""
  ).trim();

  const commenceTimeIso = comp?.date; // ISO in UTC from ESPN
  return { home: homeName, away: awayName, commenceTimeIso };
}

async function main() {
  const days = Number(process.argv[2] || 60);
  if (!Number.isFinite(days) || days <= 0) {
    console.log("Usage: node scripts/backfill_last_n_days.mjs 8");
    process.exit(1);
  }

  const store = loadStore();
  const defaults = loadDefaults();

  const [ats, ou] = await Promise.all([
    fetchATSTrends(),
    fetchOUTRends()
  ]);

  // Cache stats per date — NBA.com has rate limits so we reuse within same date
  const statsCache = new Map();

  async function getStatsForDate(dateYYYYMMDD) {
    if (statsCache.has(dateYYYYMMDD)) return statsCache.get(dateYYYYMMDD);

    // Check disk cache first (shared with recalculate.mjs)
    if (!fs.existsSync(CACHE_DIR)) fs.mkdirSync(CACHE_DIR, { recursive: true });
    const diskPath = path.join(CACHE_DIR, dateYYYYMMDD + ".json");
    if (fs.existsSync(diskPath)) {
      const raw = JSON.parse(fs.readFileSync(diskPath, "utf8"));
      // If enhanced format with last10 data, use it directly
      if (raw.season && raw.last10) {
        statsCache.set(dateYYYYMMDD, raw);
        return raw;
      }
      // Old format (no last10/home/away) — re-fetch enhanced from NBA.com
      console.log(`  [backfill] Old-format cache for ${dateYYYYMMDD} — re-fetching enhanced stats...`);
    }

    // Fetch enhanced from NBA.com (date-accurate stats as-of that day)
    const dateTo = `${dateYYYYMMDD.slice(0,4)}-${dateYYYYMMDD.slice(4,6)}-${dateYYYYMMDD.slice(6,8)}`;
    let enhanced;
    try {
      enhanced = await fetchNBAStatsEnhanced(dateTo);
    } catch (e) {
      console.warn(`  [backfill] Enhanced fetch failed: ${e.message} — trying season only`);
      const stats = await fetchNBAStats(dateTo);
      enhanced = { season: stats, last10: null, home: null, away: null };
    }
    statsCache.set(dateYYYYMMDD, enhanced);

    // Write to disk cache for recalculate.mjs to reuse
    try { fs.writeFileSync(diskPath, JSON.stringify(enhanced)); }
    catch (e) { console.warn("  [backfill] cache write failed:", e.message); }

    return enhanced;
  }

  // H2H matchup history — builds progressively as days are backfilled.
  // Each date only sees games that happened before it.
  const h2hMatchups = {};

  // Kalman state — initialized on first date with stats, evolves forward
  let kalmanState = null;
  let prevDate = null;
  let prevGraded = [];     // games with projections → Kalman + self-tune
  let prevAllScored = [];  // all games with scores → H2H (includes SKIPPED)
  let b2bTeams = new Set(); // teams that played yesterday → B2B penalty in analyzeGame

  for (let i = days; i >= 0; i--) {
    const date = dateMinusDaysCentral(i);
    const dateDisplay = toDisplayDate(date);

    let baseW    = (store.weights    && Object.keys(store.weights).length    > 0) ? store.weights    : defaults.DEFAULT_W;
    let baseWVar = (store.weightsVar && Object.keys(store.weightsVar).length > 0) ? store.weightsVar : defaults.DEFAULT_W_VAR;

    // Fetch enhanced stats as they were on this specific date
    let enhanced, baseStats, a;
    try {
      enhanced = await getStatsForDate(date);
      baseStats = blendBase(enhanced.season, enhanced.last10, baseW.recentWeight ?? 0.35);
      a = getAvgs(baseStats);
    } catch (e) {
      console.warn(`  [backfill] Could not fetch stats for ${dateDisplay}: ${e.message} — skipping`);
      continue;
    }

    // Initialize Kalman on first date with stats
    if (!kalmanState) {
      kalmanState = initializeKalman(enhanced.season);
      kalmanState.lastDriftDate = date;
      console.log(`  [backfill] Kalman initialized from ${dateDisplay}`);
    }

    // ── Start of new day: process yesterday's results before today's analysis ──
    // Flow: Kalman update → self-tune → H2H → drift → residualVar → analyze
    if (date !== prevDate) {
      prevDate = date;

      // 1-2. Kalman + self-tune (requires projections — only completed games)
      if (prevGraded.length) {
        batchUpdate(kalmanState, prevGraded);

        const { W: tunedW, W_var: tunedWVar } = tuneWeights(baseW, baseWVar, prevGraded);
        baseW = tunedW;
        baseWVar = tunedWVar;
        store.weights = tunedW;
        store.weightsVar = tunedWVar;
      }

      // 3. H2H matchup history (just needs scores — includes early season SKIPPED games)
      for (const g of prevAllScored) {
        const key = [g.home, g.away].sort().join("::");
        if (!h2hMatchups[key]) {
          h2hMatchups[key] = { team1: [g.home, g.away].sort()[0], team2: [g.home, g.away].sort()[1], games: [] };
        }
        h2hMatchups[key].games.push({
          date: g._kalmanDate || date,
          home: { team: g.home, pts: g.homeScore },
          away: { team: g.away, pts: g.awayScore },
        });
      }

      // 5. Build B2B set for TODAY — teams that played yesterday
      b2bTeams = new Set();
      for (const g of prevAllScored) {
        if (g.home) b2bTeams.add(g.home);
        if (g.away) b2bTeams.add(g.away);
      }

      prevGraded = [];
      prevAllScored = [];

      // 4. Drift AFTER learning — account for time passing to today
      applyDailyDrift(kalmanState, date);
    }

    // 4. Compute dynamic residualVar from history so far
    const dynamicResidualVar = computeResidualVar(store.runs || []);

    const sb = await fetchScoreboard(date);
    const finals = extractFinalScores(sb);
    const events = Array.isArray(sb?.events) ? sb.events : [];

    // Collect all games for batch odds fetch
    const gamesList = [];
    for (const ev of events) {
      const ha = pickHomeAwayFromScoreboardEvent(ev);
      if (!ha) continue;
      const f = finals.find((x) => x.away === ha.away && x.home === ha.home);
      if (!f) continue;
      gamesList.push({ ...ha, awayScore: f.awayScore, homeScore: f.homeScore });
    }

    // Batch fetch ALL odds for this date in 1-2 API calls (cached to disk)
    let dayOdds = {};
    if (gamesList.length > 0) {
      try {
        dayOdds = await fetchOddsForDay(date, gamesList);
      } catch (e) {
        console.warn(`  [backfill] Batch odds fetch failed: ${e.message}`);
      }
    }

    const games = [];

    // Apply B2B rest penalty to today's stats (derived from yesterday's games)
    const { adjusted: adjustedStats, b2bNotes } = applyB2BAdjustment(baseStats, b2bTeams, gamesList);

    // Compute per-team deltas for recalculate replay
    const STAT_KEYS = ["OFF", "DEF", "TS", "TO", "ORR", "PACE"];
    function computeTeamDelta(team) {
      const base = baseStats[team];
      const adj  = adjustedStats[team];
      if (!base || !adj || base === adj) return null;
      const d = {};
      let any = false;
      for (const k of STAT_KEYS) {
        const diff = (adj[k] || 0) - (base[k] || 0);
        if (Math.abs(diff) > 0.0001) { d[k] = Math.round(diff * 10000) / 10000; any = true; }
      }
      return any ? d : null;
    }

    for (const gl of gamesList) {
      const key = `${gl.away}@${gl.home}`;
      const odds = dayOdds[key] || { line: null, total: null, _book: null, _note: "No batch odds" };

      const g = {
        away: gl.away,
        home: gl.home,
        line: odds.line,
        total: odds.total,
        _book: odds._book
      };

      if (typeof g.line !== "number" || typeof g.total !== "number") {
        games.push({
          ...g,
          awayScore: gl.awayScore,
          homeScore: gl.homeScore,
          status: "MISSING_ODDS",
          note: odds._note || "Historical odds not available for this game"
        });
        continue;
      }

      const gameStats = blendForGame(
        adjustedStats, enhanced.home, enhanced.away,
        g.home, g.away, baseW.locationWeight ?? 0.25
      );
      const gameAvgs = getAvgs(gameStats);

      const r = analyzeGame(g, gameStats, gameAvgs, baseW, null, kalmanState, baseWVar, dynamicResidualVar, h2hMatchups);
      if (!r) {
        games.push({
          ...g,
          awayScore: gl.awayScore,
          homeScore: gl.homeScore,
          status: "SKIPPED",
          note: "analyzeGame returned null (team name mismatch or bad inputs)"
        });
        continue;
      }

      r.awayScore = gl.awayScore;
      r.homeScore = gl.homeScore;
      r._recencyWeight = recencyWeight(i);

      // Cache B2B deltas for recalculate replay (same pattern as run_daily)
      const awayDelta = computeTeamDelta(g.away);
      const homeDelta = computeTeamDelta(g.home);
      if (awayDelta || homeDelta) {
        r._adjDeltas = {};
        if (awayDelta) r._adjDeltas.away = awayDelta;
        if (homeDelta) r._adjDeltas.home = homeDelta;
      }

      // B2B notes for display
      const awayB2B = b2bNotes[g.away] || null;
      const homeB2B = b2bNotes[g.home] || null;
      if (awayB2B || homeB2B) {
        r.b2bNote = [awayB2B ? `${g.away}: ${awayB2B}` : null, homeB2B ? `${g.home}: ${homeB2B}` : null].filter(Boolean).join(" | ");
      }

      r.trends = {
        away: {
          atsPct: ats?.[r.away]?.atsPct ?? null,
          atsPlusMinus: ats?.[r.away]?.atsPlusMinus ?? null,
          overPct: ou?.[r.away]?.overPct ?? null,
          underPct: ou?.[r.away]?.underPct ?? null,
          totalPlusMinus: ou?.[r.away]?.totalPlusMinus ?? null
        },
        home: {
          atsPct: ats?.[r.home]?.atsPct ?? null,
          atsPlusMinus: ats?.[r.home]?.atsPlusMinus ?? null,
          overPct: ou?.[r.home]?.overPct ?? null,
          underPct: ou?.[r.home]?.underPct ?? null,
          totalPlusMinus: ou?.[r.home]?.totalPlusMinus ?? null
        }
      };

      games.push(r);
    }

    const completed = games.filter(
      (x) =>
        x.status !== "MISSING_ODDS" &&
        x.status !== "SKIPPED" &&
        Number.isFinite(x.homeScore) &&
        Number.isFinite(x.awayScore)
    );

    // All games with scores — includes SKIPPED (early season, MIN_GP not met)
    // Used for H2H learning even when projections aren't available yet
    const allScored = games.filter(
      (x) =>
        x.status !== "MISSING_ODDS" &&
        Number.isFinite(x.homeScore) &&
        Number.isFinite(x.awayScore)
    );

    const inBurnIn = i > (days - BURN_IN_DAYS);

    // Collect graded games for next day's processing
    // completed → Kalman + self-tune (needs projections)
    // allScored → H2H (just needs team names + scores)
    for (const g of completed) {
      g._kalmanDate = date;
    }
    prevGraded = completed;
    prevAllScored = allScored;

    const run = {
      date,
      dateDisplay,
      burnIn: inBurnIn,
      weightsUsed: { ...baseW },
      weightsNext: { ...baseW },
      games,
      summaryText: ""
    };

    upsertRun(store, run);
    saveStore(store);

    // Save Kalman state every 10 dates — if backfill crashes, we don't lose everything
    if (kalmanState && i % 10 === 0) {
      saveKalmanState(kalmanState);
    }

    const counts = games.reduce((acc, x) => {
      const k = x.status || "OK";
      acc[k] = (acc[k] || 0) + 1;
      return acc;
    }, {});
    const burnInTag = inBurnIn ? " [BURN-IN]" : "";
    const b2bCount = Object.keys(b2bNotes).length;
    console.log(
      `Backfilled ${dateDisplay}${burnInTag}: games=${games.length}, completed=${completed.length} statuses=${JSON.stringify(counts)}` +
      (b2bCount ? ` b2b=${b2bCount}` : "")
    );
  }

  // Save final Kalman state
  if (kalmanState) {
    pruneProcessedGames(kalmanState, 60);
    saveKalmanState(kalmanState);
    console.log("Kalman state saved.");
  }

  console.log("Backfill complete.");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});