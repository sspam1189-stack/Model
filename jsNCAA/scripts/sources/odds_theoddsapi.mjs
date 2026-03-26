// scripts/sources/odds_theoddsapi.mjs
// Fetch NCAA basketball spreads + totals from The Odds API.
// For games already started, fetches historical odds from 1.5 hours before tip.
// Requires env var: ODDS_API_KEY

import { fetchScoreboard } from "./espn_scoreboard.mjs";

const BASE = "https://api.the-odds-api.com/v4";
const HISTORICAL_OFFSET_MS = 90 * 60 * 1000; // 1.5 hours before tip-off

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
  const fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Chicago",
    year: "numeric", month: "2-digit", day: "2-digit"
  });
  return fmt.format(new Date());
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

// ── Extract odds from a single event object ─────────────────────────────────
function extractOddsFromEvent(ev) {
  const home = normTeam(ev?.home_team);
  const away = normTeam(ev?.away_team);
  if (!home || !away) return null;

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

  return { away, home, line, total, _book: book?.title ?? null };
}

// ── Fetch historical odds for a single started game ─────────────────────────
// Hits the historical odds endpoint at 1.5 hours before tip-off.
async function fetchHistoricalOddsForGame(apiKey, eventId, commence) {
  const snapshotDate = new Date(commence.getTime() - HISTORICAL_OFFSET_MS);
  const dateParam = snapshotDate.toISOString().replace(/\.\d{3}Z$/, "Z");

  const url =
    `${BASE}/historical/sports/basketball_ncaab/events/${eventId}/odds?` +
    `apiKey=${encodeURIComponent(apiKey)}` +
    `&regions=us` +
    `&markets=spreads,totals` +
    `&oddsFormat=american` +
    `&date=${encodeURIComponent(dateParam)}`;

  try {
    const res = await fetch(url);
    if (!res.ok) {
      const txt = await res.text().catch(() => "");
      console.log(`  [odds] Historical fetch failed for ${eventId}: ${res.status} ${txt.slice(0, 100)}`);
      return null;
    }
    const json = await res.json();
    // Historical response wraps the event data in a "data" field
    return json?.data || json;
  } catch (err) {
    console.log(`  [odds] Historical fetch error for ${eventId}: ${err.message}`);
    return null;
  }
}

export async function fetchTodaysOdds() {
  const apiKey = process.env.ODDS_API_KEY;
  if (!apiKey) {
    throw new Error("Missing ODDS_API_KEY env var.");
  }

  const url =
    `${BASE}/sports/basketball_ncaab/odds?` +
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
  const startedGames = []; // games that already tipped off

  for (const ev of data) {
    const home = normTeam(ev?.home_team);
    const away = normTeam(ev?.away_team);
    if (!home || !away) continue;

    const commence = ev?.commence_time ? new Date(ev.commence_time) : null;
    if (commence) {
      const d = new Intl.DateTimeFormat("en-CA", {
        timeZone: "America/Chicago",
        year: "numeric", month: "2-digit", day: "2-digit"
      }).format(commence);
      if (d !== today) continue;

      // Game already started — skip (run_daily preserves from previous run)
      if (commence <= now) {
        console.log(`  [odds] Game already started: ${away} @ ${home} — skipping`);
        continue;
      }
    }

    const game = extractOddsFromEvent(ev);
    if (game) games.push(game);
  }

  console.log(`  [odds] Got ${games.length} NCAAB games with odds for today`);
  return games;
}
