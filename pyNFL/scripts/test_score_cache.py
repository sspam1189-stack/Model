"""Tests for the ESPN score cache's "this week is complete" decision.

Written 2026-09-16, alongside test_match_team.py, after the team-name join
bug left ten 2026 Week 1 games PENDING. This is a SECOND defect with the
same symptom, found while fixing the first.

fetch_week_scores caches permanently once it believes every game is final:

    cached = _load_cache(cp, max_age_hours=None)   # optimistic
    if cached is not None:
        if <week is complete>:
            return cached                          # never expires

The old completeness test asked whether every entry in the cached list had
scores. But the list it was handed came from extract_final_scores, which
DROPS non-final games -- so the question was trivially "do the final games
have scores", answer always yes. A fetch landing mid-Sunday with 6 of 16
played cached those 6 as a complete week, forever, and the 2-hour partial
path below it was unreachable.

Run:  cd pyNFL/scripts && python test_score_cache.py
"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "sources"))

import espn_scoreboard as E          # noqa: E402

TEAMS = [
    ("New England Patriots", "Seattle Seahawks"),
    ("San Francisco 49ers", "Los Angeles Rams"),
    ("Atlanta Falcons", "Pittsburgh Steelers"),
    ("Baltimore Ravens", "Indianapolis Colts"),
    ("Buffalo Bills", "Houston Texans"),
    ("Chicago Bears", "Carolina Panthers"),
    ("Tampa Bay Buccaneers", "Cincinnati Bengals"),
    ("Cleveland Browns", "Jacksonville Jaguars"),
    ("New Orleans Saints", "Detroit Lions"),
    ("New York Jets", "Tennessee Titans"),
    ("Arizona Cardinals", "Los Angeles Chargers"),
    ("Green Bay Packers", "Minnesota Vikings"),
    ("Miami Dolphins", "Las Vegas Raiders"),
    ("Washington Commanders", "Philadelphia Eagles"),
    ("Dallas Cowboys", "New York Giants"),
    ("Denver Broncos", "Kansas City Chiefs"),
]


def scoreboard(n_final, n_total=16, week=1):
    """A synthetic ESPN scoreboard payload: n_final of n_total games played."""
    events = []
    for i, (away, home) in enumerate(TEAMS[:n_total]):
        final = i < n_final
        events.append({
            "id": str(100 + i),
            "date": "2026-09-13T17:00:00Z",
            "week": {"number": week},
            "competitions": [{
                "date": "2026-09-13T17:00:00Z",
                "status": {"type": {"name": "STATUS_FINAL" if final
                                    else "STATUS_SCHEDULED"}},
                "competitors": [
                    {"homeAway": "away", "team": {"displayName": away},
                     "score": "20" if final else None},
                    {"homeAway": "home", "team": {"displayName": home},
                     "score": "17" if final else None},
                ],
            }],
        })
    return {"events": events}


failures = []
fetches = {"n": 0}


def check(cond, msg):
    if not cond:
        failures.append(msg)


def run():
    tmp = Path(tempfile.mkdtemp(prefix="espn_cache_test_"))
    real_dir, real_fetch = E._CACHE_DIR, E.fetch_nfl_scoreboard
    E._CACHE_DIR = tmp
    try:
        # ---- 1. a half-played slate must NOT be cached as complete --------
        E.fetch_nfl_scoreboard = lambda **kw: (fetches.__setitem__("n", fetches["n"] + 1)
                                               or scoreboard(6))
        fetches["n"] = 0
        first = E.fetch_week_scores(1, season=2026)
        check(len(first) == 6, f"partial fetch should return 6 finals, got {len(first)}")
        check(fetches["n"] == 1, "first call should hit the network once")

        # Second call, same partial state. It may serve the 2-hour partial
        # cache -- that is fine. What must NOT happen is the week being
        # sealed as permanent, so once the rest are played they show up.
        E.fetch_nfl_scoreboard = lambda **kw: (fetches.__setitem__("n", fetches["n"] + 1)
                                               or scoreboard(16))
        cp = E._cache_path(2026, 1)
        # age the cache past the 2-hour partial window
        old = time.time() - 3 * 3600
        os.utime(cp, (old, old))
        again = E.fetch_week_scores(1, season=2026)
        check(len(again) == 16,
              f"after the rest were played the cache must refresh to 16, got {len(again)}")
        check(fetches["n"] == 2, "a stale partial cache must refetch")

        # ---- 2. a genuinely complete week SHOULD cache permanently -------
        E.fetch_nfl_scoreboard = lambda **kw: (fetches.__setitem__("n", fetches["n"] + 1)
                                               or scoreboard(16, week=2))
        fetches["n"] = 0
        E.fetch_week_scores(2, season=2026)
        check(fetches["n"] == 1, "complete week: first call fetches")
        cp2 = E._cache_path(2026, 2)
        old = time.time() - 30 * 24 * 3600      # a month stale
        os.utime(cp2, (old, old))
        done = E.fetch_week_scores(2, season=2026)
        check(len(done) == 16, f"complete week should return 16, got {len(done)}")
        check(fetches["n"] == 1,
              "a complete week must never refetch, however old the cache is")

        # ---- 3. legacy bare-list cache files stay readable ----------------
        # 66 of these are committed for 2023-25. They are all finished weeks,
        # so they must keep being treated as permanent rather than refetched.
        cp3 = E._cache_path(2025, 5)
        legacy = E.extract_final_scores(scoreboard(16, week=5))
        E._save_cache(legacy, cp3)              # bare list, the old shape
        fetches["n"] = 0
        E.fetch_nfl_scoreboard = lambda **kw: (fetches.__setitem__("n", fetches["n"] + 1)
                                               or scoreboard(16, week=5))
        old = time.time() - 365 * 24 * 3600
        os.utime(cp3, (old, old))
        legacy_out = E.fetch_week_scores(5, season=2025)
        check(len(legacy_out) == 16,
              f"legacy list cache should return 16, got {len(legacy_out)}")
        check(fetches["n"] == 0, "legacy list cache must not refetch")

        # ---- 4. the returned shape is unchanged for callers ---------------
        for g in done:
            check(set(("away", "home", "awayScore", "homeScore")) <= set(g),
                  f"score dict missing keys: {sorted(g)}")
            break
    finally:
        E._CACHE_DIR, E.fetch_nfl_scoreboard = real_dir, real_fetch
        shutil.rmtree(tmp, ignore_errors=True)


run()

if failures:
    print(f"FAILED ({len(failures)})")
    for f in failures:
        print("  " + f)
    sys.exit(1)
print("PASSED  partial slate refreshes, complete week seals, legacy caches honoured")
