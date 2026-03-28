/**
 * Uses ESPN's public JSON scoreboard endpoint.
 * Example base: https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates=YYYYMMDD
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CACHE_DIR = path.resolve(__dirname, "..", "..", "..", "data", "espn_cache", "nba");

export async function fetchScoreboard(dateYYYYMMDD) {
  // Disk cache — shared across all NBA engines (skip for today — statuses change)
  const _today = new Date().toISOString().slice(0, 10).replace(/-/g, "");
  if (dateYYYYMMDD && dateYYYYMMDD !== _today) {
    if (!fs.existsSync(CACHE_DIR)) fs.mkdirSync(CACHE_DIR, { recursive: true });
    const diskPath = path.join(CACHE_DIR, dateYYYYMMDD + ".json");
    if (fs.existsSync(diskPath)) {
      return JSON.parse(fs.readFileSync(diskPath, "utf8"));
    }
  }

  const base = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard";
  const url = dateYYYYMMDD ? `${base}?dates=${dateYYYYMMDD}` : base;
  const res = await fetch(url, { headers: { "user-agent": "nba-picks-bot/1.0" } });
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`);
  const data = await res.json();

  if (dateYYYYMMDD) {
    try { fs.writeFileSync(path.join(CACHE_DIR, dateYYYYMMDD + ".json"), JSON.stringify(data)); }
    catch (e) { /* ignore */ }
  }

  return data;
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
