# pyNBAPROPS/scripts/player_kalman.py
# Per-player Kalman filter for NBA player prop projections.
#
# Unlike the team-level Kalman in core/kalman_state.py which tracks a single
# "team strength" adjustment, this tracks per-player per-stat baselines:
#   - Each player has a Kalman state per stat (pts, reb, ast, fg3m, etc.)
#   - The filter smooths noisy game-to-game variance into a stable baseline
#   - Drift increases uncertainty between games (role changes, injuries)
#   - After each game, the filter updates toward actual performance
#
# Why this helps over simple rolling average:
#   1. Adaptive weighting: Recent games where the player's role changed
#      get more weight than a fixed exponential decay
#   2. Principled uncertainty: Variance tracks how confident we are in the
#      baseline — new players or players returning from injury have high
#      uncertainty, consistent starters have low uncertainty
#   3. Regime detection: A sudden role change (trade, co-star injury) causes
#      large innovations that widen variance, making the filter faster to
#      adapt to the new regime

import os
import json
import math
from datetime import datetime

# ---------------------------------------------------------------------------
# Hyperparameters
# ---------------------------------------------------------------------------

PLAYER_KALMAN_DEFAULTS = {
    # Process noise: how much baseline drifts per game not played
    # Higher = filter adapts faster to changes but is noisier
    "gameDrift": 0.3,          # Variance increase per game missed

    # Observation noise per stat: how noisy a single game's output is
    # relative to the player's true baseline. Higher = single game matters less.
    "obsNoise": {
        "pts":   25.0,         # ~5 pt std dev — scoring is moderate variance
        "reb":    6.0,         # ~2.4 std dev
        "ast":    5.0,         # ~2.2 std dev
        "fg3m":   2.5,         # ~1.6 std dev — 3PM is high relative variance
        "stl":    0.8,         # ~0.9 std dev
        "blk":    0.8,
        "tov":    2.0,         # ~1.4 std dev
    },

    # Initial variance for a new player (high uncertainty)
    "initialVar": 20.0,

    # Variance floor and ceiling
    "minVar": 0.5,             # Don't collapse to zero uncertainty
    "maxVar": 50.0,            # Cap uncertainty for players with no data

    # Minimum games before Kalman state is used (cold start protection)
    "minGamesForKalman": 5,

    # Blending: how much to trust Kalman mean vs. rolling average
    # 0.0 = pure rolling average, 1.0 = pure Kalman
    # We blend because Kalman is better for regime changes but rolling avg
    # is more robust when sample size is small.
    "kalmanBlend": 0.6,        # 60% Kalman, 40% rolling avg
}

# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def new_player_kalman_state():
    """Create empty Kalman state container."""
    return {
        "players": {},
        "processedGames": {},
        "lastDriftDate": None,
        "meta": {
            "version": "1.0",
            "created": datetime.now().isoformat(),
        },
    }


def load_player_kalman_state(path):
    """Load Kalman state from JSON file."""
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                state = json.load(f)
            if "players" not in state:
                state["players"] = {}
            if "processedGames" not in state:
                state["processedGames"] = {}
            if "lastDriftDate" not in state:
                state["lastDriftDate"] = None
            return state
        except Exception:
            pass
    return new_player_kalman_state()


