# core/lr_model.py
# Unified Logistic Regression confirmation model.
#
# Provides shared infrastructure for LR-based spread confirmation:
#   - predict_lr / _explain_lr
#   - Model persistence (_save_model / _load_model)
#   - load_or_train_lr (staleness check + retrain)
#   - train_lr_model (walk-forward training scaffold)
#   - build_team_histories (per-team rolling game buffers)
#   - extract_lr_features (feature vector extraction)
#
# Config variables (set by sport wrapper via _configure):
#   MODEL_DIR, LR_CONFIRM_THRESH, LR_VETO_THRESH,
#   LR_FEATURE_NAMES, _FEATURE_LABELS,
#   RETRAIN_INTERVAL, MIN_TRAINING_GAMES,
#   EXTRA_FEATURE_FN (optional, for sport-specific extra features)

import json
import math
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta

try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    import numpy as np
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False

try:
    import joblib
    HAS_JOBLIB = True
except ImportError:
    try:
        from sklearn.externals import joblib
        HAS_JOBLIB = True
    except ImportError:
        HAS_JOBLIB = False
        joblib = None

# -- Configurable (set by sport wrapper) ------------------------------------

MODEL_DIR = None            # Must be set by wrapper
LR_CONFIRM_THRESH = 0.57
LR_VETO_THRESH = 0.43
RETRAIN_INTERVAL = 20
MIN_TRAINING_GAMES = 80

# Per-team feature configuration (3 features per team)
# Model predicts P(team covers) from the team's own perspective.
LR_FEATURE_NAMES = [
    "ats_pm_5",       # ATS margin last 5 games
    "line_vs_avg",    # today's spread vs team's avg spread
    "is_home",        # playing at home
]

_FEATURE_LABELS = {
    "ats_pm_5":       "{team} ATS margin L5",
    "line_vs_avg":    "{team} line vs avg",
    "is_home":        "{team} is home",
}

# Optional: sport-specific extra feature function
# Signature: extra_fn(game) -> list[float]
# Set by wrapper for NCAA tournament features, etc.
EXTRA_FEATURE_FN = None


def _configure(defaults_module):
    """Configure LR model from a sport-specific defaults module."""
    global MODEL_DIR, LR_CONFIRM_THRESH, LR_VETO_THRESH
    global LR_FEATURE_NAMES, _FEATURE_LABELS
    global RETRAIN_INTERVAL, MIN_TRAINING_GAMES, EXTRA_FEATURE_FN

    LR_CONFIRM_THRESH = getattr(defaults_module, "LR_CONFIRM_THRESH", LR_CONFIRM_THRESH)
    LR_VETO_THRESH = getattr(defaults_module, "LR_VETO_THRESH", LR_VETO_THRESH)
    RETRAIN_INTERVAL = getattr(defaults_module, "LR_RETRAIN_INTERVAL", RETRAIN_INTERVAL)
    MIN_TRAINING_GAMES = getattr(defaults_module, "LR_MIN_TRAINING_GAMES", MIN_TRAINING_GAMES)

    if hasattr(defaults_module, "LR_FEATURE_NAMES"):
        LR_FEATURE_NAMES = defaults_module.LR_FEATURE_NAMES
    if hasattr(defaults_module, "LR_FEATURE_LABELS"):
        _FEATURE_LABELS = defaults_module.LR_FEATURE_LABELS
    if hasattr(defaults_module, "LR_EXTRA_FEATURE_FN"):
        EXTRA_FEATURE_FN = defaults_module.LR_EXTRA_FEATURE_FN


