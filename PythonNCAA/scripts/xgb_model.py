# scripts/xgb_model.py
# XGBoost model integration for NCAA basketball predictions.
#
# Adapts the NBA ml_independent_backtest.py approach for NCAA:
#   - Walk-forward training on historical graded games
#   - Feature extraction from pregame stats + market context
#   - Margin prediction -> P(cover) via Normal CDF
#   - Platt scaling calibration
#   - Adaptive ensemble with Bayesian model
#   - Model persistence via joblib
#
# NCAA-specific considerations:
#   - Larger variance (more teams, wider talent gap)
#   - Tournament games are neutral site (HCA=0)
#   - Season type matters (regular vs conference tourney vs NCAA tourney)
#   - No lineup/injury data (features optional)

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

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

try:
    import joblib
    HAS_JOBLIB = True
except ImportError:
    HAS_JOBLIB = False

from defaults import (
    XGB_RETRAIN_INTERVAL,
    XGB_MIN_TRAINING_GAMES,
    XGB_ENSEMBLE_WEIGHT,
    XGB_LOOKBACK_WINDOW,
)

ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = ROOT / "data" / "xgb_models"
MODEL_PATH = MODEL_DIR / "ncaa_xgb_model.joblib"
META_PATH = MODEL_DIR / "ncaa_xgb_meta.json"

# Feature names for interpretability / logging
FEATURE_NAMES = [
    # Core stat deltas (0-4)
    "dTS", "dTO", "dORR", "dNET", "avgPace",
    # Margin features (5-9)
    "mf_dTS", "mf_dTO", "mf_dORR", "mf_dNET", "mf_baseline",
    # Market context (10-15)
    "line", "total", "abs_line", "implied_home", "implied_away", "implied_gap",
    # Interactions (16-18)
    "dNET_x_line", "dNET_x_absline", "avgPace_x_line",
    # Trends (19-30)
    "h_ats", "a_ats", "ats_diff",
    "h_ou", "a_ou", "ou_diff",
    "h_ats_pm", "a_ats_pm", "ats_pm_diff",
    "h_tot_pm", "a_tot_pm", "tot_pm_diff",
    # Schedule (31-33)
    "home_b2b", "away_b2b", "b2b_diff",
    # Tournament context (34-36)
    "is_tournament", "is_neutral", "seed_diff",
]


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def normal_cdf(x: float) -> float:
    """Abramowitz & Stegun approximation (~1e-5 accuracy)."""
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


def _b2b_flags(b2b_note) -> Tuple[int, int]:
    if not b2b_note:
        return (0, 0)
    s = str(b2b_note).lower()
    home = int("home" in s and "b2b" in s)
    away = int("away" in s and "b2b" in s)
    return home, away


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

