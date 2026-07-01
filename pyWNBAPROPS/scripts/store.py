# pyNBAPROPS/scripts/store.py
# Simple JSON persistence for prop results history.

import os
import json
from datetime import datetime


class PropStore:
    """Persist daily prop picks and graded results."""

    def __init__(self, path=None):
        if path is None:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            path = os.path.join(script_dir, "..", "data", "props_history.json")
        self.path = os.path.normpath(path)
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"runs": [], "meta": {"version": "1.0"}}

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self.data, f, indent=2)

    def add_run(self, date_str, picks, summary=""):
        """Add a daily run's picks to history."""
        self.data["runs"].append({
            "date": date_str,
            "generated": datetime.now().isoformat(),
            "picks": picks,
            "summary": summary,
        })
        self.save()

    def get_recent_runs(self, n=10):
        """Get last N runs."""
        return self.data["runs"][-n:]

    def get_ungraded_picks(self):
        """Get picks that haven't been graded yet."""
        ungraded = []
        for run in self.data["runs"]:
            for pick in run.get("picks", []):
                if pick.get("result") is None:
                    ungraded.append(pick)
        return ungraded
