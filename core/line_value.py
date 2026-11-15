# core/line_value.py
#
# Line-value tracker. A model has a real edge when the market moves toward its
# side after it has made its number. Each run of the day notes, per game:
#   line0 / edge0 / side0 / at0   the line, the model's edge (points) and side
#                                 the first time the game was priced that day
#   flagLine / flagEdge / ...     the same, the first time the edge reached the
#                                 flag size (default 3 points)
#   lineLast / atLast             the most recent line seen before tip
# Line value = how many points better the noted line was than the last one, for
# the side the model was on. Positive = the market moved the model's way.
# Kept on the game as g["lv"]; started games keep what they had.
import math


def _num(x):
    return isinstance(x, (int, float)) and math.isfinite(x)


def track(games, prev_games, at, flag_edge=3.0):
    """Update g["lv"] on today's games. prev_games = {(away, home): game} from
    the earlier run of the same day (may be empty)."""
    n = 0
    for g in games or []:
        if g.get("status") in ("MISSING_ODDS", "SKIPPED", "STARTED", "POSTPONED"):
            continue
        if not (_num(g.get("line")) and _num(g.get("margin"))):
            continue
        prev = (prev_games or {}).get((g.get("away", ""), g.get("home", "")))
        lv = dict((prev or {}).get("lv") or g.get("lv") or {})
        edge = g["margin"]                      # projected home margin + home line
        side = "home" if edge >= 0 else "away"
        if "line0" not in lv:
            lv.update(line0=g["line"], edge0=round(edge, 1), side0=side, at0=at)
        if "flagLine" not in lv and abs(edge) >= flag_edge:
            lv.update(flagLine=g["line"], flagEdge=round(edge, 1), flagSide=side, flagAt=at)
        lv["lineLast"], lv["atLast"] = g["line"], at
        g["lv"] = lv
        n += 1
    return n


def value(lv, kind="first"):
    """Points of line value for one game (None when not tracked)."""
    if not lv or not _num(lv.get("lineLast")):
        return None
    line, side = (lv.get("line0"), lv.get("side0")) if kind == "first" else (lv.get("flagLine"), lv.get("flagSide"))
    if not _num(line) or side not in ("home", "away"):
        return None
    return (line - lv["lineLast"]) * (1 if side == "home" else -1)
