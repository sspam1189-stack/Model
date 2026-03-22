// scripts/sources/espn_scoreboard.mjs
// ESPN scoreboard for NCAA men's basketball

export async function fetchScoreboard(dateYYYYMMDD) {
  const base = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard";
  const params = dateYYYYMMDD ? `?dates=${dateYYYYMMDD}&groups=50&limit=365` : `?groups=50&limit=365`;
  const url = `${base}${params}`;
  const res = await fetch(url, { headers: { "user-agent": "ncaa-picks-bot/1.0" } });
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

function _normForMatch(s) {
  return String(s || "").toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .replace(/\bstate\b/g, "st")
    .replace(/\s+/g, " ").trim();
}

export async function fetchTournamentTeams(dateYYYYMMDD, statsKeys = null) {
  const base = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard";
  const teams = {};

  // Build stats lookup for resolving ESPN names to model names
  const statsNorm = {};
  if (statsKeys) {
    for (const k of statsKeys) {
      statsNorm[_normForMatch(k)] = k;
    }
  }

  function bestName(teamObj) {
    const location = (teamObj.location || "").trim();
    const display = (teamObj.displayName || "").trim();

    if (Object.keys(statsNorm).length) {
      for (const candidate of [location, display]) {
        const nk = _normForMatch(candidate);
        if (statsNorm[nk]) return statsNorm[nk];
        for (const [sk, sv] of Object.entries(statsNorm)) {
          if (sk && nk && (nk.startsWith(sk) || sk.startsWith(nk))) {
            if (Math.abs(nk.length - sk.length) <= 4) return sv;
          }
        }
      }
    }
    return location || display;
  }

  for (const groups of [100, 98]) {
    try {
      const url = `${base}?dates=${dateYYYYMMDD}&groups=${groups}&seasontype=3&limit=200`;
      const res = await fetch(url, { headers: { "user-agent": "ncaa-picks-bot/1.0" } });
      if (!res.ok) continue;
      const data = await res.json();
      for (const ev of (data.events || [])) {
        for (const comp of (ev.competitions || [])) {
          for (const c of (comp.competitors || [])) {
            const teamObj = c.team || {};
            const display = teamObj.displayName;
            const location = teamObj.location;
            if (!display) continue;
            const best = bestName(teamObj);
            for (const variant of [display, location, best]) {
              if (variant) teams[_normForMatch(variant)] = best;
            }
          }
        }
      }
    } catch (e) { continue; }
  }
  return teams;
}
