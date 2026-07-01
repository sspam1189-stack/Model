# pyWNBAPROPS/scripts/sources/odds_theoddsapi_historical.py
# Fetch REAL historical WNBA player-prop CLOSING lines from The Odds API.
#
# The live/props path (odds_theoddsapi.py, odds_fanduel.py) has no historical
# data. For the season backfill we need each completed game's closing line, so
# this module hits The Odds API *historical* endpoints:
#
#   1. events for a date:
#      GET /v4/historical/sports/basketball_wnba/events?apiKey=KEY&date=<ISO>
#      -> {timestamp, data:[{id, commence_time, home_team, away_team}, ...]}
#   2. per-event odds at a timestamp just before tip (the closing snapshot):
#      GET /v4/historical/sports/basketball_wnba/events/{id}/odds
#          ?apiKey=KEY&regions=us&markets=<props>&oddsFormat=american&date=<ISO>
#      -> bookmakers[].markets[].outcomes[] = {name:Over/Under, description:player,
#                                              point:line, price:american}
#
# Credit budget (confirmed): events-list ~1/call, per-event odds = 10 x markets
# = 50 credits/event for the 5 prop markets. ~144 completed 2026 games -> ~7.3k
# credits, well within the 100k quota.
#
# The key is read ONLY from env (ODDS_HIST_KEY or ODDS_API_KEY) — never
# hard-coded, never committed. Raw JSON responses AND parsed per-date prop
# caches are written under data/props_cache/wnba/ so re-runs never re-bill.

import os
import json
import time
import datetime
import requests
from pathlib import Path
from urllib.parse import quote

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from defaults import PROP_MARKETS_API, MARKET_MAP
# Reuse the WNBA team-name map + parsed-cache path from the live module so the
# parsed output is byte-compatible with what props_backfill already reads.
from sources.odds_theoddsapi import _TEAM_ABBR, _props_cache_path, _save_cache, _load_cache

BASE = "https://api.the-odds-api.com/v4"
SPORT_KEY = "basketball_wnba"

# Raw-response cache (separate from the parsed per-date props cache) so an
# interrupted run resumes without re-billing already-fetched events.
_RAW_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "props_cache" / "wnba" / "hist_raw"


def _hist_key():
    """Historical API key — env only, never committed."""
    for var in ("ODDS_HIST_KEY", "ODDS_API_KEY"):
        v = os.environ.get(var, "").strip()
        if v:
            return v
    return None


def _raw_path(name):
    return _RAW_CACHE_DIR / name


def _load_raw(name):
    p = _raw_path(name)
    if p.exists():
        try:
            with open(p, "r") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def _save_raw(name, data):
    _RAW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(_raw_path(name), "w") as f:
        json.dump(data, f)


def _get(url, timeout=30, max_retries=4):
    """GET with basic 429/5xx backoff. Returns (json_or_None, last_cost)."""
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.get(url, timeout=timeout)
        except Exception as e:
            if attempt == max_retries:
                print(f"  [hist] request error: {e}")
                return None, 0
            time.sleep(min(1.0 * 2 ** (attempt - 1), 8))
            continue
        cost = int(r.headers.get("x-requests-last", 0) or 0)
        if r.status_code == 200:
            try:
                return r.json(), cost
            except Exception:
                return None, cost
        if r.status_code in (429,) or r.status_code >= 500:
            time.sleep(min(1.0 * 2 ** (attempt - 1), 8))
            continue
        # 401/422/etc — no point retrying
        print(f"  [hist] HTTP {r.status_code}: {r.text[:160]}")
        return None, cost
    return None, 0


def _pick_book(bookmakers, preferred=("fanduel", "draftkings", "williamhill_us", "betrivers")):
    for p in preferred:
        b = next((x for x in bookmakers if x.get("key") == p), None)
        if b:
            return b
    return bookmakers[0] if bookmakers else None


