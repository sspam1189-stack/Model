# scripts/sources/injuries.py
# Fetches per-game player availability from ESPN's game summary endpoint.
# This catches ALL reasons a player is out -- injury, rest, personal, DTD, etc.
#
# Exports:
#   fetch_injury_data()  -> { "report": ..., "playerMPG": ... }
#   get_key_injuries(report, team_name) -> [{ player, status, tier, mpg }]
#   game_uncertainty_score(away_inj, home_inj) -> number 0-4

import requests
import datetime
import re
from zoneinfo import ZoneInfo

NBA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
}


def _current_season():
    now = datetime.datetime.now()
    year = now.year
    month = now.month
    start = year if month >= 10 else year - 1
    return f"{start}-{str(start + 1)[2:]}"


def _today_espn():
    now = datetime.datetime.now(ZoneInfo("America/Chicago"))
    return now.strftime("%Y%m%d")


# -- Player MPG from NBA.com --

def _fetch_mpg_leaguedash(season_type="Regular Season"):
    """Fetch player MPG via nba_api (replaces raw stats.nba.com calls)."""
    from nba_api.stats.endpoints import leaguedashplayerstats

    season = _current_season()
    print("  [injuries] Fetching leaguedashplayerstats via nba_api...")

    try:
        endpoint = leaguedashplayerstats.LeagueDashPlayerStats(
            season=season,
            season_type_all_star=season_type,
            measure_type_detailed_defense="Base",
            per_mode_detailed="PerGame",
            timeout=120,
        )
        df = endpoint.get_data_frames()[0]
    except Exception as e:
        raise Exception(f"nba_api leaguedashplayerstats failed: {e}")

    if df.empty:
        raise Exception("nba_api: empty response")

    # Convert DataFrame back to raw format for backward compat
    headers = list(df.columns)
    rows = df.values.tolist()

    i_name = headers.index("PLAYER_NAME") if "PLAYER_NAME" in headers else -1
    i_team = headers.index("TEAM_NAME") if "TEAM_NAME" in headers else (headers.index("TEAM_ABBREVIATION") if "TEAM_ABBREVIATION" in headers else -1)
    i_min = headers.index("MIN") if "MIN" in headers else -1
    i_gp = headers.index("GP") if "GP" in headers else -1

    if -1 in [i_name, i_team, i_min, i_gp]:
        found = [h for h in headers if h in ["PLAYER_NAME", "TEAM_NAME", "TEAM_ABBREVIATION", "MIN", "GP"]]
        print(f"  [injuries] leaguedash headers received: {', '.join(headers[:10])}...")
        raise Exception(f"missing columns (got: {','.join(found)})")

    use_abbrev = "TEAM_NAME" not in headers
    return {"headers": headers, "rows": rows, "iName": i_name, "iTeam": i_team, "iMIN": i_min, "iGP": i_gp, "useAbbrev": use_abbrev}


def _fetch_mpg_espn(espn_type=2):
    """Attempt 3: ESPN roster minutes (completely different source)."""
    print("  [injuries] Trying ESPN stats fallback...")

    urls = [
        "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/statistics/byathlete?region=us&lang=en&contentorigin=espn&is498=true&type=team&limit=500&sort=general.mpg:desc",
        f"https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/seasons/2026/types/{espn_type}/leaders?limit=500",
        "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/leaders?limit=500",
    ]

    for url in urls:
        try:
            res = requests.get(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=15)
            if res.status_code != 200:
                continue

            json_data = res.json()
            mpg_map = {}

            # Try different response shapes ESPN might use
            categories = (json_data.get("leaders") or {}).get("categories") or json_data.get("categories") or []
            min_cat = next(
                (c for c in categories
                 if c.get("name") in ("minutesPG", "minutes")
                 or c.get("abbreviation") == "MIN"
                 or c.get("displayName") == "Minutes Per Game"),
                None,
            )

            if min_cat and min_cat.get("leaders"):
                for entry in min_cat["leaders"]:
                    athlete = entry.get("athlete") or {}
                    name = athlete.get("displayName")
                    team = (athlete.get("team") or {}).get("displayName")
                    mpg_str = entry.get("displayValue") or entry.get("value") or "0"
                    try:
                        mpg = float(mpg_str)
                    except (ValueError, TypeError):
                        mpg = 0
                    if name and team and mpg > 0:
                        mpg_map[name] = {"team": team, "mpg": mpg, "gp": 50}

            # Also try flat athlete stats format
            if not mpg_map and json_data.get("athletes"):
                for ath in json_data["athletes"]:
                    athlete = ath.get("athlete") or {}
                    name = athlete.get("displayName")
                    team = (athlete.get("team") or {}).get("displayName")
                    stats = ath.get("stats") or (ath.get("categories") or [{}])[0].get("stats") or []
                    mpg_stat = next((s for s in stats if s.get("name") == "mpg" or s.get("abbreviation") == "MIN"), None)
                    try:
                        mpg = float((mpg_stat or {}).get("displayValue") or (mpg_stat or {}).get("value") or "0")
                    except (ValueError, TypeError):
                        mpg = 0
                    if name and team and mpg > 0:
                        mpg_map[name] = {"team": team, "mpg": mpg, "gp": 50}

            if len(mpg_map) >= 50:
                return mpg_map
        except Exception:
            pass  # try next URL

    raise Exception("all ESPN endpoints failed or returned insufficient data")


