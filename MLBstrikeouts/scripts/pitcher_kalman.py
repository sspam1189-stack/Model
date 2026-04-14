# MLBstrikeouts/scripts/pitcher_kalman.py
# Per-pitcher Kalman filter for MLB pitcher prop projections.
#
# Direct adaptation of pyNBAPROPS/scripts/player_kalman.py but tuned for
# pitcher stats and the unique cadence of starting pitchers.
#
# Key differences from the NBA player Kalman:
#   - Pitchers start every ~5 days (not every day like NBA players)
#   - Drift is per-day instead of per-game-slot, so uncertainty accumulates
#     across the 4-5 day gap between starts
#   - Observation noise is calibrated for pitcher stats (K, outs, H, BB)
#     rather than NBA box score stats
#   - Higher initial uncertainty since pitchers make ~32 starts/season
#     vs. ~82 games for NBA players
#   - Batch processing filters out relief appearances (only starts count)
#
# Why Kalman over simple rolling average for pitchers:
#   1. Adaptive weighting: A pitcher who suddenly gains a new pitch or loses
#      velocity creates large innovations that widen variance, making the
#      filter adapt faster to the new regime
#   2. Principled uncertainty: Variance tracks confidence in the baseline,
#      so a pitcher returning from IL has high uncertainty while an ace
#      with 10 consistent starts has low uncertainty
#   3. Rest-day drift: The filter naturally accounts for the ~5-day gap
#      between starts by accumulating process noise per day

import os
import json
import math
from datetime import datetime

# ---------------------------------------------------------------------------
# Hyperparameters
# ---------------------------------------------------------------------------

PITCHER_KALMAN_DEFAULTS = {
    # Process noise: variance increase per day not pitched.
    # Pitchers start every ~5 days, so drift accumulates between starts.
    # Higher than NBA (0.3) because more can change between appearances.
    "gameDrift": 0.5,

    # Observation noise per stat: how noisy a single start's output is
    # relative to the pitcher's true baseline. Higher = single game matters less.
    "obsNoise": {
        "k":    9.0,       # K std dev ~3.0 per game -> variance ~9
        "outs": 12.0,      # Outs std dev ~3.5 -> variance ~12
        "h":    4.0,       # Hits std dev ~2.0 -> variance ~4
        "bb":   2.5,       # Walks std dev ~1.6 -> variance ~2.5
    },

    # Initial variance for a new pitcher (high uncertainty, fewer starts than NBA games)
    "initialVar": 25.0,

    # Variance floor and ceiling
    "minVar": 0.5,         # Don't collapse to zero uncertainty
    "maxVar": 60.0,        # Cap uncertainty for pitchers with no data

    # Minimum starts before Kalman state is used (cold start protection)
    "minGamesForKalman": 2,

    # Blending: how much to trust Kalman mean vs. rolling average
    # 0.0 = pure rolling average, 1.0 = pure Kalman
    # We blend because Kalman is better for regime changes but rolling avg
    # is more robust when sample size is small.
    "kalmanBlend": 0.6,    # 60% Kalman, 40% rolling avg
}

# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def new_pitcher_kalman_state():
    """Create empty Kalman state container for pitchers."""
    return {
        "pitchers": {},
        "processedGames": {},
        "meta": {
            "version": "1.0",
            "created": datetime.now().isoformat(),
        },
    }


def load_pitcher_kalman_state(path):
    """
    Load pitcher Kalman state from JSON file.

    Returns a fresh state if the file doesn't exist or is corrupt.
    """
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                state = json.load(f)
            if "pitchers" not in state:
                state["pitchers"] = {}
            if "processedGames" not in state:
                state["processedGames"] = {}
            return state
        except Exception:
            pass
    return new_pitcher_kalman_state()


