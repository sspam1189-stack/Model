# MLBstrikeouts/scripts/batter_kalman.py
# Per-batter Kalman filter for MLB batter total bases prop projections.
#
# Direct adaptation of pitcher_kalman.py but tuned for batter stats and
# the daily cadence of position players.
#
# Key differences from the pitcher Kalman:
#   - Batters play daily (not every ~5 days like starting pitchers)
#   - Lower drift per day (0.15 vs 0.5) since daily games provide
#     continuous signal
#   - Two state variables (ISO, contact) instead of four (K, outs, H, BB)
#   - Lower initial variance (4.0 vs 25.0) since batters accumulate
#     more games faster
#   - Higher min games for Kalman (10 vs 2) since daily games mean
#     ~2 weeks is a reasonable cold-start window
#
# Why two state variables:
#   - ISO (isolated power) = (TB - H) / AB captures extra-base hit power
#   - contact rate = 1 - (K / PA) captures plate discipline and bat-on-ball
#   Together these drive total bases projections: ISO determines how many
#   extra bases per hit, contact determines how often the batter makes contact.

import os
import json
import math
from datetime import datetime

# ---------------------------------------------------------------------------
# Hyperparameters
# ---------------------------------------------------------------------------

BATTER_KALMAN_DEFAULTS = {
    # Process noise: variance increase per day without a game.
    # Batters play daily, so drift is lower than pitchers (0.5).
    "gameDrift": 0.15,

    # Observation noise per stat: how noisy a single game's output is
    # relative to the batter's true baseline. Higher = single game matters less.
    "obsNoise": {
        "iso": 0.05,       # ISO is noisy per game but stable over 15+ games
        "contact": 0.03,   # contact rate is more stable
    },

    # Initial variance for a new batter (lower than pitcher 25.0 — more games = faster convergence)
    "initialVar": 4.0,

    # Variance floor and ceiling
    "minVar": 0.01,        # Don't collapse to zero uncertainty
    "maxVar": 10.0,        # Cap uncertainty for batters with no data

    # Minimum games before Kalman state is used (cold start protection)
    # Need ~2 weeks of daily play
    "minGamesForKalman": 10,

    # Blending: how much to trust Kalman mean vs. rate model
    # 0.0 = pure rate model, 1.0 = pure Kalman
    # Lower than pitcher (0.6) because batters have more games for the
    # rate model to be reliable.
    "kalmanBlend": 0.4,    # 40% Kalman, 60% rate model
}

# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def new_batter_kalman_state():
    """Create empty Kalman state container for batters."""
    return {
        "batters": {},
        "processedGames": {},
        "meta": {
            "version": "1.0",
            "created": datetime.now().isoformat(),
        },
    }


def load_batter_kalman_state(path):
    """
    Load batter Kalman state from JSON file.

    Returns a fresh state if the file doesn't exist or is corrupt.
    """
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                state = json.load(f)
            if "batters" not in state:
                state["batters"] = {}
            if "processedGames" not in state:
                state["processedGames"] = {}
            return state
        except Exception:
            pass
    return new_batter_kalman_state()


