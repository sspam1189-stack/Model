#!/usr/bin/env node
// Fetch historical odds for every day from START_DATE to yesterday
// and write to the jsFull odds_cache (which pyFull, jsNBA, pyNBA share via junctions).
//
// Usage: ODDS_API_KEY=xxx node scripts/fetch_all_odds_cache.mjs [sport] [startDate]
//   sport: basketball_nba (default) or basketball_ncaab
//   startDate: YYYYMMDD (default: 20251022)

import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const API_KEY = process.env.ODDS_API_KEY || "6c5699682d30fc8664737160274f8d12";

const sport = process.argv[2] || "basketball_nba";
const startArg = process.argv[3] || "20251022";
const BASE = "https://api.the-odds-api.com/v4";

// Shared cache dirs at project root
const cacheMap = {
  basketball_nba: path.join(__dirname, "..", "data", "odds_cache", "nba"),
  basketball_ncaab: path.join(__dirname, "..", "data", "odds_cache", "ncaab"),
};
const CACHE_DIR = cacheMap[sport] || cacheMap.basketball_nba;

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function toDate(yyyymmdd) {
  return new Date(`${yyyymmdd.slice(0,4)}-${yyyymmdd.slice(4,6)}-${yyyymmdd.slice(6,8)}T12:00:00Z`);
}

function fmtDate(d) {
  return d.toISOString().slice(0, 10).replace(/-/g, "");
}

function normTeam(name) {
  return String(name || "").trim().replace(/\s+/g, " ");
}

function normKey(s) {
  return String(s || "").toLowerCase().replace(/[^a-z0-9\s]/g, " ").replace(/\s+/g, " ").trim();
}

function pickBookmaker(bookmakers) {
  if (!Array.isArray(bookmakers) || !bookmakers.length) return null;
  const preferred = ["DraftKings", "FanDuel", "BetMGM", "Caesars", "PointsBet", "BetRivers"];
  for (const p of preferred) {
    const b = bookmakers.find(x => x?.title === p);
    if (b) return b;
  }
  return bookmakers[0];
}

function findMarket(book, key) {
  return book?.markets?.find(m => m?.key === key) || null;
}

// Sportsbook convention: -X = home favored
function toModelLine(home, away, pts, teamFor) {
  if (!Number.isFinite(pts)) return null;
  if (teamFor === home) return pts;
  if (teamFor === away) return -pts;
  return null;
}

async function fetchWithRetry(url, tries = 5) {
  let wait = 1000;
  for (let i = 0; i < tries; i++) {
    const res = await fetch(url);
    if (res.status !== 429) return res;
    console.warn(`  429 rate limited, retrying in ${wait}ms...`);
    await sleep(wait);
    wait = Math.min(wait * 2, 10000);
  }
  return fetch(url);
}

async function fetchSnapshot(ts) {
  const url =
    `${BASE}/historical/sports/${sport}/odds?` +
    `apiKey=${encodeURIComponent(API_KEY)}` +
    `&regions=us&markets=spreads,totals&oddsFormat=american` +
    `&date=${encodeURIComponent(ts)}`;

  const res = await fetchWithRetry(url);
  if (!res.ok) {
    const txt = await res.text().catch(() => "");
    throw new Error(`${res.status} ${txt.slice(0, 200)}`);
  }
  const json = await res.json();
  return Array.isArray(json?.data) ? json.data : [];
}

function extractAllGames(data) {
  const results = {};
  for (const ev of data) {
    const home = normTeam(ev?.home_team);
    const away = normTeam(ev?.away_team);
    if (!home || !away) continue;

    const book = pickBookmaker(ev?.bookmakers);
    let line = null, total = null;

    const spreads = book ? findMarket(book, "spreads") : null;
    const totals = book ? findMarket(book, "totals") : null;

    if (spreads?.outcomes?.length) {
      const out = spreads.outcomes.find(o => Number.isFinite(Number(o?.point)));
      if (out) line = toModelLine(home, away, Number(out.point), normTeam(out.name));
    }
    if (totals?.outcomes?.length) {
      const out = totals.outcomes.find(o => Number.isFinite(Number(o?.point)));
      if (out) total = Number(out.point);
    }

    const key = `${away}@${home}`;
    results[key] = { line, total, _book: book?.title ?? null, _note: "historical fetch" };
  }
  return results;
}

async function main() {
  if (!fs.existsSync(CACHE_DIR)) fs.mkdirSync(CACHE_DIR, { recursive: true });

  const start = toDate(startArg);
  const yesterday = new Date();
  yesterday.setDate(yesterday.getDate() - 1);

  let fetched = 0, skipped = 0, failed = 0;
  const d = new Date(start);

  while (d <= yesterday) {
    const dateKey = fmtDate(d);
    const cachePath = path.join(CACHE_DIR, dateKey + ".json");

    if (fs.existsSync(cachePath)) {
      skipped++;
      d.setDate(d.getDate() + 1);
      continue;
    }

    const dateStr = `${dateKey.slice(0,4)}-${dateKey.slice(4,6)}-${dateKey.slice(6,8)}`;
    // Two snapshots: early afternoon + late evening
    const snapshots = [
      `${dateStr}T16:00:00Z`,  // 4 PM UTC — 11 AM ET, catches early tips
      `${dateStr}T23:00:00Z`,  // 11 PM UTC — 6 PM ET, catches evening tips
    ];

    try {
      const allResults = {};
      for (const ts of snapshots) {
        const data = await fetchSnapshot(ts);
        if (data && data.length > 0) {
          const games = extractAllGames(data);
          for (const [k, v] of Object.entries(games)) {
            if (!allResults[k] || allResults[k].line == null) {
              allResults[k] = v;
            }
          }
        }
        await sleep(500);
      }
      fs.writeFileSync(cachePath, JSON.stringify(allResults, null, 2));
      console.log(`${dateKey}: ${Object.keys(allResults).length} games cached`);
      fetched++;
    } catch (e) {
      console.error(`${dateKey}: FAILED - ${e.message}`);
      failed++;
      await sleep(2000);
    }

    d.setDate(d.getDate() + 1);
  }

  console.log(`\nDone. Fetched: ${fetched}, Skipped (cached): ${skipped}, Failed: ${failed}`);
}

main().catch(e => { console.error(e); process.exit(1); });
