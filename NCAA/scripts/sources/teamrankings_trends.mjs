import { fetchText, html } from "./http.mjs";

function cleanTeam(s) {
  return s.replace(/\s+/g, " ").trim();
}

function parsePercent(s) {
  const m = String(s).match(/(\d+(?:\.\d+)?)%/);
  return m ? parseFloat(m[1]) : null;
}

function parseNumber(s) {
  const x = parseFloat(String(s).replace(/[^0-9.\-]/g, ""));
  return Number.isFinite(x) ? x : null;
}

function parseTable($, table) {
  const headers = $(table).find("thead th").toArray().map(th => $(th).text().trim().toLowerCase());
  const rows = [];
  $(table).find("tbody tr").each((_, tr) => {
    const cells = $(tr).find("td").toArray().map(td => $(td).text().trim());
    if (!cells.length) return;
    rows.push({ headers, cells });
  });
  return { headers, rows };
}

export async function fetchOUTrends() {
  const url = "https://www.teamrankings.com/ncaa-basketball/trends/ou_trends/";
  const text = await fetchText(url);
  const { $ } = html(url, text);

  const table = $("table").first();
  const { headers, rows } = parseTable($, table);

  const teamIdx = headers.findIndex(h => h.includes("team"));
  const overPctIdx = headers.findIndex(h => h.includes("over %"));
  const underPctIdx = headers.findIndex(h => h.includes("under %"));
  const totalPmIdx = headers.findIndex(h => h.includes("total +/-"));

  const out = {};
  for (const r of rows) {
    const team = cleanTeam(r.cells[teamIdx] || "");
    if (!team) continue;
    out[team] = {
      overPct: parsePercent(r.cells[overPctIdx]),
      underPct: parsePercent(r.cells[underPctIdx]),
      totalPlusMinus: parseNumber(r.cells[totalPmIdx]),
    };
  }
  return out;
}

export async function fetchATSTrends() {
  const url = "https://www.teamrankings.com/ncaa-basketball/trends/ats_trends/";
  const text = await fetchText(url);
  const { $ } = html(url, text);

  const table = $("table").first();
  const { headers, rows } = parseTable($, table);

  const teamIdx = headers.findIndex(h => h.includes("team"));
  const atsPctIdx = headers.findIndex(h => h.includes("ats %"));
  const atsPmIdx = headers.findIndex(h => h.includes("ats +/-"));

  const out = {};
  for (const r of rows) {
    const team = cleanTeam(r.cells[teamIdx] || "");
    if (!team) continue;
    out[team] = {
      atsPct: parsePercent(r.cells[atsPctIdx]),
      atsPlusMinus: parseNumber(r.cells[atsPmIdx]),
    };
  }
  return out;
}