def _get_model_dir():
    if MODEL_DIR:
        return MODEL_DIR
    raise RuntimeError("core.lr_model.MODEL_DIR not configured.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_pct(wins, total):
    if total <= 0:
        return 0.5
    return wins / total


def _safe_mean(values):
    if not values:
        return 0.0
    return sum(values) / len(values)


def _safe_float(v, default=0.0):
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _parse_date(d):
    try:
        return datetime.strptime(str(d).replace("-", "")[:8], "%Y%m%d")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# build_team_histories
# ---------------------------------------------------------------------------

def build_team_histories(store):
    """Walk every graded game in store, build per-team chronological histories.

    Returns dict[team_name -> list[dict]], sorted by date ascending.
    Each entry has fields needed for LR feature extraction.
    """
    histories = defaultdict(list)

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
            home_score = g["homeScore"]
            away_score = g["awayScore"]
            line = g["line"]
            total = g["total"]
            actual_total = home_score + away_score
            home_margin = home_score - away_score
            # Line convention: +X means AWAY favored by X (home is dog getting points),
            # -X means HOME favored by X (home is fav laying points).
            # home_spread = the points the home team gets (positive = getting, negative = laying).
            home_spread = line
            home_covered = (home_margin + home_spread) > 0
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
                "line_for_team": home_spread,
                "abs_line": abs(line),
                "won": home_score > away_score,
            })

            histories[away].append({
                **base,
                "is_home": False,
                "score": away_score,
                "opp_score": home_score,
                "margin": -home_margin,
                "covered": not home_covered,
                "over": game_over,
                "line_for_team": -home_spread,
                "abs_line": abs(line),
                "won": away_score > home_score,
            })

    # Sort each team's history by date
    for team in histories:
        histories[team].sort(key=lambda x: x["date"])

    # Compute rest_days
    for team in histories:
        games = histories[team]
        for i, g in enumerate(games):
            if i == 0:
                g["rest_days"] = 3
            else:
                prev_date = _parse_date(games[i - 1]["date"])
                cur_date = _parse_date(g["date"])
                if prev_date and cur_date:
                    g["rest_days"] = max(0, min((cur_date - prev_date).days - 1, 14))
                else:
                    g["rest_days"] = 1

    return dict(histories)


# ---------------------------------------------------------------------------
# _team_features -- compute rolling stats for one team
# ---------------------------------------------------------------------------

def _team_features(hist, n5=5):
    """Compute feature components for one team given history before current game."""
    if not hist:
        return {"ats_pm_5": 0.0, "avg_line": 0.0}

    last5 = hist[-n5:]
    ats_pm_5 = _safe_mean([g["margin"] + g["line_for_team"] for g in last5])
    avg_line = _safe_mean([g["line_for_team"] for g in hist])

    return {"ats_pm_5": ats_pm_5, "avg_line": avg_line}


# ---------------------------------------------------------------------------
# extract_lr_features
# ---------------------------------------------------------------------------

def extract_team_lr_features(team_hist, line_for_team, is_home):
    """Build the 3-feature vector for one team's perspective.

    Parameters
    ----------
    team_hist : list  -- team's history *before* this game (need >= 5)
    line_for_team : float -- spread from this team's perspective (positive = getting points)
    is_home : bool -- whether this team is playing at home

    Returns list[float] matching LR_FEATURE_NAMES, or None if insufficient history.
    """
    if len(team_hist) < 5:
        return None

    try:
        tf = _team_features(team_hist)
        line_vs_avg = line_for_team - tf["avg_line"]

        return [
            tf["ats_pm_5"],
            line_vs_avg,
            1.0 if is_home else 0.0,
        ]
    except Exception:
        return None


def extract_lr_features(home_hist, away_hist, game, home_lines=None, away_lines=None, picked_home=True):
    """Compatibility wrapper — extracts features for the picked team.

    Returns list[float] matching LR_FEATURE_NAMES, or None on error.
    """
    line = game.get("line", 0) or 0
    home_spread = line
    away_spread = -line

    if picked_home:
        return extract_team_lr_features(home_hist, home_spread, is_home=True)
    else:
        return extract_team_lr_features(away_hist, away_spread, is_home=False)


# ---------------------------------------------------------------------------
# build_lr_features_for_game -- convenience for live predictions
# ---------------------------------------------------------------------------

