/**
 * Uses ESPN's public JSON scoreboard endpoint.
 * Example base: https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates=YYYYMMDD
 */
export async function fetchScoreboard(dateYYYYMMDD) {
  const base = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard";
  const url = dateYYYYMMDD ? `${base}?dates=${dateYYYYMMDD}` : base;
  const res = await fetch(url, { headers: { "user-agent": "nba-picks-bot/1.0" } });
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`);
  return await res.json();
}

export function extractFinalScores(scoreboardJson) {
  const out = [];
  for (const ev of (scoreboardJson.events || [])) {
    const comp = ev.competitions?.[0];
    if (!comp) continue;

    const status = comp.status?.type?.name;
    const isFinal = status === "STATUS_FINAL" || status === "STATUS_FINAL_OVERTIME";
    if (!isFinal) continue;

    const competitors = comp.competitors || [];
    const away = competitors.find(c => c.homeAway === "away");
    const home = competitors.find(c => c.homeAway === "home");
    if (!away || !home) continue;

    const awayName = away.team?.displayName;
    const homeName = home.team?.displayName;
    const awayScore = parseFloat(away.score);
    const homeScore = parseFloat(home.score);
    if (!awayName || !homeName || !Number.isFinite(awayScore) || !Number.isFinite(homeScore)) continue;

    out.push({ away: awayName, home: homeName, awayScore, homeScore, date: scoreboardJson.day?.date || null });
  }
  return out;
}
