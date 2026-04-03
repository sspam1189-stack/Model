# core/engines/fullseason.py
from core.model_engine import create_model_engine


def create_fullseason_engine(DEFAULT_STATS, DEFAULT_W, DEFAULT_W_VAR, BAYES_HYPER, ENGINE_CONFIG):
    return create_model_engine(
        DEFAULT_STATS,
        DEFAULT_W,
        DEFAULT_W_VAR,
        BAYES_HYPER,
        engine_config=ENGINE_CONFIG,
        enable_h2h=True,
        bayes={
            "spread": {
                "s_diff_cap": None,
                "abs_line_cap": 18,
                "abs_line_cap_inclusive": False,
            },
            "totals": {"enabled": False},
        },
    )
