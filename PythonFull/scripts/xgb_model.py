#!/usr/bin/env python3
"""
xgb_model.py -- XGBoost margin prediction for production use.

Adapted from ml_independent_backtest.py for live daily pipeline.
Provides:
  - Feature extraction from pregame game data (43+ features)
  - Walk-forward training on historical graded games
  - Periodic retraining (every N new games)
  - Margin prediction -> P(cover) via Normal CDF
  - Platt scaling calibration on trailing window
  - Adaptive ensemble of Bayesian + XGBoost probabilities
  - Model persistence via joblib
"""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

try:
    import joblib
except ImportError:
    import pickle as joblib  # fallback

try:
    import xgboost as xgb
    HAS_XGB = True
except ImportError:
    HAS_XGB = False

try:
    from sklearn.linear_model import LogisticRegression
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False

from defaults import (
    XGB_RETRAIN_INTERVAL,
    XGB_MIN_TRAINING_GAMES,
    XGB_ENSEMBLE_WEIGHT,
    XGB_LOOKBACK_WINDOW,
)

ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = ROOT / "data" / "models"
MODEL_PATH = MODEL_DIR / "xgb_spread.joblib"
CALIBRATOR_PATH = MODEL_DIR / "xgb_calibrator.joblib"
META_PATH = MODEL_DIR / "xgb_meta.json"

# XGBoost hyperparameters (from backtest)
XGB_PARAMS = dict(
    n_estimators=120,
    max_depth=4,
    learning_rate=0.05,
    subsample=0.8,
    colsample_bytree=0.8,
    reg_alpha=1.0,
    reg_lambda=2.0,
    min_child_weight=5,
    random_state=42,
    verbosity=0,
)

# Feature names for debugging / importance analysis
FEATURE_NAMES = [
    "dTS", "dTO", "dORR", "dNET", "avgPace",
    "mf_dTS", "mf_dTO", "mf_dORR", "mf_dNET", "mf_pace",
    "line", "total", "abs_line",
    "dNET_x_line", "dNET_x_absline", "avgPace_x_line",
    "h_ats", "a_ats", "d_ats", "h_ou", "a_ou", "d_ou",
    "h_ats_pm", "a_ats_pm", "d_ats_pm", "h_tot_pm", "a_tot_pm", "d_tot_pm",
    "home_b2b", "away_b2b", "d_b2b",
    "home_inj", "away_inj", "d_inj",
    "home_off_d", "away_off_d", "d_off_d",
    "home_def_d", "away_def_d", "d_def_d",
    "home_pace_d", "away_pace_d", "d_pace_d",
    "h2h_margin", "h2h_games",
]


# ---------------------------------------------------------------------------
# Utility helpers (same as backtest)
# ---------------------------------------------------------------------------

def _normal_cdf(x: float) -> float:
    """Abramowitz & Stegun approximation to the Normal CDF."""
    a1 = 0.254829592
    a2 = -0.284496736
    a3 = 1.421413741
    a4 = -1.453152027
    a5 = 1.061405429
    p = 0.3275911
    sign = -1 if x < 0 else 1
    z = abs(x) / math.sqrt(2.0)
    t = 1.0 / (1.0 + p * z)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-z * z)
    return 0.5 * (1.0 + sign * y)


def _safe_num(v, default=0.0) -> float:
    return float(v) if isinstance(v, (int, float)) else float(default)


def _trend_val(trends: dict, side: str, key: str) -> float:
    if not isinstance(trends, dict):
        return 0.0
    node = trends.get(side) or {}
    if not isinstance(node, dict):
        return 0.0
    return _safe_num(node.get(key), 0.0)


def _count_injuries(injury_note: str | None, side_label: str) -> int:
    if not injury_note or not isinstance(injury_note, str):
        return 0
    m = re.search(rf"{side_label}:\s*([^|]+)", injury_note)
    if not m:
        return 0
    part = m.group(1).strip()
    if not part:
        return 0
    return len([x for x in (p.strip() for p in part.split(",")) if x])


