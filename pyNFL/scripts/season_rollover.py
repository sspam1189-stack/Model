# pyNFL/scripts/season_rollover.py -- thin wrapper around core/season_rollover.py
#
# DELIBERATELY NARROWER THAN THE NBA WRAPPER.
#
# Roll:  data/team_states/kalman_state.json
#        Team-strength offsets are roster- and scheme-specific and the filter
#        never resets or mean-reverts on its own, so last season's offsets
#        (currently up to 13.26 pts) would price Week 1 on last year's teams.
#
# Do NOT roll: data/nfl.json
#        It holds 2023_W1..2025_W22 in ONE store on purpose - the ridge model
#        in model_engine.py trains across seasons, so wiping it each September
#        would destroy the training set. This is the opposite of NBA, where
#        history.json is a single-season pick record. Leave it alone.
#
# Do NOT roll: data/nfl-props.json
#        Same reason - it is the multi-season backtest pool the prop
#        thresholds were swept on.
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from core.season_rollover import roll_season, season_label

_HERE = os.path.dirname(os.path.abspath(__file__))

TARGETS = [
    {"path": os.path.join(_HERE, "..", "data", "team_states", "kalman_state.json"),
     "kind": "kalman"},
]

# NFL regular season opens in September.
NFL_SEASON_START_MONTH = 9


def roll_if_new_season(date_key):
    """Archive + reset the team Kalman when date_key lands in a new NFL season."""
    return roll_season(date_key, TARGETS, start_month=NFL_SEASON_START_MONTH)
