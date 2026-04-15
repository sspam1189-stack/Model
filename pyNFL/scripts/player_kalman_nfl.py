# pyNFL/scripts/player_kalman_nfl.py
# Per-player Kalman filter for NFL player prop projections.
#
# Tracks per-player per-stat baselines for two categories:
#   - Shares: target_share, rush_share (role indicators)
#   - Efficiency rates: YPA, completion_pct, td_rate, ypc, catch_rate, ypr
#
# NFL-specific differences from NBA version:
#   - Higher gameDrift (0.5 vs 0.3) due to weekly cadence — more can change
#     between games when you only play once per week
#   - Lower minGamesForKalman (3 vs 5) since NFL has only 17-game seasons
#   - Lower kalmanBlend (0.5 vs 0.6) — less Kalman trust with small samples
#   - Season reset: state starts fresh each season, no carryover
#   - Separate obsNoise tuning for rate stats vs. share stats

import os
import json
import math
from datetime import datetime

# ---------------------------------------------------------------------------
# Hyperparameters
# ---------------------------------------------------------------------------

PLAYER_KALMAN_NFL_DEFAULTS = {
    # Process noise: how much baseline drifts per game not played
    # Higher than NBA (0.3) because NFL weekly cadence means more can change
    # between observations (scheme changes, injury recovery, game plan shifts)
    "gameDrift": 0.5,

    # Observation noise per stat: how noisy a single game's output is
    # relative to the player's true baseline. Higher = single game matters less.
    "obsNoise": {
        # Efficiency rates
        "ypa":            4.0,    # Yards per attempt — high single-game variance
        "completion_pct": 0.01,   # Completion % as decimal (0-1 scale)
        "td_rate":        0.005,  # TD rate as decimal — very noisy per game
        "ypc":            3.0,    # Yards per carry — high variance
        "catch_rate":     0.02,   # Catch rate as decimal (0-1 scale)
        "ypr":            8.0,    # Yards per reception — high single-game variance

        # Raw counting stats (for markets where rate×volume decomposition fails)
        "pass_yds":       3000.0, # ~55 yd std dev — passing yards are very noisy

        # Share stats (role indicators)
        "target_share":   0.01,   # Target share (0-1 scale) — relatively stable
        "rush_share":     0.02,   # Rush share (0-1 scale) — can shift with game script
    },

    # Initial variance for a new player (high uncertainty)
    "initialVar": 25.0,

    # Variance floor and ceiling
    "minVar": 0.3,              # Don't collapse to zero uncertainty
    "maxVar": 60.0,             # Cap uncertainty for players with no data

    # Minimum games before Kalman state is used (cold start protection)
    # Lower than NBA (5) because NFL has only 17-game regular season
    "minGamesForKalman": 3,

    # Blending: how much to trust Kalman mean vs. rolling average
    # 0.0 = pure rolling average, 1.0 = pure Kalman
    # Lower than NBA (0.6) — less trust with small NFL sample sizes
    "kalmanBlend": 0.5,
}

# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def new_player_kalman_state():
    """Create empty Kalman state container."""
    return {
        "players": {},
        "processedGames": {},
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
    cfg = cfg or PLAYER_KALMAN_NFL_DEFAULTS
    if player_id not in state["players"]:
        state["players"][player_id] = {
            "name": player_name,
            "stats": {},
            "gamesProcessed": 0,
        }


def _ensure_stat(player_state, stat_key, initial_value=None, cfg=None):
    """Ensure a player has a Kalman state for a specific stat."""
    cfg = cfg or PLAYER_KALMAN_NFL_DEFAULTS
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
        Actual stats from the game, e.g.:
        {"ypa": 7.2, "completion_pct": 0.65, "td_rate": 0.05,
         "target_share": 0.28, "rush_share": 0.15, ...}
    game_id : str or None
        Unique game identifier to prevent double-processing.
    cfg : dict or None
        Hyperparameters (uses defaults if None).
    """
    cfg = cfg or PLAYER_KALMAN_NFL_DEFAULTS

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


def apply_drift(state, games_elapsed=1, cfg=None):
    """
    Apply process noise (drift) to all players.

    Call this between game weeks to increase uncertainty for players
    who haven't played recently. In NFL context, games_elapsed=1
    typically means one week has passed.

    Parameters
    ----------
    state : dict
        Player Kalman state.
    games_elapsed : int
        Number of potential game slots since last update (usually weeks).
    cfg : dict or None
        Hyperparameters.
    """
    cfg = cfg or PLAYER_KALMAN_NFL_DEFAULTS
    drift = cfg["gameDrift"] * games_elapsed

    for pid, ps in state["players"].items():
        for stat_key, s in ps.get("stats", {}).items():
            s["var"] = min(cfg["maxVar"], s["var"] + drift)


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
        Stat to project (ypa, completion_pct, target_share, etc.).
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
    cfg = cfg or PLAYER_KALMAN_NFL_DEFAULTS

    ps = state["players"].get(player_id if isinstance(player_id, str) else str(player_id))
    if ps is None:
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
        Player-game dicts with keys:
            player_id, player_name, game_id,
            ypa, completion_pct, td_rate, ypc, catch_rate, ypr,
            target_share, rush_share
    cfg : dict or None
        Hyperparameters.

    Returns
    -------
    int
        Number of player-games processed.
    """
    cfg = cfg or PLAYER_KALMAN_NFL_DEFAULTS
    processed = 0

    for g in game_logs:
        pid = g.get("player_id")
        pname = g.get("player_name", "")
        game_id = g.get("game_id")

        if pid is None:
            continue

        # Build game_stats from available fields
        game_stats = {}
        for stat_key in cfg["obsNoise"]:
            val = g.get(stat_key)
            if val is not None:
                game_stats[stat_key] = val

        if not game_stats:
            continue  # No trackable stats in this record

        update_player_from_game(state, str(pid), pname, game_stats, game_id, cfg)
        processed += 1

    return processed


def prune_inactive_players(state, max_games_since=20):
    """Remove players who have zero processed games to keep state lean."""
    to_remove = []
    for pid, ps in state["players"].items():
        if ps.get("gamesProcessed", 0) == 0:
            to_remove.append(pid)
    for pid in to_remove:
        del state["players"][pid]
    if to_remove:
        print(f"  [kalman-nfl] Pruned {len(to_remove)} inactive players")


def kalman_summary(state, top_n=10, stat_key="ypa"):
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

    lines = [f"  [kalman-nfl] Top {top_n} players by {stat_key} baseline:"]
    for e in entries[:top_n]:
        std = math.sqrt(e["var"])
        # Sanitize name for Windows cp1252 console
        safe_name = e['name'].encode('ascii', 'replace').decode('ascii')
        lines.append(f"    {safe_name:<25} {e['mean']:7.3f} +/-{std:.3f}  ({e['games']} games)")
    return "\n".join(lines)
