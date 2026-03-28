// sources/odds_batch_historical.mjs
// ────────────────────────────────────────────────────────────────────────────
// BATCH historical odds fetch — gets ALL games for a date in 1-2 API calls
// instead of per-game (10 games × 3 retries = 30 calls → 1-2 calls).
//
// Uses disk cache so re-runs don't hit the API at all.
// ────────────────────────────────────────────────────────────────────────────

import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ODDS_CACHE_DIR = path.join(__dirname, "..", "..", "..", "data", "odds_cache", "nba");

const BASE = "https://api.the-odds-api.com/v4";

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function normKey(s) {
  return String(s || "").toLowerCase().replace(/[^a-z0-9\s]/g, " ").replace(/\s+/g, " ").trim();
}

function normTeam(name) {
  return String(name || "").trim().replace(/\s+/g, " ");
}

const TEAM_ALIASES = {
  "la lakers":      ["los angeles lakers", "la lakers"],
  "la clippers":    ["los angeles clippers", "la clippers"],
  "golden state":   ["golden state warriors", "golden state"],
  "oklahoma city":  ["oklahoma city thunder", "oklahoma city"],
  "new orleans":    ["new orleans pelicans", "new orleans"],
  "new york":       ["new york knicks", "new york"],
  "san antonio":    ["san antonio spurs", "san antonio"],
  "portland":       ["portland trail blazers", "portland"],
  "philadelphia":   ["philadelphia 76ers", "philadelphia"],
};

function expandAliases(name) {
  const k = normKey(name);
  return TEAM_ALIASES[k] || [k];
}

function pickBookmaker(bookmakers) {
  if (!Array.isArray(bookmakers) || bookmakers.length === 0) return null;
  const preferred = ["DraftKings", "FanDuel", "BetMGM", "Caesars", "PointsBet", "BetRivers"];
  for (const title of preferred) {
    const found = bookmakers.find(b => b?.title === title);
    if (found) return found;
  }
  return bookmakers[0];
}

