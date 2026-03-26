# core/kalman_state.py
# Unified Bayesian team strength tracker using a Kalman filter.
#
# Config variables (set by wrapper):
#   STATE_PATH     - path to kalman_state.json
#   KALMAN_DEFAULTS - sport-specific hyperparameters

import json
import os
import math
import time
from datetime import datetime, timedelta


def _jround(x):
    return math.floor(x + 0.5)

try:
    from zoneinfo import ZoneInfo
except ImportError:
    import pytz

    class ZoneInfo:
        def __init__(self, name):
            self._tz = pytz.timezone(name)

# -- Configurable (set by wrapper) --
STATE_PATH = None  # Must be set by wrapper

KALMAN_DEFAULTS = {
    "initialVar": 16,
    "gameNoise": 144,
    "dailyDrift": 0.15,
    "minVar": 2.0,
    "maxVar": 30,
}

# -- State Management --------------------------------------------------------

EMPTY_STATE = {
    "teams": {},
    "processedGames": {},
    "lastDriftDate": None,
    "meta": {"season": None, "created": None},
}


def _get_state_path():
    if STATE_PATH:
        return os.path.abspath(os.path.normpath(STATE_PATH))
    raise RuntimeError("core.kalman_state.STATE_PATH not configured.")


def load_kalman_state():
    try:
        with open(_get_state_path(), "r", encoding="utf-8") as f:
            state = json.load(f)
        if not state.get("teams"):
            state["teams"] = {}
        if not state.get("processedGames"):
            state["processedGames"] = {}
        return state
    except Exception:
        return {**EMPTY_STATE, "teams": {}, "processedGames": {}}


def save_kalman_state(state):
    path = _get_state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    last_err = None
    for attempt in range(3):
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp_path, path)
            return
        except OSError as err:
            last_err = err
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            if attempt < 2:
                time.sleep(0.1 * (attempt + 1))
    raise last_err


def initialize_kalman(team_stats, opts=None):
    cfg = {**KALMAN_DEFAULTS, **(opts or {})}
    state = {**EMPTY_STATE, "teams": {}, "processedGames": {}}

    for team_name in team_stats:
        gp = team_stats[team_name].get("GP", 0)
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


def _ensure_team(state, team_name, cfg):
    if team_name not in state["teams"]:
        state["teams"][team_name] = {
            "adj_mean": 0,
            "adj_var": cfg["initialVar"],
        }


def apply_daily_drift(state, today=None, opts=None):
    cfg = {**KALMAN_DEFAULTS, **(opts or {})}
    today_str = today or _today_yyyymmdd()

    if state.get("lastDriftDate") == today_str:
        return

    days_since_drift = 1
    if state.get("lastDriftDate"):
        last = _parse_yyyymmdd(state["lastDriftDate"])
        now = _parse_yyyymmdd(today_str)
        days_since_drift = max(1, round((now - last).total_seconds() / 86400))
        days_since_drift = min(days_since_drift, 14)

    total_drift = cfg["dailyDrift"] * days_since_drift

    for team in state["teams"].values():
        team["adj_var"] = min(cfg["maxVar"], team["adj_var"] + total_drift)

    state["lastDriftDate"] = today_str


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

    if game_date:
        key = f"{game_date}:{away}@{home}"
        if state["processedGames"].get(key):
            return
        state["processedGames"][key] = True

    _ensure_team(state, home, cfg)
    _ensure_team(state, away, cfg)

    h = state["teams"][home]
    a = state["teams"][away]

    actual_margin = home_score - away_score
    predicted_margin = proj_margin + h["adj_mean"] - a["adj_mean"]
    innovation = actual_margin - predicted_margin

    S = h["adj_var"] + a["adj_var"] + cfg["gameNoise"]

    K_home = h["adj_var"] / S
    K_away = a["adj_var"] / S

    h["adj_mean"] = _jround((h["adj_mean"] + K_home * innovation) * 10000) / 10000
    a["adj_mean"] = _jround((a["adj_mean"] - K_away * innovation) * 10000) / 10000

    h["adj_var"] = _jround(max(cfg["minVar"], (1 - K_home) * h["adj_var"]) * 10000) / 10000
    a["adj_var"] = _jround(max(cfg["minVar"], (1 - K_away) * a["adj_var"]) * 10000) / 10000


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


def get_team_adj(state, team_name):
    t = state["teams"].get(team_name)
    if not t:
        return {"mean": 0, "var": KALMAN_DEFAULTS["initialVar"]}
    return {"mean": t["adj_mean"], "var": t["adj_var"]}


def kalman_summary(state, top_n=10):
    entries = [{"name": name, **t} for name, t in state["teams"].items()]
    entries.sort(key=lambda x: abs(x["adj_mean"]), reverse=True)

    lines = [f"  [kalman] Team adjustments (top {top_n} by |offset|):"]
    for t in entries[:top_n]:
        sign = "+" if t["adj_mean"] >= 0 else ""
        conf = f"{math.sqrt(t['adj_var']):.1f}"
        lines.append(f"    {t['name']:<28} {sign}{t['adj_mean']:.2f} pts  (+/-{conf})")
    return "\n".join(lines)


def prune_processed_games(state, keep_days=30):
    cutoff = _date_minus_days(keep_days)
    before = len(state["processedGames"])

    keys_to_delete = [key for key in state["processedGames"] if key.split(":")[0] < cutoff]
    for key in keys_to_delete:
        del state["processedGames"][key]

    after = len(state["processedGames"])
    if before != after:
        print(f"  [kalman] Pruned {before - after} old game records (kept last {keep_days} days)")


# -- Utility -------------------------------------------------------------------

def _is_finite(x):
    if x is None:
        return False
    try:
        return math.isfinite(x)
    except (TypeError, ValueError):
        return False


def _current_season():
    now = datetime.now()
    y = now.year
    m = now.month
    start = y if m >= 10 else y - 1
    return f"{start}-{str(start + 1)[2:]}"


def _today_yyyymmdd():
    try:
        tz = ZoneInfo("America/Chicago")
        now = datetime.now(tz)
    except Exception:
        now = datetime.now()
    return now.strftime("%Y%m%d")


def _parse_yyyymmdd(s):
    return datetime(int(s[0:4]), int(s[4:6]), int(s[6:8]))


def _date_minus_days(n):
    d = datetime.now() - timedelta(days=n)
    return d.strftime("%Y%m%d")