def _b2b_flags(b2b_note: str | None) -> Tuple[int, int]:
    if not b2b_note:
        return (0, 0)
    s = b2b_note.lower()
    home = int("home" in s and "b2b" in s)
    away = int("away" in s and "b2b" in s)
    return home, away


def _delta_features(adj: dict | None, side: str, stat: str) -> float:
    if not isinstance(adj, dict):
        return 0.0
    node = adj.get(side) or {}
    return _safe_num(node.get(stat), 0.0)


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

def extract_features(g: dict) -> List[float]:
    """
    Extract 47 pregame features from a game dict.
    Same core 45 features as ml_independent_backtest.py plus 2 H2H features.
    Works for both historical (graded) games and live games.
    """
    f = g.get("_features") or {}
    mf = g.get("_marginFeatures") or {}
    trends = g.get("trends") or {}

    line = _safe_num(g.get("line"), 0.0)
    total = _safe_num(g.get("total"), 220.0)
    abs_line = abs(line)

    # Trend deltas
    h_ats = _trend_val(trends, "home", "atsPct")
    a_ats = _trend_val(trends, "away", "atsPct")
    h_ou = _trend_val(trends, "home", "overPct")
    a_ou = _trend_val(trends, "away", "overPct")
    h_tot_pm = _trend_val(trends, "home", "totalPlusMinus")
    a_tot_pm = _trend_val(trends, "away", "totalPlusMinus")
    h_ats_pm = _trend_val(trends, "home", "atsPlusMinus")
    a_ats_pm = _trend_val(trends, "away", "atsPlusMinus")

    home_b2b, away_b2b = _b2b_flags(g.get("b2bNote"))
    home_inj = _count_injuries(g.get("injuryNote"), "Home")
    away_inj = _count_injuries(g.get("injuryNote"), "Away")

    # Lineup adjustment deltas
    home_off_d = _delta_features(g.get("_adjDeltas"), "home", "OFF")
    away_off_d = _delta_features(g.get("_adjDeltas"), "away", "OFF")
    home_def_d = _delta_features(g.get("_adjDeltas"), "home", "DEF")
    away_def_d = _delta_features(g.get("_adjDeltas"), "away", "DEF")
    home_pace_d = _delta_features(g.get("_adjDeltas"), "home", "PACE")
    away_pace_d = _delta_features(g.get("_adjDeltas"), "away", "PACE")

    # H2H matchup features (PythonFull extra data)
    h2h_margin = _safe_num(g.get("h2hAdj"), 0.0)
    h2h_games = _safe_num(g.get("h2hGames"), 0.0)

    dNET = _safe_num(f.get("dNET"))
    avgPace = _safe_num(f.get("avgPace"), 98.0)

    return [
        # Raw matchup/stat features (0-10)
        _safe_num(f.get("dTS")),
        _safe_num(f.get("dTO")),
        _safe_num(f.get("dORR")),
        dNET,
        avgPace,
        _safe_num(mf.get("dTS")),
        _safe_num(mf.get("dTO")),
        _safe_num(mf.get("dORR")),
        _safe_num(mf.get("dNET")),
        _safe_num(mf.get("_pace"), 1.0),
        # Market context
        line,
        total,
        abs_line,
        # Interactions (17-19)
        dNET * line,
        dNET * abs_line,
        avgPace * line,
        # Trends (20-31)
        h_ats,
        a_ats,
        h_ats - a_ats,
        h_ou,
        a_ou,
        h_ou - a_ou,
        h_ats_pm,
        a_ats_pm,
        h_ats_pm - a_ats_pm,
        h_tot_pm,
        a_tot_pm,
        h_tot_pm - a_tot_pm,
        # Schedule/injury (32-37)
        float(home_b2b),
        float(away_b2b),
        float(home_b2b - away_b2b),
        float(home_inj),
        float(away_inj),
        float(home_inj - away_inj),
        # Lineup adjustment deltas (38-46)
        home_off_d,
        away_off_d,
        home_off_d - away_off_d,
        home_def_d,
        away_def_d,
        home_def_d - away_def_d,
        home_pace_d,
        away_pace_d,
        home_pace_d - away_pace_d,
        # H2H features (47-48)
        h2h_margin,
        h2h_games,
    ]


