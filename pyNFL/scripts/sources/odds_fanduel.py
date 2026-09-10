# pyNFL/scripts/sources/odds_fanduel.py
# Fetch NFL game spreads + totals from FanDuel's public sportsbook API.
# No API key needed — uses the same public endpoints as the FD website.
# Used as primary live odds source (saves Odds API credits).
# Adapted from pyFull/scripts/sources/odds_fanduel.py for NFL.
#
# Live only — FanDuel publishes no historical snapshots, so backfill and
# closing-line grading stay on odds_theoddsapi.fetch_historical_odds.

import datetime
import requests
from zoneinfo import ZoneInfo

FD_BASE = "https://sbapi.mi.sportsbook.fanduel.com/api"
FD_API_KEY = "FhMFpcPWXMeyZxOx"  # Public key embedded in FD website

FD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


def _norm_team(name):
    return str(name or "").strip()


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
