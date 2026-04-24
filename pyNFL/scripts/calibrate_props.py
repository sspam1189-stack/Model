# pyNFL/scripts/calibrate_props.py
# Adaptive per-market bias calibration using a diagonal Kalman filter.
#
# Tracks residual bias (actual - proj) per market as a learnable parameter.
# Each graded pick is one observation; posterior mean/variance are updated
# recursively with recency weighting. Clamped and variance-floored so new
# data keeps shifting the estimate even after convergence.
#
# Lifecycle:
#   1. Seeded from 2025 backtest (`--seed`) -> initial bias per market.
#   2. Each week after grading, `--update` feeds new residuals.
#   3. props_engine.py reads `prop_calibration.json` and applies bias
#      correction: proj_adjusted = proj + bias[market].
#
# Year boundaries: use --widen-variance to inflate posterior variance when
# starting a new season (more uncertainty -> Kalman adapts faster early).
#
# State file: pyNFL/data/prop_calibration.json
#   {
#     "pass_tds":   {"bias": -1.93, "var": 0.40, "n": 110, "last_updated": "2025_W22"},
#     "rec_yds":    {"bias":  4.89, "var": 5.20, "n": 2060, ...},
#     ...
#   }
#
# bias convention: bias = actual - proj (what to ADD to projection).

import argparse
import json
import math
import os
from collections import defaultdict

# Markets we track
MARKETS = ["pass_yds", "pass_tds", "rush_yds", "rush_att", "rec_yds", "receptions"]

# Per-market observation noise (points^2). Bigger = each game tells us less.
# Rough 1.5x the EMPIRICAL_STD^2 so a single outlier doesn't swing posterior.
OBS_NOISE = {
    "pass_yds":   4500.0,
    "pass_tds":      2.5,
    "rush_yds":   2200.0,
    "rush_att":     60.0,
    "rec_yds":    1600.0,
    "receptions":    5.5,
}

# Clamp range for bias (prevent runaway on sparse markets)
BIAS_CLAMP = {
    "pass_yds":   (-30.0, 30.0),
    "pass_tds":    (-2.5,  2.5),
    "rush_yds":   (-25.0, 25.0),
    "rush_att":    (-6.0,  6.0),
    "rec_yds":    (-20.0, 20.0),
    "receptions":  (-3.0,  3.0),
}

# Posterior variance floor — stops Kalman gain from collapsing to 0.
VAR_FLOOR = {
    "pass_yds":   2.0,
    "pass_tds":   0.1,
    "rush_yds":   1.5,
    "rush_att":   0.3,
    "rec_yds":    1.2,
    "receptions": 0.15,
}

