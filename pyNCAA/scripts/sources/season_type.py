# scripts/sources/season_type.py
# Auto-detect whether we're in Regular Season or NCAA Tournament.
#
# NCAA 2025-26 key dates (update each season):
#   Regular season / conference tournaments end: ~2026-03-15
#   NCAA Tournament begins (First Four): 2026-03-17
#   NCAA Tournament proper (First Round): 2026-03-19

from datetime import datetime

TOURNEY_START = "20260317"  # First Four


def get_season_type(date_str=None):
    d = (date_str or _today_yyyymmdd()).replace("-", "")
    return "Postseason" if int(d) >= int(TOURNEY_START) else "Regular Season"


def is_tournament(date_str=None):
    return get_season_type(date_str) == "Postseason"


def _today_yyyymmdd():
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/Chicago"))
    return now.strftime("%Y%m%d")
