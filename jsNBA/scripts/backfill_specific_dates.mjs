// scripts/backfill_specific_dates.mjs
// Additive backfill of specific dates that preserves existing store + kalman.
// Use this to fill gaps (e.g. failed daily runs) without wiping history.
//
// Usage: node scripts/backfill_specific_dates.mjs YYYYMMDD [YYYYMMDD ...]
// Example: node scripts/backfill_specific_dates.mjs 20260502 20260503

import { fetchNBAStats, fetchNBAStatsEnhanced } from "./sources/nba_stats.mjs";
import { blendBase, blendForGame } from "./sources/blend_stats.mjs";
import { fetchScoreboard, extractFinalScores } from "./sources/espn_scoreboard.mjs";
import { fetchATSTrends, fetchOUTRends } from "./sources/teamrankings_trends.mjs";
import { applyB2BAdjustment } from "./sources/rest_detect.mjs";

import { loadDefaults, getAvgs, analyzeGame } from "./model_engine.mjs";
import { loadStore, saveStore, upsertRun } from "./store.mjs";
import { tuneWeights, computeResidualVar } from "./self_tune.mjs";
import {
  loadKalmanState, saveKalmanState,
  applyDailyDrift, batchUpdate, pruneProcessedGames,
} from "./kalman_state.mjs";

import { fetchOddsForDay } from "./sources/odds_batch_historical.mjs";

import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CACHE_DIR = path.join(__dirname, "..", "..", "data", "stats_cache", "nba");

function toDisplayDate(s) { return `${s.slice(0,4)}-${s.slice(4,6)}-${s.slice(6,8)}`; }

function previousYYYYMMDD(s) {
  const d = new Date(Date.UTC(Number(s.slice(0,4)), Number(s.slice(4,6))-1, Number(s.slice(6,8))));
  d.setUTCDate(d.getUTCDate() - 1);
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth()+1).padStart(2,"0");
  const dd = String(d.getUTCDate()).padStart(2,"0");
  return `${y}${m}${dd}`;
}

function pickHomeAwayFromScoreboardEvent(ev) {
  const comp = ev?.competitions?.[0];
  const competitors = comp?.competitors;
  if (!Array.isArray(competitors) || competitors.length !== 2) return null;
  const home = competitors.find(c => c?.homeAway === "home");
  const away = competitors.find(c => c?.homeAway === "away");
  if (!home || !away) return null;
  const homeName = String(home?.team?.displayName || home?.team?.name || home?.team?.location || "").trim();
  const awayName = String(away?.team?.displayName || away?.team?.name || away?.team?.location || "").trim();
  return { home: homeName, away: awayName, commenceTimeIso: comp?.date };
}

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

function normKey(s) {
  return String(s || "").toLowerCase().replace(/[^a-z0-9\s]/g, " ").replace(/\s+/g, " ").trim();
}
function matchTeam(a, b) {
  if (a === b) return true;
  const na = normKey(a), nb = normKey(b);
  if (na === nb) return true;
  if (na.includes(nb) || nb.includes(na)) return true;
  return false;
}

async function gradeDateInStoreLocal(store, yyyymmdd) {
  const run = (store.runs || []).find(r => r.date === yyyymmdd);
  if (!run) return 0;
  const needsGrading = (run.games || []).some(g =>
    g.status !== "MISSING_ODDS" && g.status !== "SKIPPED" && !Number.isFinite(g.homeScore)
  );
  if (!needsGrading) return 0;
  const sb = await fetchScoreboard(yyyymmdd).catch(() => null);
  if (!sb) return 0;
  const finals = extractFinalScores(sb);
  if (!finals.length) return 0;
  let graded = 0;
  for (const g of run.games || []) {
    if (g.status === "MISSING_ODDS" || g.status === "SKIPPED") continue;
    if (Number.isFinite(g.homeScore)) continue;
    const f = finals.find(x => matchTeam(x.away, g.away) && matchTeam(x.home, g.home));
    if (!f) continue;
    g.awayScore = f.awayScore;
    g.homeScore = f.homeScore;
    if (g.sPick && g.sPick !== "PASS") g.sResult = gradeSpread(g);
    if (g.oPick && g.oPick !== "PASS") g.oResult = gradeTotal(g);
    graded++;
  }
  if (graded > 0) saveStore(store);
  return graded;
}

