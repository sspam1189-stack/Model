import requests
import re
from datetime import datetime
import pytz

NBA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
}


def current_season():
    now = datetime.now()
    year = now.year
    month = now.month
    start = year if month >= 10 else year - 1
    return f"{start}-{str(start + 1)[2:]}"


def today_espn():
    tz = pytz.timezone("America/Chicago")
    now = datetime.now(tz)
    return now.strftime("%Y%m%d")


# -- Player MPG from NBA.com --------------------------------------------------

def _fetch_mpg_leaguedash(season_type="Regular Season"):
    """Attempt 1: leaguedashplayerstats (full stats endpoint)"""
    season = current_season()
    params = {
        "College": "", "Conference": "", "Country": "",
        "DateFrom": "", "DateTo": "", "Division": "",
        "DraftPick": "", "DraftYear": "", "GameScope": "",
        "GameSegment": "", "Height": "", "ISTRound": "",
        "LastNGames": "0", "LeagueID": "00",
        "Location": "", "MeasureType": "Base",
        "Month": "0", "OpponentTeamID": "0",
        "Outcome": "", "PORound": "0",
        "PaceAdjust": "N", "PerMode": "PerGame",
        "Period": "0", "PlayerExperience": "",
        "PlayerPosition": "", "PlusMinus": "N",
        "Rank": "N", "Season": season,
        "SeasonSegment": "", "SeasonType": "Regular Season",
        "ShotClockRange": "", "StarterBench": "",
        "TeamID": "0", "TwoWay": "0",
        "VsConference": "", "VsDivision": "", "Weight": "",
    }

    from urllib.parse import urlencode
    url = f"https://stats.nba.com/stats/leaguedashplayerstats?{urlencode(params)}"
    print("  [injuries] Trying leaguedashplayerstats...")

    res = requests.get(url, headers=NBA_HEADERS)
    if res.status_code != 200:
        raise Exception(f"HTTP {res.status_code}")

    json_data = res.json()
    result_set = (json_data.get("resultSets") or [None])[0]
    if not result_set:
        raise Exception("no resultSet in response")

    headers = result_set.get("headers", [])
    rows = result_set.get("rowSet", [])

    if not headers or not rows:
        raise Exception(f"empty response ({len(headers)} headers, {len(rows)} rows)")

    i_name = headers.index("PLAYER_NAME") if "PLAYER_NAME" in headers else -1
    i_team = headers.index("TEAM_NAME") if "TEAM_NAME" in headers else (headers.index("TEAM_ABBREVIATION") if "TEAM_ABBREVIATION" in headers else -1)
    i_min = headers.index("MIN") if "MIN" in headers else -1
    i_gp = headers.index("GP") if "GP" in headers else -1

    if any(i == -1 for i in [i_name, i_team, i_min, i_gp]):
        print(f"  [injuries] leaguedash headers received: {', '.join(headers[:10])}...")
        matched = [h for h in ["PLAYER_NAME", "TEAM_NAME", "TEAM_ABBREVIATION", "MIN", "GP"] if h in headers]
        raise Exception(f"missing columns (got: {','.join(matched)})")

    use_abbrev = "TEAM_NAME" not in headers
    return {"headers": headers, "rows": rows, "iName": i_name, "iTeam": i_team, "iMIN": i_min, "iGP": i_gp, "useAbbrev": use_abbrev}


