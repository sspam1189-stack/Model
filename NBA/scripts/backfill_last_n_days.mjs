// scripts/backfill_last_n_days.mjs
import { fetchNBAStats, fetchNBAStatsEnhanced } from "./sources/nba_stats.mjs";
import { blendBase, blendForGame } from "./sources/blend_stats.mjs";
import { fetchScoreboard, extractFinalScores } from "./sources/espn_scoreboard.mjs";
import { fetchATSTrends, fetchOUTRends } from "./sources/teamrankings_trends.mjs";

import { loadDefaults, getAvgs, analyzeGame } from "./model_engine.mjs";
import { loadStore, saveStore, upsertRun } from "./store.mjs";
import { tuneWeights } from "./self_tune.mjs";

import { fetchClosingOddsForGame } from "./sources/odds_theoddsapi_historical.mjs";

import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CACHE_DIR = path.join(__dirname, "..", "data", "stats_cache");

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

// Days at the START of the window (oldest) used for warm-up only — picks not evaluated
const BURN_IN_DAYS = 20;

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

  for (let i = days; i >= 0; i--) {
    const date = dateMinusDaysCentral(i);
    const dateDisplay = toDisplayDate(date);

    const baseW    = (store.weights    && Object.keys(store.weights).length    > 0) ? store.weights    : defaults.DEFAULT_W;
    const baseWVar = (store.weightsVar && Object.keys(store.weightsVar).length > 0) ? store.weightsVar : defaults.DEFAULT_W_VAR;

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

    const sb = await fetchScoreboard(date);
    const finals = extractFinalScores(sb);
    const events = Array.isArray(sb?.events) ? sb.events : [];

    const games = [];

    for (const ev of events) {
      const ha = pickHomeAwayFromScoreboardEvent(ev);
      if (!ha) continue;

      const f = finals.find((x) => x.away === ha.away && x.home === ha.home);
      if (!f) continue;

      let odds = { line: null, total: null, _book: null, _note: null };
      try {
        odds = await fetchClosingOddsForGame({
          home: ha.home,
          away: ha.away,
          commenceTimeIso: ha.commenceTimeIso
        });
      } catch (e) {
        odds = { line: null, total: null, _book: null, _note: String(e?.message || e) };
      }

      await sleep(350);

      const g = {
        away: ha.away,
        home: ha.home,
        line: odds.line,
        total: odds.total,
        _book: odds._book
      };

      if (typeof g.line !== "number" || typeof g.total !== "number") {
        games.push({
          ...g,
          awayScore: f.awayScore,
          homeScore: f.homeScore,
          status: "MISSING_ODDS",
          note: odds._note || "Historical odds not available for this game"
        });
        continue;
      }

      const gameStats = blendForGame(
        baseStats, enhanced.home, enhanced.away,
        g.home, g.away, baseW.locationWeight ?? 0.25
      );
      const gameAvgs = getAvgs(gameStats);

      const r = analyzeGame(g, gameStats, gameAvgs, baseW);
      if (!r) {
        games.push({
          ...g,
          awayScore: f.awayScore,
          homeScore: f.homeScore,
          status: "SKIPPED",
          note: "analyzeGame returned null (team name mismatch or bad inputs)"
        });
        continue;
      }

      r.awayScore = f.awayScore;
      r.homeScore = f.homeScore;
      r._recencyWeight = recencyWeight(i); // attach recency weight for self-tuner

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

    // BURN-IN GUARD: don't tune weights during the warm-up period (oldest days).
    // Picks in this window are logged but not used to adjust the model.
    const inBurnIn = i > (days - BURN_IN_DAYS);
    const { W: tunedW, W_var: tunedWVar } = inBurnIn ? { W: baseW, W_var: baseWVar } : tuneWeights(baseW, baseWVar, completed);
    store.weights    = tunedW;
    store.weightsVar = tunedWVar;

    const run = {
      date,
      dateDisplay,
      burnIn: inBurnIn,
      weightsUsed: baseW,
      weightsNext: tunedW,
      weightsVar: tunedWVar,
      games,
      summaryText: ""
    };

    upsertRun(store, run);
    saveStore(store);

    const counts = games.reduce((acc, x) => {
      const k = x.status || "OK";
      acc[k] = (acc[k] || 0) + 1;
      return acc;
    }, {});
    const burnInTag = inBurnIn ? " [BURN-IN]" : "";
    console.log(
      `Backfilled ${dateDisplay}${burnInTag}: games=${games.length}, completed=${completed.length} statuses=${JSON.stringify(counts)}`
    );
  }

  console.log("Backfill complete.");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});