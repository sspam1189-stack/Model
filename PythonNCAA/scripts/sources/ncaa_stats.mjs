// scripts/sources/ncaa_stats.mjs
// ────────────────────────────────────────────────────────────────────────────
// Fetches NCAA team stats from Barttorvik (T-Rank) via curl.
//
// Barttorvik has bot protection requiring a JS verification cookie.
// We use curl with a cookie jar to handle this.
//
// Array indices: [0]=name [1]=adjO [2]=adjD [6]=GP [7]=oEFG [9]=oTO [11]=oOR [15]=adjT
// ────────────────────────────────────────────────────────────────────────────

import { execSync } from "child_process";
import fs from "fs";
import path from "path";
import os from "os";

function currentSeasonYear() {
  const now = new Date();
  const year = now.getFullYear();
  const month = now.getMonth() + 1;
  return month >= 10 ? year + 1 : year;
}

// Cookie file with forward slashes (works on both Windows cmd and bash)
const COOKIE_FILE = path.join(os.tmpdir(), "barttorvik_cookies.txt").split(path.sep).join("/");
const NULL_DEV = process.platform === "win32" ? "NUL" : "/dev/null";
// Force Windows cmd.exe shell — Git Bash's sh.exe mangles cookie file paths
const SHELL_OPTS = process.platform === "win32" ? { shell: process.env.COMSPEC } : {};

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}


const USER_AGENTS = [
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:138.0) Gecko/20100101 Firefox/138.0",
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.4 Safari/605.1.15",
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0",
];

function randomUA() {
  return USER_AGENTS[Math.floor(Math.random() * USER_AGENTS.length)];
}

// Fetch with retry: refresh cookie if needed, retry up to 5 times with longer backoff
async function fetchBarttovikJSON(year) {
  let hasCookie = false;
  try {
    const stat = fs.statSync(COOKIE_FILE);
    hasCookie = (Date.now() - stat.mtimeMs) < 20 * 3600 * 1000;
  } catch {}

  // Always start fresh — delete stale cookies
  try { fs.unlinkSync(COOKIE_FILE); } catch {}

  const MAX_ATTEMPTS = 5;
  const url = `https://barttorvik.com/trank.php?year=${year}&json=1`;

  for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
    const ua = randomUA();

    // Step 1: POST js_test_submitted=1 to trank.php to get js_verified cookie
    try {
      execSync(
        `curl -s -c "${COOKIE_FILE}" -d "js_test_submitted=1" "https://barttorvik.com/trank.php" -H "User-Agent: ${ua}" -H "Referer: https://barttorvik.com/" -o ${NULL_DEV}`,
        { timeout: 15000, ...SHELL_OPTS }
      );
    } catch {}
    await sleep(500);

    // Step 2: GET the JSON with the cookie
    const result = execSync(
      `curl -s -b "${COOKIE_FILE}" "${url}" -H "User-Agent: ${ua}" -H "Referer: https://barttorvik.com/trank.php"`,
      { timeout: 30000, maxBuffer: 10 * 1024 * 1024, ...SHELL_OPTS }
    );
    const text = result.toString("utf8").trim();
    if (text.startsWith("[")) return text;

    const delay = (attempt + 1) * 8;
    console.log(`  [ncaa_stats] Attempt ${attempt + 1}/${MAX_ATTEMPTS}: got HTML, retrying in ${delay}s...`);
    try { fs.unlinkSync(COOKIE_FILE); } catch {}
    await sleep(delay * 1000);
  }

  throw new Error("Barttorvik: failed after 5 attempts — possible rate limit");
}

// Parse Barttorvik JSON array into our stats format
function parseBarttovikData(data) {
  const stats = {};
  let skipped = 0;

  for (const team of data) {
    const name = team[0];
    const gp   = Number(team[6] || 0);
    if (!name || gp < 5) { skipped++; continue; }

    const adjO = Number(team[1] || 0);
    const adjD = Number(team[2] || 0);
    const oEFG = Number(team[7] || 0) / 100;
    const oTO  = Number(team[9] || 0) / 100;
    const oOR  = Number(team[11] || 0) / 100;
    const adjT = Number(team[15] || 0);

    if (!adjO || !adjD || !adjT) { skipped++; continue; }

    stats[name] = {
      OFF: adjO, DEF: adjD,
      TS: oEFG || 0.50, TO: oTO || 0.18, ORR: oOR || 0.30,
      PACE: adjT, GP: gp,
    };
  }

  return { stats, skipped };
}

// Local cache: store fetched data to avoid repeat Barttorvik requests.
const CACHE_DIR = path.join(process.cwd(), "data");
const CACHE_FILE = path.join(CACHE_DIR, "barttorvik_cache.json");
const CACHE_TTL_MS = 20 * 3600 * 1000; // 20 hours — stats update once/day

function loadCache(year) {
  try {
    const cached = JSON.parse(fs.readFileSync(CACHE_FILE, "utf8"));
    if (cached.year === year && Object.keys(cached.stats).length >= 100) return cached;
  } catch {}
  return null;
}

export async function fetchNCAAStats(seasonYear = null) {
  const year = seasonYear || currentSeasonYear();
  console.log(`  [ncaa_stats] Fetching Barttorvik stats for ${year}...`);

  // Check local cache first (fresh = use directly)
  try {
    const stat = fs.statSync(CACHE_FILE);
    if (Date.now() - stat.mtimeMs < CACHE_TTL_MS) {
      const cached = loadCache(year);
      if (cached) {
        console.log(`  [ncaa_stats] Using cached data (${Object.keys(cached.stats).length} teams, ${Math.round((Date.now() - stat.mtimeMs) / 60000)}min old)`);
        return cached.stats;
      }
    }
  } catch {}

  // Try fetching fresh data
  try {
    const text = await fetchBarttovikJSON(year);
    const data = JSON.parse(text);

    if (!Array.isArray(data) || data.length < 100) {
      throw new Error(`Barttorvik: unexpected data (${Array.isArray(data) ? data.length : typeof data} entries)`);
    }

    const { stats, skipped } = parseBarttovikData(data);
    const count = Object.keys(stats).length;
    console.log(`  [ncaa_stats] Got ${count} teams (${skipped} skipped)`);

    if (count < 100) throw new Error(`Barttorvik incomplete: only ${count} teams`);

    // Cache to disk
    try {
      fs.mkdirSync(CACHE_DIR, { recursive: true });
      fs.writeFileSync(CACHE_FILE, JSON.stringify({ year, stats, fetchedAt: new Date().toISOString() }));
      console.log(`  [ncaa_stats] Cached to ${CACHE_FILE}`);
    } catch {}

    return stats;
  } catch (err) {
    // Fallback: use stale cache if available (better than crashing)
    const stale = loadCache(year);
    if (stale) {
      const ageHrs = Math.round((Date.now() - new Date(stale.fetchedAt).getTime()) / 3600000);
      console.warn(`  [ncaa_stats] ⚠ Fetch failed (${err.message}). Using stale cache (${ageHrs}h old, ${Object.keys(stale.stats).length} teams)`);
      return stale.stats;
    }
    throw err; // no cache at all — nothing we can do
  }
}

export async function fetchNCAAStatsEnhanced(dateTo = null) {
  const season = await fetchNCAAStats();
  console.log(`  [ncaa_stats] Enhanced: ${Object.keys(season).length} teams (season only, no splits)`);
  return { season, last10: null, home: null, away: null };
}
