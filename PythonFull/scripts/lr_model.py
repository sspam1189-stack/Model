#!/usr/bin/env python3
# scripts/lr_model.py
# ---------------------------------------------------------------------------
# Logistic Regression confirmation model for the Full season (NBA + NCAA).
#
# Uses team ATS momentum, O/U trend, line context, rest, and performance
# features extracted from history.json runs. No tournament features -- this
# model covers regular-season games only.
#
# Public API:
#   build_team_histories(store)             -> dict[team -> history]
#   extract_lr_features(home_hist, away_hist, game, home_lines, away_lines) -> list
#   LR_FEATURE_NAMES                        -> list[str]
#   train_lr_model(store, min_games=80)     -> dict (model bundle)
#   predict_lr(model_bundle, features)      -> dict
#   load_or_train_lr(store)                 -> dict (model bundle)
# ---------------------------------------------------------------------------

import json
import math
import os
import time
from collections import defaultdict

try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    import numpy as np
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.join(_DIR, "..", "data")
_MODEL_DIR = os.path.join(_DATA, "lr_models")

# ---------------------------------------------------------------------------
# Default thresholds (can be overridden by caller)
# ---------------------------------------------------------------------------

try:
    from defaults import LR_CONFIRM_THRESH, LR_VETO_THRESH
    LR_CONFIRM_THRESHOLD = LR_CONFIRM_THRESH
    LR_VETO_THRESHOLD = LR_VETO_THRESH
except ImportError:
    LR_CONFIRM_THRESHOLD = 0.60
    LR_VETO_THRESHOLD = 0.40

# ---------------------------------------------------------------------------
# Feature names -- 20 features total
# ---------------------------------------------------------------------------

LR_FEATURE_NAMES = [
    # ATS Momentum (6)
    "away_ats_pct_5",
    "home_ats_pct_5",
    "away_ats_pct_10",
    "home_ats_pct_10",
    "away_ats_pm_5",
    "home_ats_pm_5",
    # O/U Trend (4)
    "away_over_pct_10",
    "home_over_pct_10",
    "away_ou_pm_10",
    "home_ou_pm_10",
    # Line Context (3)
    "abs_line",
    "line_vs_team_avg",
    "home_is_fav",
    # Rest (3)
    "away_rest_days",
    "home_rest_days",
    "rest_advantage",
    # Performance (4)
    "away_win_pct_5",
    "home_win_pct_5",
    "away_avg_margin_5",
    "home_avg_margin_5",
]


# ---------------------------------------------------------------------------
# build_team_histories -- extract per-team game-by-game records from store
# ---------------------------------------------------------------------------

def build_team_histories(store):
    """Walk every graded game in `store` and build per-team chronological
    histories with ATS results, O/U results, margins, dates, rest days,
    and lines seen.

    Returns dict[team_name -> list[dict]], sorted by date ascending.
    Each entry:
        date, home, away, is_home, line, total, score, opp_score,
        margin, covered (bool), over (bool), rest_days, line_for_team
    """
    histories = defaultdict(list)

    for run in store.get("runs", []):
        run_date = run.get("date", "")
        if not run_date:
            continue
        for g in run.get("games", []):
            if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
                continue
            # Need actual scores to be graded
            if not isinstance(g.get("homeScore"), (int, float)):
                continue
            if not isinstance(g.get("awayScore"), (int, float)):
                continue
            if not isinstance(g.get("line"), (int, float)):
                continue
            if not isinstance(g.get("total"), (int, float)):
                continue

            home = g["home"]
            away = g["away"]
            home_score = g["homeScore"]
            away_score = g["awayScore"]
            line = g["line"]      # positive = home is underdog
            total = g["total"]
            actual_total = home_score + away_score

            # Home perspective
            home_margin = home_score - away_score
            # line is from home perspective: home +line.  If line > 0 home is dog.
            # Home covers when home_margin + line > 0
            home_covered = (home_margin + line) > 0
            game_over = actual_total > total

            base = {
                "date": run_date,
                "home": home,
                "away": away,
                "line": line,
                "total": total,
                "actual_total": actual_total,
            }

            histories[home].append({
                **base,
                "is_home": True,
                "score": home_score,
                "opp_score": away_score,
                "margin": home_margin,
                "covered": home_covered,
                "over": game_over,
                "line_for_team": line,  # home line
            })

            histories[away].append({
                **base,
                "is_home": False,
                "score": away_score,
                "opp_score": home_score,
                "margin": -home_margin,
                "covered": not home_covered,  # away covers when home doesn't
                "over": game_over,
                "line_for_team": -line,  # flip for away
            })

    # Sort each team's history by date
    for team in histories:
        histories[team].sort(key=lambda x: x["date"])

    # Compute rest_days
    for team in histories:
        games = histories[team]
        for i, g in enumerate(games):
            if i == 0:
                g["rest_days"] = 3  # assume normal rest for first game
            else:
                prev_date = games[i - 1]["date"]
                try:
                    from datetime import datetime
                    d1 = datetime.strptime(prev_date, "%Y%m%d")
                    d2 = datetime.strptime(g["date"], "%Y%m%d")
                    g["rest_days"] = max(0, (d2 - d1).days - 1)
                except Exception:
                    g["rest_days"] = 1

    return dict(histories)


