import requests
from urllib.parse import urlencode

SEASON = "2025-26"
GAME_LOG_URL = "https://stats.nba.com/stats/leaguegamelog"
NBA_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Referer": "https://www.nba.com/",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
}


# -- Fetch all team game logs for the season -----------------------------------

def _fetch_season_game_logs(season=SEASON):
    params = {
        "Counter": "0",
        "Direction": "DESC",
        "LeagueID": "00",
        "PlayerOrTeam": "T",
        "Season": season,
        "SeasonType": "Regular Season",
        "Sorter": "DATE",
    }

    url = f"{GAME_LOG_URL}?{urlencode(params)}"
    resp = requests.get(url, headers=NBA_HEADERS)
    if resp.status_code != 200:
        raise Exception(f"NBA game log fetch failed: {resp.status_code}")

    data = resp.json()
    headers = data["resultSets"][0]["headers"]
    rows = data["resultSets"][0]["rowSet"]

    result = []
    for row in rows:
        obj = {}
        for i, h in enumerate(headers):
            obj[h] = row[i]
        result.append(obj)
    return result


# -- Parse game logs into H2H matchup pairs -----------------------------------

def _parse_matchups(game_logs):
    """
    Each game has TWO rows in the log (one per team). GAME_ID links them.
    MATCHUP field: "TOR vs. CLE" = home, "CLE @ TOR" = away.
    """
    by_game = {}
    for row in game_logs:
        gid = row["GAME_ID"]
        if gid not in by_game:
            by_game[gid] = []
        by_game[gid].append(row)

    matchups = {}

    for gid, rows in by_game.items():
        if len(rows) != 2:
            continue

        home = None
        away = None
        for r in rows:
            if "vs." in r["MATCHUP"]:
                home = r
            elif "@" in r["MATCHUP"]:
                away = r
        if not home or not away:
            continue

        key = "::".join(sorted([home["TEAM_NAME"], away["TEAM_NAME"]]))

        if key not in matchups:
            sorted_teams = sorted([home["TEAM_NAME"], away["TEAM_NAME"]])
            matchups[key] = {
                "team1": sorted_teams[0],
                "team2": sorted_teams[1],
                "games": [],
            }

        matchups[key]["games"].append({
            "gameId": gid,
            "date": home["GAME_DATE"],
            "home": {
                "team": home["TEAM_NAME"],
                "pts": home["PTS"],
                "fgPct": home["FG_PCT"],
                "fg3Pct": home["FG3_PCT"],
                "reb": home["REB"],
                "ast": home["AST"],
                "tov": home["TOV"],
                "plusMinus": home["PLUS_MINUS"],
            },
            "away": {
                "team": away["TEAM_NAME"],
                "pts": away["PTS"],
                "fgPct": away["FG_PCT"],
                "fg3Pct": away["FG3_PCT"],
                "reb": away["REB"],
                "ast": away["AST"],
                "tov": away["TOV"],
                "plusMinus": away["PLUS_MINUS"],
            },
        })

    return matchups


# -- Main export ---------------------------------------------------------------

def fetch_h2h_matchups(season=SEASON):
    print("  [h2h] Fetching season game logs...")
    logs = _fetch_season_game_logs(season)
    print(f"  [h2h] Got {len(logs)} game log entries")

    matchups = _parse_matchups(logs)
    n_matchups = len(matchups)
    total_games = sum(len(m["games"]) for m in matchups.values())
    print(f"  [h2h] Parsed {total_games} games across {n_matchups} matchup pairs")

    return matchups
