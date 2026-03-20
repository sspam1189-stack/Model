# scripts/self_tune.py  --  thin wrapper around core/self_tune.py
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import defaults
import core.self_tune as _tune

_tune._configure(defaults)

_tune.HCA_CLAMP_LO = defaults.HCA_CLAMP_LO
_tune.HCA_CLAMP_HI = defaults.HCA_CLAMP_HI
_tune.HCA_VAR_FLOOR = defaults.HCA_VAR_FLOOR

# Re-export public API
from core.self_tune import tune_weights, compute_residual_var