async function getStatsForDate(dateYYYYMMDD, statsCache) {
  if (statsCache.has(dateYYYYMMDD)) return statsCache.get(dateYYYYMMDD);
  if (!fs.existsSync(CACHE_DIR)) fs.mkdirSync(CACHE_DIR, { recursive: true });
  const diskPath = path.join(CACHE_DIR, dateYYYYMMDD + ".json");
  if (fs.existsSync(diskPath)) {
    const raw = JSON.parse(fs.readFileSync(diskPath, "utf8"));
    if (raw.season && raw.last10) {
      statsCache.set(dateYYYYMMDD, raw);
      return raw;
    }
    console.log(`  [backfill] Old-format stats cache for ${dateYYYYMMDD} — re-fetching enhanced...`);
  }
  const dateTo = toDisplayDate(dateYYYYMMDD);
  let enhanced;
  try {
    enhanced = await fetchNBAStatsEnhanced(dateTo);
  } catch (e) {
    console.warn(`  [backfill] Enhanced fetch failed: ${e.message} — trying season only`);
    const stats = await fetchNBAStats(dateTo);
    enhanced = { season: stats, last10: null, home: null, away: null };
  }
  statsCache.set(dateYYYYMMDD, enhanced);
  try { fs.writeFileSync(diskPath, JSON.stringify(enhanced)); } catch {}
  return enhanced;
}

