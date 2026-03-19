# scripts/xgb_model.py
# XGBoost model integration for NBA picks pipeline.
#
# Provides:
#   - Feature extraction from pregame game data (40+ features)
#   - Walk-forward training on historical graded games
#   - Margin prediction -> P(cover) via Normal CDF
#   - Platt scaling calibration on trailing window
#   - Adaptive ensemble with Bayesian P(cover)
#   - Model persistence (save/load via joblib)

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
    import pickle as joblib

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

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = ROOT / "data" / "xgb_models"
MODEL_PATH = MODEL_DIR / "xgb_nba.pkl"
META_PATH = MODEL_DIR / "xgb_meta.json"

# ---------------------------------------------------------------------------
# Utility helpers (mirror backtest)
# ---------------------------------------------------------------------------

def normal_cdf(x: float) -> float:
    """Abramowitz & Stegun approximation (~1e-5 accuracy)."""
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
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


def _count_injuries(injury_note, side_label: str) -> int:
    if not injury_note or not isinstance(injury_note, str):
        return 0
    m = re.search(rf"{side_label}:\s*([^|]+)", injury_note)
    if not m:
        return 0
    part = m.group(1).strip()
    if not part:
        return 0
    return len([x for x in (p.strip() for p in part.split(",")) if x])


def _b2b_flags(b2b_note) -> Tuple[int, int]:
    if not b2b_note:
        return (0, 0)
    s = str(b2b_note).lower()
    home = int("home" in s and "b2b" in s)
    away = int("away" in s and "b2b" in s)
    return home, away


def _delta_features(adj, side: str, stat: str) -> float:
    if not isinstance(adj, dict):
        return 0.0
    node = adj.get(side) or {}
    return _safe_num(node.get(stat), 0.0)


# ---------------------------------------------------------------------------
# Feature extraction (40+ features, identical to backtest)
# ---------------------------------------------------------------------------

def extract_features(g: dict) -> List[float]:
    """Extract pregame features from a game dict.

    Works on both historical (graded) game dicts from history.json and
    live game dicts produced by analyze_game in the daily pipeline.
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

    home_off_d = _delta_features(g.get("_adjDeltas"), "home", "OFF")
    away_off_d = _delta_features(g.get("_adjDeltas"), "away", "OFF")
    home_def_d = _delta_features(g.get("_adjDeltas"), "home", "DEF")
    away_def_d = _delta_features(g.get("_adjDeltas"), "away", "DEF")
    home_pace_d = _delta_features(g.get("_adjDeltas"), "home", "PACE")
    away_pace_d = _delta_features(g.get("_adjDeltas"), "away", "PACE")

    return [
        # Raw matchup/stat features (0-9)
        _safe_num(f.get("dTS")),
        _safe_num(f.get("dTO")),
        _safe_num(f.get("dORR")),
        _safe_num(f.get("dNET")),
        _safe_num(f.get("avgPace"), 98.0),
        _safe_num(mf.get("dTS")),
        _safe_num(mf.get("dTO")),
        _safe_num(mf.get("dORR")),
        _safe_num(mf.get("dNET")),
        _safe_num(mf.get("_pace"), 1.0),
        # Market context (10-11)
        total,
        abs_line,
        # Interactions (12)
        _safe_num(f.get("dNET")) * abs_line,
        # Trends (13-24)
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
        # Schedule/injury (25-30)
        float(home_b2b),
        float(away_b2b),
        float(home_b2b - away_b2b),
        float(home_inj),
        float(away_inj),
        float(home_inj - away_inj),
        # Lineup adjustment deltas (31-39)
        home_off_d,
        away_off_d,
        home_off_d - away_off_d,
        home_def_d,
        away_def_d,
        home_def_d - away_def_d,
        home_pace_d,
        away_pace_d,
        home_pace_d - away_pace_d,
    ]


# ---------------------------------------------------------------------------
# Training data extraction from history store
# ---------------------------------------------------------------------------

def _load_training_rows(store: dict) -> List[dict]:
    """Extract graded games from the store suitable for XGBoost training.

    Returns list of dicts with keys: x, y_margin, y_cover, is_push, date, bayes_p.
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

            y = float((hs - aws) - line)  # margin vs line
            feats = extract_features(g)
            rows.append({
                "x": feats,
                "y_margin": y,
                "y_cover": y > 0,
                "is_push": y == 0,
                "date": date,
                "bayes_p": _safe_num(g.get("pHomeCover"), 0.5),
            })
    return rows


# ---------------------------------------------------------------------------
# Model training (walk-forward safe -- only uses past data)
# ---------------------------------------------------------------------------

