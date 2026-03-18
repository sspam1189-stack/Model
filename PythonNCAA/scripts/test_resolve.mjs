import fs from "fs";

const cache = JSON.parse(fs.readFileSync("data/barttorvik_cache.json", "utf8"));
const H = cache.stats;

function normKey(s) { return String(s||"").toLowerCase().replace(/[^a-z0-9\s]/g," ").replace(/\s+/g," ").trim(); }
function collapseAbbr(s) { return s.replace(/\./g, ""); }
function safeFuzzy(a,b) { if(!a||!b) return false; if(a===b) return true; const shorter=a.length<b.length?a:b; const longer=a.length<b.length?b:a; if(shorter.length/longer.length<0.85) return false; return longer.includes(shorter); }

function resolveTeam(H, name) {
  if (!H || !name) return null;
  if (H[name]) return name;
  const keys = Object.keys(H);
  const wanted = normKey(name);
  const wantedCollapsed = normKey(collapseAbbr(name));
  for (const k of keys) { if (normKey(k) === wanted) return k; }
  for (const k of keys) { if (normKey(collapseAbbr(k)) === wantedCollapsed) return k; }
  for (const k of keys) { const nk = normKey(k); if (safeFuzzy(nk, wanted)) return k; }
  // Longest prefix match
  let bestPrefix = null, bestLen = 0;
  for (const k of keys) {
    const nk = normKey(k);
    const nkc = normKey(collapseAbbr(k));
    const matchNk = wanted.startsWith(nk + " ") || wantedCollapsed.startsWith(nk + " ");
    const matchNkc = wantedCollapsed.startsWith(nkc + " ");
    if ((matchNk || matchNkc) && nk.length >= 3) {
      const len = Math.max(nk.length, nkc.length);
      if (len > bestLen) { bestLen = len; bestPrefix = k; }
    }
  }
  if (bestPrefix) return bestPrefix;
  return null;
}

const tests = [
  "UMBC Retrievers","Howard Bison","UNC Wilmington Seahawks","Yale Bulldogs",
  "Stephen F. Austin Lumberjacks","Tulsa Golden Hurricane","NC State Wolfpack",
  "Texas Longhorns","UNLV Rebels","UC Irvine Anteaters",
  "Duke","Kansas","Gonzaga","Auburn",
  "Arkansas Razorbacks","Kansas Jayhawks","Oregon Ducks","Oregon St. Beavers",
  "Texas A&M Aggies","North Texas Mean Green"
];
for (const t of tests) { console.log(t, "->", resolveTeam(H, t) || "FAIL"); }