def _safe_pct(wins, total):
    """Return win percentage 0-1, or 0.5 if no games."""
    if total <= 0:
        return 0.5
    return wins / total


def _safe_mean(values):
    """Return mean of values, or 0.0 if empty."""
    if not values:
        return 0.0
    return sum(values) / len(values)


def _team_features(hist, n_recent_5=5, n_recent_10=10):
    """Compute feature components for one team given its history up to (but
    not including) the current game.

    Returns dict with:
        ats_pct_5, ats_pct_10, ats_pm_5,
        over_pct_10, ou_pm_10,
        win_pct_5, avg_margin_5,
        avg_line, rest_days (of most recent game)
    """
    if not hist:
        return {
            "ats_pct_5": 0.5, "ats_pct_10": 0.5, "ats_pm_5": 0.0,
            "over_pct_10": 0.5, "ou_pm_10": 0.0,
            "win_pct_5": 0.5, "avg_margin_5": 0.0,
            "avg_line": 0.0, "rest_days": 1,
        }

    last5 = hist[-n_recent_5:]
    last10 = hist[-n_recent_10:]

    # ATS
    ats_pct_5 = _safe_pct(sum(1 for g in last5 if g["covered"]), len(last5))
    ats_pct_10 = _safe_pct(sum(1 for g in last10 if g["covered"]), len(last10))
    # ATS plus/minus (margin + line_for_team for last 5)
    ats_pm_5 = _safe_mean([g["margin"] + g["line_for_team"] for g in last5])

    # O/U
    over_pct_10 = _safe_pct(sum(1 for g in last10 if g["over"]), len(last10))
    ou_pm_10 = _safe_mean([g["actual_total"] - g["total"] for g in last10])

    # Performance
    win_pct_5 = _safe_pct(sum(1 for g in last5 if g["margin"] > 0), len(last5))
    avg_margin_5 = _safe_mean([g["margin"] for g in last5])

    # Line context
    avg_line = _safe_mean([g["line_for_team"] for g in hist])

    # Rest (from most recent game)
    rest_days = hist[-1].get("rest_days", 1) if hist else 1

    return {
        "ats_pct_5": ats_pct_5,
        "ats_pct_10": ats_pct_10,
        "ats_pm_5": ats_pm_5,
        "over_pct_10": over_pct_10,
        "ou_pm_10": ou_pm_10,
        "win_pct_5": win_pct_5,
        "avg_margin_5": avg_margin_5,
        "avg_line": avg_line,
        "rest_days": rest_days,
    }


# ---------------------------------------------------------------------------
# extract_lr_features
# ---------------------------------------------------------------------------