def train_model(store: dict) -> Optional[dict]:
    """Train XGBoost on all graded games in history.

    Returns dict with keys: model, residual_std, n_trained, calibrator.
    Returns None if not enough data or missing dependencies.
    """
    if not HAS_XGB:
        print("  [XGB] xgboost not installed -- skipping")
        return None

    rows = _load_training_rows(store)
    if len(rows) < XGB_MIN_TRAINING_GAMES:
        print(f"  [XGB] Only {len(rows)} graded games, need {XGB_MIN_TRAINING_GAMES} -- skipping")
        return None

    X = np.array([r["x"] for r in rows], dtype=np.float64)
    y = np.array([r["y_margin"] for r in rows], dtype=np.float64)

    model = xgb.XGBRegressor(
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
    model.fit(X, y)

    # Residual std for converting margin prediction to probability
    preds = model.predict(X)
    residual_std = max(float(np.std(y - preds)), 1.0)

    # Platt scaling calibrator on trailing window
    calibrator = _fit_calibrator(rows, model, residual_std)

    return {
        "model": model,
        "residual_std": residual_std,
        "n_trained": len(rows),
        "calibrator": calibrator,
    }


def _fit_calibrator(rows: list, model, residual_std: float):
    """Fit a Platt scaling logistic regression on trailing window of raw P(cover)."""
    if not HAS_SKLEARN:
        return None

    lookback = min(XGB_LOOKBACK_WINDOW * 4, len(rows))
    recent = rows[-lookback:]

    xs = []
    ys = []
    for r in recent:
        if r["is_push"]:
            continue
        x_arr = np.array([r["x"]], dtype=np.float64)
        margin_pred = float(model.predict(x_arr)[0])
        raw_p = normal_cdf(margin_pred / residual_std)
        xs.append([raw_p])
        ys.append(1 if r["y_cover"] else 0)

    if len(xs) < 40 or len(set(ys)) < 2:
        return None

    lr = LogisticRegression(solver="lbfgs", max_iter=500)
    lr.fit(np.array(xs, dtype=np.float64), np.array(ys, dtype=int))
    return lr


# ---------------------------------------------------------------------------
# Prediction
# ---------------------------------------------------------------------------

def predict_game(model_bundle: dict, features: List[float]) -> dict:
    """Predict margin and P(cover) for a single game.

    Returns dict with keys: xgb_margin, xgb_pCover_raw, xgb_pCover.
    """
    model = model_bundle["model"]
    residual_std = model_bundle["residual_std"]
    calibrator = model_bundle.get("calibrator")

    x = np.array([features], dtype=np.float64)
    margin = float(model.predict(x)[0])
    raw_p = normal_cdf(margin / residual_std)

    # Calibrate via Platt scaling if available
    if calibrator is not None:
        cal_p = float(calibrator.predict_proba(np.array([[raw_p]], dtype=np.float64))[0, 1])
    else:
        cal_p = raw_p

    return {
        "xgb_margin": round(margin, 2),
        "xgb_pCover_raw": round(raw_p, 4),
        "xgb_pCover": round(cal_p, 4),
    }


def predict_total(model_bundle: dict, features: List[float], total_line: float) -> dict:
    """Predict P(over) for a game's total.

    Uses the margin model's residual as a rough proxy. The XGBoost model
    predicts margin-vs-line; for totals we estimate from the same feature set
    by looking at whether the implied total is above or below the line.

    This is a simplified approach -- a dedicated total model would be better
    but we keep things simple for now and only use the spread model's signal.
    """
    # For totals, we don't have a separate model yet.
    # Return neutral predictions so the ensemble doesn't harm totals.
    return {
        "xgb_pOver": 0.5,
        "xgb_pOver_raw": 0.5,
    }


# ---------------------------------------------------------------------------
# Ensemble: combine Bayesian P(cover) with XGBoost P(cover)
# ---------------------------------------------------------------------------

def ensemble_probability(
    bayesian_p: float,
    xgb_p: float,
    recent_performance: Optional[dict] = None,
) -> float:
    """Combine Bayesian and XGBoost P(cover) using adaptive weighting.

    recent_performance should be a dict with keys:
        bayes_correct, bayes_total, xgb_correct, xgb_total
    representing accuracy counts over recent XGB_LOOKBACK_WINDOW games.

    Falls back to static XGB_ENSEMBLE_WEIGHT if performance data is insufficient.
    """
    xgb_weight = XGB_ENSEMBLE_WEIGHT  # default 0.35
    bayes_weight = 1.0 - xgb_weight

    if recent_performance and recent_performance.get("xgb_total", 0) >= 30:
        bayes_total = recent_performance.get("bayes_total", 0)
        xgb_total = recent_performance.get("xgb_total", 0)

        if bayes_total > 0 and xgb_total > 0:
            bayes_acc = recent_performance["bayes_correct"] / bayes_total
            xgb_acc = recent_performance["xgb_correct"] / xgb_total

            # Convert accuracy to edge (above 0.40 floor)
            floor = 0.10
            bayes_edge = max(floor, bayes_acc - 0.40)
            xgb_edge = max(floor, xgb_acc - 0.40)

            total_edge = bayes_edge + xgb_edge
            if total_edge > 0:
                xgb_weight = xgb_edge / total_edge
                bayes_weight = bayes_edge / total_edge

    return bayes_weight * bayesian_p + xgb_weight * xgb_p


def compute_recent_performance(store: dict, model_bundle: Optional[dict]) -> Optional[dict]:
    """Compute recent accuracy of Bayesian vs XGBoost on trailing window.

    Only counts non-push graded games from the last XGB_LOOKBACK_WINDOW games.
    """
    if model_bundle is None:
        return None

    rows = _load_training_rows(store)
    if len(rows) < XGB_LOOKBACK_WINDOW:
        return None

    recent = rows[-XGB_LOOKBACK_WINDOW:]
    model = model_bundle["model"]
    residual_std = model_bundle["residual_std"]

    bayes_correct = 0
    bayes_total = 0
    xgb_correct = 0
    xgb_total = 0

    for r in recent:
        if r["is_push"]:
            continue
        actual = r["y_cover"]
        bayes_total += 1
        xgb_total += 1

        # Bayesian accuracy
        bayes_pred = r["bayes_p"] >= 0.5
        if bayes_pred == actual:
            bayes_correct += 1

        # XGBoost accuracy
        x = np.array([r["x"]], dtype=np.float64)
        margin = float(model.predict(x)[0])
        xgb_pred = normal_cdf(margin / residual_std) >= 0.5
        if xgb_pred == actual:
            xgb_correct += 1

    return {
        "bayes_correct": bayes_correct,
        "bayes_total": bayes_total,
        "xgb_correct": xgb_correct,
        "xgb_total": xgb_total,
    }


# ---------------------------------------------------------------------------
# Model persistence
# ---------------------------------------------------------------------------

def save_model(model_bundle: dict) -> None:
    """Save trained model bundle to disk."""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(model_bundle, str(MODEL_PATH))

    meta = {
        "n_trained": model_bundle.get("n_trained", 0),
        "residual_std": model_bundle.get("residual_std", 12.0),
        "has_calibrator": model_bundle.get("calibrator") is not None,
    }
    META_PATH.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"  [XGB] Model saved ({meta['n_trained']} games, std={meta['residual_std']:.2f})")