# Initial posterior variance (wide -> fast adaptation from scratch).
INITIAL_VAR = {
    "pass_yds":   60.0,
    "pass_tds":    1.0,
    "rush_yds":   50.0,
    "rush_att":    6.0,
    "rec_yds":    30.0,
    "receptions":  2.0,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _init_state():
    """Fresh calibration state with wide priors (bias=0, large var)."""
    return {
        m: {"bias": 0.0, "var": INITIAL_VAR[m], "n": 0, "last_updated": ""}
        for m in MARKETS
    }


def load_state(path):
    """Load calibration state or initialize if file doesn't exist."""
    if not os.path.exists(path):
        return _init_state()
    try:
        with open(path) as f:
            state = json.load(f)
    except Exception:
        return _init_state()
    # Backfill any missing markets
    for m in MARKETS:
        if m not in state:
            state[m] = {"bias": 0.0, "var": INITIAL_VAR[m], "n": 0, "last_updated": ""}
    return state


def save_state(state, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Kalman update — one market, one batch of graded picks
# ---------------------------------------------------------------------------

def _kalman_update(market, mstate, residuals, recency_weights=None):
    """
    Fold a list of (actual - proj) residuals into the market's posterior.

    Standard scalar Kalman: each observation is a noisy measurement of the
    latent bias. Gain = var / (var + obs_noise). Posterior mean shifts
    toward the weighted average of observation and prior; variance shrinks.

    Returns the updated {bias, var, n} dict.
    """
    bias = mstate["bias"]
    var = mstate["var"]
    n = mstate["n"]

    obs_noise = OBS_NOISE[market]
    floor = VAR_FLOOR[market]
    lo, hi = BIAS_CLAMP[market]

    if recency_weights is None:
        recency_weights = [1.0] * len(residuals)

    for r, w in zip(residuals, recency_weights):
        if not math.isfinite(r):
            continue
        if w <= 0:
            continue
        # Recency scales observation noise: older games contribute less
        eff_noise = obs_noise / w
        K = var / (var + eff_noise)
        bias = bias + K * (r - bias)
        var = max(floor, var * (1 - K))
        n += 1

    bias = _clamp(bias, lo, hi)
    return {"bias": round(bias, 4), "var": round(var, 4), "n": n}


# ---------------------------------------------------------------------------
# Driver: update from a graded nfl-props.json snapshot
# ---------------------------------------------------------------------------

def update_from_graded(props_path, calib_path, recency_window=40,
                        season_widen=None):
    """
    Read a graded nfl-props.json and update calibration for each market.

    Recency weighting: most-recent `recency_window` picks get full weight;
    older picks decay linearly to 0.25 weight at 2x the window.

    Season-boundary detection: if the incoming picks include a season we
    haven't seen before, auto-widen posterior variance by `season_widen`
    before folding in new data. This lets the Kalman adapt faster to a
    new regime (rule changes, league-wide pace shifts) while still using
    last season's posterior as a prior. Pass `season_widen=None` to
    disable.
    """
    with open(props_path) as f:
        data = json.load(f)
    picks = data.get("props", data) if isinstance(data, dict) else data

    # Group picks by market, preserve order (assumed chronological)
    by_market = defaultdict(list)
    seasons_in_picks = set()
    for p in picks:
        m = p.get("market")
        if m not in MARKETS:
            continue
        actual = p.get("actual")
        proj = p.get("proj")
        if actual is None or proj is None:
            continue
        by_market[m].append(actual - proj)
        s = p.get("season")
        if s is not None:
            seasons_in_picks.add(int(s))

    state = load_state(calib_path)

    # Auto-widen on season boundary: if we see a season not in state,
    # widen posterior variance once before applying updates.
    if season_widen and seasons_in_picks:
        seen_seasons = set()
        for m in MARKETS:
            seen = state.get(m, {}).get("seen_seasons", [])
            seen_seasons.update(int(x) for x in seen)
        new_seasons = seasons_in_picks - seen_seasons
        if new_seasons and seen_seasons:  # only widen if we have a prior
            print(f"[calibrate] New season(s) {sorted(new_seasons)} detected -> widening variance by {season_widen}x")
            for m in MARKETS:
                state[m]["var"] = min(
                    INITIAL_VAR[m],
                    state[m].get("var", INITIAL_VAR[m]) * season_widen,
                )

        # Track seasons we've now processed
        all_seasons = sorted(seen_seasons | seasons_in_picks)
        for m in MARKETS:
            state[m]["seen_seasons"] = all_seasons

    for m, residuals in by_market.items():
        # Linear recency: newest = 1.0, older = 0.25
        n = len(residuals)
        weights = []
        for i, _ in enumerate(residuals):
            pos = n - i  # 1 for newest, n for oldest
            if pos <= recency_window:
                w = 1.0
            elif pos <= recency_window * 2:
                w = 1.0 - 0.75 * (pos - recency_window) / recency_window
            else:
                w = 0.25
            weights.append(w)

        updated = _kalman_update(m, state[m], residuals, weights)
        updated["last_updated"] = data.get("week") or data.get("date") or ""
        state[m] = updated

    save_state(state, calib_path)
    return state


def seed_from_backtest(props_path, calib_path, reset=True):
    """
    Seed calibration from a full backtest file. If `reset=True`, discard
    any existing state and start fresh.
    """
    if reset and os.path.exists(calib_path):
        os.remove(calib_path)
    return update_from_graded(props_path, calib_path)


def widen_variance(calib_path, multiplier=3.0):
    """
    Widen posterior variance at year boundary so Kalman adapts faster
    to the new season's regime. Keeps bias, inflates uncertainty.
    """
    state = load_state(calib_path)
    for m in MARKETS:
        state[m]["var"] = min(
            INITIAL_VAR[m],  # cap at initial wide prior
            state[m]["var"] * multiplier
        )
    save_state(state, calib_path)
    return state


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="NFL prop calibration Kalman filter")
    parser.add_argument(
        "--props", default=None,
        help="Path to graded nfl-props.json (default: pyNFL/data/nfl-props.json)"
    )
    parser.add_argument(
        "--calib", default=None,
        help="Path to prop_calibration.json (default: pyNFL/data/prop_calibration.json)"
    )
    parser.add_argument("--seed", action="store_true",
                        help="Reset state and seed from backtest")
    parser.add_argument("--update", action="store_true",
                        help="Fold new graded picks into existing state")
    parser.add_argument("--widen", type=float, default=None,
                        help="Widen posterior variance by multiplier (year boundary)")
    parser.add_argument("--reset-season", action="store_true",
                        help="Zero out bias and reset variance to initial priors. "
                             "Use before Week 1 of a new season for cold-start "
                             "(no prior-season bias transfer).")
    parser.add_argument("--show", action="store_true",
                        help="Print current state and exit")

    args = parser.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(here, "..", "data")
    props_path = args.props or os.path.join(data_dir, "nfl-props.json")
    calib_path = args.calib or os.path.join(data_dir, "prop_calibration.json")

    if args.show:
        state = load_state(calib_path)
        _print_state(state)
        return

    if args.widen is not None:
        state = widen_variance(calib_path, args.widen)
        print(f"Widened variance by {args.widen}x")
        _print_state(state)
        return

    if args.reset_season:
        state = _init_state()
        save_state(state, calib_path)
        print("Reset calibration: bias=0, variance=initial priors (cold start)")
        _print_state(state)
        return

    if args.seed:
        state = seed_from_backtest(props_path, calib_path, reset=True)
        print(f"Seeded calibration from {props_path}")
    elif args.update:
        state = update_from_graded(props_path, calib_path)
        print(f"Updated calibration from {props_path}")
    else:
        parser.print_help()
        return

    _print_state(state)


def _print_state(state):
    print()
    print(f"{'market':<12} | {'bias':>8} | {'var':>7} | {'n':>6}")
    print("-" * 42)
    for m in MARKETS:
        s = state.get(m, {})
        print(f"{m:<12} | {s.get('bias', 0):+8.3f} | {s.get('var', 0):7.3f} | {s.get('n', 0):6d}")


if __name__ == "__main__":
    main()
