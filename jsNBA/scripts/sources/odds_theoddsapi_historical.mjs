// scripts/sources/odds_theoddsapi_historical.mjs
// Historical odds (closing-ish snapshot) from The Odds API v4.
// Requires env var: ODDS_API_KEY
//
// Notes:
// - Uses /v4/historical/sports/basketball_nba/odds with `date=` as RFC3339 UTC (no milliseconds)
// - Requests snapshot 90 minutes BEFORE tipoff to ensure pre-game lines are present
// - Retries on 429 with exponential backoff
// - Returns { line, total, _book, _note } where line uses your convention:
//     +X = home favored by X
//     -X = away favored by X

const BASE = "https://api.the-odds-api.com/v4";

function normTeam(name) {
  return String(name || "").trim().replace(/\s+/g, " ");
}

function normKey(s) {
  return String(s || "")
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function toHistoricalISO(isoLike, offsetMinutes = 0) {
  const d = new Date(isoLike);
  if (Number.isNaN(d.getTime())) return null;
  // Shift by offsetMinutes (negative = before tipoff)
  d.setMinutes(d.getMinutes() + offsetMinutes);
  return d.toISOString().replace(/\.\d{3}Z$/, "Z");
}

// Convert The Odds API spread into sportsbook convention:
// -X = home favored by X, +X = away favored by X
function toModelLine(homeTeam, awayTeam, spreadPoints, teamForSpread) {
  if (!Number.isFinite(spreadPoints)) return null;
  const isHome = teamForSpread === homeTeam;
  const isAway = teamForSpread === awayTeam;
  if (!isHome && !isAway) return null;
  if (isHome) return spreadPoints;
  return -spreadPoints;
}

function pickBookmaker(bookmakers) {
  if (!Array.isArray(bookmakers) || bookmakers.length === 0) return null;

  const preferred = ["DraftKings", "FanDuel", "BetMGM", "Caesars", "PointsBet", "BetRivers"];
  for (const title of preferred) {
    const found = bookmakers.find((b) => b?.title === title);
    if (found) return found;
  }
  return bookmakers[0];
}

function findMarket(bookmaker, key) {
  if (!bookmaker?.markets) return null;
  return bookmaker.markets.find((m) => m?.key === key) || null;
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function fetchWithRetry(url, tries = 5) {
  let wait = 700;
  for (let i = 0; i < tries; i++) {
    const res = await fetch(url);
    if (res.status !== 429) return res;
    await sleep(wait);
    wait = Math.min(wait * 2, 8000);
  }
  return fetch(url);
}

// Maps Hollinger short names → possible odds API full names
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

function extractOdds(data, home, away) {
  const homeAliases = expandAliases(home);
  const awayAliases = expandAliases(away);

  const ev =
    // exact match against any alias
    data.find((e) => {
      const h = normKey(e?.home_team);
      const a = normKey(e?.away_team);
      return homeAliases.includes(h) && awayAliases.includes(a);
    }) ||
    // partial includes fallback
    data.find((e) => {
      const h = normKey(e?.home_team);
      const a = normKey(e?.away_team);
      return homeAliases.some(ha => h.includes(ha) || ha.includes(h)) &&
             awayAliases.some(aa => a.includes(aa) || aa.includes(a));
    });

  if (!ev) return null;

  const homeTeam = normTeam(ev.home_team);
  const awayTeam = normTeam(ev.away_team);
  const book = pickBookmaker(ev?.bookmakers);

  let line = null;
  let total = null;

  const spreads = book ? findMarket(book, "spreads") : null;
  const totals  = book ? findMarket(book, "totals")  : null;

  if (spreads?.outcomes?.length) {
    const out = spreads.outcomes.find((o) => Number.isFinite(Number(o?.point)));
    if (out) line = toModelLine(homeTeam, awayTeam, Number(out.point), normTeam(out.name));
  }

  if (totals?.outcomes?.length) {
    const out = totals.outcomes.find((o) => Number.isFinite(Number(o?.point)));
    if (out) total = Number(out.point);
  }

  return { line, total, _book: book?.title ?? null };
}

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
    throw new Error(`Historical odds fetch failed: ${res.status} ${res.statusText} ${txt}`);
  }

  const json = await res.json();
  return Array.isArray(json?.data) ? json.data : [];
}

// Fetch historical odds snapshot near tipoff and extract spread/total.
// Pass commenceTimeIso from ESPN (UTC ISO string).
export async function fetchClosingOddsForGame({ home, away, commenceTimeIso }) {
  const apiKey = process.env.ODDS_API_KEY;
  if (!apiKey) throw new Error("Missing ODDS_API_KEY env var.");

  // Try snapshots at -90min, -30min, and -10min before tipoff.
  // Games are removed from the feed at/after tipoff, so we need a pre-game timestamp.
  const offsets = [-90, -30, -10];

  for (const offset of offsets) {
    const ts = toHistoricalISO(commenceTimeIso, offset);
    if (!ts) return { line: null, total: null, _book: null, _note: "Bad commenceTimeIso" };

    const data = await fetchSnapshot(apiKey, ts);
    const result = extractOdds(data, home, away);

    if (result && (typeof result.line === "number" || typeof result.total === "number")) {
      return { ...result, _note: `snapshot at ${offset}min before tipoff` };
    }
  }

  return { line: null, total: null, _book: null, _note: "No odds found in pre-tipoff snapshots" };
}
