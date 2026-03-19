# scripts/kalman_state.py
# --------------------------------------------------------------------------
# Bayesian team strength tracker using a Kalman filter.
#
# Each team has an "adjustment offset" — how many points better or worse
# they're performing relative to their season-long stats. This is a single
# number per team, not per-stat, because a single game result doesn't give
# us enough information to decompose into separate OFF/DEF/TS/TO signals.
#
# State per team:
#   adj_mean:  expected offset (starts at 0)
#   adj_var:   uncertainty around that offset (starts high, shrinks with games)
#
# After each graded game:
#   innovation = actual_margin - projected_margin
#   The innovation is split between both teams via Kalman gain.
#   A team with high variance (uncertain) absorbs more of the surprise.
#   A team with low variance (well-known) absorbs less.
#
# Daily drift:
#   Variance increases slightly each day — teams change (trades, fatigue,
#   chemistry shifts). This prevents the filter from locking on to old data.
#
# The offset gets added to proj_score in model_engine.py.
# The variance feeds into the projection variance for P(cover).
#
# NCAA adaptation: higher initialVar (362 teams, more uncertain), higher
# gameNoise (14 pt std dev), higher maxVar (teams can go weeks between games).
# --------------------------------------------------------------------------

import json
import math
import os
from datetime import datetime, timedelta, timezone

DATA_DIR = os.path.join(os.getcwd(), "data")
STATE_PATH = os.path.join(DATA_DIR, "kalman_state.json")


# -- Hyperparameters -------------------------------------------------------

KALMAN_DEFAULTS = {
    # Initial uncertainty per team (points^2). +/-4.5 points = variance 20.
    # Higher than NBA because 362 D1 teams means less prior knowledge per team.
    "initialVar": 20,

    # Game outcome noise (points^2). NCAA games have ~14 point std dev in margin
    # after accounting for team strength. 14^2 = 196.
    # Higher talent disparity in college -> more random outcomes.
    "gameNoise": 196,

    # Daily drift added to each team's variance. Prevents the filter from
    # becoming too confident over time. 0.15 means after 30 days with no games,
    # a team's variance grows by ~4.5 (~ 2 extra points of uncertainty).
    "dailyDrift": 0.15,

    # Minimum variance floor — never let a team get "perfectly known."
    # Even late-season teams have game-to-game variance in true strength.
    "minVar": 2.0,

    # Maximum variance cap — don't let uncertainty blow up for teams with
    # long gaps between games. Higher than NBA because some college teams
    # can go weeks between games (exam breaks, conference tournament gaps).
    "maxVar": 40,
}


# -- State Management -----------------------------------------------------

EMPTY_STATE = {
    "teams": {},
    "processedGames": {},   # { "YYYYMMDD:away@home": True } — prevents double-counting
    "lastDriftDate": None,   # YYYYMMDD of last drift application
    "meta": {"season": None, "created": None},
}


def load_kalman_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            state = json.load(f)
        if not state.get("teams"):
            state["teams"] = {}
        if not state.get("processedGames"):
            state["processedGames"] = {}
        return state
    except Exception:
        return {**EMPTY_STATE, "teams": {}, "processedGames": {}}


def save_kalman_state(state):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# -- Initialization --------------------------------------------------------
# Call once at the start of the season or when the state file doesn't exist.
# Seeds every team with adj_mean=0 (no adjustment), adj_var=initialVar.

def initialize_kalman(team_stats, opts=None):
    cfg = {**KALMAN_DEFAULTS, **(opts or {})}
    state = {**EMPTY_STATE, "teams": {}, "processedGames": {}}

    for team_name in team_stats:
        gp = team_stats[team_name].get("GP", 0)

        # Scale initial variance by games played — a team with 60 GP is
        # better known than a team with 15 GP.
        gp_factor = max(0.5, min(1.0, 30 / max(gp, 1)))
        init_var = cfg["initialVar"] * gp_factor

        state["teams"][team_name] = {
            "adj_mean": 0,
            "adj_var": max(cfg["minVar"], init_var),
        }

    now = datetime.now()
    state["meta"] = {
        "season": _current_season(),
        "created": now.isoformat(),
    }
    state["lastDriftDate"] = _today_yyyymmdd()

    print(f"  [kalman] Initialized {len(state['teams'])} teams (initial_var={cfg['initialVar']})")
    return state


# -- Ensure Team Exists ----------------------------------------------------
# If a team isn't in the state (mid-season init, name mismatch), add it.

def _ensure_team(state, team_name, cfg):
    if team_name not in state["teams"]:
        state["teams"][team_name] = {
            "adj_mean": 0,
            "adj_var": cfg["initialVar"],
        }


# -- Daily Drift -----------------------------------------------------------
# Call once per day BEFORE analyzing games. Adds process noise to every team's
# variance, reflecting that teams change over time.

def apply_daily_drift(state, today=None, opts=None):
    cfg = {**KALMAN_DEFAULTS, **(opts or {})}
    today_str = today or _today_yyyymmdd()

    if state.get("lastDriftDate") == today_str:
        return  # already applied today

    # How many days since last drift? Usually 1, but could be more if
    # the pipeline was down for a few days.
    days_since_drift = 1
    if state.get("lastDriftDate"):
        last = _parse_yyyymmdd(state["lastDriftDate"])
        now = _parse_yyyymmdd(today_str)
        days_since_drift = max(1, round((now - last).total_seconds() / 86400))
        days_since_drift = min(days_since_drift, 14)  # cap at 2 weeks

    total_drift = cfg["dailyDrift"] * days_since_drift

    for team in state["teams"].values():
        team["adj_var"] = min(cfg["maxVar"], team["adj_var"] + total_drift)

    state["lastDriftDate"] = today_str


