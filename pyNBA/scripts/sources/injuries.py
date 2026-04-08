# scripts/sources/injuries.py
# Fetches per-game player availability from ESPN's game summary endpoint.
# This catches ALL reasons a player is out -- injury, rest, personal, DTD, etc.
#
# Exports:
#   fetch_injury_data()  -> { "report": ..., "playerMPG": ... }
#   get_key_injuries(report, team_name) -> [{ player, status, tier, mpg }]
#   game_uncertainty_score(away_inj, home_inj) -> number 0-4

import os
import json
import time
import requests
import datetime
import re
import unicodedata
from zoneinfo import ZoneInfo

_dir = os.path.dirname(os.path.abspath(__file__))
INJURY_CACHE_DIR = os.path.join(_dir, "..", "..", "..", "data", "injury_cache", "nba")

def _strip_accents(s):
    """Luka Dončić -> Luka Doncic"""
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")

NBA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
}


def _load_injury_cache(date_key, max_age_hours=2):
    """Load cached injury data if fresh enough."""
    path = os.path.join(INJURY_CACHE_DIR, f"{date_key}.json")
    if not os.path.exists(path):
        return None
    if max_age_hours is not None:
        age_h = (time.time() - os.path.getmtime(path)) / 3600
        if age_h > max_age_hours:
            return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return None


def _save_injury_cache(date_key, data):
    """Save injury data to cache."""
    os.makedirs(INJURY_CACHE_DIR, exist_ok=True)
    path = os.path.join(INJURY_CACHE_DIR, f"{date_key}.json")
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"  [injuries] Cached to {os.path.basename(path)}")
    except Exception as e:
        print(f"  [injuries] Cache write failed: {e}")


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

def _normalize_status(raw, comment=None):
    """Normalize injury status. If raw is vague (e.g. Day-To-Day), also check
    the comment text for a more specific designation (doubtful, out, etc.)."""
    s = str(raw or "").lower()
    if "out" in s:
        return "out"
    if "doubtful" in s:
        return "doubtful"
    if "questionable" in s or "day-to-day" in s or "dtd" in s:
        if comment:
            c = str(comment).lower()
            if "ruled out" in c or "will not play" in c or "will miss" in c:
                return "out"
            if "listed as doubtful" in c or "is doubtful" in c:
                return "doubtful"
        return "questionable"
    if "probable" in s:
        return "probable"
    return "active"


def _classify_tier(mpg):
    if mpg >= 32:
        return "star"
    if mpg >= 28:
        return "starter"
    if mpg >= 18:
        return "bench"
    return "deep_bench"


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
                reason = inj.get("shortComment") or inj.get("longComment") or raw_status or ""
                status = _normalize_status(raw_status, reason)
                if status in ("active", "probable"):
                    continue

                players.append({"player": player_name, "status": status, "reason": reason})

            if players:
                availability[team_name] = players

    total = sum(len(v) for v in availability.values())
    print(f"  [injuries] {total} players listed out/limited for today's games")
    return availability


def _fetch_leaguewide_injuries(teams_playing):
    """
    Fetch ESPN league-wide /injuries endpoint and return players listed as
    Out or Doubtful whose team is playing today.
    Returns: { teamDisplayName -> [{ player, status, reason }] }
    """
    url = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=15)
        if r.status_code != 200:
            return {}
        data = r.json()
    except Exception:
        return {}

    result = {}
    for team_data in data.get("injuries", []):
        team_name = team_data.get("displayName", "")
        if not team_name or team_name not in teams_playing:
            continue
        players = []
        for inj in team_data.get("injuries", []):
            name = (inj.get("athlete") or {}).get("displayName", "")
            if not name:
                continue
            reason = inj.get("shortComment") or inj.get("longComment") or inj.get("status") or ""
            status = _normalize_status(inj.get("status"), reason)
            if status not in ("out", "doubtful", "questionable"):
                continue
            players.append({"player": name, "status": status, "reason": reason})
        if players:
            result[team_name] = players
    return result


# -- Main export --

