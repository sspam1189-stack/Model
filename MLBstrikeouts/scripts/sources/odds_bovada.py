# MLBstrikeouts/scripts/sources/odds_bovada.py
# Fetch MLB batter total bases prop lines from Bovada's public API.
#
# Free, no API key needed. Bovada exposes player props including total bases
# under the "Player Props" displayGroup of each MLB game. This is used as a
# secondary source for batter TB lines after FanDuel (which doesn't expose TB).

import re
import json
import datetime
import requests
from pathlib import Path

_PROPS_CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "props_cache" / "mlb"

BOVADA_BASE = "https://www.bovada.lv/services/sports/event/v2/events/A/description/baseball/mlb"
BOVADA_LIST_URL = (
    "https://www.bovada.lv/services/sports/event/coupon/events/A/description/"
    "baseball/mlb?marketFilterId=def&preMatchOnly=true&lang=en"
)

BOVADA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.bovada.lv/sports/baseball/mlb",
}

# Map team abbreviations / city names from Bovada notation to internal abbreviations
BOVADA_TEAM_TO_ABBR = {
    "ARI": "ARI", "ATL": "ATL", "BAL": "BAL", "BOS": "BOS",
    "CHC": "CHC", "CWS": "CWS", "CIN": "CIN", "CLE": "CLE",
    "COL": "COL", "DET": "DET", "HOU": "HOU", "KC": "KC",
    "LAA": "LAA", "LAD": "LAD", "MIA": "MIA", "MIL": "MIL",
    "MIN": "MIN", "NYM": "NYM", "NYY": "NYY", "OAK": "OAK",
    "PHI": "PHI", "PIT": "PIT", "SD": "SD", "SF": "SF",
    "SEA": "SEA", "STL": "STL", "TB": "TB", "TEX": "TEX",
    "TOR": "TOR", "WAS": "WAS", "WSH": "WAS",
    # Bovada sometimes uses different forms
    "KAN": "KC", "TBR": "TB", "SDP": "SD", "SFG": "SF",
    "WSN": "WAS", "CHW": "CWS",
}


def _batter_props_cache_path(date_key):
    """Cache path for Bovada batter props (date_key in YYYYMMDD format)."""
    _PROPS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _PROPS_CACHE_DIR / f"mlb_bovada_batter_props_{date_key}.json"


def _parse_player_team(description):
    """
    Parse 'Total Bases - Brady House (WAS)' into (player, team).
    Returns (player_name, team_abbr) or (None, None) if not parseable.
    """
    if not description:
        return None, None
    # Strip the "Total Bases - " prefix
    prefix_re = re.match(r"^Total Bases\s*-\s*(.+)$", description, re.IGNORECASE)
    if not prefix_re:
        return None, None
    rest = prefix_re.group(1).strip()
    # Extract trailing (TEAM) abbreviation
    team_re = re.search(r"\(([A-Z]{2,3})\)\s*$", rest)
    if team_re:
        team = BOVADA_TEAM_TO_ABBR.get(team_re.group(1).upper(), team_re.group(1).upper())
        player = rest[:team_re.start()].strip()
    else:
        team = ""
        player = rest
    return player, team


def fetch_bovada_mlb_batter_props(date_str=None):
    """
    Fetch MLB batter total bases prop lines from Bovada.

    Parameters
    ----------
    date_str : str or None
        Date in YYYY-MM-DD or YYYYMMDD format. Used for cache filename only;
        Bovada returns all upcoming games regardless of date filter.

    Returns
    -------
    list[dict]
        [{"player": str, "player_id": None, "team": str, "market": "total_bases",
          "line": float, "over_price": int, "under_price": int}, ...]
    """
    if date_str is None:
        date_key = datetime.datetime.now().strftime("%Y%m%d")
    else:
        date_key = date_str.replace("-", "")

    cache_path = _batter_props_cache_path(date_key)

    # Step 1: Get list of MLB events
    try:
        res = requests.get(BOVADA_LIST_URL, headers=BOVADA_HEADERS, timeout=15)
        if res.status_code != 200:
            print(f"  [bovada] List fetch failed: {res.status_code}")
            return []
        list_data = res.json()
    except Exception as e:
        print(f"  [bovada] List fetch error: {e}")
        return []

    if not isinstance(list_data, list) or not list_data:
        print(f"  [bovada] Unexpected list response shape")
        return []

    events = list_data[0].get("events", [])
    print(f"  [bovada] Found {len(events)} MLB games")

    if not events:
        return []

    # Step 2: For each game, fetch the full event detail with all displayGroups
    all_lines = []
    for ev in events:
        link = ev.get("link", "")
        if not link or "/baseball/mlb/" not in link:
            continue
        # Extract game slug from link (e.g. /baseball/mlb/...-202604161235)
        slug = link.split("/baseball/mlb/")[-1].strip("/")
        if not slug:
            continue

        detail_url = f"{BOVADA_BASE}/{slug}?lang=en"
        try:
            r = requests.get(detail_url, headers=BOVADA_HEADERS, timeout=10)
            if r.status_code != 200:
                continue
            detail = r.json()
        except Exception:
            continue

        if not isinstance(detail, list) or not detail:
            continue

        det_events = detail[0].get("events", [])
        if not det_events:
            continue
        det_ev = det_events[0]

        # Find Player Props display group
        for grp in det_ev.get("displayGroups", []):
            if grp.get("description") != "Player Props":
                continue

            for market in grp.get("markets", []):
                desc = market.get("description", "")
                if "Total Bases" not in desc:
                    continue
                # Skip if not the Game period
                period = market.get("period", {})
                if period and period.get("description") and \
                   period.get("description") != "Game":
                    continue
                if market.get("status") != "O":
                    continue

                player, team = _parse_player_team(desc)
                if not player:
                    continue

                outcomes = market.get("outcomes", [])
                if len(outcomes) < 2:
                    continue

                over = next((o for o in outcomes if o.get("type") == "O"), None)
                under = next((o for o in outcomes if o.get("type") == "U"), None)
                if not over or not under:
                    continue

                over_price = over.get("price", {})
                under_price = under.get("price", {})

                line_str = over_price.get("handicap") or under_price.get("handicap")
                if line_str is None:
                    continue
                try:
                    line = float(line_str)
                except (ValueError, TypeError):
                    continue

                try:
                    over_american = int(over_price.get("american", "0").replace("+", ""))
                except (ValueError, TypeError):
                    over_american = None
                try:
                    under_american = int(under_price.get("american", "0").replace("+", ""))
                except (ValueError, TypeError):
                    under_american = None

                all_lines.append({
                    "player": player,
                    "player_id": None,
                    "team": team,
                    "market": "total_bases",
                    "line": line,
                    "over_price": over_american,
                    "under_price": under_american,
                    "source": "bovada",
                })

    print(f"  [bovada] Fetched {len(all_lines)} batter total bases lines")

    # Save to cache for inspection / debugging
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(all_lines, f, indent=2)
    except Exception:
        pass

    return all_lines