# -- Game Update -----------------------------------------------------------
# Call after a game is graded. Updates both teams' Kalman state.
#
# Inputs:
#   state:        the kalman state object
#   game:         { "home", "away", "homeScore", "awayScore" } — actual results
#   proj_margin:  the model's projected margin (hS - aS) for this game
#   game_date:    YYYYMMDD string for dedup
#   opts:         override hyperparameters
#
# The "innovation" is: actual_margin - projected_margin.
# If positive, the home team outperformed (or away underperformed).
# The Kalman gains determine how much each team absorbs.

def update_from_game(state, game, proj_margin, game_date=None, opts=None):
    cfg = {**KALMAN_DEFAULTS, **(opts or {})}

    home = game.get("home")
    away = game.get("away")
    home_score = game.get("homeScore")
    away_score = game.get("awayScore")

    if not _is_finite(home_score) or not _is_finite(away_score):
        return
    if not home or not away:
        return

    # Dedup: don't process the same game twice
    if game_date:
        key = f"{game_date}:{away}@{home}"
        if state["processedGames"].get(key):
            return
        state["processedGames"][key] = True

    _ensure_team(state, home, cfg)
    _ensure_team(state, away, cfg)

    h = state["teams"][home]
    a = state["teams"][away]

    # Actual margin from home perspective
    actual_margin = home_score - away_score

    # Predicted margin includes both teams' current adjustments
    predicted_margin = proj_margin + h["adj_mean"] - a["adj_mean"]

    # Innovation: surprise
    innovation = actual_margin - predicted_margin

    # Innovation variance: sum of both teams' uncertainty + game noise
    S = h["adj_var"] + a["adj_var"] + cfg["gameNoise"]

    # Kalman gains — more uncertain team absorbs more of the surprise
    K_home = h["adj_var"] / S
    K_away = a["adj_var"] / S

    # Update means
    h["adj_mean"] += K_home * innovation
    a["adj_mean"] -= K_away * innovation  # negative: if home outperformed, away underperformed

    # Update variances (shrink uncertainty)
    h["adj_var"] = max(cfg["minVar"], (1 - K_home) * h["adj_var"])
    a["adj_var"] = max(cfg["minVar"], (1 - K_away) * a["adj_var"])


# -- Batch Update ----------------------------------------------------------
# Process multiple graded games at once. Used by run_daily after grading.

def batch_update(state, graded_games, opts=None):
    updated = 0

    for g in graded_games:
        if not _is_finite(g.get("homeScore")) or not _is_finite(g.get("awayScore")):
            continue
        if not _is_finite(g.get("hS")) or not _is_finite(g.get("aS")):
            continue

        proj_margin = g["hS"] - g["aS"]
        update_from_game(state, g, proj_margin, g.get("_kalmanDate"), opts)
        updated += 1

    if updated > 0:
        print(f"  [kalman] Updated from {updated} graded game(s)")
    return updated


# -- Get Team Adjustment ---------------------------------------------------
# Returns { "mean": ..., "var": ... } for a team. If team not in state, returns neutral.

def get_team_adj(state, team_name):
    t = state["teams"].get(team_name)
    if not t:
        return {"mean": 0, "var": KALMAN_DEFAULTS["initialVar"]}
    return {"mean": t["adj_mean"], "var": t["adj_var"]}


# -- Diagnostics -----------------------------------------------------------
# Human-readable summary for logging.

def kalman_summary(state, top_n=10):
    entries = [
        {"name": name, **t}
        for name, t in state["teams"].items()
    ]
    entries.sort(key=lambda x: abs(x["adj_mean"]), reverse=True)

    lines = [f"  [kalman] Team adjustments (top {top_n} by |offset|):"]
    for t in entries[:top_n]:
        sign = "+" if t["adj_mean"] >= 0 else ""
        conf = f"{math.sqrt(t['adj_var']):.1f}"
        lines.append(f"    {t['name']:<28} {sign}{t['adj_mean']:.2f} pts  (+/-{conf})")
    return "\n".join(lines)


# -- Prune Old Processed Games --------------------------------------------
# Keep the processedGames map from growing forever. Called periodically.

def prune_processed_games(state, keep_days=30):
    cutoff = _date_minus_days(keep_days)
    before = len(state["processedGames"])

    keys_to_delete = []
    for key in state["processedGames"]:
        date = key.split(":")[0]
        if date < cutoff:
            keys_to_delete.append(key)

    for key in keys_to_delete:
        del state["processedGames"][key]

    after = len(state["processedGames"])
    if before != after:
        print(f"  [kalman] Pruned {before - after} old game records (kept last {keep_days} days)")


# -- Utility ---------------------------------------------------------------

def _current_season():
    now = datetime.now()
    y = now.year
    m = now.month
    start = y if m >= 10 else y - 1
    return f"{start}-{str(start + 1)[2:]}"


def _today_yyyymmdd():
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/Chicago")
    except ImportError:
        import pytz
        tz = pytz.timezone("America/Chicago")
    now = datetime.now(tz)
    return now.strftime("%Y%m%d")


def _parse_yyyymmdd(s):
    return datetime(int(s[0:4]), int(s[4:6]) - 0, int(s[6:8]))


def _date_minus_days(n):
    d = datetime.now() - timedelta(days=n)
    return d.strftime("%Y%m%d")


def _is_finite(x):
    """Check if a value is a finite number (not None, not NaN, not inf)."""
    if x is None:
        return False
    try:
        return math.isfinite(x)
    except (TypeError, ValueError):
        return False
