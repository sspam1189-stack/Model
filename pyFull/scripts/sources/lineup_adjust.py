# scripts/sources/lineup_adjust.py
# Lineup-adjusted team efficiency: replaces season-long team OFF/DEF/TS/TO/ORR
# with tonight's expected values given who's actually playing.
#
# Usage:
#   from sources.lineup_adjust import fetch_player_advanced, adjust_team_stats
#   player_adv = fetch_player_advanced()
#   adjusted   = adjust_team_stats(team_stats, injury_report, player_mpg, player_adv, games)

import requests
import datetime
import math
import re

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


# -- Fetch per-player advanced stats --

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


def fetch_player_advanced(season_type="Regular Season"):
    """
    Fetch per-player advanced stats from NBA.com.
    Returns: { player_name: { team, min, gp, offRtg, defRtg, netRtg, tsPct, tovPct, orbPct, pace } }
    Always uses Regular Season for player advanced stats.
    """
    effective_type = "Regular Season"
    season = _current_season()

    # Use nba_api instead of raw requests (handles headers/cookies/retries)
    from nba_api.stats.endpoints import leaguedashplayerstats

    try:
        endpoint = leaguedashplayerstats.LeagueDashPlayerStats(
            season=season,
            season_type_all_star=effective_type,
            measure_type_detailed_defense="Advanced",
            per_mode_detailed="PerGame",
            timeout=120,
        )
        df = endpoint.get_data_frames()[0]
    except Exception as e:
        raise Exception(f"nba_api leaguedashplayerstats Advanced failed: {e}")

    if df.empty:
        raise Exception("nba_api: empty response")

    # Convert DataFrame to raw format for backward compat
    headers = list(df.columns)
    rows = df.values.tolist()

    def idx(name):
        return headers.index(name) if name in headers else -1

    i_name = idx("PLAYER_NAME")
    i_team = idx("TEAM_ABBREVIATION")
    i_team_nm = idx("TEAM_NAME")
    i_min = idx("MIN")
    i_gp = idx("GP")
    i_off = idx("OFF_RATING")
    i_def = idx("DEF_RATING")
    i_net = idx("NET_RATING")
    i_ts = idx("TS_PCT")
    i_tov = idx("TM_TOV_PCT")
    i_orb = idx("OREB_PCT")
    i_pace = idx("PACE")

    # Minimum required columns
    if -1 in [i_name, i_min, i_gp, i_off, i_def]:
        raise Exception(f"missing expected columns (got: {', '.join(headers[:15])})")

    players = {}
    skipped = 0

    for row in rows:
        name = row[i_name]
        gp = float(row[i_gp])
        min_val = float(row[i_min])

        # Skip players with very few games or very low minutes
        if gp < 10 or min_val < 5:
            skipped += 1
            continue

        abbrev = row[i_team] if i_team != -1 else ""
        team_nm = row[i_team_nm] if i_team_nm != -1 else ""
        team = team_nm or ABBREV_TO_NAME.get(abbrev, abbrev)

        players[name] = {
            "team": team,
            "min": min_val,
            "gp": gp,
            "offRtg": float(row[i_off]),
            "defRtg": float(row[i_def]),
            "netRtg": float(row[i_net]) if i_net != -1 else None,
            "tsPct": float(row[i_ts]) if i_ts != -1 else None,
            "tovPct": float(row[i_tov]) if i_tov != -1 else None,
            "orbPct": float(row[i_orb]) if i_orb != -1 else None,
            "pace": float(row[i_pace]) if i_pace != -1 else None,
        }

    return players


# -- Team name matching --

def _norm_key(s):
    s = str(s or "").lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r" +", " ", s)
    return s.strip()


def _resolve_team_name(team_name, known_keys):
    """Match a team name from injury report / odds to the stats object key."""
    if not team_name:
        return None
    wanted = _norm_key(team_name)
    for k in known_keys:
        if _norm_key(k) == wanted:
            return k
    # Substring fallback
    for k in known_keys:
        nk = _norm_key(k)
        if nk in wanted or wanted in nk:
            return k
    return None


# -- Core: Compute lineup-adjusted team stats --