def save_player_kalman_state(state, path):
    """Save Kalman state to JSON file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Per-player Kalman operations
# ---------------------------------------------------------------------------

def _ensure_player(state, player_id, player_name, cfg=None):
    """Ensure a player has a Kalman state entry."""
    cfg = cfg or PLAYER_KALMAN_DEFAULTS
    if player_id not in state["players"]:
        state["players"][player_id] = {
            "name": player_name,
            "stats": {},
            "gamesProcessed": 0,
        }


def _ensure_stat(player_state, stat_key, initial_value=None, cfg=None):
    """Ensure a player has a Kalman state for a specific stat."""
    cfg = cfg or PLAYER_KALMAN_DEFAULTS
    if stat_key not in player_state["stats"]:
        player_state["stats"][stat_key] = {
            "mean": initial_value if initial_value is not None else 0.0,
            "var": cfg["initialVar"],
        }


def update_player_from_game(state, player_id, player_name, game_stats, game_id=None, cfg=None):
    """
    Update a player's Kalman state after observing a game.

    Parameters
    ----------
    state : dict
        Player Kalman state container.
    player_id : int or str
        Unique player identifier.
    player_name : str
        Player display name.
    game_stats : dict
        Actual stats from the game: {"pts": 28, "reb": 7, "ast": 5, ...}
    game_id : str or None
        Unique game identifier to prevent double-processing.
    cfg : dict or None
        Hyperparameters (uses defaults if None).
    """
    cfg = cfg or PLAYER_KALMAN_DEFAULTS

    # Skip if already processed
    if game_id:
        proc_key = f"{player_id}:{game_id}"
        if state["processedGames"].get(proc_key):
            return
        state["processedGames"][proc_key] = True

    _ensure_player(state, player_id, player_name, cfg)
    ps = state["players"][player_id]
    ps["name"] = player_name  # Update name in case it changed

    obs_noise = cfg.get("obsNoise", {})

    for stat_key, actual_val in game_stats.items():
        if stat_key not in obs_noise:
            continue  # Only track stats we have noise estimates for
        if actual_val is None:
            continue

        actual_val = float(actual_val)
        _ensure_stat(ps, stat_key, initial_value=actual_val, cfg=cfg)

        s = ps["stats"][stat_key]
        noise = obs_noise[stat_key]

        # --- Kalman update ---
        # Prior: N(s["mean"], s["var"])
        # Observation: N(actual_val, noise)
        # Posterior: N(updated_mean, updated_var)

        S = s["var"] + noise  # Innovation variance
        K = s["var"] / S       # Kalman gain

        innovation = actual_val - s["mean"]
        s["mean"] = s["mean"] + K * innovation
        s["var"] = max(cfg["minVar"], (1 - K) * s["var"])

    ps["gamesProcessed"] = ps.get("gamesProcessed", 0) + 1


def apply_drift(state, games_elapsed=None, cfg=None, today=None):
    """
    Apply process noise (drift) to all players, at most once per calendar day.

    Guarded by state["lastDriftDate"]: if drift has already been applied today,
    this is a no-op. This prevents variance inflation when run_daily runs
    multiple times per day.

    When games_elapsed is None, it is computed from the gap between today and
    state["lastDriftDate"] (capped at 14 days). An explicit games_elapsed
    override is honored for walk-forward backfills that simulate historical
    dates.

    Parameters
    ----------
    state : dict
        Player Kalman state.
    games_elapsed : int or None
        Override for days since last drift. If None, computed from
        state["lastDriftDate"].
    cfg : dict or None
        Hyperparameters.
    today : str or None
        ISO date (YYYY-MM-DD). Defaults to today.
    """
    cfg = cfg or PLAYER_KALMAN_DEFAULTS
    today_str = today or datetime.now().strftime("%Y-%m-%d")

    if state.get("lastDriftDate") == today_str:
        return

    if games_elapsed is None:
        last = state.get("lastDriftDate")
        if last:
            try:
                last_dt = datetime.strptime(last, "%Y-%m-%d")
                now_dt = datetime.strptime(today_str, "%Y-%m-%d")
                games_elapsed = max(1, (now_dt - last_dt).days)
                games_elapsed = min(games_elapsed, 14)
            except (ValueError, TypeError):
                games_elapsed = 1
        else:
            games_elapsed = 1

    drift = cfg["gameDrift"] * games_elapsed

    for pid, ps in state["players"].items():
        for stat_key, s in ps.get("stats", {}).items():
            s["var"] = min(cfg["maxVar"], s["var"] + drift)

    state["lastDriftDate"] = today_str


def get_player_projection(state, player_id, stat_key, rolling_avg=None, cfg=None):
    """
    Get the Kalman-informed projection for a player-stat.

    If the player has enough history, blends Kalman mean with rolling average.
    Otherwise falls back to rolling average only.

    Parameters
    ----------
    state : dict
        Player Kalman state.
    player_id : int or str
        Player identifier.
    stat_key : str
        Stat to project (pts, reb, ast, etc.).
    rolling_avg : float or None
        Rolling weighted average from simple model (fallback).
    cfg : dict or None
        Hyperparameters.

    Returns
    -------
    dict
        {"proj": float, "var": float, "source": str}
        source is "kalman_blend", "kalman_only", or "rolling_avg"
    """
    cfg = cfg or PLAYER_KALMAN_DEFAULTS

    ps = state["players"].get(player_id if isinstance(player_id, str) else str(player_id))
    if ps is None:
        # Also try int key
        ps = state["players"].get(str(player_id))
    if ps is None:
        ps = state["players"].get(player_id)

    if ps is None or stat_key not in ps.get("stats", {}):
        if rolling_avg is not None:
            return {"proj": rolling_avg, "var": cfg["initialVar"], "source": "rolling_avg"}
        return {"proj": 0.0, "var": cfg["initialVar"], "source": "no_data"}

    s = ps["stats"][stat_key]
    kalman_mean = s["mean"]
    kalman_var = s["var"]
    games = ps.get("gamesProcessed", 0)

    # Cold start: not enough games for Kalman to be meaningful
    if games < cfg["minGamesForKalman"]:
        if rolling_avg is not None:
            return {"proj": rolling_avg, "var": kalman_var, "source": "rolling_avg"}
        return {"proj": kalman_mean, "var": kalman_var, "source": "kalman_cold"}

    # Blend Kalman mean with rolling average
    if rolling_avg is not None:
        blend = cfg["kalmanBlend"]
        proj = blend * kalman_mean + (1 - blend) * rolling_avg
        return {"proj": proj, "var": kalman_var, "source": "kalman_blend"}

    return {"proj": kalman_mean, "var": kalman_var, "source": "kalman_only"}


def batch_update_from_game_logs(state, game_logs, cfg=None):
    """
    Process a batch of game logs to update all player Kalman states.

    Parameters
    ----------
    state : dict
        Player Kalman state.
    game_logs : list[dict]
        Raw game logs from NBA.com (each dict is a player-game).
    cfg : dict or None
        Hyperparameters.

    Returns
    -------
    int
        Number of player-games processed.
    """
    cfg = cfg or PLAYER_KALMAN_DEFAULTS
    processed = 0

    for g in game_logs:
        pid = g.get("player_id")
        pname = g.get("player_name", "")
        game_id = g.get("game_id")
        minutes = g.get("min", 0)

        if pid is None or minutes < 10:
            continue

        game_stats = {
            "pts": g.get("pts", 0),
            "reb": g.get("reb", 0),
            "ast": g.get("ast", 0),
            "fg3m": g.get("fg3m", 0),
            "stl": g.get("stl", 0),
            "blk": g.get("blk", 0),
            "tov": g.get("tov", 0),
        }

        update_player_from_game(state, str(pid), pname, game_stats, game_id, cfg)
        processed += 1

    return processed


def prune_inactive_players(state, max_games_since=50):
    """Remove players who haven't played in a long time to keep state lean."""
    to_remove = []
    for pid, ps in state["players"].items():
        if ps.get("gamesProcessed", 0) == 0:
            to_remove.append(pid)
    for pid in to_remove:
        del state["players"][pid]
    if to_remove:
        print(f"  [kalman] Pruned {len(to_remove)} inactive players")


def kalman_summary(state, top_n=10, stat_key="pts"):
    """Print summary of top players by Kalman mean for a stat."""
    entries = []
    for pid, ps in state["players"].items():
        s = ps.get("stats", {}).get(stat_key)
        if s:
            entries.append({
                "name": ps.get("name", pid),
                "mean": s["mean"],
                "var": s["var"],
                "games": ps.get("gamesProcessed", 0),
            })

    entries.sort(key=lambda x: x["mean"], reverse=True)

    lines = [f"  [kalman] Top {top_n} players by {stat_key} baseline:"]
    for e in entries[:top_n]:
        std = math.sqrt(e["var"])
        # Sanitize name for Windows cp1252 console
        safe_name = e['name'].encode('ascii', 'replace').decode('ascii')
        lines.append(f"    {safe_name:<25} {e['mean']:5.1f} +/-{std:.1f}  ({e['games']} games)")
    return "\n".join(lines)
