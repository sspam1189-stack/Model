// scripts/sources/season_type.mjs
// Auto-detect whether we're in Regular Season or NCAA Tournament.
//
// NCAA 2025-26 key dates (update each season):
//   Regular season / conference tournaments end: ~2026-03-15
//   NCAA Tournament begins (First Four): 2026-03-17
//   NCAA Tournament proper (First Round): 2026-03-19

const TOURNEY_START = "20260317";  // First Four

export function getSeasonType(dateStr) {
  const d = (dateStr || todayYYYYMMDD()).replace(/-/g, "");
  return parseInt(d) >= parseInt(TOURNEY_START) ? "Postseason" : "Regular Season";
}

export function isTournament(dateStr) {
  return getSeasonType(dateStr) === "Postseason";
}

function todayYYYYMMDD() {
  const now = new Date();
  const fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Chicago",
    year: "numeric", month: "2-digit", day: "2-digit"
  });
  return fmt.format(now).replace(/-/g, "");
}
