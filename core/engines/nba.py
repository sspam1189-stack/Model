# core/engines/nba.py
from core.model_engine import create_model_engine


def create_nba_engine(DEFAULT_STATS, DEFAULT_W, DEFAULT_W_VAR, BAYES_HYPER, ENGINE_CONFIG):
    return create_model_engine(
        DEFAULT_STATS,
        DEFAULT_W,
        DEFAULT_W_VAR,
        BAYES_HYPER,
        engine_config=ENGINE_CONFIG,
        bayes={
            "spread": {
                "s_diff_cap": 9,
                "abs_line_cap": 12,
                "abs_line_cap_inclusive": False,
            },
        },
        legacy={
            "spread": {
                "diff_cap": 9,
                "abs_line_cap": 12,
                "abs_line_cap_inclusive": False,
            },
        },
    )
