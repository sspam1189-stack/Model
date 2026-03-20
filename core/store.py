# core/store.py
# Unified store module for all models.

import json
import os
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
except ImportError:
    import pytz

    class ZoneInfo:
        def __init__(self, name):
            self._tz = pytz.timezone(name)

        def __repr__(self):
            return f"ZoneInfo('{self._tz.zone}')"

# -- Configurable (set by wrapper) --
DATA_PATH = None  # Must be set by wrapper before use

EMPTY_STORE = {"runs": [], "weights": {}}


def _get_data_path():
    if DATA_PATH:
        return DATA_PATH
    raise RuntimeError("core.store.DATA_PATH not configured. Set it from the sport wrapper.")


def load_store():
    path = _get_data_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            store = json.load(f)
        w = store.get("weights") or {}
        has_nulls = any(v is None for v in w.values())
        if has_nulls or len(w) == 0:
            print("store.py: weights null or missing -- resetting to defaults")
            store["weights"] = None
        if not isinstance(store.get("runs"), list):
            store["runs"] = []
        return store
    except Exception:
        print("store.py: history.json not found or invalid -- starting fresh")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(EMPTY_STORE, f, indent=2)
        return {**EMPTY_STORE, "runs": []}


def save_store(store):
    path = _get_data_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2)


def upsert_run(store, run):
    if not isinstance(store.get("runs"), list):
        store["runs"] = []
    try:
        tz = ZoneInfo("America/Chicago")
        run["ranAt"] = datetime.now(tz).strftime("%m/%d/%Y, %I:%M:%S %p")
    except Exception:
        run["ranAt"] = datetime.now().strftime("%m/%d/%Y, %I:%M:%S %p")

    idx = next((i for i, r in enumerate(store["runs"]) if r.get("date") == run.get("date")), -1)
    if idx >= 0:
        store["runs"][idx] = run
    else:
        store["runs"].append(run)
    store["runs"].sort(key=lambda r: r.get("date", ""))