# Abbreviation -> full name map
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


def fetch_player_mpg(season_type="Regular Season", espn_type=2):
    """
    Fetch player minutes per game. Tries NBA.com leaguedash first, then ESPN.
    Always uses Regular Season for MPG.
    """
    mpg_season_type = "Regular Season"
    mpg_espn_type = 2

    mpg_map = {}

    # Attempt 1: leaguedashplayerstats
    try:
        data = _fetch_mpg_leaguedash(mpg_season_type)

        for row in data["rows"]:
            name = row[data["iName"]]
            raw_tm = row[data["iTeam"]]
            team = ABBREV_TO_NAME.get(raw_tm, raw_tm) if data["useAbbrev"] else raw_tm
            mpg = float(row[data["iMIN"]])
            gp = float(row[data["iGP"]])
            if not name or gp < 5:
                continue
            mpg_map[name] = {"team": team, "mpg": mpg, "gp": gp}

        if len(mpg_map) > 100:
            abbrev_note = ", abbrev->name" if data["useAbbrev"] else ""
            print(f"  [injuries] Got MPG for {len(mpg_map)} players (leaguedash{abbrev_note})")
            return mpg_map
        print(f"  [injuries] leaguedash returned only {len(mpg_map)} players, trying fallback...")
    except Exception as e:
        print(f"  [injuries] leaguedash failed: {e}")

    # Attempt 2: ESPN leaders
    try:
        mpg_map = _fetch_mpg_espn(mpg_espn_type)
        print(f"  [injuries] Got MPG for {len(mpg_map)} players (ESPN fallback)")
        return mpg_map
    except Exception as e:
        print(f"  [injuries] ESPN fallback failed: {e}")

    # All failed -- return empty (system degrades gracefully)
    print("  [injuries] All MPG sources failed -- injury tiers will default to bench")
    return {}


# -- ESPN per-game availability --

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


def _fetch_game_availability(date=None):
    """
    Returns a map of teamDisplayName -> [{ player, status, reason }]
    Only includes players listed as Out, Doubtful, or Questionable for THIS specific game.
    """
    day = date or _today_espn()

    # Step 1: get event IDs
    events = []
    try:
        hdr_url = f"https://site.web.api.espn.com/apis/v2/scoreboard/header?sport=basketball&league=nba&dates={day}"
        hdr_res = requests.get(hdr_url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=10)
        if hdr_res.status_code == 200:
            hdr = hdr_res.json()
            leagues = ((hdr.get("sports") or [{}])[0].get("leagues") or [{}])[0]
            events = [{"id": e.get("id")} for e in leagues.get("events", [])]
    except Exception:
        pass  # fall through to scoreboard

    if not events:
        sb_url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={day}"
        sb_res = requests.get(sb_url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=10)
        if sb_res.status_code != 200:
            raise Exception(f"ESPN scoreboard failed: {sb_res.status_code}")
        sb = sb_res.json()
        events = sb.get("events", [])

    if not events:
        print("  [injuries] No games today on ESPN scoreboard")
        return {}

    print(f"  [injuries] Fetching game-specific availability for {len(events)} games...")

    availability = {}  # teamDisplayName -> [{ player, status, reason }]

    for ev in events:
        event_id = ev.get("id")
        if not event_id:
            continue

        # Step 2: game summary -> injuries per competitor
        sum_url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={event_id}"
        try:
            sum_res = requests.get(sum_url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=10)
        except Exception:
            continue
        if sum_res.status_code != 200:
            continue

        try:
            summary = sum_res.json()
        except Exception:
            continue

        inj_entries = summary.get("injuries") or []
        for team_entry in inj_entries:
            team_name = (team_entry.get("team") or {}).get("displayName")
            if not team_name:
                continue

            players = []
            for inj in team_entry.get("injuries", []):
                player_name = (inj.get("athlete") or {}).get("displayName")
                if not player_name:
                    continue

                raw_status = inj.get("status") or ""
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


# -- Main export --

def fetch_injury_data(date=None, season_type="Regular Season", espn_type=2):
    """
    Returns { "report": { team -> [{ player, status, tier, mpg, reason }] }, "playerMPG": {...} }
    """
    # Fetch both in sequence (Python doesn't have Promise.all, but we handle errors individually)
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
                    None,
                )
                if found:
                    mpg_entry = player_mpg[found]

            mpg = mpg_entry["mpg"] if mpg_entry else 0
            tier = _classify_tier(mpg)
            enriched.append({**p, "tier": tier, "mpg": mpg})

        report[team_name] = enriched

    return {"report": report, "playerMPG": player_mpg}


# -- Convenience exports --

TEAM_ALIASES = {
    "Los Angeles Clippers": "LA Clippers",
    "LA Clippers": "Los Angeles Clippers",
}


def get_key_injuries(report, team_name, player_mpg=None):
    """Returns only Out/Doubtful players who are Star or Starter tier."""
    entries = report.get(team_name) or report.get(TEAM_ALIASES.get(team_name, "")) or []
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
    """Returns 0 (no concern) -> 5+ (skip the game)."""
    score = 0
    for inj in list(away_injuries) + list(home_injuries):
        if inj.get("tier") == "star" and inj.get("status") == "out":
            score += 1
        if inj.get("tier") == "star" and inj.get("status") == "doubtful":
            score += 1
    return min(score, 5)