def save_batter_kalman_state(state, path):
    """Save batter Kalman state to JSON file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Per-batter Kalman operations
# ---------------------------------------------------------------------------

def _ensure_batter(state, batter_id, batter_name, cfg=None):
    """Ensure a batter has a Kalman state entry."""
    cfg = cfg or BATTER_KALMAN_DEFAULTS
    if batter_id not in state["batters"]:
        state["batters"][batter_id] = {
            "name": batter_name,
            "stats": {},
            "gamesProcessed": 0,
            "lastUpdateDate": None,
        }


def _ensure_stat(batter_state, stat_key, initial_value=None, cfg=None):
    """
    Ensure a batter has a Kalman state for a specific stat.

    If this is the first observation, initializes the mean to initial_value
    so the filter starts at the observed value rather than zero.
    """
    cfg = cfg or BATTER_KALMAN_DEFAULTS
    if stat_key not in batter_state["stats"]:
        batter_state["stats"][stat_key] = {
            "mean": initial_value if initial_value is not None else 0.0,
            "var": cfg["initialVar"],
        }


# ---------------------------------------------------------------------------
# Kalman update
# ---------------------------------------------------------------------------

def update_batter_from_game(state, batter_id, batter_name, game_stats,
                             game_id=None, cfg=None):
    """
    Update a batter's Kalman state after observing a game.

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
        Batter Kalman state container.
    batter_id : str
        Unique batter identifier.
    batter_name : str
        Batter display name.
    game_stats : dict
        Computed per-game rates: {"iso": float, "contact": float}
        Values of None are skipped (e.g. 0 AB game).
    game_id : str or None
        Unique game identifier to prevent double-processing.
    cfg : dict or None
        Hyperparameters (uses defaults if None).
    """
    cfg = cfg or BATTER_KALMAN_DEFAULTS

    # Skip if already processed
    if game_id:
        proc_key = f"{batter_id}:{game_id}"
        if state["processedGames"].get(proc_key):
            return
        state["processedGames"][proc_key] = True

    _ensure_batter(state, batter_id, batter_name, cfg)
    bs = state["batters"][batter_id]
    bs["name"] = batter_name  # Update name in case it changed

    obs_noise = cfg.get("obsNoise", {})

    for stat_key, actual_val in game_stats.items():
        if stat_key not in obs_noise:
            continue  # Only track stats we have noise estimates for
        if actual_val is None:
            continue

        actual_val = float(actual_val)
        _ensure_stat(bs, stat_key, initial_value=actual_val, cfg=cfg)

        s = bs["stats"][stat_key]
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

    bs["gamesProcessed"] = bs.get("gamesProcessed", 0) + 1
    bs["lastUpdateDate"] = datetime.now().isoformat()


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------

def apply_drift(state, current_date=None, cfg=None):
    """
    Apply process noise (drift) to all batters based on days since last game.

    Unlike pitchers who drift on a fixed days_elapsed parameter, batter drift
    is computed per-batter from the gap between current_date and their
    lastUpdateDate. This handles rest days, off-days, and IL stints naturally.

      drift = gameDrift * days_since_last_update

    For a batter who played yesterday: drift = 0.15 * 1 = 0.15
    For a batter on a 3-day break: drift = 0.15 * 3 = 0.45

    Parameters
    ----------
    state : dict
        Batter Kalman state.
    current_date : str or None
        ISO date string (e.g. "2026-04-15"). Uses today if None.
    cfg : dict or None
        Hyperparameters.
    """
    cfg = cfg or BATTER_KALMAN_DEFAULTS

    if current_date is None:
        now = datetime.now()
    else:
        now = datetime.fromisoformat(current_date) if isinstance(current_date, str) else current_date

    for bid, bs in state["batters"].items():
        last_update = bs.get("lastUpdateDate")
        if last_update is None:
            continue
        try:
            last_dt = datetime.fromisoformat(last_update)
            days_elapsed = max(0, (now - last_dt).days)
        except (ValueError, TypeError):
            days_elapsed = 1  # Fallback if date is corrupt

        if days_elapsed <= 0:
            continue

        drift = cfg["gameDrift"] * days_elapsed

        for stat_key, s in bs.get("stats", {}).items():
            s["var"] = min(cfg["maxVar"], s["var"] + drift)


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------

def get_batter_projection(state, batter_id, stats=None, cfg=None):
    """
    Get the Kalman-informed projection for a batter's ISO and contact rate.

    If the batter has enough games, blends Kalman mean with rate model values.
    Otherwise falls back to rate model only.

    Parameters
    ----------
    state : dict
        Batter Kalman state.
    batter_id : str
        Batter identifier.
    stats : dict or None
        Rate model values to blend with, e.g. {"iso": 0.18, "contact": 0.78}.
        If None, returns Kalman-only values.
    cfg : dict or None
        Hyperparameters.

    Returns
    -------
    dict
        Per-stat projections:
        {
            "iso": {"proj": float, "var": float, "source": str},
            "contact": {"proj": float, "var": float, "source": str},
        }
        source is "kalman_blend", "kalman_only", "kalman_cold",
        "rate_model", or "no_data"
    """
    cfg = cfg or BATTER_KALMAN_DEFAULTS
    stats = stats or {}

    bs = state["batters"].get(batter_id if isinstance(batter_id, str) else str(batter_id))
    if bs is None:
        # Also try string conversion
        bs = state["batters"].get(str(batter_id))

    result = {}
    for stat_key in ["iso", "contact"]:
        rate_val = stats.get(stat_key)

        if bs is None or stat_key not in bs.get("stats", {}):
            if rate_val is not None:
                result[stat_key] = {"proj": rate_val, "var": cfg["initialVar"], "source": "rate_model"}
            else:
                result[stat_key] = {"proj": 0.0, "var": cfg["initialVar"], "source": "no_data"}
            continue

        s = bs["stats"][stat_key]
        kalman_mean = s["mean"]
        kalman_var = s["var"]
        games = bs.get("gamesProcessed", 0)

        # Cold start: not enough games for Kalman to be meaningful
        if games < cfg["minGamesForKalman"]:
            if rate_val is not None:
                result[stat_key] = {"proj": rate_val, "var": kalman_var, "source": "rate_model"}
            else:
                result[stat_key] = {"proj": kalman_mean, "var": kalman_var, "source": "kalman_cold"}
            continue

        # Blend Kalman mean with rate model
        if rate_val is not None:
            blend = cfg["kalmanBlend"]
            proj = blend * kalman_mean + (1 - blend) * rate_val
            result[stat_key] = {"proj": proj, "var": kalman_var, "source": "kalman_blend"}
        else:
            result[stat_key] = {"proj": kalman_mean, "var": kalman_var, "source": "kalman_only"}

    return result


# ---------------------------------------------------------------------------
# Batch operations
# ---------------------------------------------------------------------------

def batch_update_from_game_logs(state, batter_logs, defaults=None):
    """
    Process a batch of game logs to update all batter Kalman states.

    Computes per-game ISO and contact rate from raw box score stats,
    then runs the Kalman update. Games where the batter had 0 AB or
    0 PA (pinch-run only, etc.) are skipped.

    Parameters
    ----------
    state : dict
        Batter Kalman state.
    batter_logs : list[dict]
        Game log entries. Each dict should have:
          - batter_id: str
          - batter_name: str
          - game_id: str (optional, for dedup)
          - ab: int (at bats)
          - pa: int (plate appearances)
          - h: int (hits)
          - tb: int (total bases)
          - k: int (strikeouts)
    defaults : dict or None
        Hyperparameters (uses BATTER_KALMAN_DEFAULTS if None).

    Returns
    -------
    int
        Number of batter-games processed.
    """
    cfg = defaults or BATTER_KALMAN_DEFAULTS
    processed = 0

    for g in batter_logs:
        bid = g.get("batter_id")
        bname = g.get("batter_name", "")
        game_id = g.get("game_id")

        if bid is None:
            continue

        ab = g.get("ab", 0)
        pa = g.get("pa", 0)

        # Skip games where batter had 0 AB or 0 PA (pinch-run only, etc.)
        if ab <= 0 or pa <= 0:
            continue

        tb = g.get("tb", 0)
        h = g.get("h", 0)
        k = g.get("k", 0)

        # Compute per-game observations
        obs_iso = (tb - h) / ab if ab > 0 else None
        obs_contact = 1.0 - (k / pa) if pa > 0 else None

        game_stats = {
            "iso": obs_iso,
            "contact": obs_contact,
        }

        update_batter_from_game(state, str(bid), bname, game_stats, game_id, cfg)
        processed += 1

    return processed


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def prune_inactive_batters(state, active_ids=None, max_inactive_days=30):
    """
    Remove batters who haven't played recently to keep state lean.

    Batters are pruned if:
      - They are not in active_ids (if provided), OR
      - They have zero games processed, OR
      - Their lastUpdateDate is more than max_inactive_days ago

    Parameters
    ----------
    state : dict
        Batter Kalman state.
    active_ids : set or list or None
        Set of currently active batter IDs. If None, only time-based pruning.
    max_inactive_days : int
        Remove batters whose lastUpdateDate is more than this many days ago.
    """
    now = datetime.now()
    to_remove = []

    active_set = set(str(x) for x in active_ids) if active_ids is not None else None

    for bid, bs in state["batters"].items():
        # If active_ids provided, prune anyone not in it (with time check)
        if active_set is not None and bid not in active_set:
            to_remove.append(bid)
            continue

        last_update = bs.get("lastUpdateDate")
        if last_update is None:
            # No update date recorded — prune if zero games processed
            if bs.get("gamesProcessed", 0) == 0:
                to_remove.append(bid)
            continue
        try:
            last_dt = datetime.fromisoformat(last_update)
            days_since = (now - last_dt).days
            if days_since > max_inactive_days:
                to_remove.append(bid)
        except (ValueError, TypeError):
            # Corrupt date — prune
            to_remove.append(bid)

    for bid in to_remove:
        del state["batters"][bid]

    if to_remove:
        print(f"  [kalman] Pruned {len(to_remove)} inactive batters")


def kalman_summary(state, top_n=10, stat_key="iso"):
    """
    Print summary of top batters by Kalman mean for a stat.

    Parameters
    ----------
    state : dict
        Batter Kalman state.
    top_n : int
        Number of batters to show.
    stat_key : str
        Stat to rank by (default: "iso" for isolated power).

    Returns
    -------
    str
        Formatted summary string.
    """
    entries = []
    for bid, bs in state["batters"].items():
        s = bs.get("stats", {}).get(stat_key)
        if s:
            entries.append({
                "name": bs.get("name", bid),
                "mean": s["mean"],
                "var": s["var"],
                "games": bs.get("gamesProcessed", 0),
            })

    entries.sort(key=lambda x: x["mean"], reverse=True)

    lines = [f"  [kalman] Top {top_n} batters by {stat_key} baseline:"]
    for e in entries[:top_n]:
        std = math.sqrt(e["var"])
        # Sanitize name for Windows cp1252 console
        safe_name = e["name"].encode("ascii", "replace").decode("ascii")
        lines.append(
            f"    {safe_name:<25} {e['mean']:.3f} +/-{std:.3f}  ({e['games']} games)"
        )
    return "\n".join(lines)
