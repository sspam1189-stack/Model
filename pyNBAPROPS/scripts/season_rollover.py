# pyNBAPROPS/scripts/season_rollover.py -- thin wrapper around core/season_rollover.py
#
# NBA player-prop state that must NOT survive into a new season:
#   kalman_state.json  per-player baselines — rosters, roles and minutes all
#                      turn over, and the filter never resets on its own
#   nba-props.json     the graded pick record (both copies), so season ROI and
#                      the dashboard start from zero
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from core.season_rollover import roll_season, season_label

_HERE = os.path.dirname(os.path.abspath(__file__))

TARGETS = [
    {"path": os.path.join(_HERE, "..", "data", "kalman_state.json"), "kind": "kalman"},
    {"path": os.path.join(_HERE, "..", "data", "nba-props.json"),    "kind": "picks"},
    {"path": os.path.join(_HERE, "..", "..", "PythonDashboard", "data", "nba-props.json"),
     "kind": "picks"},
]

NBA_SEASON_START_MONTH = 10


def roll_if_new_season(date_key):
    """Archive + reset season state when date_key lands in a new NBA season."""
    return roll_season(date_key, TARGETS, start_month=NBA_SEASON_START_MONTH)