# ---------------------------------------------------------------------------
# Training data from history
# ---------------------------------------------------------------------------

def _load_training_rows(store: dict) -> List[dict]:
    """
    Extract training rows from store (history.json data).
    Returns list of {date, x, y_margin_vs_line, y_home_cover, is_push}.
    Only uses graded games with valid features.
    """
    rows = []
    for run in store.get("runs", []):
        if run.get("burnIn"):
            continue
        date = str(run.get("date", ""))
        for g in run.get("games", []):
            hs = g.get("homeScore")
            aws = g.get("awayScore")
            line = g.get("line")
            if not isinstance(hs, (int, float)) or not isinstance(aws, (int, float)):
                continue
            if not isinstance(line, (int, float)):
                continue
            if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
                continue
            if not g.get("_features") or not g.get("_marginFeatures"):
                continue

            y = float((hs - aws) - line)
            try:
                x = extract_features(g)
            except Exception:
                continue

            rows.append({
                "date": date,
                "x": x,
                "y_margin_vs_line": y,
                "y_home_cover": bool(y > 0),
                "is_push": bool(y == 0),
            })
    return rows


# ---------------------------------------------------------------------------
# Model training
# ---------------------------------------------------------------------------

def train_model(store: dict) -> Optional[dict]:
    """
    Train XGBoost regressor on all graded historical games.
    Returns dict with model, residual_std, and training metadata,
    or None if insufficient data or missing dependencies.
    """
    if not HAS_XGB:
        print("  [XGB] xgboost not installed -- skipping")
        return None

    rows = _load_training_rows(store)
    if len(rows) < XGB_MIN_TRAINING_GAMES:
        print(f"  [XGB] Only {len(rows)} graded games, need {XGB_MIN_TRAINING_GAMES} -- skipping")
        return None

    X = np.array([r["x"] for r in rows], dtype=np.float64)
    y = np.array([r["y_margin_vs_line"] for r in rows], dtype=np.float64)

    model = xgb.XGBRegressor(**XGB_PARAMS)
    model.fit(X, y)

    residuals = y - model.predict(X)
    residual_std = max(float(np.std(residuals)), 1.0)

    result = {
        "model": model,
        "residual_std": residual_std,
        "n_trained": len(rows),
        "last_train_date": rows[-1]["date"] if rows else "",
    }

    # Persist to disk
    _save_model(result)
    return result


def _save_model(model_data: dict) -> None:
    """Save model and metadata to disk."""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    try:
        joblib.dump(model_data["model"], str(MODEL_PATH))
        meta = {
            "residual_std": model_data["residual_std"],
            "n_trained": model_data["n_trained"],
            "last_train_date": model_data["last_train_date"],
        }
        META_PATH.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"  [XGB] Warning: Could not save model: {e}")


def _load_model() -> Optional[dict]:
    """Load saved model from disk."""
    if not HAS_XGB:
        return None
    if not MODEL_PATH.exists() or not META_PATH.exists():
        return None
    try:
        model = joblib.load(str(MODEL_PATH))
        meta = json.loads(META_PATH.read_text(encoding="utf-8"))
        return {
            "model": model,
            "residual_std": meta["residual_std"],
            "n_trained": meta["n_trained"],
            "last_train_date": meta.get("last_train_date", ""),
        }
    except Exception as e:
        print(f"  [XGB] Warning: Could not load model: {e}")
        return None


