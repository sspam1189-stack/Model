// scripts/calibration.mjs
// ────────────────────────────────────────────────────────────────────────────
// P(cover) calibration — are the probabilities accurate?
// ────────────────────────────────────────────────────────────────────────────

const JUICE = 1.1;  // -110 standard

// ── Calibration Table ───────────────────────────────────────────────────────
// Buckets historical picks by their P(cover) and shows actual hit rate.
// If well-calibrated: P=60% bucket should hit ~60%.
// If under-confident: P=60% bucket hits 70% → tighten variance.
// If over-confident: P=60% bucket hits 50% → widen variance.

const BUCKETS = [
  { min: 0.57, max: 0.60, label: "57-60%" },
  { min: 0.60, max: 0.63, label: "60-63%" },
  { min: 0.63, max: 0.66, label: "63-66%" },
  { min: 0.66, max: 0.69, label: "66-69%" },
  { min: 0.69, max: 0.72, label: "69-72%" },
  { min: 0.72, max: 0.75, label: "72-75%" },
  { min: 0.75, max: 0.78, label: "75-78%" },
  { min: 0.78, max: 1.00, label: "78%+" },
];

export function buildCalibrationTable(store) {
  const rows = [];

  for (const bucket of BUCKETS) {
    const hits = { spread: { w: 0, l: 0, p: 0 }, total: { w: 0, l: 0, p: 0 } };

    for (const run of store.runs || []) {
      if (run.burnIn) continue;
      for (const g of run.games || []) {
        // Spread calibration
        if (g.pCover != null && g.pCover >= bucket.min && g.pCover < bucket.max) {
          if (g.sResult === "WIN") hits.spread.w++;
          else if (g.sResult === "LOSS") hits.spread.l++;
          else if (g.sResult === "PUSH") hits.spread.p++;
        }

        // Total calibration
        if (g.pOU != null && g.pOU >= bucket.min && g.pOU < bucket.max) {
          if (g.oResult === "WIN") hits.total.w++;
          else if (g.oResult === "LOSS") hits.total.l++;
          else if (g.oResult === "PUSH") hits.total.p++;
        }
      }
    }

    const sN = hits.spread.w + hits.spread.l;
    const tN = hits.total.w + hits.total.l;

    rows.push({
      label: bucket.label,
      midpoint: (bucket.min + bucket.max) / 2,
      spread: {
        n: sN,
        winPct: sN > 0 ? Math.round(100 * hits.spread.w / sN) : null,
        units: sN > 0 ? +(hits.spread.w * (1 / JUICE) - hits.spread.l).toFixed(1) : 0,
      },
      total: {
        n: tN,
        winPct: tN > 0 ? Math.round(100 * hits.total.w / tN) : null,
        units: tN > 0 ? +(hits.total.w * (1 / JUICE) - hits.total.l).toFixed(1) : 0,
      },
    });
  }

  return rows;
}

// Build HTML for the calibration card in the email
export function buildCalibrationHtml(calibRows) {
  const hasData = calibRows.some(r => r.spread.n > 0 || r.total.n > 0);
  if (!hasData) return "";

  let html = `<table width="100%" cellpadding="4" cellspacing="0" style="font-size:12px; border-collapse:collapse;">
    <tr style="background:#1a2332; color:#e2e8f0;">
      <th align="left">P(cover)</th>
      <th align="left">Spread N</th>
      <th align="left">Actual%</th>
      <th align="left">Δ</th>
      <th align="left">Total N</th>
      <th align="left">Actual%</th>
      <th align="left">Δ</th>
    </tr>`;

  for (const r of calibRows) {
    const midStr = Math.round(r.midpoint * 100);
    const sDelta = r.spread.winPct != null ? r.spread.winPct - midStr : null;
    const tDelta = r.total.winPct != null ? r.total.winPct - midStr : null;

    const sColor = sDelta != null ? (sDelta >= 0 ? "#48bb78" : "#fc8181") : "#a0aec0";
    const tColor = tDelta != null ? (tDelta >= 0 ? "#48bb78" : "#fc8181") : "#a0aec0";

    html += `<tr style="border-bottom:1px solid #2d3748;">
      <td>${r.label}</td>
      <td align="left">${r.spread.n || "—"}</td>
      <td align="left">${r.spread.winPct != null ? r.spread.winPct + "%" : "—"}</td>
      <td align="left" style="color:${sColor}">${sDelta != null ? (sDelta >= 0 ? "+" : "") + sDelta : "—"}</td>
      <td align="left">${r.total.n || "—"}</td>
      <td align="left">${r.total.winPct != null ? r.total.winPct + "%" : "—"}</td>
      <td align="left" style="color:${tColor}">${tDelta != null ? (tDelta >= 0 ? "+" : "") + tDelta : "—"}</td>
    </tr>`;
  }

  html += `</table>`;
  return html;
}
