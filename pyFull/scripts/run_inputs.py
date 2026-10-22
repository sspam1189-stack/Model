# scripts/run_inputs.py
# Record every outside input a live run_daily reads, so a backfill can replay
# the exact same run through the exact same code.
#
# Live:   each input goes through capture(key, fn). fn runs as normal and its
#         result (or the exception it raised) is kept. At the end of the run
#         save() writes them, plus the run's start time, to
#         data/run_inputs/nba/<date>/<HHMMSS>.json.gz.
# Replay: begin_replay(path) loads one of those files. capture() then returns
#         the recorded value (or re-raises the recorded error) without calling
#         fn, the clock is frozen at the recorded start time, and sleeps are
#         skipped. replay_season.py drives this over every saved run in order.
#
# Only outside reads are recorded (feeds, the clock, cache files written by
# other jobs). Everything run_daily derives from its own history.json /
# kalman_state.json is recomputed on replay, which is what makes a replay a
# real test of the code rather than a copy of its output.

import datetime as _dt_mod
import gzip
import json
import os
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Chicago")
_HERE = os.path.dirname(os.path.abspath(__file__))
INPUTS_DIR = os.path.normpath(os.path.join(_HERE, "..", "..", "data", "run_inputs", "nba"))
VERSION = 1

_record = {}          # key -> {"v": value} | {"err": message}
_replay = None        # loaded snapshot while replaying
_started = None       # aware datetime the run started (Chicago)


def replaying():
    return _replay is not None


def start(now=None):
    """Mark the start of a live run (fixes the clock for this run)."""
    global _started, _record
    _record = {}
    _started = now or _dt_mod.datetime.now(TZ)
    return _started


def now():
    """The run's clock: the recorded start time on replay, the real time live."""
    if _replay is not None:
        return _started
    return _dt_mod.datetime.now(TZ)


def _to_json(v):
    if isinstance(v, set):
        return {"__set__": sorted(v, key=str)}
    if isinstance(v, tuple):
        return [_to_json(x) for x in v]
    if isinstance(v, list):
        return [_to_json(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _to_json(x) for k, x in v.items()}
    return v


def _from_json(v):
    if isinstance(v, dict):
        if set(v) == {"__set__"}:
            return set(v["__set__"])
        return {k: _from_json(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_from_json(x) for x in v]
    return v


class RecordedError(Exception):
    """A fetch that failed live fails the same way on replay."""


def capture(key, fn, *args, **kwargs):
    if _replay is not None:
        inputs = _replay["inputs"]
        if key not in inputs and callable(_replay.get("provider")):
            inputs[key] = _replay["provider"](key)
        if key not in inputs:
            raise KeyError(f"[replay] input '{key}' was not recorded for this run")
        rec = inputs[key]
        if "err" in rec:
            raise RecordedError(rec["err"])
        return _from_json(rec["v"])
    try:
        value = fn(*args, **kwargs)
    except Exception as e:
        _record[key] = {"err": str(e)}
        raise
    _record[key] = {"v": _to_json(value)}
    return value


def note(key, value):
    """Record run state for checking a replay against live (not served on replay)."""
    if _replay is None:
        _record.setdefault("_notes", {"v": {}})["v"][key] = _to_json(value)


def sleep(seconds):
    if _replay is None:
        import time
        time.sleep(seconds)


def save(date, status="ok", error=None):
    """Write this live run's inputs. Never raises: recording must not break a run."""
    if _replay is not None or _started is None:
        return None
    try:
        d = os.path.join(INPUTS_DIR, date)
        os.makedirs(d, exist_ok=True)
        stem = _started.strftime("%H%M%S")
        path = os.path.join(d, stem + ".json.gz")
        n = 2
        while os.path.exists(path):          # two runs in the same second
            path = os.path.join(d, f"{stem}-{n}.json.gz")
            n += 1
        body = {"version": VERSION, "date": date, "startedAt": _started.isoformat(),
                "status": status, "error": error, "inputs": _record}
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(body, f, separators=(",", ":"), sort_keys=True)
        print(f"  [inputs] saved {len(_record)} inputs -> {os.path.relpath(path, os.path.join(_HERE, '..', '..'))}")
        return path
    except Exception as e:
        print(f"  [inputs] save failed (non-fatal): {e}")
        return None


def load(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


class _FrozenDatetime(_dt_mod.datetime):
    @classmethod
    def now(cls, tz=None):
        t = _started
        if tz is None:
            return _dt_mod.datetime(t.year, t.month, t.day, t.hour, t.minute, t.second, t.microsecond)
        return t.astimezone(tz)


def begin_replay(snapshot, modules=()):
    """Serve inputs from snapshot and freeze datetime.now in the given modules."""
    global _replay, _started, _record
    _replay = snapshot
    _record = {}
    _started = _dt_mod.datetime.fromisoformat(snapshot["startedAt"])
    for m in modules:
        if getattr(m, "datetime", None) is not None:
            m.datetime = _FrozenDatetime


def end_replay(modules=()):
    global _replay
    _replay = None
    for m in modules:
        if getattr(m, "datetime", None) is _FrozenDatetime:
            m.datetime = _dt_mod.datetime
