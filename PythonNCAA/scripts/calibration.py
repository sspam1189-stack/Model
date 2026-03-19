# scripts/calibration.py
# --------------------------------------------------------------------------
# P(cover) calibration — are the probabilities accurate?
# --------------------------------------------------------------------------

JUICE = 1.1  # -110 standard


# -- Calibration Table -----------------------------------------------------
# Buckets historical picks by their P(cover) and shows actual hit rate.
# If well-calibrated: P=60% bucket should hit ~60%.
# If under-confident: P=60% bucket hits 70% -> tighten variance.
# If over-confident: P=60% bucket hits 50% -> widen variance.

BUCKETS = [
    {"min": 0.57, "max": 0.60, "label": "57-60%"},
    {"min": 0.60, "max": 0.63, "label": "60-63%"},
    {"min": 0.63, "max": 0.66, "label": "63-66%"},
    {"min": 0.66, "max": 0.69, "label": "66-69%"},
    {"min": 0.69, "max": 0.72, "label": "69-72%"},
    {"min": 0.72, "max": 0.75, "label": "72-75%"},
    {"min": 0.75, "max": 0.78, "label": "75-78%"},
    {"min": 0.78, "max": 1.00, "label": "78%+"},
]


def build_calibration_table(store):
    rows = []

    for bucket in BUCKETS:
        hits = {
            "spread": {"w": 0, "l": 0, "p": 0},
            "total": {"w": 0, "l": 0, "p": 0},
        }

        for run in store.get("runs", []):
            if run.get("burnIn"):
                continue
            for g in run.get("games", []):
                # Spread calibration
                p_cover = g.get("pCover")
                if p_cover is not None and bucket["min"] <= p_cover < bucket["max"]:
                    if g.get("sResult") == "WIN":
                        hits["spread"]["w"] += 1
                    elif g.get("sResult") == "LOSS":
                        hits["spread"]["l"] += 1
                    elif g.get("sResult") == "PUSH":
                        hits["spread"]["p"] += 1

                # Total calibration
                p_ou = g.get("pOU")
                if p_ou is not None and bucket["min"] <= p_ou < bucket["max"]:
                    if g.get("oResult") == "WIN":
                        hits["total"]["w"] += 1
                    elif g.get("oResult") == "LOSS":
                        hits["total"]["l"] += 1
                    elif g.get("oResult") == "PUSH":
                        hits["total"]["p"] += 1

        s_n = hits["spread"]["w"] + hits["spread"]["l"]
        t_n = hits["total"]["w"] + hits["total"]["l"]

        rows.append({
            "label": bucket["label"],
            "midpoint": (bucket["min"] + bucket["max"]) / 2,
            "spread": {
                "n": s_n,
                "winPct": round(100 * hits["spread"]["w"] / s_n) if s_n > 0 else None,
                "units": round(hits["spread"]["w"] * (1 / JUICE) - hits["spread"]["l"], 1) if s_n > 0 else 0,
            },
            "total": {
                "n": t_n,
                "winPct": round(100 * hits["total"]["w"] / t_n) if t_n > 0 else None,
                "units": round(hits["total"]["w"] * (1 / JUICE) - hits["total"]["l"], 1) if t_n > 0 else 0,
            },
        })

    return rows


# Build HTML for the calibration card in the email
def build_calibration_html(calib_rows):
    has_data = any(r["spread"]["n"] > 0 or r["total"]["n"] > 0 for r in calib_rows)
    if not has_data:
        return ""

    html = '''<table width="100%" cellpadding="4" cellspacing="0" style="font-size:12px; border-collapse:collapse;">
    <tr style="background:#1a2332; color:#e2e8f0;">
      <th align="left">P(cover)</th>
      <th align="left">Spread N</th>
      <th align="left">Actual%</th>
      <th align="left">D</th>
      <th align="left">Total N</th>
      <th align="left">Actual%</th>
      <th align="left">D</th>
    </tr>'''

    for r in calib_rows:
        mid_str = round(r["midpoint"] * 100)
        s_delta = r["spread"]["winPct"] - mid_str if r["spread"]["winPct"] is not None else None
        t_delta = r["total"]["winPct"] - mid_str if r["total"]["winPct"] is not None else None

        s_color = ("#48bb78" if s_delta >= 0 else "#fc8181") if s_delta is not None else "#a0aec0"
        t_color = ("#48bb78" if t_delta >= 0 else "#fc8181") if t_delta is not None else "#a0aec0"

        s_n_str = str(r["spread"]["n"]) if r["spread"]["n"] else "\u2014"
        s_pct_str = f"{r['spread']['winPct']}%" if r["spread"]["winPct"] is not None else "\u2014"
        s_delta_str = (("+" if s_delta >= 0 else "") + str(s_delta)) if s_delta is not None else "\u2014"

        t_n_str = str(r["total"]["n"]) if r["total"]["n"] else "\u2014"
        t_pct_str = f"{r['total']['winPct']}%" if r["total"]["winPct"] is not None else "\u2014"
        t_delta_str = (("+" if t_delta >= 0 else "") + str(t_delta)) if t_delta is not None else "\u2014"

        html += f'''<tr style="border-bottom:1px solid #2d3748;">
      <td>{r["label"]}</td>
      <td align="left">{s_n_str}</td>
      <td align="left">{s_pct_str}</td>
      <td align="left" style="color:{s_color}">{s_delta_str}</td>
      <td align="left">{t_n_str}</td>
      <td align="left">{t_pct_str}</td>
      <td align="left" style="color:{t_color}">{t_delta_str}</td>
    </tr>'''

    html += "</table>"
    return html
