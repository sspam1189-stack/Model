#!/usr/bin/env python3
# scripts/backtest_wnba.py
#
# WNBA backtest / calibration harness. Iterates a fixed historical date range
# (real WNBA seasons — 2024, 2025) rather than "today minus N", fetches team
# advanced stats as-of each game date via nba_api (LeagueID=10), pulls final
# scores from ESPN, runs the SAME engine used live, and measures:
#
#   1. Projection accuracy  — margin MAE / bias, straight-up (winner) hit rate.
#   2. pCover CALIBRATION   — at line=0 (pick'em) the engine emits pHomeCover;
#      we bucket predictions and compare predicted vs realized home-cover rate.
#      This is the only ATS-shaped signal available without historical closing
#      lines (The Odds API historical is paywalled; ESPN drops WNBA lines).
#
# Because it uses a synthetic line=0, this does NOT prove betting ROI — it
# validates that the engine's probability output is well-calibrated on real
# WNBA score distributions, which is what the WNBA variance/HCA constants in
# defaults.py are calibrated to. Live ATS ROI is validated forward on 2026
# FanDuel lines via run_daily / backfill.
#
# Usage:
#   python scripts/backtest_wnba.py 2025            # one season
#   python scripts/backtest_wnba.py 2024 2025       # pooled

import sys
import os
import math
import time
import datetime
import statistics

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sources.nba_stats import fetch_nba_stats_enhanced
from sources.blend_stats import blend_base, blend_for_game
from sources.espn_scoreboard import fetch_scoreboard, extract_final_scores
from model_engine import load_defaults, get_avgs, analyze_game
from kalman_state import initialize_kalman

EXHIBITION_MARKERS = ("TEAM ", "Team USA", "Team WNBA", "All-Star", "All Star",
                      "TEAM COLLIER", "TEAM CLARK", "Rise", "Sun ")


def season_ranges(year):
    # WNBA regular season ~ mid-May through mid-Sept. Overshoot the window a
    # little; final-score filtering + odds/exhibition filtering handle the rest.
    return f"{year}0501", f"{year}0930"


def daterange(start, end):
    d = datetime.datetime.strptime(start, "%Y%m%d")
    e = datetime.datetime.strptime(end, "%Y%m%d")
    while d <= e:
        yield d.strftime("%Y%m%d")
        d += datetime.timedelta(days=1)


def is_exhibition(a, h):
    s = f"{a} {h}"
    return any(m in s for m in EXHIBITION_MARKERS)


def collect_finals(year):
    """One ESPN call per season using a date-range query."""
    start, end = season_ranges(year)
    sb = fetch_scoreboard(f"{start}-{end}")
    by_date = {}
    for ev in sb.get("events", []):
        comp = ev["competitions"][0]
        if "FINAL" not in comp["status"]["type"]["name"]:
            continue
        cs = comp["competitors"]
        a = next(c for c in cs if c["homeAway"] == "away")
        h = next(c for c in cs if c["homeAway"] == "home")
        an = a["team"]["displayName"]
        hn = h["team"]["displayName"]
        if is_exhibition(an, hn):
            continue
        try:
            as_ = float(a["score"]); hs = float(h["score"])
        except (TypeError, ValueError):
            continue
        d = ev["date"][:10].replace("-", "")
        by_date.setdefault(d, []).append({"away": an, "home": hn, "awayScore": as_, "homeScore": hs})
    return by_date