def build_lr_features_for_game(game, team_histories, run_date=None):
    """Build LR features for a single upcoming game.

    Parameters
    ----------
    game : dict -- must have 'home', 'away', 'line', 'total'
    team_histories : dict -- output of build_team_histories(store)
    run_date : str or None -- YYYYMMDD for rest-day calculation

    Returns list or None.
    """
    home = game.get("home", "")
    away = game.get("away", "")

    home_hist = team_histories.get(home, [])
    away_hist = team_histories.get(away, [])

    game_with_date = {**game}
    if run_date:
        game_with_date["_run_date"] = run_date

    return extract_lr_features(
        home_hist, away_hist, game_with_date,
        home_hist, away_hist,
    )


# ---------------------------------------------------------------------------
# _explain_lr
# ---------------------------------------------------------------------------

def _explain_lr(model_bundle, features, top_n=3, game=None, picked_home=True, supporting=False):
    """Return the top contributing features for/against the picked team.

    New per-team model: predicts P(team covers). supporting=True shows features
    pushing P(cover) up; supporting=False shows features pushing it down.
    """
    if model_bundle is None or features is None:
        return []
    if not HAS_SKLEARN:
        return []

    model = model_bundle["model"]
    scaler = model_bundle["scaler"]
    names = model_bundle.get("feature_names", LR_FEATURE_NAMES)

    # Determine picked team name
    g = game or {}
    team_name = g.get("home", "Home") if picked_home else g.get("away", "Away")

    try:
        X_scaled = scaler.transform(np.array([features], dtype=np.float64))[0]
        coefs = model.coef_[0]
    except Exception:
        return []

    contributions = []
    for name, coef, scaled_val, raw_val in zip(names, coefs, X_scaled, features):
        contrib = coef * scaled_val
        contributions.append((contrib, name, raw_val))

    # P(team covers): positive contributions support, negative hurt
    contributions.sort(key=lambda x: x[0], reverse=supporting)

    reasons = []
    for contrib, name, raw_val in contributions[:top_n]:
        label = _FEATURE_LABELS.get(name, name).replace("{team}", team_name)
        if "pm" in name:
            val_str = f"{raw_val:+.1f}"
        elif "line_vs" in name:
            val_str = f"{raw_val:+.1f}"
        elif name == "is_home":
            val_str = "yes" if raw_val > 0.5 else "no"
        else:
            val_str = f"{raw_val:.2f}"
        reasons.append(f"{label}: {val_str}")

    return reasons


# ---------------------------------------------------------------------------
# predict_lr
# ---------------------------------------------------------------------------

def predict_lr(model_bundle, features, game=None, picked_home=True):
    """Run a single prediction for the picked team.

    Returns dict with lr_prob (P(team covers)), lr_verdict, lr_reasons.
    """
    if model_bundle is None or features is None:
        return {"lr_prob": None, "lr_verdict": "NEUTRAL", "lr_reasons": []}
    if not HAS_SKLEARN:
        return {"lr_prob": None, "lr_verdict": "NEUTRAL", "lr_reasons": []}

    try:
        model = model_bundle["model"]
        scaler = model_bundle["scaler"]

        X = np.array([features], dtype=np.float64)
        X_scaled = scaler.transform(X)

        proba = model.predict_proba(X_scaled)[0]
        prob = float(proba[1]) if len(proba) > 1 else float(proba[0])

        if prob >= LR_CONFIRM_THRESH:
            verdict = "CONFIRM"
        elif prob <= LR_VETO_THRESH:
            verdict = "VETO"
        else:
            verdict = "NEUTRAL"

        if verdict == "VETO":
            reasons = _explain_lr(model_bundle, features, game=game, picked_home=picked_home, supporting=False)
        elif verdict == "CONFIRM":
            reasons = _explain_lr(model_bundle, features, game=game, picked_home=picked_home, supporting=True)
        else:
            reasons = []

        return {"lr_prob": round(prob, 3), "lr_verdict": verdict, "lr_reasons": reasons}
    except Exception as e:
        print(f"lr_model: predict error: {e}")
        return {"lr_prob": None, "lr_verdict": "NEUTRAL", "lr_reasons": []}