def fetch_injury_data(date=None, season_type="Regular Season", espn_type=2):
    """
    Returns { "report": { team -> [{ player, status, tier, mpg, reason }] }, "playerMPG": {...} }

    Always fetches live for today (like JS models). Uses cache only for
    historical dates.
    """
    date_key = date or _today_espn()

    # Only use cache for past dates — today always fetches live
    is_today = (date_key == _today_espn())
    if not is_today:
        cached = _load_injury_cache(date_key, max_age_hours=None)
        if cached is not None:
            # Re-enrich tiers using accent-aware matching (fixes stale caches)
            player_mpg = cached.get("playerMPG", {})
            if player_mpg:
                for team_name, players in cached.get("report", {}).items():
                    for p in players:
                        if p.get("mpg", 0) == 0:
                            stripped = _strip_accents(p["player"]).lower()
                            found = next(
                                (k for k in player_mpg
                                 if _strip_accents(k).lower() == stripped
                                 and player_mpg[k]["team"] == team_name),
                                None,
                            )
                            if found:
                                p["mpg"] = player_mpg[found]["mpg"]
                                p["tier"] = _classify_tier(p["mpg"])
            n_players = sum(len(v) for v in cached.get("report", {}).values())
            print(f"  [injuries] Using cache: {date_key}.json ({n_players} players)")
            return cached

    # Fetch both in sequence
    try:
        game_availability = _fetch_game_availability(date)
    except Exception as e:
        print(f"  [injuries] Game availability fetch failed: {e}")
        game_availability = {}

    # Supplement with league-wide injuries for players the per-game data missed
    try:
        teams_playing = set(game_availability.keys())
        # Also need teams that had games but returned no injuries
        # Use scoreboard to get all teams playing today
        day = date or _today_espn()
        sb_url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={day}"
        sb_res = requests.get(sb_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if sb_res.status_code == 200:
            for ev in sb_res.json().get("events", []):
                for comp in ev.get("competitions", []):
                    for c in comp.get("competitors", []):
                        tname = c.get("team", {}).get("displayName", "")
                        if tname:
                            teams_playing.add(tname)

        lw_injuries = _fetch_leaguewide_injuries(teams_playing)
        # Merge: add players from league-wide that aren't already in per-game data,
        # and upgrade status if league-wide is more severe (e.g. DTD -> doubtful)
        _severity = {"active": 0, "probable": 1, "questionable": 2, "doubtful": 3, "out": 4}
        added = 0
        upgraded = 0
        for team_name, lw_players in lw_injuries.items():
            existing = {p["player"]: p for p in game_availability.get(team_name, [])}
            for p in lw_players:
                if p["player"] not in existing:
                    game_availability.setdefault(team_name, []).append(p)
                    added += 1
                else:
                    # Upgrade status if league-wide is more severe
                    cur = existing[p["player"]]
                    if _severity.get(p["status"], 0) > _severity.get(cur["status"], 0):
                        cur["status"] = p["status"]
                        if p.get("reason") and (not cur.get("reason") or cur["reason"] == cur.get("status", "")):
                            cur["reason"] = p["reason"]
                        upgraded += 1
        if added or upgraded:
            parts = []
            if added:
                parts.append(f"{added} added")
            if upgraded:
                parts.append(f"{upgraded} upgraded")
            print(f"  [injuries] Merged from league-wide injuries: {', '.join(parts)}")
    except Exception as e:
        print(f"  [injuries] League-wide merge failed (non-fatal): {e}")

    try:
        player_mpg = fetch_player_mpg(season_type=season_type, espn_type=espn_type)
    except Exception as e:
        print(f"  [injuries] MPG fetch failed: {e}")
        player_mpg = {}

    report = {}

    for team_name, players in game_availability.items():
        enriched = []
        for p in players:
            # Look up MPG -- exact match first, then accent-stripped / last-name fallback
            mpg_entry = player_mpg.get(p["player"])
            if not mpg_entry:
                stripped = _strip_accents(p["player"]).lower()
                last_name = stripped.split(" ")[-1]
                found = next(
                    (k for k in player_mpg
                     if (_strip_accents(k).lower() == stripped
                         or _strip_accents(k).split(" ")[-1].lower() == last_name)
                     and player_mpg[k]["team"] == team_name),
                    None,
                )
                if found:
                    mpg_entry = player_mpg[found]

            mpg = mpg_entry["mpg"] if mpg_entry else 0
            tier = _classify_tier(mpg)
            enriched.append({**p, "tier": tier, "mpg": mpg})

        report[team_name] = enriched

    result = {"report": report, "playerMPG": player_mpg}

    # Cache the result
    if report:
        _save_injury_cache(date_key, result)

    return result


# -- Convenience exports --

TEAM_ALIASES = {
    "Los Angeles Clippers": "LA Clippers",
    "LA Clippers": "Los Angeles Clippers",
}


def get_key_injuries(report, team_name, player_mpg=None, recent_injury_dates=None, ofs_players=None):
    """Display only — show everyone out/doubtful today above deep_bench tier.
    Long-term and OFS filtering only applies to stat adjustments (in adjust_team_stats),
    not to the dashboard display."""
    entries = report.get(team_name) or report.get(TEAM_ALIASES.get(team_name, "")) or []

    long_term_out = set()
    if recent_injury_dates and len(recent_injury_dates) >= 5:
        for entry in entries:
            if entry["status"] not in ("out", "doubtful"):
                continue
            name = entry["player"]
            last_name = name.split(" ")[-1].lower()
            dates_out = 0
            for date_report in recent_injury_dates.values():
                team_inj = date_report.get(team_name) or date_report.get(TEAM_ALIASES.get(team_name, "")) or []
                if any(inj for inj in team_inj
                       if inj.get("status") in ("out", "doubtful")
                       and (inj["player"] == name or inj["player"].split(" ")[-1].lower() == last_name)):
                    dates_out += 1
            if dates_out >= 4:
                long_term_out.add(name)

    result = []
    for p in entries:
        if p["status"] not in ("out", "doubtful"):
            continue
        if p["tier"] == "deep_bench":
            continue
        if p["player"] in long_term_out:
            continue
        if ofs_players and p["player"] in ofs_players:
            continue
        if player_mpg:
            mpg_entry = player_mpg.get(p["player"])
            if not mpg_entry:
                stripped = _strip_accents(p["player"]).lower()
                mpg_entry = next(
                    (v for k, v in player_mpg.items()
                     if _strip_accents(k).lower() == stripped),
                    None,
                )
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


def fetch_out_for_season(game_injury_report=None):
    """
    Fetch the ESPN league-wide injuries page and return a set of player names
    who are status=Out with a return date still in the future.
    Players listed as questionable/DTD in today's per-game report are excluded.
    """
    if game_injury_report is None:
        game_injury_report = {}
    url = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
    today = datetime.datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")

    # Build set of players listed as questionable/DTD in today's per-game report
    game_dtd = set()
    for players in game_injury_report.values():
        for p in players:
            if p.get("status") in ("questionable", "doubtful"):
                game_dtd.add(p.get("player", ""))

    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=15)
        if r.status_code != 200:
            print("  [injuries] ESPN league-wide injuries fetch failed")
            return set()
        data = r.json()
        # Only flag truly season-ending injuries — return date past July 1
        now = datetime.datetime.now()
        season_end = f"{now.year + 1 if now.month >= 10 else now.year}-07-01"
        ofs_players = set()
        for team in data.get("injuries", []):
            for inj in team.get("injuries", []):
                status = (inj.get("status") or "").lower()
                return_date = (inj.get("details") or {}).get("returnDate", "")
                if status == "out" and return_date >= season_end:
                    name = (inj.get("athlete") or {}).get("displayName", "")
                    if name and name not in game_dtd:
                        ofs_players.add(name)
        print(f"  [injuries] Found {len(ofs_players)} out-for-season players (returnDate >= {season_end})")
        return ofs_players
    except Exception as e:
        print(f"  [injuries] OFS fetch error: {e}")
        return set()
