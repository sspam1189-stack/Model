// scripts/sources/rest_detect.mjs
// Detects back-to-back situations for tonight's games.

function yesterdayESPN() {
  const now = new Date();
  const fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Chicago",
    year: "numeric", month: "2-digit", day: "2-digit",
  });
  const parts = fmt.formatToParts(now);
  const y = Number(parts.find(p => p.type === "year").value);
  const m = Number(parts.find(p => p.type === "month").value);
  const d = Number(parts.find(p => p.type === "day").value);
  const base = new Date(Date.UTC(y, m - 1, d));
  base.setUTCDate(base.getUTCDate() - 1);
  const yy = base.getUTCFullYear();
  const mm = String(base.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(base.getUTCDate()).padStart(2, "0");
  return `${yy}${mm}${dd}`;
}

export async function detectB2B() {
  const date = yesterdayESPN();
  const url = `https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates=${date}`;

  const res = await fetch(url, {
    headers: { "User-Agent": "Mozilla/5.0", Accept: "application/json" },
  });
  if (!res.ok) return new Set();

  const sb = await res.json();
  const teams = new Set();

  for (const ev of sb?.events || []) {
    const comp = ev?.competitions?.[0];
    if (!comp?.status?.type?.completed) continue;
    for (const c of comp?.competitors || []) {
      const name = c?.team?.displayName;
      if (name) teams.add(name);
    }
  }

  return teams;
}

const B2B_OFF_PENALTY = -1.5;
const B2B_DEF_PENALTY = 0.8;

export function applyB2BAdjustment(teamStats, b2bTeams, todaysGames) {
  if (!b2bTeams || !b2bTeams.size) return { adjusted: teamStats, b2bNotes: {} };

  const adjusted = { ...teamStats };
  const b2bNotes = {};

  const teamsTonight = new Set();
  for (const g of todaysGames) {
    if (g.away) teamsTonight.add(g.away);
    if (g.home) teamsTonight.add(g.home);
  }

  for (const b2bName of b2bTeams) {
    const statKey = findStatKey(teamStats, b2bName);
    if (!statKey) continue;

    const isTonight = teamsTonight.has(statKey) ||
      [...teamsTonight].some(t => normKey(t) === normKey(statKey));
    if (!isTonight) continue;

    const orig = teamStats[statKey];
    adjusted[statKey] = {
      ...orig,
      OFF: Math.round((orig.OFF + B2B_OFF_PENALTY) * 100) / 100,
      DEF: Math.round((orig.DEF + B2B_DEF_PENALTY) * 100) / 100,
    };

    b2bNotes[statKey] = `B2B: OFF ${B2B_OFF_PENALTY}, DEF +${B2B_DEF_PENALTY}`;
  }

  return { adjusted, b2bNotes };
}

function normKey(s) {
  return String(s || "").toLowerCase().replace(/[^a-z0-9 ]/g, " ").replace(/ +/g, " ").trim();
}

function findStatKey(stats, teamName) {
  if (stats[teamName]) return teamName;
  const wanted = normKey(teamName);
  for (const k of Object.keys(stats)) {
    const nk = normKey(k);
    if (nk === wanted || nk.includes(wanted) || wanted.includes(nk)) return k;
  }
  const mascot = wanted.split(" ").pop();
  for (const k of Object.keys(stats)) {
    if (normKey(k).split(" ").pop() === mascot) return k;
  }
  return null;
}
