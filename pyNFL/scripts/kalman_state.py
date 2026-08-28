# scripts/kalman_state.py  --  thin wrapper around core/kalman_state.py
#
# !! THE NFL TEAM KALMAN IS CURRENTLY NOT CONSUMED BY THE PICK ENGINE. !!
# engine_v2.analyze_game -- which both run_weekly and backfill_last_n_weeks use
# -- takes kalman_states "for signature compatibility and ignored", because the
# team stats are already exponentially decayed. Only the legacy
# model_engine.analyze_game folds the offsets into proj_home/proj_away, and
# nothing calls it.
#
# So the adj_mean values in data/team_states/kalman_state.json are computed and
# persisted but never priced. Do NOT read them as model inputs: they currently
# random-walk to +/-27 points because the filter has no restoring force and
# nothing downstream constrains it. If the Kalman layer is ever re-enabled,
# retune KALMAN_DEFAULTS (dailyDrift 0.5 / maxVar 50 keeps the gain pinned
# near 15%/game) before trusting the output.
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import defaults
import core.kalman_state as _ks

_ks.STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'team_states', 'kalman_state.json')
_ks.KALMAN_DEFAULTS = defaults.KALMAN_DEFAULTS
# NFL opens in September, so a Sept-Dec date belongs to THAT year's season.
# With the NBA default (October) initialize_kalman would label the 2026 season
# "2025-26" and the season-rollover check would never fire.
_ks.SEASON_START_MONTH = 9

# Re-export public API
from core.kalman_state import (
    load_kalman_state,
    save_kalman_state,
    initialize_kalman,
    apply_daily_drift,
    batch_update,
    kalman_summary,
    prune_processed_games,
)