def predict_lr_for_pick(model_bundle, features, picked_home, game=None):
    """Predict P(picked team covers) and return a verdict.

    Features should already be extracted for the picked team's perspective
    (via extract_lr_features with picked_home).

    Returns dict with lr_prob, lr_pick_prob, lr_verdict, lr_reasons.
    """
    raw = predict_lr(model_bundle, features, game=game, picked_home=picked_home)
    if raw["lr_prob"] is None:
        return {"lr_prob": None, "lr_pick_prob": None, "lr_verdict": "neutral", "lr_reasons": []}

    p_cover = raw["lr_prob"]  # already P(picked team covers)

    if p_cover >= LR_CONFIRM_THRESH:
        verdict = "confirm"
    elif p_cover <= LR_VETO_THRESH:
        verdict = "veto"
    else:
        verdict = "neutral"

    reasons = []
    if verdict == "veto":
        reasons = _explain_lr(model_bundle, features, game=game, picked_home=picked_home, supporting=False)
    elif verdict == "confirm":
        reasons = _explain_lr(model_bundle, features, game=game, picked_home=picked_home, supporting=True)

    return {
        "lr_prob": round(p_cover, 4),
        "lr_pick_prob": round(p_cover, 4),
        "lr_verdict": verdict,
        "lr_reasons": reasons,
    }


# ---------------------------------------------------------------------------
# train_lr_model
# ---------------------------------------------------------------------------

def train_lr_model(store, min_games=None):
    """Train a LogisticRegression on graded games from the store.

    Per-team model: each game produces 2 training samples (one per team).
    Target: team covers the spread (binary).
    Only teams with >= 5 prior graded games are used.
    """
    if not HAS_SKLEARN:
        print("lr_model: scikit-learn not installed, cannot train")
        return None

    if min_games is None:
        min_games = MIN_TRAINING_GAMES

    print("lr_model: Building team histories...")
    running = defaultdict(list)

    X_rows = []
    y_rows = []

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

            home = g["home"]
            away = g["away"]
            line = g["line"]
            home_margin = g["homeScore"] - g["awayScore"]
            home_spread = line
            ats_margin = home_margin + home_spread

            # Skip pushes
            if ats_margin == 0:
                _append_to_running(running, g, run_date)
                continue

            home_covered = ats_margin > 0

            # Home team sample
            home_feats = extract_team_lr_features(running[home], home_spread, is_home=True)
            if home_feats is not None:
                X_rows.append(home_feats)
                y_rows.append(1 if home_covered else 0)

            # Away team sample
            away_feats = extract_team_lr_features(running[away], -home_spread, is_home=False)
            if away_feats is not None:
                X_rows.append(away_feats)
                y_rows.append(1 if not home_covered else 0)

            # Record into running histories
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
        l1_ratio=0,
        C=1.0,
        class_weight="balanced",
        max_iter=1000,
        solver="lbfgs",
        random_state=42,
    )
    model.fit(X_scaled, y)

    preds = model.predict(X_scaled)
    accuracy = float(np.mean(preds == y))
    print(f"lr_model: In-sample accuracy: {accuracy:.3f}")

    bundle = {
        "model": model,
        "scaler": scaler,
        "n_train": len(X),
        "n_trained": len(X),  # alias for compatibility
        "accuracy": accuracy,
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "feature_names": list(LR_FEATURE_NAMES),
    }

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
    # Line convention: +X means AWAY favored (home getting points),
    # -X means HOME favored (home laying points).
    home_spread = line
    home_covered = (home_margin + home_spread) > 0
    game_over = actual_total > total

    base = {
        "date": run_date,
        "home": home,
        "away": away,
        "line": line,
        "total": total,
        "actual_total": actual_total,
    }

    home_entry = {
        **base,
        "is_home": True,
        "score": home_score,
        "opp_score": away_score,
        "margin": home_margin,
        "covered": home_covered,
        "over": game_over,
        "line_for_team": home_spread,
        "abs_line": abs(line),
        "won": home_score > away_score,
    }
    if running[home]:
        prev_date = _parse_date(running[home][-1]["date"])
        cur_date = _parse_date(run_date)
        if prev_date and cur_date:
            home_entry["rest_days"] = max(0, min((cur_date - prev_date).days - 1, 14))
        else:
            home_entry["rest_days"] = 1
    else:
        home_entry["rest_days"] = 3
    running[home].append(home_entry)

    away_entry = {
        **base,
        "is_home": False,
        "score": away_score,
        "opp_score": home_score,
        "margin": -home_margin,
        "covered": not home_covered,
        "over": game_over,
        "line_for_team": -home_spread,
        "abs_line": abs(line),
        "won": away_score > home_score,
    }
    if running[away]:
        prev_date = _parse_date(running[away][-1]["date"])
        cur_date = _parse_date(run_date)
        if prev_date and cur_date:
            away_entry["rest_days"] = max(0, min((cur_date - prev_date).days - 1, 14))
        else:
            away_entry["rest_days"] = 1
    else:
        away_entry["rest_days"] = 3
    running[away].append(away_entry)


