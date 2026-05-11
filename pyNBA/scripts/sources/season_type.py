# scripts/sources/season_type.py
# Auto-detect whether we're in Regular Season or Playoffs based on date.
#
# NBA 2025-26 key dates (update these each season):
#   Regular season ends:  2026-04-12
#   Play-in tournament:   2026-04-14 to 2026-04-17
#   Playoffs begin:       2026-04-18

import datetime
from zoneinfo import ZoneInfo

PLAYOFF_START = "20260414"  # play-in tournament day 1 (4/14-4/17), playoffs proper 4/18+


def _today_yyyymmdd():
    now = datetime.datetime.now(ZoneInfo("America/Chicago"))
    return now.strftime("%Y%m%d")


def get_season_type(date_str=None):
    """
    Returns "Regular Season" or "Playoffs".
    date_str can be "YYYYMMDD" or "YYYY-MM-DD".
    """
    d = (date_str or _today_yyyymmdd()).replace("-", "")
    return "Playoffs" if int(d) >= int(PLAYOFF_START) else "Regular Season"


def get_espn_season_type(date_str=None):
    """Returns 2 (regular season) or 3 (playoffs) for ESPN API."""
    return 3 if get_season_type(date_str) == "Playoffs" else 2


def is_playoffs(date_str=None):
    return get_season_type(date_str) == "Playoffs"
