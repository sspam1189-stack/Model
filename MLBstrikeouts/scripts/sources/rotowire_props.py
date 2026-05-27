"""
Rotowire MLB pitcher strikeouts scraper.

Parses the inline data array on
https://www.rotowire.com/betting/mlb/player-props.php — each row carries
per-book columns for line + over/under prices across DraftKings,
FanDuel, BetMGM, Caesars, BetRivers, Hard Rock, theScore.

Per-pitcher book priority: DraftKings -> Caesars -> Hard Rock -> FanDuel.
The first book with both a non-empty line and at least one priced side
wins. Pitchers absent from Rotowire entirely fall through to the FD
direct scrape and finally the Odds API at the run_daily merge step.

Strikeouts market only. Uses the same cache file + line_key dedup as
odds_fanduel.py / odds_theoddsapi.py so all three sources read/write a
unified mlb_props_{date_key}.json.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path

_URL = "https://www.rotowire.com/betting/mlb/player-props.php"
_UA = "Mozilla/5.0 (compatible; MLB-Model/1.0)"
_PROPS_CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "props_cache" / "mlb"

# Book priority for line + price selection. First non-empty wins.
# Order: DraftKings -> Caesars -> Hard Rock -> FanDuel. Odds API is the
# external last-resort fallback applied at the run_daily merge step.
_BOOK_PRIORITY = ("draftkings", "caesars", "hardrock", "fanduel")

# Rotowire team abbreviation aliases (Rotowire -> our model canonical).
# Without normalizing, the (player, market, event_away, event_home)
# line_key collides with FanDuel's identical row but different team code,
# leaving both entries in the unified cache as duplicates.
_TEAM_NORMALIZE = {
    "ATH": "OAK",   # Rotowire uses ATH for the Sacramento Athletics; FD uses OAK
    "WSH": "WAS",   # Rotowire uses WSH for Washington; FD uses WAS
}


def _normalize_team(abbr: str) -> str:
    return _TEAM_NORMALIZE.get(abbr, abbr)


# --- Cache helpers (mirror odds_fanduel.py / odds_theoddsapi.py) -----------


def _props_cache_path(date_key):
    return _PROPS_CACHE_DIR / f"mlb_props_{date_key}.json"


def _rotowire_cache_path(date_key):
    """Separate Rotowire-only cache (alongside the unified mlb_props file)."""
    return _PROPS_CACHE_DIR / f"rotowire_props_{date_key}.json"


def _load_cache(path, max_age_hours=None):
    if not path.exists():
        return None
    if max_age_hours is not None:
        age_h = (time.time() - path.stat().st_mtime) / 3600
        if age_h > max_age_hours:
            return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return None


def _save_cache(data, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _line_key(p):
    return (
        p.get("player", ""),
        p.get("market", ""),
        p.get("event_away", ""),
        p.get("event_home", ""),
    )


# --- Scrape ----------------------------------------------------------------


def _fetch_html(timeout: float = 20.0) -> str:
    req = urllib.request.Request(_URL, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _extract_strikeouts_rows(html: str) -> list[dict]:
    """Extract the data:[...] array from the strikeouts section."""
    m = re.search(
        r'<div id="props-strikeouts"(.*?)(?=<div id="props-|</body>)',
        html, re.DOTALL,
    )
    if not m:
        return []
    section = m.group(1)
    dm = re.search(r"data:\s*(\[\s*\{.*?\}\s*\]),", section, re.DOTALL)
    if not dm:
        return []
    try:
        return json.loads(dm.group(1))
    except Exception:
        return []


def _to_int_price(val):
    if val in (None, "", "null"):
        return None
    try:
        return int(str(val).strip())
    except (TypeError, ValueError):
        return None


def _to_float_line(val):
    if val in (None, "", "null"):
        return None
    try:
        return float(str(val).strip())
    except (TypeError, ValueError):
        return None


def _pick_book(row: dict):
    """Return (book, line, over_price, under_price) for the first priority
    book with a usable line. None if no priority book has the pitcher.
    """
    for book in _BOOK_PRIORITY:
        line = _to_float_line(row.get(f"{book}_strikeouts"))
        if line is None:
            continue
        over = _to_int_price(row.get(f"{book}_strikeoutsOver"))
        under = _to_int_price(row.get(f"{book}_strikeoutsUnder"))
        if over is None and under is None:
            continue
        return book, line, over, under
    return None


def fetch_rotowire_strikeouts_props(
    date_key: str | None = None,
    started_team_keys=None,
) -> list[dict]:
    """Fetch pitcher-strikeouts props from Rotowire and merge into the
    shared MLB props cache.

    Parameters
    ----------
    date_key : str or None
        Date in YYYYMMDD format. Auto-detected if None.
    started_team_keys : iterable[str] or None
        Set of team abbreviations whose game has already started. Fresh
        scrape rows involving any of these teams are dropped — the cached
        pre-start line is preserved verbatim instead.

    Returns
    -------
    list[dict]
      [{
        "player": "Chad Patrick",
        "market": "strikeouts",
        "line": 3.5,
        "over_price": -161,
        "under_price": 126,
        "event_home": "MIL", "event_away": "STL",
        "source": "rotowire:draftkings",
      }, ...]
    """
    if date_key is None:
        import datetime
        from zoneinfo import ZoneInfo
        date_key = datetime.datetime.now(
            ZoneInfo("America/Chicago")
        ).strftime("%Y%m%d")

    started_team_keys = set(started_team_keys or [])

    cp = _props_cache_path(date_key)
    rw_cp = _rotowire_cache_path(date_key)
    existing_props = _load_cache(cp, max_age_hours=None) or []
    existing_rw = _load_cache(rw_cp, max_age_hours=None) or []

    try:
        html = _fetch_html()
    except Exception as e:
        print(f"  [rotowire_props] fetch failed: {e}")
        return existing_rw

    rows = _extract_strikeouts_rows(html)
    if not rows:
        print("  [rotowire_props] no strikeouts data found in HTML")
        return existing_rw

    new_props = []
    book_counts = {}
    started_skipped = 0
    for row in rows:
        picked = _pick_book(row)
        if picked is None:
            continue
        book, line, over, under = picked
        name = (row.get("name") or "").strip()
        if not name:
            continue
        team = _normalize_team((row.get("team") or "").strip())
        opp_raw = (row.get("opp") or "").strip()
        away_marker = opp_raw.startswith("@")
        opp = _normalize_team(opp_raw.lstrip("@").strip())
        if away_marker:
            event_home, event_away = opp, team
        else:
            event_home, event_away = team, opp
        # Drop fresh entries for started games so the pre-start cached line
        # stays frozen (same convention as odds_fanduel started_games).
        if team in started_team_keys or opp in started_team_keys:
            started_skipped += 1
            continue
        new_props.append({
            "player": name,
            "market": "strikeouts",
            "line": line,
            "over_price": over,
            "under_price": under,
            "event_home": event_home,
            "event_away": event_away,
            "source": f"rotowire:{book}",
        })
        book_counts[book] = book_counts.get(book, 0) + 1

    # Merge into unified mlb_props_{date_key}.json: cached entries kept;
    # fresh entries with matching line_key overwrite. Started-game rows
    # were dropped above, so cached pre-start lines stay frozen.
    fresh_keys = {_line_key(p) for p in new_props}
    kept_props = [p for p in existing_props if _line_key(p) not in fresh_keys]
    all_props = kept_props + new_props
    if all_props:
        _save_cache(all_props, cp)

    # Separate rotowire_props_{date_key}.json cache: rotowire-only entries,
    # merged the same way against the prior rotowire snapshot.
    rw_fresh_keys = {_line_key(p) for p in new_props}
    kept_rw = [p for p in existing_rw if _line_key(p) not in rw_fresh_keys]
    all_rw = kept_rw + new_props
    if all_rw:
        _save_cache(all_rw, rw_cp)

    parts = ", ".join(f"{c} {b.upper()}" for b, c in book_counts.items())
    started_note = f", skipped {started_skipped} started" if started_skipped else ""
    print(f"  [rotowire_props] Kept {len(kept_props)} cached + {len(new_props)} fresh "
          f"({parts}{started_note}) — unified total {len(all_props)} / rotowire {len(all_rw)}")
    return new_props


if __name__ == "__main__":
    props = fetch_rotowire_strikeouts_props()
    print(f"\nFetched {len(props)} props:")
    for p in props:
        src = p["source"].split(":")[1].upper()
        print(f"  {p['player']:25s} {p['event_away']}@{p['event_home']:5s} "
              f"line={p['line']:>4} O{p['over_price']} U{p['under_price']} [{src}]")
