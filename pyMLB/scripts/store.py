# pyMLB/scripts/store.py -- thin wrapper around core/store.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import core.store as _store
_store.DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'mlb_history.json')
from core.store import load_store, save_store, upsert_run
