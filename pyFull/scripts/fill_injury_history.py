#!/usr/bin/env python3
# scripts/fill_injury_history.py
# Fill data/injury_cache/nba/<date>.json for past game days that have none,
# so a backfill (replay_season.py) applies injuries the way live does.
#
# Built with live's own code, as of each date:
#   report       ESPN per-game injury lists for that date
#                (injuries._fetch_game_availability, same status rules)
#   playerMPG    NBA.com minutes per game through the day before
#   playerAdvanced NBA.com player advanced stats through the day before
# then injuries.build_injury_report attaches mpg + tier, as live does.
#
# Not available for past dates: ESPN's league-wide injuries page (live merges
# it in; it only shows today) and the out-for-season list. Existing files are
# never overwritten. Needs ESPN + stats.nba.com, so run it on the self-hosted
# runner (fill-injury-history workflow).
#
# Usage (from pyFull/scripts):
#   python fill_injury_history.py --from 20251021 --to 20260318

import argparse
import datetime as dt
import json
import os
import sys
import time

from sources.injuries import _fetch_game_availability, build_injury_report, fetch_player_mpg
from sources.lineup_adjust import fetch_player_advanced

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.normpath(os.path.join(HERE, "..", "..", "data"))
INJ_DIR = os.path.join(DATA, "injury_cache", "nba")
ESPN_DIR = os.path.join(DATA, "espn_cache", "nba")


def _days(d0, d1):
    a = dt.date(int(d0[:4]), int(d0[4:6]), int(d0[6:]))
    b = dt.date(int(d1[:4]), int(d1[4:6]), int(d1[6:]))
    while a <= b:
        yield a
        a += dt.timedelta(days=1)


def _has_games(d):
    try:
        with open(os.path.join(ESPN_DIR, d.strftime("%Y%m%d") + ".json"), encoding="utf-8") as f:
            return bool(json.load(f).get("events"))
    except Exception:
        return True  # no saved scoreboard: ask ESPN


def _season(d):
    s = d.year if d.month >= 10 else d.year - 1
    return f"{s}-{str(s + 1)[2:]}"


def _retry(fn, tries=3, wait=10):
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            if i == tries - 1:
                raise
            print(f"    retry {i + 1}: {e}")
            time.sleep(wait)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d0", default="20251021")
    ap.add_argument("--to", dest="d1", default="20260318")
    a = ap.parse_args()
    os.makedirs(INJ_DIR, exist_ok=True)

    done = skipped = empty_streak = 0
    for d in _days(a.d0, a.d1):
        key = d.strftime("%Y%m%d")
        path = os.path.join(INJ_DIR, key + ".json")
        if os.path.exists(path) or not _has_games(d):
            skipped += 1
            continue
        day_before = (d - dt.timedelta(days=1)).strftime("%m/%d/%Y")
        season = _season(d)
        print(f"[fill] {key} (stats through {day_before}, {season})", flush=True)

        availability = _retry(lambda: _fetch_game_availability(date=key))
        time.sleep(2)
        mpg = _retry(lambda: fetch_player_mpg(season=season, date_to=day_before))
        time.sleep(2)
        try:
            adv = _retry(lambda: fetch_player_advanced(season=season, date_to=day_before))
        except Exception as e:
            print(f"    player advanced failed: {e}")
            adv = {}
        if not mpg:
            print(f"    no minutes data for {key}; not writing (rerun later)")
            continue

        report = build_injury_report(availability, mpg)
        n = sum(len(v) for v in report.values())
        empty_streak = empty_streak + 1 if n == 0 else 0
        if empty_streak >= 5:
            print("ESPN returned no injuries for 5 game days in a row -- its summaries for these "
                  "dates may not carry injury lists. Stopping so nothing misleading is written.")
            return 1
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"report": report, "playerMPG": mpg, "playerAdvanced": adv,
                       "_filled": {"by": "fill_injury_history.py", "asOf": day_before,
                                   "source": "ESPN game summaries + NBA.com (through day before)"}}, f)
        done += 1
        print(f"    wrote {n} listed players across {len(report)} teams, "
              f"{len(mpg)} players' minutes, {len(adv)} players' advanced stats", flush=True)
        time.sleep(2)

    print(f"\nFilled {done} day(s); skipped {skipped} (already saved or no games).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
