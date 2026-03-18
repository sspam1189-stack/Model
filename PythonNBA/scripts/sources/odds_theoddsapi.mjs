// scripts/sources/odds_theoddsapi.mjs
// Fetch NBA spreads + totals from The Odds API and return in your bot format.
// Requires env var: ODDS_API_KEY

import { fetchClosingOddsForGame } from "./odds_theoddsapi_historical.mjs";
import { fetchScoreboard } from "./espn_scoreboard.mjs";

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

// Convert The Odds API spread into your convention:
// +X means HOME favored by X
// -X means AWAY favored by X
function toModelLine(homeTeam, awayTeam, spreadPoints, teamForSpread) {
  if (!Number.isFinite(spreadPoints)) return null;
  const abs = Math.abs(spreadPoints);

  const isHome = teamForSpread === homeTeam;
  const isAway = teamForSpread === awayTeam;

  if (!isHome && !isAway) return null;

  // In The Odds API, spread points are usually negative for favorite (e.g., -5.5)
  // If HOME team is the favorite, model line should be +5.5
  // If AWAY team is the favorite, model line should be -5.5
  if (isHome) return spreadPoints < 0 ? abs : -abs;
  return spreadPoints < 0 ? -abs : abs;
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
  const apiKey = process.env.ODDS_API_KEY;
  if (!apiKey) {
    throw new Error("Missing ODDS_API_KEY env var (The Odds API key).");
  }

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

      // Game already started — fetch pre-game odds from historical API (-90min snapshot)
      if (commence <= now) {
        console.log(`  [odds] Game already started: ${away} @ ${home} — fetching historical odds...`);
        try {
          const hist = await fetchClosingOddsForGame({
            home,
            away,
            commenceTimeIso: ev.commence_time
          });
          if (typeof hist.line === "number" && typeof hist.total === "number") {
            games.push({ away, home, line: hist.line, total: hist.total, _book: hist._book ?? null });
          } else {
            console.log(`  [odds] No historical odds found for ${away} @ ${home} — skipping`);
          }
        } catch (e) {
          console.warn(`  [odds] Historical fetch failed for ${away} @ ${home}:`, e.message);
        }
        continue;
      }
    }

    const book = pickBestBookmaker(ev?.bookmakers);

    let line = null;
    let total = null;

    const spreads = book ? findMarket(book, "spreads") : null;
    const totals = book ? findMarket(book, "totals") : null;

    // spreads: outcomes like [{name: team, point: -5.5}, ...]
    if (spreads?.outcomes?.length) {
      const out = spreads.outcomes.find((o) => Number.isFinite(Number(o?.point)));
      if (out) {
        const teamForSpread = normTeam(out.name);
        const pts = Number(out.point);
        line = toModelLine(home, away, pts, teamForSpread);
      }
    }

    // totals: outcomes like [{name:"Over", point: 226.5}, {name:"Under", point:226.5}]
    if (totals?.outcomes?.length) {
      const out = totals.outcomes.find((o) => Number.isFinite(Number(o?.point)));
      if (out) total = Number(out.point);
    }

    games.push({
      away,
      home,
      line,
      total,
      _book: book?.title ?? null
    });
  }

  // Cross-reference ESPN schedule — pick up finished games missing from Odds API
  const espnSb = await fetchScoreboard(today.replace(/-/g, "")).catch(() => null);
  const espnGames = espnSb ? extractAllESPNGames(espnSb) : [];

  for (const eg of espnGames) {
    if (!eg.isFinal) continue; // only care about finished games here

    // Check if already in our games list
    const nAway = normTeam(eg.away);
    const nHome = normTeam(eg.home);
    const already = games.some(g => normTeam(g.away) === nAway && normTeam(g.home) === nHome);
    if (already) continue;

    // Finished game missing from Odds API — fetch pre-game historical line
    console.log(`  [odds] Finished game not in API: ${eg.away} @ ${eg.home} — fetching historical odds...`);
    try {
      const hist = await fetchClosingOddsForGame({
        home: eg.home,
        away: eg.away,
        commenceTimeIso: eg.commenceTimeIso
      });
      if (typeof hist.line === "number" && typeof hist.total === "number") {
        games.push({ away: eg.away, home: eg.home, line: hist.line, total: hist.total, _book: hist._book ?? null });
      } else {
        console.log(`  [odds] No historical odds for ${eg.away} @ ${eg.home}`);
        games.push({ away: eg.away, home: eg.home, line: null, total: null, _book: null });
      }
    } catch (e) {
      console.warn(`  [odds] Historical fetch failed for ${eg.away} @ ${eg.home}:`, e.message);
      games.push({ away: eg.away, home: eg.home, line: null, total: null, _book: null });
    }
  }

  return games;
}