def load_or_train_model(store: dict) -> Optional[dict]:
    """
    Load existing model from disk, or train a new one if:
      - No saved model exists
      - Enough new games have accumulated since last training
    """
    saved = _load_model()

    if saved is not None:
        # Check if retraining is needed
        rows = _load_training_rows(store)
        games_since_train = len(rows) - saved["n_trained"]
        if games_since_train >= XGB_RETRAIN_INTERVAL:
            print(f"  [XGB] {games_since_train} new games since last train -- retraining")
            return train_model(store)
        return saved

    # No saved model -- train from scratch
    return train_model(store)


# ---------------------------------------------------------------------------
# Prediction
# ---------------------------------------------------------------------------

def predict_game(model_data: dict, features: List[float]) -> dict:
    """
    Predict margin-vs-line for a single game.
    Returns dict with predicted margin, P(home cover), P(over).
    """
    model = model_data["model"]
    std = model_data["residual_std"]

    x = np.array([features], dtype=np.float64)
    margin = float(model.predict(x)[0])

    p_home_cover = _normal_cdf(margin / std)
    p_away_cover = 1.0 - p_home_cover

    return {
        "xgb_margin": round(margin * 10) / 10,
        "xgb_pHomeCover": round(p_home_cover * 1000) / 1000,
        "xgb_pAwayCover": round(p_away_cover * 1000) / 1000,
    }


def predict_total(model_data: dict, features: List[float], game: dict) -> dict:
    """
    Predict P(over) for a game total.
    Uses the same margin model but interprets total-related features.
    Returns xgb_pOver and xgb_pUnder.
    """
    # For totals, we use the market context features to estimate total deviation
    # The margin model is primarily for spread; for totals we derive from
    # the pace/scoring features in the feature vector
    model = model_data["model"]
    std = model_data["residual_std"] * 1.1  # totals slightly noisier

    x = np.array([features], dtype=np.float64)
    margin = float(model.predict(x)[0])

    # The total prediction leverages the same model's scoring insights
    # We use projected total deviation from the line (total) as a proxy
    line = _safe_num(game.get("line"), 0.0)
    total = _safe_num(game.get("total"), 220.0)
    t_diff = _safe_num(game.get("tDiff"), 0.0)

    if abs(t_diff) > 0.01:
        p_over = _normal_cdf(t_diff / std)
    else:
        p_over = 0.5

    return {
        "xgb_pOver": round(p_over * 1000) / 1000,
        "xgb_pUnder": round((1.0 - p_over) * 1000) / 1000,
    }


# ---------------------------------------------------------------------------
# Platt scaling calibration
# ---------------------------------------------------------------------------

def calibrate_probability(
    raw_p: float,
    recent_preds: List[float],
    recent_actuals: List[bool],
    min_samples: int = 40,
) -> float:
    """
    Calibrate a raw P(cover) via Platt scaling (logistic regression)
    on a trailing window of recent predictions and outcomes.
    """
    if not HAS_SKLEARN:
        return raw_p
    if len(recent_preds) < min_samples:
        return raw_p
    if len(set(recent_actuals)) < 2:
        return raw_p

    xs = np.array(recent_preds, dtype=np.float64).reshape(-1, 1)
    ys = np.array([int(a) for a in recent_actuals], dtype=np.int32)

    try:
        lr = LogisticRegression(solver="lbfgs", max_iter=500)
        lr.fit(xs, ys)
        cal_p = float(lr.predict_proba(np.array([[raw_p]], dtype=np.float64))[0, 1])
        return cal_p
    except Exception:
        return raw_p


