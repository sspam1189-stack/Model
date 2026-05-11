"""
Season-type detection for pyNBAPROPS.

Mirrors pyFull/scripts/sources/season_type.py — returns the NBA Stats API
season_type string ("Regular Season" or "Playoffs") based on date. The
playoff start date (PLAYOFF_START) is the league's round-1 day 1.
"""

from datetime import datetime
import pytz


PLAYOFF_START = "20260418"  # first round game 1, 2025-26 NBA season


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
