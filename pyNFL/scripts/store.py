# scripts/store.py  --  thin wrapper around core/store.py
import sys, os
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import core.store as _store

_store.DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'nfl.json')

# Re-export public API
from core.store import load_store, save_store


def _matchup_key(game):
    return (str(game.get("away") or "").strip().lower(),
            str(game.get("home") or "").strip().lower())


def upsert_run(store, run):
    """Upsert a run by WEEK, carrying already-played games forward.

    core.upsert_run keys on the run's calendar date, which is right for a sport
    where one date is one slate. NFL projects the same week six times, so date
    keying left six entries for one week, and that broke two ways: the systems
    tally summed across every entry (Week 1 2026 showed 26 pending plays for 13
    games), and each rebuild quietly lost whatever had already kicked off, since
    a started game drops out of the odds feed. The 2026 Wednesday opener went
    that way and took a winning UNDER with it.

    So: one entry per weekKey, games merged by matchup. The incoming run wins
    for any game it carries; games only an earlier entry has are kept rather
    than dropped. Re-running the backfill for a week merges into that week for
    the same reason instead of replacing it wholesale.
    """
    if not isinstance(store.get("runs"), list):
        store["runs"] = []

    week_key = run.get("weekKey")
    if not week_key:
        return _store.upsert_run(store, run)

    try:
        run["ranAt"] = datetime.now(ZoneInfo("America/Chicago")).strftime("%m/%d/%Y, %I:%M:%S %p")
    except Exception:
        run["ranAt"] = datetime.now().strftime("%m/%d/%Y, %I:%M:%S %p")

    prior = [r for r in store["runs"] if r.get("weekKey") == week_key]
    if prior:
        merged = {}
        for r in sorted(prior, key=lambda r: r.get("date", "")):
            for g in r.get("games", []):
                merged[_matchup_key(g)] = g
        for g in run.get("games", []):
            merged[_matchup_key(g)] = g
        run["games"] = list(merged.values())
        store["runs"] = [r for r in store["runs"] if r.get("weekKey") != week_key]

    store["runs"].append(run)
    # Season/week, not the date string: the backfill writes a week label there
    # ("2025_W9"), which sorts lexicographically as W1, W10 ... W19, W2, W20 ...
    # W9, leaving Week 9 looking like the last week of the season.
    store["runs"].sort(key=lambda r: (r.get("season") or 0, r.get("week") or 0,
                                      str(r.get("date") or "")))
