from datetime import datetime
import pytz


# WNBA: the playoff-override machinery (empirical HCA, per-team HCA, probHigh
# floor) in run_daily/backfill is NBA-backtested and NOT validated for the
# WNBA's short, thin playoffs. Per the design spec ("NBA conclusions are NOT
# inherited"), we keep the ENTIRE WNBA season classified as Regular Season by
# parking PLAYOFF_START past the season end. The WNBA playoffs still fetch fine
# from nba_api under season_type='Regular Season' for the regular-season stat
# base; postseason games simply aren't given special HCA treatment.
# Revisit only if a WNBA-specific playoff backtest justifies it.
PLAYOFF_START = "29990101"  # effectively disabled — treat all WNBA dates as Regular Season


def _today_yyyymmdd():
    tz = pytz.timezone("America/Chicago")
    now = datetime.now(tz)
    return now.strftime("%Y%m%d")


def get_season_type(date_str=None):
    """
    nba_api season_type value: "Regular Season" or "Playoffs".
    WNBA: always "Regular Season" (see PLAYOFF_START note above).
    """
    d = (date_str or _today_yyyymmdd()).replace("-", "")
    return "Playoffs" if int(d) >= int(PLAYOFF_START) else "Regular Season"


def get_espn_season_type(date_str=None):
    """ESPN API value: 2 (regular season) or 3 (playoffs)"""
    return 3 if get_season_type(date_str) == "Playoffs" else 2


def is_playoffs(date_str=None):
    return get_season_type(date_str) == "Playoffs"
