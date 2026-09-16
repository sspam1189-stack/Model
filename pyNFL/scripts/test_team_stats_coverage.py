"""Every team that has played must appear in team_stats.

Written 2026-09-16, the third bug in a family found this session. The 2026
Week 2 board showed "PHI @ NE" for a game that is really PHI @ TEN, and
"LA @ TB" for CLE @ TB, plus two games left with unabbreviated names. All
four traced back to the same place: CLE, DEN, SEA and TEN were missing from
team_stats, so nothing downstream could resolve them.

They were missing because compute_team_stats_through_week drops a team with
fewer than _MIN_PLAYS_TEAM scrimmage plays, and that gate is ABSOLUTE. After
one week a normal team has run 46-86 scrimmage plays:

    DEN 46   SEA 47   TEN 49   CLE 49   <- dropped at a gate of 50
    MIA 50   LAC 51   TB 51             <- survived by a single play
    median 60, max 86 (NO)

So the gate was not protecting against a thin sample, it was deleting teams
for the crime of playing a low-possession game in Week 1. By Week 4 nobody
is near it and the bug disappears on its own -- which is why it has never
been noticed before the season's opening weeks.

The gate itself is worth keeping: EPA off a dozen plays is noise. It just
has to scale with how much football has actually been played.

Run:  cd pyNFL/scripts && python test_team_stats_coverage.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "sources"))

from sources.nflfastr import fetch_pbp                       # noqa: E402
from sources.nfl_stats import compute_team_stats_through_week  # noqa: E402

ALL32 = {
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN",
    "DET", "GB", "HOU", "IND", "JAX", "KC", "LA", "LAC", "LV", "MIA",
    "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB",
    "TEN", "WAS",
}

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


def run():
    pbp = fetch_pbp(2026)
    w1 = pbp[pbp["week"] <= 1]

    played = set(w1["posteam"].dropna().unique()) | set(w1["defteam"].dropna().unique())
    played = {t for t in played if isinstance(t, str) and t}
    check(played == ALL32,
          f"fixture problem: week 1 pbp should cover all 32, missing {sorted(ALL32 - played)}")

    # ---- 1. after one week, every team that played is present -----------
    ts = compute_team_stats_through_week(pbp, 1, decay=0.85)
    missing = sorted(played - set(ts))
    check(not missing,
          f"teams played week 1 but are absent from team_stats: {missing}")
    check(len(ts) == 32, f"expected 32 teams through week 1, got {len(ts)}")

    # ---- 2. a genuinely degenerate sample is still dropped --------------
    # The gate exists for a reason. Cut one team down to a handful of plays
    # and it must not survive, or the protection is gone rather than fixed.
    victim = "KC"
    keep_idx = w1.index[
        ((w1["posteam"] == victim) | (w1["defteam"] == victim))
    ][:6]
    other = w1[(w1["posteam"] != victim) & (w1["defteam"] != victim)]
    import pandas as pd
    starved = pd.concat([other, w1.loc[keep_idx]])
    ts2 = compute_team_stats_through_week(starved, 1, decay=0.85)
    check(victim not in ts2,
          f"{victim} had only 6 plays and must still be dropped, but survived")
    check(len(ts2) >= 30,
          f"starving one team should not cascade; got {len(ts2)} teams")


run()

if failures:
    print(f"FAILED ({len(failures)})")
    for f in failures:
        print("  " + f)
    sys.exit(1)
print("PASSED  all 32 present after one week, degenerate samples still dropped")
