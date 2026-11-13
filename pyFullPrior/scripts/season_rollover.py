# scripts/season_rollover.py  -- thin wrapper around core/season_rollover.py
#
# NBA full-season state that must NOT survive into a new season:
#   kalman_state.json  team-strength offsets — roster-specific, and the filter
#                      never resets or mean-reverts on its own
#   history.json       the run/pick record — otherwise last season's games feed
#                      the season record, residualVar and the tuner window
#
# Tuned weights are deliberately KEPT (see core/season_rollover._reset): they
# are model parameters, not season state.
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from core.season_rollover import roll_season, season_label

_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')

TARGETS = [
    {"path": os.path.join(_DATA, "kalman_state.json"), "kind": "kalman"},
    {"path": os.path.join(_DATA, "history.json"),      "kind": "store"},
]

NBA_SEASON_START_MONTH = 10


def roll_if_new_season(date_key):
    """Archive + reset season state when date_key lands in a new NBA season."""
    return roll_season(date_key, TARGETS, start_month=NBA_SEASON_START_MONTH)
