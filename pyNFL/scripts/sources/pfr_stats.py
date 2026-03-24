"""
pfr_stats.py — Pro Football Reference supplementary data source.

Scrapes PFR for snap counts and target shares as optional enrichment
for the pyNFL pipeline. PFR actively blocks automated scrapers, so every
function is wrapped in a try/except and returns None on failure. The
pipeline runs fine without this data (falls back to nflfastR-derived
snap/target estimates).

Rate-limiting: 2-second delay between requests to respect PFR's servers.
"""

import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_URL = "https://www.pro-football-reference.com"
_REQUEST_DELAY_SEC = 2.0
_REQUEST_TIMEOUT_SEC = 15

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.google.com/",
}

# Module-level timestamp for rate-limiting across calls.
_last_request_time: float = 0.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _rate_limited_get(url: str) -> requests.Response:
    """GET *url* while respecting the per-module rate limit.

    Raises on non-200 status so callers can catch uniformly.
    """
    global _last_request_time

    elapsed = time.time() - _last_request_time
    if elapsed < _REQUEST_DELAY_SEC:
        time.sleep(_REQUEST_DELAY_SEC - elapsed)

    resp = requests.get(url, headers=_HEADERS, timeout=_REQUEST_TIMEOUT_SEC)
    _last_request_time = time.time()

    if resp.status_code in (403, 429):
        raise RuntimeError(
            f"PFR blocked request (HTTP {resp.status_code}). "
            "Returning None — pipeline will use nflfastR fallback."
        )
    resp.raise_for_status()
    return resp


def _parse_comment_table(soup: BeautifulSoup, table_id: str) -> Optional[BeautifulSoup]:
    """PFR hides some tables inside HTML comments. Extract and re-parse."""
    # First try a visible table.
    table = soup.find("table", {"id": table_id})
    if table is not None:
        return table

    # Fall back to comment-embedded tables.
    from bs4 import Comment

    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        if table_id in comment:
            fragment = BeautifulSoup(comment, "html.parser")
            table = fragment.find("table", {"id": table_id})
            if table is not None:
                return table

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_snap_counts(
    season: int, week: Optional[int] = None
) -> Optional[dict]:
    """Fetch player snap-count data from Pro Football Reference.

    Parameters
    ----------
    season : int
        NFL season year (e.g. 2025).
    week : int or None
        If provided, fetch snap counts for a specific week.
        If None, fetch the full-season snap-count summary.

    Returns
    -------
    dict or None
        Keyed by player name with snap-count details, e.g.::

            {
                "Patrick Mahomes": {
                    "team": "KAN",
                    "position": "QB",
                    "off_snaps": 1050,
                    "off_pct": 100.0,
                    "def_snaps": 0,
                    "def_pct": 0.0,
                    "st_snaps": 0,
                    "st_pct": 0.0,
                },
                ...
            }

        Returns None if the request fails for any reason.
    """
    try:
        if week is not None:
            url = f"{_BASE_URL}/years/{season}/week_{week}_snap-counts.htm"
        else:
            url = f"{_BASE_URL}/years/{season}/snap-counts.htm"

        resp = _rate_limited_get(url)
        soup = BeautifulSoup(resp.text, "html.parser")

        table = _parse_comment_table(soup, "snap_counts")
        if table is None:
            print(
                f"[WARN] pfr_stats: snap-counts table not found for "
                f"season={season}, week={week} — continuing without PFR data"
            )
            return None

        rows = table.find("tbody").find_all("tr")
        results: dict = {}

        for row in rows:
            # Skip separator / sub-header rows.
            if row.get("class") and "thead" in row.get("class", []):
                continue

            cells = row.find_all(["th", "td"])
            if len(cells) < 8:
                continue

            player_tag = cells[0].find("a")
            player_name = player_tag.text.strip() if player_tag else cells[0].text.strip()
            if not player_name:
                continue

            def _safe_int(cell) -> int:
                txt = cell.text.strip().replace(",", "")
                return int(txt) if txt else 0

            def _safe_float(cell) -> float:
                txt = cell.text.strip().replace("%", "")
                return float(txt) if txt else 0.0

            results[player_name] = {
                "team": cells[1].text.strip(),
                "position": cells[2].text.strip(),
                "off_snaps": _safe_int(cells[3]),
                "off_pct": _safe_float(cells[4]),
                "def_snaps": _safe_int(cells[5]),
                "def_pct": _safe_float(cells[6]),
                "st_snaps": _safe_int(cells[7]),
                "st_pct": _safe_float(cells[8]) if len(cells) > 8 else 0.0,
            }

        if not results:
            print(
                f"[WARN] pfr_stats: parsed 0 rows from snap-counts for "
                f"season={season}, week={week} — continuing without PFR data"
            )
            return None

        return results

    except Exception as e:
        print(f"[WARN] pfr_stats: {e} — continuing without PFR snap-count data")
        return None


