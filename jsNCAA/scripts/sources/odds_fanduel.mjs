// scripts/sources/odds_fanduel.mjs
// Fetch NCAA basketball game spreads + totals from FanDuel's public API.
// No API key needed.

import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ODDS_CACHE_DIR = path.join(__dirname, "..", "..", "..", "data", "odds_cache", "ncaab");

const FD_BASE = "https://sbapi.mi.sportsbook.fanduel.com/api";
const FD_API_KEY = "FhMFpcPWXMeyZxOx";

function todayChicago() {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Chicago",
    year: "numeric", month: "2-digit", day: "2-digit",
  }).format(new Date());
}

function normTeam(name) { return String(name || "").trim(); }

function toModelLine(home, away, spreadPts, spreadTeam) {
  if (!spreadTeam) return spreadPts;
  const st = normTeam(spreadTeam).toLowerCase();
  const h = normTeam(home).toLowerCase();
  const a = normTeam(away).toLowerCase();
  if (st.includes(h) || h.includes(st)) return spreadPts;
  if (st.includes(a) || a.includes(st)) return -spreadPts;
  return spreadPts;
}

export async function fetchFanDuelNCAABOdds() {
  const url = `${FD_BASE}/content-managed-page?page=CUSTOM&customPageId=ncaab&_ak=${FD_API_KEY}`;
  let data;
  try {
    const res = await fetch(url, {
      headers: { "User-Agent": "Mozilla/5.0", Accept: "application/json" },
    });
    if (!res.ok) {
      console.warn(`  [fanduel] API returned ${res.status}`);
      return [];
    }
    data = await res.json();
  } catch (e) {
    console.warn(`  [fanduel] Fetch error: ${e.message}`);
    return [];
  }

  const attachments = data.attachments || {};
  const events = attachments.events || {};
  const markets = attachments.markets || {};

  const today = todayChicago();
  const games = [];
  for (const [eid, ev] of Object.entries(events)) {
    const name = ev.name || "";
    if (!name.includes("@")) continue;
    if (name.includes("(W)")) continue; // Skip women's games

    // Filter to today's games only (Chicago time)
    const openDate = ev.openDate || "";
    if (openDate) {
      try {
        const dt = new Date(openDate);
        const gameDate = new Intl.DateTimeFormat("en-CA", {
          timeZone: "America/Chicago",
          year: "numeric", month: "2-digit", day: "2-digit",
        }).format(dt);
        if (gameDate !== today) continue;
      } catch (e) { /* ignore parse errors */ }
    }

    const parts = name.split(" @ ");
    if (parts.length !== 2) continue;
    const away = normTeam(parts[0]);
    const home = normTeam(parts[1]);

    const evMarkets = Object.values(markets).filter(
      (m) => String(m.eventId) === String(eid)
    );

    let line = null;
    let total = null;

    for (const m of evMarkets) {
      const mt = m.marketType || "";
      if (mt.includes("HANDICAP") && mt.includes("2-WAY")) {
        for (const r of m.runners || []) {
          if (r.handicap != null && Number(r.handicap) < 0) {
            line = toModelLine(home, away, Number(r.handicap), r.runnerName || "");
          }
        }
      }
      if (mt.includes("TOTAL_POINTS")) {
        for (const r of m.runners || []) {
          if (r.runnerName === "Over" && r.handicap != null) {
            total = Number(r.handicap);
          }
        }
      }
    }

    games.push({ away, home, line, total, _book: "FanDuel" });
  }

  // Cache
  if (games.length) {
    try {
      const dateKey = todayChicago().replace(/-/g, "");
      if (!fs.existsSync(ODDS_CACHE_DIR)) fs.mkdirSync(ODDS_CACHE_DIR, { recursive: true });
      const cachePath = path.join(ODDS_CACHE_DIR, dateKey + ".json");
      let existing = {};
      if (fs.existsSync(cachePath)) {
        existing = JSON.parse(fs.readFileSync(cachePath, "utf8"));
      }
      for (const g of games) {
        if (g.line != null || g.total != null) {
          existing[`${g.away}@${g.home}`] = { line: g.line, total: g.total, _book: "FanDuel", _note: "fanduel live" };
        }
      }
      fs.writeFileSync(cachePath, JSON.stringify(existing, null, 2));
      console.log(`  [fanduel] Cached ${games.length} games to ${dateKey}.json`);
    } catch (e) { /* non-critical */ }
  }

  console.log(`  [fanduel] Fetched ${games.length} NCAAB men's games with spreads/totals`);
  return games;
}
