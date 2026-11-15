# scripts/self_tune.py  -- thin wrapper around core/self_tune.py
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import defaults
import core.self_tune as _tune

_tune._configure(defaults)

_tune.HCA_CLAMP_LO = getattr(defaults, 'HCA_CLAMP_LO', 1.5)
_tune.HCA_CLAMP_HI = getattr(defaults, 'HCA_CLAMP_HI', 3.5)
_tune.HCA_VAR_FLOOR = getattr(defaults, 'HCA_VAR_FLOOR', 0.2)

# Re-export all public API
from core.self_tune import tune_weights, compute_residual_var