def _fetch_mpg_playerindex(season_type="Regular Season"):
    """Attempt 2: playerindex (alternative endpoint)"""
    season = current_season()
    from urllib.parse import urlencode
    params = {
        "College": "", "Country": "", "DraftPick": "", "DraftRound": "", "DraftYear": "",
        "Height": "", "Historical": "1", "LeagueID": "00", "Season": season,
        "SeasonType": "Regular Season", "TeamID": "0", "Weight": "",
    }

    url = f"https://stats.nba.com/stats/playerindex?{urlencode(params)}"
    print("  [injuries] Trying playerindex fallback...")

    res = requests.get(url, headers=NBA_HEADERS)
    if res.status_code != 200:
        raise Exception(f"HTTP {res.status_code}")

    json_data = res.json()
    result_set = (json_data.get("resultSets") or [None])[0]
    if not result_set:
        raise Exception("no resultSet in response")

    headers = result_set.get("headers", [])
    rows = result_set.get("rowSet", [])

    if not headers or not rows:
        raise Exception(f"empty response ({len(headers)} headers, {len(rows)} rows)")

    i_first = headers.index("PLAYER_FIRST_NAME") if "PLAYER_FIRST_NAME" in headers else -1
    i_last = headers.index("PLAYER_LAST_NAME") if "PLAYER_LAST_NAME" in headers else -1
    i_team = headers.index("TEAM_NAME") if "TEAM_NAME" in headers else -1

    if any(i == -1 for i in [i_first, i_last, i_team]):
        print(f"  [injuries] playerindex headers: {', '.join(headers[:10])}...")
        raise Exception("playerindex missing expected columns")

    return {"headers": headers, "rows": rows, "iFirst": i_first, "iLast": i_last, "iTeam": i_team, "isIndex": True}


