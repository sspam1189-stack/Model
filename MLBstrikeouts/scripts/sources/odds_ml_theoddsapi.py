# MLBstrikeouts/scripts/sources/odds_ml_theoddsapi.py
# FanDuel moneyline (h2h) odds for the fade-list ML model, via The Odds API.
#
# Two roles:
#   * Historical closing lines for the backfill (FanDuel bookmaker only),
#     pulled from the /historical snapshot at ~game time. FanDuel has no
#     historical feed of its own, so this is the only closing-line source.
#   * Live fallback when the direct FanDuel API (odds_fanduel) is unavailable.
#
# Per-date cache mirrors the K props cache:
#   data/odds_cache/mlb_ml/mlb_ml_<YYYYMMDD>.json  (list of both-team rows)
#   - past dates: permanent (once written, never refetched by backfill)
#   - today: upcoming games overwrite; started games freeze (closing number)

import os
import json
import datetime
import requests
from pathlib import Path

BASE = "https://api.the-odds-api.com/v4"
SPORT_KEY = "baseball_mlb"
BOOK = "fanduel"

_CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "odds_cache" / "mlb_ml"


def _api_key():
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        raise RuntimeError("ODDS_API_KEY not set in environment")
    return key


# --- per-date cache -------------------------------------------------------

def cache_path(date_key):
    return _CACHE_DIR / f"mlb_ml_{date_key}.json"


def load_ml_cache(date_key):
    """Return the cached both-team rows for a date, or None."""
    cp = cache_path(date_key)
    if not cp.exists():
        return None
    try:
        with open(cp, "r") as f:
            return json.load(f)
    except Exception:
        return None


def _row_key(row):
    return (row.get("away"), row.get("home"), row.get("commence"))


def save_ml_cache(date_key, rows, freeze_started=True):
    """Merge ``rows`` into the per-date cache and write it.

    Freeze rule (only relevant for a live 'today' run): a game already marked
    started in the cache is never overwritten — its captured price is the
    closing number. Upcoming games are overwritten with the fresh price.
    Historical/backfill rows carry started=True, so they persist unchanged.
    """
    existing = load_ml_cache(date_key) or []
    by_key = {_row_key(r): r for r in existing}
    for r in rows:
        k = _row_key(r)
        if freeze_started and by_key.get(k, {}).get("started"):
            continue  # frozen — keep the closing snapshot
        by_key[k] = r
    merged = list(by_key.values())
    cp = cache_path(date_key)
    cp.parent.mkdir(parents=True, exist_ok=True)
    with open(cp, "w") as f:
        json.dump(merged, f, indent=2)
    return merged


# --- The Odds API ---------------------------------------------------------

def _fanduel_markets(game):
    """Return the FanDuel bookmaker's markets list for a game (or [])."""
    for bk in game.get("bookmakers", []):
        if bk.get("key") == BOOK:
            return bk.get("markets", [])
    return []


def _extract_fanduel_h2h(game):
    """Return {home_price, away_price} from a game's FanDuel h2h, or None."""
    home = game.get("home_team")
    away = game.get("away_team")
    for mk in _fanduel_markets(game):
        if mk.get("key") != "h2h":
            continue
        prices = {o.get("name"): o.get("price") for o in mk.get("outcomes", [])}
        if home in prices and away in prices:
            return {"home_price": prices[home], "away_price": prices[away]}
    return None


def _extract_fanduel_totals(game):
    """Return {line, over_price, under_price} from FanDuel totals, or None."""
    for mk in _fanduel_markets(game):
        if mk.get("key") != "totals":
            continue
        over = under = line = None
        for o in mk.get("outcomes", []):
            if o.get("name") == "Over":
                over, line = o.get("price"), o.get("point")
            elif o.get("name") == "Under":
                under = o.get("price")
        if over is not None and under is not None and line is not None:
            return {"line": line, "over_price": over, "under_price": under}
    return None


def _historical_snapshot(when_iso):
    """Fetch the /historical h2h+totals snapshot at-or-before ``when_iso``."""
    url = (
        f"{BASE}/historical/sports/{SPORT_KEY}/odds/"
        f"?apiKey={_api_key()}&regions=us&markets=h2h,totals&oddsFormat=american"
        f"&bookmakers={BOOK}&date={when_iso}"
    )
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _snapshot_ts_iso(commence_iso, minutes_before=5):
    """ISO timestamp ``minutes_before`` minutes before first pitch."""
    dt = datetime.datetime.fromisoformat(commence_iso.replace("Z", "+00:00"))
    dt = dt - datetime.timedelta(minutes=minutes_before)
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Import here to avoid a cycle at module load.
def _abbr(name):
    from sources.mlb_schedule import team_abbr
    return team_abbr(name)


def historical_closing_odds(commence_iso, home_abbr, away_abbr):
    """FanDuel closing ML + total for one game -> row, or None.

    Snapshots at commence-5min; if the FanDuel h2h for the matchup is missing,
    walks back one snapshot (previous_timestamp) before giving up. Totals are
    included when present (None if FanDuel didn't post a total).
    """
    when = _snapshot_ts_iso(commence_iso, 5)
    for attempt in range(2):
        try:
            snap = _historical_snapshot(when)
        except Exception:
            return None
        for g in snap.get("data", []):
            if _abbr(g.get("home_team")) == home_abbr and _abbr(g.get("away_team")) == away_abbr:
                fd = _extract_fanduel_h2h(g)
                if fd:
                    tot = _extract_fanduel_totals(g) or {}
                    return {
                        "home_ml": fd["home_price"],
                        "away_ml": fd["away_price"],
                        "total_line": tot.get("line"),
                        "over_ml": tot.get("over_price"),
                        "under_ml": tot.get("under_price"),
                        "book": BOOK,
                        "source": "oddsapi_fanduel",
                        "snapshot_ts": snap.get("timestamp"),
                    }
        # FanDuel line not found in this snapshot — step back once.
        prev = snap.get("previous_timestamp")
        if not prev:
            break
        when = prev
    return None


# Back-compat alias.
historical_closing_ml = historical_closing_odds


def fetch_mlb_ml_live(date_key):
    """Live fallback: current FanDuel ML + totals for a date's games.

    Returns both-team rows keyed like the cache. Used only when the direct
    FanDuel API fails; costs Odds API credits.
    """
    url = (
        f"{BASE}/sports/{SPORT_KEY}/odds/"
        f"?apiKey={_api_key()}&regions=us&markets=h2h,totals&oddsFormat=american"
        f"&bookmakers={BOOK}"
    )
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    date_iso = f"{date_key[:4]}-{date_key[4:6]}-{date_key[6:8]}"
    rows = []
    for g in resp.json():
        commence = g.get("commence_time", "")
        if not commence.startswith(date_iso):
            continue
        fd = _extract_fanduel_h2h(g)
        if not fd:
            continue
        tot = _extract_fanduel_totals(g) or {}
        rows.append({
            "date": date_iso,
            "commence": commence,
            "home": _abbr(g.get("home_team")),
            "away": _abbr(g.get("away_team")),
            "home_ml": fd["home_price"],
            "away_ml": fd["away_price"],
            "total_line": tot.get("line"),
            "over_ml": tot.get("over_price"),
            "under_ml": tot.get("under_price"),
            "book": BOOK,
            "source": "oddsapi_fanduel",
            "started": False,
        })
    return rows
