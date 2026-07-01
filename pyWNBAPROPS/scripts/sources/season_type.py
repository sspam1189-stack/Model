"""
Season-type detection for pyWNBAPROPS.

Returns the WNBA Stats API
season_type string ("Regular Season" or "Playoffs") based on date. The
playoff start date (PLAYOFF_START) is the league's round-1 day 1.
"""

from datetime import datetime
import pytz


PLAYOFF_START = "20260914"  # WNBA 2026 playoffs day 1 — PROVISIONAL estimate (mid-Sep). WNBA plays May-Sep with playoffs in Sep; any mid-Sep value keeps the whole regular season classified correctly. Confirm when the WNBA releases the 2026 schedule.


def _today_yyyymmdd():
    tz = pytz.timezone("America/Chicago")
    now = datetime.now(tz)
    return now.strftime("%Y%m%d")


def get_season_type(date_str=None):
    """WNBA.com API value: 'Regular Season' or 'Playoffs'."""
    d = (date_str or _today_yyyymmdd()).replace("-", "")
    return "Playoffs" if int(d) >= int(PLAYOFF_START) else "Regular Season"


def is_playoffs(date_str=None):
    return get_season_type(date_str) == "Playoffs"