async function main() {
  const targetDates = process.argv.slice(2).filter(s => /^\d{8}$/.test(s)).sort();
  if (!targetDates.length) {
    console.log("Usage: node scripts/backfill_specific_dates.mjs YYYYMMDD [YYYYMMDD ...]");
    process.exit(1);
  }

  const store = loadStore();
  const defaults = loadDefaults();
  const kalmanState = loadKalmanState();
  if (!kalmanState) {
    console.error("No existing Kalman state — run a full backfill first.");
    process.exit(1);
  }

  for (const d of targetDates) {
    if ((store.runs || []).some(r => r.date === d)) {
      console.error(`Date ${d} already exists in store; refusing to double-process. Remove it from store first if you really want to redo it.`);
      process.exit(1);
    }
  }

  let baseW    = (store.weights    && Object.keys(store.weights).length    > 0) ? store.weights    : defaults.DEFAULT_W;
  let baseWVar = (store.weightsVar && Object.keys(store.weightsVar).length > 0) ? store.weightsVar : defaults.DEFAULT_W_VAR;

  const [ats, ou] = await Promise.all([fetchATSTrends(), fetchOUTRends()]);
  const statsCache = new Map();

  for (const date of targetDates) {
    const dateDisplay = toDisplayDate(date);
    console.log(`\n══ Backfill ${dateDisplay} ══`);

    // Auto-grade prior day if it has ungraded games (mirrors run_daily.mjs behavior)
    const prevDate = previousYYYYMMDD(date);
    const newlyGraded = await gradeDateInStoreLocal(store, prevDate);
    if (newlyGraded > 0) console.log(`  [backfill] Auto-graded ${newlyGraded} games in ${prevDate} from ESPN`);

    // Apply previous day's graded games to Kalman + tune weights, then build B2B
    const prevRun = (store.runs || []).find(r => r.date === prevDate);
    const prevGraded = (prevRun?.games || []).filter(g =>
      g.status !== "MISSING_ODDS" && g.status !== "SKIPPED" &&
      Number.isFinite(g.homeScore) && Number.isFinite(g.awayScore) &&
      ((g.sPick && g.sPick !== "PASS") || (g.oPick && g.oPick !== "PASS"))
    );

    if (prevGraded.length) {
      batchUpdate(kalmanState, prevGraded);
      const { W: tunedW, W_var: tunedWVar } = tuneWeights(baseW, baseWVar, prevGraded);
      baseW = tunedW;
      baseWVar = tunedWVar;
      store.weights = tunedW;
      store.weightsVar = tunedWVar;
      console.log(`  [backfill] Applied ${prevGraded.length} games from ${prevDate} → Kalman + tuned weights`);
    } else {
      console.log(`  [backfill] No graded games found for ${prevDate} — skipping Kalman/tune update`);
    }

    const b2bTeams = new Set();
    for (const g of (prevRun?.games || [])) {
      if (g.home) b2bTeams.add(g.home);
      if (g.away) b2bTeams.add(g.away);
    }

    applyDailyDrift(kalmanState, date);

    // Stats as of date
    let enhanced, baseStats;
    try {
      enhanced = await getStatsForDate(date, statsCache);
      baseStats = blendBase(enhanced.season, enhanced.last10, baseW.recentWeight ?? 0.35);
    } catch (e) {
      console.error(`  [backfill] Could not fetch stats for ${dateDisplay}: ${e.message} — skipping`);
      continue;
    }

    const dynamicResidualVar = computeResidualVar(store.runs || []);
    store.residualVar = dynamicResidualVar;

    const sb = await fetchScoreboard(date);
    const finals = extractFinalScores(sb);
    const events = Array.isArray(sb?.events) ? sb.events : [];

    const gamesList = [];
    for (const ev of events) {
      const ha = pickHomeAwayFromScoreboardEvent(ev);
      if (!ha) continue;
      const f = finals.find(x => x.away === ha.away && x.home === ha.home);
      if (!f) continue;
      gamesList.push({ ...ha, awayScore: f.awayScore, homeScore: f.homeScore });
    }

    let dayOdds = {};
    if (gamesList.length > 0) {
      try {
        dayOdds = await fetchOddsForDay(date, gamesList);
      } catch (e) {
        console.warn(`  [backfill] Batch odds fetch failed: ${e.message}`);
      }
    }

    const games = [];
    const { adjusted: adjustedStats, b2bNotes } = applyB2BAdjustment(baseStats, b2bTeams, gamesList);

    for (const gl of gamesList) {
      const key = `${gl.away}@${gl.home}`;
      const odds = dayOdds[key] || { line: null, total: null, _book: null, _note: "No batch odds" };
      const g = {
        away: gl.away,
        home: gl.home,
        line: odds.line,
        total: odds.total,
        _book: odds._book,
        startTimeUTC: gl.commenceTimeIso || null,
      };

      if (typeof g.line !== "number" || typeof g.total !== "number") {
        games.push({ ...g, awayScore: gl.awayScore, homeScore: gl.homeScore, status: "MISSING_ODDS", note: odds._note || "Historical odds not available" });
        continue;
      }

      const gameStats = blendForGame(adjustedStats, enhanced.home, enhanced.away, g.home, g.away, baseW.locationWeight ?? 0.25);
      const gameAvgs = getAvgs(gameStats);
      const r = analyzeGame(g, gameStats, gameAvgs, baseW, null, kalmanState, baseWVar, dynamicResidualVar, null);
      if (!r) {
        games.push({ ...g, awayScore: gl.awayScore, homeScore: gl.homeScore, status: "SKIPPED", note: "analyzeGame returned null" });
        continue;
      }
      r.awayScore = gl.awayScore;
      r.homeScore = gl.homeScore;

      const awayB2B = b2bNotes[g.away] || null;
      const homeB2B = b2bNotes[g.home] || null;
      if (awayB2B || homeB2B) {
        r.b2bNote = [awayB2B ? `${g.away}: ${awayB2B}` : null, homeB2B ? `${g.home}: ${homeB2B}` : null].filter(Boolean).join(" | ");
      }

      r.trends = {
        away: { atsPct: ats?.[r.away]?.atsPct ?? null, atsPlusMinus: ats?.[r.away]?.atsPlusMinus ?? null, overPct: ou?.[r.away]?.overPct ?? null, underPct: ou?.[r.away]?.underPct ?? null, totalPlusMinus: ou?.[r.away]?.totalPlusMinus ?? null },
        home: { atsPct: ats?.[r.home]?.atsPct ?? null, atsPlusMinus: ats?.[r.home]?.atsPlusMinus ?? null, overPct: ou?.[r.home]?.overPct ?? null, underPct: ou?.[r.home]?.underPct ?? null, totalPlusMinus: ou?.[r.home]?.totalPlusMinus ?? null }
      };

      if (r.sPick && r.sPick !== "PASS" && Number.isFinite(r.homeScore) && Number.isFinite(r.awayScore)) r.sResult = gradeSpread(r);
      if (r.oPick && r.oPick !== "PASS" && Number.isFinite(r.homeScore) && Number.isFinite(r.awayScore)) r.oResult = gradeTotal(r);

      games.push(r);
    }

    const completed = games.filter(x =>
      x.status !== "MISSING_ODDS" && x.status !== "SKIPPED" &&
      Number.isFinite(x.homeScore) && Number.isFinite(x.awayScore)
    );
    for (const g of completed) g._kalmanDate = date;

    const run = { date, dateDisplay, burnIn: false, weightsUsed: { ...baseW }, weightsNext: { ...baseW }, games, summaryText: "" };
    upsertRun(store, run);
    saveStore(store);
    saveKalmanState(kalmanState);

    const counts = games.reduce((acc, x) => { const k = x.status || "OK"; acc[k] = (acc[k] || 0) + 1; return acc; }, {});
    console.log(`  Done ${dateDisplay}: games=${games.length}, completed=${completed.length}, statuses=${JSON.stringify(counts)}`);
  }

  pruneProcessedGames(kalmanState, 60);
  saveKalmanState(kalmanState);
  console.log("\nBackfill complete.");
}

main().catch(err => { console.error(err); process.exit(1); });
