"""Tests for _match_team, the join between the run store and ESPN finals.

Written 2026-09-16 after 10 of 16 2026 Week 1 games sat PENDING two days
after they were played. ESPN had all 16 finals; the store holds team
ABBREVIATIONS and ESPN returns full display names, and the matcher compared
them by substring. That only works when the abbreviation happens to be a
prefix of the city -- "ne" inside "new england", "sea" inside "seattle" --
so exactly the six games whose BOTH abbreviations were accidental substrings
got graded, and the other ten never did.

Run:  cd pyNFL/scripts && python test_match_team.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from run_weekly import _match_team          # noqa: E402

# (espn display name, store abbreviation) -- every 2026 Week 1 side.
WEEK1 = [
    ("New England Patriots", "NE"), ("Seattle Seahawks", "SEA"),
    ("San Francisco 49ers", "SF"), ("Los Angeles Rams", "LA"),
    ("Atlanta Falcons", "ATL"), ("Pittsburgh Steelers", "PIT"),
    ("Baltimore Ravens", "BAL"), ("Indianapolis Colts", "IND"),
    ("Buffalo Bills", "BUF"), ("Houston Texans", "HOU"),
    ("Chicago Bears", "CHI"), ("Carolina Panthers", "CAR"),
    ("Tampa Bay Buccaneers", "TB"), ("Cincinnati Bengals", "CIN"),
    ("Cleveland Browns", "CLE"), ("Jacksonville Jaguars", "JAX"),
    ("New Orleans Saints", "NO"), ("Detroit Lions", "DET"),
    ("New York Jets", "NYJ"), ("Tennessee Titans", "TEN"),
    ("Arizona Cardinals", "ARI"), ("Los Angeles Chargers", "LAC"),
    ("Green Bay Packers", "GB"), ("Minnesota Vikings", "MIN"),
    ("Miami Dolphins", "MIA"), ("Las Vegas Raiders", "LV"),
    ("Washington Commanders", "WAS"), ("Philadelphia Eagles", "PHI"),
    ("Dallas Cowboys", "DAL"), ("New York Giants", "NYG"),
    ("Denver Broncos", "DEN"), ("Kansas City Chiefs", "KC"),
]

# Pairs that must NEVER match.
MUST_NOT_MATCH = [
    # The worst of it: "LA" did not match EITHER Los Angeles club, but it did
    # match Philadelphia and Atlanta, because "la" sits inside "phi-la-delphia"
    # and "at-la-nta". A Rams pick could have been settled off a Falcons final.
    ("Philadelphia Eagles", "LA"),
    ("Atlanta Falcons", "LA"),
    ("Los Angeles Chargers", "LA"),
    ("Los Angeles Rams", "LAC"),
    ("New York Giants", "NYJ"),
    ("New York Jets", "NYG"),
    ("Kansas City Chiefs", "KAN"),
    ("Seattle Seahawks", "SF"),
]

failures = []

for espn, abbr in WEEK1:
    if not _match_team(espn, abbr):
        failures.append(f"MISS  {espn!r} should match store abbreviation {abbr!r}")

for espn, abbr in MUST_NOT_MATCH:
    if _match_team(espn, abbr):
        failures.append(f"FALSE {espn!r} must NOT match {abbr!r}")

# Full names on both sides must still work -- that is how 2023-2025 graded.
for espn, _ in WEEK1:
    if not _match_team(espn, espn):
        failures.append(f"SELF  {espn!r} should match itself")

if failures:
    print(f"FAILED ({len(failures)})")
    for f in failures:
        print("  " + f)
    sys.exit(1)

print(f"PASSED  {len(WEEK1)} abbreviation joins, "
      f"{len(MUST_NOT_MATCH)} negative cases, {len(WEEK1)} self-joins")
