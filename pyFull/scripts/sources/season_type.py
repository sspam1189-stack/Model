from datetime import datetime
import pytz


PLAYOFF_START = "20270414"  # 2026-27 play-in day 1 — PROVISIONAL estimate; confirm exact date when the NBA releases the 2026-27 schedule (Aug 2026). Any mid-Apr-2027 value keeps the whole 2026-27 regular season classified correctly.


def _today_yyyymmdd():
    tz = pytz.timezone("America/Chicago")
    now = datetime.now(tz)
    return now.strftime("%Y%m%d")


def get_season_type(date_str=None):
    """
    NBA.com API value: "Regular Season" or "Playoffs"
    """
    d = (date_str or _today_yyyymmdd()).replace("-", "")
    return "Playoffs" if int(d) >= int(PLAYOFF_START) else "Regular Season"


def get_espn_season_type(date_str=None):
    """ESPN API value: 2 (regular season) or 3 (playoffs)"""
    return 3 if get_season_type(date_str) == "Playoffs" else 2


def is_playoffs(date_str=None):
    return get_season_type(date_str) == "Playoffs"