function findMarket(bookmaker, key) {
  if (!bookmaker?.markets) return null;
  return bookmaker.markets.find(m => m?.key === key) || null;
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

async function fetchWithRetry(url, tries = 5) {
  let wait = 700;
  for (let i = 0; i < tries; i++) {
    const res = await fetch(url);
    if (res.status !== 429) return res;
    console.warn(`  [odds_batch] 429 rate limited, retrying in ${wait}ms...`);
    await sleep(wait);
    wait = Math.min(wait * 2, 8000);
  }
  return fetch(url);
}

// ── Fetch one snapshot from the API ─────────────────────────────────────────

async function fetchSnapshot(apiKey, ts) {
  const url =
    `${BASE}/historical/sports/basketball_nba/odds?` +
    `apiKey=${encodeURIComponent(apiKey)}` +
    `&regions=us` +
    `&markets=spreads,totals` +
    `&oddsFormat=american` +
    `&date=${encodeURIComponent(ts)}`;

  const res = await fetchWithRetry(url);
  if (!res.ok) {
    const txt = await res.text().catch(() => "");
    throw new Error(`Historical odds fetch failed: ${res.status} ${txt}`);
  }

  const json = await res.json();
  return Array.isArray(json?.data) ? json.data : [];
}

// ── Extract odds for one game from a snapshot ───────────────────────────────

function extractOddsForGame(data, home, away) {
  const homeAliases = expandAliases(home);
  const awayAliases = expandAliases(away);

  const ev =
    data.find(e => {
      const h = normKey(e?.home_team);
      const a = normKey(e?.away_team);
      return homeAliases.includes(h) && awayAliases.includes(a);
    }) ||
    data.find(e => {
      const h = normKey(e?.home_team);
      const a = normKey(e?.away_team);
      return homeAliases.some(ha => h.includes(ha) || ha.includes(h)) &&
             awayAliases.some(aa => a.includes(aa) || aa.includes(a));
    });

  if (!ev) return null;

  const homeTeam = normTeam(ev.home_team);
  const awayTeam = normTeam(ev.away_team);
  const book = pickBookmaker(ev?.bookmakers);

  let line = null, total = null;

  const spreads = book ? findMarket(book, "spreads") : null;
  const totals = book ? findMarket(book, "totals") : null;

  if (spreads?.outcomes?.length) {
    const out = spreads.outcomes.find(o => Number.isFinite(Number(o?.point)));
    if (out) line = toModelLine(homeTeam, awayTeam, Number(out.point), normTeam(out.name));
  }

  if (totals?.outcomes?.length) {
    const out = totals.outcomes.find(o => Number.isFinite(Number(o?.point)));
    if (out) total = Number(out.point);
  }

  return { line, total, _book: book?.title ?? null };
}

// ── Main export: fetch ALL odds for a date in 1-2 API calls ─────────────────
//
// Strategy: fetch a snapshot at a time when all games should have lines posted
// but none have tipped off yet. For NBA that's ~5:00 PM ET (22:00 UTC) on
// game day. If any games are missing, try an earlier snapshot.
//
// Returns: Map of "away@home" → { line, total, _book, _note }

export async function fetchOddsForDay(dateYYYYMMDD, gamesList) {
  const apiKey = process.env.ODDS_API_KEY;

  // Check disk cache first
  if (!fs.existsSync(ODDS_CACHE_DIR)) fs.mkdirSync(ODDS_CACHE_DIR, { recursive: true });
  const cachePath = path.join(ODDS_CACHE_DIR, dateYYYYMMDD + ".json");
  if (fs.existsSync(cachePath)) {
    const cached = JSON.parse(fs.readFileSync(cachePath, "utf8"));
    // Fuzzy-match cache keys to ESPN names via alias expansion
    if (gamesList?.length) {
      const cacheEntries = Object.entries(cached);
      for (const g of gamesList) {
        const espnKey = `${g.away}@${g.home}`;
        if (cached[espnKey]) continue;
        const awayAliases = expandAliases(g.away);
        const homeAliases = expandAliases(g.home);
        for (const [ck, cv] of cacheEntries) {
          const [cAway, cHome] = ck.split("@").map(normKey);
          if (awayAliases.includes(cAway) && homeAliases.includes(cHome)) {
            cached[espnKey] = cv;
            break;
          }
        }
      }
    }
    console.log(`  [odds_batch] ${dateYYYYMMDD}: loaded from cache (${Object.keys(cached).length} games)`);
    return cached;
  }

  if (!apiKey) throw new Error("Missing ODDS_API_KEY env var (no cache found, need API).");

  // Build snapshot timestamps to try.
  // Priority: per-game commence-based times FIRST (90 min before tipoff),
  // then fixed fallbacks. This avoids grabbing live/in-game lines for
  // early-start games (e.g. Sunday noon tips).
  const dateStr = `${dateYYYYMMDD.slice(0,4)}-${dateYYYYMMDD.slice(4,6)}-${dateYYYYMMDD.slice(6,8)}`;

  // Game-specific times: 90 min before each game's tipoff
  const gameSpecificTimes = [];
  for (const g of gamesList) {
    if (g.commenceTimeIso) {
      const d = new Date(g.commenceTimeIso);
      if (!Number.isNaN(d.getTime())) {
        d.setMinutes(d.getMinutes() - 90);
        gameSpecificTimes.push(d.toISOString().replace(/\.\d{3}Z$/, "Z"));
      }
    }
  }

  // Fixed fallbacks (only used if commence times aren't available)
  const fallbacks = [
    `${dateStr}T16:00:00Z`,  // 4 PM UTC — 11 AM ET, catches early tips
    `${dateStr}T23:00:00Z`,  // 11 PM UTC — 6 PM ET, catches evening tips
  ];

  // Deduplicate: game-specific first, then fallbacks
  const allTimes = [...new Set([...gameSpecificTimes, ...fallbacks])];

  const results = {};
  let snapshotData = null;

  for (const ts of allTimes) {
    try {
      snapshotData = await fetchSnapshot(apiKey, ts);
      await sleep(500);  // one sleep between snapshot calls
    } catch (e) {
      console.warn(`  [odds_batch] Snapshot ${ts} failed: ${e.message}`);
      continue;
    }

    if (!snapshotData || snapshotData.length === 0) continue;

    // Extract odds for all games we're looking for
    let found = 0;
    for (const g of gamesList) {
      const key = `${g.away}@${g.home}`;
      if (results[key] && typeof results[key].line === "number") continue; // already found

      const odds = extractOddsForGame(snapshotData, g.home, g.away);
      if (odds && typeof odds.line === "number") {
        results[key] = { ...odds, _note: `batch snapshot ${ts}` };
        found++;
      }
    }

    console.log(`  [odds_batch] Snapshot ${ts.slice(11, 16)} UTC: found ${found} new games (${Object.keys(results).length}/${gamesList.length} total)`);

    // If we found all games, stop
    if (Object.keys(results).length >= gamesList.length) break;
  }

  // Fill missing games with null
  for (const g of gamesList) {
    const key = `${g.away}@${g.home}`;
    if (!results[key]) {
      results[key] = { line: null, total: null, _book: null, _note: "Not found in any snapshot" };
    }
  }

  // Cache to disk
  try { fs.writeFileSync(cachePath, JSON.stringify(results, null, 2)); }
  catch (e) { /* non-critical */ }

  return results;
}
