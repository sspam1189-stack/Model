"""
Season-type detection for pyNBAPROPS.

Mirrors pyFull/scripts/sources/season_type.py — returns the NBA Stats API
season_type string ("Regular Season" or "Playoffs") based on date. The
playoff start date (PLAYOFF_START) is the league's round-1 day 1.
"""

from datetime import datetime
import pytz


PLAYOFF_START = "20270414"  # 2026-27 play-in day 1 — PROVISIONAL estimate; confirm exact date when the NBA releases the 2026-27 schedule (Aug 2026). Any mid-Apr-2027 value keeps the whole 2026-27 regular season classified correctly.


def _today_yyyymmdd():
    tz = pytz.timezone("America/Chicago")
    now = datetime.now(tz)
    return now.strftime("%Y%m%d")


def get_season_type(date_str=None):
    """NBA.com API value: 'Regular Season' or 'Playoffs'."""
    d = (date_str or _today_yyyymmdd()).replace("-", "")
    return "Playoffs" if int(d) >= int(PLAYOFF_START) else "Regular Season"


def is_playoffs(date_str=None):
    return get_season_type(date_str) == "Playoffs"
