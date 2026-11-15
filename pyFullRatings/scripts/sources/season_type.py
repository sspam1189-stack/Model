from datetime import datetime
import pytz


PLAYOFF_START = "20270414"  # 2026-27 play-in day 1 — STILL PROVISIONAL. Regular season confirmed to open Tue 2026-10-20 (user, 2026-08-27). 2025-26 ran 2025-10-21 -> play-in 2026-04-14 (175 days), so the same span from 2026-10-20 lands on 2027-04-13. Kept one day LATE on purpose: a regular-season day misread as regular season is harmless, whereas a regular-season day misread as playoff applies the playoff HCA and the 0.65 probHigh floor and changes picks. Confirm against the published schedule and set the exact date.


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