def extract_lr_features(home_hist, away_hist, game, home_lines=None, away_lines=None):
    """Build the 20-element feature vector for a single game.

    Parameters
    ----------
    home_hist : list[dict]  -- home team's history *before* this game
    away_hist : list[dict]  -- away team's history *before* this game
    game      : dict        -- must have 'line', 'total'
    home_lines : list[float] -- optional, all lines seen by home (for avg)
    away_lines : list[float] -- optional, all lines seen by away (for avg)

    Returns list[float] of length len(LR_FEATURE_NAMES), or None on error.
    """
    try:
        hf = _team_features(home_hist)
        af = _team_features(away_hist)

        line = game.get("line", 0) or 0
        abs_line = abs(line)

        # line_vs_team_avg: how unusual is this line for the home team?
        if home_lines:
            avg_home_line = _safe_mean(home_lines)
        else:
            avg_home_line = hf["avg_line"]
        line_vs_team_avg = line - avg_home_line

        home_is_fav = 1.0 if line < 0 else 0.0  # negative line = home favored

        # Rest -- use the game's rest if available, otherwise from history
        away_rest = af["rest_days"]
        home_rest = hf["rest_days"]
        rest_advantage = home_rest - away_rest

        features = [
            # ATS Momentum
            af["ats_pct_5"],
            hf["ats_pct_5"],
            af["ats_pct_10"],
            hf["ats_pct_10"],
            af["ats_pm_5"],
            hf["ats_pm_5"],
            # O/U Trend
            af["over_pct_10"],
            hf["over_pct_10"],
            af["ou_pm_10"],
            hf["ou_pm_10"],
            # Line Context
            abs_line,
            line_vs_team_avg,
            home_is_fav,
            # Rest
            away_rest,
            home_rest,
            rest_advantage,
            # Performance
            af["win_pct_5"],
            hf["win_pct_5"],
            af["avg_margin_5"],
            hf["avg_margin_5"],
        ]

        # Validate -- no NaN/Inf
        for i, v in enumerate(features):
            if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
                features[i] = 0.0

        return features

    except Exception:
        return None


# ---------------------------------------------------------------------------
# train_lr_model
# ---------------------------------------------------------------------------

