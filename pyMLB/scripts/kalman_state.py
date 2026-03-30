# pyMLB/scripts/kalman_state.py -- thin wrapper around core/kalman_state.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import defaults
import core.kalman_state as _ks
_ks.STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'kalman_state.json')
_ks.KALMAN_DEFAULTS = defaults.KALMAN_DEFAULTS
from core.kalman_state import (
    load_kalman_state, save_kalman_state, initialize_kalman,
    apply_daily_drift, batch_update, kalman_summary, prune_processed_games,
)
