from datetime import datetime
import pytz


# WNBA playoffs ON from 2026-09-27 (user, 2026-09-30: "similar to NBA").
# Was parked at 29990101 (all dates Regular Season) because the NBA playoff
# machinery isn't WNBA-validated; the user chose to run it anyway. From
# PLAYOFF_START: team stats = regular season blended with playoff games
# (nba_stats.PLAYOFF_RAMP_GAMES), playoff minutes inflation (lineup_adjust),
# probHigh floor 0.65, per-team HCA, and empirical playoff HCA once >= 10
# playoff games are in history.json. 2026: regular season ended 9/24, playoff
# round 1 day 1 = 9/27 (ESPN season type 3). Set next season's date by hand.
PLAYOFF_START = "20260927"


def _today_yyyymmdd():
    tz = pytz.timezone("America/Chicago")
    now = datetime.now(tz)
    return now.strftime("%Y%m%d")


def get_season_type(date_str=None):
    """
    nba_api season_type value: "Regular Season" or "Playoffs".
    "Playoffs" from PLAYOFF_START on (see note above).
    """
    d = (date_str or _today_yyyymmdd()).replace("-", "")
    return "Playoffs" if int(d) >= int(PLAYOFF_START) else "Regular Season"


def get_espn_season_type(date_str=None):
    """ESPN API value: 2 (regular season) or 3 (playoffs)"""
    return 3 if get_season_type(date_str) == "Playoffs" else 2


def is_playoffs(date_str=None):
    return get_season_type(date_str) == "Playoffs"