def adjust_team_stats(team_stats, injury_report, player_mpg, player_adv, todays_games, recent_injury_dates=None, ofs_players=None):
    """
    Compute lineup-adjusted team stats based on who's actually playing tonight.
    Returns: adjusted copy of team_stats (same shape).
    """
    if not player_adv or not len(player_adv):
        return team_stats

    adjusted = dict(team_stats)
    team_keys = list(team_stats.keys())

    # Build set of teams playing tonight
    teams_tonight = set()
    for g in todays_games:
        if g.get("away"):
            teams_tonight.add(g["away"])
        if g.get("home"):
            teams_tonight.add(g["home"])

    # Group players by team from the advanced stats
    players_by_team = {}
    for name, p in player_adv.items():
        team_key = _resolve_team_name(p["team"], team_keys)
        if not team_key:
            continue
        if team_key not in players_by_team:
            players_by_team[team_key] = []
        players_by_team[team_key].append({"name": name, **p})

    adjusted_count = 0

    for team_key in team_keys:
        # Only adjust teams playing tonight
        is_tonight = team_key in teams_tonight or any(
            _resolve_team_name(t, [team_key]) for t in teams_tonight
        )
        if not is_tonight:
            continue

        # Get all rostered players for this team
        roster = players_by_team.get(team_key)
        if not roster or len(roster) < 5:
            continue

        # Get injury report for this team
        inj_key = _resolve_team_name(team_key, list((injury_report or {}).keys()))
        injuries = (injury_report or {}).get(inj_key, []) if inj_key else []

        # Only care about players who are OUT or DOUBTFUL
        out_players = set(
            i["player"] for i in injuries
            if i.get("status") in ("out", "doubtful")
        )

        # -- Returning-star boost --
        # When a high-minute player was recently OUT but is active tonight,
        # boost the team by their value over league average, decaying 0.1/day.
        if recent_injury_dates and len(recent_injury_dates) > 0:
            MIN_MPG = 24
            total_boost_off = 0
            total_boost_def = 0

            # Compute league averages from team stats
            all_off = [t["OFF"] for t in team_stats.values() if isinstance(t, dict) and "OFF" in t]
            all_def = [t["DEF"] for t in team_stats.values() if isinstance(t, dict) and "DEF" in t]
            league_avg_off = sum(all_off) / len(all_off) if all_off else 114.0
            league_avg_def = sum(all_def) / len(all_def) if all_def else 114.0

            # Today's date from the most recent cache key (YYYYMMDD)
            today_str = max(recent_injury_dates.keys())

            # OFS set for season-ending detection
            _ofs = ofs_players or set()

            for p in roster:
                if p["min"] < MIN_MPG:
                    continue
                # Must be active tonight
                if p["name"] in out_players:
                    continue
                last_name = p["name"].split(" ")[-1].lower()
                is_out = any(
                    on.split(" ")[-1].lower() == last_name for on in out_players
                )
                if is_out:
                    continue

                # Skip out-for-season players (from ESPN league-wide injuries)
                if p["name"] in _ofs or any(
                    n.split(" ")[-1].lower() == last_name for n in _ofs
                ):
                    print(f"  [lineup] Skipping returning-star boost for {p['name']} — out for season (ESPN OFS)")
                    continue

                # Find dates where this player was OUT in recent caches
                out_dates = []
                for cache_date, report in recent_injury_dates.items():
                    team_inj = report.get(team_key, [])
                    if not team_inj:
                        resolved = _resolve_team_name(team_key, list(report.keys()))
                        team_inj = report.get(resolved, []) if resolved else []
                    was_out = any(
                        inj.get("status") in ("out", "doubtful")
                        and (inj.get("player") == p["name"]
                             or (inj.get("player") or "").split(" ")[-1].lower() == last_name)
                        for inj in team_inj
                    )
                    if was_out:
                        out_dates.append(cache_date)

                if len(out_dates) < 3:
                    continue

                # Decay: 0.1 per day since last OUT appearance
                last_out = max(out_dates)
                try:
                    d1 = datetime.datetime.strptime(today_str, "%Y%m%d")
                    d2 = datetime.datetime.strptime(last_out, "%Y%m%d")
                    days_back = (d1 - d2).days
                except Exception:
                    days_back = 0
                decay = max(0.0, 1.0 - 0.1 * days_back)
                if decay <= 0:
                    continue

                off_delta = ((p.get("offRtg") or 0) - league_avg_off) * decay
                def_delta = ((p.get("defRtg") or 0) - league_avg_def) * decay

                total_boost_off += off_delta
                total_boost_def += def_delta

                print(f"  [lineup] Returning-star boost: {p['name']} (offRtg {p.get('offRtg', 0):.1f}, defRtg {p.get('defRtg', 0):.1f}, {days_back}d back, decay {decay:.1f}) → {team_key} OFF {'+' if off_delta > 0 else ''}{off_delta:.1f}, DEF {'+' if def_delta > 0 else ''}{def_delta:.1f}")

            if total_boost_off != 0 or total_boost_def != 0:
                orig = adjusted.get(team_key, team_stats[team_key])
                adjusted[team_key] = {
                    **orig,
                    "OFF": round((orig["OFF"] + total_boost_off) * 100) / 100,
                    "DEF": round((orig["DEF"] + total_boost_def) * 100) / 100,
                }
                adjusted_count += 1

        if not out_players:
            continue

        # Match out players to roster using exact + fuzzy name matching
        roster_out = set()
        for out_name in out_players:
            # Exact match
            found = next((r for r in roster if r["name"] == out_name), None)
            # Last-name fallback
            if not found:
                last_name = out_name.split(" ")[-1].lower()
                found = next(
                    (r for r in roster if r["name"].split(" ")[-1].lower() == last_name),
                    None,
                )
            if found:
                roster_out.add(found["name"])

        if not roster_out:
            continue

        # Separate available vs out
        available = [r for r in roster if r["name"] not in roster_out]
        out_list = [r for r in roster if r["name"] in roster_out]

        if len(available) < 5:
            continue

        # Compute full-roster weighted averages (weighted by minutes)
        full_total_min = sum(r["min"] for r in roster)
        if full_total_min <= 0:
            continue

        def weighted_avg(player_list, getter):
            total_sum = 0
            valid_min = 0
            for p in player_list:
                val = getter(p)
                if isinstance(val, (int, float)) and math.isfinite(val):
                    total_sum += p["min"] * val
                    valid_min += p["min"]
            return total_sum / valid_min if valid_min > 0 else None

        # Full-roster weighted averages
        full_off = weighted_avg(roster, lambda p: p.get("offRtg"))
        full_def = weighted_avg(roster, lambda p: p.get("defRtg"))
        full_ts = weighted_avg(roster, lambda p: p.get("tsPct"))
        full_tov = weighted_avg(roster, lambda p: p.get("tovPct"))
        full_orb = weighted_avg(roster, lambda p: p.get("orbPct"))

        # Available-roster weighted averages
        avail_off = weighted_avg(available, lambda p: p.get("offRtg"))
        avail_def = weighted_avg(available, lambda p: p.get("defRtg"))
        avail_ts = weighted_avg(available, lambda p: p.get("tsPct"))
        avail_tov = weighted_avg(available, lambda p: p.get("tovPct"))
        avail_orb = weighted_avg(available, lambda p: p.get("orbPct"))

        # Impact-aware dampening
        def impact_dampen(out_l):
            best = 0.70
            for p in out_l:
                net = p.get("netRtg")
                if net is None:
                    off_r = p.get("offRtg")
                    def_r = p.get("defRtg")
                    net = (off_r - def_r) if off_r is not None and def_r is not None else 0
                if p["min"] >= 28 and net > 5:
                    d = 1.20
                elif p["min"] >= 28:
                    d = 1.00
                elif p["min"] >= 18:
                    d = 0.85
                else:
                    d = 0.70
                if d > best:
                    best = d
            return best

        dampen = impact_dampen(out_list)

        orig = adjusted.get(team_key, team_stats[team_key])
        adj = dict(orig)
        any_change = False

        if full_off is not None and avail_off is not None:
            delta = (avail_off - full_off) * dampen
            adj["OFF"] = round((orig["OFF"] + delta) * 100) / 100
            any_change = True
        if full_def is not None and avail_def is not None:
            delta = (avail_def - full_def) * dampen
            adj["DEF"] = round((orig["DEF"] + delta) * 100) / 100
            any_change = True
        if full_ts is not None and avail_ts is not None:
            delta = (avail_ts - full_ts) * dampen
            adj["TS"] = round((orig["TS"] + delta) * 10000) / 10000
        if full_tov is not None and avail_tov is not None:
            delta = (avail_tov - full_tov) * dampen
            adj["TO"] = round((orig["TO"] + delta) * 10000) / 10000
        if full_orb is not None and avail_orb is not None:
            delta = (avail_orb - full_orb) * dampen
            adj["ORR"] = round((orig["ORR"] + delta) * 10000) / 10000

        if any_change:
            adjusted[team_key] = adj
            adjusted_count += 1

    return adjusted


# -- Diagnostic: build human-readable adjustment notes per game --

def get_adjustment_notes(original_stats, adjusted_stats):
    """
    Returns: { team_name: { offDelta, defDelta, adjOFF, adjDEF, origOFF, origDEF } }
    Useful for email display.
    """
    notes = {}
    for team, orig in original_stats.items():
        adj = adjusted_stats.get(team)
        if not adj or adj is orig:
            continue

        off_delta = adj["OFF"] - orig["OFF"]
        def_delta = adj["DEF"] - orig["DEF"]

        # Only note if there's a meaningful change (> 0.3 pts)
        if abs(off_delta) > 0.3 or abs(def_delta) > 0.3:
            notes[team] = {
                "offDelta": round(off_delta * 10) / 10,
                "defDelta": round(def_delta * 10) / 10,
                "adjOFF": adj["OFF"],
                "adjDEF": adj["DEF"],
                "origOFF": orig["OFF"],
                "origDEF": orig["DEF"],
            }
    return notes
