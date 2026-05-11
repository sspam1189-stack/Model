# core/engines/nba.py
from core.model_engine import create_model_engine


def create_nba_engine(DEFAULT_STATS, DEFAULT_W, DEFAULT_W_VAR, BAYES_HYPER, ENGINE_CONFIG):
    return create_model_engine(
        DEFAULT_STATS,
        DEFAULT_W,
        DEFAULT_W_VAR,
        BAYES_HYPER,
        engine_config=ENGINE_CONFIG,
        enable_team_hca=True,   # team-specific HCA — engine only applies it when caller passes a team_hca dict (run_daily wires it in playoffs only)
        bayes={
            "spread": {
                "s_diff_cap": None,
                "abs_line_cap": 13,
                "abs_line_cap_inclusive": False,
            },
            "totals": {"enabled": False},
        },
    )