def _fetch_mpg_espn(espn_type=2):
    """Attempt 3: ESPN roster minutes (completely different source)"""
    print("  [injuries] Trying ESPN stats fallback...")

    urls = [
        "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/statistics/byathlete?region=us&lang=en&contentorigin=espn&is498=true&type=team&limit=500&sort=general.mpg:desc",
        "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/seasons/2026/types/2/leaders?limit=500",
        "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/leaders?limit=500",
    ]

    for url in urls:
        try:
            res = requests.get(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            if res.status_code != 200:
                continue

            json_data = res.json()
            mpg_map = {}

            # Try different response shapes ESPN might use
            categories = json_data.get("leaders", {}).get("categories") or json_data.get("categories", [])
            min_cat = next(
                (c for c in categories
                 if c.get("name") in ("minutesPG", "minutes")
                 or c.get("abbreviation") == "MIN"
                 or c.get("displayName") == "Minutes Per Game"),
                None
            )

            if min_cat and min_cat.get("leaders"):
                for entry in min_cat["leaders"]:
                    athlete = entry.get("athlete") or {}
                    name = athlete.get("displayName")
                    team = (athlete.get("team") or {}).get("displayName")
                    mpg = float(entry.get("displayValue") or entry.get("value") or "0")
                    if name and team and mpg > 0:
                        mpg_map[name] = {"team": team, "mpg": mpg, "gp": 50}

            # Also try flat athlete stats format
            if not mpg_map and json_data.get("athletes"):
                for ath in json_data["athletes"]:
                    athlete = ath.get("athlete") or {}
                    name = athlete.get("displayName")
                    team = (athlete.get("team") or {}).get("displayName")
                    stats = ath.get("stats") or (ath.get("categories", [{}])[0].get("stats") if ath.get("categories") else [])
                    mpg_stat = next(
                        (s for s in (stats or []) if s.get("name") == "mpg" or s.get("abbreviation") == "MIN"),
                        None
                    )
                    mpg = float((mpg_stat or {}).get("displayValue") or (mpg_stat or {}).get("value") or "0")
                    if name and team and mpg > 0:
                        mpg_map[name] = {"team": team, "mpg": mpg, "gp": 50}

            if len(mpg_map) >= 50:
                return mpg_map
        except Exception:
            continue

    raise Exception("all ESPN endpoints failed or returned insufficient data")


def fetch_player_mpg(season_type="Regular Season", espn_type=2):
    """Try NBA.com leaguedash first (best data), then playerindex, then ESPN"""
    mpg_map = {}

    ABBREV_TO_NAME = {
        "ATL": "Atlanta Hawks", "BOS": "Boston Celtics", "BKN": "Brooklyn Nets",
        "CHA": "Charlotte Hornets", "CHI": "Chicago Bulls", "CLE": "Cleveland Cavaliers",
        "DAL": "Dallas Mavericks", "DEN": "Denver Nuggets", "DET": "Detroit Pistons",
        "GSW": "Golden State Warriors", "HOU": "Houston Rockets", "IND": "Indiana Pacers",
        "LAC": "LA Clippers", "LAL": "Los Angeles Lakers", "MEM": "Memphis Grizzlies",
        "MIA": "Miami Heat", "MIL": "Milwaukee Bucks", "MIN": "Minnesota Timberwolves",
        "NOP": "New Orleans Pelicans", "NYK": "New York Knicks", "OKC": "Oklahoma City Thunder",
        "ORL": "Orlando Magic", "PHI": "Philadelphia 76ers", "PHX": "Phoenix Suns",
        "POR": "Portland Trail Blazers", "SAC": "Sacramento Kings", "SAS": "San Antonio Spurs",
        "TOR": "Toronto Raptors", "UTA": "Utah Jazz", "WAS": "Washington Wizards",
    }

    # Attempt 1: leaguedashplayerstats
    try:
        data = _fetch_mpg_leaguedash(season_type)
        for row in data["rows"]:
            name = row[data["iName"]]
            raw_tm = row[data["iTeam"]]
            team = ABBREV_TO_NAME.get(raw_tm, raw_tm) if data["useAbbrev"] else raw_tm
            mpg = float(row[data["iMIN"]])
            gp = int(row[data["iGP"]])
            if not name or gp < 5:
                continue
            mpg_map[name] = {"team": team, "mpg": mpg, "gp": gp}
        if len(mpg_map) > 100:
            suffix = ", abbrev->name" if data["useAbbrev"] else ""
            print(f"  [injuries] Got MPG for {len(mpg_map)} players (leaguedash{suffix})")
            return mpg_map
        print(f"  [injuries] leaguedash returned only {len(mpg_map)} players, trying fallback...")
    except Exception as e:
        print(f"  [injuries] leaguedash failed: {e}")

    # Attempt 2: ESPN leaders
    try:
        mpg_map = _fetch_mpg_espn(espn_type)
        print(f"  [injuries] Got MPG for {len(mpg_map)} players (ESPN fallback)")
        return mpg_map
    except Exception as e:
        print(f"  [injuries] ESPN fallback failed: {e}")

    # All failed
    print("  [injuries] All MPG sources failed -- injury tiers will default to bench")
    return {}


# -- ESPN per-game availability ------------------------------------------------

def _fetch_game_availability(date=None):
    """
    Returns a map of teamDisplayName -> [{ player, status, reason }]
    Only includes players listed as Out, Doubtful, or Questionable for THIS specific game.
    """
    day = date or today_espn()

    # Step 1: get event IDs
    events = []
    try:
        hdr_url = f"https://site.web.api.espn.com/apis/v2/scoreboard/header?sport=basketball&league=nba&dates={day}"
        hdr_res = requests.get(hdr_url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        if hdr_res.status_code == 200:
            hdr = hdr_res.json()
            leagues = ((hdr.get("sports") or [{}])[0].get("leagues") or [{}])[0]
            events = [{"id": e.get("id")} for e in leagues.get("events", [])]
    except Exception:
        pass

    if not events:
        sb_url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={day}"
        sb_res = requests.get(sb_url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        if sb_res.status_code != 200:
            raise Exception(f"ESPN scoreboard failed: {sb_res.status_code}")
        sb = sb_res.json()
        events = sb.get("events", [])

    if not events:
        print("  [injuries] No games today on ESPN scoreboard")
        return {}

    print(f"  [injuries] Fetching game-specific availability for {len(events)} games...")

    availability = {}

    for ev in events:
        event_id = ev.get("id") if isinstance(ev, dict) else None
        if not event_id:
            continue

        sum_url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={event_id}"
        try:
            sum_res = requests.get(sum_url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        except Exception:
            continue
        if sum_res.status_code != 200:
            continue

        try:
            summary = sum_res.json()
        except Exception:
            continue

        inj_entries = summary.get("injuries", [])
        for team_entry in inj_entries:
            team_name = (team_entry.get("team") or {}).get("displayName")
            if not team_name:
                continue

            players = []
            for inj in team_entry.get("injuries", []):
                player_name = (inj.get("athlete") or {}).get("displayName")
                if not player_name:
                    continue

                raw_status = inj.get("status", "")
                status = _normalize_status(raw_status)
                if status in ("active", "probable"):
                    continue

                reason = inj.get("shortComment") or inj.get("longComment") or raw_status or ""
                players.append({"player": player_name, "status": status, "reason": reason})

            if players:
                availability[team_name] = players

    total = sum(len(v) for v in availability.values())
    print(f"  [injuries] {total} players listed out/limited for today's games")
    return availability


# -- Helpers -------------------------------------------------------------------

def _normalize_status(raw):
    s = str(raw or "").lower()
    if "out" in s:
        return "out"
    if "doubtful" in s:
        return "doubtful"
    if "questionable" in s or "day-to-day" in s or "dtd" in s:
        return "questionable"
    if "probable" in s:
        return "probable"
    return "active"


def _classify_tier(mpg):
    if mpg >= 28:
        return "star"
    if mpg >= 18:
        return "starter"
    return "bench"


# -- Main export ---------------------------------------------------------------

def fetch_injury_data(date=None, season_type="Regular Season", espn_type=2):
    """
    report -> { "Boston Celtics": [{ player, status, tier, mpg, reason }] }
    """
    try:
        game_availability = _fetch_game_availability(date)
    except Exception as e:
        print(f"  [injuries] Game availability fetch failed: {e}")
        game_availability = {}

    try:
        player_mpg = fetch_player_mpg(season_type=season_type, espn_type=espn_type)
    except Exception as e:
        print(f"  [injuries] MPG fetch failed: {e}")
        player_mpg = {}

    report = {}

    for team_name, players in game_availability.items():
        enriched = []
        for p in players:
            # Look up MPG -- exact match first, then same-team last-name fallback
            mpg_entry = player_mpg.get(p["player"])
            if not mpg_entry:
                last_name = p["player"].split(" ")[-1].lower()
                found = next(
                    (k for k in player_mpg
                     if k.split(" ")[-1].lower() == last_name
                     and player_mpg[k]["team"] == team_name),
                    None
                )
                if found:
                    mpg_entry = player_mpg[found]

            mpg = (mpg_entry or {}).get("mpg", 0)
            tier = _classify_tier(mpg)
            enriched.append({**p, "tier": tier, "mpg": mpg})

        report[team_name] = enriched

    return {"report": report, "playerMPG": player_mpg}


# -- Convenience exports -------------------------------------------------------

TEAM_ALIASES_INJ = {
    "Los Angeles Clippers": "LA Clippers",
    "LA Clippers": "Los Angeles Clippers",
}


def get_key_injuries(report, team_name, player_mpg=None):
    """Returns only Out/Doubtful players who are Star or Starter tier"""
    entries = report.get(team_name) or report.get(TEAM_ALIASES_INJ.get(team_name, ""), [])
    result = []
    for p in entries:
        if p["status"] not in ("out", "doubtful"):
            continue
        if p["tier"] not in ("star", "starter"):
            continue
        if player_mpg:
            mpg_entry = player_mpg.get(p["player"])
            if not mpg_entry or mpg_entry.get("gp", 0) < 10:
                continue
        result.append(p)
    return result


def game_uncertainty_score(away_injuries, home_injuries):
    """Returns 0 (no concern) -> 5+ (skip the game)"""
    score = 0
    for inj in list(away_injuries) + list(home_injuries):
        if inj.get("tier") == "star" and inj.get("status") == "out":
            score += 1
        if inj.get("tier") == "star" and inj.get("status") == "doubtful":
            score += 1
    return min(score, 5)
