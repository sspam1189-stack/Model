#!/usr/bin/env python3
"""
MLBstrikeouts/scripts/build_pbp_team_pa.py
TRUE all-pitcher team offense splits source (play-by-play).

The old team-offense splits (build_team_woba_splits.py) read game-level batter
logs tagged with only the opposing STARTER's hand, so every PA in a game was
attributed to the starter -- lefty relievers were invisible. This ingests the
play-by-play (statsapi /game/{pk}/playByPlay) and tallies each PLATE APPEARANCE
under the ACTUAL pitcher's hand, split by the batting team's home/away side.

Output (data/pitcher_cache/mlb/team_pa_splits_2026.json): a list of rows
    {game_date, team, opp_hand ('L'/'R'), is_home, pa, ab, h, doubles, triples,
     hr, bb, hbp}
-- one row per (game, batting team, pitcher hand). A game yields up to 4 rows
(2 teams x 2 hands). build_team_woba_splits.py consumes these instead of the
starter-attributed game logs, keeping the same wOBA/wRC+ math, windows, and
home/away cross -- but now vs-LHP/vs-RHP is TRUE (starters + relievers).

Incremental: processed gamePks are cached so re-runs only fetch new games
(the daily pipeline fetches just today's slate).

Usage:
    cd MLBstrikeouts
    python -m scripts.build_pbp_team_pa            # full season backfill
    python -m scripts.build_pbp_team_pa --date 2026-08-03
"""
import os
import sys
import json
import time
import argparse
import urllib.request
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources"))
from sources.mlb_stats import MLB_TEAM_ID_TO_ABBR

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DATA = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "data"))
CACHE_DIR = os.path.join(ROOT_DATA, "pitcher_cache", "mlb")
OUT_PATH = os.path.join(CACHE_DIR, "team_pa_splits_2026.json")
DONE_PATH = os.path.join(CACHE_DIR, "team_pa_splits_2026_done.json")
# Per-batter SEASON totals by (batter, hand, venue, role) -- powers the
# "Confirmed Lineup" wRC+ window (aggregate tonight's 9 confirmed hitters).
BAT_PATH = os.path.join(CACHE_DIR, "batter_pa_splits_2026.json")
BASE = "https://statsapi.mlb.com/api/v1"
SEASON = 2026
SEASON_START, SEASON_END = f"{SEASON}-03-01", f"{SEASON}-11-30"

HITS = {"single", "double", "triple", "home_run"}
BB_EVENTS = {"walk", "intent_walk"}
HBP_EVENTS = {"hit_by_pitch"}
# PA-ending events that are NOT at-bats (excluded from AB; excluded from the
# ab+bb+hbp wOBA denominator except bb/hbp which are added explicitly).
NON_AB = {"sac_fly", "sac_bunt", "catcher_interf", "batter_interference",
          "sac_fly_double_play", "sac_bunt_double_play"}
ACC = ("pa", "ab", "h", "doubles", "triples", "hr", "bb", "hbp")


def _fetch_json(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=25) as r:
                return json.load(r)
        except Exception as e:
            if i == tries - 1:
                raise
            time.sleep(1.0 + i)


def _blank():
    return {k: 0 for k in ACC}


def _tally(agg, event_type):
    """Add one plate appearance's outcome to an accumulator dict in place."""
    agg["pa"] += 1
    et = event_type
    if et in BB_EVENTS:
        agg["bb"] += 1
    elif et in HBP_EVENTS:
        agg["hbp"] += 1
    elif et in NON_AB:
        pass  # sacrifice / interference: not an at-bat, not bb/hbp
    else:
        agg["ab"] += 1  # outs, reached-on-error, fielder's choice, and hits
        if et in HITS:
            agg["h"] += 1
            if et == "double":
                agg["doubles"] += 1
            elif et == "triple":
                agg["triples"] += 1
            elif et == "home_run":
                agg["hr"] += 1


