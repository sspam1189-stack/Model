// scripts/sources/season_type.mjs
// Auto-detect whether we're in Regular Season or Playoffs based on date.
//
// NBA 2025-26 key dates (update these each season):
//   Regular season ends:  2026-04-12
//   Play-in tournament:   2026-04-14 to 2026-04-17
//   Playoffs begin:       2026-04-18
//
// During play-in we still use "Regular Season" stats since there's no
// meaningful playoff sample yet. Once first round starts, switch to "Playoffs".

const PLAYOFF_START = "20260418";  // first round game 1

// NBA.com API value: "Regular Season" or "Playoffs"
// ESPN API value: 2 (regular season) or 3 (playoffs)

export function getSeasonType(dateStr) {
  // dateStr can be "YYYYMMDD" or "YYYY-MM-DD"
  const d = (dateStr || todayYYYYMMDD()).replace(/-/g, "");
  return parseInt(d) >= parseInt(PLAYOFF_START) ? "Playoffs" : "Regular Season";
}

export function getESPNSeasonType(dateStr) {
  return getSeasonType(dateStr) === "Playoffs" ? 3 : 2;
}

export function isPlayoffs(dateStr) {
  return getSeasonType(dateStr) === "Playoffs";
}

function todayYYYYMMDD() {
  const now = new Date();
  const fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Chicago",
    year: "numeric", month: "2-digit", day: "2-digit"
  });
  return fmt.format(now).replace(/-/g, "");
}