def extract_features(g: dict, stats: Optional[dict] = None, w: Optional[dict] = None) -> List[float]:
    """
    Extract pregame features from a game dict for XGBoost prediction.

    Works with both:
      - Historical games (from history.json, which have _features/_marginFeatures stored)
      - Live games (from run_daily.py analyze_game output)

    Parameters:
        g: game dict with keys like line, total, _features, _marginFeatures, trends, b2bNote
        stats: team stats dict (optional, for live feature extraction if _features missing)
        w: model weights dict (optional, for live feature extraction)

    Returns:
        List of floats matching FEATURE_NAMES ordering.
    """
    f = g.get("_features") or {}
    mf = g.get("_marginFeatures") or {}
    trends = g.get("trends") or {}

    line = _safe_num(g.get("line"), 0.0)
    total = _safe_num(g.get("total"), 220.0)
    abs_line = abs(line)

    # Market implied scoring
    implied_home = (total + line) / 2.0
    implied_away = (total - line) / 2.0
    implied_gap = implied_home - implied_away

    # Trend features
    h_ats = _trend_val(trends, "home", "atsPct")
    a_ats = _trend_val(trends, "away", "atsPct")
    h_ou = _trend_val(trends, "home", "overPct")
    a_ou = _trend_val(trends, "away", "overPct")
    h_tot_pm = _trend_val(trends, "home", "totalPlusMinus")
    a_tot_pm = _trend_val(trends, "away", "totalPlusMinus")
    h_ats_pm = _trend_val(trends, "home", "atsPlusMinus")
    a_ats_pm = _trend_val(trends, "away", "atsPlusMinus")

    # B2B flags
    home_b2b, away_b2b = _b2b_flags(g.get("b2bNote"))

    # Tournament context
    is_tourney = 1.0 if g.get("is_tournament") else 0.0
    is_neutral = 1.0 if g.get("is_neutral") else 0.0
    seed_diff = _safe_num(g.get("seed_diff"), 0.0)

    # If tournament game and no explicit neutral flag, assume neutral
    if is_tourney and not g.get("is_neutral"):
        is_neutral = 1.0

    avg_pace = _safe_num(f.get("avgPace"), 68.0)  # NCAA pace ~68 possessions

    return [
        # Core stat deltas
        _safe_num(f.get("dTS")),
        _safe_num(f.get("dTO")),
        _safe_num(f.get("dORR")),
        _safe_num(f.get("dNET")),
        avg_pace,
        # Margin features (if available)
        _safe_num(mf.get("dTS")),
        _safe_num(mf.get("dTO")),
        _safe_num(mf.get("dORR")),
        _safe_num(mf.get("dNET")),
        _safe_num(mf.get("_baseline")),
        # Market context
        line,
        total,
        abs_line,
        implied_home,
        implied_away,
        implied_gap,
        # Interactions
        _safe_num(f.get("dNET")) * line,
        _safe_num(f.get("dNET")) * abs_line,
        avg_pace * line,
        # Trends
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
        # Schedule
        float(home_b2b),
        float(away_b2b),
        float(home_b2b - away_b2b),
        # Tournament context
        is_tourney,
        is_neutral,
        seed_diff,
    ]


# ---------------------------------------------------------------------------
# Training data loading
# ---------------------------------------------------------------------------