def train_lr_model(store, min_games=80):
    """Train a LogisticRegression on graded games from the store.

    Target: home team covers the spread (binary).
    Only games where both teams have >= 5 prior graded games are used.

    Parameters
    ----------
    store     : dict with "runs" key
    min_games : int, minimum training samples required

    Returns dict:
        model       : fitted LogisticRegression
        scaler      : fitted StandardScaler
        n_train     : int
        accuracy    : float (in-sample)
        trained_at  : str (ISO timestamp)
    Or None if insufficient data.
    """
    if not HAS_SKLEARN:
        print("lr_model: scikit-learn not installed, cannot train")
        return None

    print("lr_model: Building team histories...")
    histories = build_team_histories(store)

    # Walk games chronologically and build training samples
    X_rows = []
    y_rows = []

    # We need to track running histories per team
    running = defaultdict(list)

    for run in store.get("runs", []):
        run_date = run.get("date", "")
        if not run_date:
            continue
        for g in run.get("games", []):
            if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
                continue
            if not isinstance(g.get("homeScore"), (int, float)):
                continue
            if not isinstance(g.get("awayScore"), (int, float)):
                continue
            if not isinstance(g.get("line"), (int, float)):
                continue
            if not isinstance(g.get("total"), (int, float)):
                continue

            home = g["home"]
            away = g["away"]

            # Need at least 5 games of history for both teams
            if len(running[home]) < 5 or len(running[away]) < 5:
                # Still record the game into running histories
                _append_to_running(running, g, run_date)
                continue

            # Extract features using history *before* this game
            features = extract_lr_features(
                running[home], running[away], g,
                home_lines=[x["line_for_team"] for x in running[home]],
                away_lines=[x["line_for_team"] for x in running[away]],
            )

            if features is None:
                _append_to_running(running, g, run_date)
                continue

            # Target: home covers
            home_margin = g["homeScore"] - g["awayScore"]
            line = g["line"]
            home_covered = 1 if (home_margin + line) > 0 else 0

            # Skip pushes (margin + line == 0)
            if (home_margin + line) == 0:
                _append_to_running(running, g, run_date)
                continue

            X_rows.append(features)
            y_rows.append(home_covered)

            # Now record the game into running histories
            _append_to_running(running, g, run_date)

    if len(X_rows) < min_games:
        print(f"lr_model: Only {len(X_rows)} training samples (need {min_games}), skipping")
        return None

    X = np.array(X_rows, dtype=np.float64)
    y = np.array(y_rows, dtype=np.int32)

    print(f"lr_model: Training on {len(X)} games (home covers: {y.sum()}, away covers: {len(y) - y.sum()})...")

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    model = LogisticRegression(
        penalty="l2",
        C=1.0,
        class_weight="balanced",
        max_iter=1000,
        solver="lbfgs",
        random_state=42,
    )
    model.fit(X_scaled, y)

    # In-sample accuracy
    preds = model.predict(X_scaled)
    accuracy = float(np.mean(preds == y))
    print(f"lr_model: In-sample accuracy: {accuracy:.3f}")

    bundle = {
        "model": model,
        "scaler": scaler,
        "n_train": len(X),
        "accuracy": accuracy,
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    # Save to disk
    _save_model(bundle)

    return bundle


def _append_to_running(running, g, run_date):
    """Append a graded game to the running per-team histories."""
    home = g["home"]
    away = g["away"]
    home_score = g["homeScore"]
    away_score = g["awayScore"]
    line = g["line"]
    total = g["total"]
    actual_total = home_score + away_score
    home_margin = home_score - away_score
    home_covered = (home_margin + line) > 0
    game_over = actual_total > total

    base = {
        "date": run_date,
        "home": home,
        "away": away,
        "line": line,
        "total": total,
        "actual_total": actual_total,
    }

    # Home entry
    home_entry = {
        **base,
        "is_home": True,
        "score": home_score,
        "opp_score": away_score,
        "margin": home_margin,
        "covered": home_covered,
        "over": game_over,
        "line_for_team": line,
    }
    # Compute rest days
    if running[home]:
        try:
            from datetime import datetime
            d1 = datetime.strptime(running[home][-1]["date"], "%Y%m%d")
            d2 = datetime.strptime(run_date, "%Y%m%d")
            home_entry["rest_days"] = max(0, (d2 - d1).days - 1)
        except Exception:
            home_entry["rest_days"] = 1
    else:
        home_entry["rest_days"] = 3

    running[home].append(home_entry)

    # Away entry
    away_entry = {
        **base,
        "is_home": False,
        "score": away_score,
        "opp_score": home_score,
        "margin": -home_margin,
        "covered": not home_covered,
        "over": game_over,
        "line_for_team": -line,
    }
    if running[away]:
        try:
            from datetime import datetime
            d1 = datetime.strptime(running[away][-1]["date"], "%Y%m%d")
            d2 = datetime.strptime(run_date, "%Y%m%d")
            away_entry["rest_days"] = max(0, (d2 - d1).days - 1)
        except Exception:
            away_entry["rest_days"] = 1
    else:
        away_entry["rest_days"] = 3

    running[away].append(away_entry)


# ---------------------------------------------------------------------------
# predict_lr
# ---------------------------------------------------------------------------

def predict_lr(model_bundle, features):
    """Run a single prediction.

    Parameters
    ----------
    model_bundle : dict from train_lr_model / load_or_train_lr
    features     : list[float] of length len(LR_FEATURE_NAMES)

    Returns
    -------
    dict with ``lr_prob`` (float) and ``lr_verdict`` ("CONFIRM"/"VETO"/"NEUTRAL").
    """
    if model_bundle is None or features is None:
        return {"lr_prob": None, "lr_verdict": "NEUTRAL"}
    if not HAS_SKLEARN:
        return {"lr_prob": None, "lr_verdict": "NEUTRAL"}

    try:
        model = model_bundle["model"]
        scaler = model_bundle["scaler"]

        X = np.array([features], dtype=np.float64)
        X_scaled = scaler.transform(X)

        proba = model.predict_proba(X_scaled)[0]
        prob = float(proba[1]) if len(proba) > 1 else float(proba[0])

        if prob >= LR_CONFIRM_THRESHOLD:
            verdict = "CONFIRM"
        elif prob <= LR_VETO_THRESHOLD:
            verdict = "VETO"
        else:
            verdict = "NEUTRAL"

        return {"lr_prob": round(prob, 3), "lr_verdict": verdict}
    except Exception as e:
        print(f"lr_model: predict error: {e}")
        return {"lr_prob": None, "lr_verdict": "NEUTRAL"}


# ---------------------------------------------------------------------------
# Model persistence
# ---------------------------------------------------------------------------

def _save_model(bundle):
    """Persist model + scaler to disk using joblib."""
    try:
        import joblib
    except ImportError:
        try:
            from sklearn.externals import joblib
        except ImportError:
            print("lr_model: joblib not available, model not saved to disk")
            return

    os.makedirs(_MODEL_DIR, exist_ok=True)
    path = os.path.join(_MODEL_DIR, "lr_latest.joblib")
    save_data = {
        "model": bundle["model"],
        "scaler": bundle["scaler"],
        "n_train": bundle["n_train"],
        "accuracy": bundle["accuracy"],
        "trained_at": bundle["trained_at"],
    }
    joblib.dump(save_data, path)
    print(f"lr_model: Saved to {path}")

    # Also save metadata as JSON for inspection
    meta_path = os.path.join(_MODEL_DIR, "lr_meta.json")
    meta = {
        "n_train": bundle["n_train"],
        "accuracy": round(bundle["accuracy"], 4),
        "trained_at": bundle["trained_at"],
        "features": LR_FEATURE_NAMES,
        "confirm_threshold": LR_CONFIRM_THRESHOLD,
        "veto_threshold": LR_VETO_THRESHOLD,
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)


def _load_model():
    """Load persisted model from disk. Returns bundle dict or None."""
    try:
        import joblib
    except ImportError:
        try:
            from sklearn.externals import joblib
        except ImportError:
            return None

    path = os.path.join(_MODEL_DIR, "lr_latest.joblib")
    if not os.path.exists(path):
        return None

    try:
        data = joblib.load(path)
        return {
            "model": data["model"],
            "scaler": data["scaler"],
            "n_train": data.get("n_train", 0),
            "accuracy": data.get("accuracy", 0),
            "trained_at": data.get("trained_at", "unknown"),
        }
    except Exception as e:
        print(f"lr_model: Failed to load from disk: {e}")
        return None


# ---------------------------------------------------------------------------
# load_or_train_lr -- main entry point
# ---------------------------------------------------------------------------

_RETRAIN_INTERVAL = 20  # retrain every N new graded games

def load_or_train_lr(store):
    """Load the LR model from disk, retraining if stale or missing.

    Retrains if:
      - No saved model exists
      - The store has >= _RETRAIN_INTERVAL more graded games than when
        the model was last trained

    Returns model bundle dict, or None if training fails.
    """
    if not HAS_SKLEARN:
        print("lr_model: scikit-learn not installed")
        return None

    # Count current graded games
    n_graded = 0
    for run in store.get("runs", []):
        for g in run.get("games", []):
            if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
                continue
            if isinstance(g.get("homeScore"), (int, float)) and isinstance(g.get("line"), (int, float)):
                n_graded += 1

    # Try loading from disk
    bundle = _load_model()
    if bundle is not None:
        n_train = bundle.get("n_train", 0)
        if n_graded - n_train < _RETRAIN_INTERVAL:
            print(f"lr_model: Loaded (trained on {n_train}, current graded: {n_graded})")
            return bundle
        else:
            print(f"lr_model: Stale (trained on {n_train}, current graded: {n_graded}), retraining...")

    # Train fresh
    return train_lr_model(store, min_games=80)


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    sys.path.insert(0, _DIR)
    from store import load_store

    store = load_store()
    bundle = load_or_train_lr(store)
    if bundle:
        print(f"\nModel ready: {bundle['n_train']} training samples, "
              f"accuracy {bundle['accuracy']:.3f}, "
              f"trained at {bundle['trained_at']}")
    else:
        print("\nFailed to load or train LR model.")
