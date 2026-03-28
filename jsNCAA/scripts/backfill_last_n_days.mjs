#!/usr/bin/env node
// scripts/backfill.mjs
// ────────────────────────────────────────────────────────────────────────────
// Replays NCAA games from a start date through yesterday to train the model
// AND build a win/loss record using historical odds from The Odds API.
//
// For each day:
//   1. Fetch historical odds (spreads + totals) from the Odds API
//   2. Fetch final scores from ESPN
//   3. Run the model → make picks
//   4. Grade picks against actual results
//   5. Feed into Kalman filter + self-tune weights
//
// Usage:
//   node scripts/backfill.mjs                  # Jan 1 → yesterday
//   node scripts/backfill.mjs 20260201         # Feb 1 → yesterday
//   node scripts/backfill.mjs 20260101 20260301 # Jan 1 → Mar 1
//
// Requires: ODDS_API_KEY in .env (paid tier with historical access)
// Cost: ~10 API credits per day (1 region × 2 markets × 5 credits each)
// ────────────────────────────────────────────────────────────────────────────

import "dotenv/config";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { fetchNCAAStats } from "./sources/ncaa_stats.mjs";
import { fetchScoreboard, extractFinalScores } from "./sources/espn_scoreboard.mjs";
import { loadStore, saveStore, upsertRun } from "./store.mjs";
import {
  loadKalmanState, saveKalmanState, initializeKalman,
  applyDailyDrift, batchUpdate, kalmanSummary, pruneProcessedGames,
} from "./kalman_state.mjs";
import { tuneWeights, computeResidualVar } from "./self_tune.mjs";
import {
  analyzeGame, getAvgs, extractMarginFeatures, loadDefaults,
} from "./model_engine.mjs";
import { blendBase } from "./sources/blend_stats.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CACHE_DIR = path.join(__dirname, "..", "..", "data", "stats_cache", "ncaab");
const ODDS_CACHE_DIR = path.join(__dirname, "..", "..", "data", "odds_cache", "ncaab");

// ── Date helpers ─────────────────────────────────────────────────────────

function todayCST() {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Chicago",
    year: "numeric", month: "2-digit", day: "2-digit",
  }).format(new Date()).replace(/-/g, "");
}