def load_model() -> Optional[dict]:
    """Load trained model bundle from disk. Returns None if not found."""
    if not MODEL_PATH.exists():
        return None
    try:
        bundle = joblib.load(str(MODEL_PATH))
        if not isinstance(bundle, dict) or "model" not in bundle:
            return None
        return bundle
    except Exception as e:
        print(f"  [XGB] Failed to load model: {e}")
        return None


def _should_retrain(store: dict, model_bundle: Optional[dict]) -> bool:
    """Check if model needs retraining based on new game count."""
    rows = _load_training_rows(store)
    current_count = len(rows)

    if model_bundle is None:
        return current_count >= XGB_MIN_TRAINING_GAMES

    prev_count = model_bundle.get("n_trained", 0)
    return (current_count - prev_count) >= XGB_RETRAIN_INTERVAL


# ---------------------------------------------------------------------------
# Top-level entry point for daily pipeline
# ---------------------------------------------------------------------------

def load_or_train_model(store: dict, force_retrain: bool = False) -> Optional[dict]:
    """Load existing model or train a new one if needed.

    Called from run_daily.py after grading yesterday's games.
    """
    if not HAS_XGB:
        return None

    model_bundle = load_model()

    if force_retrain or _should_retrain(store, model_bundle):
        print("  [XGB] Training model...")
        new_bundle = train_model(store)
        if new_bundle is not None:
            save_model(new_bundle)
            return new_bundle
        # If training failed but we have an old model, keep using it
        return model_bundle

    if model_bundle is not None:
        meta = {}
        if META_PATH.exists():
            try:
                meta = json.loads(META_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass
        print(f"  [XGB] Loaded saved model ({meta.get('n_trained', '?')} games)")

    return model_bundle
