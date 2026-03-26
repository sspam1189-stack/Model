# core/engines/ncaa.py
from core.model_engine import create_model_engine


def create_ncaa_engine(DEFAULT_STATS, DEFAULT_W, DEFAULT_W_VAR, BAYES_HYPER, ENGINE_CONFIG, is_tournament=None):
    return create_model_engine(
        DEFAULT_STATS,
        DEFAULT_W,
        DEFAULT_W_VAR,
        BAYES_HYPER,
        engine_config=ENGINE_CONFIG,
        enable_team_hca=True,
        is_neutral_site=(lambda g: is_tournament(g.get("_date"))) if is_tournament else None,
        bayes={
            "spread": {
                "mode": "fixed",
                "threshold": 0.60,
                "fav_line_cap": 8,
                "require_line_nonzero": True,
                "use_s_diff": False,
                "abs_line_cap": None,
            },
            "totals": {
                "enabled": False,
            },
        },
        legacy={
            "spread": {
                "min_diff_floor": 3,
                "diff_cap": 9,
                "abs_line_cap": 10,
                "abs_line_cap_inclusive": True,
            },
        },
    )
