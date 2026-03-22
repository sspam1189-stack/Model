# scripts/sources/teamrankings_trends.py
# Scrapes TeamRankings ATS and O/U trend tables.

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
    """Parse an HTML table into headers and row dicts."""
    header_els = table.select("thead th")
    headers = [th.get_text(strip=True).lower() for th in header_els]
    rows = []
    for tr in table.select("tbody tr"):
        cells = [td.get_text(strip=True) for td in tr.select("td")]
        if not cells:
            continue
        rows.append({"headers": headers, "cells": cells})
    return {"headers": headers, "rows": rows}


def fetch_ou_trends():
    """Fetch over/under trends from TeamRankings."""
    url = "https://www.teamrankings.com/nba/trends/ou_trends/"
    text = fetch_text(url)
    parsed = html(url, text)
    soup = parsed["soup"]

    table = soup.select_one("table")
    data = _parse_table(soup, table)
    headers = data["headers"]
    rows = data["rows"]

    team_idx = next((i for i, h in enumerate(headers) if "team" in h), 0)
    over_pct_idx = next((i for i, h in enumerate(headers) if "over %" in h), -1)
    under_pct_idx = next((i for i, h in enumerate(headers) if "under %" in h), -1)
    total_pm_idx = next((i for i, h in enumerate(headers) if "total +/-" in h), -1)

    out = {}
    for r in rows:
        team = _clean_team(r["cells"][team_idx] if team_idx < len(r["cells"]) else "")
        if not team:
            continue
        out[team] = {
            "overPct": _parse_percent(r["cells"][over_pct_idx]) if over_pct_idx >= 0 and over_pct_idx < len(r["cells"]) else None,
            "underPct": _parse_percent(r["cells"][under_pct_idx]) if under_pct_idx >= 0 and under_pct_idx < len(r["cells"]) else None,
            "totalPlusMinus": _parse_number(r["cells"][total_pm_idx]) if total_pm_idx >= 0 and total_pm_idx < len(r["cells"]) else None,
        }
    return out


def fetch_ats_trends():
    """Fetch ATS trends from TeamRankings."""
    url = "https://www.teamrankings.com/nba/trends/ats_trends/"
    text = fetch_text(url)
    parsed = html(url, text)
    soup = parsed["soup"]

    table = soup.select_one("table")
    data = _parse_table(soup, table)
    headers = data["headers"]
    rows = data["rows"]

    team_idx = next((i for i, h in enumerate(headers) if "team" in h), 0)
    ats_pct_idx = next((i for i, h in enumerate(headers) if "ats %" in h), -1)
    ats_pm_idx = next((i for i, h in enumerate(headers) if "ats +/-" in h), -1)

    out = {}
    for r in rows:
        team = _clean_team(r["cells"][team_idx] if team_idx < len(r["cells"]) else "")
        if not team:
            continue
        out[team] = {
            "atsPct": _parse_percent(r["cells"][ats_pct_idx]) if ats_pct_idx >= 0 and ats_pct_idx < len(r["cells"]) else None,
            "atsPlusMinus": _parse_number(r["cells"][ats_pm_idx]) if ats_pm_idx >= 0 and ats_pm_idx < len(r["cells"]) else None,
        }
    return out