def fetch_target_shares(
    season: int, week: Optional[int] = None
) -> Optional[dict]:
    """Fetch player target-share data from Pro Football Reference.

    Parameters
    ----------
    season : int
        NFL season year (e.g. 2025).
    week : int or None
        If provided, fetch targets for a specific week.
        If None, fetch the full-season receiving summary.

    Returns
    -------
    dict or None
        Keyed by player name with receiving/target details, e.g.::

            {
                "Tyreek Hill": {
                    "team": "MIA",
                    "position": "WR",
                    "targets": 170,
                    "receptions": 119,
                    "target_share": 28.5,
                    "yards": 1799,
                    "td": 13,
                    "catch_pct": 70.0,
                },
                ...
            }

        Returns None if the request fails for any reason.
    """
    try:
        if week is not None:
            url = f"{_BASE_URL}/years/{season}/week_{week}_receiving.htm"
        else:
            url = f"{_BASE_URL}/years/{season}/receiving.htm"

        resp = _rate_limited_get(url)
        soup = BeautifulSoup(resp.text, "html.parser")

        table = _parse_comment_table(soup, "receiving")
        if table is None:
            print(
                f"[WARN] pfr_stats: receiving table not found for "
                f"season={season}, week={week} — continuing without PFR data"
            )
            return None

        rows = table.find("tbody").find_all("tr")
        results: dict = {}

        for row in rows:
            if row.get("class") and "thead" in row.get("class", []):
                continue

            cells = row.find_all(["th", "td"])
            if len(cells) < 8:
                continue

            player_tag = cells[0].find("a")
            player_name = player_tag.text.strip() if player_tag else cells[0].text.strip()
            if not player_name:
                continue

            def _safe_int(cell) -> int:
                txt = cell.text.strip().replace(",", "")
                return int(txt) if txt else 0

            def _safe_float(cell) -> float:
                txt = cell.text.strip().replace("%", "")
                return float(txt) if txt else 0.0

            # PFR receiving table columns vary — extract by data-stat attribute
            # when available, fall back to positional indexing.
            def _get_stat(row_cells, stat_name: str, fallback_idx: int, parse_fn):
                for c in row_cells:
                    if c.get("data-stat") == stat_name:
                        return parse_fn(c)
                if fallback_idx < len(row_cells):
                    return parse_fn(row_cells[fallback_idx])
                return parse_fn.__defaults__[0] if hasattr(parse_fn, "__defaults__") else 0

            team = ""
            pos = ""
            for c in cells:
                if c.get("data-stat") == "team":
                    team = c.text.strip()
                elif c.get("data-stat") == "pos":
                    pos = c.text.strip()

            if not team:
                team = cells[1].text.strip() if len(cells) > 1 else ""
            if not pos:
                pos = cells[2].text.strip() if len(cells) > 2 else ""

            targets = 0
            receptions = 0
            yards = 0
            td = 0
            catch_pct = 0.0
            tgt_share = 0.0

            for c in cells:
                ds = c.get("data-stat", "")
                if ds == "targets":
                    targets = _safe_int(c)
                elif ds == "rec":
                    receptions = _safe_int(c)
                elif ds == "rec_yds":
                    yards = _safe_int(c)
                elif ds == "rec_td":
                    td = _safe_int(c)
                elif ds == "catch_pct":
                    catch_pct = _safe_float(c)
                elif ds == "tgt_pct":
                    tgt_share = _safe_float(c)

            results[player_name] = {
                "team": team,
                "position": pos,
                "targets": targets,
                "receptions": receptions,
                "target_share": tgt_share,
                "yards": yards,
                "td": td,
                "catch_pct": catch_pct,
            }

        if not results:
            print(
                f"[WARN] pfr_stats: parsed 0 rows from receiving for "
                f"season={season}, week={week} — continuing without PFR data"
            )
            return None

        return results

    except Exception as e:
        print(f"[WARN] pfr_stats: {e} — continuing without PFR target-share data")
        return None


def fetch_pfr_data(season, week=None):
    """Convenience wrapper fetching all PFR data in one call.

    Returns dict with 'snap_counts' and 'target_shares' keys (either may be None).
    """
    return {
        "snap_counts": fetch_snap_counts(season, week=week),
        "target_shares": fetch_target_shares(season, week=week),
    }