def save_pitcher_kalman_state(state, path):
    """Save pitcher Kalman state to JSON file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Per-pitcher Kalman operations
# ---------------------------------------------------------------------------

def _ensure_pitcher(state, pitcher_id, pitcher_name, cfg=None):
    """Ensure a pitcher has a Kalman state entry."""
    cfg = cfg or PITCHER_KALMAN_DEFAULTS
    if pitcher_id not in state["pitchers"]:
        state["pitchers"][pitcher_id] = {
            "name": pitcher_name,
            "stats": {},
            "gamesProcessed": 0,
            "lastUpdateDate": None,
        }


def _ensure_stat(pitcher_state, stat_key, initial_value=None, cfg=None):
    """
    Ensure a pitcher has a Kalman state for a specific stat.

    If this is the first observation, initializes the mean to initial_value
    so the filter starts at the observed value rather than zero.
    """
    cfg = cfg or PITCHER_KALMAN_DEFAULTS
    if stat_key not in pitcher_state["stats"]:
        pitcher_state["stats"][stat_key] = {
            "mean": initial_value if initial_value is not None else 0.0,
            "var": cfg["initialVar"],
        }


# ---------------------------------------------------------------------------
# Kalman update
# ---------------------------------------------------------------------------

def update_pitcher_from_game(state, pitcher_id, pitcher_name, game_stats,
                              game_id=None, cfg=None):
    """
    Update a pitcher's Kalman state after observing a start.

    The Kalman update step:
      1. Innovation variance: S = prior_var + obs_noise
      2. Kalman gain: K = prior_var / S
      3. Update mean: mean += K * (observed - mean)
      4. Update variance: var = max(minVar, (1 - K) * var)

    Higher observation noise means the single-game result is less trusted,
    so the filter moves more slowly. Higher prior variance (from drift or
    cold start) means the filter trusts the new observation more.

    Parameters
    ----------
    state : dict
        Pitcher Kalman state container.
    pitcher_id : str
        Unique pitcher identifier.
    pitcher_name : str
        Pitcher display name.
    game_stats : dict
        Actual stats from the start: {"k": 8, "outs": 18, "h": 5, "bb": 2}
    game_id : str or None
        Unique game identifier to prevent double-processing.
    cfg : dict or None
        Hyperparameters (uses defaults if None).
    """
    cfg = cfg or PITCHER_KALMAN_DEFAULTS

    # Skip if already processed
    if game_id:
        proc_key = f"{pitcher_id}:{game_id}"
        if state["processedGames"].get(proc_key):
            return
        state["processedGames"][proc_key] = True

    _ensure_pitcher(state, pitcher_id, pitcher_name, cfg)
    ps = state["pitchers"][pitcher_id]
    ps["name"] = pitcher_name  # Update name in case it changed

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

        S = s["var"] + noise       # Innovation variance
        K = s["var"] / S            # Kalman gain

        innovation = actual_val - s["mean"]
        s["mean"] = s["mean"] + K * innovation
        s["var"] = max(cfg["minVar"], (1 - K) * s["var"])

    ps["gamesProcessed"] = ps.get("gamesProcessed", 0) + 1
    ps["lastUpdateDate"] = datetime.now().isoformat()


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------

def apply_drift(state, days_elapsed=1, cfg=None):
    """
    Apply process noise (drift) to all pitchers.

    Unlike the NBA version which drifts per game-slot, pitcher drift is
    per-day because pitchers don't play every day. Between starts (~5 days),
    uncertainty grows steadily:

      drift = gameDrift * days_elapsed

    For a typical 5-day rotation: drift = 0.5 * 5 = 2.5 variance added.
    This means after 5 days without a start, uncertainty grows by 2.5,
    which is enough to make the filter more responsive to the next
    observed start.

    Parameters
    ----------
    state : dict
        Pitcher Kalman state.
    days_elapsed : int
        Number of calendar days since last update.
    cfg : dict or None
        Hyperparameters.
    """
    cfg = cfg or PITCHER_KALMAN_DEFAULTS
    drift = cfg["gameDrift"] * days_elapsed

    for pid, ps in state["pitchers"].items():
        for stat_key, s in ps.get("stats", {}).items():
            s["var"] = min(cfg["maxVar"], s["var"] + drift)


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------

def get_pitcher_projection(state, pitcher_id, stat_key, rolling_avg=None, cfg=None):
    """
    Get the Kalman-informed projection for a pitcher-stat.

    If the pitcher has enough starts, blends Kalman mean with rolling average.
    Otherwise falls back to rolling average only.

    Parameters
    ----------
    state : dict
        Pitcher Kalman state.
    pitcher_id : str
        Pitcher identifier.
    stat_key : str
        Stat to project (k, outs, h, bb).
    rolling_avg : float or None
        Rolling weighted average from simple model (fallback).
    cfg : dict or None
        Hyperparameters.

    Returns
    -------
    dict
        {"proj": float, "var": float, "source": str}
        source is "kalman_blend", "kalman_only", "kalman_cold",
        "rolling_avg", or "no_data"
    """
    cfg = cfg or PITCHER_KALMAN_DEFAULTS

    ps = state["pitchers"].get(pitcher_id if isinstance(pitcher_id, str) else str(pitcher_id))
    if ps is None:
        # Also try string conversion
        ps = state["pitchers"].get(str(pitcher_id))

    if ps is None or stat_key not in ps.get("stats", {}):
        if rolling_avg is not None:
            return {"proj": rolling_avg, "var": cfg["initialVar"], "source": "rolling_avg"}
        return {"proj": 0.0, "var": cfg["initialVar"], "source": "no_data"}

    s = ps["stats"][stat_key]
    kalman_mean = s["mean"]
    kalman_var = s["var"]
    games = ps.get("gamesProcessed", 0)

    # Cold start: not enough starts for Kalman to be meaningful
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


# ---------------------------------------------------------------------------
# Batch operations
# ---------------------------------------------------------------------------

def batch_update_from_game_logs(state, game_logs, cfg=None):
    """
    Process a batch of game logs to update all pitcher Kalman states.

    Only processes starts (not relief appearances). A game log entry is
    treated as a start if it has an explicit "is_start" flag set to True,
    or if the pitcher recorded at least 3 innings pitched (outs >= 9).

    Parameters
    ----------
    state : dict
        Pitcher Kalman state.
    game_logs : list[dict]
        Game log entries. Each dict should have:
          - pitcher_id: str
          - pitcher_name: str
          - game_id: str (optional, for dedup)
          - k: int (strikeouts)
          - outs: int (outs recorded, i.e. IP * 3)
          - h: int (hits allowed)
          - bb: int (walks)
          - is_start: bool (optional, defaults to checking outs >= 9)
    cfg : dict or None
        Hyperparameters.

    Returns
    -------
    int
        Number of pitcher-starts processed.
    """
    cfg = cfg or PITCHER_KALMAN_DEFAULTS
    processed = 0

    for g in game_logs:
        pid = g.get("pitcher_id")
        pname = g.get("pitcher_name", "")
        game_id = g.get("game_id")

        if pid is None:
            continue

        # Only process starts, skip relief appearances
        is_start = g.get("is_start")
        if is_start is None:
            # Fallback: treat as start if outs >= 9 (3+ IP)
            outs = g.get("outs", 0)
            if outs < 9:
                continue
        elif not is_start:
            continue

        game_stats = {
            "k":    g.get("k", 0),
            "outs": g.get("outs", 0),
            "h":    g.get("h", 0),
            "bb":   g.get("bb", 0),
        }

        update_pitcher_from_game(state, str(pid), pname, game_stats, game_id, cfg)
        processed += 1

    return processed


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def prune_inactive_pitchers(state, max_games_since=50):
    """
    Remove pitchers who haven't started recently to keep state lean.

    Pitchers are pruned if they have zero games processed, a corrupt
    lastUpdateDate, or if more than max_games_since calendar days have
    passed since their last update.

    Parameters
    ----------
    state : dict
        Pitcher Kalman state.
    max_games_since : int
        Remove pitchers whose lastUpdateDate is more than this many days ago.
    """
    now = datetime.now()
    to_remove = []

    for pid, ps in state["pitchers"].items():
        last_update = ps.get("lastUpdateDate")
        if last_update is None:
            # No update date recorded — prune if zero games processed
            if ps.get("gamesProcessed", 0) == 0:
                to_remove.append(pid)
            continue
        try:
            last_dt = datetime.fromisoformat(last_update)
            days_since = (now - last_dt).days
            if days_since > max_games_since:
                to_remove.append(pid)
        except (ValueError, TypeError):
            # Corrupt date — prune
            to_remove.append(pid)

    for pid in to_remove:
        del state["pitchers"][pid]

    if to_remove:
        print(f"  [kalman] Pruned {len(to_remove)} inactive pitchers")


def kalman_summary(state, top_n=10, stat_key="k"):
    """
    Print summary of top pitchers by Kalman mean for a stat.

    Parameters
    ----------
    state : dict
        Pitcher Kalman state.
    top_n : int
        Number of pitchers to show.
    stat_key : str
        Stat to rank by (default: "k" for strikeouts).

    Returns
    -------
    str
        Formatted summary string.
    """
    entries = []
    for pid, ps in state["pitchers"].items():
        s = ps.get("stats", {}).get(stat_key)
        if s:
            entries.append({
                "name": ps.get("name", pid),
                "mean": s["mean"],
                "var": s["var"],
                "games": ps.get("gamesProcessed", 0),
            })

    entries.sort(key=lambda x: x["mean"], reverse=True)

    lines = [f"  [kalman] Top {top_n} pitchers by {stat_key} baseline:"]
    for e in entries[:top_n]:
        std = math.sqrt(e["var"])
        # Sanitize name for Windows cp1252 console
        safe_name = e["name"].encode("ascii", "replace").decode("ascii")
        lines.append(
            f"    {safe_name:<25} {e['mean']:5.1f} +/-{std:.1f}  ({e['games']} starts)"
        )
    return "\n".join(lines)
