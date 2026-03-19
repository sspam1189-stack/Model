# scripts/store.py

import json
import os
import warnings
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from pytz import timezone as _pytz_tz

    class ZoneInfo:
        def __init__(self, name):
            self._tz = _pytz_tz(name)

        def __repr__(self):
            return f"ZoneInfo('{self._tz.zone}')"

DATA = os.path.join(os.getcwd(), "data", "history.json")

EMPTY_STORE = {"runs": [], "weights": {}}


def load_store():
    try:
        with open(DATA, "r", encoding="utf-8") as f:
            store = json.load(f)
        w = store.get("weights") or {}
        has_nulls = any(v is None for v in w.values())
        if has_nulls or len(w) == 0:
            warnings.warn("store.py: weights null or missing -- resetting to defaults")
            store["weights"] = None
        if not isinstance(store.get("runs"), list):
            store["runs"] = []
        return store
    except Exception:
        warnings.warn("store.py: history.json not found or invalid -- starting fresh")
        os.makedirs(os.path.dirname(DATA), exist_ok=True)
        with open(DATA, "w", encoding="utf-8") as f:
            json.dump(EMPTY_STORE, f, indent=2)
        return {**EMPTY_STORE, "runs": []}


def save_store(store):
    os.makedirs(os.path.dirname(DATA), exist_ok=True)
    with open(DATA, "w", encoding="utf-8") as f:
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