def main():
    years = [int(y) for y in sys.argv[1:]] or [2025]
    defaults = load_defaults()
    base_w = {**defaults["DEFAULT_W"]}
    base_w_var = {**defaults["DEFAULT_W_VAR"]}

    stats_by_date = {}

    def _snap_week(date):
        # Snap to the prior Monday so we fetch team stats ~once/week (they barely
        # move day-to-day) — keeps the backtest to ~15 stat pulls/season.
        d = datetime.datetime.strptime(date, "%Y%m%d")
        d = d - datetime.timedelta(days=d.weekday())
        return d.strftime("%Y%m%d")

    def stats_for(date):
        date = _snap_week(date)
        if date in stats_by_date:
            return stats_by_date[date]
        os.makedirs(CACHE_DIR := os.path.join(os.path.dirname(__file__), "..", "data", "stats_cache", "wnba"), exist_ok=True)
        disk = os.path.join(CACHE_DIR, date + ".json")
        if os.path.exists(disk):
            import json
            with open(disk) as f:
                raw = json.load(f)
            if raw.get("season"):
                stats_by_date[date] = raw
                return raw
        date_to = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
        enh = fetch_nba_stats_enhanced(date_to)
        stats_by_date[date] = enh
        import json
        try:
            with open(disk, "w") as f:
                json.dump(enh, f)
        except Exception:
            pass
        time.sleep(0.5)
        return enh

    margins_err = []      # projected margin - actual margin
    su_hits = 0           # straight-up winner correct
    su_total = 0
    # pCover calibration buckets (predicted pHomeCover -> realized home-cover@0)
    buckets = {}          # bucket_lo -> [n, home_covered]

    kalman_state = None

    for year in years:
        finals = collect_finals(year)
        print(f"[backtest] {year}: {sum(len(v) for v in finals.values())} final games across {len(finals)} dates")
        for date in sorted(finals):
            try:
                enh = stats_for(date)
            except Exception as e:
                print(f"  [backtest] {date} stats failed: {e}")
                continue
            season = enh["season"]
            if len(season) < 4:
                continue
            if kalman_state is None:
                kalman_state = initialize_kalman(season)
            base_stats = blend_base(season, enh.get("last10"), base_w.get("recentWeight", 0.87))
            a = get_avgs(base_stats)

            for g in finals[date]:
                gg = {"away": g["away"], "home": g["home"], "line": 0.0, "total": 0.0}
                game_stats = blend_for_game(base_stats, enh.get("home"), enh.get("away"),
                                            g["home"], g["away"], base_w.get("locationWeight", 0.25))
                r = analyze_game(gg, game_stats, get_avgs(game_stats), base_w, None,
                                 kalman_state, base_w_var, defaults["BAYES_HYPER"]["residualVar"])
                if not r:
                    continue
                actual_margin = g["homeScore"] - g["awayScore"]  # home perspective
                proj_margin = r["margin"]  # at line=0, this is projected home margin
                margins_err.append(proj_margin - actual_margin)
                # straight-up
                if (proj_margin > 0) == (actual_margin > 0):
                    su_hits += 1
                su_total += 1
                # pCover calibration at line=0: pHomeCover vs realized home win
                ph = r.get("pHomeCover")
                if ph is not None:
                    lo = math.floor(ph * 10) / 10  # 0.1 buckets
                    b = buckets.setdefault(lo, [0, 0])
                    b[0] += 1
                    if actual_margin > 0:
                        b[1] += 1

    print("\n==== WNBA BACKTEST RESULTS ====")
    print(f"Games scored: {su_total}")
    if margins_err:
        mae = statistics.mean(abs(x) for x in margins_err)
        bias = statistics.mean(margins_err)
        print(f"Margin MAE: {mae:.2f}   bias(proj-actual): {bias:+.2f}")
    if su_total:
        print(f"Straight-up (winner) hit rate: {su_hits}/{su_total} = {su_hits/su_total*100:.1f}%")
    print("\npCover calibration (predicted pHomeCover vs realized home-win rate, line=0):")
    print(f"  {'bucket':>10} {'n':>5} {'pred':>6} {'realized':>9}")
    for lo in sorted(buckets):
        n, cov = buckets[lo]
        if n == 0:
            continue
        print(f"  {lo:.1f}-{lo+0.1:.1f} {n:5d} {lo+0.05:6.2f} {cov/n*100:8.1f}%")


if __name__ == "__main__":
    main()
