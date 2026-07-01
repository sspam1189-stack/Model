import re
from .http import fetch_text, html


def _clean_team(s):
    return re.sub(r"\s+", " ", s).strip()


def _parse_percent(s):
    m = re.search(r"(\d+(?:\.\d+)?)%", str(s))
    return float(m.group(1)) if m else None


def _parse_number(s):
    cleaned = re.sub(r"[^0-9.\-]", "", str(s))
    try:
        x = float(cleaned)
        return x if x == x else None  # NaN check
    except (ValueError, TypeError):
        return None


def _parse_table(soup, table):
    header_els = table.find("thead").find_all("th") if table.find("thead") else []
    headers = [th.get_text().strip().lower() for th in header_els]
    rows = []
    tbody = table.find("tbody")
    if tbody:
        for tr in tbody.find_all("tr"):
            cells = [td.get_text().strip() for td in tr.find_all("td")]
            if cells:
                rows.append({"headers": headers, "cells": cells})
    return {"headers": headers, "rows": rows}


def fetch_ou_trends():
    try:
        url = "https://www.teamrankings.com/wnba/trends/ou_trends/"
        text = fetch_text(url)
        result = html(url, text)
        soup = result["$"]
        table = soup.find("table")
        if table is None:
            return {}
    except Exception as e:
        print(f"  [trends] OU trends fetch failed ({e}) — skipping (display-only)")
        return {}
    parsed = _parse_table(soup, table)
    headers = parsed["headers"]
    rows = parsed["rows"]

    team_idx = next((i for i, h in enumerate(headers) if "team" in h), -1)
    over_pct_idx = next((i for i, h in enumerate(headers) if "over %" in h), -1)
    under_pct_idx = next((i for i, h in enumerate(headers) if "under %" in h), -1)
    total_pm_idx = next((i for i, h in enumerate(headers) if "total +/-" in h), -1)

    out = {}
    for r in rows:
        team = _clean_team(r["cells"][team_idx] if team_idx >= 0 else "")
        if not team:
            continue
        out[team] = {
            "overPct": _parse_percent(r["cells"][over_pct_idx]) if over_pct_idx >= 0 else None,
            "underPct": _parse_percent(r["cells"][under_pct_idx]) if under_pct_idx >= 0 else None,
            "totalPlusMinus": _parse_number(r["cells"][total_pm_idx]) if total_pm_idx >= 0 else None,
        }
    return out


def fetch_ats_trends():
    try:
        url = "https://www.teamrankings.com/wnba/trends/ats_trends/"
        text = fetch_text(url)
        result = html(url, text)
        soup = result["$"]
        table = soup.find("table")
        if table is None:
            return {}
    except Exception as e:
        print(f"  [trends] ATS trends fetch failed ({e}) — skipping (display-only)")
        return {}
    parsed = _parse_table(soup, table)
    headers = parsed["headers"]
    rows = parsed["rows"]

    team_idx = next((i for i, h in enumerate(headers) if "team" in h), -1)
    ats_pct_idx = next((i for i, h in enumerate(headers) if "ats %" in h), -1)
    ats_pm_idx = next((i for i, h in enumerate(headers) if "ats +/-" in h), -1)

    out = {}
    for r in rows:
        team = _clean_team(r["cells"][team_idx] if team_idx >= 0 else "")
        if not team:
            continue
        out[team] = {
            "atsPct": _parse_percent(r["cells"][ats_pct_idx]) if ats_pct_idx >= 0 else None,
            "atsPlusMinus": _parse_number(r["cells"][ats_pm_idx]) if ats_pm_idx >= 0 else None,
        }
    return out