def _snapshot_iso(commence_iso, minutes_before=8):
    """ISO timestamp `minutes_before` minutes before commence (closing line)."""
    dt = datetime.datetime.fromisoformat(commence_iso.replace("Z", "+00:00"))
    dt = dt - datetime.timedelta(minutes=minutes_before)
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_historical_events(date_str, key):
    """List events for a date (YYYY-MM-DD). Cached raw. Returns event list."""
    raw_name = f"events_{date_str.replace('-', '')}.json"
    cached = _load_raw(raw_name)
    if cached is not None:
        return cached.get("data", []), 0
    # noon UTC snapshot reliably contains that day's slate in its "next events"
    ts = f"{date_str}T12:00:00Z"
    url = f"{BASE}/historical/sports/{SPORT_KEY}/events?apiKey={quote(key)}&date={quote(ts)}"
    j, cost = _get(url)
    if j is None:
        return [], cost
    _save_raw(raw_name, j)
    return j.get("data", []), cost


def fetch_event_props(event, date_str, key):
    """Fetch one event's closing prop snapshot. Cached raw. Returns parsed lines."""
    eid = event.get("id")
    commence = event.get("commence_time", "")
    home = _TEAM_ABBR.get(event.get("home_team", ""), event.get("home_team", ""))
    away = _TEAM_ABBR.get(event.get("away_team", ""), event.get("away_team", ""))

    raw_name = f"odds_{eid}.json"
    j = _load_raw(raw_name)
    cost = 0
    if j is None:
        if not commence:
            return [], 0
        snap = _snapshot_iso(commence, minutes_before=8)
        markets = ",".join(PROP_MARKETS_API)
        url = (f"{BASE}/historical/sports/{SPORT_KEY}/events/{eid}/odds?"
               f"apiKey={quote(key)}&regions=us&markets={markets}"
               f"&oddsFormat=american&date={quote(snap)}")
        j, cost = _get(url)
        if j is None:
            return [], cost
        _save_raw(raw_name, j)

    data = j.get("data", {}) if isinstance(j, dict) else {}
    book = _pick_book(data.get("bookmakers", []))
    if not book:
        return [], cost

    lines = []
    for market in book.get("markets", []):
        internal = MARKET_MAP.get(market.get("key", ""))
        if not internal:
            continue
        by_player = {}
        for o in market.get("outcomes", []):
            desc = o.get("description", "")
            name = o.get("name", "")
            point = o.get("point")
            price = o.get("price")
            if not desc or point is None:
                continue
            pl = by_player.setdefault(desc, {"player": desc, "line": point})
            if name == "Over":
                pl["over_price"] = price
            elif name == "Under":
                pl["under_price"] = price
        for pl in by_player.values():
            lines.append({**pl, "market": internal,
                          "event_home": home, "event_away": away})
    return lines, cost


def backfill_date(date_str, key=None, sleep=0.25):
    """Fetch + cache real closing prop lines for all events on a date.

    Writes the parsed lines to the standard per-date props cache
    (wnba_props_YYYYMMDD.json) so props_backfill reads them exactly like the
    live path. Returns (lines, credits_spent, n_events).
    """
    key = key or _hist_key()
    if not key:
        print("  [hist] No API key in ODDS_HIST_KEY / ODDS_API_KEY — cannot backfill")
        return [], 0, 0

    date_key = date_str.replace("-", "")
    parsed_cp = _props_cache_path(date_key)
    parsed_cached = _load_cache(parsed_cp)
    # If we already parsed this date (and it's non-empty), reuse — no billing.
    if parsed_cached:
        return parsed_cached, 0, None

    credits = 0
    events, c = fetch_historical_events(date_str, key)
    credits += c
    all_lines = []
    for ev in events:
        lines, c2 = fetch_event_props(ev, date_str, key)
        credits += c2
        all_lines.extend(lines)
        if c2:  # only sleep when we actually hit the API (not on cache hits)
            time.sleep(sleep)

    if all_lines:
        _save_cache(all_lines, parsed_cp)
    return all_lines, credits, len(events)