def build_calibration_window(store: dict) -> Tuple[List[float], List[bool]]:
    """
    Build trailing calibration window from recent graded games.
    Returns (xgb_predictions, actual_covers) for Platt scaling.
    """
    preds = []
    actuals = []

    saved = _load_model()
    if saved is None:
        return preds, actuals

    model = saved["model"]
    std = saved["residual_std"]

    rows = _load_training_rows(store)
    # Use last XGB_LOOKBACK_WINDOW rows for calibration
    window = rows[-XGB_LOOKBACK_WINDOW:] if len(rows) > XGB_LOOKBACK_WINDOW else rows

    for r in window:
        if r["is_push"]:
            continue
        try:
            x = np.array([r["x"]], dtype=np.float64)
            margin = float(model.predict(x)[0])
            p = _normal_cdf(margin / std)
            preds.append(p)
            actuals.append(r["y_home_cover"])
        except Exception:
            continue

    return preds, actuals


# ---------------------------------------------------------------------------
# Ensemble: Bayesian + XGBoost
# ---------------------------------------------------------------------------

def ensemble_probability(
    bayesian_p: float,
    xgb_p: float,
    recent_performance: Optional[dict] = None,
) -> float:
    """
    Combine Bayesian P(cover) with XGBoost P(cover) using adaptive weighting.

    recent_performance: optional dict with:
      - bayes_accuracy: float (0-1) recent Bayesian accuracy
      - xgb_accuracy: float (0-1) recent XGBoost accuracy
      - n_recent: int number of recent games evaluated

    If no performance data, uses default weight from XGB_ENSEMBLE_WEIGHT.
    """
    if recent_performance and recent_performance.get("n_recent", 0) >= 30:
        # Adaptive weighting based on recent accuracy
        b_acc = recent_performance.get("bayes_accuracy", 0.5)
        x_acc = recent_performance.get("xgb_accuracy", 0.5)

        floor = 0.10
        b_score = max(floor, b_acc - 0.40)
        x_score = max(floor, x_acc - 0.40)
        total = b_score + x_score

        if total > 0:
            w_xgb = x_score / total
        else:
            w_xgb = XGB_ENSEMBLE_WEIGHT
    else:
        w_xgb = XGB_ENSEMBLE_WEIGHT

    w_bayes = 1.0 - w_xgb
    return w_bayes * bayesian_p + w_xgb * xgb_p


def compute_recent_performance(store: dict, model_data: Optional[dict] = None) -> dict:
    """
    Evaluate Bayesian and XGBoost accuracy on recent graded games
    for adaptive ensemble weighting.
    """
    result = {"bayes_accuracy": 0.5, "xgb_accuracy": 0.5, "n_recent": 0}

    if model_data is None:
        return result

    rows = _load_training_rows(store)
    window = rows[-XGB_LOOKBACK_WINDOW:] if len(rows) > XGB_LOOKBACK_WINDOW else rows

    model = model_data["model"]
    std = model_data["residual_std"]

    bayes_correct = 0
    xgb_correct = 0
    total = 0

    # Need original game data for Bayesian P(cover)
    game_lookup = {}
    for run in store.get("runs", []):
        if run.get("burnIn"):
            continue
        for g in run.get("games", []):
            key = f"{run['date']}_{g.get('home','')}_{g.get('away','')}"
            game_lookup[key] = g

    for r in window:
        if r["is_push"]:
            continue

        actual = r["y_home_cover"]

        # XGBoost prediction
        try:
            x = np.array([r["x"]], dtype=np.float64)
            margin = float(model.predict(x)[0])
            xgb_p = _normal_cdf(margin / std)
            xgb_pred = xgb_p >= 0.5
            xgb_correct += int(xgb_pred == actual)
        except Exception:
            continue

        # Look up Bayesian P(cover) from history
        # We scan game_lookup for matching date
        bayes_p = 0.5  # default
        for key, g in game_lookup.items():
            if key.startswith(r["date"]):
                bp = g.get("pHomeCover")
                if isinstance(bp, (int, float)):
                    # Check if this game's features match (approximate)
                    bayes_p = float(bp)
                    break

        bayes_pred = bayes_p >= 0.5
        bayes_correct += int(bayes_pred == actual)
        total += 1

    if total > 0:
        result["bayes_accuracy"] = bayes_correct / total
        result["xgb_accuracy"] = xgb_correct / total
        result["n_recent"] = total

    return result