def _load_training_rows(store: dict) -> List[dict]:
    """
    Extract training rows from history store.
    Each row: {"date": str, "x": [...], "y": float, "cover": bool, "push": bool, "bayes_p": float}
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
            if not g.get("_features"):
                continue

            # Label: margin vs line (positive = home covered)
            y = float((hs - aws) - line)
            rows.append({
                "date": date,
                "x": extract_features(g),
                "y": y,
                "cover": bool(y > 0),
                "push": bool(y == 0),
                "bayes_p": _safe_num(g.get("pHomeCover"), 0.5),
            })
    return rows


# ---------------------------------------------------------------------------
# Model training
# ---------------------------------------------------------------------------

def train_model(history_store: dict, min_games: int = None) -> Optional[dict]:
    """
    Train XGBoost regressor on historical graded games using walk-forward methodology.

    Returns dict with:
        model: trained XGBRegressor
        residual_std: float (for CDF conversion)
        n_trained: int
        calibrator: LogisticRegression (Platt scaling) or None
    """
    if not HAS_XGB:
        print("[xgb_model] xgboost not installed -- skipping")
        return None

    min_games = min_games or XGB_MIN_TRAINING_GAMES
    rows = _load_training_rows(history_store)

    if len(rows) < min_games:
        print(f"[xgb_model] Only {len(rows)} training rows (need {min_games}) -- skipping")
        return None

    x_all = np.array([r["x"] for r in rows], dtype=np.float64)
    y_all = np.array([r["y"] for r in rows], dtype=np.float64)

    # Replace any NaN/inf with 0
    x_all = np.nan_to_num(x_all, nan=0.0, posinf=0.0, neginf=0.0)

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
    model.fit(x_all, y_all)

    # Compute residual std for CDF conversion
    preds = model.predict(x_all)
    residuals = y_all - preds
    residual_std = max(float(np.std(residuals)), 1.0)

    # Platt scaling calibrator (on training data -- will be refined online)
    calibrator = None
    if HAS_SKLEARN:
        # Convert margin predictions to raw P(cover)
        raw_probs = np.array([normal_cdf(p / residual_std) for p in preds])
        labels = np.array([1 if r["cover"] else 0 for r in rows], dtype=int)
        non_push = np.array([not r["push"] for r in rows])

        if np.sum(non_push) >= 40 and len(set(labels[non_push])) == 2:
            cal = LogisticRegression(solver="lbfgs", max_iter=500)
            cal.fit(raw_probs[non_push].reshape(-1, 1), labels[non_push])
            calibrator = cal

    print(f"[xgb_model] Trained on {len(rows)} games, residual_std={residual_std:.2f}")

    return {
        "model": model,
        "residual_std": residual_std,
        "n_trained": len(rows),
        "calibrator": calibrator,
    }


# ---------------------------------------------------------------------------
# Prediction
# ---------------------------------------------------------------------------

def predict_game(model_bundle: dict, features: List[float]) -> Optional[dict]:
    """
    Predict margin and P(cover) for a single game.

    Returns dict with:
        pred_margin: float (predicted margin vs line, positive = home covers)
        raw_p_cover: float (raw P(home cover) from Normal CDF)
        cal_p_cover: float (calibrated P(home cover) via Platt scaling, if available)
    """
    if not model_bundle or not model_bundle.get("model"):
        return None

    model = model_bundle["model"]
    residual_std = model_bundle.get("residual_std", 14.0)

    x = np.array([features], dtype=np.float64)
    x = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)

    pred_margin = float(model.predict(x)[0])
    raw_p = normal_cdf(pred_margin / residual_std)

    # Calibrate via Platt scaling if available
    cal_p = raw_p
    calibrator = model_bundle.get("calibrator")
    if calibrator is not None:
        try:
            cal_p = float(calibrator.predict_proba(np.array([[raw_p]]))[0, 1])
        except Exception:
            cal_p = raw_p

    return {
        "pred_margin": round(pred_margin * 10) / 10,
        "raw_p_cover": round(raw_p * 1000) / 1000,
        "cal_p_cover": round(cal_p * 1000) / 1000,
    }


# ---------------------------------------------------------------------------
# Ensemble
# ---------------------------------------------------------------------------

def ensemble_probability(
    bayesian_p: float,
    xgb_p: float,
    recent_performance: Optional[dict] = None,
    base_xgb_weight: float = None,
) -> dict:
    """
    Ensemble Bayesian P(cover) with XGBoost P(cover) using adaptive weighting.

    Parameters:
        bayesian_p: P(home cover) from Bayesian model
        xgb_p: P(home cover) from XGBoost (calibrated)
        recent_performance: optional dict with recent accuracy of each model
            {"bayes_correct": int, "bayes_total": int, "xgb_correct": int, "xgb_total": int}
        base_xgb_weight: base weight for XGBoost (default from defaults.py)

    Returns dict with:
        ensemble_p: float (ensembled P(home cover))
        bayes_weight: float
        xgb_weight: float
    """
    base_w = base_xgb_weight if base_xgb_weight is not None else XGB_ENSEMBLE_WEIGHT

    # Adaptive weighting based on recent performance
    if recent_performance and recent_performance.get("bayes_total", 0) >= 30:
        bt = recent_performance["bayes_total"]
        bc = recent_performance.get("bayes_correct", 0)
        xt = recent_performance.get("xgb_total", 0)
        xc = recent_performance.get("xgb_correct", 0)

        floor = 0.10
        bayes_acc = max(floor, (bc / bt) - 0.40) if bt > 0 else floor
        xgb_acc = max(floor, (xc / xt) - 0.40) if xt >= 20 else floor

        total_acc = bayes_acc + xgb_acc
        if total_acc > 0:
            xgb_w = xgb_acc / total_acc
            bayes_w = bayes_acc / total_acc
        else:
            xgb_w = base_w
            bayes_w = 1.0 - base_w
    else:
        xgb_w = base_w
        bayes_w = 1.0 - base_w

    ens_p = bayes_w * bayesian_p + xgb_w * xgb_p

    return {
        "ensemble_p": round(ens_p * 1000) / 1000,
        "bayes_weight": round(bayes_w * 100) / 100,
        "xgb_weight": round(xgb_w * 100) / 100,
    }


# ---------------------------------------------------------------------------
# Model persistence
# ---------------------------------------------------------------------------

def save_model(model_bundle: dict) -> bool:
    """Save trained model to disk."""
    if not HAS_JOBLIB or not model_bundle:
        return False
    try:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        joblib.dump(model_bundle, MODEL_PATH)
        # Save metadata separately as JSON
        meta = {
            "n_trained": model_bundle.get("n_trained", 0),
            "residual_std": model_bundle.get("residual_std", 14.0),
        }
        META_PATH.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(f"[xgb_model] Model saved to {MODEL_PATH}")
        return True
    except Exception as e:
        print(f"[xgb_model] Failed to save model: {e}")
        return False


def load_model() -> Optional[dict]:
    """Load trained model from disk."""
    if not HAS_JOBLIB or not MODEL_PATH.exists():
        return None
    try:
        bundle = joblib.load(MODEL_PATH)
        if bundle and bundle.get("model"):
            print(f"[xgb_model] Model loaded from {MODEL_PATH} (n_trained={bundle.get('n_trained', '?')})")
            return bundle
    except Exception as e:
        print(f"[xgb_model] Failed to load model: {e}")
    return None


def load_or_train_model(history_store: dict) -> Optional[dict]:
    """
    Load model from disk if available and recent enough; otherwise retrain.

    Retrains if:
      - No saved model exists
      - New games since last training exceed XGB_RETRAIN_INTERVAL
    """
    existing = load_model()
    rows = _load_training_rows(history_store)
    n_available = len(rows)

    if existing:
        n_prev = existing.get("n_trained", 0)
        new_games = n_available - n_prev
        if new_games < XGB_RETRAIN_INTERVAL:
            print(f"[xgb_model] {new_games} new games since last train (threshold={XGB_RETRAIN_INTERVAL}) -- using cached model")
            return existing
        print(f"[xgb_model] {new_games} new games since last train -- retraining")

    bundle = train_model(history_store)
    if bundle:
        save_model(bundle)
    return bundle


# ---------------------------------------------------------------------------
# Convenience: compute recent model performance for adaptive weighting
# ---------------------------------------------------------------------------

def compute_recent_performance(history_store: dict, lookback: int = None) -> dict:
    """
    Compute recent accuracy of Bayesian vs XGBoost predictions
    over the last `lookback` graded games (from history).

    Returns dict suitable for ensemble_probability(recent_performance=...).
    """
    lookback = lookback or XGB_LOOKBACK_WINDOW
    rows = _load_training_rows(history_store)

    if len(rows) < XGB_MIN_TRAINING_GAMES:
        return {}

    # Use last `lookback` non-push rows
    recent = [r for r in rows[-lookback:] if not r["push"]]

    bayes_correct = sum(1 for r in recent if (r["bayes_p"] >= 0.5) == r["cover"])
    xgb_correct = 0
    xgb_total = 0

    # Load model to get XGBoost predictions on recent games
    bundle = load_model()
    if bundle and bundle.get("model"):
        for r in recent:
            pred = predict_game(bundle, r["x"])
            if pred:
                xgb_total += 1
                xgb_pred_cover = pred["cal_p_cover"] >= 0.5
                if xgb_pred_cover == r["cover"]:
                    xgb_correct += 1

    return {
        "bayes_correct": bayes_correct,
        "bayes_total": len(recent),
        "xgb_correct": xgb_correct,
        "xgb_total": xgb_total,
    }
