// scripts/sources/odds_theoddsapi.mjs
// Fetch NBA spreads + totals from The Odds API and return in your bot format.
// Requires env var: ODDS_API_KEY

import { fetchClosingOddsForGame } from "./odds_theoddsapi_historical.mjs";
import { fetchScoreboard } from "./espn_scoreboard.mjs";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ODDS_CACHE_DIR = path.join(__dirname, "..", "..", "..", "data", "odds_cache", "nba");

const BASE = "https://api.the-odds-api.com/v4";

function extractAllESPNGames(scoreboardJson) {
  const out = [];
  for (const ev of (scoreboardJson?.events || [])) {
    const comp = ev.competitions?.[0];
    if (!comp) continue;
    const competitors = comp.competitors || [];
    const awayC = competitors.find(c => c.homeAway === "away");
    const homeC = competitors.find(c => c.homeAway === "home");
    if (!awayC || !homeC) continue;
    const away = awayC.team?.displayName;
    const home = homeC.team?.displayName;
    const commenceTimeIso = comp.date || ev.date || null;
    const statusName = comp.status?.type?.name || "";
    const isFinal = statusName.includes("FINAL");
    const isInProgress = statusName === "STATUS_IN_PROGRESS" || statusName === "STATUS_HALFTIME";
    if (!away || !home) continue;
    out.push({ away, home, commenceTimeIso, isFinal, isInProgress });
  }
  return out;
}

function normTeam(name) {
  return String(name || "").trim().replace(/\s+/g, " ");
}

function todayISOChicago() {
  // date-only in America/Chicago, formatted YYYY-MM-DD
  const fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Chicago",
    year: "numeric",
    month: "2-digit",
    day: "2-digit"
  });
  return fmt.format(new Date()); // "YYYY-MM-DD"
}

// Convert The Odds API spread into sportsbook convention:
// -X = home favored by X, +X = away favored by X
function toModelLine(homeTeam, awayTeam, spreadPoints, teamForSpread) {
  if (!Number.isFinite(spreadPoints)) return null;
  const isHome = teamForSpread === homeTeam;
  const isAway = teamForSpread === awayTeam;
  if (!isHome && !isAway) return null;
  // The Odds API already uses sportsbook convention for the named team.
  // If the spread is for the home team, return it directly.
  // If the spread is for the away team, negate to express as home line.
  if (isHome) return spreadPoints;
  return -spreadPoints;
}

function pickBestBookmaker(bookmakers) {
  if (!Array.isArray(bookmakers) || bookmakers.length === 0) return null;

  // Prefer common US books if present (otherwise first)
  const preferred = ["DraftKings", "FanDuel", "BetMGM", "Caesars", "PointsBet", "BetRivers"];
  for (const p of preferred) {
    const b = bookmakers.find((x) => x?.title === p);
    if (b) return b;
  }
  return bookmakers[0];
}

function findMarket(bookmaker, key) {
  if (!bookmaker?.markets) return null;
  return bookmaker.markets.find((m) => m?.key === key) || null;
}

export async function fetchTodaysOdds() {
  const apiKey = process.env.ODDS_API_KEY || "6c5699682d30fc8664737160274f8d12";

  // Get events (upcoming) + odds in one call
  // markets: spreads + totals
  const url =
    `${BASE}/sports/basketball_nba/odds?` +
    `apiKey=${encodeURIComponent(apiKey)}` +
    `&regions=us` +
    `&markets=spreads,totals` +
    `&oddsFormat=american`;

  const res = await fetch(url);
  if (!res.ok) {
    const txt = await res.text().catch(() => "");
    throw new Error(`TheOddsAPI failed: ${res.status} ${res.statusText} ${txt}`);
  }

  const data = await res.json();
  const today = todayISOChicago();

  const games = [];

  const now = new Date();

  for (const ev of data) {
    const home = normTeam(ev?.home_team);
    const away = normTeam(ev?.away_team);
    if (!home || !away) continue;

    // Filter to today (Chicago date)
    const commence = ev?.commence_time ? new Date(ev.commence_time) : null;
    if (commence) {
      const d = new Intl.DateTimeFormat("en-CA", {
        timeZone: "America/Chicago",
        year: "numeric",
        month: "2-digit",
        day: "2-digit"
      }).format(commence);
      if (d !== today) continue;

      // Game already started — skip fetch, will backfill from cache
      if (commence <= now) continue;
    }

    const book = pickBestBookmaker(ev?.bookmakers);

    let line = null;
    let total = null;

    const spreads = book ? findMarket(book, "spreads") : null;
    const totals = book ? findMarket(book, "totals") : null;

    if (spreads?.outcomes?.length) {
      const out = spreads.outcomes.find((o) => Number.isFinite(Number(o?.point)));
      if (out) {
        const teamForSpread = normTeam(out.name);
        const pts = Number(out.point);
        line = toModelLine(home, away, pts, teamForSpread);
      }
    }

    if (totals?.outcomes?.length) {
      const out = totals.outcomes.find((o) => Number.isFinite(Number(o?.point)));
      if (out) total = Number(out.point);
    }

    games.push({ away, home, line, total, _book: book?.title ?? null });
  }

  // Load existing cache
  const dateKey = today.replace(/-/g, "");
  const cachePath = path.join(ODDS_CACHE_DIR, dateKey + ".json");
  let existing = {};
  try {
    if (fs.existsSync(cachePath)) {
      existing = JSON.parse(fs.readFileSync(cachePath, "utf8"));
    }
  } catch {}

  // Write fresh pre-game odds to cache
  if (games.length > 0) {
    try {
      if (!fs.existsSync(ODDS_CACHE_DIR)) fs.mkdirSync(ODDS_CACHE_DIR, { recursive: true });
      for (const g of games) {
        const key = `${g.away}@${g.home}`;
        existing[key] = { line: g.line, total: g.total, _book: g._book, _note: "live fetch" };
      }
      fs.writeFileSync(cachePath, JSON.stringify(existing, null, 2));
      console.log(`  [odds] Cached ${games.length} games to ${dateKey}.json`);
    } catch {}
  }

  // Backfill started/finished games from cache
  const freshKeys = new Set(games.map(g => `${g.away}@${g.home}`));
  for (const [key, val] of Object.entries(existing)) {
    if (!freshKeys.has(key) && val.line != null) {
      const [a, h] = key.split("@");
      if (a && h) {
        games.push({ away: a, home: h, line: val.line, total: val.total, _book: val._book });
        console.log(`  [odds] Backfilled started/finished game from cache: ${key}`);
      }
    }
  }

  return games;
}