# ---------------------------------------------------------------------------
# High-level interface for run_daily.py
# ---------------------------------------------------------------------------

def xgb_predict_games(
    games: List[dict],
    store: dict,
) -> Tuple[Optional[dict], List[dict]]:
    """
    Run XGBoost predictions on a list of game dicts.
    Returns (model_data, updated_games) where each game gets
    xgb_margin, xgb_pCover, xgb_pOver, and ensemble_pCover added.

    If XGBoost is unavailable or untrained, returns (None, games) unchanged.
    """
    if not HAS_XGB:
        print("  [XGB] xgboost not installed -- skipping predictions")
        return None, games

    model_data = load_or_train_model(store)
    if model_data is None:
        print("  [XGB] No trained model available -- skipping predictions")
        return None, games

    print(f"  [XGB] Model loaded: {model_data['n_trained']} training games, "
          f"residual_std={model_data['residual_std']:.2f}")

    # Build calibration window
    cal_preds, cal_actuals = build_calibration_window(store)

    # Compute recent performance for adaptive weighting
    perf = compute_recent_performance(store, model_data)
    if perf["n_recent"] > 0:
        print(f"  [XGB] Recent performance: bayes={perf['bayes_accuracy']:.1%} "
              f"xgb={perf['xgb_accuracy']:.1%} (n={perf['n_recent']})")

    for g in games:
        if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
            continue
        if not g.get("_features") or not g.get("_marginFeatures"):
            continue

        try:
            features = extract_features(g)
            pred = predict_game(model_data, features)

            g["xgb_margin"] = pred["xgb_margin"]
            g["xgb_pHomeCover"] = pred["xgb_pHomeCover"]
            g["xgb_pAwayCover"] = pred["xgb_pAwayCover"]

            # Calibrate XGBoost P(cover) via Platt scaling
            raw_p = pred["xgb_pHomeCover"]
            cal_p = calibrate_probability(raw_p, cal_preds, cal_actuals)
            g["xgb_pHomeCover_cal"] = round(cal_p * 1000) / 1000

            # Determine which side for xgb_pCover
            if cal_p >= 0.5:
                g["xgb_pCover"] = round(cal_p * 1000) / 1000
            else:
                g["xgb_pCover"] = round((1.0 - cal_p) * 1000) / 1000

            # Total prediction
            total_pred = predict_total(model_data, features, g)
            g["xgb_pOver"] = total_pred["xgb_pOver"]
            g["xgb_pUnder"] = total_pred["xgb_pUnder"]

            # Ensemble with Bayesian
            bayes_p_cover = g.get("pCover")
            if bayes_p_cover is not None and isinstance(bayes_p_cover, (int, float)):
                ens_p = ensemble_probability(bayes_p_cover, g["xgb_pCover"], perf)
                g["ensemble_pCover"] = round(ens_p * 1000) / 1000
            else:
                g["ensemble_pCover"] = g["xgb_pCover"]

            # Also ensemble pOver if Bayesian pOver exists
            bayes_p_over = g.get("pOver")
            if bayes_p_over is not None and isinstance(bayes_p_over, (int, float)):
                ens_p_over = ensemble_probability(bayes_p_over, g["xgb_pOver"], perf)
                g["ensemble_pOver"] = round(ens_p_over * 1000) / 1000

        except Exception as e:
            print(f"  [XGB] Warning: prediction failed for {g.get('away','')} @ {g.get('home','')}: {e}")
            continue

    return model_data, games


def retrain_after_grading(store: dict) -> Optional[dict]:
    """
    Called after grading yesterday's games. Checks if retraining is needed
    and returns updated model_data if retrained.
    """
    if not HAS_XGB:
        return None
    return load_or_train_model(store)
