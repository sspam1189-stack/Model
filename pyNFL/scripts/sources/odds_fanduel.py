# pyNFL/scripts/sources/odds_fanduel.py
# Fetch NFL game spreads + totals from FanDuel's public sportsbook API.
# No API key needed — uses the same public endpoints as the FD website.
# Used as primary live odds source (saves Odds API credits).
# Adapted from pyFull/scripts/sources/odds_fanduel.py for NFL.
#
# Live only — FanDuel publishes no historical snapshots, so backfill and
# closing-line grading stay on odds_theoddsapi.fetch_historical_odds.

import datetime
import time

import requests
from zoneinfo import ZoneInfo

FD_BASE = "https://sbapi.mi.sportsbook.fanduel.com/api"
FD_API_KEY = "FhMFpcPWXMeyZxOx"  # Public key embedded in FD website

FD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


# Player props live behind per-tab event requests, the same way MLB fetches
# them. Market names carry a _HIGH/_MEDIUM/_LOW suffix that is FanDuel's own
# tiering of which players sit in which block — all three are the same market.
FD_PROP_TABS = ("passing-props", "rushing-props", "receiving-props")
FD_PROP_MARKETS = {
    "PLAYER_X_PASSING_YARDS": "pass_yds",
    "PLAYER_X_PASSING_TOUCHDOWNS": "pass_tds",
    "PLAYER_X_RUSHING_YARDS": "rush_yds",
    "PLAYER_X_RECEIVING_YARDS": "rec_yds",
    "PLAYER_X_RECEPTIONS": "receptions",
}


def _norm_team(name):
    return str(name or "").strip()


def _american(runner):
    """American price off a FanDuel runner, or None. The payload shape is not
    contractual, so a change here must degrade to "no price" rather than take
    the fetch down."""
    try:
        wro = runner.get("winRunnerOdds") or {}
        for key in ("americanDisplayOdds", "americanOdds"):
            node = wro.get(key)
            if isinstance(node, dict):
                for f in ("americanOddsInt", "americanOdds"):
                    v = node.get(f)
                    if isinstance(v, (int, float)):
                        return int(v)
                    if isinstance(v, str) and v.strip().lstrip("+-").isdigit():
                        return int(v.replace("+", ""))
            elif isinstance(node, (int, float)):
                return int(node)
    except Exception:
        pass
    return None


def _prop_market(market_type):
    """Strip FanDuel's tier suffix and map to our internal market name."""
    mt = str(market_type or "")
    for prefix, internal in FD_PROP_MARKETS.items():
        if mt.startswith(prefix):
            return internal
    return None


def _same_team(runner_name, team):
    """FanDuel runner names are not always the full team name, so match the
    way the rest of the pipeline does: loose substring either direction."""
    a = _norm_team(runner_name).lower()
    b = _norm_team(team).lower()
    return bool(a) and bool(b) and (a in b or b in a)


