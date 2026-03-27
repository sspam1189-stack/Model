# scripts/lr_model.py  --  thin wrapper around core/lr_model.py
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import defaults
import core.lr_model as _lr

_lr._configure(defaults)
_lr.MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'lr_models')

# Re-export public API
from core.lr_model import (
    build_team_histories,
    extract_lr_features,
    extract_team_lr_features,
    build_lr_features_for_game,
    LR_FEATURE_NAMES,
    train_lr_model,
    predict_lr,
    predict_lr_for_pick,
    load_or_train_lr,
)
