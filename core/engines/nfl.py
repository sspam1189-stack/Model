# core/engines/nfl.py
# NFL-specific configuration for core modules (self_tune, kalman_state).
#
# This does NOT configure core/model_engine.py's proj_score — the NFL engine
# uses standalone ridge regression on EPA features (pyNFL/scripts/model_engine.py),
# which is a fundamentally different approach from the NBA's weighted stat deltas.
#
# This module configures:
#   - core/self_tune.py weight keys for Bayesian updating
#   - core/kalman_state.py hyperparameters (higher process noise for weekly variance)
#   - Bayesian hyper settings tuned for NFL score margins

import core.self_tune as self_tune
import core.kalman_state as kalman_state


# ---------------------------------------------------------------------------
# NFL Weight Keys
# ---------------------------------------------------------------------------
# These are the Bayesian-tunable weight keys for the NFL model.
# The ridge regression provides initial coefficients; self_tune refines them
# in-season via Bayesian posterior updates.

NFL_WEIGHT_KEYS = [
    "wPassOff",   # Offensive passing EPA weight
    "wRushOff",   # Offensive rushing EPA weight
    "wPassDef",   # Defensive passing EPA weight
    "wRushDef",   # Defensive rushing EPA weight
    "wPace",      # Pace interaction weight
    "wRZ",        # Red zone efficiency weight
    "hfa",        # Home field advantage (points)
]


# ---------------------------------------------------------------------------
# NFL Kalman Defaults
# ---------------------------------------------------------------------------
# NFL has higher process noise than NBA because:
#   - Games are weekly (not daily), so each game carries more variance
#   - Injuries cause regime changes between weeks
#   - 17-game season means each data point is ~6x more impactful than NBA
#
# initialVar is higher because early-season NFL estimates are very uncertain
# (only 1-2 games of data vs NBA's 5-10).

NFL_KALMAN_DEFAULTS = {
    "initialVar": 36,       # ~6 pt std dev initially (NBA uses 16 = 4pt)
    "gameNoise": 225,       # ~15 pt std dev per game (NFL margins are wider)
    "dailyDrift": 0.5,      # Higher drift: weekly regime changes from injuries
    "minVar": 4.0,          # Don't let variance collapse too much (small sample)
    "maxVar": 50,           # Allow wide uncertainty early season
}


# ---------------------------------------------------------------------------
# NFL Bayesian Hyper
# ---------------------------------------------------------------------------
# Tuned for NFL score margins:
#   - NFL margins have ~14 pt std dev -> ~196 variance
#   - Residual variance is higher than NBA (more randomness per game)

NFL_BAYES_HYPER = {
    "marginNoise": 196,     # NFL margin std ~14 pts -> 196 variance
    "minWeightVar": 0.05,   # Same floor as NBA
    "maxWeightVar": 12,     # Slightly higher cap (fewer data points)
    "residualVar": 180,     # Irreducible noise — NFL is noisier than NBA
}


# ---------------------------------------------------------------------------
# HCA clamp range for self_tune (NFL-specific)
# ---------------------------------------------------------------------------
# NFL home field advantage has been declining in recent years (~2-3 pts).
# Tighter clamp range than NBA.

NFL_HCA_CLAMP_LO = 1.0
NFL_HCA_CLAMP_HI = 4.0
NFL_HCA_VAR_FLOOR = 0.3


def configure_core_modules(defaults_module):
    """
    Configure core/self_tune.py and core/kalman_state.py with NFL-specific
    settings from a defaults module (pyNFL/scripts/defaults.py).

    This is called once at pipeline startup.

    Parameters
    ----------
    defaults_module : module
        Must expose DEFAULT_W, DEFAULT_W_VAR, BAYES_HYPER, KALMAN_DEFAULTS.
    """
    # Configure self_tune with NFL weight keys and Bayesian hyper
    self_tune._configure(defaults_module)

    # Override HCA clamp range for NFL
    self_tune.HCA_CLAMP_LO = NFL_HCA_CLAMP_LO
    self_tune.HCA_CLAMP_HI = NFL_HCA_CLAMP_HI
    self_tune.HCA_VAR_FLOOR = NFL_HCA_VAR_FLOOR

    # Configure kalman_state with NFL defaults
    kalman_defaults = getattr(defaults_module, "KALMAN_DEFAULTS", NFL_KALMAN_DEFAULTS)
    kalman_state.KALMAN_DEFAULTS = kalman_defaults


def create_nfl_engine(defaults_module):
    """
    Full NFL engine configuration. Configures core modules and returns
    a namespace with NFL-specific Kalman and Bayes settings for use
    by the orchestrator.

    Parameters
    ----------
    defaults_module : module
        The pyNFL/scripts/defaults.py module.

    Returns
    -------
    dict
        Configuration summary for the caller.
    """
    configure_core_modules(defaults_module)

    return {
        "weight_keys": NFL_WEIGHT_KEYS,
        "kalman_defaults": kalman_defaults_for(defaults_module),
        "bayes_hyper": bayes_hyper_for(defaults_module),
        "hca_clamp": (NFL_HCA_CLAMP_LO, NFL_HCA_CLAMP_HI),
    }


def kalman_defaults_for(defaults_module):
    """Return the Kalman defaults, preferring the defaults module's value."""
    return getattr(defaults_module, "KALMAN_DEFAULTS", NFL_KALMAN_DEFAULTS)


def bayes_hyper_for(defaults_module):
    """Return the Bayes hyper, preferring the defaults module's value."""
    return getattr(defaults_module, "BAYES_HYPER", NFL_BAYES_HYPER)
