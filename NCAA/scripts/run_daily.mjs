// scripts/run_daily.mjs
// Daily NCAA picks pipeline:
//   1. Grade yesterday's picks against final scores
//   2. Fetch today's stats, odds, trends
//   3. Analyze each game via model engine
//   4. Build HTML + text email with records, trends, rolling windows
//   5. Send via email

import { createRequire } from "module";
const require = createRequire(import.meta.url);
require("dotenv").config();

import { fetchNCAAStatsEnhanced } from "./sources/ncaa_stats.mjs";
import { blendBase, blendForGame } from "./sources/blend_stats.mjs";
import { fetchTodaysOdds } from "./sources/odds_theoddsapi.mjs";
import { fetchScoreboard, extractFinalScores, fetchTournamentTeams } from "./sources/espn_scoreboard.mjs";
import { fetchATSTrends, fetchOUTrends } from "./sources/teamrankings_trends.mjs";
import { detectB2B, applyB2BAdjustment } from "./sources/rest_detect.mjs";
import { loadDefaults, getAvgs, analyzeGame } from "./model_engine.mjs";
import { loadStore, saveStore, upsertRun } from "./store.mjs";
import { tuneWeights, computeResidualVar } from "./self_tune.mjs";
import {
  loadKalmanState, saveKalmanState, initializeKalman,
  applyDailyDrift, batchUpdate, kalmanSummary, pruneProcessedGames,
} from "./kalman_state.mjs";
import { buildCalibrationTable, buildCalibrationHtml } from "./calibration.mjs";
import { sendEmail } from "./email.mjs";

// ─── Constants ───────────────────────────────────────────────────────────────

const TIMEZONE = "America/Chicago";
const CONF_ACTIONABLE = ["high", "elite"];
const UNIT_LOSS = -1.1; // standard -110 juice

// ─── Utility Helpers ─────────────────────────────────────────────────────────