# ---------------------------------------------------------------------------
# Model persistence
# ---------------------------------------------------------------------------

def _save_model(bundle):
    """Persist model + scaler to disk using joblib."""
    if not HAS_JOBLIB or not joblib:
        print("lr_model: joblib not available, model not saved to disk")
        return

    model_dir = _get_model_dir()
    os.makedirs(model_dir, exist_ok=True)

    path = os.path.join(model_dir, "lr_latest.joblib")
    save_data = {
        "model": bundle["model"],
        "scaler": bundle["scaler"],
        "n_train": bundle.get("n_train", bundle.get("n_trained", 0)),
        "accuracy": bundle.get("accuracy", 0),
        "trained_at": bundle.get("trained_at", "unknown"),
        "feature_names": bundle.get("feature_names", list(LR_FEATURE_NAMES)),
    }
    joblib.dump(save_data, path)
    print(f"lr_model: Saved to {path}")

    # Also save metadata as JSON for inspection
    meta_path = os.path.join(model_dir, "lr_meta.json")
    meta = {
        "n_train": save_data["n_train"],
        "accuracy": round(save_data["accuracy"], 4) if save_data["accuracy"] else 0,
        "trained_at": save_data["trained_at"],
        "features": save_data["feature_names"],
        "confirm_threshold": LR_CONFIRM_THRESH,
        "veto_threshold": LR_VETO_THRESH,
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)


def _load_model():
    """Load persisted model from disk. Returns bundle dict or None."""
    if not HAS_JOBLIB or not joblib:
        return None

    model_dir = _get_model_dir()
    path = os.path.join(model_dir, "lr_latest.joblib")
    if not os.path.exists(path):
        return None

    try:
        data = joblib.load(path)
        return {
            "model": data["model"],
            "scaler": data["scaler"],
            "n_train": data.get("n_train", data.get("n_trained", 0)),
            "n_trained": data.get("n_train", data.get("n_trained", 0)),
            "accuracy": data.get("accuracy", 0),
            "trained_at": data.get("trained_at", "unknown"),
            "feature_names": data.get("feature_names", list(LR_FEATURE_NAMES)),
        }
    except Exception as e:
        print(f"lr_model: Failed to load from disk: {e}")
        return None


# ---------------------------------------------------------------------------
# load_or_train_lr -- main entry point
# ---------------------------------------------------------------------------

def load_or_train_lr(store):
    """Load the LR model from disk, retraining if stale or missing."""
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
        if n_graded - n_train < RETRAIN_INTERVAL:
            print(f"lr_model: Loaded (trained on {n_train}, current graded: {n_graded})")
            return bundle
        else:
            print(f"lr_model: Stale (trained on {n_train}, current graded: {n_graded}), retraining...")

    return train_lr_model(store, min_games=MIN_TRAINING_GAMES)
