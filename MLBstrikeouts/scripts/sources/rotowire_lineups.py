"""
Rotowire default lineup scraper.

Parses the "Default vs. RHP" / "Default vs. LHP" sections from
https://www.rotowire.com/baseball/batting-orders.php?team={ABBR}

Used as the fallback lineup when MLB StatsAPI hasn't posted today's
card yet — the default vs opposing-hand is closer to the real lineup
than the team's most recent batting order, which is shaped by the
previous game's opposing pitcher.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
import urllib.request
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "stats_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

_UA = "Mozilla/5.0 (compatible; MLB-Model/1.0)"

# Rotowire uses "ATH" for the Sacramento Athletics (formerly OAK).
# Unknown team codes silently fall through to ARI's default page, so we
# must translate rather than trust the model's abbreviation verbatim.
_ROTOWIRE_ABBR_ALIASES = {
    "OAK": "ATH",
}

# Matches: Default vs. RHP</div> ... <ol class="list is-rankings ..."> ... </ol>
_SECTION_RE = re.compile(
    r'Default vs\. (RHP|LHP)\s*</div>\s*'
    r'<ol[^>]*class="[^"]*is-rankings[^"]*"[^>]*>(.*?)</ol>',
    re.DOTALL,
)
_PLAYER_RE = re.compile(
    r'<a[^>]+href="/baseball/player/[^"]+"[^>]*>([^<]+)</a>',
    re.DOTALL,
)


def _norm_name(name: str) -> str:
    """Lowercase, strip accents, drop punctuation — for roster matching."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z\s]", " ", s).strip().lower()
    s = re.sub(r"\s+", " ", s)
    # Drop Jr/Sr/II/III suffix so "Lourdes Gurriel Jr" matches "Lourdes Gurriel"
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s).strip()
    return s


def _cache_path(date_str: str, team_abbr: str) -> Path:
    return CACHE_DIR / f"rotowire_{date_str.replace('-', '')}_{team_abbr}.json"


def fetch_default_lineup(team_abbr: str, vs_hand: str, date_str: str,
                         max_age_hours: float = 3.0) -> list[dict] | None:
    """
    Scrape Rotowire default lineup for `team_abbr` vs opposing hand.

    Parameters
    ----------
    team_abbr : str       e.g. "MIL"
    vs_hand   : "R" | "L" — the OPPOSING pitcher's throw-hand
    date_str  : "YYYY-MM-DD" — used for cache key only (Rotowire page is
                               a rolling "today" view; we still want
                               per-day caching so past dates are stable).
    max_age_hours : re-fetch today's page after this staleness.

    Returns
    -------
    [{"name": "Brice Turang", "slot": 1}, ...] in order, or None on failure.
    """
    if vs_hand not in ("R", "L"):
        return None
    section_key = "RHP" if vs_hand == "R" else "LHP"
    cpath = _cache_path(date_str, team_abbr)
    # Cache: {"RHP": [...], "LHP": [...], "fetched_at": epoch}
    cached = None
    if cpath.exists():
        try:
            cached = json.loads(cpath.read_text())
            age_h = (time.time() - cached.get("fetched_at", 0)) / 3600.0
            if age_h < max_age_hours and section_key in cached:
                return cached[section_key]
        except Exception:
            cached = None

    rw_abbr = _ROTOWIRE_ABBR_ALIASES.get(team_abbr, team_abbr)
    url = f"https://www.rotowire.com/baseball/batting-orders.php?team={rw_abbr}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        html = urllib.request.urlopen(req, timeout=15).read().decode(
            "utf-8", "ignore"
        )
    except Exception as e:
        print(f"  [rotowire] {team_abbr}: fetch failed: {e}")
        return cached.get(section_key) if cached else None

    parsed = {"RHP": [], "LHP": []}
    for m in _SECTION_RE.finditer(html):
        hand = m.group(1)
        block = m.group(2)
        names = [p.strip() for p in _PLAYER_RE.findall(block)]
        parsed[hand] = [{"name": n, "slot": i + 1} for i, n in enumerate(names)]

    parsed["fetched_at"] = time.time()
    try:
        cpath.write_text(json.dumps(parsed))
    except Exception:
        pass
    return parsed.get(section_key) or None


def resolve_names_to_ids(names: list[str], roster: dict[str, int]) -> list[int]:
    """
    Map Rotowire names → MLB player IDs using a normalized-name roster dict.

    `roster` is {normalized_full_name: mlb_id}. Returns IDs in the same order
    as `names`, skipping any that fail to resolve.
    """
    out = []
    for n in names:
        pid = roster.get(_norm_name(n))
        if pid:
            out.append(pid)
    return out


def fetch_team_roster_name_to_id(team_id: int, max_age_hours: float = 24.0
                                 ) -> dict[str, int]:
    """
    Return {normalized_full_name: player_id} for a team's active roster.
    Cached per day.
    """
    today = time.strftime("%Y%m%d")
    cpath = CACHE_DIR / f"roster_nameidx_{team_id}_{today}.json"
    if cpath.exists():
        age_h = (time.time() - cpath.stat().st_mtime) / 3600.0
        if age_h < max_age_hours:
            try:
                return {k: int(v) for k, v in json.loads(cpath.read_text()).items()}
            except Exception:
                pass
    url = (
        f"https://statsapi.mlb.com/api/v1/teams/{team_id}/roster"
        f"?rosterType=active"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        raw = json.loads(urllib.request.urlopen(req, timeout=15).read())
    except Exception as e:
        print(f"  [rotowire] roster fetch failed for team {team_id}: {e}")
        return {}
    idx = {}
    for r in raw.get("roster", []):
        person = r.get("person", {})
        pid = person.get("id")
        name = person.get("fullName", "")
        if pid and name:
            idx[_norm_name(name)] = int(pid)
    try:
        cpath.write_text(json.dumps(idx))
    except Exception:
        pass
    return idx