def fetch_fanduel_nfl_odds():
    """
    Fetch upcoming NFL spreads + totals from FanDuel.

    Returns a list of dicts in the same shape odds_theoddsapi.fetch_nfl_odds
    returns, so callers are interchangeable:
        [{away, home, line, total, commenceTimeIso, _book}]

    Line convention: negative = home favored.

    Every upcoming game the feed carries is returned — NFL weeks span Thu
    through Mon, and run_weekly.filter_odds_to_week does the week windowing.
    Games already under way are dropped, matching the Odds API path.
    """
    url = f"{FD_BASE}/content-managed-page?page=CUSTOM&customPageId=nfl&_ak={FD_API_KEY}"
    try:
        r = requests.get(url, headers=FD_HEADERS, timeout=30)
        if r.status_code != 200:
            print(f"  [fanduel] API returned {r.status_code}")
            return []
        data = r.json()
    except Exception as e:
        print(f"  [fanduel] Fetch error: {e}")
        return []

    attachments = data.get("attachments", {})
    events = attachments.get("events", {})
    markets = attachments.get("markets", {})

    now = datetime.datetime.now(datetime.timezone.utc)
    games = []

    for eid, ev in events.items():
        # Real matchups are "Away @ Home"; futures/specials/awards are not.
        name = ev.get("name", "")
        if " @ " not in name:
            continue
        away, home = (_norm_team(p) for p in name.split(" @ ", 1))
        if not home or not away:
            continue

        open_date = ev.get("openDate", "")
        commence = None
        if open_date:
            try:
                commence = datetime.datetime.fromisoformat(
                    open_date.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                commence = None
        if commence and commence <= now:
            continue

        line = None
        total = None
        for m in markets.values():
            if str(m.get("eventId")) != str(eid):
                continue
            mt = m.get("marketType", "")

            if "HANDICAP" in mt and "2-WAY" in mt:
                # The home runner's handicap IS the model line (negative =
                # home favored), so read it directly rather than deriving it
                # from whichever side is favored — that keeps pick'em games,
                # where neither handicap is negative, from going MISSING_ODDS.
                for runner in m.get("runners", []):
                    h = runner.get("handicap")
                    if h is None:
                        continue
                    rn = runner.get("runnerName", "")
                    if _same_team(rn, home):
                        line = float(h)
                        break
                    if _same_team(rn, away):
                        line = -float(h)

            elif "TOTAL_POINTS" in mt:
                for runner in m.get("runners", []):
                    if runner.get("runnerName") == "Over":
                        h = runner.get("handicap")
                        if h is not None:
                            total = float(h)
                        break

        games.append({
            "away": away,
            "home": home,
            "line": line,
            "total": total,
            "commenceTimeIso": open_date or None,
            "_book": "FanDuel",
        })

    print(f"  [fanduel] Fetched {len(games)} upcoming NFL games with spreads/totals")
    return games


def fetch_fanduel_nfl_player_props(days_ahead=8, sleep_s=0.15):
    """
    Fetch player prop lines from FanDuel for every upcoming NFL game inside
    *days_ahead*.

    Returns the same shape odds_theoddsapi.fetch_nfl_player_props returns, so
    the two are interchangeable:
        [{player, market, line, over_price, under_price,
          event_home, event_away, commenceTimeIso}]

    Markets covered: pass_yds, pass_tds, rush_yds, rec_yds, receptions.
    FanDuel does not post attempts or completions, so pass_att / rush_att /
    completions come back empty here and stay Odds-API-only.

    One request per event per tab, so ~3x the games — free, but not instant.
    """
    url = f"{FD_BASE}/content-managed-page?page=CUSTOM&customPageId=nfl&_ak={FD_API_KEY}"
    try:
        r = requests.get(url, headers=FD_HEADERS, timeout=30)
        if r.status_code != 200:
            print(f"  [fanduel-props] event list returned {r.status_code}")
            return []
        events = r.json().get("attachments", {}).get("events", {})
    except Exception as e:
        print(f"  [fanduel-props] event list failed: {e}")
        return []

    now = datetime.datetime.now(datetime.timezone.utc)
    horizon = now + datetime.timedelta(days=days_ahead)
    wanted = []
    for eid, ev in events.items():
        name = ev.get("name", "")
        if " @ " not in name:
            continue
        try:
            when = datetime.datetime.fromisoformat(
                (ev.get("openDate") or "").replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        if now < when <= horizon:
            away, home = (_norm_team(p) for p in name.split(" @ ", 1))
            wanted.append((eid, away, home, ev.get("openDate")))

    out = []
    for eid, away, home, open_date in wanted:
        for tab in FD_PROP_TABS:
            try:
                rr = requests.get(
                    f"{FD_BASE}/event-page?eventId={eid}&tab={tab}&_ak={FD_API_KEY}",
                    headers=FD_HEADERS, timeout=30)
                if rr.status_code != 200:
                    continue
                markets = rr.json().get("attachments", {}).get("markets", {})
            except Exception:
                continue
            for m in markets.values():
                internal = _prop_market(m.get("marketType"))
                if not internal:
                    continue
                # "Brock Purdy - Passing Yds" -> "Brock Purdy"
                player = str(m.get("marketName") or "").split(" - ")[0].strip()
                if not player:
                    continue
                line = over = under = None
                for runner in m.get("runners", []):
                    rn = str(runner.get("runnerName") or "")
                    h = runner.get("handicap")
                    if h is not None and line is None:
                        line = float(h)
                    if " Over" in rn or rn.endswith("Over"):
                        over = _american(runner)
                    elif " Under" in rn or rn.endswith("Under"):
                        under = _american(runner)
                if line is None:
                    continue
                out.append({
                    "player": player, "market": internal, "line": line,
                    "over_price": over, "under_price": under,
                    "event_home": home, "event_away": away,
                    "commenceTimeIso": open_date,
                })
            if sleep_s:
                time.sleep(sleep_s)

    print(f"  [fanduel-props] {len(out)} prop lines across {len(wanted)} games")
    return out