function esc(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function fmtNum(x, digits = 1) {
  return Number.isFinite(x) ? Number(x).toFixed(digits) : "n/a";
}

function calcUnits(w, l) {
  return w + l * UNIT_LOSS;
}

function fmtUnits(u) {
  if (!Number.isFinite(u)) return "—";
  return (u >= 0 ? "+" : "") + u.toFixed(1) + "u";
}

function winPct(w, l) {
  const total = w + l;
  return total > 0 ? (w / total * 100).toFixed(1) : "n/a";
}

function isActionable(conf) {
  return CONF_ACTIONABLE.includes(String(conf).toLowerCase());
}

// ─── Team Name Resolution ────────────────────────────────────────────────────

function normKey(s) {
  return String(s || "")
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .replace(/\bstate\b/g, "st")
    .replace(/\s+/g, " ")
    .trim();
}

// Safe substring: only allow includes() if lengths are within 30%
function safeFuzzy(a, b) {
  if (!a || !b) return false;
  if (a === b) return true;
  const shorter = a.length < b.length ? a : b;
  const longer  = a.length < b.length ? b : a;
  if (shorter.length / longer.length < 0.85) return false;
  return longer.includes(shorter);
}

// Explicit aliases for commonly confused teams
const TEAM_ALIASES = {
  "ohio state buckeyes": "Ohio St.",
  "ohio state": "Ohio St.",
  "ohio st": "Ohio St.",
};

function resolveKey(obj, teamName) {
  if (!obj || !teamName) return null;
  if (obj[teamName]) return teamName;

  const wanted = normKey(teamName);

  // Check explicit aliases first
  if (TEAM_ALIASES[wanted] && obj[TEAM_ALIASES[wanted]]) return TEAM_ALIASES[wanted];

  // Exact normKey match
  for (const k of Object.keys(obj)) {
    const nk = normKey(k);
    if (nk === wanted) return k;
  }

  // Safe substring matching (prevents arkansas→kansas, etc.)
  for (const k of Object.keys(obj)) {
    const nk = normKey(k);
    if (safeFuzzy(nk, wanted)) return k;
  }

  // ESPN "School Mascot" prefix match (e.g. "Arkansas Razorbacks" → "Arkansas")
  // Word boundary prevents "Kansas" → "Arkansas" false positives
  // Prefer longest match to avoid "Ohio" beating "Ohio St."
  let prefixMatch = null;
  for (const k of Object.keys(obj)) {
    const nk = normKey(k);
    if (nk.length < 3) continue;
    if (wanted.startsWith(nk + " ") || wanted === nk) {
      if (!prefixMatch || nk.length > normKey(prefixMatch).length) prefixMatch = k;
    }
  }
  if (prefixMatch) return prefixMatch;

  // Barttorvik key starts with first word of wanted (e.g. "Penn" from "Pennsylvania")
  const firstWord = wanted.split(" ")[0];
  if (firstWord.length >= 3) {
    for (const k of Object.keys(obj)) {
      const nk = normKey(k);
      if (nk === firstWord || (nk.length >= 3 && firstWord.startsWith(nk))) return k;
    }
  }

  // Last-word (mascot) matching for NCAA's varied names
  const wantedWords = wanted.split(" ");
  const wantedLast = wantedWords[wantedWords.length - 1];
  if (wantedLast && wantedLast.length >= 4) {
    for (const k of Object.keys(obj)) {
      const kWords = normKey(k).split(" ");
      const kLast = kWords[kWords.length - 1];
      if (kLast === wantedLast) return k;
    }
  }

  return null;
}

// Fuzzy team matching for score grading
// Common NCAA abbreviation aliases (store name → ESPN displayName prefix)
const MATCH_ALIASES = {
  "liu": "long island university",
  "cal baptist": "california baptist",
  "umbc": "maryland baltimore county",
  "utsa": "ut san antonio",
  "utep": "ut el paso",
  "uab": "alabama birmingham",
  "unc": "north carolina",
  "lsu": "louisiana st",
  "smu": "southern methodist",
  "tcu": "texas christian",
  "ucf": "central florida",
  "vcu": "virginia commonwealth",
  "fiu": "florida international",
  "fau": "florida atlantic",
};

function matchTeam(a, b) {
  if (a === b) return true;
  const na = normKey(a), nb = normKey(b);
  if (na === nb) return true;
  if (safeFuzzy(na, nb)) return true;

  // Prefix match: "kentucky" matches "kentucky wildcats" (ESPN displayName includes mascot)
  const shorter = na.length <= nb.length ? na : nb;
  const longer  = na.length <= nb.length ? nb : na;
  if (shorter.length >= 3 && (longer.startsWith(shorter + " ") || longer === shorter)) return true;

  // Strip trailing " st" and retry prefix (handles "Sam Houston St." vs "Sam Houston Bearkats")
  const stripSt = (s) => s.endsWith(" st") ? s.slice(0, -3) : s;
  const sa = stripSt(na), sb2 = stripSt(nb);
  if (sa.length >= 3 && nb.startsWith(sa + " ")) return true;
  if (sb2.length >= 3 && na.startsWith(sb2 + " ")) return true;

  // Alias lookup
  const aliasA = MATCH_ALIASES[na];
  const aliasB = MATCH_ALIASES[nb];
  if (aliasA && (nb.startsWith(aliasA) || nb === aliasA)) return true;
  if (aliasB && (na.startsWith(aliasB) || na === aliasB)) return true;

  // Last-word mascot match
  const aWords = na.split(" ");
  const bWords = nb.split(" ");
  const aLast = aWords[aWords.length - 1];
  const bLast = bWords[bWords.length - 1];
  if (aLast && bLast && aLast.length >= 4 && aLast === bLast) return true;

  return false;
}

// ─── Date Helpers ────────────────────────────────────────────────────────────

function centralDateParts(date = new Date()) {
  const fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone: TIMEZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  const parts = fmt.formatToParts(date);
  const get = (type) => parts.find((p) => p.type === type).value;
  return { y: get("year"), m: get("month"), d: get("day") };
}

function todayCentralYYYYMMDD() {
  const { y, m, d } = centralDateParts();
  return `${y}${m}${d}`;
}

function yyyymmddFromCentralOffset(daysBack = 0) {
  const { y, m, d } = centralDateParts();
  const base = new Date(Date.UTC(Number(y), Number(m) - 1, Number(d)));
  base.setUTCDate(base.getUTCDate() - daysBack);
  const yy = base.getUTCFullYear();
  const mm = String(base.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(base.getUTCDate()).padStart(2, "0");
  return `${yy}${mm}${dd}`;
}

function toDisplayDate(yyyymmdd) {
  return `${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6, 8)}`;
}

// ─── Trend Formatting ────────────────────────────────────────────────────────

function joinTrends(team, atsObj, ouObj) {
  const parts = [];
  if (atsObj?.atsPct != null) parts.push(`ATS ${atsObj.atsPct}%`);
  if (atsObj?.atsPlusMinus != null) parts.push(`ATS +/- ${atsObj.atsPlusMinus}`);
  if (ouObj?.overPct != null) parts.push(`Over ${ouObj.overPct}%`);
  if (ouObj?.underPct != null) parts.push(`Under ${ouObj.underPct}%`);
  if (ouObj?.totalPlusMinus != null) parts.push(`O/U +/- ${ouObj.totalPlusMinus}`);
  return parts.join(" · ");
}

// ─── Grading ─────────────────────────────────────────────────────────────────

function parseSpreadPick(pick) {
  if (!pick || pick === "PASS") return null;
  const m = pick.match(/(.+?)\s+([+-])(\d+(?:\.\d+)?)/);
  if (!m) return null;
  return { team: m[1].trim(), sign: m[2], pts: parseFloat(m[3]) };
}

function gradeSpreadPick(g) {
  const parsed = parseSpreadPick(g.sPick);
  if (!parsed) return null;

  const { team, sign, pts } = parsed;
  const chosenIsHome = team === g.home;
  const margin = chosenIsHome
    ? g.homeScore - g.awayScore
    : g.awayScore - g.homeScore;
  const val = sign === "+" ? margin + pts : margin - pts;

  if (val === 0) return "PUSH";
  return val > 0 ? "WIN" : "LOSS";
}

function gradeTotalPick(g) {
  if (!g.oPick || g.oPick === "PASS") return null;
  const actual = g.homeScore + g.awayScore;
  if (actual === g.total) return "PUSH";
  if (g.oPick === "OVER") return actual > g.total ? "WIN" : "LOSS";
  return actual < g.total ? "WIN" : "LOSS";
}

function gradeResult(g, type) {
  if (type === "spread") return g.sResult || gradeSpreadPick(g);
  return g.oResult || gradeTotalPick(g);
}

// ─── Store: Grade a Past Date ────────────────────────────────────────────────

async function gradeDateInStore(store, yyyymmdd) {
  const run = (store.runs || []).find((r) => r.date === yyyymmdd);
  if (!run) return;

  // Skip if all pickable games already have scores
  const needsGrading = (run.games || []).some(
    (g) => g.status !== "MISSING_ODDS" && g.status !== "SKIPPED" && !Number.isFinite(g.homeScore)
  );
  if (!needsGrading) return;

  const sb = await fetchScoreboard(yyyymmdd).catch(() => null);
  if (!sb) return;

  const finals = extractFinalScores(sb);
  if (!finals.length) return;

  let graded = 0;
  for (const g of run.games || []) {
    if (g.status === "MISSING_ODDS" || g.status === "SKIPPED") continue;
    if (Number.isFinite(g.homeScore)) continue; // already scored

    const f = finals.find((x) => matchTeam(x.away, g.away) && matchTeam(x.home, g.home));
    if (!f) continue;

    g.awayScore = f.awayScore;
    g.homeScore = f.homeScore;
    if (g.sPick && g.sPick !== "PASS") g.sResult = gradeSpreadPick(g);
    if (g.oPick && g.oPick !== "PASS") g.oResult = gradeTotalPick(g);
    graded++;
  }

  if (graded > 0) saveStore(store);
}

// ─── Team Record ─────────────────────────────────────────────────────────────

function computeTeamRecords(store) {
  const teams = {};
  for (const r of store.runs || []) {
    for (const g of r.games || []) {
      const pick = g.sPick;
      const conf = g.sConf;
      if (!pick || pick === "PASS" || !isActionable(conf)) continue;
      if (!Number.isFinite(g.homeScore) || !Number.isFinite(g.awayScore)) continue;

      const result = g.sResult || gradeSpreadPick(g);
      if (!result) continue;

      const m = pick.match(/(.+?)\s+([+-])(\d+(?:\.\d+)?)/);
      if (!m) continue;
      const team = m[1].trim();
      const isFav = m[2] === "-";

      if (!teams[team]) teams[team] = { w: 0, l: 0, p: 0, fav: 0, dog: 0, picks: 0 };
      teams[team].picks++;
      if (isFav) teams[team].fav++;
      else teams[team].dog++;
      if (result === "WIN") {
        teams[team].w++;
      } else if (result === "LOSS") {
        teams[team].l++;
      } else {
        teams[team].p++;
      }
    }
  }
  return teams;
}

function buildTeamRecordTable(teamRecords) {
  const sorted = Object.entries(teamRecords)
    .filter(([, t]) => t.w + t.l >= 3)
    .sort((a, b) => {
      const unitsA = a[1].w - a[1].l * 1.1;
      const unitsB = b[1].w - b[1].l * 1.1;
      return unitsB - unitsA;
    });

  if (!sorted.length) return "";

  const rows = sorted.map(([name, t]) => {
    const total = t.w + t.l;
    const pct = total > 0 ? Math.round(100 * t.w / total) : 0;
    const flatUnits = Math.round((t.w - t.l * 1.1) * 10) / 10;
    const flatStr = (flatUnits >= 0 ? "+" : "") + flatUnits.toFixed(1) + "u";
    const flatColor = flatUnits >= 0 ? "#10b981" : "#ef4444";
    return `<tr>` +
      `<td>${esc(name)}</td>` +
      `<td class="">${t.picks}</td>` +
      `<td class="">${t.w}-${t.l}-${t.p}</td>` +
      `<td class="">${pct}%</td>` +
      `<td class="" style="color:${flatColor}">${flatStr}</td>` +
      `<td class="">${t.fav}</td>` +
      `<td class="">${t.dog}</td>` +
      `</tr>`;
  }).join("");

  return `
    <div class="card card-records">
      <div class="summaryTitle">\u{1F3C0} Team Spread Record (ATS)</div>
      <table class="data">
        <thead><tr>
          <th>Team</th><th class="">Picks</th><th class="">W-L-P</th>
          <th class="">Win%</th><th class="">Flat</th>
          <th class="">Fav</th><th class="">Dog</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="tiny">Only graded spread picks. Units at -110 juice.</div>
    </div>`;
}

// ─── Record Computation ─────────────────────────────────────────────────────

function getGradedPicks(store) {
  const picks = [];
  for (const r of store.runs || []) {
    if (r?.burnIn) continue;
    for (const g of r.games || []) {
      if (!Number.isFinite(g.homeScore) || !Number.isFinite(g.awayScore)) continue;
      if (g.status === "MISSING_ODDS" || g.status === "SKIPPED") continue;
      picks.push({ date: r.date, ...g });
    }
  }
  return picks;
}

function tallyPicks(list, { type = "spread", conf = null, side = null } = {}) {
  let w = 0, l = 0, p = 0;

  for (const g of list) {
    const pick = type === "spread" ? g.sPick : g.oPick;
    const pickConf = type === "spread" ? g.sConf : g.oConf;
    if (!pick || pick === "PASS") continue;
    if (conf && pickConf !== conf) continue;

    // Filter by side (fav/dog for spread, over/under for total)
    if (side) {
      if (type === "spread") {
        if (!isActionable(pickConf)) continue;
        const parsed = parseSpreadPick(pick);
        if (!parsed) continue;
        const pickSide = parsed.sign === "-" ? "fav" : "dog";
        if (pickSide !== side) continue;
      } else {
        if (!isActionable(pickConf)) continue;
        const pickSide = pick === "OVER" ? "over" : "under";
        if (pickSide !== side) continue;
      }
    }

    const result = gradeResult(g, type);
    if (!result) continue;

    if (result === "WIN") {
      w++;
    } else if (result === "LOSS") {
      l++;
    } else {
      p++;
    }
  }

  const pct = winPct(w, l);
  const units = calcUnits(w, l);
  return { w, l, p, pct, played: w + l + p, units };
}

function computeSummaryObj(store) {
  const picks = getGradedPicks(store);
  const tally = (opts) => tallyPicks(picks, opts);

  return {
    spread: {
      all:   tally({ type: "spread" }),
      elite: tally({ type: "spread", conf: "elite" }),
    },
    favdog: {
      fav: tally({ type: "spread", side: "fav" }),
      dog: tally({ type: "spread", side: "dog" }),
    },
    total: {
      all:   tally({ type: "total" }),
      high:  tally({ type: "total", conf: "high" }),
      elite: tally({ type: "total", conf: "elite" }),
    },
    ouside: {
      over:  tally({ type: "total", side: "over" }),
      under: tally({ type: "total", side: "under" }),
    },
  };
}

function computeSummaryText(store) {
  const s = computeSummaryObj(store);
  const row = (label, b) =>
    `  ${label.padEnd(12)} ${b.w}-${b.l}-${b.p}   (${b.pct}%)   [graded ${b.played}]`;

  return [
    "RECORD (graded picks only)", "",
    "SPREAD (ATS)",
    row("All:", s.spread.all),
    row("Elite:", s.spread.elite), "",
    "FAV/DOG (Spread)",
    row("Favorites:", s.favdog.fav),
    row("Underdogs:", s.favdog.dog), "",
  ].join("\n");
}

// ─── Last 10 / Rolling / Weekly ──────────────────────────────────────────────

function getActionablePicks(store, type = "spread") {
  const results = [];
  for (const r of store.runs || []) {
    if (r?.burnIn) continue;
    for (const g of r.games || []) {
      if (g.status === "MISSING_ODDS" || g.status === "SKIPPED") continue;
      const pick = type === "spread" ? g.sPick : g.oPick;
      const conf = type === "spread" ? g.sConf : g.oConf;
      if (!pick || pick === "PASS" || !isActionable(conf)) continue;
      if (!Number.isFinite(g.homeScore) || !Number.isFinite(g.awayScore)) continue;

      const result = gradeResult(g, type);
      const final = `${g.awayScore}-${g.homeScore}`;
      results.push({
        date: r.date,
        matchup: `${g.away} @ ${g.home}`,
        pick,
        conf,
        result: result || "PENDING",
        final,
        total: g.total ?? null,
        home: g.home,
      });
    }
  }
  return results;
}

function computeLast10(store, type = "spread") {
  return getActionablePicks(store, type).slice(-10);
}

function last10Text(store, type = "spread") {
  const label = type === "spread" ? "SPREAD" : "TOTAL";
  const picks = computeLast10(store, type);
  const lines = [`LAST 10 ${label} PICKS (run order)`];
  if (!picks.length) return lines.concat("  No picks yet.").join("\n");
  for (const p of picks) {
    const extra = type === "total" ? ` ${p.total ?? ""}` : "";
    lines.push(`  ${toDisplayDate(p.date)} | ${p.matchup} | ${p.pick}${extra} | ${p.conf.toUpperCase()} | ${p.result} | ${p.final}`);
  }
  return lines.join("\n");
}

function computeRollingWindows(store, type = "spread") {
  const picks = getActionablePicks(store, type).filter((p) => p.result && p.result !== "PENDING");

  // Auto-size: groups of 10 if under 100 picks, otherwise 20
  const window = picks.length < 100 ? 10 : 20;

  const rows = [];
  for (let i = 0; i + window <= picks.length; i += window) {
    const slice = picks.slice(i, i + window);
    const w = slice.filter((x) => x.result === "WIN").length;
    const l = slice.filter((x) => x.result === "LOSS").length;
    const p = slice.filter((x) => x.result === "PUSH").length;
    const units = calcUnits(w, l);

    rows.push({
      label: `#${i + 1}–${i + window}`,
      w, l, p,
      pct: winPct(w, l),
      units,
      startDate: toDisplayDate(slice[0].date),
      endDate: toDisplayDate(slice.at(-1).date),
    });
  }
  return rows;
}

function getMondayOfWeek(date) {
  const d = new Date(date);
  const day = d.getDay(); // 0=Sun, 1=Mon, ...
  const diff = day === 0 ? -6 : 1 - day; // shift back to Monday
  d.setDate(d.getDate() + diff);
  const yy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${yy}-${mm}-${dd}`;
}

function computeWeeklyBreakdown(store, type = "spread") {
  const picks = getActionablePicks(store, type).filter((p) => p.result && p.result !== "PENDING");
  const byWeek = new Map();

  for (const p of picks) {
    const d = new Date(`${toDisplayDate(p.date)}T12:00:00-06:00`);
    const key = getMondayOfWeek(d);
    if (!byWeek.has(key)) byWeek.set(key, []);
    byWeek.get(key).push({ result: p.result, date: p.date });
  }

  const rows = [];
  for (const [week, entries] of [...byWeek.entries()].sort()) {
    const w = entries.filter((x) => x.result === "WIN").length;
    const l = entries.filter((x) => x.result === "LOSS").length;
    const p = entries.filter((x) => x.result === "PUSH").length;
    const units = calcUnits(w, l);

    rows.push({ week, w, l, p, pct: winPct(w, l), units });
  }
  return rows.slice(-12);
}

// ─── Yesterday's Recap ───────────────────────────────────────────────────────

function computeYesterdayRecap(store, yesterdayDate) {
  const yesterdayRun = (store.runs || []).find((r) => r.date === yesterdayDate);
  if (!yesterdayRun) return null;

  const picks = [];
  let units = 0;

  for (const g of yesterdayRun.games || []) {
    if (g.status === "MISSING_ODDS" || g.status === "SKIPPED") continue;
    if (!Number.isFinite(g.homeScore) || !Number.isFinite(g.awayScore)) continue;

    // Spread pick only
    if (g.sPick && g.sPick !== "PASS" && isActionable(g.sConf)) {
      const result = g.sResult || gradeSpreadPick(g);
      if (result) {
        picks.push({
          matchup: `${g.away} @ ${g.home}`,
          pick: g.sPick,
          conf: g.sConf,
          result,
          final: `${g.awayScore}-${g.homeScore}`,
          sDiff: g.sDiff,
        });

        if (result === "WIN") {
          units += 1;
        } else if (result === "LOSS") {
          units -= 1.1;
        }
      }
    }
  }

  if (!picks.length) return null;

  const w = picks.filter(p => p.result === "WIN").length;
  const l = picks.filter(p => p.result === "LOSS").length;
  const p = picks.filter(p => p.result === "PUSH").length;

  return {
    date: yesterdayDate,
    dateDisplay: toDisplayDate(yesterdayDate),
    picks,
    tally: { w, l, p },
    units: Math.round(units * 100) / 100,
  };
}

function buildRecapHtml(recap) {
  if (!recap || !recap.picks.length) return "";

  const resultBadge = (result) => {
    if (result === "WIN") return `<span style="color:#10b981; font-weight:700">✓ WIN</span>`;
    if (result === "LOSS") return `<span style="color:#ef4444; font-weight:700">✗ LOSS</span>`;
    return `<span style="color:#6b7280; font-weight:700">⊜ PUSH</span>`;
  };

  const rows = recap.picks.map(p => {
    return `<tr>
      <td>🏀 ${esc(p.matchup)}</td>
      <td><b>${esc(p.pick)}</b> <span class="trend-label">sDiff ${p.sDiff != null ? p.sDiff.toFixed(1) : "—"}</span></td>
      <td style="text-align:center">${resultBadge(p.result)}</td>
      <td style="text-align:center">${esc(p.final)}</td>
    </tr>`;
  }).join("");

  const t = recap.tally;
  const summaryStr = `<b>${t.w}-${t.l}${t.p ? `-${t.p}` : ""}</b>`;
  const unitsColor = recap.units >= 0 ? "#10b981" : "#ef4444";
  const unitsStr = `<span style="color:${unitsColor}; font-weight:700">${recap.units >= 0 ? "+" : ""}${recap.units.toFixed(2)}u</span>`;

  return `
    <div class="card" style="border-left:4px solid #f59e0b; margin-bottom:10px;">
      <div class="summaryTitle">📋 Yesterday's Recap (${esc(recap.dateDisplay)})</div>
      <table class="data">
        <thead><tr>
          <th>Game</th>
          <th>Pick</th>
          <th style="text-align:center">Result</th>
          <th style="text-align:center">Final</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="tiny" style="margin-top:6px">Spread: ${summaryStr} · ${unitsStr}</div>
    </div>`;
}

// ─── Email HTML Builder ──────────────────────────────────────────────────────

const EMAIL_STYLES = `
<style>
  body { font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif; margin:0; padding:0; background:#f3f4f6; }
  .wrap { max-width: 920px; margin: 0 auto; padding: 16px; }
  .h1 { font-size:20px; font-weight:800; color:#111827; }
  .sub { font-size:12px; color:#6b7280; }
  .card { background:#fff; border:1px solid #e5e7eb; border-radius:14px; padding:12px; box-shadow: 0 1px 3px rgba(0,0,0,.05); }
  .card .summaryTitle { font-size:12px; font-weight:800; color:#111827; letter-spacing:.02em; margin-bottom:8px; }
  .tiny { font-size:11px; color:#6b7280; line-height:1.35; }
  table.data { width:100%; border-collapse:collapse; }
  table.data th, table.data td { font-size:12px; padding:7px 6px; border-bottom:1px solid #eef2f7; }
  table.data th { text-align:left; color:#6b7280; font-weight:700; }
  .right { text-align:right; }

  .badge { display:inline-block; font-size:10px; font-weight:800; padding:3px 8px; border-radius:999px; border:1px solid #e5e7eb; white-space:nowrap; }
  .b-high { background:#ecfdf5; color:#047857; border-color:#a7f3d0; }
  .b-elite { background:#111827; color:#ffffff; border-color:#111827; }
  .b-low { background:#fef3c7; color:#92400e; border-color:#fde68a; }
  .pick-team { font-weight:800; color:#111827; }
  .pick-line { font-size:11px; color:#6b7280; }
  .no-picks { color:#6b7280; font-size:12px; padding:6px 0; }
  .trend-label { font-size:11px; color:#6b7280; }
  .l10-tally { margin-top:10px; font-size:13px; font-weight:700; color:#111827; }
  .win-text { color:#047857; }
  .loss-text { color:#b91c1c; }
  .section-label { font-size:10px; font-weight:800; text-transform:uppercase; letter-spacing:.1em; padding:10px 0 4px; opacity:.55; }
  .card-picks { border-left: 4px solid #6366f1; }
  .card-records { border-left: 4px solid #0ea5e9; }
  .card-games { border-left: 4px solid #f59e0b; }
  .card-trends { border-left: 4px solid #10b981; }
</style>`;

function winRateClass(pct) {
  const n = Number(pct);
  if (!Number.isFinite(n)) return "";
  return n >= 55 ? "win-text" : n <= 50 ? "loss-text" : "";
}

function unitsClass(u) {
  if (!Number.isFinite(u)) return "";
  return u > 0 ? "win-text" : u < 0 ? "loss-text" : "";
}

function confBadge(conf) {
  const c = String(conf || "").toLowerCase();
  const cls = c === "elite" ? "b-elite" : c === "high" ? "b-high" : "b-low";
  return `<span class="badge ${cls}">${esc(c ? c.toUpperCase() : "N/A")}</span>`;
}

function buildTable(rows, cols) {
  if (!rows?.length) return '<div class="no-picks">Not enough picks yet.</div>';
  const header = cols.map((c) => `<th>${esc(c.label)}</th>`).join("");
  const body = rows
    .map((r) => `<tr>${cols.map((c) => `<td>${c.render(r)}</td>`).join("")}</tr>`)
    .join("");
  return `<table class="data"><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table>`;
}

function wlpHtml(r) {
  return `<span class="win-text">${r.w}W</span>–<span class="loss-text">${r.l}L</span>–${r.p}P`;
}

function pctHtml(pct) {
  return `<span class="${winRateClass(pct)}">${pct}%</span>`;
}

function unitsHtml(units) {
  return `<span class="${unitsClass(units)}">${fmtUnits(units)}</span>`;
}

function tallyL10(rows, type = "spread") {
  const w = rows.filter((x) => x.result === "WIN").length;
  const l = rows.filter((x) => x.result === "LOSS").length;
  const p = rows.filter((x) => x.result === "PUSH").length;
  const units = calcUnits(w, l);
  return { w, l, p, pct: winPct(w, l), units };
}

function buildRecordTable(title, bucket, { cardClass = "card-records" } = {}) {
  const row = (label, b) =>
    `<tr><td>${label}</td><td class="">${b.w}-${b.l}-${b.p}</td>` +
    `<td class="">${esc(b.pct)}%</td><td class="">${esc(fmtUnits(b.units))}</td>` +
    `<td class="">${b.played}</td></tr>`;

  return `
    <div class="card ${cardClass}">
      <div class="summaryTitle">${esc(title)}</div>
      <table class="data">
        <thead><tr><th>Bucket</th><th class="">W-L-P</th><th class="">Win%</th><th class="">Units</th><th class="">Graded</th></tr></thead>
        <tbody>
          ${row("All", bucket.all)}
          ${bucket.elite ? row("Elite", bucket.elite) : ""}
        </tbody>
      </table>
      <div class="tiny">Only graded picks (games with final scores + a non-PASS pick).</div>
    </div>`;
}

function buildSplitTable(title, subtitle, rows, cardClass = "card-records") {
  const body = rows
    .map(({ label, emoji, data }) =>
      `<tr><td>${emoji} ${label}</td><td class="">${data.w}-${data.l}-${data.p}</td>` +
      `<td class="">${esc(data.pct)}%</td><td class="">${esc(fmtUnits(data.units))}</td>` +
      `<td class="">${data.played}</td></tr>`
    )
    .join("");

  return `
    <div class="card ${cardClass}">
      <div class="summaryTitle">${esc(title)}</div>
      <table class="data">
        <thead><tr><th>Bucket</th><th class="">W-L-P</th><th class="">Win%</th><th class="">Units</th><th class="">Graded</th></tr></thead>
        <tbody>${body}</tbody>
      </table>
      <div class="tiny">${esc(subtitle)}</div>
    </div>`;
}

function buildCombinedRecordTable(title, eliteBucket, splitRows, cardClass = "card-records") {
  const row = (label, b) => {
    return `<tr><td>${label}</td><td class="">${b.w}-${b.l}-${b.p}</td>` +
    `<td class="">${esc(b.pct)}%</td><td class="">${esc(fmtUnits(b.units))}</td>` +
    `<td class="">${b.played}</td></tr>`;
  };

  const splitBody = splitRows
    .map(({ label, emoji, data }) => row(`${emoji} ${label}`, data))
    .join("");

  return `
    <div class="card ${cardClass}">
      <div class="summaryTitle">${esc(title)}</div>
      <table class="data">
        <thead><tr><th>Bucket</th><th class="">W-L-P</th><th class="">Win%</th><th class="">Flat</th><th class="">Graded</th></tr></thead>
        <tbody>
          ${row("🔒 Elite", eliteBucket)}
          ${splitBody}
        </tbody>
      </table>
      <div class="tiny">Only graded picks (games with final scores + a non-PASS pick).</div>
    </div>`;
}

function buildGameProbTable(games) {
  const rows = (games || [])
    .filter((g) => g.status !== "MISSING_ODDS" && g.status !== "SKIPPED")
    .map((g) => {
      const sPickDisplay = g.sPick && g.sPick !== "PASS" ? esc(g.sPick) : `<span style="color:#9ca3af">PASS</span>`;
      const oPickDisplay = g.oPick && g.oPick !== "PASS" ? esc(g.oPick) : `<span style="color:#9ca3af">PASS</span>`;

      const pCoverStr = g.pCover != null ? `<b>${(g.pCover * 100).toFixed(0)}%</b>` : `<span style="color:#9ca3af">—</span>`;
      const pOUStr    = g.pOU   != null ? `<b>${(g.pOU   * 100).toFixed(0)}%</b>` : `<span style="color:#9ca3af">—</span>`;

      const pHome = g.pHomeCover != null ? (g.pHomeCover * 100).toFixed(0) + "%" : "—";
      const pAway = g.pAwayCover != null ? (g.pAwayCover * 100).toFixed(0) + "%" : "—";
      const pOver  = g.pOver  != null ? (g.pOver  * 100).toFixed(0) + "%" : "—";
      const pUnder = g.pUnder != null ? (g.pUnder * 100).toFixed(0) + "%" : "—";

      const sConfBadge = g.sPick && g.sPick !== "PASS" ? ` ${confBadge(g.sConf)}` : "";
      const oConfBadge = g.oPick && g.oPick !== "PASS" ? ` ${confBadge(g.oConf)}` : "";

      const margin = Number.isFinite(g.margin) ? (g.margin >= 0 ? "+" : "") + fmtNum(g.margin, 1) : "—";
      const tDiff  = Number.isFinite(g.tDiff)  ? (g.tDiff  >= 0 ? "+" : "") + fmtNum(g.tDiff,  1) : "—";

      return `<tr>
        <td style="font-weight:700">${esc(g.away)} @ ${esc(g.home)}</td>
        <td>${sPickDisplay}${sConfBadge}<div class="tiny" style="margin-top:2px">Line ${fmtNum(g.line,1)} · proj ${margin} · sDiff ${fmtNum(g.sDiff,1)}</div></td>
        <td style="text-align:center">${pCoverStr}<div class="tiny">${pAway} away / ${pHome} home</div></td>
        <td style="text-align:center"><div class="tiny">O/U ${fmtNum(g.total,1)} · diff ${tDiff}</div></td>
      </tr>`;
    }).join("");

  if (!rows) return "";

  return `<div class="card" style="border-left:4px solid #8b5cf6; margin-bottom:10px;">
    <div class="summaryTitle">🎯 Cover Probabilities — All Games</div>
    <table class="data">
      <thead><tr>
        <th>Game</th>
        <th>Spread Pick</th>
        <th style="text-align:center">P(Cover)</th>
        <th style="text-align:center">O/U Info</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <div class="tiny" style="margin-top:6px">P(Cover) / P(Hit) = probability the picked side wins. Directional % shown below.</div>
  </div>`;
}

function buildEmailHtml(run, summaryObj, last10, last10Totals, weeklySpread, weeklyTotal, rollingSpread, rollingTotal, teamRecords, calibRows, yesterdayRecap) {
  const s = summaryObj || computeSummaryObj({ runs: [] });
  const calibHtml = calibRows ? buildCalibrationHtml(calibRows) : "";

  // ── Today's picks cards ──
  const buildPicksList = (games, type) => {
    const isSpread = type === "spread";
    const filtered = games
      .filter((g) => {
        if (g.status === "MISSING_ODDS" || g.status === "SKIPPED") return false;
        const pick = isSpread ? g.sPick : g.oPick;
        const conf = isSpread ? g.sConf : g.oConf;
        return pick && pick !== "PASS" && isActionable(conf);
      })
      .sort((a, b) => {
        const aP = isSpread ? (a.pCover ?? 0) : (a.pOU ?? 0);
        const bP = isSpread ? (b.pCover ?? 0) : (b.pOU ?? 0);
        return bP - aP;
      });

    if (!filtered.length) return `<div class="no-picks">No actionable ${type} picks today.</div>`;

    const renderPick = (g) => {
      if (isSpread) {
        const pStr = g.pCover ? ` · P=${(g.pCover*100).toFixed(0)}%` : "";
        const projMargin = Math.round((g.hS - g.aS) * 10) / 10;
        const favTeam = projMargin >= 0 ? g.home : g.away;
        return `<div style="padding:6px 0; border-bottom:1px dashed #eef2f7;">🏀 <span class="pick-team">${esc(g.sPick)}</span>` +
          ` <span class="trend-label">proj ${esc(favTeam)} by ${fmtNum(Math.abs(projMargin), 1)} · sDiff ${fmtNum(g.sDiff, 1)}${pStr}</span>` +
          ` ${confBadge(g.sConf)}</div>`;
      }
      const arrow = g.oPick === "OVER" ? "⬆️" : "⬇️";
      const pStr = g.pOU ? ` · P=${(g.pOU*100).toFixed(0)}%` : "";
      const cleanTotal = Number.isFinite(g.tDiff) && Number.isFinite(g.total) ? g.total + g.tDiff : g.pT;
      return `<div style="padding:6px 0; border-bottom:1px dashed #eef2f7;">${arrow}` +
        ` <span class="pick-team">${esc(g.oPick)} ${fmtNum(g.total, 1)}</span>` +
        ` <span class="pick-line">${esc(g.away)} @ ${esc(g.home)}</span>` +
        ` <span class="trend-label">proj ${fmtNum(cleanTotal, 1)} · edge ${fmtNum(Math.abs(g.tDiff), 1)}${pStr}</span>` +
        ` ${confBadge(g.oConf)}</div>`;
    };

    const confKey = isSpread ? "sConf" : "oConf";
    const elites = filtered.filter(g => g[confKey] === "elite");
    const highs  = filtered.filter(g => g[confKey] === "high");

    let html = "";
    if (elites.length) {
      html += `<div style="padding:4px 0 2px; font-weight:700; font-size:13px; color:#111827;">🔒 Elite</div>`;
      html += elites.map(renderPick).join("");
    }
    if (highs.length) {
      html += `<div style="padding:${elites.length ? '10' : '4'}px 0 2px; font-weight:700; font-size:13px; color:#047857;">🟢 High</div>`;
      html += highs.map(renderPick).join("");
    }
    return html;
  };

  // ── Last 10 cards ──
  const buildLast10Card = (title, picks, type) => {
    const rows = picks.map((p) => ({ ...p, dateDisp: toDisplayDate(p.date) }));
    const t = tallyL10(rows, type);
    const pickCol = type === "total"
      ? { label: "Pick", right: false, render: (r) => `<span class="pick-team">${esc(r.pick)} ${esc(r.total ?? "")}</span>` }
      : { label: "Pick", right: false, render: (r) => `<span class="pick-team">${esc(r.pick)}</span>` };

    return `<div class="card card-trends">` +
      `<div class="summaryTitle">${title}</div>` +
      buildTable(rows, [
        { label: "Date", right: false, render: (r) => esc(r.dateDisp) },
        { label: "Matchup", right: false, render: (r) => esc(r.matchup) },
        pickCol,
        { label: "Conf", center: true, render: (r) => confBadge(r.conf) },
        { label: "Result", center: true, render: (r) => esc(r.result) },
        { label: "Final", center: true, render: (r) => esc(r.final) },
      ]) +
      `<div class="l10-tally">Last 10: ${wlpHtml(t)} · ${pctHtml(t.pct)} · ${unitsHtml(t.units)}</div>` +
      `</div>`;
  };

  // ── Weekly + Rolling cards ──
  const buildWeeklyCard = (title, rows) =>
    `<div class="card card-trends"><div class="summaryTitle">${title}</div>` +
    buildTable(rows, [
      { label: "Week of", right: false, render: (r) => esc(r.week) },
      { label: "W-L-P", center: true, render: wlpHtml },
      { label: "Win%", center: true, render: (r) => pctHtml(r.pct) },
      { label: "Flat", center: true, render: (r) => unitsHtml(r.units) },
    ]) + `</div>`;

  const buildRollingCard = (title, rows) => {
    const windowSize = rows.length > 0 ? (rows[0].label.match(/#\d+–(\d+)/)?.[1] || "?") : "?";
    const fullTitle = title.replace("{n}", windowSize);
    return `<div class="card card-trends"><div class="summaryTitle">${fullTitle}</div>` +
    `<div class="tiny" style="margin-bottom:6px">Green = above break-even (52.4%) · Red = below · Units at -110</div>` +
    buildTable(rows, [
      { label: "Window", right: false, render: (r) => esc(r.label) },
      { label: "W-L-P", center: true, render: wlpHtml },
      { label: "Win%", center: true, render: (r) => pctHtml(r.pct) },
      { label: "Flat", center: true, render: (r) => unitsHtml(r.units) },
      { label: "Dates", center: true, render: (r) => `<span class="trend-label">${esc(r.startDate)} → ${esc(r.endDate)}</span>` },
    ]) + `</div>`;
  };

  // ── Layout helpers (email-safe table layout) ──
  const row2col = (left, right) =>
    `<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:10px;">
      <tr>
        <td width="50%" valign="top" style="padding-right:5px;">${left}</td>
        <td width="50%" valign="top" style="padding-left:5px;">${right}</td>
      </tr>
    </table>`;

  const rowFull = (content) =>
    `<div style="margin-bottom:10px;">${content}</div>`;

  const sectionLabel = (text) =>
    `<div class="section-label">${esc(text)}</div>`;

  // ── Build game cards in pairs ──
  const gameCards = (run.games || []).map((g) => {
    const isSkipped = g.status === "MISSING_ODDS" || g.status === "SKIPPED";

    const spreadPick = !isSkipped && g.sPick && g.sPick !== "PASS"
      ? (() => {
          const projMargin = Math.round((g.hS - g.aS) * 10) / 10;
          const favTeam = projMargin >= 0 ? g.home : g.away;
          return `<div><span class="pick-team">${esc(g.sPick)}</span> ${confBadge(g.sConf)} <span class="trend-label">proj ${esc(favTeam)} by ${fmtNum(Math.abs(projMargin), 1)} · sDiff ${fmtNum(g.sDiff, 1)}${g.pCover ? ` · P=${(g.pCover*100).toFixed(0)}%` : ""}</span></div>`;
        })()
      : `<div class="tiny">Spread: <span class="trend-label">${esc(isSkipped ? g.status : "PASS")}</span></div>`;

    const totalPick = !isSkipped && g.oPick && g.oPick !== "PASS"
      ? (() => {
          const cleanTotal = Number.isFinite(g.tDiff) && Number.isFinite(g.total) ? g.total + g.tDiff : g.pT;
          return `<div class="tiny">Total: <span class="pick-team">${esc(g.oPick)} ${fmtNum(g.total, 1)}</span> ${confBadge(g.oConf)} <span class="trend-label">proj ${fmtNum(cleanTotal, 1)} · edge ${fmtNum(Math.abs(g.tDiff), 1)}${g.pOU ? ` · P=${(g.pOU*100).toFixed(0)}%` : ""}</span></div>`;
        })()
      : `<div class="tiny">Total: <span class="trend-label">${esc(isSkipped ? g.status : "PASS")}</span></div>`;

    let projLine = "";
    if (!isSkipped && Number.isFinite(g.aS) && Number.isFinite(g.hS)) {
      const cleanTotal = Number.isFinite(g.tDiff) && Number.isFinite(g.total) ? g.total + g.tDiff : g.pT;
      projLine = `<div class="tiny" style="margin-top:4px">📐 Proj: <b>${esc(g.away)} ${fmtNum(g.aS, 1)}</b> – <b>${esc(g.home)} ${fmtNum(g.hS, 1)}</b></div>` +
        `<div class="tiny">📐 Total: <b>${fmtNum(cleanTotal, 1)}</b> <span class="trend-label">(line ${fmtNum(g.total, 1)})</span></div>`;
    }

    const trends = g.trends
      ? `<div class="tiny" style="margin-top:4px"><div><b>${esc(g.away)}:</b> ${esc(g.trends.away || "—")}</div><div><b>${esc(g.home)}:</b> ${esc(g.trends.home || "—")}</div></div>`
      : "";

    const injury = g.injuryNote
      ? (() => {
          const sides = g.injuryNote.split(" | ");
          return `<div class="tiny" style="margin-top:4px">` +
            sides.map(s => `<div style="margin-left:0">🏥 ${esc(s)}</div>`).join("") +
            `</div>`;
        })()
      : "";

    const b2b = g.b2bNote
      ? `<div class="tiny" style="margin-top:4px">🔄 ${esc(g.b2bNote)}</div>`
      : "";

    const scoreLine = Number.isFinite(g.awayScore) && Number.isFinite(g.homeScore)
      ? `<div class="tiny" style="margin-top:4px">Final: <b>${esc(g.awayScore)}-${esc(g.homeScore)}</b></div>`
      : "";

    return `<div class="card card-games">
      <div class="summaryTitle">${esc(g.away)} @ ${esc(g.home)} <span class="tiny">Line ${fmtNum(g.line, 1)} · Total ${fmtNum(g.total, 1)}</span></div>
      ${spreadPick}${totalPick}${projLine}${injury}${b2b}${scoreLine}${trends}
    </div>`;
  });

  // Pair game cards into 2-col rows
  let gamesHtml = "";
  for (let i = 0; i < gameCards.length; i += 2) {
    if (i + 1 < gameCards.length) {
      gamesHtml += row2col(gameCards[i], gameCards[i + 1]);
    } else {
      gamesHtml += rowFull(gameCards[i]);
    }
  }

  // ── Assemble full HTML ──
  const timestamp = new Date().toLocaleString("en-US", { timeZone: TIMEZONE });
  const recapHtml = yesterdayRecap ? buildRecapHtml(yesterdayRecap) : "";

  return `<html><head>${EMAIL_STYLES}</head><body>
    <div class="wrap">
      <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:10px;">
        <tr>
          <td>
            <div class="h1">NCAAB Picks — ${esc(run.dateDisplay || "")}</div>
            <div class="sub">High + Elite only (no medium). Units at -110. Times in ${TIMEZONE}.</div>
          </td>
          <td style="text-align:right;" valign="top"><div class="sub">${esc(timestamp)}</div></td>
        </tr>
      </table>
      <div style="text-align:center;color:#888;font-size:11px;margin:4px 0 12px">Filters: P(cover) &ge; 0.60 | Fav line &le; 8 | Dogs uncapped · Tournament: HCA = 0 (neutral sites)</div>

      ${recapHtml ? rowFull(recapHtml) : ""}

      ${rowFull(`<div class="card card-picks"><div class="summaryTitle">🔥 Today's Spread Picks (Actionable)</div>${buildPicksList(run.games || [], "spread")}</div>`)}
      ${rowFull(buildCombinedRecordTable("📊 Spread (ATS)", s.spread.elite, [
          { label: "Favorites", emoji: "⭐", data: s.favdog.fav },
          { label: "Underdogs", emoji: "🐶", data: s.favdog.dog },
        ]))}


      ${sectionLabel("Games")}
      ${gamesHtml}

      ${sectionLabel("Cover Probabilities")}
      ${rowFull(buildGameProbTable(run.games || []))}

      ${sectionLabel("Spread Trends")}
      ${rowFull(buildLast10Card("🧾 Last 10 Spread", last10 || [], "spread"))}
      ${rowFull(buildWeeklyCard("📅 Weekly Spread", weeklySpread || []))}
      ${rowFull(buildRollingCard("📊 Rolling {n}-Pick Groups (Spread)", rollingSpread || []))}


      ${sectionLabel("Team Records")}
      ${rowFull(buildTeamRecordTable(teamRecords || {}))}

      ${calibHtml ? `${sectionLabel("Model Calibration")}
      ${rowFull(`<div class="card"><div class="summaryTitle">📊 P(cover) Calibration</div><div class="tiny" style="margin-bottom:6px">Expected vs actual win rate by probability bucket. Δ shows calibration error.</div>${calibHtml}</div>`)}` : ""}
    </div>
  </body></html>`;
}


// ─── Text Email Builder ──────────────────────────────────────────────────────

function buildTextEmail(run, store) {
  const lines = [`NCAAB PICKS — ${run.dateDisplay}`, "", "TODAY (Actionable only)", ""];

  for (const g of run.games || []) {
    if (g.status === "MISSING_ODDS" || g.status === "SKIPPED") {
      lines.push(`${g.away} @ ${g.home} | ${g.status} | ${g.note || ""}`, "");
      continue;
    }

    lines.push(`${g.away} @ ${g.home} | Line: ${g.home} ${g.line > 0 ? "+" : ""}${fmtNum(g.line, 1)} | Total: ${fmtNum(g.total, 1)}`);

    if (g.sPick && g.sPick !== "PASS") {
      const projMargin = Math.round((g.hS - g.aS) * 10) / 10;
      const favTeam = projMargin >= 0 ? g.home : g.away;
      lines.push(`  Spread: ${g.sPick} (${String(g.sConf).toUpperCase()}) | proj ${favTeam} by ${fmtNum(Math.abs(projMargin), 1)} | edge ${fmtNum(g.sDiff, 1)}`);
    } else {
      lines.push("  Spread: PASS");
    }

    if (g.oPick && g.oPick !== "PASS") {
      const cleanTotal = Number.isFinite(g.tDiff) && Number.isFinite(g.total) ? g.total + g.tDiff : g.pT;
      lines.push(`  Total:  ${g.oPick} ${fmtNum(g.total, 1)} (${String(g.oConf).toUpperCase()}) | proj ${fmtNum(cleanTotal, 1)} | edge ${fmtNum(Math.abs(g.tDiff), 1)}`);
    } else {
      lines.push("  Total:  PASS");
    }

    if (g.trends) {
      lines.push(`  Trends Away: ${g.trends.away || "—"}`);
      lines.push(`  Trends Home: ${g.trends.home || "—"}`);
    }
    lines.push("");
  }

  lines.push("—", computeSummaryText(store), "", last10Text(store, "spread"), "", last10Text(store, "total"));
  return lines.join("\n");
}

// ─── Main Pipeline ───────────────────────────────────────────────────────────

async function main() {
  const date = todayCentralYYYYMMDD();
  const dateDisplay = toDisplayDate(date);
  const store = loadStore();
  const defaults = loadDefaults();

  console.log(`\n══ NCAAB Picks Pipeline — ${dateDisplay} ══\n`);

  // 1. Grade recent days
  const daysToGrade = new Set();
  for (let d = 1; d <= 5; d++) daysToGrade.add(yyyymmddFromCentralOffset(d));
  for (const r of store.runs || []) {
    const hasUngraded = (r.games || []).some(
      (g) => g.status !== "MISSING_ODDS" && g.status !== "SKIPPED" &&
        !Number.isFinite(g.homeScore) &&
        ((g.sPick && g.sPick !== "PASS") || (g.oPick && g.oPick !== "PASS"))
    );
    if (hasUngraded) daysToGrade.add(r.date);
  }

  const yesterday = yyyymmddFromCentralOffset(1);
  for (const d of daysToGrade) {
    await gradeDateInStore(store, d);
  }

  // 2. Fetch today's data (no injuries, no lineup adjust, no season type for NCAA v1)
  console.log("[1/7] Fetching stats, odds, trends...");
  const [enhancedStats, rawOdds, ats, ou, b2bTeams] = await Promise.all([
    fetchNCAAStatsEnhanced(date),
    fetchTodaysOdds(),
    fetchATSTrends(),
    fetchOUTrends(),
    detectB2B().catch(() => new Set()),
  ]);

  // Tournament team validation (March Madness / NIT)
  let tourneyTeams = {};
  try {
    tourneyTeams = await fetchTournamentTeams(date, Object.keys(enhancedStats.season || {}));
  } catch (e) {
    console.log(`  [tourney] WARNING: Could not fetch tournament teams: ${e.message}`);
  }

  // Tournament team validation (March Madness / NIT)
  let odds = rawOdds;
  if (Object.keys(tourneyTeams).length) {
    console.log(`  [tourney] ${Object.keys(tourneyTeams).length} tournament team names loaded`);
    const normT = s => String(s || "").toLowerCase()
      .replace(/[^a-z0-9\s]/g, " ")
      .replace(/\bstate\b/g, "st")
      .replace(/\s+/g, " ").trim();

    const fuzzyTourney = (nameNorm) => {
      if (!nameNorm) return null;
      let best = null, bestLen = 0;
      for (const [tk, tv] of Object.entries(tourneyTeams)) {
        if (tk.startsWith(nameNorm) || nameNorm.startsWith(tk)) {
          if (tk.length > bestLen) { best = tv; bestLen = tk.length; }
        }
      }
      return best;
    };

    odds = [];
    for (const g of rawOdds) {
      const awayN = normT(g.away);
      const homeN = normT(g.home);
      const awayMatch = tourneyTeams[awayN] || fuzzyTourney(awayN);
      const homeMatch = tourneyTeams[homeN] || fuzzyTourney(homeN);
      if (awayMatch || homeMatch) {
        if (awayMatch && awayMatch !== g.away) {
          console.log(`  [tourney] Corrected: ${g.away} -> ${awayMatch}`);
          g.away = awayMatch;
        }
        if (homeMatch && homeMatch !== g.home) {
          console.log(`  [tourney] Corrected: ${g.home} -> ${homeMatch}`);
          g.home = homeMatch;
        }
        odds.push(g);
      } else {
        console.log(`  [tourney] Skipping non-tournament game: ${g.away} @ ${g.home}`);
      }
    }
  }

  let baseW = store.weights || defaults.DEFAULT_W;
  let baseWVar = store.weightsVar || defaults.DEFAULT_W_VAR;

  // Blend season + last 10 games for recent form
  const stats = blendBase(enhancedStats.season, enhancedStats.last10, baseW.recentWeight ?? 0.35);

  // Cache stats to disk for recalculate.mjs (full enhanced set)
  try {
    const { fileURLToPath: toPath } = await import("url");
    const { default: fsx } = await import("fs");
    const { default: pathMod } = await import("path");
    const scriptsDir = pathMod.dirname(toPath(import.meta.url));
    const cacheDir = pathMod.join(scriptsDir, "..", "data", "stats_cache");
    if (!fsx.existsSync(cacheDir)) fsx.mkdirSync(cacheDir, { recursive: true });
    fsx.writeFileSync(pathMod.join(cacheDir, date + ".json"), JSON.stringify(enhancedStats));
  } catch (e) { /* non-critical */ }

  // 3. B2B rest + Kalman state (no lineup adjustment for NCAA)
  console.log("[2/7] Applying B2B rest + Kalman filter...");

  const { adjusted: adjustedStats, b2bNotes } = applyB2BAdjustment(stats, b2bTeams, odds);
  const a = getAvgs(adjustedStats); // league averages (used as fallback)

  let kalmanState = loadKalmanState();
  if (!Object.keys(kalmanState.teams || {}).length) {
    kalmanState = initializeKalman(stats);
  }

  // Feed newly graded games into Kalman — learn from yesterday BEFORE drifting
  const newlyGraded = [];
  for (const r of (store.runs || []).slice(-14)) {
    if (r.date === date) continue;
    for (const g of r.games || []) {
      if (g.status === "MISSING_ODDS" || g.status === "SKIPPED") continue;
      if (!Number.isFinite(g.homeScore) || !Number.isFinite(g.awayScore)) continue;
      if (!Number.isFinite(g.hS) || !Number.isFinite(g.aS)) continue;
      g._kalmanDate = r.date;
      newlyGraded.push(g);
    }
  }
  batchUpdate(kalmanState, newlyGraded);

  // Apply drift AFTER learning — account for time passing to today
  applyDailyDrift(kalmanState, date);

  // 3b. Self-tune weights on yesterday's graded games BEFORE analysis
  const yesterdayRun = (store.runs || []).find(r => r.date === yesterday);
  const recentGraded = [];
  if (yesterdayRun) {
    for (const g of yesterdayRun.games || []) {
      if (g.status === "MISSING_ODDS" || g.status === "SKIPPED") continue;
      if (!Number.isFinite(g.homeScore) || !Number.isFinite(g.awayScore)) continue;
      if (!g._features) continue;
      recentGraded.push(g);
    }
  }
  if (recentGraded.length && store.lastTuneDate !== date) {
    const { W: tunedW, W_var: tunedWVar } = tuneWeights(baseW, baseWVar, recentGraded);
    baseW = tunedW;
    baseWVar = tunedWVar;
    store.weights = tunedW;
    store.weightsVar = tunedWVar;
    store.lastTuneDate = date;
    console.log("[3b] Weights tuned on", recentGraded.length, "graded games from", yesterday);
  } else if (store.lastTuneDate === date) {
    console.log("[3b] Weights already tuned today — skipping");
  }

  // 3c. Compute dynamic residualVar from historical prediction errors
  const dynamicResidualVar = computeResidualVar(store.runs || []);
  store.residualVar = dynamicResidualVar;

  // 4. Analyze each game
  console.log("[3/7] Analyzing " + odds.length + " games...");
  const games = [];
  for (const g of odds) {
    if (typeof g.line !== "number" || typeof g.total !== "number") {
      games.push({ ...g, status: "MISSING_ODDS" });
      continue;
    }

    // Per-game: blend home team toward their home splits, away toward road splits
    const gameStats = blendForGame(
      adjustedStats, enhancedStats.home, enhancedStats.away,
      g.home, g.away, baseW.locationWeight ?? 0.25
    );
    const gameAvgs = getAvgs(gameStats);

    // No injury adjustment for NCAA
    const injuryAdj = null;

    g._date = date;  // used by analyzeGame for tournament neutral-site detection
    const r = analyzeGame(g, gameStats, gameAvgs, baseW, injuryAdj, kalmanState, baseWVar, dynamicResidualVar);

    if (!r) {
      games.push({ ...g, status: "SKIPPED" });
      continue;
    }

    games.push(r);
  }

  // 5. Attach trends
  for (const g of games) {
    if (g.status === "MISSING_ODDS" || g.status === "SKIPPED") continue;
    const atsKeyA = resolveKey(ats, g.away), atsKeyH = resolveKey(ats, g.home);
    const ouKeyA  = resolveKey(ou,  g.away), ouKeyH  = resolveKey(ou,  g.home);
    g.trends = {
      away: joinTrends(g.away, ats[atsKeyA], ou[ouKeyA]),
      home: joinTrends(g.home, ats[atsKeyH], ou[ouKeyH]),
    };

    // B2B notes
    const awayB2B = b2bNotes[g.away] || null;
    const homeB2B = b2bNotes[g.home] || null;
    if (awayB2B || homeB2B) {
      g.b2bNote = [awayB2B ? `${g.away}: ${awayB2B}` : null, homeB2B ? `${g.home}: ${homeB2B}` : null].filter(Boolean).join(" | ");
    }
  }

  // 6. Build run record
  console.log("[4/7] Saving...");
  const run = { date, dateDisplay, burnIn: false, weightsUsed: baseW, weightsNext: baseW, games, summaryText: "" };

  // 7. Save state
  console.log("[5/7] Saving...");
  run.summaryText = computeSummaryText(store);
  upsertRun(store, run);
  saveStore(store);

  pruneProcessedGames(kalmanState, 30);
  saveKalmanState(kalmanState);

  // 8. Compute trend data
  const summaryObj = computeSummaryObj(store);
  const l10 = computeLast10(store, "spread");
  const l10t = computeLast10(store, "total");
  const weeklySpread = computeWeeklyBreakdown(store, "spread");
  const weeklyTotal = computeWeeklyBreakdown(store, "total");
  const rollingSpread = computeRollingWindows(store, "spread");
  const rollingTotal = computeRollingWindows(store, "total");
  const teamRecords = computeTeamRecords(store);
  const calibRows = buildCalibrationTable(store);
  const yesterdayRecap = computeYesterdayRecap(store, yesterday);

  // 9. Send
  console.log("[6/7] Sending email...");
  const html = buildEmailHtml(run, summaryObj, l10, l10t, weeklySpread, weeklyTotal, rollingSpread, rollingTotal, teamRecords, calibRows, yesterdayRecap);
  const text = buildTextEmail(run, store);
  const subject = `[JS] NCAA Daily Picks \u2014 ${run.dateDisplay}`;

  await sendEmail(subject, text, html);


  // 10. Summary
  console.log(`\nDone: ${subject}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
