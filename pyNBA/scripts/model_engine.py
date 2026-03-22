# scripts/model_engine.py  -- thin wrapper around core/engines/nba.py
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from core.engines.nba import create_nba_engine
from defaults import DEFAULT_STATS, DEFAULT_W, DEFAULT_W_VAR, BAYES_HYPER, ENGINE_CONFIG

_engine = create_nba_engine(DEFAULT_STATS, DEFAULT_W, DEFAULT_W_VAR, BAYES_HYPER, ENGINE_CONFIG)

load_defaults = _engine.load_defaults
get_avgs = _engine.get_avgs
normal_cdf = _engine.normal_cdf
proj_score = _engine.proj_score
proj_total = _engine.proj_total
extract_margin_features = _engine.extract_margin_features
analyze_game = _engine.analyze_game
