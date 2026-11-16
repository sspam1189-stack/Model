# Lineup-value shift for the season-average models (Full Season, KalmanOff,
# KalmanEarly). Added 2026-11-15 (user).
#
# The value of tonight's expected lineup (roster minus players listed out or
# doubtful) is compared with the lineups the team has actually fielded this
# season -- the same player values and minutes the Ratings model uses
# (core/ratings_model.py). The difference, in points of margin, is applied at
# LINEUP_VALUE_STRENGTH (0.5 = half strength) to the game's projected margin:
# half to each side, so the projected total does not move.
#
# Walk-forward 2026-10-23..11-14 on KalmanOff (173 games): 71-47 +27.7u as is,
# 71-48 +29.9u at half strength, 67-48 +25.9u at full strength.
import os


def strength(engine_config):
    """Configured strength; NBA_LINEUP_STRENGTH overrides it for a one-off replay."""
    env = (os.environ.get("NBA_LINEUP_STRENGTH") or "").strip()
    try:
        return float(env) if env else float(engine_config.get("LINEUP_VALUE_STRENGTH") or 0.0)
    except ValueError:
        return 0.0


def points(date, team_names, injury_report, refresh=False):
    """{team name: points of margin tonight's lineup is worth versus the
    lineups the team has fielded}, plus notes. Never raises; ({}, {}) when it
    cannot be worked out (too few games, no player logs)."""
    try:
        from core.ratings_model import build_team_stats, refresh_logs
        if refresh:
            print(f"  [lineup value] {refresh_logs(date)}")
        st, info = build_team_stats(date, list(team_names), injury_report or {}, versus_fielded=True)
        if not st:
            return {}, {}
        return dict(info.get("pts") or {}), dict(info.get("notes") or {})
    except Exception as e:
        print(f"  [lineup value] unavailable (non-fatal): {e}")
        return {}, {}


def shift_game(game_stats, home, away, pts, k, W):
    """Copy of game_stats with the two teams' OFF moved so the projected home
    margin changes by k * (pts[home] - pts[away]). Returns (stats, shift)."""
    h, a = game_stats.get(home), game_stats.get(away)
    if not h or not a or not k:
        return game_stats, 0.0
    shift = k * ((pts.get(home) or 0.0) - (pts.get(away) or 0.0))
    if abs(shift) < 0.05:
        return game_stats, 0.0
    per_point = (W.get("wOFF", 1) or 1) * (((h["PACE"] + a["PACE"]) / 2) * W.get("paceAdj", 1)) / 100
    if per_point <= 0:
        return game_stats, 0.0
    d = (shift / 2) / per_point
    out = dict(game_stats)
    out[home] = {**h, "OFF": h["OFF"] + d}
    out[away] = {**a, "OFF": a["OFF"] - d}
    return out, round(shift, 2)