function addDays(yyyymmdd, n) {
  const d = new Date(
    Number(yyyymmdd.slice(0, 4)),
    Number(yyyymmdd.slice(4, 6)) - 1,
    Number(yyyymmdd.slice(6, 8))
  );
  d.setDate(d.getDate() + n);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${y}${m}${dd}`;
}

function dateRange(start, end) {
  const dates = [];
  let cur = start;
  while (cur <= end) {
    dates.push(cur);
    cur = addDays(cur, 1);
  }
  return dates;
}

function fmtDate(d) {
  return `${d.slice(0,4)}-${d.slice(4,6)}-${d.slice(6,8)}`;
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// ── Fuzzy team name matcher ──────────────────────────────────────────────

// ESPN/OddsAPI name variants → Barttorvik keys for ambiguous cases
const TEAM_ALIASES = {
  "miami hurricanes": "Miami FL",
  "miami fl hurricanes": "Miami FL",
  "miami oh redhawks": "Miami OH",
  "miami redhawks": "Miami OH",
  "ohio bobcats": "Ohio",
  "ohio st buckeyes": "Ohio St.",
  "ohio state buckeyes": "Ohio St.",
};

function normKey(s) {
  return String(s || "")
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .replace(/\bstate\b/g, "st")
    .replace(/\s+/g, " ")
    .trim();
}

function safeFuzzy(a, b) {
  if (!a || !b) return false;
  if (a === b) return true;
  const shorter = a.length < b.length ? a : b;
  const longer  = a.length < b.length ? b : a;
  if (shorter.length / longer.length < 0.85) return false;
  return longer.includes(shorter);
}

function resolveTeamFuzzy(stats, name) {
  if (!name) return null;
  if (stats[name]) return name;
  const keys = Object.keys(stats);
  const wanted = normKey(name);

  // 0.5. Check alias map
  const aliased = TEAM_ALIASES[wanted];
  if (aliased && stats[aliased]) return aliased;

  // 1. Exact normKey match
  for (const k of keys) {
    if (normKey(k) === wanted) return k;
  }

  // 2. Safe fuzzy (similar length substrings only)
  for (const k of keys) {
    const nk = normKey(k);
    if (safeFuzzy(nk, wanted)) return k;
  }

  // 3. ESPN sends "School Mascot" (e.g. "Arkansas Razorbacks").
  //    Barttorvik uses just "Arkansas". Check if the ESPN name STARTS WITH
  //    a Barttorvik key (prefix match — word boundary prevents "Kansas"→"Arkansas").
  //    Prefer longest match to avoid "Ohio" beating "Ohio St."
  let prefixMatch = null;
  for (const k of keys) {
    const nk = normKey(k);
    if (nk.length < 3) continue;
    if (wanted.startsWith(nk + " ") || wanted === nk) {
      if (!prefixMatch || nk.length > normKey(prefixMatch).length) prefixMatch = k;
    }
  }
  if (prefixMatch) return prefixMatch;

  // 4. Barttorvik key starts with first word of wanted (e.g. "Penn" starts with "Penn" from "Pennsylvania Quakers")
  const firstWord = wanted.split(" ")[0];
  if (firstWord.length >= 3) {
    for (const k of keys) {
      const nk = normKey(k);
      if (nk === firstWord || (nk.length >= 3 && firstWord.startsWith(nk))) return k;
    }
  }

  // 5. Last-word mascot match (e.g. "Wright St." → "st" won't work, need >= 4 chars)
  const lastWord = wanted.split(" ").pop();
  if (lastWord.length >= 4) {
    for (const k of keys) {
      const kLast = normKey(k).split(" ").pop();
      if (kLast === lastWord) return k;
    }
  }
  return null;
}

// ── Historical Odds from The Odds API ────────────────────────────────────

const BASE = "https://api.the-odds-api.com/v4";

function normTeam(name) {
  return String(name || "").trim().replace(/\s+/g, " ");
}

function pickBestBookmaker(bookmakers) {
  if (!Array.isArray(bookmakers) || bookmakers.length === 0) return null;
  const preferred = ["DraftKings", "FanDuel", "BetMGM", "Caesars", "PointsBet", "BetRivers"];
  for (const p of preferred) {
    const b = bookmakers.find((x) => x?.title === p);
    if (b) return b;
  }
  return bookmakers[0];
}

// Sportsbook convention: -X = home favored by X, +X = away favored by X
function toModelLine(homeTeam, awayTeam, spreadPoints, teamForSpread) {
  if (!Number.isFinite(spreadPoints)) return null;
  const isHome = teamForSpread === homeTeam;
  const isAway = teamForSpread === awayTeam;
  if (!isHome && !isAway) return null;
  if (isHome) return spreadPoints;
  return -spreadPoints;
}

function parseOddsSnapshot(data) {
  // Parse a single historical odds API response into game objects with commence times
  if (!Array.isArray(data)) return [];
  const games = [];
  for (const ev of data) {
    const home = normTeam(ev?.home_team);
    const away = normTeam(ev?.away_team);
    if (!home || !away) continue;

    const commence = ev?.commence_time ? new Date(ev.commence_time) : null;
    const book = pickBestBookmaker(ev?.bookmakers);
    let line = null;
    let total = null;

    const spreads = book?.markets?.find(m => m?.key === "spreads");
    const totals = book?.markets?.find(m => m?.key === "totals");

    if (spreads?.outcomes?.length) {
      const out = spreads.outcomes.find(o => Number.isFinite(Number(o?.point)));
      if (out) {
        line = toModelLine(home, away, Number(out.point), normTeam(out.name));
      }
    }

    if (totals?.outcomes?.length) {
      const out = totals.outcomes.find(o => Number.isFinite(Number(o?.point)));
      if (out) total = Number(out.point);
    }

    if (line != null) {
      games.push({ away, home, line, total, commence, _book: book?.title ?? null });
    }
  }
  return games;
}

async function fetchSnapshotAt(dateYYYYMMDD, hourUTC) {
  const apiKey = process.env.ODDS_API_KEY;
  if (!apiKey) throw new Error("Missing ODDS_API_KEY in .env");

  const hh = String(hourUTC).padStart(2, "0");
  const isoDate = `${fmtDate(dateYYYYMMDD)}T${hh}:00:00Z`;

  const url =
    `${BASE}/historical/sports/basketball_ncaab/odds?` +
    `apiKey=${encodeURIComponent(apiKey)}` +
    `&regions=us` +
    `&markets=spreads,totals` +
    `&oddsFormat=american` +
    `&date=${encodeURIComponent(isoDate)}`;

  const res = await fetch(url);
  if (!res.ok) {
    const txt = await res.text().catch(() => "");
    if (res.status === 422) return { data: [], snapshotTime: new Date(isoDate) };
    throw new Error(`Odds API ${res.status}: ${txt.slice(0, 200)}`);
  }

  const json = await res.json();
  const raw = json.data || json;
  const remaining = res.headers.get("x-requests-remaining");
  if (remaining) {
    const rem = parseInt(remaining);
    if (rem < 100) console.log(`  ⚠ Odds API credits remaining: ${rem}`);
  }

  return { data: parseOddsSnapshot(raw), snapshotTime: new Date(isoDate) };
}

async function fetchHistoricalOdds(dateYYYYMMDD) {
  // Check disk cache first
  if (!fs.existsSync(ODDS_CACHE_DIR)) fs.mkdirSync(ODDS_CACHE_DIR, { recursive: true });
  const cachePath = path.join(ODDS_CACHE_DIR, dateYYYYMMDD + ".json");
  if (fs.existsSync(cachePath)) {
    try {
      const raw = JSON.parse(fs.readFileSync(cachePath, "utf8"));
      // Support both array (old format) and object (shared cache format)
      let cached;
      if (Array.isArray(raw)) {
        cached = raw;
      } else {
        cached = Object.entries(raw)
          .filter(([, v]) => v.line != null)
          .map(([k, v]) => {
            const [away, home] = k.split("@");
            return { home, away, line: v.line, total: v.total, _book: v._book };
          });
      }
      console.log(`  [odds] ${dateYYYYMMDD}: loaded from cache (${cached.length} games)`);
      return cached;
    } catch (e) { /* fall through to API */ }
  }

  // Take multiple snapshots throughout the day to get pre-game lines
  // ~1.5 hours before each tipoff window.
  // NCAA tipoff windows (ET): noon(17UTC), 2pm(19UTC), 4pm(21UTC), 7pm(00UTC), 9pm(02UTC)
  // Snapshots: 15:00, 17:30, 19:30, 22:00, 00:30+1 UTC
  const snapshotHours = [13, 15, 17, 19, 21, 23]; // 8am, 10am, noon, 2pm, 4pm, 6pm ET

  const snapshots = [];
  for (const h of snapshotHours) {
    try {
      const snap = await fetchSnapshotAt(dateYYYYMMDD, h);
      if (snap.data.length > 0) snapshots.push(snap);
      await sleep(200); // be nice to API
    } catch (err) {
      // Skip failed snapshots
    }
  }

  if (snapshots.length === 0) return [];

  // For each game, pick the snapshot closest to (but at least 90 min before) tipoff.
  // If no snapshot is 90min+ before tipoff, use the earliest available snapshot.
  // Key by normalized home|away to deduplicate across snapshots.
  const bestLines = new Map(); // key → { game, timeBefore }

  for (const snap of snapshots) {
    for (const g of snap.data) {
      const key = `${normKey(g.home)}|${normKey(g.away)}`;
      const tipoff = g.commence;

      if (!tipoff) {
        // No commence time — keep the earliest snapshot's line
        if (!bestLines.has(key)) bestLines.set(key, { game: g, timeBefore: Infinity });
        continue;
      }

      const msBefore = tipoff.getTime() - snap.snapshotTime.getTime();
      const minBefore = msBefore / 60000;

      // Must be at least 60 min before tipoff (pre-game line)
      if (minBefore < 60) continue;

      const existing = bestLines.get(key);
      if (!existing) {
        bestLines.set(key, { game: g, timeBefore: minBefore });
      } else {
        // Prefer the snapshot closest to tipoff (but still 60+ min before)
        if (minBefore < existing.timeBefore) {
          bestLines.set(key, { game: g, timeBefore: minBefore });
        }
      }
    }
  }

  const result = Array.from(bestLines.values()).map(v => {
    // Strip non-serializable Date objects for cache
    const { commence, ...rest } = v.game;
    return rest;
  });

  // Cache to disk
  try { fs.writeFileSync(cachePath, JSON.stringify(result, null, 2)); }
  catch (e) { /* non-critical */ }

  return result;
}

// ── Grading functions ────────────────────────────────────────────────────

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

// ── Main ────────────────────────────────────────────────────────────────

async function main() {
  const args = process.argv.slice(2);
  const startDate = args[0] || "20260101";
  const endDate   = args[1] || addDays(todayCST(), -1);

  const dates = dateRange(startDate, endDate);
  console.log(`\n══════════════════════════════════════════════════════════`);
  console.log(`  NCAA BACKFILL (with historical odds)`);
  console.log(`  ${fmtDate(startDate)} → ${fmtDate(endDate)} (${dates.length} days)`);
  console.log(`══════════════════════════════════════════════════════════\n`);

  // ── Step 1: Stats cache (per-date when available, else live Barttorvik) ──
  if (!fs.existsSync(CACHE_DIR)) fs.mkdirSync(CACHE_DIR, { recursive: true });

  // In-memory cache so we only fetch once per date
  const statsCache = new Map();
  let liveFetched = null; // lazy — only fetch from Barttorvik if needed

  async function getStatsForDate(dateYYYYMMDD) {
    if (statsCache.has(dateYYYYMMDD)) return statsCache.get(dateYYYYMMDD);

    // Check disk cache first (saved by run_daily.mjs)
    const diskPath = path.join(CACHE_DIR, dateYYYYMMDD + ".json");
    if (fs.existsSync(diskPath)) {
      try {
        const raw = JSON.parse(fs.readFileSync(diskPath, "utf8"));
        const enhanced = raw.season ? raw : { season: raw, last10: null, home: null, away: null };
        statsCache.set(dateYYYYMMDD, enhanced);
        console.log(`  [cache] Loaded stats from disk cache for ${dateYYYYMMDD}`);
        return enhanced;
      } catch (e) {
        console.warn(`  [cache] Failed to read ${diskPath}: ${e.message}`);
      }
    }

    // No cache — fall back to live Barttorvik fetch (same stats for all uncached dates)
    if (!liveFetched) {
      console.log("[stats] No cache for this date — fetching live from Barttorvik...");
      const raw = await fetchNCAAStats();
      liveFetched = { season: raw, last10: null, home: null, away: null };
      console.log(`  Got ${Object.keys(raw).length} teams`);
    }
    statsCache.set(dateYYYYMMDD, liveFetched);
    return liveFetched;
  }

  // ── Step 2: Load / initialize state ────────────────────────────────
  let store = loadStore();
  const { DEFAULT_W, DEFAULT_W_VAR } = loadDefaults();

  let W     = { ...DEFAULT_W, ...(store.weights || {}) };
  let W_var = { ...DEFAULT_W_VAR, ...(store.weightsVar || {}) };

  let kalman = loadKalmanState();
  let kalmanInitialized = kalman.teams && Object.keys(kalman.teams).length > 0;
  if (kalmanInitialized) {
    console.log(`[2/3] Loaded existing Kalman state (${Object.keys(kalman.teams).length} teams)`);
  } else {
    console.log("[2/3] Kalman will initialize on first day's stats");
  }

  // ── Step 3: Process each day ──────────────────────────────────────
  console.log("\n[3/3] Processing games day by day...\n");

  let totalGames = 0;
  let totalDays = 0;
  let matchFailures = 0;
  let totalPicks = 0;
  let totalWins = 0;
  let totalLosses = 0;
  let totalPushes = 0;

  for (const date of dates) {
    // ── Fetch per-date stats (from cache or live) ────────────────
    const enhanced = await getStatsForDate(date);
    const blended = blendBase(enhanced.season, null, 0);
    const avgs = getAvgs(blended);

    // Initialize Kalman on first day if needed
    if (!kalmanInitialized) {
      kalman = initializeKalman(blended);
      kalmanInitialized = true;
    }

    // ── Fetch historical odds ──────────────────────────────────────
    let odds = [];
    try {
      odds = await fetchHistoricalOdds(date);
    } catch (err) {
      console.log(`  ${fmtDate(date)}: Odds API failed (${err.message}) — no lines for this day`);
    }

    // Build lookup: OddsAPI team name → { line, total }
    const oddsMap = new Map();
    for (const o of odds) {
      // Key by both home and away normalized names
      const key = `${normKey(o.home)}|${normKey(o.away)}`;
      oddsMap.set(key, o);
    }

    // ── Fetch final scores from ESPN ──────────────────────────────
    let scores;
    try {
      const sb = await fetchScoreboard(date);
      scores = extractFinalScores(sb);
    } catch (err) {
      console.log(`  ${fmtDate(date)}: ESPN fetch failed (${err.message}) — skipping`);
      continue;
    }

    if (!scores.length) continue;

    // Drift first — time has passed since last day's learn step
    applyDailyDrift(kalman, date);

    const games = [];
    let dayMatched = 0;
    let dayUnmatched = 0;
    let dayPicks = 0;
    let dayWins = 0;
    let dayLosses = 0;

    const dynamicResidualVar = computeResidualVar(store.runs);
    store.residualVar = dynamicResidualVar;

    for (const s of scores) {
      const homeKey = resolveTeamFuzzy(blended, s.home);
      const awayKey = resolveTeamFuzzy(blended, s.away);

      if (!homeKey || !awayKey) {
        dayUnmatched++;
        continue;
      }

      // ── Match to odds ────────────────────────────────────────────
      // Try to find the odds for this game by fuzzy matching team names
      let matchedOdds = null;
      const espnHomeNorm = normKey(s.home);
      const espnAwayNorm = normKey(s.away);
      const bartHomeNorm = normKey(homeKey);
      const bartAwayNorm = normKey(awayKey);

      for (const [key, o] of oddsMap) {
        const oddsHomeNorm = normKey(o.home);
        const oddsAwayNorm = normKey(o.away);

        // Try multiple matching strategies
        const homeMatch = oddsHomeNorm === espnHomeNorm
          || oddsHomeNorm === bartHomeNorm
          || oddsHomeNorm.includes(espnHomeNorm) || espnHomeNorm.includes(oddsHomeNorm)
          || oddsHomeNorm.includes(bartHomeNorm) || bartHomeNorm.includes(oddsHomeNorm);

        const awayMatch = oddsAwayNorm === espnAwayNorm
          || oddsAwayNorm === bartAwayNorm
          || oddsAwayNorm.includes(espnAwayNorm) || espnAwayNorm.includes(oddsAwayNorm)
          || oddsAwayNorm.includes(bartAwayNorm) || bartAwayNorm.includes(oddsAwayNorm);

        if (homeMatch && awayMatch) {
          matchedOdds = o;
          break;
        }
      }

      const line = matchedOdds?.line ?? 0;
      const total = matchedOdds?.total ?? 0;

      const g = {
        away: awayKey,
        home: homeKey,
        line,
        _date: date,
        total,
      };

      const result = analyzeGame(g, blended, avgs, W, null, kalman, W_var, dynamicResidualVar);

      if (!result) {
        dayUnmatched++;
        continue;
      }

      // Attach actual scores
      result.homeScore = s.homeScore;
      result.awayScore = s.awayScore;
      result._kalmanDate = date;

      // If no real odds were matched, mark picks as PASS
      if (!matchedOdds) {
        result.sPick = "PASS";
        result.sConf = "low";
        result.oPick = "PASS";
        result.oConf = "low";
      }

      // Grade picks
      if (result.sPick !== "PASS") {
        const sResult = gradeSpread(result);
        result.sResult = sResult;
        if (sResult === "WIN" || sResult === "LOSS") {
          dayPicks++;
          if (sResult === "WIN") dayWins++;
          else dayLosses++;
        } else if (sResult === "PUSH") {
          totalPushes++;
        }
      }

      if (result.oPick !== "PASS") {
        result.oResult = gradeTotal(result);
      }

      games.push(result);
      dayMatched++;
    }

    if (!games.length) {
      matchFailures += dayUnmatched;
      continue;
    }

    // ── Kalman update: learn from today's results ──────────────
    batchUpdate(kalman, games);

    // ── Self-tune ────────────────────────────────────────────────
    // Now Signal 2 (threshold tuning) CAN fire because we have graded picks
    const tuned = tuneWeights(W, W_var, games);
    W     = tuned.W;
    W_var = tuned.W_var;

    // ── Store the run ────────────────────────────────────────────
    upsertRun(store, {
      date,
      dateDisplay: fmtDate(date),
      burnIn: false,  // real picks with real odds — count in record
      weightsUsed: { ...W },
      games,
    });

    store.weights    = W;
    store.weightsVar = W_var;
    store.lastTuneDate = date;

    totalGames += dayMatched;
    totalDays++;
    totalPicks += dayPicks;
    totalWins += dayWins;
    totalLosses += dayLosses;
    matchFailures += dayUnmatched;

    // Progress log
    if (totalDays % 7 === 0 || date === endDate) {
      const pct = totalPicks > 0 ? ((totalWins / (totalWins + totalLosses)) * 100).toFixed(1) : "N/A";
      console.log(`  ${fmtDate(date)}: ${dayMatched} games (${dayPicks} picks: ${dayWins}W-${dayLosses}L) | ` +
        `Season: ${totalWins}-${totalLosses} (${pct}%) | ` +
        `hca=${W.hca} wTS=${W.wTS}`);
    }

    // Rate limit: Odds API allows ~500 req/min, but be nice
    // Small delay between days to avoid hammering
    await sleep(300);
  }

  // ── Prune + Save ─────────────────────────────────────────────────
  pruneProcessedGames(kalman, 90);
  saveStore(store);
  saveKalmanState(kalman);

  // ── Summary ──────────────────────────────────────────────────────
  const winPct = totalPicks > 0 ? ((totalWins / (totalWins + totalLosses)) * 100).toFixed(1) : "N/A";
  const units = totalWins * 0.91 - totalLosses;

  console.log(`\n══════════════════════════════════════════════════════════`);
  console.log(`  BACKFILL COMPLETE`);
  console.log(`══════════════════════════════════════════════════════════`);
  console.log(`  Days processed:   ${totalDays}`);
  console.log(`  Games processed:  ${totalGames}`);
  console.log(`  Unmatched teams:  ${matchFailures}`);
  console.log(`──────────────────────────────────────────────────────────`);
  console.log(`  SPREAD RECORD:    ${totalWins}-${totalLosses}-${totalPushes}  (${winPct}%)`);
  console.log(`  Units:            ${units > 0 ? "+" : ""}${units.toFixed(2)}u`);
  console.log(`──────────────────────────────────────────────────────────`);
  console.log(`  Final weights:`);
  console.log(`    wTS=${W.wTS}  wTO=${W.wTO}  wORR=${W.wORR}  wNET=${W.wNET}`);
  console.log(`    hca=${W.hca}  constant=${W.constant}  paceAdj=${W.paceAdj}`);
  console.log(`    probHigh=${W.probHigh}  probOUElite=${W.probOUElite}`);
  console.log(`──────────────────────────────────────────────────────────`);
  console.log(kalmanSummary(kalman, 15));
  console.log(`══════════════════════════════════════════════════════════\n`);
}

main().catch(err => {
  console.error("Backfill failed:", err);
  process.exit(1);
});