def game_rows(pk, date, home, away):
    """Return per-(team,hand,role) tally rows for one game, or [] on failure.

    role is 'SP' (the plate appearance faced that team's STARTING pitcher) or
    'RP' (a reliever). The starter for each pitching side is the pitcher in that
    side's first plate appearance (pitchers can't re-enter, so first-seen ==
    starter). This lets the wRC+ table split all-pitcher vs starters-only vs
    relievers-only.
    """
    try:
        pbp = _fetch_json(f"{BASE}/game/{pk}/playByPlay")
    except Exception as e:
        print(f"  [pbp] {pk} fetch failed: {e}")
        return [], {}

    def atbats():
        for p in pbp.get("allPlays", []):
            res = p.get("result", {})
            if res.get("type") != "atBat":
                continue  # skip baserunning/substitution "action" plays
            et = res.get("eventType")
            if not et:
                continue
            m = p.get("matchup", {})
            hand = (m.get("pitchHand") or {}).get("code")
            pid = (m.get("pitcher") or {}).get("id")
            bid = (m.get("batter") or {}).get("id")
            top = p.get("about", {}).get("isTopInning", True)
            if hand in ("L", "R") and pid is not None:
                yield top, pid, hand, et, bid

    # Starter per pitching side = the first pitcher seen. Home pitches the top
    # half (away batting); away pitches the bottom half.
    home_sp = away_sp = None
    for top, pid, _h, _e, _b in atbats():
        if top and home_sp is None:
            home_sp = pid
        elif not top and away_sp is None:
            away_sp = pid
        if home_sp is not None and away_sp is not None:
            break

    buckets = defaultdict(_blank)      # (team, hand, is_home_bat, role) -> tally
    bat_buckets = defaultdict(_blank)  # (batter_id, hand, is_home_bat, role) -> tally
    for top, pid, hand, et, bid in atbats():
        is_home_bat = not top
        team = home if is_home_bat else away
        starter = home_sp if top else away_sp
        role = "SP" if pid == starter else "RP"
        _tally(buckets[(team, hand, is_home_bat, role)], et)
        if bid is not None:
            _tally(bat_buckets[(bid, hand, is_home_bat, role)], et)

    rows = []
    for (team, hand, is_home_bat, role), agg in buckets.items():
        # opp lets the wRC+ builder attribute the park (always the home park):
        # park = team if is_home else opp.
        opp = away if is_home_bat else home
        row = {"game_date": date, "team": team, "opp": opp, "opp_hand": hand,
               "is_home": is_home_bat, "role": role}
        row.update(agg)
        rows.append(row)
    return rows, bat_buckets


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="only this date (YYYY-MM-DD); default full season")
    ap.add_argument("--limit", type=int, default=0, help="cap games (testing)")
    args = ap.parse_args()

    start = end = None
    if args.date:
        start = end = args.date
    else:
        start, end = SEASON_START, SEASON_END

    sched = _fetch_json(
        f"{BASE}/schedule?sportId=1&startDate={start}&endDate={end}")
    games = []
    for d in sched.get("dates", []):
        for g in d.get("games", []):
            if g.get("status", {}).get("abstractGameState") != "Final":
                continue
            if g.get("gameType") not in ("R", "F", "D", "L", "W"):
                continue  # regular + postseason; skip spring/exhibition
            t = g["teams"]
            home = MLB_TEAM_ID_TO_ABBR.get(t["home"]["team"]["id"])
            away = MLB_TEAM_ID_TO_ABBR.get(t["away"]["team"]["id"])
            if not home or not away:
                continue  # non-MLB club (e.g. exhibition opponent)
            games.append((g["gamePk"], d["date"], home, away))

    done = set(_load(DONE_PATH, []))
    rows = _load(OUT_PATH, [])
    # Per-batter SEASON accumulator, keyed (batter_id, hand, is_home, role),
    # seeded from the existing cache so daily runs add only new games.
    bat = defaultdict(_blank)
    for r in _load(BAT_PATH, []):
        k = (r["batter_id"], r["opp_hand"], r["is_home"], r["role"])
        for kk in ACC:
            bat[k][kk] += r.get(kk) or 0
    todo = [g for g in games if g[0] not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"[pbp] {len(games)} final games in range, {len(todo)} new to fetch")

    def _flush():
        with open(OUT_PATH, "w") as f:
            json.dump(rows, f)
        with open(DONE_PATH, "w") as f:
            json.dump(sorted(done), f)
        bat_rows = [{"batter_id": k[0], "opp_hand": k[1], "is_home": k[2],
                     "role": k[3], **v} for k, v in bat.items()]
        with open(BAT_PATH, "w") as f:
            json.dump(bat_rows, f)

    n = 0
    for pk, date, home, away in todo:
        team_rows, bat_b = game_rows(pk, date, home, away)
        rows.extend(team_rows)
        for k, v in bat_b.items():
            acc = bat[k]
            for kk in ACC:
                acc[kk] += v[kk]
        done.add(pk)
        n += 1
        time.sleep(0.12)
        if n % 50 == 0:
            _flush()
            print(f"  [pbp] {n}/{len(todo)} games, {len(rows)} team rows, "
                  f"{len(bat)} batter splits cached")

    _flush()
    print(f"[pbp] done: {len(done)} games processed, {len(rows)} team-hand rows, "
          f"{len(bat)} batter-hand rows -> {OUT_PATH}, {BAT_PATH}")


if __name__ == "__main__":
    main()
